"""Gestor de órdenes: envío idempotente y ejecución de los planes de OCO y reconciliación.

Idempotencia
- Toda orden lleva un cliOrdId determinista (ver oco.make_trade_id) y queda
  registrada en SQLite ANTES de enviarse.
- Si el envío es ambiguo (timeout / error de red tras enviar), se busca la
  orden por cliOrdId en órdenes abiertas y ejecuciones recientes. Si no aparece,
  se reenvía UNA vez con el MISMO cliOrdId: si la primera llegó tarde, Kraken
  rechaza el duplicado (clientOrderIdAlreadyExist) y no se duplica la orden.

Seguridad
- Si no se puede colocar el stop loss de una posición abierta, se cierra la
  posición a mercado de inmediato. Nunca se deja una posición sin stop.
- El stop se mueve (trailing) editando la orden; si la edición falla, primero se
  coloca el stop nuevo y después se cancela el viejo (nunca hay un hueco sin stop).
"""
from __future__ import annotations

import logging
import time

from ..config import ExecutionCfg
from ..exchange.fees import Fees
from ..exchange.rest import AmbiguousOrderResult, KrakenAPIError
from ..models import Fill, InstrumentSpec, OrderAck, OrderRequest, Side
from ..state.db import Database
from ..state.reconcile import (AdoptExchangeSize, MarkCancelled, MarkClosed, MarkEntryFilled,
                               PlaceEmergencyStop)
from .broker import Broker
from .oco import Alert, CancelOrder, PlaceProtection, ResizeOrder, TradeState, on_fill

log = logging.getLogger(__name__)

DUPLICATE = "clientOrderIdAlreadyExist"


