"""Limitador por coste (cubo de fichas) para la API de Kraken Futures.

Documentación de Kraken: en los endpoints /derivatives se pueden gastar hasta
500 puntos cada 10 s. Costes documentados: sendorder 10, editorder 10,
cancelorder 10, batchorder 9 + tamaño del lote, cancelallorders 25.
Los GET de consulta tienen coste propio (ver docs); usamos una estimación
conservadora y además dejamos un margen de seguridad (por defecto 80 % del cupo).
"""
from __future__ import annotations

import asyncio
import time

ENDPOINT_COST = {
    "sendorder": 10,
    "editorder": 10,
    "cancelorder": 10,
    "cancelallorders": 25,
    "cancelallordersafter": 25,
    "batchorder": 10,
    # Consultas: coste estimado conservador.
    "accounts": 2,
    "openpositions": 2,
    "openorders": 2,
    "fills": 2,
    "leveragepreferences": 2,
    "feeschedules/volumes": 2,
}
DEFAULT_COST = 2


def cost_of(endpoint: str) -> int:
    return ENDPOINT_COST.get(endpoint.strip("/"), DEFAULT_COST)


class CostRateLimiter:
    def __init__(self, capacity: float = 500, window_s: float = 10.0, safety: float = 0.8,
                 clock=time.monotonic) -> None:
        self.capacity = capacity * safety
        self.refill_per_s = self.capacity / window_s
        self._tokens = self.capacity
        self._clock = clock
        self._last = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.refill_per_s)
        self._last = now

    def try_acquire(self, cost: float) -> float:
        """Devuelve 0 si se concede; si no, los segundos que hay que esperar."""
        self._refill()
        if cost > self.capacity:
            raise ValueError("coste mayor que la capacidad del limitador")
        if self._tokens >= cost:
            self._tokens -= cost
            return 0.0
        return (cost - self._tokens) / self.refill_per_s

    async def acquire(self, cost: float) -> None:
        async with self._lock:
            while (wait := self.try_acquire(cost)) > 0:
                await asyncio.sleep(wait)
