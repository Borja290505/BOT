"""Notificaciones push web (estándar Web Push + VAPID), compatibles con iOS 16.4+.

En iPhone solo funcionan si la interfaz está AÑADIDA A LA PANTALLA DE INICIO
(PWA) y se abre por HTTPS (con Tailscale: https://<máquina>.<tailnet>.ts.net).

- Claves VAPID: se generan una vez en ui-state/ (vapid_private.pem, permisos 600).
  Son claves de la interfaz, no tienen nada que ver con Kraken.
- Suscripciones: ui-state/push.sqlite (una por dispositivo). Si Apple/Google
  responden 404/410 (suscripción caducada), se borran solas.
- La usan la interfaz (avisos de eventos del bot y botón de prueba) y el watchdog.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS subs (
  endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
  created_at TEXT NOT NULL, user_agent TEXT, last_ok TEXT, failures INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

# Servicios push conocidos (Apple, Google/FCM, Mozilla, Microsoft). Evita que alguien
# registre una URL arbitraria a la que el servidor haría peticiones (SSRF).
ALLOWED_PUSH_HOSTS = ("web.push.apple.com", "fcm.googleapis.com", "updates.push.services.mozilla.com",
                      "push.services.mozilla.com", "notify.windows.com")


DEFAULT_CONTACT = "mailto:xrpbot@example.com"


class PushError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def valid_endpoint(url: str) -> bool:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == h or host.endswith("." + h) for h in ALLOWED_PUSH_HOSTS)


class PushService:
    def __init__(self, state_dir: str | Path, contact: str | None = None) -> None:
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.key_path = self.dir / "vapid_private.pem"
        self.conn = sqlite3.connect(self.dir / "push.sqlite", check_same_thread=False, isolation_level=None, timeout=10)
        self.conn.executescript(SCHEMA)
        # la interfaz guarda su contacto (ui.yaml: push_contact) y el watchdog lo reutiliza
        if contact:
            self.set("contact", contact)
        self.contact = contact or self.get("contact") or DEFAULT_CONTACT

    # ------------------------------------------------------------ claves VAPID
    def ensure_keys(self) -> None:
        if self.key_path.exists():
            return
        from py_vapid import Vapid01
        v = Vapid01()
        v.generate_keys()
        v.save_key(str(self.key_path))
        os.chmod(self.key_path, 0o600)
        log.info("Claves VAPID generadas en %s", self.key_path)

    def configured(self) -> bool:
        return self.key_path.exists()

    def public_key(self) -> str:
        """applicationServerKey (base64url) que necesita el navegador para suscribirse."""
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid01
        from py_vapid.utils import b64urlencode
        self.ensure_keys()
        v = Vapid01.from_file(str(self.key_path))
        raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return b64urlencode(raw)

    # ------------------------------------------------------------ suscripciones
    def subscribe(self, sub: dict, user_agent: str = "") -> None:
        try:
            endpoint = str(sub["endpoint"])
            p256dh, auth = str(sub["keys"]["p256dh"]), str(sub["keys"]["auth"])
        except (KeyError, TypeError):
            raise PushError("Suscripción incompleta") from None
        if not valid_endpoint(endpoint):
            raise PushError("Servicio push no reconocido")
        if len(p256dh) > 200 or len(auth) > 100 or len(endpoint) > 1000:
            raise PushError("Suscripción no válida")
        self.conn.execute(
            "INSERT INTO subs(endpoint, p256dh, auth, created_at, user_agent) VALUES(?,?,?,?,?) "
            "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth, failures=0",
            (endpoint, p256dh, auth, _now(), user_agent[:200]))

    def unsubscribe(self, endpoint: str) -> None:
        self.conn.execute("DELETE FROM subs WHERE endpoint=?", (endpoint,))

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM subs").fetchone()[0])

    def subscriptions(self) -> list[dict]:
        cur = self.conn.execute("SELECT endpoint, p256dh, auth FROM subs")
        return [{"endpoint": e, "keys": {"p256dh": p, "auth": a}} for e, p, a in cur]

    # ------------------------------------------------------------ envío
    def send_all(self, title: str, body: str, level: str = "info", url: str = "/", tag: str | None = None) -> int:
        if not self.configured():
            return 0
        from pywebpush import WebPushException, webpush
        payload = json.dumps({"title": title, "body": body, "level": level, "url": url, "tag": tag or level})
        delivered = 0
        for sub in self.subscriptions():
            try:
                webpush(subscription_info=sub, data=payload, vapid_private_key=str(self.key_path),
                        vapid_claims={"sub": self.contact}, ttl=3600,
                        headers={"Urgency": "high" if level == "critical" else "normal"}, timeout=10)
                delivered += 1
                self.conn.execute("UPDATE subs SET last_ok=?, failures=0 WHERE endpoint=?", (_now(), sub["endpoint"]))
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (404, 410):
                    log.info("Suscripción caducada, se elimina: %s", sub["endpoint"][:60])
                    self.unsubscribe(sub["endpoint"])
                else:
                    log.warning("Push fallido (%s): %s", status, exc)
                    self.conn.execute("UPDATE subs SET failures=failures+1 WHERE endpoint=?", (sub["endpoint"],))
            except Exception as exc:  # noqa: BLE001 - red, timeouts…
                log.warning("Push fallido: %r", exc)
        return delivered

    # ------------------------------------------------------------ eventos del bot ya avisados
    def get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO kv(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, value))
