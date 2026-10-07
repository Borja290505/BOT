"""Seguridad de la interfaz: login, sesiones, CSRF, cabeceras, aislamiento del bot y contrato."""
import json
import sqlite3
import subprocess
import sys

import jsonschema
import pytest

from ui_helpers import PASSWORD, login, make_env
from xrpbot_ui.contract import (CommandError, check_confirm_word, derive_state, effective_value,
                                validate_command_shape, validate_reduction)
from xrpbot_ui.settings import UISettings, UISettingsError


def test_pages_and_api_require_session(tmp_path):
    client, *_ = make_env(tmp_path)
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    for url in ("/api/state", "/api/trades", "/api/events", "/api/audit", "/api/candles", "/api/trades.csv"):
        assert client.get(url).status_code == 401, url


def test_login_lockout_after_failed_attempts(tmp_path):
    client, prov, store, clock = make_env(tmp_path)
    for _ in range(5):
        r = client.post("/login", data={"username": "admin", "password": "mala-contraseña-x"}, follow_redirects=False)
        assert r.status_code == 401
    r = client.post("/login", data={"username": "admin", "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 429, "tras 5 fallos ni la contraseña correcta entra"
    clock.advance(15 * 60 + 1)
    assert client.post("/login", data={"username": "admin", "password": PASSWORD}, follow_redirects=False).status_code == 303
    assert ("login", "locked") in [(a["action"], a["result"]) for a in store.audit_log()]


def test_session_expires_when_idle(tmp_path):
    client, prov, store, clock = make_env(tmp_path)
    login(client)
    assert client.get("/api/state").status_code == 200
    clock.advance(31 * 60)
    assert client.get("/api/state").status_code == 401


def test_cookie_flags(tmp_path):
    client, *_ = make_env(tmp_path)
    r = client.post("/login", data={"username": "admin", "password": PASSWORD}, follow_redirects=False)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_password_policy(tmp_path):
    _, _, store, _ = make_env(tmp_path)
    with pytest.raises(ValueError):
        store.set_password("admin", "corta")


def test_csrf_and_origin_required_for_posts(tmp_path):
    client, *_ = make_env(tmp_path)
    csrf = login(client)
    assert client.post("/api/control/unlock", json={"password": PASSWORD}).status_code == 403
    assert client.post("/api/control/unlock", json={"password": PASSWORD}, headers={"X-CSRF-Token": "otro"}).status_code == 403
    r = client.post("/api/control/unlock", json={"password": PASSWORD},
                    headers={"X-CSRF-Token": csrf, "Origin": "https://atacante.example"})
    assert r.status_code == 403
    assert client.post("/api/control/unlock", json={"password": PASSWORD}, headers={"X-CSRF-Token": csrf}).status_code == 200


def test_security_headers(tmp_path):
    client, *_ = make_env(tmp_path)
    login(client)
    r = client.get("/")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-eval" not in csp
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/state").headers["cache-control"] == "no-store"


def test_bot_messages_cannot_inject_html(tmp_path):
    client, prov, store, clock = make_env(tmp_path)
    prov._event("critical", "x", "</script><script>alert(1)</script>")
    login(client)
    html = client.get("/").text
    assert "</script><script>alert(1)" not in html
    assert "<\\/script><script>alert(1)<\\/script>" in html


def test_only_loopback_allowed():
    for host in ("0.0.0.0", "192.168.1.10", "100.64.0.1"):
        with pytest.raises(UISettingsError):
            UISettings(host=host).validate()
    UISettings(host="127.0.0.1").validate()


def test_ui_never_imports_exchange_client_nor_loads_env(tmp_path):
    """El proceso de la interfaz no importa el cliente de Kraken ni carga .env."""
    (tmp_path / ".env").write_text("KRAKEN_LIVE_API_KEY=no-debe-leerse\n")
    code = ("import sys, os; import xrpbot_ui.server, xrpbot_ui.__main__, xrpbot_ui.providers.demo, "
            "xrpbot_ui.providers.botdb, xrpbot_ui.backtest_jobs; "
            "bad=[m for m in sys.modules if m in ('xrpbot.exchange.rest','xrpbot.exchange.ws','xrpbot.live.runner')]; "
            "print(bad, os.environ.get('KRAKEN_LIVE_API_KEY'))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path,
                         env={"PYTHONPATH": str(__import__("pathlib").Path.cwd()), "PATH": ""}).stdout.strip()
    assert out == "[] None", out


def test_bot_database_is_opened_read_only(tmp_path):
    from xrpbot.state.db import Database
    from xrpbot_ui.providers.botdb import BotDBProvider
    db_path = tmp_path / "xrpbot-sim.sqlite"
    Database(db_path).close()
    prov = BotDBProvider(db_path, "sim", "config/settings.yaml")
    conn = prov._conn()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO kv(key, value) VALUES('x', 'y')")


def test_bot_provider_reads_existing_tables(tmp_path):
    from xrpbot.execution.oco import TradeState
    from xrpbot.models import Side
    from xrpbot.state.db import Database
    from xrpbot_ui.providers.botdb import BotDBProvider
    db = Database(tmp_path / "xrpbot-sim.sqlite")
    st = TradeState(trade_id="xrpusd-20261003T1000-L", side=Side.LONG, status="closed", target_size=7,
                    stop_price=1.4, tp_price=1.6, filled_size=7, exited_size=7, entry_avg=1.45,
                    realized_pnl=0.5, fees=0.01, risk_usd=0.35, opened_ts=1.7e9, closed_ts=1.7e9 + 3600, exit_reason="take_profit")
    db.save_trade(st)
    db.set("dl.halted", "1"); db.set("dl.reason", "KILL SWITCH manual")
    db.log_event("warning", "kill", "Kill switch activado desde la CLI")
    db.close()
    prov = BotDBProvider(tmp_path / "xrpbot-sim.sqlite", "sim", "config/settings.yaml")
    snap = prov.snapshot()
    assert snap["bot"]["state"] == "halted" and snap["account"]["pnl_total_usd"] == pytest.approx(0.49)
    trades = prov.trades()
    assert trades[0]["exit_reason"] == "take_profit" and trades[0]["r_multiple"] == pytest.approx(0.49 / 0.35)
    assert prov.events(level="warning")[0]["kind"] == "kill"
    schema = json.load(open("docs/ui/contrato/snapshot.schema.json"))
    jsonschema.Draft202012Validator(schema).validate(json.loads(json.dumps(snap)))


# ------------------------------------------------------------------ contrato
def test_demo_snapshots_follow_contract_schema(tmp_path):
    from xrpbot_ui.providers.demo import SCENARIOS, DemoProvider
    schema = json.load(open("docs/ui/contrato/snapshot.schema.json"))
    v = jsonschema.Draft202012Validator(schema)
    p = DemoProvider()
    for sc in SCENARIOS:
        p.set_scenario(sc)
        errors = list(v.iter_errors(json.loads(json.dumps(p.snapshot()))))
        assert not errors, (sc, [e.message for e in errors[:3]])


def test_contract_rules():
    with pytest.raises(CommandError):
        validate_command_shape("set_mode_live", {})
    with pytest.raises(CommandError):
        check_confirm_word("kill", "kill")
    check_confirm_word("kill", "KILL")
    check_confirm_word("pause", None)
    with pytest.raises(CommandError):
        validate_reduction("risk.max_leverage", 3.0, 2.0)
    with pytest.raises(CommandError):
        validate_reduction("risk.max_leverage", 0.01, 2.0)         # por debajo del mínimo razonable
    validate_reduction("risk.max_leverage", 1.0, 2.0)
    assert effective_value("risk.max_leverage", 2.0, 1.0) == 1.0
    assert effective_value("risk.max_leverage", 1.0, 2.0) == 1.0   # un override nunca sube
    assert effective_value("circuit_breaker.min_book_depth_multiple", 3.0, 5.0) == 5.0
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    assert derive_state({"state": "active", "heartbeat_at": (now - timedelta(seconds=3)).isoformat()}, now) == "active"
    assert derive_state({"state": "active", "heartbeat_at": (now - timedelta(seconds=20)).isoformat()}, now) == "down"
    assert derive_state({"state": "active", "heartbeat_at": None}, now) == "down"


def test_backtest_api(tmp_path, monkeypatch):
    import time
    from xrpbot_ui.providers.demo import synthetic_candles
    import pandas as pd
    client, prov, store, clock = make_env(tmp_path)
    csrf = login(client)
    short = synthetic_candles(3000, pd.Timestamp.now(tz="UTC").floor("1h"), 3, start_price=0.6)
    monkeypatch.setattr(prov, "backtest_data", lambda: (short, None, None, "sintético"))
    h = {"X-CSRF-Token": csrf}
    assert client.post("/api/backtest", json={"entry_lookback": 10, "exit_lookback": 20}, headers=h).status_code == 422
    assert client.post("/api/backtest", json={"atr_stop_mult": 99}, headers=h).status_code == 422
    job = client.post("/api/backtest", json={"entry_lookback": 20, "exit_lookback": 10}, headers=h).json()["job_id"]
    for _ in range(120):
        r = client.get(f"/api/backtest/{job}").json()
        if r["status"] != "running":
            break
        time.sleep(0.25)
    assert r["status"] == "done", r
    res = r["result"]
    assert "pasado no garantiza" in res["disclaimer"]
    assert {"rentabilidad_total", "max_drawdown", "sharpe", "sortino", "profit_factor", "win_rate",
            "expectativa_usd", "num_operaciones"} <= set(res["metrics"])
    assert res["funding"]["available"] is False and "DESHABILITADA" in res["funding"]["note"]
