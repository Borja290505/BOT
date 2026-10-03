"""Clasificación de regímenes de mercado para evaluar la estrategia por tramos.

Se usan solo datos pasados (ventana móvil de 30 días):
- alta_volatilidad: volatilidad realizada en el percentil >= 80 de la serie.
- tendencia_alcista / tendencia_bajista: |rendimiento 30 d| > 1 desviación
  típica de 30 d (escalada).
- lateral: el resto.
Es una clasificación para el INFORME, no para operar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .metrics import compute_metrics

WINDOW = 24 * 30


def classify_regimes(candles: pd.DataFrame) -> pd.Series:
    close = candles["close"]
    ret = np.log(close).diff()
    vol = ret.rolling(WINDOW, min_periods=WINDOW // 2).std()
    trend = np.log(close).diff(WINDOW)
    trend_scale = vol * np.sqrt(WINDOW)
    vol_hi = vol.expanding(min_periods=WINDOW).quantile(0.8)
    reg = pd.Series("lateral", index=candles.index)
    reg[trend > trend_scale] = "tendencia_alcista"
    reg[trend < -trend_scale] = "tendencia_bajista"
    reg[vol >= vol_hi] = "alta_volatilidad"
    reg[vol.isna()] = "sin_datos"
    return reg


def metrics_by_regime(trades: pd.DataFrame, regimes: pd.Series) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    t = trades.copy()
    t["regimen"] = regimes.reindex(pd.DatetimeIndex(t["signal_time"])).to_numpy()
    rows = []
    for reg, g in t.groupby("regimen"):
        pnl = g["net_pnl"]
        losses = -pnl[pnl <= 0].sum()
        rows.append({
            "regimen": reg, "operaciones": len(g), "pnl_neto": pnl.sum(),
            "win_rate": (pnl > 0).mean(), "expectativa_r": g["r_multiple"].mean(),
            "profit_factor": pnl[pnl > 0].sum() / losses if losses > 0 else float("inf"),
        })
    return pd.DataFrame(rows).set_index("regimen")


__all__ = ["classify_regimes", "metrics_by_regime", "compute_metrics"]
