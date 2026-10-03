"""Interfaz de broker y sus implementaciones.

- KrakenBroker: demo y live (misma clase, distinta URL y claves).
- PaperBroker: simulador interno (modo sim) alimentado con datos públicos en
  vivo. Simula órdenes stp/take_profit/lmt/post/ioc/mkt con reduce-only,
  comisiones, slippage y funding horario. Es una aproximación: no modela la
  cola del libro ni ejecuciones parciales.

El resto del bot solo habla con la interfaz Broker, así que la lógica de
estrategia, riesgo, OCO y reconciliación es idéntica en sim, demo y live.
"""
from __future__ import annotations

import itertools
import time
from typing import Protocol

from ..exchange.fees import Fees
from ..exchange.rest import KrakenFuturesRest, _f
from ..models import (Fill, InstrumentSpec, MarketSnapshot, OpenOrder, OrderAck, OrderRequest,
                      Position, Side)


class Broker(Protocol):
    async def positions(self) -> list[Position]: ...
    async def open_orders(self) -> list[OpenOrder]: ...
    async def recent_fills(self) -> list[Fill]: ...
    async def send(self, req: OrderRequest) -> OrderAck: ...
    async def edit(self, *, cli_ord_id: str, size: float | None = None,
                   stop_price: float | None = None, limit_price: float | None = None) -> None: ...
    async def cancel(self, *, cli_ord_id: str | None = None, order_id: str | None = None) -> str: ...
    async def cancel_all(self, symbol: str) -> None: ...
    async def equity(self) -> float: ...
    async def ticker(self, symbol: str) -> MarketSnapshot | None: ...


class KrakenBroker:
    def __init__(self, rest: KrakenFuturesRest) -> None:
        self.rest = rest

    async def positions(self) -> list[Position]:
        return await self.rest.get_open_positions()

    async def open_orders(self) -> list[OpenOrder]:
        return await self.rest.get_open_orders()

    async def recent_fills(self) -> list[Fill]:
        return await self.rest.get_fills()

    async def send(self, req: OrderRequest) -> OrderAck:
        return await self.rest.send_order(req)

    async def edit(self, *, cli_ord_id: str, size=None, stop_price=None, limit_price=None) -> None:
        await self.rest.edit_order(cli_ord_id=cli_ord_id, size=size, stop_price=stop_price,
                                   limit_price=limit_price)

    async def cancel(self, *, cli_ord_id=None, order_id=None) -> str:
        return await self.rest.cancel_order(order_id=order_id, cli_ord_id=cli_ord_id)

    async def cancel_all(self, symbol: str) -> None:
        await self.rest.cancel_all_orders(symbol)

    async def equity(self) -> float:
        return await self.rest.get_equity_usd()

    async def ticker(self, symbol: str) -> MarketSnapshot | None:
        t = await self.rest.get_ticker(symbol)
        if not t or t.get("bid") is None or t.get("ask") is None:
            return None
        return MarketSnapshot(symbol=symbol.upper(), bid=float(t["bid"]), ask=float(t["ask"]),
                              last=_f(t.get("last"), 0.0), mark=_f(t.get("markPrice"), 0.0),
                              ts=self.rest.server_time())


