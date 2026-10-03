"""Indicadores sin sesgo de anticipación.

Regla: el valor en la fila i solo usa datos de velas <= i (conocidos al CIERRE
de la vela i). Los canales de entrada excluyen la propia vela i, para que
"cierre > máximo de las N velas anteriores" sea una ruptura real.
"""
from __future__ import annotations

import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return tr


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR de Wilder (media móvil exponencial con alpha = 1/period)."""
    out = true_range(df).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return out


def donchian_prev(df: pd.DataFrame, lookback: int) -> tuple[pd.Series, pd.Series]:
    """Máximo y mínimo de las `lookback` velas ANTERIORES (excluye la vela actual)."""
    upper = df["high"].shift(1).rolling(lookback, min_periods=lookback).max()
    lower = df["low"].shift(1).rolling(lookback, min_periods=lookback).min()
    return upper, lower


def donchian_incl(df: pd.DataFrame, lookback: int) -> tuple[pd.Series, pd.Series]:
    """Máximo y mínimo de las `lookback` velas HASTA la actual incluida (para el trailing)."""
    upper = df["high"].rolling(lookback, min_periods=lookback).max()
    lower = df["low"].rolling(lookback, min_periods=lookback).min()
    return upper, lower
