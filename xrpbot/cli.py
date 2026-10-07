"""Línea de comandos del bot.

    python -m xrpbot.cli check                      # conectividad, firma, instrumento y comisiones
    python -m xrpbot.cli download [--since 2022-01-01]
    python -m xrpbot.cli backtest [--funding-filter on|off|both]
    python -m xrpbot.cli walkforward [--funding-filter both] [--holdout]
    python -m xrpbot.cli run                        # modo de settings.yaml (demo por defecto)
    python -m xrpbot.cli run --mode sim
    python -m xrpbot.cli run --mode live --live     # pide confirmación escrita
    python -m xrpbot.cli kill [--mode demo|live]    # KILL SWITCH: cancela todo, cierra y detiene
    python -m xrpbot.cli rearm [--force-same-day]
    python -m xrpbot.cli status
    python -m xrpbot.cli export [--out reports/csv]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import URLS, Mode, Settings, load_settings
from .monitoring.logging_setup import setup_logging

log = logging.getLogger("xrpbot.cli")

LIVE_PHRASE = "OPERAR EN REAL {symbol}"


# ---------------------------------------------------------------- utilidades
def _offline_spec_and_fees(s: Settings):
    """Especificación y comisiones para backtest: de la caché de `download` o de la config."""
    from .exchange.fees import Fees, select_fees
    from .exchange.instruments import parse_instrument
    from .models import InstrumentSpec

    data = Path(s.backtest.data_dir)
    spec = fees = None
    inst_file, fee_file = data / "instruments.json", data / "feeschedules.json"
    if inst_file.exists():
        instruments = json.loads(inst_file.read_text())
        spec = parse_instrument(instruments, s.symbol)
        raw = next(i for i in instruments if i["symbol"].upper() == s.symbol.upper())
        if fee_file.exists():
            try:
                fees = select_fees(json.loads(fee_file.read_text()), raw.get("feeScheduleUid"), 0.0)
            except Exception as exc:  # noqa: BLE001
                log.warning("Comisiones en caché no válidas: %s", exc)
    if spec is None:
        o = s.backtest.offline_spec
        log.warning("Usando la especificación OFFLINE de la config (no verificada con /instruments)")
        spec = InstrumentSpec(s.symbol, o.contract_size, o.tick_size, o.size_step, o.min_size,
                              o.maintenance_margin)
    if fees is None:
        fees = Fees(s.backtest.fallback_maker_fee, s.backtest.fallback_taker_fee, "respaldo")
    return spec, fees


def _load_market(s: Settings):
    from .data.store import load_candles, load_funding
    from .data.validation import validate_candles

    candles = load_candles(s.backtest.data_dir, s.symbol, "trade")
    rep = validate_candles(candles)
    print(f"Velas: {candles.index[0]} -> {candles.index[-1]} | {rep.summary()}")
    if not rep.ok:
        raise SystemExit("Datos no válidos: revisa la descarga antes de hacer backtest")
    mark = funding = None
    try:
        mark = load_candles(s.backtest.data_dir, s.symbol, "mark") if s.backtest.stop_trigger_source == "mark" else None
    except FileNotFoundError:
        print("AVISO: sin velas de mark price; el stop se dispara con velas de trades")
    try:
        funding = load_funding(s.backtest.data_dir, s.symbol)
        r = funding["relative_rate"]
        print(f"Funding: {len(funding)} registros, media {r.mean():.6%}/periodo, "
              f"|máx| {r.abs().max():.4%} (si parece absurdo, revisa la escala)")
    except FileNotFoundError:
        print("AVISO: sin funding histórico; el backtest NO incluirá funding")
    return candles, mark, funding


def _bt_config(s: Settings, costs: str = "futures"):
    from .backtest.engine import BacktestConfig

    spec, fees = _offline_spec_and_fees(s)
    base = dict(initial_capital=s.capital.backtest_initial_usd, spec=spec, risk_per_trade=s.risk.risk_per_trade,
                max_leverage=s.risk.max_leverage, max_daily_loss=s.risk.max_daily_loss,
                tp_post_only=s.execution.take_profit_post_only,
                min_liq_distance_multiple=s.risk.min_liq_distance_multiple)
    if costs == "futures":
        print(f"Costes FUTUROS: maker {fees.maker:.4%} taker {fees.taker:.4%} ({fees.source}); "
              f"slippage {s.backtest.slippage_bps} pb; funding histórico si está descargado")
        return BacktestConfig(**base, taker_fee=fees.taker, maker_fee=fees.maker, slippage_bps=s.backtest.slippage_bps)
    prof = s.backtest.cost_profiles.get(costs)
    if prof is None:
        raise SystemExit(f"Perfil de costes desconocido: {costs}. Disponibles: futures, {', '.join(s.backtest.cost_profiles)}")
    print(f"Costes {costs.upper()}: maker {prof['maker_fee']:.2%} taker {prof['taker_fee']:.2%} · apertura "
          f"{prof.get('open_fee', 0):.2%} · rollover {prof.get('rollover_fee_per_4h', 0):.2%}/4 h · slippage "
          f"{prof.get('slippage_bps', s.backtest.slippage_bps)} pb · sin funding")
    return BacktestConfig(**base, taker_fee=prof["taker_fee"], maker_fee=prof["maker_fee"],
                          slippage_bps=prof.get("slippage_bps", s.backtest.slippage_bps),
                          open_fee=prof.get("open_fee", 0.0), rollover_fee_per_4h=prof.get("rollover_fee_per_4h", 0.0))


def _filter_variants(arg: str, base):
    if arg == "both":
        return [("sin_filtro_funding", base.with_(funding_filter=False)),
                ("con_filtro_funding", base.with_(funding_filter=True))]
    return [("con_filtro_funding" if arg == "on" else "sin_filtro_funding", base.with_(funding_filter=arg == "on"))]


# ------------------------------------------------------------------ comandos
async def cmd_check(s: Settings) -> None:
    from .exchange.fees import select_fees
    from .exchange.instruments import describe, parse_instrument
    from .exchange.rest import KrakenFuturesRest

    async with KrakenFuturesRest(s.urls["rest"], s.urls["charts"], s.secrets.api_key, s.secrets.api_secret) as rest:
        instruments = await rest.get_instruments()
        spec = parse_instrument(instruments, s.symbol)
        print("OK instrumento:", describe(spec))
        raw = next(i for i in instruments if i["symbol"].upper() == s.symbol.upper())
        print("   campos crudos:", {k: raw.get(k) for k in ("tickSize", "contractSize", "contractValuePrecision",
                                                            "marginLevels", "maxPositionSize", "feeScheduleUid")})
        fees = select_fees(await rest.get_fee_schedules(), raw.get("feeScheduleUid"), 0.0)
        print(f"OK comisiones (tramo base): maker {fees.maker:.4%} taker {fees.taker:.4%}")
        t = await rest.get_ticker(s.symbol)
        print("OK ticker:", {k: t.get(k) for k in ("bid", "ask", "last", "markPrice", "fundingRate")} if t else None)
        print(f"   desfase de reloj con el servidor: {rest.clock_offset_s:+.3f} s")
        if rest.has_credentials:
            print(f"OK firma privada: equity {await rest.get_equity_usd():.2f} USD")
            print("   posiciones:", await rest.get_open_positions())
            print("   órdenes abiertas:", await rest.get_open_orders())
            try:
                print("   preferencias de apalancamiento:", await rest.get_leverage_preferences())
            except Exception as exc:  # noqa: BLE001
                print("   (no se pudieron leer las preferencias de apalancamiento:", exc, ")")
        else:
            print("Sin claves para este modo: solo se han comprobado los endpoints públicos")


async def cmd_download(s: Settings, since: str | None) -> None:
    from .data.downloader import download_candles, download_funding
    from .exchange.rest import KrakenAPIError, KrakenFuturesRest

    data_dir = Path(s.backtest.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    urls = URLS["live"]  # el histórico siempre se descarga de producción (API pública, sin claves)
    async with KrakenFuturesRest(urls["rest"], urls["charts"]) as rest:
        instruments = await rest.get_instruments()
        (data_dir / "instruments.json").write_text(json.dumps(instruments, indent=1))
        (data_dir / "feeschedules.json").write_text(json.dumps(await rest.get_fee_schedules(), indent=1))
        since_ts = pd.Timestamp(since, tz="UTC") if since else None
        for tick_type in ("trade", "mark"):
            df = await download_candles(rest, data_dir, s.symbol, tick_type, "1h", since_ts)
            print(f"{tick_type}: {len(df)} velas ({df.index.min()} -> {df.index.max()})")
        try:
            f = await download_funding(rest, data_dir, s.symbol)
            print(f"funding: {len(f)} registros ({f.index.min()} -> {f.index.max()})")
        except KrakenAPIError as exc:
            print(f"AVISO: no se pudo descargar el funding histórico ({exc}). "
                  "Las velas sí se guardaron; el backtest se hará sin funding.")


def cmd_backtest(s: Settings, funding_filter: str, start: str | None, end: str | None, out: str,
                 costs: str = "futures") -> None:
    from .backtest.engine import run_backtest
    from .backtest.metrics import compute_metrics, format_metrics
    from .backtest.regimes import classify_regimes, metrics_by_regime
    from .strategy.donchian_atr import StrategyParams

    candles, mark, funding = _load_market(s)
    cfg = _bt_config(s, costs)
    if costs != "futures":
        out = str(Path(out) / costs)   # resultados aparte: no pisan los de futuros
        funding = None          # el margen de spot no paga funding (paga rollover, ya incluido)
        if funding_filter != "off":
            print("Perfil sin funding: el filtro de funding no aplica; se ejecuta solo sin filtro")
            funding_filter = "off"
    base = StrategyParams.from_cfg(s.strategy)
    regimes = classify_regimes(candles)
    st = pd.Timestamp(start, tz="UTC") if start else None
    en = pd.Timestamp(end, tz="UTC") if end else None
    Path(out).mkdir(parents=True, exist_ok=True)
    for name, params in _filter_variants(funding_filter, base):
        r = run_backtest(candles, params, cfg, funding, mark, start=st, end=en)
        m = compute_metrics(r.equity, r.trades, cfg.initial_capital)
        print(f"\n=== Backtest {name} | {params} ===\n{format_metrics(m)}")
        print(f"  operaciones descartadas: {r.skipped}")
        reg = metrics_by_regime(r.trades, regimes)
        if not reg.empty:
            print("\n  Por régimen de mercado:\n" + reg.round(3).to_string())
        r.trades.to_csv(Path(out) / f"trades_{name}.csv", index=False)
        r.equity.to_csv(Path(out) / f"equity_{name}.csv")
    print(f"\nCSV guardados en {out}/")


def cmd_walkforward(s: Settings, funding_filter: str, holdout: bool, out: str, costs: str = "futures") -> None:
    from .backtest.metrics import format_metrics
    from .backtest.walkforward import evaluate_holdout, walk_forward
    from .strategy.donchian_atr import StrategyParams

    candles, mark, funding = _load_market(s)
    cfg = _bt_config(s, costs)
    if costs != "futures":
        out = str(Path(out) / costs)   # resultados aparte: no pisan los de futuros
        funding = None          # el margen de spot no paga funding (paga rollover, ya incluido)
        if funding_filter != "off":
            print("Perfil sin funding: el filtro de funding no aplica; se ejecuta solo sin filtro")
            funding_filter = "off"
    base = StrategyParams.from_cfg(s.strategy)
    Path(out).mkdir(parents=True, exist_ok=True)
    for name, params in _filter_variants(funding_filter, base):
        wf = walk_forward(candles, params, cfg, s.walkforward, funding, mark)
        print(f"\n=== Walk-forward {name} (holdout desde {wf.holdout_start.date()}, fuera de la optimización) ===")
        if wf.windows.empty:
            print("  No hay datos suficientes para ninguna ventana (amplía el histórico o reduce train_months)")
            continue
        print(wf.windows.round({c: 3 for c in wf.windows.select_dtypes("number").columns}).to_string(index=False))
        print(f"\n  Resultado fuera de muestra concatenado:\n{format_metrics(wf.oos_metrics)}")
        wf.windows.to_csv(Path(out) / f"wf_windows_{name}.csv", index=False)
        wf.oos_trades.to_csv(Path(out) / f"wf_oos_trades_{name}.csv", index=False)
        wf.oos_equity.to_csv(Path(out) / f"wf_oos_equity_{name}.csv")
        if holdout and wf.last_params:
            print(f"\n  HOLDOUT con los parámetros de la última ventana {wf.last_params}")
            print("  (evalúalo UNA sola vez; si después cambias la estrategia, ya no es fuera de muestra)")
            _, hm = evaluate_holdout(candles, wf.last_params, cfg, wf.holdout_start, funding, mark)
            print(format_metrics(hm))


async def cmd_kill(s: Settings) -> None:
    """Kill switch local: cancela órdenes, cierra la posición a mercado y deja el bot detenido."""
    from .exchange.rest import KrakenFuturesRest
    from .models import OrderRequest
    from .risk.limits import DailyLossGuard
    from .state.db import Database

    db = Database(s.storage.db_path)
    DailyLossGuard(db, s.risk.max_daily_loss).halt("KILL SWITCH manual")
    db.log_event("critical", "kill", "Kill switch activado desde la CLI")
    print("Parada registrada: el bot no abrirá nada hasta 'rearm'.")
    if s.mode not in (Mode.DEMO, Mode.LIVE):
        print("Modo sin exchange real: el bot en marcha cerrará la posición simulada al ver la parada.")
        return
    async with KrakenFuturesRest(s.urls["rest"], s.urls["charts"], s.secrets.api_key, s.secrets.api_secret) as rest:
        await rest.cancel_all_orders(s.symbol)
        print("Órdenes canceladas.")
        for p in await rest.get_open_positions():
            if p.symbol != s.symbol.upper():
                continue
            req = OrderRequest(symbol=p.symbol, side=p.side.exit_order_side, order_type="mkt", size=p.size,
                               cli_ord_id=f"kill-{int(datetime.now().timestamp())}", reduce_only=True)
            ack = await rest.send_order(req)
            print(f"Cierre a mercado de {p.side.value} {p.size}: {ack.status}")
        left = [p for p in await rest.get_open_positions() if p.symbol == s.symbol.upper()]
        print("Posición restante:", left or "ninguna")
    from .monitoring.telegram import TelegramNotifier
    if s.alerts.telegram_enabled:
        await TelegramNotifier(s.secrets.telegram_token, s.secrets.telegram_chat_id).send_now(
            "critical", f"KILL SWITCH activado en {s.mode.value} ({s.symbol})")


def cmd_rearm(s: Settings, force_same_day: bool) -> None:
    from .risk.limits import DailyLossGuard
    from .state.db import K_FROZEN, Database

    db = Database(s.storage.db_path)
    guard = DailyLossGuard(db, s.risk.max_daily_loss)
    today = datetime.now(timezone.utc).date().isoformat()
    if guard.halted and guard.halt_day == today and not force_same_day and "KILL" not in guard.halt_reason:
        raise SystemExit("La parada diaria es de HOY (UTC). Espera al día siguiente o usa --force-same-day "
                         "(asumes que el límite diario se reinicia con la equity actual).")
    print(f"Parada previa: {guard.halt_reason or 'ninguna'} | congelación: {db.get(K_FROZEN) or 'ninguna'}")
    guard.rearm()
    db.set(K_FROZEN, None)
    db.log_event("warning", "rearm", "Rearme manual desde la CLI")
    print("Bot rearmado. Revisa 'status' antes de arrancarlo.")


def cmd_status(s: Settings) -> None:
    from .risk.limits import DailyLossGuard
    from .state.db import K_FROZEN, Database

    db = Database(s.storage.db_path)
    g = DailyLossGuard(db, s.risk.max_daily_loss)
    print(f"Parada: {'SÍ - ' + g.halt_reason if g.halted else 'no'}")
    print(f"Congelado: {db.get(K_FROZEN) or 'no'}")
    print(f"Día UTC de referencia: {g.halt_day} | equity inicio del día: {db.get(g.K_START)}")
    st = db.active_trade()
    print("Operación activa:", json.dumps(st.to_dict(), indent=1, default=str) if st else "ninguna")
    for row in db.conn.execute("SELECT datetime(ts,'unixepoch'), level, kind, message FROM events "
                               "ORDER BY id DESC LIMIT 10"):
        print("  ", *row)


def confirm_live(s: Settings, flag: bool) -> None:
    if not flag:
        raise SystemExit("El modo live exige el flag --live")
    phrase = LIVE_PHRASE.format(symbol=s.symbol)
    print("=" * 70)
    print(f" MODO REAL: dinero real en {s.symbol}. Riesgo {s.risk.risk_per_trade:.1%}/operación, "
          f"pérdida diaria máx {s.risk.max_daily_loss:.1%}, apalancamiento máx {s.risk.max_leverage}x")
    print("=" * 70)
    # Confirmación explícita por entorno/.env: necesaria para arranques desatendidos
    # (reinicio automático, Docker, systemd), donde nadie puede escribir la frase.
    if os.getenv("XRPBOT_LIVE_CONFIRM", "").strip() == phrase:
        print("Confirmación de live recibida por XRPBOT_LIVE_CONFIRM")
        return
    if not sys.stdin.isatty():
        raise SystemExit(f"Sin terminal: define XRPBOT_LIVE_CONFIRM='{phrase}' para confirmar el modo real")
    if input(f"Escribe exactamente '{phrase}' para continuar: ").strip() != phrase:
        raise SystemExit("Confirmación incorrecta: abortado")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="xrpbot")
    ap.add_argument("--config", default="config/settings.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "kill", "status", "export"):
        p = sub.add_parser(name)
        p.add_argument("--mode", choices=[m.value for m in Mode])
        if name == "export":
            p.add_argument("--out", default="reports/csv")
    p = sub.add_parser("download")
    p.add_argument("--since", default="2022-01-01")
    for name in ("backtest", "walkforward"):
        p = sub.add_parser(name)
        p.add_argument("--funding-filter", choices=["on", "off", "both"], default="both")
        p.add_argument("--out", default="reports")
        p.add_argument("--costs", default="futures", help="perfil de costes: futures (por defecto) o margin")
        if name == "backtest":
            p.add_argument("--start")
            p.add_argument("--end")
        else:
            p.add_argument("--holdout", action="store_true", help="evalúa el tramo final (solo una vez)")
    p = sub.add_parser("run")
    p.add_argument("--mode", choices=["demo", "sim", "live"])
    p.add_argument("--live", action="store_true", help="obligatorio para el modo real")
    p = sub.add_parser("rearm")
    p.add_argument("--mode", choices=[m.value for m in Mode])
    p.add_argument("--force-same-day", action="store_true")
    args = ap.parse_args(argv)

    mode = getattr(args, "mode", None)
    if args.cmd in ("backtest", "walkforward"):
        mode = "backtest"
    s = load_settings(args.config, mode_override=mode)
    setup_logging(s.storage.log_dir, name=f"xrpbot-{s.mode.value}")
    if args.cmd in ("kill", "rearm", "status", "export"):
        print(f"[modo {s.mode.value} | base de datos {s.storage.db_path}]")

    if args.cmd == "check":
        asyncio.run(cmd_check(s))
    elif args.cmd == "download":
        asyncio.run(cmd_download(s, args.since))
    elif args.cmd == "backtest":
        cmd_backtest(s, args.funding_filter, args.start, args.end, args.out, args.costs)
    elif args.cmd == "walkforward":
        cmd_walkforward(s, args.funding_filter, args.holdout, args.out, args.costs)
    elif args.cmd == "kill":
        asyncio.run(cmd_kill(s))
    elif args.cmd == "rearm":
        cmd_rearm(s, args.force_same_day)
    elif args.cmd == "status":
        cmd_status(s)
    elif args.cmd == "export":
        from .state.db import Database
        for path in Database(s.storage.db_path).export_csv(args.out):
            print("Exportado", path)
    elif args.cmd == "run":
        if s.mode == Mode.BACKTEST:
            raise SystemExit("Para backtest usa el comando 'backtest'")
        if s.mode == Mode.LIVE:
            confirm_live(s, args.live)
        elif args.live:
            raise SystemExit("--live solo tiene sentido con --mode live")
        from .live.runner import LiveBot
        bot = LiveBot(s)
        try:
            asyncio.run(bot.run())
        except KeyboardInterrupt:
            print("\nDetenido por el usuario. Las órdenes de stop y TP siguen en el exchange.")


if __name__ == "__main__":
    main()
