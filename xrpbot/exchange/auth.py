"""Firma de peticiones de Kraken Futures (REST v3 y challenge del WebSocket).

Algoritmo REST (documentación de Kraken Futures, "Authentication"):
    1. message = postData + nonce + endpointPath
       - postData: los parámetros url-encoded (cuerpo del POST o query del GET)
       - endpointPath: ruta SIN el prefijo "/derivatives", p. ej. "/api/v3/sendorder"
    2. sha = SHA-256(message)
    3. Authent = Base64( HMAC-SHA-512( Base64Decode(api_secret), sha ) )

WebSocket privado: se pide un "challenge" (UUID) y se firma igual, pero
message = challenge, sin nonce ni ruta.

Nota: este algoritmo es el documentado y el que usan los SDK públicos. No se
ha podido contrastar contra la API desde el entorno de desarrollo (red
bloqueada); `python -m xrpbot.cli check` lo valida contra la demo.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import threading
import time

DERIVATIVES_PREFIX = "/derivatives"


def endpoint_path_for_signing(path: str) -> str:
    """'/derivatives/api/v3/sendorder' -> '/api/v3/sendorder'."""
    if path.startswith(DERIVATIVES_PREFIX):
        return path[len(DERIVATIVES_PREFIX):]
    return path


def sign_rest(api_secret: str, endpoint_path: str, post_data: str, nonce: str = "") -> str:
    message = (post_data + nonce + endpoint_path_for_signing(endpoint_path)).encode("utf-8")
    sha = hashlib.sha256(message).digest()
    mac = hmac.new(base64.b64decode(api_secret), sha, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


def sign_challenge(api_secret: str, challenge: str) -> str:
    sha = hashlib.sha256(challenge.encode("utf-8")).digest()
    mac = hmac.new(base64.b64decode(api_secret), sha, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


class NonceGenerator:
    """Nonce estrictamente creciente (ms + contador) aunque el reloj retroceda."""

    def __init__(self) -> None:
        self._last = 0
        self._lock = threading.Lock()

    def next(self) -> str:
        with self._lock:
            now = int(time.time() * 1000)
            self._last = max(now, self._last + 1)
            return str(self._last)
