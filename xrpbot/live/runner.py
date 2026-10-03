"""Bucle principal del bot en demo, sim y live.

Estructura (una sola tarea principal + WebSocket en segundo plano):

  arranque: especificación del instrumento, comisiones y margen leídos de la API ->
            reconciliación -> si la parada diaria o la congelación están activas,
            solo vigila (no abre nada)
  bucle cada poll_interval_s (o antes si el WS avisa de una ejecución):
      - órdenes, posiciones y ejecuciones por REST (fuente de verdad) -> OCO
      - equity -> control de pérdida diaria (si salta: cierra todo y se detiene)
      - cada reconcile_interval_s: reconciliación completa
  al cierre de cada vela de 1 h (+ candle_close_delay_s):
      - con posición: funding estimado de la hora + trailing del stop
      - sin posición: señal -> filtros (circuit breaker, liquidación, tamaño) -> entrada
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timezone

import pandas as pd

from ..config import Mode, Settings
from ..data.downloader import candles_to_frame, funding_to_frame
from ..data.validation import candles_are_stale, drop_incomplete_last_candle, validate_candles
from ..exchange.fees import Fees, FeeParseError, select_fees
from ..exchange.instruments import describe, parse_instrument
from ..exchange.rest import KrakenAPIError, KrakenFuturesRest
from ..exchange.ws import KrakenFuturesWS
from ..execution.broker import KrakenBroker, PaperBroker
from ..execution.oco import TradeState, make_trade_id
from ..execution.order_manager import OrderManager
from ..models import MarketSnapshot, OrderRequest, Side
from ..monitoring.telegram import TelegramNotifier
from ..risk.circuit_breaker import book_depth_within, entry_block_reasons
from ..risk.limits import DailyLossGuard, estimate_liquidation_price, liquidation_is_safe
from ..risk.sizing import position_size
from ..state.db import K_FROZEN, Database
from ..state.reconcile import reconcile
from ..strategy.donchian_atr import (StrategyParams, compute_indicators, funding_rate_at, signal_at,
                                     take_profit_price, trailing_stop)

log = logging.getLogger(__name__)
HOUR = 3600


class LiveBot:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.symbol = settings.symbol.upper()
        self.params = StrategyParams.from_cfg(settings.strategy)
        self.db = Database(settings.storage.db_path)
        self.guard = DailyLossGuard(self.db, settings.risk.max_daily_loss)
        self.notify = TelegramNotifier(settings.secrets.telegram_token, settings.secrets.telegram_chat_id,
                                       prefix=f"[{settings.mode.value}] ") \
            if settings.alerts.telegram_enabled else TelegramNotifier(None, None)
        self.wake = asyncio.Event()
        self.margin_mode = settings.risk.margin_mode
        self.funding: pd.DataFrame | None = None
        self._stop = asyncio.Event()
        self._force_reconcile = True

    # ------------------------------------------------------------ arranque
    async def setup(self) -> None:
        s = self.s
        keys = (s.secrets.api_key, s.secrets.api_secret) if s.mode in (Mode.DEMO, Mode.LIVE) else (None, None)
        if s.mode in (Mode.DEMO, Mode.LIVE) and not all(keys):
            raise SystemExit(f"Faltan las claves de {s.mode.value} en .env")
        self.rest = KrakenFuturesRest(s.urls["rest"], s.urls["charts"], *keys)
        await self.rest.open()

        instruments = await self.rest.get_instruments()
        self.spec = parse_instrument(instruments, self.symbol)
        raw = next(i for i in instruments if str(i.get("symbol", "")).upper() == self.symbol)
        log.info("Instrumento verificado: %s", describe(self.spec))
        self.fees = await self._load_fees(raw.get("feeScheduleUid"))
        log.info("Comisiones: maker=%.4f%% taker=%.4f%% (%s)", self.fees.maker * 100,
                 self.fees.taker * 100, self.fees.source)

        if s.mode == Mode.SIM:
            self.broker = PaperBroker(self.spec, self.fees, s.capital.backtest_initial_usd,
                                      s.backtest.slippage_bps)
        else:
            self.broker = KrakenBroker(self.rest)
            await self._configure_margin()
        self.om = OrderManager(self.broker, self.db, self.spec, self.fees, s.execution, self.notify)

        self.ws = KrakenFuturesWS(s.urls["ws"], self.symbol, *keys,
                                  on_private_event=self._on_ws_private, on_reconnect=self._on_ws_reconnect)
        self.notify.start()
        msg = f"Bot arrancado en modo {s.mode.value.upper()} sobre {self.symbol}"
        self.db.log_event("info", "start", msg)
        self.notify("info", msg)

    async def _load_fees(self, schedule_uid: str | None) -> Fees:
        try:
            schedules = await self.rest.get_fee_schedules()
            volume = 0.0
            if self.rest.has_credentials and schedule_uid:
                volume = (await self.rest.get_fee_volumes()).get(schedule_uid, 0.0)
            return select_fees(schedules, schedule_uid, volume)
        except (KrakenAPIError, FeeParseError, KeyError, ValueError) as exc:
            if self.s.mode == Mode.LIVE:
                raise SystemExit(f"No se pudieron leer las comisiones ({exc}); en live no se usan valores por defecto")
            log.warning("Comisiones no disponibles (%s): uso las de respaldo de la configuración", exc)
            b = self.s.backtest
            return Fees(b.fallback_maker_fee, b.fallback_taker_fee, source="respaldo")

    async def _configure_margin(self) -> None:
        if self.s.risk.margin_mode != "isolated":
            self.margin_mode = "cross"
            return
        try:
            await self.rest.set_leverage_preference(self.symbol, self.s.risk.max_leverage)
            prefs = await self.rest.get_leverage_preferences()
            ok = any(str(p.get("symbol", "")).upper() == self.symbol for p in prefs)
            self.margin_mode = "isolated" if ok else "cross"
        except KrakenAPIError as exc:
            log.warning("No se pudo fijar margen aislado (%s)", exc)
            self.margin_mode = "cross"
        if self.margin_mode == "cross":
            msg = ("Margen AISLADO no disponible: se opera en CRUZADO. Deja en la cuenta de "
                   "futuros solo el capital del bot.")
            log.warning(msg)
            self.notify("warning", msg)
        else:
            log.info("Margen aislado con apalancamiento máximo %sx en %s", self.s.risk.max_leverage, self.symbol)

    # ------------------------------------------------------------ eventos
    async def _on_ws_private(self) -> None:
        self.wake.set()

    async def _on_ws_reconnect(self) -> None:
        log.info("WebSocket reconectado: reconciliación inmediata")
        self._force_reconcile = True
        self.wake.set()

    def stop(self) -> None:
        self._stop.set()
        self.wake.set()

    # ------------------------------------------------------------ bucle
    async def run(self) -> None:
        await self.setup()
        ws_task = asyncio.create_task(self.ws.run_forever())
        self._force_reconcile = True
        last_reconcile = 0.0
        next_bar = self._next_bar_time()
        try:
            while not self._stop.is_set():
                try:
                    await self._feed_paper()
                    if self._force_reconcile or time.time() - last_reconcile > self.s.execution.reconcile_interval_s:
                        await self.reconcile_now()
                        self._force_reconcile, last_reconcile = False, time.time()
                    await self.poll()
                    if time.time() >= next_bar:
                        await self.on_bar_close()
                        next_bar = self._next_bar_time()
                except (KrakenAPIError, OSError, asyncio.TimeoutError) as exc:
                    log.error("Error en el bucle principal (se reintenta): %r", exc)
                    self.db.log_event("error", "loop", repr(exc))
                except Exception as exc:  # noqa: BLE001
                    log.exception("Error inesperado: el bot se CONGELA por seguridad")
                    self.freeze(f"error inesperado: {exc!r}")
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=self.s.execution.poll_interval_s)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.ws.stop()
            ws_task.cancel()
            await self.notify.stop()
            await self.rest.close()
            self.db.close()

    def _next_bar_time(self) -> float:
        now = time.time()
        return (math.floor(now / HOUR) + 1) * HOUR + self.s.execution.candle_close_delay_s

    async def _feed_paper(self) -> None:
        if isinstance(self.broker, PaperBroker):
            snap = self.ws.snapshot
            if snap is None:
                t = await self.rest.get_ticker(self.symbol)
                if t:
                    snap = MarketSnapshot(self.symbol, float(t["bid"]), float(t["ask"]),
                                          float(t.get("last") or 0), float(t.get("markPrice") or 0),
                                          time.time())
            if snap is not None:
                self.broker.on_market(snap)

    # ------------------------------------------------------------ estado
    @property
    def frozen_reason(self) -> str | None:
        return self.db.get(K_FROZEN)

    def freeze(self, reason: str) -> None:
        if self.db.get(K_FROZEN) is None:
            self.db.set(K_FROZEN, reason)
            msg = f"Bot CONGELADO: {reason}. Revisa y ejecuta 'rearm' para reanudar."
            log.critical(msg)
            self.db.log_event("critical", "freeze", msg)
            self.notify("critical", msg)

    async def reconcile_now(self) -> None:
        # primero las ejecuciones: así la reconciliación ve el estado más reciente
        await self.om.process_fills(self.db.active_trade(), await self.broker.recent_fills())
        st = self.db.active_trade()
        positions = await self.broker.positions()
        orders = await self.broker.open_orders()
        entry_live = bool(st and any(o.cli_ord_id == st.entry_cli for o in orders))
        plan = reconcile(symbol=self.symbol, local=st, positions=positions, open_orders=orders,
                         entry_order_live=entry_live,
                         protect_unknown=self.s.risk.protect_unknown_positions)
        for n in plan.notes:
            log.warning("Reconciliación: %s", n)
            self.db.log_event("warning", "reconcile", n)
        if plan.freeze:
            self.freeze("; ".join(plan.notes) or "discrepancia en la reconciliación")
        if plan.actions:
            await self.om.apply(st, plan.actions)

    async def poll(self) -> None:
        st = self.db.active_trade()
        fills = await self.broker.recent_fills()
        await self.om.process_fills(st, fills)
        equity = await self.broker.equity()
        status = self.guard.check(equity, datetime.now(timezone.utc))
        if status.just_tripped:
            msg = f"PARADA DIARIA: {status.reason}. Cerrando todo. Rearme manual necesario."
            log.critical(msg)
            self.db.log_event("critical", "daily_loss", msg)
            self.notify("critical", msg)
            await self.om.flatten(self.db.active_trade(), reason="daily_loss_halt")
        elif status.halted and await self.broker.positions():
            # parada activa (p. ej. kill switch desde la CLI) y aún hay posición: cerrar
            log.warning("Parada activa (%s) con posición abierta: cerrando", status.reason)
            await self.om.flatten(self.db.active_trade(), reason=f"parada: {status.reason}")

    # ------------------------------------------------------------ velas
    async def _fetch_closed_candles(self) -> pd.DataFrame:
        now = pd.Timestamp.now(tz="UTC")
        bars = max(self.params.warmup_bars, 24 * 31) + 5
        payload = await self.rest.get_candles("trade", self.symbol, "1h",
                                              int((now - pd.Timedelta(hours=bars)).timestamp()),
                                              int(now.timestamp()))
        df = candles_to_frame(payload.get("candles", []))
        return drop_incomplete_last_candle(df, now)

    async def _refresh_funding(self) -> None:
        try:
            self.funding = funding_to_frame(await self.rest.get_historical_funding(self.symbol))
        except KrakenAPIError as exc:
            log.warning("No se pudo leer el funding histórico: %s", exc)

    async def on_bar_close(self) -> None:
        await self.reconcile_now()   # nunca decidir sobre un estado desactualizado
        df = await self._fetch_closed_candles()
        now = pd.Timestamp.now(tz="UTC")
        rep = validate_candles(df)
        stale = candles_are_stale(df, now, self.s.circuit_breaker.max_candle_delay_bars)
        if not rep.ok:
            log.warning("Datos de velas con problemas: %s", rep.summary())
        if df.empty:
            log.error("Sin velas: no se evalúa nada en esta hora")
            return
        await self._refresh_funding()
        ind = compute_indicators(df, self.params)
        st = self.db.active_trade()

        if st is not None and st.status == "open":
            await self._manage_open_trade(st, ind, df)
            return
        if st is not None:
            return  # entrada pendiente: la reconciliación la resuelve

        if self.guard.halted or self.frozen_reason:
            log.info("Sin nuevas entradas: %s", self.guard.halt_reason or self.frozen_reason)
            return
        sig = signal_at(ind, len(ind) - 1, self.params, self.funding)
        if sig is None:
            return
        await self._try_enter(sig, ind, stale, rep.ok)

    async def _manage_open_trade(self, st: TradeState, ind: pd.DataFrame, df: pd.DataFrame) -> None:
        rate = funding_rate_at(self.funding, ind.index[-1])
        if rate is not None:
            snap = self.ws.snapshot or await self.broker.ticker(self.symbol)
            mark = (snap.mark or snap.last) if snap else float(df["close"].iloc[-1])
            st.funding += -st.side.sign * st.open_size * self.spec.contract_size * mark * rate
            self.db.save_trade(st)
        last = ind.iloc[-1]
        new_stop = trailing_stop(st.side, st.stop_price, last["exit_low"], last["exit_high"])
        if new_stop != st.stop_price:
            await self.om.move_stop(st, new_stop)

    async def _try_enter(self, sig, ind: pd.DataFrame, stale: bool, data_ok: bool) -> None:
        s = self.s
        snap = self.ws.snapshot or await self.broker.ticker(self.symbol)
        if snap is None:
            self.db.log_signal(sig, "bloqueada: sin ticker")
            return
        if not isinstance(self.broker, PaperBroker):
            try:
                snap.bid_depth, snap.ask_depth = book_depth_within(await self.rest.get_orderbook(self.symbol), snap.mid)
            except KrakenAPIError as exc:
                log.warning("Sin libro de órdenes: %s", exc)
        side = sig.side
        entry_ref = snap.ask if side is Side.LONG else snap.bid
        if (side is Side.LONG and entry_ref <= sig.stop_price) or (side is Side.SHORT and entry_ref >= sig.stop_price):
            self.db.log_signal(sig, "descartada: el precio ya está más allá del stop")
            return

        equity = await self.broker.equity()
        capital = min(equity, s.capital.max_capital_usd) if s.capital.max_capital_usd else equity
        sz = position_size(capital_usd=capital, entry_price=entry_ref, stop_price=sig.stop_price,
                           spec=self.spec, risk_per_trade=s.risk.risk_per_trade,
                           max_leverage=s.risk.max_leverage, taker_fee=self.fees.taker,
                           slippage_bps=s.backtest.slippage_bps)
        atr_hist = ind["atr"].dropna().iloc[-24 * 30:]
        atr_ratio = float(ind["atr"].iloc[-1] / atr_hist.median()) if len(atr_hist) else None
        reasons = entry_block_reasons(snapshot=snap, now_s=time.time(), cfg=s.circuit_breaker,
                                      atr_ratio=atr_ratio, candles_stale=stale or not data_ok,
                                      intended_size=sz.size if sz.ok else None)
        if not sz.ok:
            reasons.append(f"tamaño: {sz.reason}")
        else:
            liq = estimate_liquidation_price(
                side=side, entry=entry_ref, size=sz.size, contract_size=self.spec.contract_size,
                maintenance_margin=self.spec.maintenance_margin, margin_mode=self.margin_mode,
                collateral_usd=equity, isolated_leverage=s.risk.max_leverage)
            safe, why = liquidation_is_safe(side=side, entry=entry_ref, stop=sig.stop_price, liq_price=liq,
                                            min_multiple=s.risk.min_liq_distance_multiple)
            if not safe:
                reasons.append(why)
        if reasons:
            decision = "bloqueada: " + "; ".join(reasons)
            log.info("Señal %s %s", sig.side.value, decision)
            self.db.log_signal(sig, decision)
            self.notify("info", f"Señal {sig.side.value} {decision}")
            return

        trade_id = make_trade_id(self.symbol, sig.time.isoformat(), side)
        if self.db.load_trade(trade_id) is not None:
            log.info("Señal %s ya procesada (reinicio): no se repite", trade_id)
            return
        tp = take_profit_price(side, entry_ref, sig.stop_price, self.params.take_profit_r)
        st = TradeState(trade_id=trade_id, side=side, status="entry_pending", target_size=sz.size,
                        stop_price=self.spec.round_price(sig.stop_price), tp_price=tp,
                        contract_size=self.spec.contract_size, risk_usd=sz.risk_usd,
                        tp_r=self.params.take_profit_r, entry_cli=f"{trade_id}-entry-0")
        self.db.set_active_trade(st)
        self.db.log_signal(sig, f"tomada: {trade_id} size={sz.size} riesgo={sz.risk_usd:.2f} "
                                f"apalancamiento={sz.effective_leverage:.2f}x")
        cap = s.execution.entry_max_slippage_bps / 1e4
        limit = entry_ref * (1 + side.sign * cap)
        limit = self.spec.round_price(limit)
        req = OrderRequest(symbol=self.symbol, side=side.entry_order_side, order_type="ioc", size=sz.size,
                           cli_ord_id=st.entry_cli, limit_price=limit, role="entry")
        ack = await self.om.submit(req, trade_id)
        self.notify("info", f"ENTRADA {side.value} {sz.size} {self.symbol} ~{entry_ref} stop {st.stop_price} "
                            f"riesgo {sz.risk_usd:.2f} USD -> {ack.status}")
        if not ack.accepted and not ack.status.startswith("ambiguous"):
            st.status, st.exit_reason = "cancelled", f"entrada rechazada: {ack.status}"
            self.db.save_trade(st)
            self.db.set_active_trade(None)
            return
        # protección inmediata: se procesan ya las ejecuciones de la entrada
        await self.om.process_fills(st, await self.broker.recent_fills())
        self._force_reconcile = True