class OrderManager:
    def __init__(self, broker: Broker, db: Database, spec: InstrumentSpec, fees: Fees,
                 exec_cfg: ExecutionCfg, notify=None) -> None:
        self.broker, self.db, self.spec, self.fees, self.cfg = broker, db, spec, fees, exec_cfg
        self.notify = notify or (lambda level, msg: None)

    # ------------------------------------------------------------ envío
    async def submit(self, req: OrderRequest, trade_id: str | None) -> OrderAck:
        self.db.log_order(req, trade_id, "sending")
        try:
            ack = await self.broker.send(req)
        except AmbiguousOrderResult as exc:
            log.warning("Envío ambiguo de %s: %s. Verificando por cliOrdId", req.cli_ord_id, exc)
            ack = await self._verify_or_resend(req)
        except KrakenAPIError as exc:
            ack = OrderAck(None, req.cli_ord_id, f"error:{exc.error}")
        if ack.status == DUPLICATE:
            ack = OrderAck(ack.order_id, req.cli_ord_id, "placed", raw={"note": "ya existía"})
        self.db.log_order(req, trade_id, ack.status, ack.order_id)
        log.info("Orden %s %s %s %s size=%s stop=%s limit=%s -> %s", req.role, req.cli_ord_id,
                 req.order_type, req.side, req.size, req.stop_price, req.limit_price, ack.status)
        return ack

    async def _verify_or_resend(self, req: OrderRequest) -> OrderAck:
        try:
            for o in await self.broker.open_orders():
                if o.cli_ord_id == req.cli_ord_id:
                    return OrderAck(o.order_id, req.cli_ord_id, "placed")
            for f in await self.broker.recent_fills():
                if f.cli_ord_id == req.cli_ord_id:
                    return OrderAck(f.order_id, req.cli_ord_id, "filled", f.size, f.price)
            return await self.broker.send(req)   # mismo cliOrdId: el exchange evita duplicados
        except (AmbiguousOrderResult, KrakenAPIError) as exc:
            return OrderAck(None, req.cli_ord_id, f"ambiguous:{exc}")

    # ------------------------------------------------------- constructores
    def _protect_req(self, st: TradeState, role: str, size: float, price: float) -> OrderRequest:
        exit_side = st.side.exit_order_side
        if role == "sl":
            return OrderRequest(symbol=self.spec.symbol, side=exit_side, order_type="stp",
                                size=self.spec.round_size_down(size), cli_ord_id=st.next_cli("sl"),
                                stop_price=self.spec.round_price(price), reduce_only=True,
                                trigger_signal=self.cfg.stop_trigger_signal, role="sl")
        otype = "post" if self.cfg.take_profit_post_only else "lmt"
        return OrderRequest(symbol=self.spec.symbol, side=exit_side, order_type=otype,
                            size=self.spec.round_size_down(size), cli_ord_id=st.next_cli("tp"),
                            limit_price=self.spec.round_price(price), reduce_only=True, role="tp")

    def flatten_req(self, side: Side, size: float, cli_ord_id: str) -> OrderRequest:
        return OrderRequest(symbol=self.spec.symbol, side=side.exit_order_side, order_type="mkt",
                            size=self.spec.round_size_down(size), cli_ord_id=cli_ord_id,
                            reduce_only=True, role="flatten")

    # ------------------------------------------------------- planes
    async def apply(self, st: TradeState | None, actions: list) -> None:
        """Ejecuta acciones de OCO / reconciliación y persiste el estado."""
        for a in actions:
            if isinstance(a, Alert):
                log.log(logging.getLevelName(a.level.upper()), a.message)
                self.db.log_event(a.level, "alert", a.message)
                if a.level != "info":
                    self.notify(a.level, a.message)
            elif isinstance(a, CancelOrder):
                status = await self.broker.cancel(cli_ord_id=a.cli_ord_id if not a.order_id else None,
                                                  order_id=a.order_id)
                if a.cli_ord_id:
                    self.db.update_order_status(a.cli_ord_id, "cancelled", a.why)
                if st is not None:
                    if a.cli_ord_id == st.sl_cli and st.status != "closed":
                        st.sl_cli = None
                    if a.cli_ord_id == st.tp_cli:
                        st.tp_cli = None
                log.info("Cancelada %s (%s): %s", a.cli_ord_id or a.order_id, a.why, status)
            elif isinstance(a, PlaceProtection):
                await self._place_protection(st, a)
            elif isinstance(a, ResizeOrder):
                try:
                    await self.broker.edit(cli_ord_id=a.cli_ord_id, size=self.spec.round_size_down(a.size))
                except Exception as exc:  # noqa: BLE001
                    log.warning("No se pudo redimensionar %s (%r): se recoloca", a.cli_ord_id, exc)
                    price = st.stop_price if a.role == "sl" else st.tp_price
                    await self._place_protection(st, PlaceProtection(a.role, a.size, price), replace=a.cli_ord_id)
            elif isinstance(a, PlaceEmergencyStop):
                await self._emergency_stop(a)
            elif isinstance(a, AdoptExchangeSize):
                st.filled_size = st.exited_size + a.size
                if st.entry_avg == 0:
                    st.entry_avg = a.entry_price
            elif isinstance(a, MarkEntryFilled):
                st.filled_size, st.entry_avg, st.status = a.size, a.entry_price, "open"
                await self.apply(st, [PlaceProtection("sl", a.size, st.stop_price),
                                      PlaceProtection("tp", a.size, st.tp_price)])
            elif isinstance(a, MarkClosed):
                st.status, st.exit_reason = "closed", a.reason
            elif isinstance(a, MarkCancelled):
                st.status, st.exit_reason = "cancelled", a.reason
            if st is not None:
                self.db.save_trade(st)
        if st is not None and st.status in ("closed", "cancelled"):
            self.db.set_active_trade(None)

    async def _place_protection(self, st: TradeState, a: PlaceProtection, replace: str | None = None) -> None:
        req = self._protect_req(st, a.role, a.size, a.price)
        ack = await self.submit(req, st.trade_id)
        if ack.accepted:
            if a.role == "sl":
                st.sl_cli = req.cli_ord_id
            else:
                st.tp_cli = req.cli_ord_id
            if replace:
                await self.broker.cancel(cli_ord_id=replace)
                self.db.update_order_status(replace, "cancelled", "reemplazada")
            return
        if a.role == "sl":
            msg = f"NO se pudo colocar el stop de {st.trade_id} ({ack.status}). Cerrando posición a mercado."
            log.critical(msg)
            self.db.log_event("critical", "sl_failed", msg)
            self.notify("critical", msg)
            await self.flatten(st, reason="sl_rechazado")
        elif ack.status == "postWouldExecute":
            # el precio ya superó el objetivo: se toma beneficio a mercado
            await self.flatten(st, reason="take_profit_inmediato")
        else:
            msg = f"No se pudo colocar el TP de {st.trade_id} ({ack.status}); el stop sigue activo"
            log.error(msg)
            self.db.log_event("error", "tp_failed", msg)
            self.notify("error", msg)

    async def _emergency_stop(self, a: PlaceEmergencyStop) -> None:
        snap = await self.broker.ticker(self.spec.symbol)
        if snap is None:
            self.notify("critical", "Sin precio para el stop de emergencia: revisa la cuenta YA")
            return
        ref = snap.mark or snap.last
        # 5 % de distancia: amplio a propósito, solo evita la pérdida catastrófica
        price = ref * (0.95 if a.side is Side.LONG else 1.05)
        req = OrderRequest(symbol=self.spec.symbol, side=a.side.exit_order_side, order_type="stp",
                           size=self.spec.round_size_down(a.size),
                           cli_ord_id=f"emerg-{int(time.time())}", stop_price=self.spec.round_price(price),
                           reduce_only=True, trigger_signal=self.cfg.stop_trigger_signal, role="emergency_sl")
        ack = await self.submit(req, None)
        self.notify("critical", f"Stop de emergencia en {price:.5f} para posición desconocida: {ack.status}")

    async def move_stop(self, st: TradeState, new_stop: float) -> None:
        new_stop = self.spec.round_price(new_stop)
        if st.sl_cli is None or new_stop == st.stop_price:
            return
        try:
            await self.broker.edit(cli_ord_id=st.sl_cli, stop_price=new_stop)
            st.stop_price = new_stop
            self.db.save_trade(st)
            log.info("Trailing: stop de %s movido a %s", st.trade_id, new_stop)
        except Exception as exc:  # noqa: BLE001
            log.warning("editorder falló (%r): coloco stop nuevo y cancelo el viejo", exc)
            old = st.sl_cli
            st.stop_price = new_stop
            await self._place_protection(st, PlaceProtection("sl", st.open_size, new_stop), replace=old)
            self.db.save_trade(st)

    async def flatten(self, st: TradeState | None, reason: str) -> None:
        """Cancela todas las órdenes del símbolo y cierra la posición a mercado (reduce-only)."""
        await self.broker.cancel_all(self.spec.symbol)
        for p in await self.broker.positions():
            if p.symbol != self.spec.symbol:
                continue
            cli = st.next_cli("flat") if st else f"kill-{int(time.time())}"
            ack = await self.submit(self.flatten_req(p.side, p.size, cli), st.trade_id if st else None)
            self.db.log_event("warning", "flatten", f"Cierre forzado ({reason}): {ack.status}")
        if st is not None:
            st.exit_reason = reason
            st.sl_cli = st.tp_cli = None
            self.db.save_trade(st)

    # ------------------------------------------------------- ejecuciones
    def fee_for(self, fill: Fill) -> float:
        if fill.fee is not None:
            return fill.fee
        rate = self.fees.maker if fill.fill_type == "maker" else self.fees.taker
        return fill.size * self.spec.contract_size * fill.price * rate

    async def process_fills(self, st: TradeState | None, fills: list[Fill]) -> None:
        for f in sorted(fills, key=lambda x: x.time):
            if f.symbol != self.spec.symbol:
                continue
            fee = self.fee_for(f)
            if st is None or not (f.cli_ord_id or "").startswith(st.trade_id + "-"):
                self.db.log_fill(f, None, fee)
                continue
            if not self.db.log_fill(f, st.trade_id, fee) and f.fill_id in st.seen_fills:
                continue
            actions = on_fill(st, f, fee)
            log.info("Ejecución %s %s %s @ %s (%s)", f.cli_ord_id, f.side, f.size, f.price, f.fill_type)
            await self.apply(st, actions)
            if st.status == "closed":
                msg = (f"Operación {st.trade_id} cerrada ({st.exit_reason}): P&L precio "
                       f"{st.realized_pnl:+.2f} comisiones {st.fees:.2f} funding {st.funding:+.2f}")
                self.db.log_event("info", "trade_closed", msg)
                self.notify("info", msg)
                break
