"""Límites de riesgo: pérdida diaria, liquidación y apalancamiento.

Pérdida diaria
- El "día" empieza a las 00:00 UTC. La referencia es la equity (realizado +
  no realizado) al primer control del día.
- Si equity <= referencia * (1 - max_daily_loss): se activa la parada. El bot
  cierra todo y queda DETENIDO hasta un rearme MANUAL (python -m xrpbot.cli rearm).
  El estado se guarda en SQLite: reiniciar el bot no se salta la parada.
- Limitación: un depósito o una retirada durante el día distorsiona la
  referencia. No muevas fondos con el bot en marcha.

Liquidación
- Se estima el precio de liquidación y se exige que quede al menos N veces más
  lejos de la entrada que el stop. Es una ESTIMACIÓN (Kraken usa margen por
  tramos y el mark price); el margen de seguridad N existe por eso.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from ..models import Side


class KeyValueStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str | None) -> None: ...


class DictStore:
    """Almacén en memoria (backtest y tests)."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str | None) -> None:
        if value is None:
            self.data.pop(key, None)
        else:
            self.data[key] = value


@dataclass(frozen=True)
class GuardStatus:
    halted: bool
    just_tripped: bool
    day_start_equity: float | None
    loss_pct: float
    reason: str = ""


class DailyLossGuard:
    K_DAY, K_START, K_HALTED, K_REASON = "dl.day", "dl.start_equity", "dl.halted", "dl.reason"

    def __init__(self, store: KeyValueStore, max_daily_loss: float) -> None:
        self.store = store
        self.max_daily_loss = max_daily_loss

    @property
    def halted(self) -> bool:
        return self.store.get(self.K_HALTED) == "1"

    @property
    def halt_reason(self) -> str:
        return self.store.get(self.K_REASON) or ""

    @property
    def halt_day(self) -> str | None:
        return self.store.get(self.K_DAY)

    def halt(self, reason: str) -> None:
        self.store.set(self.K_HALTED, "1")
        self.store.set(self.K_REASON, reason)

    def rearm(self) -> None:
        """Rearme manual: borra la parada y la referencia (se fija en el siguiente control)."""
        self.store.set(self.K_HALTED, "0")
        self.store.set(self.K_REASON, None)
        self.store.set(self.K_START, None)

    def check(self, equity: float, now: datetime) -> GuardStatus:
        day = now.astimezone(timezone.utc).date().isoformat()
        start_raw = self.store.get(self.K_START)
        if self.halted:
            start = float(start_raw) if start_raw else None
            loss = (start - equity) / start if start else 0.0
            return GuardStatus(True, False, start, loss, self.halt_reason)
        if self.store.get(self.K_DAY) != day or start_raw is None:
            self.store.set(self.K_DAY, day)
            self.store.set(self.K_START, repr(equity))
            start_raw = repr(equity)
        start = float(start_raw)
        loss = (start - equity) / start if start > 0 else 0.0
        if loss >= self.max_daily_loss:
            reason = (f"pérdida diaria {loss:.2%} >= límite {self.max_daily_loss:.2%} "
                      f"(equity {equity:.2f} / inicio del día {start:.2f})")
            self.halt(reason)
            return GuardStatus(True, True, start, loss, reason)
        return GuardStatus(False, False, start, loss)


def estimate_liquidation_price(*, side: Side, entry: float, size: float, contract_size: float,
                               maintenance_margin: float, margin_mode: str,
                               collateral_usd: float, isolated_leverage: float | None = None) -> float:
    """Precio de liquidación aproximado para un contrato lineal.

    - isolated: margen asignado = nocional / apalancamiento. Se liquida cuando la
      pérdida consume ese margen menos el de mantenimiento.
    - cross: toda la equity de la cuenta respalda la posición.
    Devuelve 0 (largo) o inf (corto) si no hay liquidación posible por precio.
    """
    q = size * contract_size
    if q <= 0:
        raise ValueError("tamaño no válido")
    mm = maintenance_margin
    if margin_mode == "isolated":
        if not isolated_leverage or isolated_leverage <= 0:
            raise ValueError("isolated requiere isolated_leverage")
        frac = 1 / isolated_leverage - mm
        price = entry * (1 - frac) if side is Side.LONG else entry * (1 + frac)
    else:
        if side is Side.LONG:
            price = (q * entry - collateral_usd) / (q * (1 - mm))
        else:
            price = (collateral_usd + q * entry) / (q * (1 + mm))
    if side is Side.LONG:
        return max(price, 0.0)
    return price if price > 0 else float("inf")


def liquidation_is_safe(*, side: Side, entry: float, stop: float, liq_price: float,
                        min_multiple: float) -> tuple[bool, str]:
    stop_dist = abs(entry - stop)
    if side is Side.LONG:
        if liq_price >= stop:
            return False, f"liquidación estimada {liq_price:.5f} por encima del stop {stop:.5f}"
        liq_dist = entry - liq_price
    else:
        if liq_price <= stop:
            return False, f"liquidación estimada {liq_price:.5f} por debajo del stop {stop:.5f}"
        liq_dist = liq_price - entry
    if liq_dist < min_multiple * stop_dist:
        return False, (f"liquidación a {liq_dist:.5f} de la entrada, menos de {min_multiple}x "
                       f"la distancia al stop ({stop_dist:.5f})")
    return True, ""
