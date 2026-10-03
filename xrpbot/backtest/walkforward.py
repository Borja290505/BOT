"""Validación walk-forward con tramo final de prueba intocable (holdout).

    |------ train 12m ------|-- test 3m --|
                |------ train 12m ------|-- test 3m --|
                            ...                            |--- holdout 6m ---|

- En cada ventana se elige la mejor combinación de la rejilla SOLO con datos
  de entrenamiento (Sharpe, con un mínimo de operaciones) y se evalúa en el
  tramo de test siguiente, que el optimizador no ha visto.
- El resultado fuera de muestra (OOS) es la concatenación de los tramos de test.
- El holdout final se evalúa UNA vez, con parámetros fijados de antemano. Si se
  repite el ciclo "mirar holdout -> cambiar estrategia", deja de ser fuera de
  muestra.

Contra el sobreajuste: rejilla pequeña (12 combinaciones), pocos parámetros y
un informe de estabilidad (si los parámetros elegidos saltan de una ventana a
otra, es mala señal).
"""
from __future__ import annotations

import itertools
import logging
import math
from dataclasses import dataclass, replace

import pandas as pd

from ..config import WalkForwardCfg
from ..strategy.donchian_atr import StrategyParams
from .engine import BacktestConfig, run_backtest
from .metrics import compute_metrics

log = logging.getLogger(__name__)


@dataclass
class WalkForwardResult:
    windows: pd.DataFrame
    oos_trades: pd.DataFrame
    oos_equity: pd.Series
    oos_metrics: dict
    holdout_start: pd.Timestamp
    last_params: StrategyParams | None


def param_grid(base: StrategyParams, grid: dict[str, list]) -> list[StrategyParams]:
    keys = list(grid)
    out = []
    for values in itertools.product(*(grid[k] for k in keys)):
        p = base.with_(**dict(zip(keys, values)))
        if p.exit_lookback < p.entry_lookback:
            out.append(p)
    return out


def _score(m: dict, min_trades: int) -> float:
    if m["num_operaciones"] < min_trades:
        return -math.inf
    s = m.get("sharpe", float("nan"))
    return s if not math.isnan(s) else -math.inf


def walk_forward(candles: pd.DataFrame, base: StrategyParams, cfg: BacktestConfig,
                 wf: WalkForwardCfg, funding: pd.DataFrame | None = None,
                 mark: pd.DataFrame | None = None) -> WalkForwardResult:
    data_end = candles.index[-1] + pd.Timedelta("1h")
    holdout_start = data_end - pd.DateOffset(months=wf.holdout_months)
    grid = param_grid(base, wf.grid)
    max_warmup = max(p.warmup_bars for p in grid)
    train_start = candles.index[min(max_warmup, len(candles) - 1)]

    rows, trades, equities = [], [], []
    capital = cfg.initial_capital
    last_params = None
    while True:
        train_end = train_start + pd.DateOffset(months=wf.train_months)
        test_end = train_end + pd.DateOffset(months=wf.test_months)
        if test_end > holdout_start:
            break
        best, best_score = None, -math.inf
        for p in grid:
            r = run_backtest(candles, p, cfg, funding, mark, start=train_start, end=train_end)
            s = _score(compute_metrics(r.equity, r.trades, cfg.initial_capital), wf.min_train_trades)
            if s > best_score:
                best, best_score = p, s
        if best is None:
            log.warning("Ventana %s-%s: ninguna combinación con suficientes operaciones",
                        train_start.date(), train_end.date())
            train_start += pd.DateOffset(months=wf.test_months)
            continue
        test_cfg = replace(cfg, initial_capital=capital)
        r = run_backtest(candles, best, test_cfg, funding, mark, start=train_end, end=test_end)
        tm = compute_metrics(r.equity, r.trades, capital)
        capital = tm["capital_final"]
        last_params = best
        rows.append({
            "train_inicio": train_start, "test_inicio": train_end, "test_fin": test_end,
            "entry_lookback": best.entry_lookback, "exit_lookback": best.exit_lookback,
            "atr_stop_mult": best.atr_stop_mult, "sharpe_train": best_score,
            "sharpe_test": tm["sharpe"], "rent_test": tm["rentabilidad_total"],
            "dd_test": tm["max_drawdown"], "ops_test": tm["num_operaciones"],
        })
        if not r.trades.empty:
            trades.append(r.trades)
        equities.append(r.equity)
        train_start += pd.DateOffset(months=wf.test_months)

    oos_trades = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame()
    oos_equity = pd.concat(equities) if equities else pd.Series(dtype=float)
    oos_equity = oos_equity[~oos_equity.index.duplicated(keep="last")]
    metrics = compute_metrics(oos_equity, oos_trades, cfg.initial_capital) if len(oos_equity) else {}
    return WalkForwardResult(pd.DataFrame(rows), oos_trades, oos_equity, metrics, holdout_start, last_params)


def evaluate_holdout(candles: pd.DataFrame, params: StrategyParams, cfg: BacktestConfig,
                     holdout_start: pd.Timestamp, funding: pd.DataFrame | None = None,
                     mark: pd.DataFrame | None = None):
    r = run_backtest(candles, params, cfg, funding, mark, start=holdout_start)
    return r, compute_metrics(r.equity, r.trades, cfg.initial_capital)
