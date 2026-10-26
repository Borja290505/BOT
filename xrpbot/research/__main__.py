"""Investigación de la estrategia según el plan fijado en docs/research/PLAN.md.

    python -m xrpbot.research [--out reports/research]

Necesita los datos de `python -m xrpbot.cli download`. No usa claves ni red.
Escribe un informe en Markdown (informe.md) y CSV de apoyo. El holdout solo se
evalúa si la candidata supera la fase C, y una sola vez.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path

import pandas as pd

from ..backtest.engine import BacktestConfig, run_backtest
from ..backtest.metrics import compute_metrics
from ..backtest.walkforward import walk_forward
from ..config import WalkForwardCfg, load_settings
from ..strategy.donchian_atr import StrategyParams

TIMEFRAMES = {"1h": 1, "4h": 4, "1d": 24}
CHANNELS = {"20/10": (20, 10), "55/20": (55, 20)}
FILTERS = {"sin filtro": 0, "EMA 200": 200}
MIN_TRADES_SCREEN = 50
MAX_DD_SCREEN = -0.30
MIN_PF_SCREEN = 1.1
HOLDOUT_MONTHS = 12

DISCLAIMER = "> Resultados simulados sobre datos pasados. No garantizan resultados futuros."


# ------------------------------------------------------------------ datos
def resample(df: pd.DataFrame | None, hours: int) -> pd.DataFrame | None:
    """Agrega velas de 1 h en velas de N horas (UTC). Descarta las incompletas."""
    if df is None or hours == 1:
        return df
    rule = f"{hours}h"
    g = df.resample(rule, label="left", closed="left", origin="epoch")
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                        "low": g["low"].min(), "close": g["close"].last()})
    if "volume" in df.columns:
        out["volume"] = g["volume"].sum()
    count = g["close"].count()
    out = out[count >= 0.75 * hours].dropna(subset=["open", "close"])
    last_full = df.index[-1] + pd.Timedelta("1h") - pd.Timedelta(hours=hours)
    return out[out.index <= last_full]


def params_for(channel: str, ema: int, atr_mult: float = 2.0) -> StrategyParams:
    e, x = CHANNELS[channel]
    return StrategyParams(entry_lookback=e, exit_lookback=x, atr_period=14, atr_stop_mult=atr_mult,
                          take_profit_r=4.0, funding_filter=False, trend_ema=ema)


def fmt_pct(v) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:+.1%}"


def fmt(v, d=2) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v))):
        return "—"
    if isinstance(v, float) and math.isinf(v):
        return "∞"
    return f"{v:.{d}f}"


def summary_row(name: str, m: dict) -> dict:
    return {"config": name, "ops": m.get("num_operaciones", 0), "rent": m.get("rentabilidad_total"),
            "dd": m.get("max_drawdown"), "pf": m.get("profit_factor"), "exp_r": m.get("expectativa_r"),
            "sharpe": m.get("sharpe"), "win": m.get("win_rate"), "comisiones": m.get("comisiones_totales")}


def md_table(rows: list[dict]) -> str:
    head = "| Configuración | Ops | Rentab. | Máx. DD | PF | Expect. R | Sharpe | Acierto | Comisiones USD |\n" \
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    lines = [head]
    for r in rows:
        lines.append(f"| {r['config']} | {r['ops']} | {fmt_pct(r['rent'])} | {fmt_pct(r['dd'])} | {fmt(r['pf'])} | "
                     f"{fmt(r['exp_r'], 3)} | {fmt(r['sharpe'])} | {fmt_pct(r['win'])} | {fmt(r['comisiones'], 0)} |")
    return "\n".join(lines)


# ------------------------------------------------------------------ fase A
def diagnose(candles, mark, cfg: BacktestConfig, out: Path) -> str:
    base = params_for("20/10", 0)
    r = run_backtest(candles, base, cfg, None, mark)
    m = compute_metrics(r.equity, r.trades, cfg.initial_capital)
    free = run_backtest(candles, base, replace(cfg, taker_fee=0.0, maker_fee=0.0, slippage_bps=0.0), None, mark)
    mf = compute_metrics(free.equity, free.trades, cfg.initial_capital)
    t = r.trades.copy()
    t.to_csv(out / "A_referencia_operaciones.csv", index=False)
    lines = ["## Fase A · Diagnóstico de la referencia (Donchian 20/10, 1 h, todos los datos)", "",
             md_table([summary_row("Con costes", m), summary_row("SIN costes (comisiones y slippage = 0)", mf)]), ""]
    if t.empty:
        return "\n".join(lines + ["Sin operaciones."])
    gross, fees = t["gross_pnl"].sum(), t["fees"].sum()
    months = max(1.0, (candles.index[-1] - candles.index[0]).days / 30.4)
    lines += [f"- Resultado bruto de las operaciones (antes de costes): **{gross:+,.0f} USD**; "
              f"comisiones: **{fees:,.0f} USD** ({len(t) / months:.1f} operaciones/mes).",
              f"- Duración media: {t['bars_held'].mean():.1f} velas; "
              f"pérdida media {t.loc[t.net_pnl <= 0, 'r_multiple'].mean():+.2f} R, "
              f"ganancia media {t.loc[t.net_pnl > 0, 'r_multiple'].mean():+.2f} R.", ""]
    t["año"] = pd.to_datetime(t["entry_time"]).dt.year

    def group(col: str, title: str) -> list[str]:
        g = t.groupby(col).agg(ops=("net_pnl", "size"), neto=("net_pnl", "sum"), bruto=("gross_pnl", "sum"),
                               comisiones=("fees", "sum"), exp_r=("r_multiple", "mean"),
                               acierto=("net_pnl", lambda x: (x > 0).mean()))
        rows = [f"### {title}", "", "| | Ops | Neto USD | Bruto USD | Comisiones USD | Expect. R | Acierto |",
                "|---|---:|---:|---:|---:|---:|---:|"]
        for k, v in g.iterrows():
            rows.append(f"| {k} | {int(v.ops)} | {v.neto:+,.0f} | {v.bruto:+,.0f} | {v.comisiones:,.0f} | "
                        f"{v.exp_r:+.3f} | {v.acierto:.0%} |")
        return rows + [""]

    lines += group("side", "Por lado") + group("año", "Por año") + group("exit_reason", "Por motivo de salida")
    return "\n".join(lines)


# ------------------------------------------------------------------ fases B-D
def screen(market: dict, cfg: BacktestConfig, dev_end: pd.Timestamp) -> tuple[list[dict], dict | None]:
    rows = []
    for tf, hours in TIMEFRAMES.items():
        candles, mark = market[tf]
        tcfg = replace(cfg, bar_hours=hours)
        for ch in CHANNELS:
            for fname, ema in FILTERS.items():
                p = params_for(ch, ema)
                r = run_backtest(candles, p, tcfg, None, mark, end=dev_end)
                m = compute_metrics(r.equity, r.trades, cfg.initial_capital)
                row = summary_row(f"{tf} · {ch} · {fname}", m)
                row.update(tf=tf, channel=ch, ema=ema)
                rows.append(row)
    eligible = [r for r in rows if r["ops"] >= MIN_TRADES_SCREEN and (r["dd"] or -1) >= MAX_DD_SCREEN
                and r["pf"] is not None and not math.isnan(r["pf"])]
    eligible.sort(key=lambda r: (r["pf"], r["sharpe"] if r["sharpe"] == r["sharpe"] else -9), reverse=True)
    best = eligible[0] if eligible and eligible[0]["pf"] >= MIN_PF_SCREEN else None
    return rows, best


def validate(best: dict, market: dict, cfg: BacktestConfig, dev_end: pd.Timestamp) -> tuple[list[str], bool]:
    tf, hours = best["tf"], TIMEFRAMES[best["tf"]]
    candles, mark = market[tf]
    dev = candles[candles.index < dev_end]
    dmark = mark[mark.index < dev_end] if mark is not None else None
    tcfg = replace(cfg, bar_hours=hours)
    base = params_for(best["channel"], best["ema"])
    wcfg = WalkForwardCfg(train_months=12, test_months=3, holdout_months=0,
                          min_train_trades={"1h": 20, "4h": 10, "1d": 5}[tf],
                          grid={"entry_lookback": [20, 55], "exit_lookback": [10, 20], "atr_stop_mult": [2.0, 3.0]})
    wf = walk_forward(dev, base, tcfg, wcfg, None, dmark)
    om = wf.oos_metrics or {}
    win_share = float((wf.windows["rent_test"] > 0).mean()) if not wf.windows.empty else 0.0
    stress = run_backtest(candles, base, replace(tcfg, slippage_bps=15.0, taker_fee=cfg.taker_fee * 1.5,
                                                 maker_fee=cfg.maker_fee * 1.5), None, mark, end=dev_end)
    sm = compute_metrics(stress.equity, stress.trades, cfg.initial_capital)
    checks = [
        ("Operaciones OOS ≥ 100", om.get("num_operaciones", 0) >= 100, f"{om.get('num_operaciones', 0)}"),
        ("Profit factor OOS ≥ 1,2", (om.get("profit_factor") or 0) >= 1.2, fmt(om.get("profit_factor"))),
        ("Expectativa OOS ≥ 0,10 R", (om.get("expectativa_r") or -9) >= 0.10, fmt(om.get("expectativa_r"), 3)),
        ("Drawdown OOS ≤ 20 %", (om.get("max_drawdown") or -1) >= -0.20, fmt_pct(om.get("max_drawdown"))),
        ("Sharpe OOS ≥ 0,8", (om.get("sharpe") or -9) >= 0.8, fmt(om.get("sharpe"))),
        ("Ganancia en ≥ 60 % de ventanas", win_share >= 0.6, f"{win_share:.0%} de {len(wf.windows)}"),
        ("Robustez (15 pb, comisiones ×1,5): expectativa > 0", (sm.get("expectativa_r") or -9) > 0,
         fmt(sm.get("expectativa_r"), 3)),
    ]
    ok = all(c[1] for c in checks)
    lines = [f"## Fase C · Validación de la candidata «{best['config']}» (solo desarrollo)", "",
             "| Criterio | Resultado | ¿Pasa? |", "|---|---:|:---:|"]
    lines += [f"| {n} | {v} | {'✔' if p else '✖'} |" for n, p, v in checks]
    lines += ["", "Walk-forward (parámetros elegidos en cada ventana con datos de entrenamiento):", "", "```",
              wf.windows.round({c: 3 for c in wf.windows.select_dtypes("number").columns}).to_string(index=False) if not wf.windows.empty else "(sin ventanas)", "```", ""]
    return lines, ok


def holdout(best: dict, market: dict, cfg: BacktestConfig, dev_end: pd.Timestamp) -> tuple[list[str], bool]:
    tf = best["tf"]
    candles, mark = market[tf]
    r = run_backtest(candles, params_for(best["channel"], best["ema"]), replace(cfg, bar_hours=TIMEFRAMES[tf]),
                     None, mark, start=dev_end)
    m = compute_metrics(r.equity, r.trades, cfg.initial_capital)
    ok = (m.get("profit_factor") or 0) >= 1.1 and (m.get("max_drawdown") or -1) >= -0.20
    return ["## Fase D · Holdout (evaluado una sola vez)", "", md_table([summary_row(best["config"], m)]), "",
            f"Criterio PF ≥ 1,1 y drawdown ≤ 20 %: **{'PASA' if ok else 'NO PASA'}**", ""], ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="xrpbot.research")
    ap.add_argument("--config", default="config/settings.yaml")
    ap.add_argument("--out", default="reports/research")
    args = ap.parse_args(argv)
    from ..cli import _bt_config, _load_market
    s = load_settings(args.config, mode_override="backtest", env_file=None)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    candles, mark, _funding = _load_market(s)
    cfg = _bt_config(s, "futures")
    data_end = candles.index[-1] + pd.Timedelta("1h")
    dev_end = data_end - pd.DateOffset(months=HOLDOUT_MONTHS)
    market = {tf: (resample(candles, h), resample(mark, h)) for tf, h in TIMEFRAMES.items()}

    rep = ["# Informe de investigación · PF_XRPUSD", "", DISCLAIMER, "",
           f"Datos: {candles.index[0]:%Y-%m-%d} → {candles.index[-1]:%Y-%m-%d %H:%M} UTC · "
           f"desarrollo hasta {dev_end:%Y-%m-%d} · holdout desde {dev_end:%Y-%m-%d} (12 meses). "
           f"Costes de futuros: maker {cfg.maker_fee:.3%}, taker {cfg.taker_fee:.3%}, slippage {cfg.slippage_bps:g} pb; "
           "sin funding. Plan: docs/research/PLAN.md.", ""]
    rep.append(diagnose(candles, mark, cfg, out))
    rows, best = screen(market, cfg, dev_end)
    pd.DataFrame(rows).to_csv(out / "B_cribado.csv", index=False)
    rep += ["## Fase B · Cribado de 12 configuraciones fijas (solo desarrollo)", "", md_table(rows), "",
            f"Regla: ≥ {MIN_TRADES_SCREEN} operaciones, drawdown ≥ {MAX_DD_SCREEN:.0%}, mayor PF; mínimo PF {MIN_PF_SCREEN}.", ""]
    verdict = {"candidata": None, "fase_c": None, "holdout": None}
    if best is None:
        rep += ["**Ninguna configuración cumple la regla de selección.** La investigación termina aquí "
                "y el holdout queda sin tocar.", ""]
    else:
        verdict["candidata"] = best["config"]
        rep += [f"**Candidata: {best['config']}** (PF {fmt(best['pf'])} en desarrollo).", ""]
        lines, ok = validate(best, market, cfg, dev_end)
        verdict["fase_c"] = ok
        rep += lines
        if ok:
            lines, hok = holdout(best, market, cfg, dev_end)
            verdict["holdout"] = hok
            rep += lines
        else:
            rep += ["La candidata **no supera la fase C**: no se evalúa el holdout y no se usa la estrategia.", ""]
    conclusion = ("La candidata supera todas las fases: pasa a SIM durante el plan de pruebas (Fase 2). "
                  "No se activa el modo real." if verdict["holdout"] else
                  "No hay ninguna configuración que supere los criterios fijados de antemano. "
                  "**No se recomienda operar esta familia de estrategias en real.** El bot sigue en SIM.")
    rep += ["## Conclusión", "", conclusion, "", DISCLAIMER, ""]
    text = "\n".join(rep)
    (out / "informe.md").write_text(text, encoding="utf-8")
    (out / "veredicto.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
