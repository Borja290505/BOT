"""Watchdog independiente: avisa si el bot deja de latir.

Es un proceso aparte (otro contenedor u otro servicio). Solo lee el fichero de
latido y no tiene claves de Kraken. Si el bot se cuelga, se cae o su máquina se
queda sin red, el bot no puede avisar de nada; el watchdog sí.

Avisos (por push web a la PWA y por Telegram como respaldo):
  - SIN LATIDO: el latido tiene más de N minutos (WATCHDOG_MAX_SILENCE_MIN, por defecto 5).
  - VIVO PERO SIN CICLOS CORRECTOS: el bucle late pero lleva mucho sin completar
    un ciclo sin errores (p. ej. Kraken no responde).
  - DETENIDO / CONGELADO: el bot ha pasado a esos estados (con su motivo).
  - Recordatorio cada WATCHDOG_REMIND_MIN (por defecto 30) mientras dure el problema.
  - RECUPERADO: cuando vuelve a la normalidad.

Configuración por variables de entorno: WATCHDOG_MAX_SILENCE_MIN, WATCHDOG_REMIND_MIN,
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID y, para push web, UI_STATE_DIR (donde la
interfaz guarda las claves VAPID y las suscripciones).

Límite: si se cae el SERVIDOR entero, se caen a la vez el bot y el watchdog y nadie
avisa. Para cubrirlo, WATCHDOG_PING_URL (opcional) recibe un ping cada minuto
mientras el bot late bien; un servicio externo de «dead man's switch» (p. ej.
healthchecks.io, con aviso a Telegram) avisa cuando los pings dejan de llegar.
"""
from __future__ import annotations

import logging
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .heartbeat import age_seconds, heartbeat_path, read_heartbeat

log = logging.getLogger("xrpbot.watchdog")


@dataclass
class Alert:
    level: str          # "critical" | "warning" | "info"
    key: str            # tipo de problema (para no repetir)
    title: str
    body: str


@dataclass
class WatchdogState:
    active: dict[str, datetime] = field(default_factory=dict)   # problema -> último aviso enviado
    last_state: str | None = None


class Watchdog:
    def __init__(self, mode: str, max_silence_min: float = 5, remind_min: float = 30,
                 stale_ok_min: float | None = None) -> None:
        self.mode = mode
        self.max_silence = timedelta(minutes=max_silence_min)
        self.remind = timedelta(minutes=remind_min)
        self.stale_ok = timedelta(minutes=stale_ok_min if stale_ok_min is not None else max(15.0, 3 * max_silence_min))
        self.st = WatchdogState()

    def check(self, hb: dict | None, now: datetime) -> list[Alert]:
        problems: dict[str, Alert] = {}
        tag = self.mode.upper()
        if hb is None:
            problems["silence"] = Alert("critical", "silence", f"[{tag}] Bot SIN LATIDO",
                                        "No existe el fichero de latido: el bot no ha arrancado o no escribe su estado.")
        else:
            age = age_seconds(hb.get("ts"), now)
            if age is None or age > self.max_silence.total_seconds():
                mins = f"{age / 60:.0f} min" if age is not None else "?"
                problems["silence"] = Alert("critical", "silence", f"[{tag}] Bot SIN LATIDO desde hace {mins}",
                                            "El proceso del bot está caído o bloqueado. Revisa el servidor "
                                            "(docker compose ps / journalctl). Las órdenes de stop y TP siguen en Kraken.")
            else:
                ok_age = age_seconds(hb.get("last_ok_at"), now)
                started_age = age_seconds(hb.get("started_at"), now) or 0
                if (ok_age is None and started_age > self.stale_ok.total_seconds()) or \
                        (ok_age is not None and ok_age > self.stale_ok.total_seconds()):
                    since = f"{ok_age / 60:.0f} min" if ok_age is not None else "el arranque"
                    problems["not_ok"] = Alert("warning", "not_ok", f"[{tag}] Bot vivo pero SIN CICLOS CORRECTOS",
                                               f"Sin un ciclo completo sin errores desde hace {since}. "
                                               "Posible caída de conexión con Kraken: revisa el log del bot.")
                state = hb.get("state")
                if state in ("halted", "frozen"):
                    word = "DETENIDO" if state == "halted" else "CONGELADO"
                    problems["state"] = Alert("critical" if state == "frozen" else "warning", "state",
                                              f"[{tag}] Bot {word}", hb.get("reason") or "Requiere rearme manual.")
        alerts: list[Alert] = []
        for key, alert in problems.items():
            last = self.st.active.get(key)
            if last is None or now - last >= self.remind:
                if last is not None:
                    alert.title = "Recordatorio · " + alert.title
                alerts.append(alert)
                self.st.active[key] = now
        for key in list(self.st.active):
            if key not in problems:
                del self.st.active[key]
                names = {"silence": "El bot vuelve a latir", "not_ok": "Los ciclos del bot vuelven a ser correctos",
                         "state": "El bot vuelve a estar activo"}
                alerts.append(Alert("info", key, f"[{tag}] RECUPERADO", names.get(key, key)))
        return alerts


