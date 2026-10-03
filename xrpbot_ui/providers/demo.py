"""Proveedor de DEMOSTRACIÓN: simula el bot sin Kraken ni claves.

Sirve para ver y probar la interfaz completa, incluidos los casos límite:

    normal          bot activo con una posición larga abierta
    vacio           bot recién arrancado, sin operaciones ni posición
    caido           el bot dejó de publicar su latido hace 45 s
    liquidacion     posición abierta con la liquidación cerca del stop
    breaker         volatilidad anómala: un circuit breaker bloquea entradas
    limite_diario   detenido por pérdida diaria del 3 %
    desfasado       los datos de mercado llevan 95 s sin actualizarse
    reconectando    el WebSocket se ha caído y se está reconectando
    discrepancia    reconciliación con posición desconocida: bot congelado
    live_piloto     modo LIVE con tope de capital (PILOTO)

Las operaciones, la curva de capital y las marcas del gráfico salen de
ejecutar el motor de backtest REAL del bot sobre velas sintéticas, así que
son coherentes entre sí. Los comandos se "procesan" con las mismas reglas
que aplicará el bot (lista cerrada, caducidad, idempotencia, solo reducir).
"""
from __future__ import annotations

import math
import random
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from xrpbot.backtest.engine import BacktestConfig, run_backtest
from xrpbot.models import InstrumentSpec
from xrpbot.strategy.donchian_atr import StrategyParams, compute_indicators

from ..contract import (REDUCIBLE_PARAMS, SCHEMA_VERSION, Command, CommandError, effective_value,
                        validate_command_shape, validate_reduction)
from .base import filter_trades, level_at_least

SCENARIOS = {
    "normal": "Activo con posición abierta",
    "vacio": "Recién arrancado, sin operaciones",
    "caido": "Bot caído (sin latido)",
    "liquidacion": "Liquidación cercana",
    "breaker": "Circuit breaker activo",
    "limite_diario": "Detenido por límite diario",
    "desfasado": "Datos desfasados",
    "reconectando": "Reconectando WebSocket",
    "discrepancia": "Congelado por discrepancia",
    "live_piloto": "LIVE · PILOTO",
}

TICK = 0.0001
SYMBOL = "PF_XRPUSD"
PROCESS_DELAY_S = 1.2          # el "bot" simulado recoge los comandos en su siguiente ciclo

DEFAULT_FILE_PARAMS = {
    "risk.risk_per_trade": 0.01,
    "risk.max_daily_loss": 0.03,
    "risk.max_leverage": 2.0,
    "circuit_breaker.max_spread_bps": 15.0,
    "circuit_breaker.max_data_age_s": 30.0,
    "circuit_breaker.max_candle_delay_bars": 2.0,
    "circuit_breaker.max_atr_ratio": 3.0,
    "circuit_breaker.max_mark_last_divergence_bps": 50.0,
    "circuit_breaker.min_book_depth_multiple": 3.0,
}


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def read_file_params(path: str | Path | None) -> tuple[dict, dict]:
    """Lee SOLO parámetros de settings.yaml (nunca .env). Devuelve (reducibles, estrategia)."""
    params = dict(DEFAULT_FILE_PARAMS)
    strategy = {"name": "donchian_atr", "timeframe": "1h", "entry_lookback": 20, "exit_lookback": 10,
                "atr_period": 14, "atr_stop_mult": 2.0, "take_profit_r": 4.0,
                "funding_filter": False, "funding_max_abs_hourly": 0.0001, "max_capital_usd": None}
    if path and Path(path).exists():
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        for name in params:
            sect, key = name.split(".")
            v = (raw.get(sect) or {}).get(key)
            if v is not None:
                params[name] = float(v)
        st = raw.get("strategy") or {}
        for k in ("entry_lookback", "exit_lookback", "atr_period", "atr_stop_mult", "take_profit_r"):
            if st.get(k) is not None:
                strategy[k] = st[k]
        ff = st.get("funding_filter") or {}
        strategy["funding_filter"] = bool(ff.get("enabled", False))
        strategy["funding_max_abs_hourly"] = float(ff.get("max_abs_hourly_rate", 0.0001))
        strategy["timeframe"] = raw.get("timeframe", "1h")
        strategy["max_capital_usd"] = (raw.get("capital") or {}).get("max_capital_usd")
    return params, strategy


