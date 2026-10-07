"""Construye InstrumentSpec a partir de la respuesta de /instruments.

El bot NUNCA usa valores fijos de tick o tamaño en demo/live: los lee de aquí
al arrancar y se niega a operar si el símbolo no existe o no es negociable.

Campos usados (documentación de /instruments):
- tickSize: incremento de precio
- contractSize: unidades del subyacente por contrato
- contractValuePrecision: decimales permitidos en el tamaño de orden
  (tamaño mínimo e incremento = 10^-contractValuePrecision)
- marginLevels[0]: {initialMargin, maintenanceMargin} del primer tramo
- maxPositionSize, tradeable, postOnly
"""
from __future__ import annotations

from ..models import InstrumentSpec


class InstrumentNotAvailable(Exception):
    pass


def parse_instrument(instruments: list[dict], symbol: str) -> InstrumentSpec:
    match = [i for i in instruments if str(i.get("symbol", "")).upper() == symbol.upper()]
    if not match:
        similar = sorted(i.get("symbol", "") for i in instruments if "XRP" in str(i.get("symbol", "")).upper())
        raise InstrumentNotAvailable(f"{symbol} no aparece en /instruments. Símbolos con XRP: {similar}")
    raw = match[0]
    if not raw.get("tradeable", False):
        raise InstrumentNotAvailable(f"{symbol} existe pero no es negociable (tradeable=false)")

    tick = float(raw["tickSize"])
    # Si Kraken no informa la precisión (ocurre en PF_XRPUSD), se asumen contratos
    # enteros: es lo más prudente (un tamaño entero siempre es un múltiplo válido
    # si el exchange admitiera decimales; si exigiera más, rechazaría la orden).
    raw_precision = raw.get("contractValuePrecision")
    precision = int(raw_precision) if raw_precision is not None else 0
    step = 10.0 ** (-precision)
    levels = raw.get("marginLevels") or []
    mm = float(levels[0]["maintenanceMargin"]) if levels else None
    im = float(levels[0]["initialMargin"]) if levels else None
    if mm is None or not 0 < mm < 1:
        raise InstrumentNotAvailable(f"{symbol}: margen de mantenimiento no válido ({mm})")

    return InstrumentSpec(
        symbol=raw["symbol"].upper(),
        contract_size=float(raw.get("contractSize", 1.0)),
        tick_size=tick,
        size_step=step,
        min_size=step,
        maintenance_margin=mm,
        initial_margin=im,
        max_position_size=float(raw["maxPositionSize"]) if raw.get("maxPositionSize") else None,
        tradeable=True,
        post_only=bool(raw.get("postOnly", False)),
    )


def describe(spec: InstrumentSpec) -> str:
    return (f"{spec.symbol}: contrato={spec.contract_size} tick={spec.tick_size} "
            f"incremento/mínimo={spec.size_step} MM={spec.maintenance_margin:.4f} "
            f"IM={spec.initial_margin} maxPos={spec.max_position_size} postOnly={spec.post_only}")
