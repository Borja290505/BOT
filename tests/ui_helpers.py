"""Utilidades comunes de los tests de la interfaz."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from xrpbot_ui.backtest_jobs import BacktestRunner
from xrpbot_ui.providers.demo import DemoProvider
from xrpbot_ui.security import SecurityStore
from xrpbot_ui.server import create_app
from xrpbot_ui.settings import UISettings

PASSWORD = "contraseña-de-test-123"


class Clock:
    """Reloj controlable: empieza en la hora real y avanza a mano."""

    def __init__(self) -> None:
        self.now = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_env(tmp_path, *, read_only=False, scenario="normal", provider=None):
    clock = Clock()
    settings = UISettings(read_only=read_only, ui_db_path=str(tmp_path / "ui.sqlite"), state_dir=str(tmp_path))
    store = SecurityStore(settings.ui_db_path, clock=clock)
    store.set_password("admin", PASSWORD)
    prov = provider or DemoProvider(scenario, settings_path="config/settings.yaml", clock=clock)
    app = create_app(settings, prov, store, BacktestRunner.threaded())
    client = TestClient(app, base_url="http://127.0.0.1:8050")
    return client, prov, store, clock


def login(client, password=PASSWORD) -> str:
    r = client.post("/login", data={"username": "admin", "password": password}, follow_redirects=False)
    assert r.status_code == 303, r.text
    html = client.get("/").text
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def unlock(client, csrf, password=PASSWORD):
    return client.post("/api/control/unlock", json={"password": password}, headers={"X-CSRF-Token": csrf})


def command(client, csrf, ctype, params=None, word=None, cmd_id=None):
    body = {"type": ctype, "params": params or {}, "confirm_word": word}
    if cmd_id:
        body["id"] = cmd_id
    return client.post("/api/commands", json=body, headers={"X-CSRF-Token": csrf})


def wait_result(client, clock, cmd_id):
    """El bot simulado procesa los comandos en su siguiente ciclo (>= 1,2 s)."""
    clock.advance(2)
    r = client.get(f"/api/commands/{cmd_id}")
    assert r.status_code == 200
    return r.json()