def synthetic_candles(n: int, end: pd.Timestamp, seed: int, start_price: float = 1.40) -> pd.DataFrame:
    """Velas de 1 h a partir de un camino intrabarra (como en los tests del bot)."""
    rng = np.random.default_rng(seed)
    sub = 12
    drift = np.zeros(n)
    drift[n // 5: n // 3] = 0.0006
    drift[2 * n // 3: 3 * n // 4] = -0.0007
    drift[-n // 8:] = 0.0004
    steps = rng.normal(0, 0.007 * start_price / math.sqrt(sub), (n, sub)) + (drift * start_price / sub)[:, None]
    p = np.maximum(start_price + np.cumsum(steps.ravel()), 0.2).reshape(n, sub)
    o = np.r_[start_price, p[:-1, -1]]
    c, h, lo = p[:, -1], np.maximum(p.max(1), o), np.minimum(p.min(1), o)
    idx = pd.date_range(end=end, periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": rng.uniform(2e5, 9e5, n)}, index=idx)
    return df.round({"open": 4, "high": 4, "low": 4, "close": 4})


class DemoProvider:
    name = "demo"

    def __init__(self, scenario: str = "normal", settings_path: str | Path | None = "config/settings.yaml",
                 seed: int = 7, clock=_now) -> None:
        self.clock = clock
        self.seed = seed
        self.lock = threading.RLock()
        self.file_params, self.strategy = read_file_params(settings_path)
        self.params = StrategyParams(entry_lookback=int(self.strategy["entry_lookback"]),
                                     exit_lookback=int(self.strategy["exit_lookback"]),
                                     atr_period=int(self.strategy["atr_period"]),
                                     atr_stop_mult=float(self.strategy["atr_stop_mult"]),
                                     take_profit_r=float(self.strategy["take_profit_r"]))
        self.spec = InstrumentSpec(SYMBOL, 1.0, TICK, 1.0, 1.0, 0.01)
        end = pd.Timestamp(self.clock()).floor("1h") - pd.Timedelta("1h")
        self.base_candles = synthetic_candles(2000, end, seed)
        self.set_scenario(scenario)

    # ================================================================ escenarios
    def set_scenario(self, name: str) -> None:
        if name not in SCENARIOS:
            raise ValueError(f"Escenario desconocido: {name}")
        with self.lock:
            self.scenario = name
            now = self.clock()
            self.rng = random.Random(self.seed)
            self.mode = "live" if name == "live_piloto" else "sim"
            self.initial_capital = 14.60 if name == "live_piloto" else 1000.0
            self.pilot_cap = 50.0 if name == "live_piloto" else None
            self.cdf = self.base_candles.copy()
            self.overrides: dict[str, float] = {}
            self.commands: dict[str, Command] = {}
            self.event_log: list[dict] = []
            self.alerts_sent: list[dict] = []
            self.reason = ""
            self.state = "active"
            self.started_at = now - timedelta(hours=26)
            self.heartbeat_lag = 1.0
            self.data_lag = 2.0
            self.ws_connected = True
            self.reconnects_today = 1
            self.entries_blocked_reasons: list[str] = []
            self.position = None
            self.orders: list[dict] = []
            self.unknown_position = None
            self.last_reconcile = {"at": _iso(now - timedelta(seconds=40)), "result": "ok", "notes": []}
            self.breaker_overrides: dict[str, float] = {}
            self.account_max_leverage = 2.0
            self.halt_day: str | None = None    # día UTC de una parada por límite diario

            # Historial: backtest real del bot sobre las velas sintéticas
            if name == "vacio":
                self.trades_rows, self.equity_points = [], [(now, self.initial_capital)]
                self.started_at = now - timedelta(minutes=12)
            else:
                self._build_history()
            self.cash = self.equity_points[-1][1]
            self.day_start_equity = self._equity_at_day_start()
            self.price = float(self.cdf["close"].iloc[-1])
            self.last_tick = now

            if name in ("normal", "liquidacion", "breaker", "desfasado", "reconectando", "live_piloto", "caido"):
                self._open_position(near_liq=(name == "liquidacion"))
            self._event("info", "start", f"Bot arrancado en modo {self.mode.upper()} sobre {SYMBOL}", now - timedelta(hours=26))

            if name == "caido":
                self.heartbeat_lag = 45.0
                self.data_lag = 47.0
            elif name == "breaker":
                self.breaker_overrides["atr_ratio"] = 3.42
                self._event("warning", "signal", "Señal larga bloqueada: volatilidad anómala (ATR 3,4× su mediana)",
                            now - timedelta(minutes=18))
            elif name == "limite_diario":
                self.state, self.reason = "halted", "Límite de pérdida diaria (3,0 %) alcanzado a las 11:12 UTC"
                self.halt_day = now.date().isoformat()
                self.cash = self.day_start_equity * (1 - 0.0305)
                self._add_closed_trade("long", -0.0305 * self.day_start_equity, "daily_loss_halt", now - timedelta(hours=2))
                self._event("critical", "daily_loss", "PARADA DIARIA: pérdida 3,05 % >= límite 3,00 %. Cerrando todo. "
                            "Rearme manual necesario.", now - timedelta(hours=2))
            elif name == "desfasado":
                self.data_lag = 95.0
            elif name == "reconectando":
                self.state, self.ws_connected = "reconnecting", False
                self.reconnects_today = 4
                self._event("warning", "ws", "WebSocket caído (ConnectionClosed). Reconexión en 8 s", now - timedelta(seconds=9))
            elif name == "discrepancia":
                self.state = "frozen"
                self.reason = "Posición DESCONOCIDA en el exchange: long 100 @ 1,4720"
                self.unknown_position = {"side": "long", "size": 100.0, "entry_price": 1.4720}
                self.orders = [{"role": "emergency_sl", "type": "stp", "side": "sell", "size": 100.0,
                                "price": round(self.price * 0.95, 4), "trigger": "mark", "reduce_only": True,
                                "cli_ord_id": f"emerg-{int(now.timestamp())}", "status": "open"}]
                self.last_reconcile = {"at": _iso(now - timedelta(minutes=3)), "result": "frozen",
                                       "notes": ["Posición DESCONOCIDA en el exchange: long 100 @ 1,4720",
                                                 "Stop de emergencia colocado al 5 %"]}
                self._event("critical", "freeze", "Bot CONGELADO: posición desconocida en el exchange. "
                            "Revisa y ejecuta 'rearm' para reanudar.", now - timedelta(minutes=3))
            elif name == "live_piloto":
                self.account_max_leverage = 2.0
            if name != "discrepancia":
                self._event("info", "reconcile", "Reconciliación sin discrepancias", now - timedelta(seconds=40))

    def _build_history(self) -> None:
        cfg = BacktestConfig(initial_capital=self.initial_capital, spec=self.spec,
                             risk_per_trade=self.file_params["risk.risk_per_trade"],
                             max_leverage=self.file_params["risk.max_leverage"],
                             max_daily_loss=self.file_params["risk.max_daily_loss"],
                             taker_fee=0.0005, maker_fee=0.0002, slippage_bps=5)
        res = run_backtest(self.cdf.iloc[:-8], self.params, cfg)
        self.trades_rows = []
        for i, t in enumerate(res.trades.to_dict("records")):
            self.trades_rows.append(self._trade_row(i, t))
        eq = res.equity.dropna()
        self.equity_points = [(ts.to_pydatetime(), float(v)) for ts, v in eq.items()]

    def _trade_row(self, i: int, t: dict) -> dict:
        tid = f"xrpusd-{t['entry_time']:%Y%m%dT%H%M}-{'L' if t['side'] == 'long' else 'S'}"
        return {
            "trade_id": tid, "side": t["side"],
            "opened_at": _iso(t["entry_time"].to_pydatetime()), "closed_at": _iso(t["exit_time"].to_pydatetime()),
            "size": float(t["size"]), "entry_price": round(float(t["entry_price"]), 4),
            "exit_price": round(float(t["exit_price"]), 4),
            "stop_price": round(float(t["initial_stop"]), 4), "tp_price": round(float(t["take_profit"]), 4),
            "gross_pnl": float(t["gross_pnl"]), "fees": float(t["fees"]), "funding": float(t["funding"]),
            "net_pnl": float(t["net_pnl"]), "r_multiple": float(t["r_multiple"]), "risk_usd": float(t["risk_usd"]),
            "exit_reason": t["exit_reason"], "bars_held": int(t["bars_held"]),
            "signal": f"Ruptura {'alcista' if t['side'] == 'long' else 'bajista'} del canal de "
                      f"{self.params.entry_lookback} velas",
        }

    def _add_closed_trade(self, side: str, pnl: float, reason: str, at: datetime) -> None:
        px = self.price
        self.trades_rows.append({
            "trade_id": f"xrpusd-{at:%Y%m%dT%H%M}-{'L' if side == 'long' else 'S'}", "side": side,
            "opened_at": _iso(at - timedelta(hours=3)), "closed_at": _iso(at), "size": 7.0,
            "entry_price": round(px, 4), "exit_price": round(px * 0.99, 4), "stop_price": round(px * 0.985, 4),
            "tp_price": round(px * 1.06, 4), "gross_pnl": pnl, "fees": 0.01, "funding": 0.0, "net_pnl": pnl - 0.01,
            "r_multiple": -1.0, "risk_usd": abs(pnl), "exit_reason": reason, "bars_held": 3,
            "signal": "Ruptura alcista del canal de 20 velas"})
        self.equity_points.append((at, self.cash))

    def _equity_at_day_start(self) -> float:
        midnight = self.clock().replace(hour=0, minute=0, second=0, microsecond=0)
        before = [v for ts, v in self.equity_points if ts <= midnight]
        return before[-1] if before else self.equity_points[0][1]

    def _open_position(self, near_liq: bool = False) -> None:
        ind = compute_indicators(self.cdf, self.params)
        atr = float(ind["atr"].iloc[-1])
        entry = round(float(self.cdf["close"].iloc[-6]), 4)
        stop = round(entry - self.params.atr_stop_mult * atr, 4)
        risk_usd = self.cash * self.effective("risk.risk_per_trade")
        size = max(1.0, math.floor(risk_usd / max(entry - stop, TICK)))
        size = min(size, math.floor(self.effective("risk.max_leverage") * self.cash / entry))
        if self.pilot_cap:
            size = min(size, math.floor(self.pilot_cap * self.effective("risk.max_leverage") / entry))
        tp = round(entry + self.params.take_profit_r * (entry - stop), 4)
        liq = round(entry - 1.45 * (entry - stop), 4) if near_liq else round(entry * (1 - 0.5 + 0.01), 4)
        opened = pd.Timestamp(self.cdf.index[-5]).to_pydatetime()
        self.position = {"trade_id": f"xrpusd-{opened:%Y%m%dT%H%M}-L", "side": "long", "size": float(size),
                         "entry_price": entry, "stop_price": stop, "tp_price": tp, "liquidation_price": liq,
                         "opened_at": opened, "risk_usd": size * (entry - stop), "funding_accrued_usd": -0.0021 * size / 7}
        tid = self.position["trade_id"]
        self.orders = [
            {"role": "sl", "type": "stp", "side": "sell", "size": float(size), "price": stop, "trigger": "mark",
             "reduce_only": True, "cli_ord_id": f"{tid}-sl-3", "status": "open"},
            {"role": "tp", "type": "post", "side": "sell", "size": float(size), "price": tp, "trigger": None,
             "reduce_only": True, "cli_ord_id": f"{tid}-tp-2", "status": "open"},
        ]
        self._event("info", "entry", f"ENTRADA long {size:.0f} {SYMBOL} @ {_es(entry)} · stop {_es(stop)} · TP {_es(tp)}",
                    opened + timedelta(seconds=21))

    # ================================================================ mercado simulado
    def _tick(self) -> None:
        now = self.clock()
        dt = max(0.0, (now - self.last_tick).total_seconds())
        if self.scenario not in ("caido", "desfasado") and dt > 0:
            self.price = max(0.2, self.price * math.exp(self.rng.gauss(0, 0.00035 * math.sqrt(min(dt, 60)))))
            if self.scenario == "liquidacion" and self.position:
                lo = self.position["stop_price"] + 0.001
                self.price = min(max(self.price, lo), self.position["entry_price"] * 0.995)
        self.last_tick = now

    @property
    def mark(self) -> float:
        return round(self.price, 5)

    def effective(self, name: str) -> float:
        return effective_value(name, self.file_params[name], self.overrides.get(name))

    def _unrealized(self) -> float:
        p = self.position
        if not p:
            return 0.0
        sign = 1 if p["side"] == "long" else -1
        return sign * p["size"] * (self.mark - p["entry_price"])

    def _equity(self) -> float:
        return self.cash + self._unrealized() + (self.position or {}).get("funding_accrued_usd", 0.0)

    # ================================================================ eventos
    def _event(self, level: str, kind: str, message: str, at: datetime | None = None, alert: bool | None = None) -> None:
        ev = {"ts": _iso(at or self.clock()), "level": level, "kind": kind, "message": message}
        self.event_log.append(ev)
        if alert if alert is not None else level in ("warning", "error", "critical") or kind in ("entry", "trade_closed", "command"):
            self.alerts_sent.append({"ts": ev["ts"], "level": level, "channel": "telegram",
                                     "message": message, "delivered": self.scenario != "caido"})

    # ================================================================ API del proveedor
    def capabilities(self) -> dict:
        return {"source": "demo", "control": True, "live_status": True, "scenarios": SCENARIOS,
                "scenario": self.scenario, "backtest": True, "note": "Datos SIMULADOS de demostración"}

    def snapshot(self) -> dict:
        with self.lock:
            self._tick()
            self._process_pending()
            now = self.clock()
            hb = now - timedelta(seconds=self.heartbeat_lag)
            data_at = now - timedelta(seconds=self.data_lag)
            equity = self._equity()
            pnl_day = equity - self.day_start_equity
            limit_usd = self.day_start_equity * self.effective("risk.max_daily_loss")
            used = max(0.0, -pnl_day)
            spread_bps = 0.7 if self.scenario != "breaker" else 2.1
            bid, ask = self.mark - TICK / 2, self.mark + TICK / 2
            pos = None
            if self.position:
                p = self.position
                sign = 1 if p["side"] == "long" else -1
                notional = p["size"] * self.mark
                unreal = self._unrealized()
                pos = {
                    "trade_id": p["trade_id"], "side": p["side"], "size": p["size"],
                    "entry_price": p["entry_price"], "mark_price": self.mark,
                    "notional_usd": notional, "effective_leverage": notional / equity if equity > 0 else None,
                    "unrealized_pnl_usd": unreal, "r_multiple": unreal / p["risk_usd"] if p["risk_usd"] else None,
                    "risk_usd": p["risk_usd"],
                    "stop_price": p["stop_price"], "tp_price": p["tp_price"], "liquidation_price": p["liquidation_price"],
                    "dist_stop_pct": sign * (self.mark - p["stop_price"]) / self.mark,
                    "dist_tp_pct": sign * (p["tp_price"] - self.mark) / self.mark,
                    "dist_liq_pct": sign * (self.mark - p["liquidation_price"]) / self.mark,
                    "funding_accrued_usd": p["funding_accrued_usd"], "opened_at": _iso(p["opened_at"]),
                }
            elif self.unknown_position:
                u = self.unknown_position
                pos = {"trade_id": None, "unknown": True, "side": u["side"], "size": u["size"],
                       "entry_price": u["entry_price"], "mark_price": self.mark, "notional_usd": u["size"] * self.mark,
                       "effective_leverage": u["size"] * self.mark / equity if equity > 0 else None,
                       "unrealized_pnl_usd": u["size"] * (self.mark - u["entry_price"]), "r_multiple": None,
                       "risk_usd": None, "stop_price": self.orders[0]["price"] if self.orders else None,
                       "tp_price": None, "liquidation_price": None, "dist_stop_pct": None, "dist_tp_pct": None,
                       "dist_liq_pct": None, "funding_accrued_usd": 0.0, "opened_at": None}

            breakers = self._breakers(spread_bps, data_at)
            blocking = [b for b in breakers if b["state"] == "blocking"]
            reasons = [f"{b['label']}: {b['value_text']} (umbral {b['threshold_text']})" for b in blocking]
            reasons += self.entries_blocked_reasons
            if self.state == "paused":
                reasons.append("Entradas pausadas manualmente")
            if self.state == "reconnecting":
                reasons.append("Reconectando con Kraken: no se abren posiciones hasta reconciliar")
            if self.state in ("halted", "frozen"):
                reasons.append(self.reason)

            last_ev = next((e for e in reversed(self.event_log) if e["level"] in ("warning", "error", "critical")
                            or e["kind"] in ("entry", "trade_closed", "command")), None)
            eff = {n.split(".")[1]: self.effective(n) for n in ("risk.risk_per_trade", "risk.max_daily_loss", "risk.max_leverage")}
            return {
                "schema": SCHEMA_VERSION,
                "generated_at": _iso(now),
                "source": "demo",
                "symbol": SYMBOL,
                "tick_size": TICK,
                "mode": self.mode,
                "pilot": {"active": self.pilot_cap is not None, "max_capital_usd": self.pilot_cap},
                "bot": {"state": self.state, "reason": self.reason or None, "heartbeat_at": _iso(hb),
                        "version": "0.1.0", "commit": "demo", "started_at": _iso(self.started_at),
                        "strategy": f"Donchian {self.params.entry_lookback} + ATR · 1 h"},
                "market": {"mark": self.mark, "last": round(self.mark - 0.0001, 4), "bid": round(bid, 5),
                           "ask": round(ask, 5), "spread_bps": spread_bps, "funding_rate_hourly": 0.0000061,
                           "at": _iso(data_at)},
                "account": {"equity_usd": equity, "equity_at": _iso(data_at),
                            "day_start_equity_usd": self.day_start_equity, "pnl_day_usd": pnl_day,
                            "pnl_total_usd": equity - self.initial_capital, "initial_capital_usd": self.initial_capital,
                            "account_max_leverage": self.account_max_leverage},
                "position": pos,
                "orders": list(self.orders),
                "risk": {
                    "daily_loss_used_usd": used, "daily_loss_limit_usd": limit_usd,
                    "daily_loss_fraction_of_limit": (used / limit_usd) if limit_usd > 0 else 0.0,
                    "reset_at": _iso((now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)),
                    "effective": eff,
                    "entries_blocked": bool(reasons), "block_reasons": reasons,
                },
                "breakers": breakers,
                "health": {
                    "ws": {"connected": self.ws_connected,
                           "last_msg_at": _iso(now - timedelta(seconds=(9 if not self.ws_connected else self.data_lag * 0.4))),
                           "reconnects_today": self.reconnects_today},
                    "rest": {"latency_p50_ms": 118, "latency_p95_ms": 342},
                    "rate_limit": {"used": 38, "capacity": 400, "window_s": 10},
                    "clock_offset_s": -0.049,
                    "ticker_at": _iso(data_at),
                    "last_candle_at": _iso(pd.Timestamp(self.cdf.index[-1]).to_pydatetime()),
                    "last_reconcile": self.last_reconcile,
                },
                "last_event": last_ev,
            }

    def _breakers(self, spread_bps: float, data_at: datetime) -> list[dict]:
        now = self.clock()
        age = (now - data_at).total_seconds()
        ov = self.breaker_overrides
        rows = [
            ("spread", "Spread", spread_bps, "circuit_breaker.max_spread_bps", "pb", "lower"),
            ("book_depth", "Profundidad ±0,5 %", 41.0, "circuit_breaker.min_book_depth_multiple", "× tamaño", "higher"),
            ("atr_ratio", "ATR / mediana 30 d", ov.get("atr_ratio", 1.18), "circuit_breaker.max_atr_ratio", "×", "lower"),
            ("data_age", "Antigüedad del ticker", age, "circuit_breaker.max_data_age_s", "s", "lower"),
            ("mark_last", "Divergencia mark / last", 0.7, "circuit_breaker.max_mark_last_divergence_bps", "pb", "lower"),
        ]
        out = []
        for bid, label, value, param, unit, direction in rows:
            thr = self.effective(param)
            blocking = value > thr if direction == "lower" else value < thr
            state = "blocking" if blocking else "ok"
            if self.scenario == "caido" and bid != "data_age":
                state = "no_data"
            out.append({"id": bid, "label": label, "value": value, "threshold": thr, "unit": unit,
                        "comparator": "<=" if direction == "lower" else ">=", "state": state,
                        "param": param, "value_text": _fmt_num(value, unit), "threshold_text": _fmt_num(thr, unit),
                        "updated_at": _iso(data_at)})
        ff_on = self.strategy["funding_filter"]
        out.append({"id": "funding", "label": "Funding horario", "value": 0.0000061,
                    "threshold": self.strategy["funding_max_abs_hourly"], "unit": "%/h", "comparator": "<=",
                    "state": "ok" if ff_on else "disabled", "param": None,
                    "value_text": "0,0006 %/h", "threshold_text": "0,01 %/h" + ("" if ff_on else " (filtro desactivado)"),
                    "updated_at": _iso(data_at)})
        return out

    def candles(self, limit: int = 500) -> dict:
        with self.lock:
            df = self.cdf.iloc[-limit:]
            ind = compute_indicators(self.cdf, self.params).iloc[-limit:]
            rows = [{"time": int(ts.timestamp()), "open": r.open, "high": r.high, "low": r.low, "close": r.close}
                    for ts, r in df.iterrows()]
            # vela en curso (se forma con el precio simulado)
            cur_t = int(pd.Timestamp(self.clock()).floor("1h").timestamp())
            last_close = rows[-1]["close"]
            rows.append({"time": cur_t, "open": last_close, "high": max(last_close, self.mark),
                         "low": min(last_close, self.mark), "close": round(self.mark, 4)})

            def series(col):
                return [{"time": int(ts.timestamp()), "value": round(float(v), 5)}
                        for ts, v in ind[col].items() if np.isfinite(v)]
            start = df.index[0]
            markers = []
            for t in self.trades_rows:
                ot, ct = pd.Timestamp(t["opened_at"]), pd.Timestamp(t["closed_at"])
                if ot >= start:
                    markers.append({"time": int(ot.floor("1h").timestamp()), "kind": "entry", "side": t["side"],
                                    "text": f"{'Largo' if t['side'] == 'long' else 'Corto'} {t['entry_price']}"})
                if ct >= start:
                    markers.append({"time": int(ct.floor("1h").timestamp()), "kind": "exit", "side": t["side"],
                                    "text": f"Salida {t['exit_reason']} {t['net_pnl']:+.2f}"})
            if self.position:
                markers.append({"time": int(pd.Timestamp(self.position["opened_at"]).timestamp()), "kind": "entry",
                                "side": "long", "text": f"Largo {self.position['entry_price']}"})
            levels = []
            if self.position:
                p = self.position
                levels = [{"id": "entry", "label": "ENTRADA", "price": p["entry_price"]},
                          {"id": "tp", "label": "TP", "price": p["tp_price"]},
                          {"id": "stop", "label": "STOP", "price": p["stop_price"]},
                          {"id": "liq", "label": "LIQ", "price": p["liquidation_price"]}]
            return {
                "symbol": SYMBOL, "timeframe": "1h", "tick_size": TICK, "candles": rows,
                "indicators": [
                    {"id": "dc_upper", "name": f"Donchian {self.params.entry_lookback} sup.", "pane": "price",
                     "style": "dashed", "color": "ind-1", "points": series("dc_upper")},
                    {"id": "dc_lower", "name": f"Donchian {self.params.entry_lookback} inf.", "pane": "price",
                     "style": "dashed", "color": "ind-1", "points": series("dc_lower")},
                    {"id": "exit_low", "name": f"Canal de salida {self.params.exit_lookback}", "pane": "price",
                     "style": "dotted", "color": "ind-1", "points": series("exit_low")},
                    {"id": "atr", "name": f"ATR {self.params.atr_period}", "pane": "separate",
                     "style": "solid", "color": "line", "points": series("atr")},
                ],
                "markers": sorted(markers, key=lambda m: m["time"]),
                "levels": levels,
            }

    def equity(self) -> dict:
        with self.lock:
            pts = list(self.equity_points) + [(self.clock(), self._equity())]
            s = pd.Series([v for _, v in pts], index=pd.DatetimeIndex([t for t, _ in pts]))
            dd = s / s.cummax() - 1
            return {"points": [{"time": int(t.timestamp()), "value": round(v, 4)} for t, v in pts],
                    "drawdown": [{"time": int(t.timestamp()), "value": round(float(v) * 100, 3)} for t, v in dd.items()],
                    "initial_capital_usd": self.initial_capital}

    def trades(self, **filters) -> list[dict]:
        with self.lock:
            rows = sorted(self.trades_rows, key=lambda t: t["opened_at"], reverse=True)
            return filter_trades(rows, **filters)

    def trade(self, trade_id: str) -> dict | None:
        with self.lock:
            t = next((t for t in self.trades_rows if t["trade_id"] == trade_id), None)
            if not t:
                return None
            d = dict(t)
            exit_side = "sell" if t["side"] == "long" else "buy"
            d["timeline"] = [
                {"ts": t["opened_at"], "what": "Señal", "detail": t["signal"]},
                {"ts": t["opened_at"], "what": "Orden entrada IOC", "detail": f"{t['size']:.0f} @ ≤ {t['entry_price']}"},
                {"ts": t["opened_at"], "what": "Ejecución entrada", "detail": f"{t['size']:.0f} @ {t['entry_price']}"},
                {"ts": t["opened_at"], "what": "Stop colocado", "detail": f"{exit_side} stp {t['stop_price']} (mark, reduce-only)"},
                {"ts": t["opened_at"], "what": "TP colocado", "detail": f"{exit_side} post {t['tp_price']} (reduce-only)"},
                {"ts": t["closed_at"], "what": "Salida", "detail": f"{t['exit_reason']} @ {t['exit_price']}"},
            ]
            return d

    def events(self, *, level=None, kind=None, q=None, limit: int = 300) -> list[dict]:
        with self.lock:
            out = [e for e in reversed(self.event_log) if level_at_least(e["level"], level)
                   and (not kind or e["kind"] == kind) and (not q or q.lower() in e["message"].lower())]
            return out[:limit]

    def alerts(self, limit: int = 200) -> list[dict]:
        with self.lock:
            return list(reversed(self.alerts_sent))[:limit]

    def config_view(self) -> dict:
        with self.lock:
            params = []
            for name, rule in REDUCIBLE_PARAMS.items():
                params.append({"name": name, "label": rule["label"], "unit": rule["unit"], "dir": rule["dir"],
                               "file": self.file_params[name], "override": self.overrides.get(name),
                               "effective": self.effective(name)})
            return {"params": params, "strategy": self.strategy, "mode": self.mode, "symbol": SYMBOL}

    # ================================================================ comandos
    def submit(self, cmd: Command) -> Command:
        with self.lock:
            existing = self.commands.get(cmd.id)
            if existing:                       # idempotente: el mismo id no se procesa dos veces
                return existing
            self.commands[cmd.id] = cmd
            self._event("info", "command", f"Comando recibido: {_cmd_label(cmd)} (id {cmd.id[:8]})", alert=False)
            return cmd

    def command(self, cmd_id: str) -> Command | None:
        with self.lock:
            self._process_pending()
            return self.commands.get(cmd_id)

    def _process_pending(self) -> None:
        now = self.clock()
        bot_alive = self.heartbeat_lag <= 15
        for cmd in self.commands.values():
            if cmd.status != "pending":
                continue
            if cmd.expired(now):
                cmd.status, cmd.result = "expired", "Caducado sin procesar: no se ha ejecutado"
                self._event("warning", "command", f"Comando caducado: {_cmd_label(cmd)}")
                continue
            if not bot_alive or (now - cmd.created_at).total_seconds() < PROCESS_DELAY_S:
                continue
            try:
                validate_command_shape(cmd.type, cmd.params)
                cmd.result = self._execute(cmd)
                cmd.status = "done"
                self._event("info", "command", f"Comando ejecutado: {_cmd_label(cmd)}. {cmd.result}")
            except CommandError as exc:
                cmd.status, cmd.result = "rejected", str(exc)
                self._event("warning", "command", f"Comando rechazado: {_cmd_label(cmd)}. {exc}")

    def _close_position(self, reason: str) -> str:
        p = self.position
        if not p:
            return "Sin posición"
        pnl = self._unrealized() + p["funding_accrued_usd"]
        fee = p["size"] * self.mark * 0.0005
        self.cash += pnl - fee
        now = self.clock()
        self.trades_rows.append({
            "trade_id": p["trade_id"], "side": p["side"], "opened_at": _iso(p["opened_at"]), "closed_at": _iso(now),
            "size": p["size"], "entry_price": p["entry_price"], "exit_price": round(self.mark, 4),
            "stop_price": p["stop_price"], "tp_price": p["tp_price"], "gross_pnl": self._unrealized(),
            "fees": 0.0005 * p["size"] * p["entry_price"] + fee, "funding": p["funding_accrued_usd"],
            "net_pnl": pnl - fee, "r_multiple": (pnl - fee) / p["risk_usd"] if p["risk_usd"] else 0.0,
            "risk_usd": p["risk_usd"], "exit_reason": reason, "bars_held": 5,
            "signal": f"Ruptura alcista del canal de {self.params.entry_lookback} velas"})
        self.equity_points.append((now, self.cash))
        self.position, self.orders = None, []
        self._event("info", "trade_closed", f"Operación {p['trade_id']} cerrada ({reason}): neto {_es(pnl - fee, 2, True)} USD")
        return f"Posición cerrada a mercado @ {_es(self.mark)} (neto {_es(pnl - fee, 2, True)} USD)"

    def _execute(self, cmd: Command) -> str:
        t = cmd.type
        if t == "pause":
            if self.state == "paused":
                return "Ya estaba pausado (sin cambios)"
            if self.state != "active":
                raise CommandError(f"No se puede pausar en estado {self.state}")
            self.state = "paused"
            return "Nuevas entradas pausadas. La posición abierta sigue protegida."
        if t == "resume":
            if self.state == "active":
                return "Ya estaba activo (sin cambios)"
            if self.state in ("halted", "frozen"):
                raise CommandError("El bot está detenido o congelado: hay que REARMAR, no reanudar")
            if self.state != "paused":
                raise CommandError(f"No se puede reanudar en estado {self.state}")
            if self.entries_blocked_reasons:
                raise CommandError("Hay una reducción que deja la posición fuera de límites: "
                                   "ciérrala o espera a que se cierre antes de reanudar")
            self.state = "active"
            return "Entradas reanudadas"
        if t == "close_position":
            if not self.position:
                raise CommandError("No hay posición abierta que cerrar")
            msg = self._close_position("cierre_manual")
            self.entries_blocked_reasons = []
            if self.state == "active":
                self.state = "paused"
            return msg + ". Entradas pausadas: pulsa «Reanudar» para seguir operando."
        if t == "kill":
            parts = [f"{len(self.orders)} órdenes canceladas"]
            if self.position:
                parts.append(self._close_position("kill_switch"))
            if self.unknown_position:
                self.unknown_position = None
                parts.append("posición desconocida cerrada a mercado")
            self.orders = []
            self.state, self.reason = "halted", "KILL SWITCH manual desde la interfaz"
            self._event("critical", "kill", "KILL SWITCH activado desde la interfaz")
            return "; ".join(parts) + ". Bot DETENIDO hasta rearme."
        if t == "rearm":
            if self.state not in ("halted", "frozen"):
                raise CommandError("No hay ninguna parada ni congelación que rearmar")
            today = self.clock().date().isoformat()
            if self.state == "halted" and self.halt_day == today:
                raise CommandError(f"La parada diaria es de hoy ({today} UTC). Se podrá rearmar tras el reinicio "
                                   "de las 00:00 UTC. (Saltarse el límite diario no está permitido desde la interfaz.)")
            if self.unknown_position:
                raise CommandError("Sigue habiendo una posición desconocida en el exchange: resuélvela antes de rearmar")
            self.state, self.reason = "active", ""
            if self.last_reconcile["result"] == "frozen":
                self.last_reconcile = {"at": _iso(self.clock()), "result": "ok", "notes": []}
            return "Bot rearmado: vuelve a operar"
        if t == "reduce_param":
            name, value = cmd.params["name"], float(cmd.params["value"])
            validate_reduction(name, value, self.effective(name))
            self.overrides[name] = value
            note = ""
            if name == "risk.max_leverage" and self.position:
                lev = self.position["size"] * self.mark / self._equity()
                if lev > value:
                    self.entries_blocked_reasons = [f"La posición abierta ({lev:.2f}×) supera el nuevo apalancamiento "
                                                    f"máximo ({value}×). No se cierra sola: se bloquean nuevas entradas."]
                    note = " " + self.entries_blocked_reasons[0]
                    self._event("warning", "risk", self.entries_blocked_reasons[0])
            return f"{REDUCIBLE_PARAMS[name]['label']}: {value} (override guardado; valor efectivo = el menor).{note}"
        raise CommandError(f"Comando no permitido: {t}")

    # ================================================================ backtest
    def backtest_data(self):
        """Datos para la pantalla de Backtest: velas sintéticas (en demo)."""
        from ..backtest_jobs import demo_backtest_candles
        if getattr(self, "_bt_candles", None) is None:
            self._bt_candles = demo_backtest_candles()
        return self._bt_candles, None, None, "Velas SINTÉTICAS de demostración (2 años, no son de mercado)"


def _es(v: float, dec: int = 4, signed: bool = False) -> str:
    """Número en formato español para los mensajes (1,1727 · −2,31)."""
    txt = f"{abs(v):,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    sign = "\u2212" if v < 0 else ("+" if signed and v > 0 else "")
    return sign + txt


def _cmd_label(cmd: Command) -> str:
    names = {"pause": "pausar", "resume": "reanudar", "close_position": "cerrar posición", "kill": "KILL SWITCH",
             "rearm": "rearmar", "reduce_param": "reducir parámetro"}
    extra = f" {cmd.params.get('name')} → {cmd.params.get('value')}" if cmd.type == "reduce_param" else ""
    return names.get(cmd.type, cmd.type) + extra


def _fmt_num(v: float, unit: str) -> str:
    if unit == "s":
        return f"{v:.0f} s"
    if unit == "pb":
        return f"{v:.1f} pb".replace(".", ",")
    return f"{v:.2f}".replace(".", ",") + (f" {unit}" if unit else "")
