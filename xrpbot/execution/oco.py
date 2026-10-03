"""Estado de la operación y OCO propio (stop loss + take profit).

Kraken Futures no ofrece en la API una orden bracket/OCO nativa, así que:
- Tras la entrada se colocan DOS órdenes reduce-only en el exchange:
    SL: "stp" disparada por mark price, sin limitPrice => se ejecuta a mercado.
    TP: "post" (post-only, comisión maker) o "lmt" al precio objetivo.
- Cuando se ejecuta una (total o parcialmente), se cancela o se reduce la otra.
- Si el bot está caído cuando salta una, la otra queda viva en el libro, pero
  al ser reduce-only NO puede abrir una posición nueva; la reconciliación la
  cancela al volver.

Este módulo es lógica pura: recibe ejecuciones y devuelve acciones. No toca la
red, así se puede testear exhaustivamente.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..models import Fill, Side

EPS = 1e-9


@dataclass
class TradeState:
    trade_id: str
    side: Side
    status: str                     # entry_pending | open | closed | cancelled
    target_size: float
    stop_price: float
    tp_price: float
    contract_size: float = 1.0
    filled_size: float = 0.0
    exited_size: float = 0.0
    entry_avg: float = 0.0
    realized_pnl: float = 0.0
    fees: float = 0.0
    funding: float = 0.0            # estimado: suma horaria de -signo * nocional * funding relativo
    risk_usd: float = 0.0
    tp_r: float | None = None       # si se indica, el TP se recalcula sobre el precio real de entrada
    entry_cli: str | None = None
    sl_cli: str | None = None
    tp_cli: str | None = None
    seq: int = 0                    # para generar cliOrdId únicos en reemplazos
    exit_reason: str = ""
    opened_ts: float | None = None
    closed_ts: float | None = None
    exit_notional: float = 0.0
    seen_fills: list[str] = field(default_factory=list)

    @property
    def open_size(self) -> float:
        return max(0.0, self.filled_size - self.exited_size)

    @property
    def exit_avg(self) -> float | None:
        return self.exit_notional / self.exited_size if self.exited_size > EPS else None

    def next_cli(self, role: str) -> str:
        self.seq += 1
        return f"{self.trade_id}-{role}-{self.seq}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["side"] = self.side.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TradeState":
        d = dict(d)
        d["side"] = Side(d["side"])
        return cls(**d)


# ------------------------------------------------------------------ acciones
@dataclass(frozen=True)
class PlaceProtection:
    role: str            # "sl" | "tp"
    size: float
    price: float


@dataclass(frozen=True)
class ResizeOrder:
    role: str
    cli_ord_id: str
    size: float


@dataclass(frozen=True)
class CancelOrder:
    cli_ord_id: str | None = None
    order_id: str | None = None
    why: str = ""


@dataclass(frozen=True)
class Alert:
    level: str
    message: str


Action = PlaceProtection | ResizeOrder | CancelOrder | Alert


def make_trade_id(symbol: str, bar_time_iso: str, side: Side) -> str:
    """ID determinista por vela de señal: reprocesar la misma señal tras un
    reinicio genera el MISMO cliOrdId de entrada y Kraken rechaza el duplicado."""
    compact = bar_time_iso.replace("-", "").replace(":", "").replace("+0000", "")[:13]
    return f"{symbol.split('_')[-1].lower()}-{compact}-{side.value[0].upper()}"


def role_of(state: TradeState, cli_ord_id: str | None) -> str | None:
    if not cli_ord_id:
        return None
    if cli_ord_id == state.entry_cli:
        return "entry"
    if cli_ord_id == state.sl_cli or cli_ord_id.startswith(f"{state.trade_id}-sl-"):
        return "sl"
    if cli_ord_id == state.tp_cli or cli_ord_id.startswith(f"{state.trade_id}-tp-"):
        return "tp"
    if cli_ord_id.startswith(f"{state.trade_id}-flat-"):
        return "flatten"
    return None


def on_fill(state: TradeState, fill: Fill, fee: float = 0.0) -> list[Action]:
    """Aplica una ejecución al estado y devuelve las acciones necesarias."""
    if fill.fill_id in state.seen_fills:
        return []  # idempotente: la misma ejecución puede llegar por WS y por REST
    role = role_of(state, fill.cli_ord_id)
    if role is None:
        return [Alert("error", f"Ejecución {fill.fill_id} de orden desconocida {fill.cli_ord_id}")]
    state.seen_fills.append(fill.fill_id)
    state.fees += fee
    actions: list[Action] = []

    if role == "entry":
        if state.filled_size + fill.size > state.target_size + EPS:
            # la reconciliación ya adoptó el tamaño real de la posición: no contar dos veces
            return [Alert("info", f"Ejecución de entrada {fill.fill_id} ya reflejada en el tamaño")]
        new_filled = state.filled_size + fill.size
        state.entry_avg = (state.entry_avg * state.filled_size + fill.price * fill.size) / new_filled
        state.filled_size = new_filled
        if state.tp_r and state.tp_cli is None:
            state.tp_price = state.entry_avg + state.side.sign * state.tp_r * abs(state.entry_avg - state.stop_price)
        if state.status == "entry_pending":
            state.status = "open"
            state.opened_ts = fill.time
        actions += protection_actions(state)
        return actions

    # salida: sl, tp o cierre forzado
    qty = min(fill.size, state.open_size)
    state.exited_size += qty
    state.exit_notional += qty * fill.price
    state.realized_pnl += state.side.sign * qty * state.contract_size * (fill.price - state.entry_avg)
    if state.open_size <= EPS:
        state.status = "closed"
        state.closed_ts = fill.time
        state.exit_reason = {"sl": "stop", "tp": "take_profit"}.get(role, role)
        for other in ("sl", "tp"):
            cli = state.sl_cli if other == "sl" else state.tp_cli
            if cli and other != role:
                actions.append(CancelOrder(cli_ord_id=cli, why=f"OCO: {role} ejecutado"))
    else:
        # ejecución parcial: la otra pata se ajusta al tamaño restante
        for other in ("sl", "tp"):
            cli = state.sl_cli if other == "sl" else state.tp_cli
            if cli and other != role:
                actions.append(ResizeOrder(other, cli, state.open_size))
    return actions


def protection_actions(state: TradeState) -> list[Action]:
    """Coloca o ajusta SL y TP al tamaño abierto actual."""
    acts: list[Action] = []
    size = state.open_size
    if size <= EPS:
        return acts
    if state.sl_cli is None:
        acts.append(PlaceProtection("sl", size, state.stop_price))
    else:
        acts.append(ResizeOrder("sl", state.sl_cli, size))
    if state.tp_cli is None:
        acts.append(PlaceProtection("tp", size, state.tp_price))
    else:
        acts.append(ResizeOrder("tp", state.tp_cli, size))
    return acts