# ---------------------------------------------------------------------- envío
def send_telegram(token: str | None, chat_id: str | None, alert: Alert, timeout: float = 10) -> bool:
    if not (token and chat_id):
        return False
    icon = {"critical": "🛑", "warning": "⚠️", "info": "✅"}.get(alert.level, "")
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": f"{icon} {alert.title}\n{alert.body}"}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=timeout) as r:
            return r.status == 200
    except Exception as exc:  # noqa: BLE001 - un fallo de Telegram no debe parar el watchdog
        log.warning("Telegram falló: %r", exc)
        return False


def send_push(ui_state_dir: str | None, alert: Alert) -> int:
    """Envía push web a las suscripciones de la PWA. Devuelve cuántas se entregaron (0 si no hay push)."""
    if not ui_state_dir:
        return 0
    try:
        from xrpbot_ui.push import PushService
    except ImportError:
        return 0
    try:
        svc = PushService(Path(ui_state_dir))
        if not svc.configured():
            return 0
        return svc.send_all(alert.title, alert.body, level=alert.level, url="/salud")
    except Exception as exc:  # noqa: BLE001
        log.warning("Push web falló: %r", exc)
        return 0


def ping(url: str | None, timeout: float = 10) -> bool:
    """Ping al servicio externo (solo si el bot está sano): su ausencia es la alarma."""
    if not url:
        return False
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception as exc:  # noqa: BLE001
        log.warning("Ping externo falló: %r", exc)
        return False


def deliver(alert: Alert, env=os.environ) -> dict:
    pushed = send_push(env.get("UI_STATE_DIR"), alert)
    tg = send_telegram(env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID"), alert)
    if not pushed and not tg:
        log.error("AVISO SIN CANAL DE ENTREGA (configura push o Telegram): %s · %s", alert.title, alert.body)
    else:
        log.info("Aviso enviado (push=%d, telegram=%s): %s", pushed, tg, alert.title)
    return {"push": pushed, "telegram": tg}


def run_watchdog(args) -> int:
    env = os.environ
    max_silence = args.max_silence_min if args.max_silence_min is not None else float(env.get("WATCHDOG_MAX_SILENCE_MIN", 5))
    remind = args.remind_min if args.remind_min is not None else float(env.get("WATCHDOG_REMIND_MIN", 30))
    wd = Watchdog(args.mode, max_silence, remind)
    path = heartbeat_path(args.state_dir, args.mode)
    if not (env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")):
        log.warning("Telegram no configurado: los avisos dependerán solo del push web")
    ping_url = env.get("WATCHDOG_PING_URL") or None
    log.info("Watchdog vigilando %s (silencio máx. %.0f min, recordatorio cada %.0f min, ping externo: %s)",
             path, max_silence, remind, "sí" if ping_url else "no")
    last_ping = 0.0
    while True:
        for alert in wd.check(read_heartbeat(path), datetime.now(timezone.utc)):
            deliver(alert)
        if ping_url and "silence" not in wd.st.active and time.monotonic() - last_ping >= 60:
            ping(ping_url)
            last_ping = time.monotonic()
        if args.once:
            return 0
        time.sleep(30)
