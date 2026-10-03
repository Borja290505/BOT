"""WebSocket de Kraken Futures (wss://.../ws/v1).

Uso en este bot:
- Feed público "ticker": bid/ask/last/mark en tiempo real para el circuit
  breaker (spread, desfase de datos, divergencia mark/last).
- Feeds privados ("fills", "open_orders"): solo como AVISO para despertar al
  bucle principal y reconciliar al momento. La fuente de verdad sigue siendo el
  sondeo REST: si el WS se cae o cambia el formato, el bot sigue siendo correcto,
  solo reacciona unos segundos más tarde.

Reconexión con backoff exponencial y jitter. En cada reconexión se llama a
on_reconnect para forzar una reconciliación completa.

Autenticación privada (documentación de Kraken Futures WS):
  -> {"event": "challenge", "api_key": KEY}
  <- {"event": "challenge", "message": "<uuid>"}
  -> {"event": "subscribe", "feed": "fills", "api_key": KEY,
      "original_challenge": uuid, "signed_challenge": firma}
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Awaitable, Callable

import websockets

from ..models import MarketSnapshot
from .auth import sign_challenge

log = logging.getLogger(__name__)

Callback = Callable[[], Awaitable[None]]


class KrakenFuturesWS:
    def __init__(self, url: str, symbol: str, api_key: str | None = None,
                 api_secret: str | None = None,
                 on_private_event: Callback | None = None,
                 on_reconnect: Callback | None = None) -> None:
        self.url, self.symbol = url, symbol.upper()
        self._key, self._secret = api_key, api_secret
        self._on_private = on_private_event
        self._on_reconnect = on_reconnect
        self.snapshot: MarketSnapshot | None = None
        self.last_msg_ts: float = 0.0
        self._stop = asyncio.Event()
        self._connected_once = False

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=20,
                                              close_timeout=5) as ws:
                    attempt = 0
                    await self._subscribe(ws)
                    if self._connected_once and self._on_reconnect:
                        await self._on_reconnect()
                    self._connected_once = True
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        await self._handle(ws, raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - cualquier fallo => reconectar
                attempt += 1
                delay = min(60.0, 2 ** min(attempt, 6)) + random.uniform(0, 1)
                log.warning("WebSocket caído (%r). Reconexión en %.1fs", exc, delay)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

    async def _subscribe(self, ws) -> None:
        await ws.send(json.dumps({"event": "subscribe", "feed": "ticker", "product_ids": [self.symbol]}))
        if self._key and self._secret:
            await ws.send(json.dumps({"event": "challenge", "api_key": self._key}))

    async def _handle(self, ws, raw: str | bytes) -> None:
        self.last_msg_ts = time.time()
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        event, feed = msg.get("event"), msg.get("feed")
        if event == "challenge" and "message" in msg and self._secret:
            signed = sign_challenge(self._secret, msg["message"])
            for private_feed in ("fills", "open_orders"):
                await ws.send(json.dumps({
                    "event": "subscribe", "feed": private_feed, "api_key": self._key,
                    "original_challenge": msg["message"], "signed_challenge": signed,
                }))
        elif event in ("alert", "error"):
            log.error("Mensaje de error del WS: %s", msg)
        elif feed == "ticker" and str(msg.get("product_id", "")).upper() == self.symbol:
            self.snapshot = parse_ticker(msg)
        elif feed in ("fills", "open_orders") and self._on_private:
            await self._on_private()


def parse_ticker(msg: dict) -> MarketSnapshot | None:
    """Mensaje 'ticker' del WS -> MarketSnapshot. Devuelve None si faltan campos."""
    try:
        ts_ms = msg.get("time")
        return MarketSnapshot(
            symbol=str(msg["product_id"]).upper(),
            bid=float(msg["bid"]), ask=float(msg["ask"]),
            last=float(msg.get("last", 0) or 0),
            mark=float(msg.get("markPrice", 0) or 0),
            ts=float(ts_ms) / 1000 if ts_ms else time.time(),
            funding_rate_rel=float(msg["relative_funding_rate"]) if msg.get("relative_funding_rate") is not None else None,
        )
    except (KeyError, TypeError, ValueError):
        return None
