"""Motor de backtest vela a vela (1 h) con supuestos conservadores.

Para evitar el sesgo de anticipación:
- Las señales se calculan al CIERRE de la vela i con datos <= i, y la entrada
  se ejecuta en la APERTURA de la vela i+1 (nunca al cierre de la vela de señal).
- El trailing calculado al cierre de i rige a partir de la vela i+1.

Ejecución conservadora:
- Entrada: taker + slippage adverso.
- Si en la misma vela se tocan el stop y el TP: se asume el STOP.
- Si la vela abre ya más allá del stop (gap): se sale a la apertura, no al stop.
- El TP exige que el precio lo SUPERE (no basta con tocarlo); comisión maker
  si es post-only.
- Stop disparado por velas de mark price (como en vivo) si están disponibles;
  la ejecución es a mercado: precio del stop o apertura si hay gap, con slippage.

Funding (perpetuo): al final de cada hora con posición abierta se aplica
    pago = -signo * tamaño * contract_size * mark_cierre * funding_relativo
(los largos pagan si el funding es positivo). SUPUESTO A VERIFICAR: que
relativeFundingRate de /historicalfundingrates sea la fracción del periodo
horario. El informe muestra la media del funding para detectar escalas absurdas.

Parada diaria: se evalúa al cierre de cada vela con la equity marcada a
mercado; si salta se cierra la posición y no se abre nada más hasta el día UTC
siguiente. (En vivo se evalúa cada pocos segundos y exige rearme manual.)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from ..models import InstrumentSpec, Side
from ..risk.limits import estimate_liquidation_price, liquidation_is_safe
from ..risk.sizing import position_size
from ..strategy.donchian_atr import (StrategyParams, compute_indicators, signal_at,
                                     take_profit_price, trailing_stop)


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float
    spec: InstrumentSpec
    risk_per_trade: float = 0.01
    max_leverage: float = 2.0
    max_daily_loss: float = 0.03
    taker_fee: float = 0.0005
    maker_fee: float = 0.0002
    slippage_bps: float = 5.0
    tp_post_only: bool = True
    min_liq_distance_multiple: float = 3.0


@dataclass
class Trade:
    side: str
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    entry_price: float
    size: float
    initial_stop: float
    take_profit: float
    risk_usd: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    gross_pnl: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    bars_held: int = 0

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.fees + self.funding

    @property
    def r_multiple(self) -> float:
        return self.net_pnl / self.risk_usd if self.risk_usd > 0 else 0.0


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity: pd.Series
    params: StrategyParams
    skipped: dict[str, int] = field(default_factory=dict)
    initial_capital: float = 0.0


def _align(other: pd.DataFrame | None, index: pd.DatetimeIndex) -> pd.DataFrame | None:
    if other is None or other.empty:
        return None
    return other.reindex(index)


def run_backtest(candles: pd.DataFrame, params: StrategyParams, cfg: BacktestConfig,
                 funding: pd.DataFrame | None = None, mark: pd.DataFrame | None = None,
                 start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> BacktestResult:
    """Ejecuta el backtest. Solo se abren operaciones con vela de señal en [start, end).

    Las velas anteriores a start sirven de calentamiento de indicadores (sin
    operar), lo que permite encadenar ventanas walk-forward sin sesgo.
    """
    df = candles.sort_index()
    if end is not None:
        df = df[df.index < end]
    ind = compute_indicators(df, params)
    mk = _align(mark, df.index)
    fund_rate = None
    if funding is not None and not funding.empty:
        fund_rate = funding["relative_rate"].reindex(df.index).fillna(0.0).to_numpy()

    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    exit_low, exit_high = ind["exit_low"].to_numpy(), ind["exit_high"].to_numpy()
    any_signal = (ind["long_signal"] | ind["short_signal"]).to_numpy()
    if mk is not None:
        # velas de mark: donde falten se usa la de trades
        to, th, tl, tc = (mk[k].fillna(df[k]).to_numpy() for k in ("open", "high", "low", "close"))
    else:
        to, th, tl, tc = o, h, l, c

    spec = cfg.spec
    cs = spec.contract_size
    slip = cfg.slippage_bps / 1e4
    first_entry_i = 0 if start is None else int(df.index.searchsorted(start, side="left"))

    cash = cfg.initial_capital
    equity = np.full(len(df), np.nan)
    trades: list[Trade] = []
    skipped = {"below_min": 0, "gap_through_stop": 0, "liquidation": 0, "daily_halt": 0}
    pos: Trade | None = None
    stop = tp = 0.0
    pending = None
    day = None
    day_start_eq = cash
    halted_day = None

    def close_pos(i: int, price: float, reason: str, fee_rate: float) -> None:
        nonlocal cash, pos
        side = Side(pos.side)
        pos.exit_time, pos.exit_price, pos.exit_reason = df.index[i], price, reason
        pos.gross_pnl = side.sign * pos.size * cs * (price - pos.entry_price)
        exit_fee = pos.size * cs * price * fee_rate
        pos.fees += exit_fee
        cash += pos.gross_pnl - exit_fee
        trades.append(pos)
        pos = None

    for i in range(len(df)):
        t = df.index[i]
        d = t.date()
        if d != day:
            day = d
            mtm = cash + (Side(pos.side).sign * pos.size * cs * (o[i] - pos.entry_price) if pos else 0.0)
            day_start_eq = mtm

        # 1) Entrada pendiente de la señal de la vela anterior, a la apertura.
        if pending is not None and pos is None:
            sig = pending
            pending = None
            side = sig.side
            gap_through = (to[i] <= sig.stop_price) if side is Side.LONG else (to[i] >= sig.stop_price)
            if gap_through:
                skipped["gap_through_stop"] += 1
            else:
                entry_px = o[i] * (1 + side.sign * slip)
                sz = position_size(capital_usd=cash, entry_price=entry_px, stop_price=sig.stop_price,
                                   spec=spec, risk_per_trade=cfg.risk_per_trade,
                                   max_leverage=cfg.max_leverage, taker_fee=cfg.taker_fee,
                                   slippage_bps=cfg.slippage_bps)
                if not sz.ok:
                    skipped["below_min"] += 1
                else:
                    liq = estimate_liquidation_price(
                        side=side, entry=entry_px, size=sz.size, contract_size=cs,
                        maintenance_margin=spec.maintenance_margin, margin_mode="isolated",
                        collateral_usd=cash, isolated_leverage=cfg.max_leverage)
                    safe, _ = liquidation_is_safe(side=side, entry=entry_px, stop=sig.stop_price,
                                                  liq_price=liq, min_multiple=cfg.min_liq_distance_multiple)
                    if not safe:
                        skipped["liquidation"] += 1
                    else:
                        stop = sig.stop_price
                        tp = take_profit_price(side, entry_px, stop, params.take_profit_r)
                        fee = sz.size * cs * entry_px * cfg.taker_fee
                        cash -= fee
                        pos = Trade(side=side.value, signal_time=sig.time, entry_time=t,
                                    entry_price=entry_px, size=sz.size, initial_stop=stop,
                                    take_profit=tp, risk_usd=sz.risk_usd, fees=fee)

        # 2) Salidas dentro de la vela (stop tiene prioridad sobre TP).
        if pos is not None:
            side = Side(pos.side)
            pos.bars_held += 1
            if side is Side.LONG:
                stop_hit, tp_hit = tl[i] <= stop, h[i] > tp
                gap = to[i] <= stop
            else:
                stop_hit, tp_hit = th[i] >= stop, l[i] < tp
                gap = to[i] >= stop
            if stop_hit:
                base = o[i] if gap else stop
                close_pos(i, base * (1 - side.sign * slip), "stop_gap" if gap else "stop", cfg.taker_fee)
            elif tp_hit:
                close_pos(i, tp, "take_profit", cfg.maker_fee if cfg.tp_post_only else cfg.taker_fee)

        # 3) Funding de la hora con posición abierta al cierre.
        if pos is not None and fund_rate is not None:
            side = Side(pos.side)
            pay = -side.sign * pos.size * cs * tc[i] * fund_rate[i]
            pos.funding += pay
            cash += pay

        # 4) Equity marcada a mercado y parada diaria.
        unreal = Side(pos.side).sign * pos.size * cs * (c[i] - pos.entry_price) if pos else 0.0
        equity[i] = cash + unreal
        if halted_day != d and day_start_eq > 0 and \
                (day_start_eq - equity[i]) / day_start_eq >= cfg.max_daily_loss:
            halted_day = d
            if pos is not None:
                side = Side(pos.side)
                close_pos(i, c[i] * (1 - side.sign * slip), "daily_loss_halt", cfg.taker_fee)
                equity[i] = cash
            pending = None

        # 5) Trailing y nueva señal al cierre.
        if pos is not None:
            stop = trailing_stop(Side(pos.side), stop, exit_low[i], exit_high[i])
        elif i >= first_entry_i and i < len(df) - 1 and any_signal[i]:
            if halted_day == d:
                skipped["daily_halt"] += 1
            else:
                pending = signal_at(ind, i, params, funding)

    if pos is not None:
        side = Side(pos.side)
        close_pos(len(df) - 1, c[-1] * (1 - side.sign * slip), "end_of_data", cfg.taker_fee)
        equity[-1] = cash

    eq = pd.Series(equity, index=df.index, name="equity")
    if start is not None:
        eq = eq[eq.index >= start]
    tdf = pd.DataFrame([{**asdict(tr), "net_pnl": tr.net_pnl, "r_multiple": tr.r_multiple} for tr in trades])
    return BacktestResult(trades=tdf, equity=eq.ffill(), params=params, skipped=skipped,
                          initial_capital=cfg.initial_capital)
