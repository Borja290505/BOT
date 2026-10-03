"""Estrategia A: ruptura de canal Donchian + stop por ATR + trailing por canal de salida.

Reglas (evaluadas al CIERRE de cada vela de 1 h, solo con velas cerradas):
- Largo: cierre > máximo de las `entry_lookback` velas anteriores.
- Corto: cierre < mínimo de las `entry_lookback` velas anteriores.
- Stop inicial: precio de señal -/+ atr_stop_mult * ATR(atr_period).
- Take profit: entrada +/- take_profit_r * R (R = distancia entrada-stop).
- Trailing: tras cada cierre, el stop se mueve (nunca hacia atrás) al mínimo
  (largo) / máximo (corto) de las `exit_lookback` últimas velas.
- Filtro de funding opcional: no abrir largos si el funding relativo horario
  > umbral (los largos pagan mucho), ni cortos si < -umbral.

Este módulo es puro (sin red ni estado): lo usan igual el backtest y el bot en
vivo, de modo que ambos toman exactamente las mismas decisiones.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from ..config import StrategyCfg
from ..models import Side, Signal
from .indicators import atr, donchian_incl, donchian_prev


@dataclass(frozen=True)
class StrategyParams:
    entry_lookback: int = 20
    exit_lookback: int = 10
    atr_period: int = 14
    atr_stop_mult: float = 2.0
    take_profit_r: float = 4.0
    funding_filter: bool = False
    funding_max_abs_hourly: float = 0.0001

    @classmethod
    def from_cfg(cls, cfg: StrategyCfg) -> "StrategyParams":
        return cls(entry_lookback=cfg.entry_lookback, exit_lookback=cfg.exit_lookback,
                   atr_period=cfg.atr_period, atr_stop_mult=cfg.atr_stop_mult,
                   take_profit_r=cfg.take_profit_r, funding_filter=cfg.funding_filter.enabled,
                   funding_max_abs_hourly=cfg.funding_filter.max_abs_hourly_rate)

    def with_(self, **kw) -> "StrategyParams":
        return replace(self, **kw)

    @property
    def warmup_bars(self) -> int:
        return max(self.entry_lookback + 1, self.atr_period * 3, self.exit_lookback) + 1


def compute_indicators(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["close"] = df["close"]
    out["atr"] = atr(df, p.atr_period)
    out["dc_upper"], out["dc_lower"] = donchian_prev(df, p.entry_lookback)
    out["exit_high"], out["exit_low"] = donchian_incl(df, p.exit_lookback)
    valid = out[["atr", "dc_upper", "dc_lower"]].notna().all(axis=1)
    out["long_signal"] = valid & (df["close"] > out["dc_upper"])
    out["short_signal"] = valid & (df["close"] < out["dc_lower"])
    return out


def funding_rate_at(funding: pd.DataFrame | None, bar_open: pd.Timestamp) -> float | None:
    """Último funding relativo con timestamp <= apertura de la vela de señal.

    Conservador: no usa la tasa del periodo que empieza al cierre de la vela,
    aunque ya podría estar publicada, para no arriesgar sesgo de anticipación.
    """
    if funding is None or funding.empty:
        return None
    sub = funding.loc[:bar_open, "relative_rate"]
    return float(sub.iloc[-1]) if len(sub) else None


def funding_allows(side: Side, rate: float | None, p: StrategyParams) -> bool:
    if not p.funding_filter or rate is None:
        return True
    if side is Side.LONG:
        return rate <= p.funding_max_abs_hourly
    return rate >= -p.funding_max_abs_hourly


def take_profit_price(side: Side, entry: float, stop: float, take_profit_r: float) -> float:
    """Precio de take profit a partir de la entrada y del stop."""
    r = abs(entry - stop)
    return entry + side.sign * take_profit_r * r


def signal_at(ind: pd.DataFrame, i: int, p: StrategyParams,
              funding: pd.DataFrame | None = None) -> Signal | None:
    """Señal al cierre de la vela i (posición entera en ind), o None."""
    row = ind.iloc[i]
    if not (row["long_signal"] or row["short_signal"]) or not np.isfinite(row["atr"]) or row["atr"] <= 0:
        return None
    side = Side.LONG if row["long_signal"] else Side.SHORT
    t = ind.index[i]
    rate = funding_rate_at(funding, t)
    if not funding_allows(side, rate, p):
        return None
    ref = float(row["close"])
    stop = ref - side.sign * p.atr_stop_mult * float(row["atr"])
    if stop <= 0:
        return None
    tp = take_profit_price(side, ref, stop, p.take_profit_r)
    reason = (f"ruptura {'alcista' if side is Side.LONG else 'bajista'} canal {p.entry_lookback} "
              f"(canal={row['dc_upper'] if side is Side.LONG else row['dc_lower']:.5f}, "
              f"ATR={row['atr']:.5f}, funding={rate})")
    return Signal(time=t, side=side, ref_price=ref, stop_price=stop, take_profit_price=tp,
                  atr=float(row["atr"]), reason=reason)


def trailing_stop(side: Side, current_stop: float, exit_low: float, exit_high: float) -> float:
    """Nuevo stop tras el cierre de una vela: canal de salida, nunca retrocede."""
    if side is Side.LONG:
        return max(current_stop, float(exit_low)) if np.isfinite(exit_low) else current_stop
    return min(current_stop, float(exit_high)) if np.isfinite(exit_high) else current_stop
