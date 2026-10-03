"""Tipos de datos compartidos por todos los módulos."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal
from enum import Enum


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1

    @property
    def entry_order_side(self) -> str:
        return "buy" if self is Side.LONG else "sell"

    @property
    def exit_order_side(self) -> str:
        return "sell" if self is Side.LONG else "buy"

    @property
    def opposite(self) -> "Side":
        return Side.SHORT if self is Side.LONG else Side.LONG


@dataclass(frozen=True)
class InstrumentSpec:
    """Especificación del contrato. En demo/live se construye desde /instruments."""
    symbol: str
    contract_size: float       # unidades del subyacente por contrato (lineal: XRP por contrato)
    tick_size: float           # incremento mínimo de precio
    size_step: float           # incremento mínimo de tamaño (10^-contractValuePrecision)
    min_size: float            # tamaño mínimo de orden
    maintenance_margin: float  # fracción del primer tramo de margen
    initial_margin: float | None = None
    max_position_size: float | None = None
    tradeable: bool = True
    post_only: bool = False    # si el mercado está en modo solo post-only

    def round_price(self, price: float, mode: str = "nearest") -> float:
        return _round_to_step(price, self.tick_size, mode)

    def round_size_down(self, size: float) -> float:
        return _round_to_step(size, self.size_step, "down")


def _round_to_step(value: float, step: float, mode: str) -> float:
    """Redondeo exacto a un múltiplo de step usando Decimal (evita 0.1+0.2)."""
    if step <= 0:
        raise ValueError("step debe ser > 0")
    # round(.., 10) absorbe errores de coma flotante (1999.9999999999998 -> 2000)
    v, s = Decimal(str(round(value, 10))), Decimal(str(step))
    rounding = {"down": ROUND_DOWN, "up": ROUND_UP, "nearest": ROUND_HALF_UP}[mode]
    units = (v / s).quantize(Decimal(1), rounding=rounding)
    return float(units * s)


@dataclass(frozen=True)
class Signal:
    time: object               # pd.Timestamp de apertura de la vela que genera la señal
    side: Side
    ref_price: float           # cierre de la vela de señal
    stop_price: float
    take_profit_price: float
    atr: float
    reason: str = ""


@dataclass
class MarketSnapshot:
    """Estado del mercado en vivo (ticker)."""
    symbol: str
    bid: float
    ask: float
    last: float
    mark: float
    ts: float                  # epoch segundos (hora del servidor si se conoce)
    funding_rate_rel: float | None = None   # funding relativo de la hora en curso
    bid_depth: float | None = None          # tamaño acumulado a -0,5 %
    ask_depth: float | None = None          # tamaño acumulado a +0,5 %

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.mid * 1e4 if self.mid > 0 else float("inf")


@dataclass
class OrderRequest:
    symbol: str
    side: str                  # "buy" | "sell"
    order_type: str            # "lmt" | "post" | "ioc" | "mkt" | "stp" | "take_profit"
    size: float
    cli_ord_id: str
    limit_price: float | None = None
    stop_price: float | None = None
    reduce_only: bool = False
    trigger_signal: str | None = None   # "mark" | "last" | "index"
    role: str = ""             # "entry" | "sl" | "tp" | "flatten" | "emergency_sl" (solo interno)


@dataclass
class OpenOrder:
    order_id: str
    cli_ord_id: str | None
    symbol: str
    side: str
    order_type: str
    size: float                # tamaño pendiente
    filled: float = 0.0
    limit_price: float | None = None
    stop_price: float | None = None
    reduce_only: bool = False


@dataclass
class Position:
    symbol: str
    side: Side
    size: float                # siempre positivo
    entry_price: float
    unrealized_funding: float = 0.0


@dataclass
class Fill:
    fill_id: str
    order_id: str
    cli_ord_id: str | None
    symbol: str
    side: str                  # "buy" | "sell"
    size: float
    price: float
    time: float                # epoch segundos
    fill_type: str = ""        # maker | taker | liquidation | ...
    fee: float | None = None


@dataclass
class OrderAck:
    order_id: str | None
    cli_ord_id: str
    status: str                # "placed" | "filled" | "partiallyFilled" | "cancelled" | rechazos...
    filled_size: float = 0.0
    avg_fill_price: float | None = None
    raw: dict = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.status in ("placed", "filled", "partiallyFilled", "attempted")
