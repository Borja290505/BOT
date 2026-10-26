"""Push web: suscripciones (anti-SSRF), envío, limpieza de caducadas, API y reenvío de eventos del bot."""
import asyncio

import pytest

from ui_helpers import login, make_env
from xrpbot_ui.providers.botdb import BotDBProvider
from xrpbot_ui.push import PushError, PushService, valid_endpoint
from xrpbot_ui.server import push_bot_events
from xrpbot_ui.settings import UISettings

APPLE = "https://web.push.apple.com/QGd3abc"
SUB = {"endpoint": APPLE, "keys": {"p256dh": "BNc" + "a" * 84, "auth": "x" * 22}}


def test_endpoint_allowlist_blocks_ssrf():
    assert valid_endpoint(APPLE)
    assert valid_endpoint("https://fcm.googleapis.com/fcm/send/abc")
    for bad in ("http://web.push.apple.com/x", "https://127.0.0.1/x", "https://evil.com/web.push.apple.com",
                "https://web.push.apple.com.evil.com/x", "https://localhost:8050/api/commands"):
        assert not valid_endpoint(bad), bad

def test_subscribe_validates_and_upserts(tmp_path):
    svc = PushService(tmp_path)
    with pytest.raises(PushError):
        svc.subscribe({"endpoint": "https://evil.com/x", "keys": SUB["keys"]})
    with pytest.raises(PushError):
        svc.subscribe({"endpoint": APPLE})
    svc.subscribe(SUB)
    svc.subscribe(SUB)
    assert svc.count() == 1
    svc.unsubscribe(APPLE)
    assert svc.count() == 0


def test_vapid_keys_generated_once_with_private_permissions(tmp_path):
    svc = PushService(tmp_path)
    assert not svc.configured()
    k1 = svc.public_key()
    assert svc.configured() and len(k1) == 87                         # punto P-256 sin comprimir en base64url
    assert (tmp_path / "vapid_private.pem").stat().st_mode & 0o077 == 0
    assert PushService(tmp_path).public_key() == k1


def test_send_all_removes_expired_subscriptions(tmp_path, monkeypatch):
    import pywebpush
    svc = PushService(tmp_path)
    svc.ensure_keys()
    svc.subscribe(SUB)
    svc.subscribe({"endpoint": "https://fcm.googleapis.com/fcm/send/old", "keys": SUB["keys"]})
    calls = []

    class Resp:
        status_code = 410

    def fake(subscription_info, data, **kw):
        calls.append((subscription_info["endpoint"], data, kw["headers"]["Urgency"]))
        if "old" in subscription_info["endpoint"]:
            raise pywebpush.WebPushException("gone", response=Resp())

    monkeypatch.setattr(pywebpush, "webpush", fake)
    assert svc.send_all("t", "b", level="critical") == 1
    assert svc.count() == 1 and all(c[2] == "high" for c in calls)


def test_push_api_requires_session_and_csrf(tmp_path, monkeypatch):
    client, *_ = make_env(tmp_path)
    assert client.get("/api/push/key").status_code == 401
    csrf = login(client)
    r = client.get("/api/push/key")
    assert r.status_code == 200 and len(r.json()["public_key"]) == 87
    assert client.post("/api/push/subscribe", json=SUB).status_code == 403          # sin CSRF
    bad = client.post("/api/push/subscribe", json={"endpoint": "https://10.0.0.1/x", "keys": SUB["keys"]},
                      headers={"X-CSRF-Token": csrf})
    assert bad.status_code == 422
    ok = client.post("/api/push/subscribe", json=SUB, headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 200 and ok.json()["subscriptions"] == 1
    monkeypatch.setattr("pywebpush.webpush", lambda **kw: None)
    assert client.post("/api/push/test", headers={"X-CSRF-Token": csrf}).json()["delivered"] == 1
    assert client.post("/api/push/unsubscribe", json={"endpoint": APPLE},
                       headers={"X-CSRF-Token": csrf}).json()["subscriptions"] == 0


def test_pwa_files_are_public_and_have_right_headers(tmp_path):
    client, *_ = make_env(tmp_path)
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")
    assert m.json()["display"] == "standalone"
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["service-worker-allowed"] == "/" and "no-cache" in sw.headers["cache-control"]
    assert "/api/" in sw.text                                                   # nunca cachea la API
    assert client.get("/apple-touch-icon.png").headers["content-type"] == "image/png"
    assert client.get("/healthz").json() == {"ok": True}
    assert "worker-src 'self'" in client.get("/healthz").headers["content-security-policy"]
    html = client.get("/login").text
    assert 'rel="manifest"' in html and "apple-mobile-web-app-capable" in html and "viewport-fit=cover" in html


def _bot_db(path):
    from xrpbot.state.db import Database
    db = Database(str(path))
    db.log_event("info", "startup", "arranque")
    return db


def test_bot_events_forwarded_once(tmp_path, monkeypatch):
    db = _bot_db(tmp_path / "xrpbot-sim.sqlite")
    prov = BotDBProvider(tmp_path / "xrpbot-sim.sqlite", "sim", "config/settings.yaml", data_dir=tmp_path)
    svc = PushService(tmp_path / "ui")
    svc.subscribe(SUB)
    sent = []
    monkeypatch.setattr(svc, "send_all", lambda title, body, level, url, tag: sent.append((title, body, level)) or 1)
    settings = UISettings(state_dir=str(tmp_path))
    run = lambda: asyncio.run(push_bot_events(prov, svc, settings, once=True))  # noqa: E731
    run()                                                    # primer arranque: no avisa del pasado
    assert sent == []
    db.log_event("info", "loop", "ruido")                    # info: no se avisa
    db.log_event("critical", "freeze", "posición desconocida")
    db.log_event("info", "entry", "LONG 100 @ 0,5")
    run()
    assert [(t, lvl) for t, _, lvl in sent] == [("[SIM] CRÍTICO", "critical"), ("[SIM] Entrada", "info")]
    run()
    assert len(sent) == 2                                    # no se repiten


def test_botdb_reads_heartbeat(tmp_path):
    from xrpbot.ops.heartbeat import write_heartbeat
    from xrpbot_ui.contract import derive_state
    _bot_db(tmp_path / "xrpbot-sim.sqlite")
    prov = BotDBProvider(tmp_path / "xrpbot-sim.sqlite", "sim", "config/settings.yaml", data_dir=tmp_path)
    assert prov.capabilities()["live_status"] is False
    write_heartbeat(tmp_path / "heartbeat-sim.json", mode="sim", state="active", started_at=None, last_ok_at=None)
    assert prov.capabilities()["live_status"] is True
    bot = prov.snapshot()["bot"]
    assert bot["heartbeat_at"] and derive_state(bot) == "active"
