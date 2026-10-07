"""Contrato bot <-> interfaz (PROPUESTA, pendiente de confirmación; ver docs/ui/contrato.md).

Lo usan la interfaz y, cuando se confirme, el bot. Contiene solo reglas puras:
la lista cerrada de comandos, su caducidad, qué palabra de confirmación exige
cada uno y qué significa "reducir" cada parámetro (nunca aumentar el riesgo).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = 1

# Estados del bot que puede mostrar la interfaz
# "halted": detenido por el límite diario o por el kill switch (el motivo va en bot.reason);
# exige rearme manual. "down" no lo publica el bot: lo deduce la interfaz si el latido es viejo.
BOT_STATES = ("starting", "active", "paused", "halted", "frozen", "reconnecting", "down")

# Lista CERRADA de comandos. Cualquier otro tipo se rechaza en ambos lados.
COMMAND_TYPES = ("pause", "resume", "close_position", "kill", "rearm", "reduce_param")

# Palabra que hay que escribir para confirmar (None = confirmación simple)
CONFIRM_WORDS = {
    "pause": None,
    "resume": None,
    "rearm": None,
    "reduce_param": None,
    "close_position": "CERRAR",
    "kill": "KILL",
}

COMMAND_TTL_S = 60          # un comando no procesado en 60 s caduca y no se ejecuta nunca
HEARTBEAT_STALE_S = 15      # sin latido en 15 s => la interfaz considera el bot caído

# Parámetros reducibles desde la interfaz y qué dirección es "más estricta".
#   "lower"  -> solo se acepta un valor MENOR que el efectivo
#   "higher" -> solo se acepta un valor MAYOR que el efectivo
REDUCIBLE_PARAMS: dict[str, dict] = {
    "risk.risk_per_trade":                      {"dir": "lower",  "min": 0.0005, "label": "Riesgo por operación", "unit": "fracción"},
    "risk.max_daily_loss":                      {"dir": "lower",  "min": 0.001,  "label": "Pérdida diaria máxima", "unit": "fracción"},
    "risk.max_leverage":                        {"dir": "lower",  "min": 0.1,    "label": "Apalancamiento efectivo máximo", "unit": "×"},
    "circuit_breaker.max_spread_bps":           {"dir": "lower",  "min": 1.0,    "label": "Spread máximo", "unit": "pb"},
    "circuit_breaker.max_data_age_s":           {"dir": "lower",  "min": 5.0,    "label": "Antigüedad máxima del ticker", "unit": "s"},
    "circuit_breaker.max_candle_delay_bars":    {"dir": "lower",  "min": 1.0,    "label": "Retraso máximo de velas", "unit": "velas"},
    "circuit_breaker.max_atr_ratio":            {"dir": "lower",  "min": 1.0,    "label": "ATR máximo / mediana", "unit": "×"},
    "circuit_breaker.max_mark_last_divergence_bps": {"dir": "lower", "min": 1.0, "label": "Divergencia mark/last máxima", "unit": "pb"},
    "circuit_breaker.min_book_depth_multiple":  {"dir": "higher", "max": 100.0,  "label": "Profundidad mínima del libro", "unit": "× tamaño"},
}


class CommandError(ValueError):
    pass


@dataclass
class Command:
    type: str
    params: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    requested_by: str = ""
    source_ip: str = ""
    status: str = "pending"          # pending | accepted | rejected | expired | done | failed
    result: str = ""

    def __post_init__(self) -> None:
        if self.expires_at is None:
            self.expires_at = self.created_at + timedelta(seconds=COMMAND_TTL_S)

    def expired(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(timezone.utc)) >= self.expires_at

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "params": self.params,
            "created_at": self.created_at.isoformat(), "expires_at": self.expires_at.isoformat(),
            "requested_by": self.requested_by, "source_ip": self.source_ip,
            "status": self.status, "result": self.result,
        }


def check_confirm_word(cmd_type: str, word: str | None) -> None:
    expected = CONFIRM_WORDS.get(cmd_type)
    if expected is not None and (word or "").strip() != expected:
        raise CommandError(f"Para confirmar hay que escribir exactamente {expected}")


def validate_command_shape(cmd_type: str, params: dict) -> None:
    """Validación estructural común (la interfaz la aplica antes de enviar; el bot, al recibir)."""
    if cmd_type not in COMMAND_TYPES:
        raise CommandError(f"Comando no permitido: {cmd_type}")
    if cmd_type == "reduce_param":
        name = params.get("name")
        if name not in REDUCIBLE_PARAMS:
            raise CommandError(f"Parámetro no reducible desde la interfaz: {name}")
        try:
            float(params.get("value"))
        except (TypeError, ValueError):
            raise CommandError("Valor no numérico") from None
    elif params:
        raise CommandError(f"El comando {cmd_type} no admite parámetros")


def validate_reduction(name: str, new_value: float, effective_value: float) -> None:
    """Rechaza cualquier cambio que no sea estrictamente más prudente."""
    rule = REDUCIBLE_PARAMS[name]
    if rule["dir"] == "lower":
        if not new_value < effective_value:
            raise CommandError(f"{rule['label']}: solo se puede reducir (actual {effective_value}). "
                               "Para aumentarlo, edita config/settings.yaml y reinicia el bot.")
        if new_value < rule["min"]:
            raise CommandError(f"{rule['label']}: el mínimo admitido es {rule['min']}")
    else:
        if not new_value > effective_value:
            raise CommandError(f"{rule['label']}: solo se puede endurecer (subir) desde la interfaz "
                               f"(actual {effective_value}).")
        if new_value > rule["max"]:
            raise CommandError(f"{rule['label']}: el máximo admitido es {rule['max']}")


def effective_value(name: str, file_value: float, override: float | None) -> float:
    """Valor efectivo = el más estricto entre el fichero y el override."""
    if override is None:
        return file_value
    return min(file_value, override) if REDUCIBLE_PARAMS[name]["dir"] == "lower" else max(file_value, override)


def derive_state(bot: dict, now: datetime | None = None) -> str:
    """Estado mostrado: el publicado por el bot, salvo que su latido sea viejo (=> down)."""
    hb = bot.get("heartbeat_at")
    if not hb:
        return "down"
    age = ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(hb)).total_seconds()
    if age > HEARTBEAT_STALE_S:
        return "down"
    return bot.get("state", "down")
