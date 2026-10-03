"""Reconciliación del estado interno con el exchange.

Se ejecuta al arrancar, tras cada reconexión y periódicamente. El EXCHANGE es
la fuente de verdad sobre posiciones y órdenes; el estado local aporta la
intención (qué stop y qué TP debería haber).

Reglas:
- Lo que se puede resolver sin adivinar se resuelve (recolocar un stop que
  falta, cancelar órdenes huérfanas reduce-only, ajustar tamaños).
- Lo que no se puede resolver con certeza CONGELA el bot (no abre nada más) y
  avisa: posición desconocida, lado contrario al esperado, etc.
- Una posición desconocida sin stop recibe, si así se configura, un stop de
  emergencia: reduce el riesgo sin decidir nada sobre la posición.

Lógica pura: recibe instantáneas y devuelve un plan de acciones.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..execution.oco import EPS, Alert, CancelOrder, PlaceProtection, ResizeOrder, TradeState
from ..models import OpenOrder, Position, Side


@dataclass(frozen=True)
class PlaceEmergencyStop:
    side: Side           # lado de la POSICIÓN a proteger
    size: float


@dataclass(frozen=True)
class AdoptExchangeSize:
    size: float
    entry_price: float


@dataclass(frozen=True)
class MarkClosed:
    reason: str


@dataclass(frozen=True)
class MarkEntryFilled:
    size: float
    entry_price: float


@dataclass(frozen=True)
class MarkCancelled:
    reason: str


@dataclass
class ReconcilePlan:
    freeze: bool = False
    actions: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.freeze and not self.actions


def _is_ours(o: OpenOrder, trade_id: str | None) -> bool:
    return bool(trade_id and o.cli_ord_id and o.cli_ord_id.startswith(trade_id + "-"))


def reconcile(*, symbol: str, local: TradeState | None, positions: list[Position],
              open_orders: list[OpenOrder], entry_order_live: bool = False,
              protect_unknown: bool = True) -> ReconcilePlan:
    plan = ReconcilePlan()
    pos = next((p for p in positions if p.symbol == symbol.upper()), None)
    orders = [o for o in open_orders if o.symbol == symbol.upper()]
    active = local if local and local.status in ("entry_pending", "open") else None
    trade_id = active.trade_id if active else None

    # 1) Sin operación activa local
    if active is None:
        if pos is not None:
            plan.freeze = True
            plan.notes.append(f"Posición DESCONOCIDA en el exchange: {pos.side.value} {pos.size} @ {pos.entry_price}")
            plan.actions.append(Alert("critical", plan.notes[-1] + ". Bot congelado."))
            has_stop = any(o.reduce_only and o.order_type == "stp"
                           and o.side == pos.side.exit_order_side for o in orders)
            if protect_unknown and not has_stop:
                plan.actions.append(PlaceEmergencyStop(pos.side, pos.size))
            return plan
        for o in orders:
            plan.actions.append(CancelOrder(order_id=o.order_id, cli_ord_id=o.cli_ord_id,
                                            why="orden huérfana sin operación activa"))
            if not o.reduce_only:
                plan.notes.append(f"Orden no reduce-only desconocida {o.order_id} cancelada")
        return plan

    # Órdenes que no pertenecen a la operación activa: fuera.
    for o in orders:
        if not _is_ours(o, trade_id):
            plan.actions.append(CancelOrder(order_id=o.order_id, cli_ord_id=o.cli_ord_id,
                                            why="orden ajena a la operación activa"))
    ours = [o for o in orders if _is_ours(o, trade_id)]

    # 2) Entrada pendiente (p. ej. envío ambiguo)
    if active.status == "entry_pending":
        if pos is not None and pos.side is active.side:
            plan.actions.append(MarkEntryFilled(pos.size, pos.entry_price))
            plan.notes.append("Entrada confirmada por la posición del exchange")
        elif pos is not None:
            plan.freeze = True
            plan.actions.append(Alert("critical", f"Posición {pos.side.value} contraria a la entrada "
                                                  f"pendiente {active.side.value}. Bot congelado."))
        elif not entry_order_live:
            plan.actions.append(MarkCancelled("la entrada no se ejecutó"))
        return plan

    # 3) Operación abierta
    if pos is None:
        plan.actions.append(MarkClosed("posición cerrada en el exchange (stop/TP o liquidación) "
                                       "mientras el bot no lo veía"))
        for o in ours:
            plan.actions.append(CancelOrder(order_id=o.order_id, cli_ord_id=o.cli_ord_id,
                                            why="posición ya cerrada"))
        return plan

    if pos.side is not active.side:
        plan.freeze = True
        plan.actions.append(Alert("critical", f"Lado de la posición ({pos.side.value}) distinto del "
                                              f"esperado ({active.side.value}). Bot congelado."))
        return plan

    if abs(pos.size - active.open_size) > EPS:
        plan.notes.append(f"Tamaño en exchange {pos.size} != local {active.open_size}: se adopta el del exchange")
        plan.actions.append(AdoptExchangeSize(pos.size, pos.entry_price))
    size = pos.size

    sl = next((o for o in ours if o.cli_ord_id == active.sl_cli), None)
    tp = next((o for o in ours if o.cli_ord_id == active.tp_cli), None)
    if sl is None:
        plan.notes.append("Falta el stop loss en el exchange: se recoloca")
        plan.actions.append(PlaceProtection("sl", size, active.stop_price))
    elif abs(sl.size - size) > EPS:
        plan.actions.append(ResizeOrder("sl", sl.cli_ord_id, size))
    if tp is None:
        plan.notes.append("Falta el take profit en el exchange: se recoloca")
        plan.actions.append(PlaceProtection("tp", size, active.tp_price))
    elif abs(tp.size - size) > EPS:
        plan.actions.append(ResizeOrder("tp", tp.cli_ord_id, size))
    for o in ours:
        if o is not sl and o is not tp:
            plan.actions.append(CancelOrder(order_id=o.order_id, cli_ord_id=o.cli_ord_id,
                                            why="orden duplicada/obsoleta de la operación"))
    return plan
