"""Alertas por Telegram (solo envío; el bot no acepta comandos remotos).

- No bloquea: los mensajes van a una cola y se envían en segundo plano.
- Nunca lanza excepciones al bucle principal: si Telegram falla, se registra
  en el log y el bot sigue.
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp

log = logging.getLogger(__name__)

ICON = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "critical": "🛑"}


class TelegramNotifier:
    def __init__(self, token: str | None, chat_id: str | None, prefix: str = "") -> None:
        self.enabled = bool(token and chat_id)
        self._url = f"https://api.telegram.org/bot{token}/sendMessage" if token else ""
        self._chat = chat_id
        self._prefix = prefix
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self._task: asyncio.Task | None = None

    def __call__(self, level: str, message: str) -> None:
        if not self.enabled:
            return
        text = f"{ICON.get(level, '')} {self._prefix}{message}"
        try:
            self._queue.put_nowait(text[:4000])
        except asyncio.QueueFull:
            log.warning("Cola de Telegram llena: mensaje descartado")

    def start(self) -> None:
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._task:
            await asyncio.sleep(0)  # deja salir lo pendiente
            self._task.cancel()

    async def send_now(self, level: str, message: str) -> None:
        if not self.enabled:
            return
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            await self._post(s, f"{ICON.get(level, '')} {self._prefix}{message}")

    async def _post(self, session: aiohttp.ClientSession, text: str) -> None:
        try:
            async with session.post(self._url, json={"chat_id": self._chat, "text": text}) as r:
                if r.status != 200:
                    log.warning("Telegram respondió %s", r.status)
        except Exception as exc:  # noqa: BLE001
            log.warning("Fallo enviando a Telegram: %r", exc)

    async def _worker(self) -> None:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            while True:
                text = await self._queue.get()
                await self._post(s, text)
                await asyncio.sleep(0.5)  # límite de Telegram ~1 msg/s por chat
