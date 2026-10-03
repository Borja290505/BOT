"""Métricas de rendimiento.

Sharpe y Sortino se calculan sobre rendimientos DIARIOS (00:00 UTC) y se
anualizan con 365 días (mercado 24/7), sin tipo libre de riesgo.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def max_drawdown(equity: pd.Series) -> float:
    """Máximo drawdown como fracción negativa (p. ej. -0.12 = -12 %)."""
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float((equity / peak - 1).min())


def compute_metrics(equity: pd.Series, trades: pd.DataFrame, initial_capital: float) -> dict:
    m: dict[str, float | int] = {}
    eq = equity.dropna()
    final = float(eq.iloc[-1]) if len(eq) else initial_capital
    m["capital_inicial"] = initial_capital
    m["capital_final"] = final
    m["rentabilidad_total"] = final / initial_capital - 1
    days = (eq.index[-1] - eq.index[0]).total_seconds() / 86400 if len(eq) > 1 else 0
    m["dias"] = round(days, 1)
    m["cagr"] = (final / initial_capital) ** (365 / days) - 1 if days > 30 and final > 0 else float("nan")
    m["max_drawdown"] = max_drawdown(eq)

    daily = eq.resample("1D").last().dropna().pct_change().dropna()
    if len(daily) > 1 and daily.std() > 0:
        m["sharpe"] = float(daily.mean() / daily.std() * math.sqrt(365))
        downside = daily[daily < 0]
        dd = math.sqrt((downside ** 2).sum() / len(daily)) if len(downside) else 0.0
        m["sortino"] = float(daily.mean() / dd * math.sqrt(365)) if dd > 0 else float("nan")
    else:
        m["sharpe"] = m["sortino"] = float("nan")
    m["calmar"] = m["cagr"] / abs(m["max_drawdown"]) if m["max_drawdown"] < 0 and not math.isnan(m["cagr"]) else float("nan")

    n = len(trades)
    m["num_operaciones"] = n
    if n:
        pnl = trades["net_pnl"]
        wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
        m["win_rate"] = len(wins) / n
        m["profit_factor"] = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf")
        m["expectativa_usd"] = float(pnl.mean())
        m["expectativa_r"] = float(trades["r_multiple"].mean())
        m["ganancia_media"] = float(wins.mean()) if len(wins) else 0.0
        m["perdida_media"] = float(losses.mean()) if len(losses) else 0.0
        m["mayor_perdida"] = float(pnl.min())
        m["racha_perdedora_max"] = _max_losing_streak(pnl.to_numpy())
        m["horas_medias_en_posicion"] = float(trades["bars_held"].mean())
        m["comisiones_totales"] = float(trades["fees"].sum())
        m["funding_neto"] = float(trades["funding"].sum())
        m["largos"] = int((trades["side"] == "long").sum())
        m["cortos"] = int((trades["side"] == "short").sum())
    else:
        for k in ("win_rate", "profit_factor", "expectativa_usd", "expectativa_r"):
            m[k] = float("nan")
    return m


def _max_losing_streak(pnl: np.ndarray) -> int:
    best = cur = 0
    for x in pnl:
        cur = cur + 1 if x <= 0 else 0
        best = max(best, cur)
    return best


def format_metrics(m: dict) -> str:
    pct = {"rentabilidad_total", "cagr", "max_drawdown", "win_rate"}
    lines = []
    for k, v in m.items():
        if isinstance(v, float) and k in pct:
            lines.append(f"  {k:28s} {v:9.2%}")
        elif isinstance(v, float):
            lines.append(f"  {k:28s} {v:12.4f}")
        else:
            lines.append(f"  {k:28s} {v}")
    return "\n".join(lines)
