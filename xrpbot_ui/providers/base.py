"""Interfaz común de los proveedores de datos de la interfaz.

- DemoProvider: datos simulados con escenarios (no necesita el bot).
- BotDBProvider: lee las bases de datos del bot en modo solo lectura.

Todos devuelven estructuras con la forma del contrato (docs/ui/contrato.md).
"""
from __future__ import annotations

from typing import Protocol

from ..contract import Command


class Provider(Protocol):
    name: str

    def capabilities(self) -> dict: ...
    def snapshot(self) -> dict: ...
    def candles(self, limit: int = 500) -> dict: ...
    def equity(self) -> dict: ...
    def trades(self, *, side: str | None = None, result: str | None = None,
               reason: str | None = None, since: str | None = None, until: str | None = None) -> list[dict]: ...
    def trade(self, trade_id: str) -> dict | None: ...
    def events(self, *, level: str | None = None, kind: str | None = None, q: str | None = None,
               limit: int = 300) -> list[dict]: ...
    def alerts(self, limit: int = 200) -> list[dict]: ...
    def config_view(self) -> dict: ...
    def submit(self, cmd: Command) -> Command: ...
    def command(self, cmd_id: str) -> Command | None: ...
    def backtest_data(self): ...


LEVELS = ("debug", "info", "warning", "error", "critical")


def level_at_least(level: str, minimum: str | None) -> bool:
    if not minimum:
        return True
    try:
        return LEVELS.index(level) >= LEVELS.index(minimum)
    except ValueError:
        return True


def filter_trades(rows: list[dict], *, side=None, result=None, reason=None, since=None, until=None) -> list[dict]:
    out = []
    for t in rows:
        if side and t["side"] != side:
            continue
        if result == "win" and not t["net_pnl"] > 0:
            continue
        if result == "loss" and not t["net_pnl"] <= 0:
            continue
        if reason and t["exit_reason"] != reason:
            continue
        if since and t["opened_at"] < since:
            continue
        if until and t["opened_at"] > until + "T23:59:59":
            continue
        out.append(t)
    return out
