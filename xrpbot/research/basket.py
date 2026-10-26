"""Experimento 2: Donchian diario sobre una cesta de perpetuos (docs/research/PLAN-2.md).

    python -m xrpbot.cli download --symbols PF_XBTUSD PF_ETHUSD ...   # datos
    python -m xrpbot.research.basket [--out reports/research2]

Cada contrato opera en una subcuenta con la misma parte del capital; la cartera
es la suma. El holdout solo se evalúa si la elegida supera la fase C, una vez.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd

from ..backtest.engine import BacktestConfig, run_backtest
from ..backtest.metrics import compute_metrics
from ..config import load_settings
from ..data.store import load_candles, load_funding
from ..exchange.instruments import parse_instrument
from ..strategy.donchian_atr import StrategyParams
from .__main__ import DISCLAIMER, fmt, fmt_pct, md_table, resample, summary_row

UNIVERSE = ["PF_XBTUSD", "PF_ETHUSD", "PF_SOLUSD", "PF_XRPUSD", "PF_ADAUSD", "PF_DOGEUSD", "PF_LTCUSD",
            "PF_LINKUSD", "PF_DOTUSD", "PF_AVAXUSD", "PF_BCHUSD", "PF_XLMUSD"]
CONFIGS = {
    "20/10 · TP 4R": dict(entry_lookback=20, exit_lookback=10, take_profit_r=4.0),
    "20/10 · sin TP": dict(entry_lookback=20, exit_lookback=10, take_profit_r=1000.0),
    "55/20 · TP 4R": dict(entry_lookback=55, exit_lookback=20, take_profit_r=4.0),
    "55/20 · sin TP": dict(entry_lookback=55, exit_lookback=20, take_profit_r=1000.0),
}
HOLDOUT_MONTHS = 12
MIN_DEV_MONTHS = 24
MIN_TRADES, MAX_DD, MIN_PF = 100, -0.25, 1.2


def params_for(name: str) -> StrategyParams:
    return StrategyParams(atr_period=14, atr_stop_mult=2.0, funding_filter=False, **CONFIGS[name])


def load_basket(data_dir: str, dev_end: pd.Timestamp | None = None) -> tuple[dict, list[str]]:
    """{símbolo: (velas 1d, mark 1d, funding diario | None, spec)} y notas de exclusión."""
    instruments = json.loads((Path(data_dir) / "instruments.json").read_text())
    market, notes = {}, []
    for sym in UNIVERSE:
        try:
            c1h = load_candles(data_dir, sym, "trade")
        except FileNotFoundError:
            notes.append(f"{sym}: sin datos descargados (excluido)")
            continue
        try:
            spec = parse_instrument(instruments, sym)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{sym}: sin especificación en /instruments ({exc}) (excluido)")
            continue
        try:
            m1h = load_candles(data_dir, sym, "mark")
        except FileNotFoundError:
            m1h = None
        fund = None
        try:
            f = load_funding(data_dir, sym)
            if len(f):
                fund = f[["relative_rate"]].resample("24h").sum()
        except FileNotFoundError:
            pass
        if fund is None:
            notes.append(f"{sym}: sin funding histórico (su resultado no incluye funding)")
        market[sym] = (resample(c1h, 24), resample(m1h, 24), fund, spec)
    return market, notes


def eligible(market: dict, dev_end: pd.Timestamp, notes: list[str]) -> dict:
    out = {}
    for sym, v in market.items():
        start = v[0].index[0]
        if start <= dev_end - pd.DateOffset(months=MIN_DEV_MONTHS):
            out[sym] = v
        else:
            notes.append(f"{sym}: datos desde {start:%Y-%m-%d}, menos de {MIN_DEV_MONTHS} meses antes del "
                         "fin del desarrollo (excluido)")
    return out


def run_portfolio(market: dict, params: StrategyParams, cfg: BacktestConfig,
                  start: pd.Timestamp | None = None, end: pd.Timestamp | None = None,
                  with_funding: bool = True, skipped: dict | None = None) -> tuple[pd.Series, pd.DataFrame, float]:
    share = cfg.initial_capital / len(market)
    equities, trades = [], []
    for sym, (candles, mark, fund, spec) in market.items():
        scfg = replace(cfg, initial_capital=share, spec=spec, bar_hours=24)
        r = run_backtest(candles, params, scfg, fund if with_funding else None, mark, start=start, end=end)
        equities.append(r.equity.rename(sym))
        if skipped is not None:
            skipped[sym] = r.skipped
        if not r.trades.empty:
            trades.append(r.trades.assign(symbol=sym))
    eq = pd.concat(equities, axis=1).sort_index().ffill().fillna(share).sum(axis=1)
    tr = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame(
        columns=["net_pnl", "r_multiple", "symbol", "fees", "funding", "bars_held", "side"])
    return eq, tr, cfg.initial_capital


def pf_of(trades: pd.DataFrame) -> float:
    if trades.empty:
        return float("nan")
    w, lo = trades.net_pnl[trades.net_pnl > 0].sum(), trades.net_pnl[trades.net_pnl <= 0].sum()
    return float(w / -lo) if lo < 0 else float("inf")


def quarters_positive(eq: pd.Series) -> tuple[float, int]:
    q = eq.resample("QE").last()
    q = pd.concat([eq.iloc[:1], q])
    ch = q.pct_change().dropna()
    return (float((ch > 0).mean()) if len(ch) else 0.0), len(ch)


def per_symbol_table(trades: pd.DataFrame) -> list[str]:
    if trades.empty:
        return ["(sin operaciones)"]
    g = trades.groupby("symbol").agg(ops=("net_pnl", "size"), neto=("net_pnl", "sum"),
                                     funding=("funding", "sum"), exp_r=("r_multiple", "mean"))
    rows = ["| Contrato | Ops | Neto USD | Funding USD | Expect. R | PF |", "|---|---:|---:|---:|---:|---:|"]
    for sym, v in g.sort_values("neto", ascending=False).iterrows():
        rows.append(f"| {sym} | {int(v.ops)} | {v.neto:+,.0f} | {v.funding:+,.0f} | {v.exp_r:+.3f} | "
                    f"{fmt(pf_of(trades[trades.symbol == sym]))} |")
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="xrpbot.research.basket")
    ap.add_argument("--config", default="config/settings.yaml")
    ap.add_argument("--out", default="reports/research2")
    args = ap.parse_args(argv)
    from ..cli import _bt_config
    s = load_settings(args.config, mode_override="backtest", env_file=None)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = _bt_config(s, "futures")
    market, notes = load_basket(s.backtest.data_dir)
    if not market:
        raise SystemExit("No hay datos de ningún contrato: ejecuta primero download --symbols ...")
    data_end = max(v[0].index[-1] for v in market.values()) + pd.Timedelta("24h")
    dev_end = data_end - pd.DateOffset(months=HOLDOUT_MONTHS)
    market = eligible(market, dev_end, notes)
    rep = ["# Experimento 2 · Cesta diaria de perpetuos", "", DISCLAIMER, "",
           f"Fin de los datos {data_end:%Y-%m-%d} · desarrollo hasta {dev_end:%Y-%m-%d} · holdout 12 meses. "
           f"Comisiones maker {cfg.maker_fee:.3%} / taker {cfg.taker_fee:.3%}, slippage {cfg.slippage_bps:g} pb. "
           f"Capital {cfg.initial_capital:,.0f} USD repartido entre {len(market)} contratos. Plan: docs/research/PLAN-2.md.",
           "", f"Contratos en la cesta: {', '.join(market) or '—'}", ""]
    if notes:
        rep += ["Notas:", ""] + [f"- {n}" for n in notes] + [""]
    verdict = {"contratos": list(market), "elegida": None, "fase_c": None, "holdout": None}
    if len(market) < 3:
        rep += ["**Menos de 3 contratos con datos suficientes: el experimento no es válido.**", ""]
    else:
        rows, results = [], {}
        for name in CONFIGS:
            skipped: dict = {}
            eq, tr, cap = run_portfolio(market, params_for(name), cfg, end=dev_end, skipped=skipped)
            m = compute_metrics(eq, tr, cap)
            results[name] = (eq, tr, m, skipped)
            rows.append(summary_row(name, m))
        pd.DataFrame(rows).to_csv(out / "B_cribado.csv", index=False)
        rep += ["## Fase B · 4 configuraciones (solo desarrollo)", "", md_table(rows), "",
                f"Regla: ≥ {MIN_TRADES} operaciones, drawdown ≥ {MAX_DD:.0%}, mayor PF; mínimo PF {MIN_PF}.", ""]
        ok_rows = [r for r in rows if r["ops"] >= MIN_TRADES and (r["dd"] or -1) >= MAX_DD
                   and r["pf"] == r["pf"] and r["pf"] >= MIN_PF]
        best = max(ok_rows, key=lambda r: r["pf"]) if ok_rows else None
        if best is None:
            rep += ["**Ninguna configuración cumple la regla de selección.** Termina aquí; holdout sin tocar.", ""]
        else:
            name = best["config"]
            verdict["elegida"] = name
            eq, tr, m, skipped = results[name]
            p = params_for(name)
            tiny = {k: v["below_min"] for k, v in skipped.items() if v.get("below_min")}
            if tiny:
                rep += ["Señales descartadas por tamaño mínimo del contrato (capital de la subcuenta demasiado "
                        f"pequeño): {tiny}", ""]
            stress_cfg = replace(cfg, slippage_bps=15.0, taker_fee=cfg.taker_fee * 1.5, maker_fee=cfg.maker_fee * 1.5)
            _, str_tr, _ = run_portfolio(market, p, stress_cfg, end=dev_end)
            top = tr.groupby("symbol").net_pnl.sum().idxmax() if not tr.empty else None
            pf_wo_top = pf_of(tr[tr.symbol != top]) if top else float("nan")
            qshare, nq = quarters_positive(eq)
            checks = [
                ("Operaciones ≥ 100", m["num_operaciones"] >= 100, str(m["num_operaciones"])),
                ("Profit factor ≥ 1,2", (m.get("profit_factor") or 0) >= 1.2, fmt(m.get("profit_factor"))),
                ("Expectativa ≥ 0,10 R", (m.get("expectativa_r") or -9) >= 0.10, fmt(m.get("expectativa_r"), 3)),
                ("Drawdown ≤ 20 %", m["max_drawdown"] >= -0.20, fmt_pct(m["max_drawdown"])),
                ("Sharpe ≥ 0,8", (m.get("sharpe") or -9) >= 0.8, fmt(m.get("sharpe"))),
                ("Trimestres con ganancia ≥ 60 %", qshare >= 0.6, f"{qshare:.0%} de {nq}"),
                ("Robustez (15 pb, comisiones ×1,5): expectativa > 0",
                 (str_tr.r_multiple.mean() if not str_tr.empty else -9) > 0,
                 fmt(str_tr.r_multiple.mean() if not str_tr.empty else float("nan"), 3)),
                (f"Sin el contrato que más gana ({top}): PF ≥ 1,1", pf_wo_top >= 1.1, fmt(pf_wo_top)),
                ("Rentabilidad neta > 0", m["rentabilidad_total"] > 0, fmt_pct(m["rentabilidad_total"])),
            ]
            passed = all(c[1] for c in checks)
            verdict["fase_c"] = passed
            tr.to_csv(out / "C_operaciones_desarrollo.csv", index=False)
            rep += [f"## Fase C · Validación de «{name}» (solo desarrollo)", "",
                    "| Criterio | Resultado | ¿Pasa? |", "|---|---:|:---:|"]
            rep += [f"| {n} | {v} | {'✔' if ok else '✖'} |" for n, ok, v in checks]
            rep += ["", "Por contrato (desarrollo):", ""] + per_symbol_table(tr) + [""]
            if passed:
                heq, htr, hcap = run_portfolio(market, p, cfg, start=dev_end)
                hm = compute_metrics(heq, htr, hcap)
                hok = hm["rentabilidad_total"] > 0 and (hm.get("profit_factor") or 0) >= 1.1 and hm["max_drawdown"] >= -0.20
                verdict["holdout"] = hok
                rep += ["## Fase D · Holdout (evaluado una sola vez)", "", md_table([summary_row(name, hm)]), "",
                        f"Rentabilidad > 0, PF ≥ 1,1 y drawdown ≤ 20 %: **{'PASA' if hok else 'NO PASA'}**", ""]
                rep += ["Por contrato (holdout):", ""] + per_symbol_table(htr) + [""]
            else:
                rep += ["No supera la fase C: el holdout queda sin tocar.", ""]
    conclusion = ("Supera todas las fases: el siguiente paso es adaptar el bot a varios contratos y probarlo en SIM. "
                  "No se activa el modo real." if verdict["holdout"] else
                  "No supera los criterios fijados de antemano. **No se recomienda operarla en real.**")
    rep += ["## Conclusión", "", conclusion, "", DISCLAIMER, ""]
    text = "\n".join(rep)
    (out / "informe.md").write_text(text, encoding="utf-8")
    (out / "veredicto.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
