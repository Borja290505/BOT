"""Cálculo del tamaño de posición.

Contrato lineal (PF_): P&L = tamaño * contract_size * (salida - entrada) * signo.

Pérdida por contrato si salta el stop (estimación prudente):
    |entrada - stop| * contract_size                      (movimiento de precio)
  + entrada * contract_size * (comisión_taker + slippage)  (entrada)
  + stop    * contract_size * (comisión_taker + slippage)  (salida por stop, taker)

tamaño = floor_a_incremento( min(
    riesgo_usd / pérdida_por_contrato,                     # 1 % del capital
    max_leverage * capital / (entrada * contract_size),    # tope de apalancamiento efectivo
    max_position_size del instrumento ))

Si el resultado es menor que el tamaño mínimo -> no se opera (nunca se redondea
hacia arriba, eso superaría el riesgo permitido).

Limitación: el slippage real en un gap puede superar el estimado; el 1 % es un
objetivo, no un máximo garantizado.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..models import InstrumentSpec


@dataclass(frozen=True)
class SizingResult:
    size: float
    risk_usd: float              # pérdida estimada si salta el stop con este tamaño
    notional_usd: float
    effective_leverage: float
    limited_by: str              # "risk" | "leverage" | "max_position" | "below_min" | "invalid"
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.size > 0


def position_size(*, capital_usd: float, entry_price: float, stop_price: float,
                  spec: InstrumentSpec, risk_per_trade: float, max_leverage: float,
                  taker_fee: float, slippage_bps: float) -> SizingResult:
    def reject(why: str, by: str = "invalid") -> SizingResult:
        return SizingResult(0.0, 0.0, 0.0, 0.0, by, why)

    if capital_usd <= 0:
        return reject("capital <= 0")
    if entry_price <= 0 or stop_price <= 0:
        return reject("precios no válidos")
    stop_dist = abs(entry_price - stop_price)
    if stop_dist < spec.tick_size:
        return reject("stop a menos de un tick de la entrada")

    cost_rate = taker_fee + slippage_bps / 1e4
    loss_per_contract = spec.contract_size * (stop_dist + (entry_price + stop_price) * cost_rate)
    risk_budget = capital_usd * risk_per_trade

    by_risk = risk_budget / loss_per_contract
    by_lev = max_leverage * capital_usd / (entry_price * spec.contract_size)
    candidates = {"risk": by_risk, "leverage": by_lev}
    if spec.max_position_size:
        candidates["max_position"] = spec.max_position_size
    limited_by = min(candidates, key=candidates.get)
    size = spec.round_size_down(candidates[limited_by])

    if size < spec.min_size:
        return reject(f"tamaño {candidates[limited_by]:.4f} < mínimo {spec.min_size} "
                      f"(limitado por {limited_by})", "below_min")

    notional = size * spec.contract_size * entry_price
    return SizingResult(
        size=size,
        risk_usd=size * loss_per_contract,
        notional_usd=notional,
        effective_leverage=notional / capital_usd,
        limited_by=limited_by,
    )
