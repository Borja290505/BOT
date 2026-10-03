"""Comisiones maker/taker desde /feeschedules y /feeschedules/volumes.

En la respuesta de /feeschedules cada plan tiene tramos (tiers) con makerFee,
takerFee y usdVolume. Los valores vienen en PORCENTAJE (0.02 = 0,02 %): se
convierten a fracción. Como salvaguarda, si una comisión convertida supera el
1 % se rechaza (indicaría que el supuesto de unidades es incorrecto).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fees:
    maker: float   # fracción, p. ej. 0.0002
    taker: float
    source: str = "endpoint"


class FeeParseError(ValueError):
    pass


def select_fees(schedules: list[dict], schedule_uid: str | None, volume_usd: float = 0.0) -> Fees:
    if not schedules:
        raise FeeParseError("lista de planes de comisiones vacía")
    sched = next((s for s in schedules if s.get("uid") == schedule_uid), None) if schedule_uid else None
    if sched is None:
        if len(schedules) != 1 and schedule_uid is None:
            raise FeeParseError("varios planes de comisiones y ninguno identificado para el instrumento")
        sched = schedules[0] if schedule_uid is None else None
    if sched is None:
        raise FeeParseError(f"plan de comisiones {schedule_uid} no encontrado")
    tiers = sorted(sched.get("tiers", []), key=lambda t: float(t.get("usdVolume", 0)))
    if not tiers:
        raise FeeParseError("plan de comisiones sin tramos")
    tier = tiers[0]
    for t in tiers:
        if volume_usd >= float(t.get("usdVolume", 0)):
            tier = t
    maker, taker = float(tier["makerFee"]) / 100, float(tier["takerFee"]) / 100
    if not (-0.01 < maker < 0.01 and 0 <= taker < 0.01):
        raise FeeParseError(f"comisiones fuera de rango: maker={maker} taker={taker}")
    return Fees(maker=maker, taker=taker)
