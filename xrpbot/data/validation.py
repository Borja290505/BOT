"""Validación de datos de mercado.

Se valida antes de cada backtest y antes de cada decisión en vivo. Un dato
incoherente no se "arregla" inventando valores: se reporta y, en vivo, se
bloquean las entradas nuevas.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class ValidationReport:
    n_rows: int
    duplicates: int = 0
    gaps: list[tuple[pd.Timestamp, pd.Timestamp]] = field(default_factory=list)
    bad_ohlc: int = 0
    non_positive: int = 0
    zero_volume: int = 0

    @property
    def missing_bars(self) -> int:
        return sum(int((b - a) / pd.Timedelta("1h")) - 1 for a, b in self.gaps)

    @property
    def ok(self) -> bool:
        return self.duplicates == 0 and self.bad_ohlc == 0 and self.non_positive == 0

    def summary(self) -> str:
        return (f"filas={self.n_rows} duplicados={self.duplicates} huecos={len(self.gaps)} "
                f"(velas que faltan={self.missing_bars}) OHLC incoherente={self.bad_ohlc} "
                f"precios<=0={self.non_positive} volumen 0={self.zero_volume}")


def validate_candles(df: pd.DataFrame, freq: str = "1h") -> ValidationReport:
    rep = ValidationReport(n_rows=len(df))
    if df.empty:
        return rep
    rep.duplicates = int(df.index.duplicated().sum())
    idx = df.index[~df.index.duplicated()].sort_values()
    step = pd.Timedelta(freq)
    diffs = idx[1:] - idx[:-1]
    for i in (diffs > step).nonzero()[0]:
        rep.gaps.append((idx[i], idx[i + 1]))
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rep.bad_ohlc = int(((h < l) | (h < o) | (h < c) | (l > o) | (l > c)).sum())
    rep.non_positive = int(((o <= 0) | (h <= 0) | (l <= 0) | (c <= 0)).sum())
    if "volume" in df:
        rep.zero_volume = int((df["volume"] <= 0).sum())
    return rep


def drop_incomplete_last_candle(df: pd.DataFrame, now: pd.Timestamp, freq: str = "1h") -> pd.DataFrame:
    """Elimina velas que aún no han cerrado (evita sesgo de anticipación en vivo)."""
    return df[df.index + pd.Timedelta(freq) <= now]


def candles_are_stale(df: pd.DataFrame, now: pd.Timestamp, max_delay_bars: int, freq: str = "1h") -> bool:
    """True si la última vela cerrada es más vieja de lo tolerable."""
    if df.empty:
        return True
    last_close = df.index[-1] + pd.Timedelta(freq)
    return now - last_close > pd.Timedelta(freq) * max_delay_bars
