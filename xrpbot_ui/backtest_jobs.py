"""Backtests lanzados desde la interfaz, en un proceso aparte.

Usa el motor de backtest del bot (sin red ni claves) y no toca el bot en
marcha. Un solo trabajo a la vez para no cargar la máquina del bot.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import replace

import numpy as np
import pandas as pd

DISCLAIMER = ("Resultados SIMULADOS sobre datos pasados, con comisiones, slippage y funding estimados. "
              "El rendimiento pasado no garantiza resultados futuros.")

LIMITS = {
    "entry_lookback": (5, 200, int), "exit_lookback": (2, 100, int), "atr_period": (5, 100, int),
    "atr_stop_mult": (0.5, 10.0, float), "take_profit_r": (0.5, 20.0, float), "slippage_bps": (0.0, 100.0, float),
}


class BacktestParamError(ValueError):
    pass


def parse_params(body: dict) -> dict:
    p = {}
    for k, (lo, hi, typ) in LIMITS.items():
        if k not in body or body[k] in ("", None):
            continue
        try:
            v = typ(body[k])
        except (TypeError, ValueError):
            raise BacktestParamError(f"{k}: valor no válido") from None
        if not lo <= v <= hi:
            raise BacktestParamError(f"{k}: debe estar entre {lo} y {hi}")
        p[k] = v
    if p.get("exit_lookback", 10) >= p.get("entry_lookback", 20):
        raise BacktestParamError("El canal de salida debe ser más corto que el de entrada")
    for k in ("start", "end"):
        if body.get(k):
            try:
                p[k] = pd.Timestamp(body[k], tz="UTC").isoformat()
            except ValueError:
                raise BacktestParamError(f"{k}: fecha no válida (AAAA-MM-DD)") from None
    p["walkforward"] = bool(body.get("walkforward"))
    p["funding_filter"] = bool(body.get("funding_filter"))
    return p


def _downsample(series: pd.Series, n: int = 1500) -> list[dict]:
    s = series.dropna()
    if len(s) > n:
        s = s.iloc[:: math.ceil(len(s) / n)]
    return [{"time": int(ts.timestamp()), "value": round(float(v), 4)} for ts, v in s.items()]


def _clean(m: dict) -> dict:
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in m.items()}


def run_backtest_job(params: dict, candles: pd.DataFrame, mark, funding, file_risk: dict, capital: float,
                     fees: tuple[float, float], data_note: str) -> dict:
    """Se ejecuta en el proceso de trabajo. Devuelve un dict serializable a JSON."""
    from xrpbot.backtest.engine import BacktestConfig, run_backtest
    from xrpbot.backtest.metrics import compute_metrics
    from xrpbot.backtest.regimes import classify_regimes, metrics_by_regime
    from xrpbot.backtest.walkforward import walk_forward
    from xrpbot.config import WalkForwardCfg
    from xrpbot.models import InstrumentSpec
    from xrpbot.strategy.donchian_atr import StrategyParams

    t0 = time.time()
    spec = InstrumentSpec("PF_XRPUSD", 1.0, 0.0001, 1.0, 1.0, 0.01)
    cfg = BacktestConfig(initial_capital=capital, spec=spec, risk_per_trade=file_risk["risk_per_trade"],
                         max_leverage=file_risk["max_leverage"], max_daily_loss=file_risk["max_daily_loss"],
                         maker_fee=fees[0], taker_fee=fees[1], slippage_bps=params.get("slippage_bps", 5.0))
    sp = StrategyParams(**{k: params[k] for k in ("entry_lookback", "exit_lookback", "atr_period",
                                                  "atr_stop_mult", "take_profit_r") if k in params},
                        funding_filter=params.get("funding_filter", False))
    start = pd.Timestamp(params["start"]) if params.get("start") else None
    end = pd.Timestamp(params["end"]) if params.get("end") else None
    res = run_backtest(candles, sp, cfg, funding, mark, start=start, end=end)
    m = _clean(compute_metrics(res.equity, res.trades, capital))
    eq = res.equity.dropna()
    dd = (eq / eq.cummax() - 1) * 100
    regimes = metrics_by_regime(res.trades, classify_regimes(candles))
    out = {
        "params": params, "data_note": data_note, "disclaimer": DISCLAIMER, "metrics": m,
        "equity": _downsample(eq), "drawdown": _downsample(dd), "skipped": res.skipped,
        "regimes": [] if regimes.empty else [
            {"regimen": idx, **_clean({k: float(v) for k, v in row.items()})} for idx, row in regimes.iterrows()],
        "funding": {"available": funding is not None and not getattr(funding, "empty", True),
                    "note": None if funding is not None else
                    "Comparación con/sin funding DESHABILITADA: no hay funding histórico (la descarga de Kraken falla; "
                    "tarea pendiente)."},
        "walkforward": None,
    }
    if params.get("walkforward"):
        span_months = (candles.index[-1] - candles.index[0]).days / 30.4
        wf_cfg = WalkForwardCfg() if span_months >= 27 else WalkForwardCfg(train_months=6, test_months=2,
                                                                          holdout_months=3, min_train_trades=10)
        wf = walk_forward(candles, sp, replace(cfg), wf_cfg, funding, mark)
        rows = []
        for r in wf.windows.to_dict("records"):
            rows.append({k: (v.isoformat() if isinstance(v, pd.Timestamp) else
                             (None if isinstance(v, float) and not math.isfinite(v) else v)) for k, v in r.items()})
        out["walkforward"] = {"windows": rows, "oos_metrics": _clean(wf.oos_metrics) if wf.oos_metrics else {},
                              "holdout_start": wf.holdout_start.isoformat(),
                              "config": {"train_months": wf_cfg.train_months, "test_months": wf_cfg.test_months,
                                         "holdout_months": wf_cfg.holdout_months}}
    out["elapsed_s"] = round(time.time() - t0, 2)
    return out


class BacktestRunner:
    def __init__(self, executor: Executor | None = None) -> None:
        self.executor = executor or ProcessPoolExecutor(max_workers=1)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    @classmethod
    def threaded(cls) -> "BacktestRunner":
        return cls(ThreadPoolExecutor(max_workers=1))

    def busy(self) -> bool:
        return any(j["status"] == "running" for j in self.jobs.values())

    def submit(self, *args) -> str:
        with self.lock:
            if self.busy():
                raise BacktestParamError("Ya hay un backtest en marcha; espera a que termine")
            job_id = uuid.uuid4().hex[:12]
            fut = self.executor.submit(run_backtest_job, *args)
            self.jobs[job_id] = {"status": "running", "future": fut, "started": time.time()}
            return job_id

    def get(self, job_id: str) -> dict | None:
        job = self.jobs.get(job_id)
        if not job:
            return None
        fut = job["future"]
        if job["status"] == "running" and fut.done():
            exc = fut.exception()
            if exc:
                job.update(status="error", error=str(exc))
            else:
                job.update(status="done", result=fut.result())
        return {k: v for k, v in job.items() if k != "future"}

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)


def demo_backtest_candles(seed: int = 11) -> pd.DataFrame:
    """2 años de velas sintéticas para la pantalla de Backtest en modo demo."""
    from .providers.demo import synthetic_candles
    end = pd.Timestamp.now(tz="UTC").floor("1h") - pd.Timedelta("1h")
    df = synthetic_candles(24 * 730, end, seed, start_price=0.6)
    return df.astype(np.float64)