class PaperBroker:
    """Exchange simulado en memoria para el modo sim."""

    def __init__(self, spec: InstrumentSpec, fees: Fees, initial_cash: float,
                 slippage_bps: float = 5.0, clock=time.time) -> None:
        self.spec, self.fees = spec, fees
        self.cash = initial_cash
        self.slip = slippage_bps / 1e4
        self.clock = clock
        self.pos: Position | None = None
        self.orders: dict[str, OpenOrder] = {}
        self.order_meta: dict[str, OrderRequest] = {}
        self.fills: list[Fill] = []
        self.snap: MarketSnapshot | None = None
        self._ids = itertools.count(1)
        self._last_funding_hour: int | None = None

    # ------------------------------------------------------------ mercado
    def on_market(self, snap: MarketSnapshot) -> None:
        self.snap = snap
        self._apply_funding(snap)
        for cli, o in list(self.orders.items()):
            req = self.order_meta[cli]
            price = self._trigger_price(req)
            if price is not None:
                self._execute(req, o.order_id, price, taker=req.order_type != "post" and req.order_type != "lmt")

    def _trigger_price(self, req: OrderRequest) -> float | None:
        s = self.snap
        buy = req.side == "buy"
        if req.order_type == "stp":
            ref = s.mark if req.trigger_signal == "mark" and s.mark else s.last
            hit = ref >= req.stop_price if buy else ref <= req.stop_price
            return (s.ask * (1 + self.slip) if buy else s.bid * (1 - self.slip)) if hit else None
        if req.order_type in ("lmt", "post"):
            hit = s.ask <= req.limit_price if buy else s.bid >= req.limit_price
            return req.limit_price if hit else None
        return None

    def _apply_funding(self, snap: MarketSnapshot) -> None:
        hour = int(snap.ts // 3600)
        if self._last_funding_hour is None:
            self._last_funding_hour = hour
            return
        if hour != self._last_funding_hour and self.pos and snap.funding_rate_rel is not None:
            q = self.pos.size * self.spec.contract_size
            self.cash -= self.pos.side.sign * q * snap.mark * snap.funding_rate_rel
        self._last_funding_hour = hour

    # ----------------------------------------------------------- ejecución
    def _execute(self, req: OrderRequest, order_id: str, price: float, taker: bool) -> float:
        side = Side.LONG if req.side == "buy" else Side.SHORT
        size = req.size
        if req.reduce_only:
            if not self.pos or self.pos.side is side:
                self._remove(req.cli_ord_id)
                return 0.0
            size = min(size, self.pos.size)
        q = size * self.spec.contract_size
        fee = q * price * (self.fees.taker if taker else self.fees.maker)
        self.cash -= fee
        if self.pos is None:
            self.pos = Position(self.spec.symbol, side, size, price)
        elif self.pos.side is side:
            tot = self.pos.size + size
            self.pos.entry_price = (self.pos.entry_price * self.pos.size + price * size) / tot
            self.pos.size = tot
        else:
            closed = min(size, self.pos.size)
            self.cash += self.pos.side.sign * closed * self.spec.contract_size * (price - self.pos.entry_price)
            self.pos.size -= closed
            if self.pos.size <= 1e-12:
                self.pos = None
        self.fills.append(Fill(fill_id=f"pf{next(self._ids)}", order_id=order_id,
                               cli_ord_id=req.cli_ord_id, symbol=req.symbol, side=req.side, size=size,
                               price=price, time=self.clock(), fill_type="taker" if taker else "maker",
                               fee=fee))
        self._remove(req.cli_ord_id)
        return size

    def _remove(self, cli: str) -> None:
        self.orders.pop(cli, None)
        self.order_meta.pop(cli, None)

    # ------------------------------------------------------------ interfaz
    async def positions(self) -> list[Position]:
        return [Position(**vars(self.pos))] if self.pos else []

    async def open_orders(self) -> list[OpenOrder]:
        return [OpenOrder(**vars(o)) for o in self.orders.values()]

    async def recent_fills(self) -> list[Fill]:
        return list(self.fills[-100:])

    async def send(self, req: OrderRequest) -> OrderAck:
        if req.cli_ord_id in self.orders or any(f.cli_ord_id == req.cli_ord_id for f in self.fills):
            return OrderAck(None, req.cli_ord_id, "clientOrderIdAlreadyExist")
        if self.snap is None:
            return OrderAck(None, req.cli_ord_id, "marketInactive")
        oid = f"po{next(self._ids)}"
        s = self.snap
        buy = req.side == "buy"
        if req.order_type in ("ioc", "mkt"):
            px = s.ask * (1 + self.slip) if buy else s.bid * (1 - self.slip)
            if req.order_type == "ioc" and (px > req.limit_price if buy else px < req.limit_price):
                return OrderAck(oid, req.cli_ord_id, "iocWouldNotExecute")
            filled = self._execute(req, oid, px, taker=True)
            if filled <= 0:
                return OrderAck(oid, req.cli_ord_id, "wouldNotReducePosition")
            return OrderAck(oid, req.cli_ord_id, "placed", filled, px)
        if req.order_type == "post":
            crosses = s.ask <= req.limit_price if buy else s.bid >= req.limit_price
            if crosses:
                return OrderAck(oid, req.cli_ord_id, "postWouldExecute")
        if req.order_type == "stp":
            ref = s.mark if req.trigger_signal == "mark" and s.mark else s.last
            if (buy and ref >= req.stop_price) or (not buy and ref <= req.stop_price):
                # stop ya cruzado al colocarlo: se ejecuta a mercado
                filled = self._execute(req, oid, s.ask * (1 + self.slip) if buy else s.bid * (1 - self.slip), True)
                return OrderAck(oid, req.cli_ord_id, "placed", filled)
        self.orders[req.cli_ord_id] = OpenOrder(
            order_id=oid, cli_ord_id=req.cli_ord_id, symbol=req.symbol, side=req.side,
            order_type=req.order_type, size=req.size, limit_price=req.limit_price,
            stop_price=req.stop_price, reduce_only=req.reduce_only)
        self.order_meta[req.cli_ord_id] = req
        return OrderAck(oid, req.cli_ord_id, "placed")

    async def edit(self, *, cli_ord_id: str, size=None, stop_price=None, limit_price=None) -> None:
        if cli_ord_id not in self.orders:
            raise KeyError(f"orderForEditNotFound {cli_ord_id}")
        o, req = self.orders[cli_ord_id], self.order_meta[cli_ord_id]
        if size is not None:
            o.size = req.size = size
        if stop_price is not None:
            o.stop_price = req.stop_price = stop_price
        if limit_price is not None:
            o.limit_price = req.limit_price = limit_price

    async def cancel(self, *, cli_ord_id=None, order_id=None) -> str:
        if cli_ord_id is None and order_id is not None:
            cli_ord_id = next((c for c, o in self.orders.items() if o.order_id == order_id), None)
        if cli_ord_id in self.orders:
            self._remove(cli_ord_id)
            return "cancelled"
        return "notFound"

    async def cancel_all(self, symbol: str) -> None:
        self.orders.clear()
        self.order_meta.clear()

    async def equity(self) -> float:
        unreal = 0.0
        if self.pos and self.snap:
            unreal = self.pos.side.sign * self.pos.size * self.spec.contract_size * \
                (self.snap.mark or self.snap.last) - self.pos.side.sign * self.pos.size * \
                self.spec.contract_size * self.pos.entry_price
        return self.cash + unreal

    async def ticker(self, symbol: str) -> MarketSnapshot | None:
        return self.snap
