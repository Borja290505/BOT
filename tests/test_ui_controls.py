"""Acciones críticas de la interfaz: kill switch, cierre de posición, rearme,
reducción de parámetros, solo lectura, idempotencia y caducidad."""
import uuid

import pytest

from ui_helpers import command, login, make_env, unlock, wait_result
from xrpbot_ui.contract import Command


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)


def audit_actions(store):
    return [(a["action"], a["result"]) for a in store.audit_log()]


# ------------------------------------------------------------------ solo lectura
def test_read_only_blocks_every_control(tmp_path):
    client, prov, store, clock = make_env(tmp_path, read_only=True)
    csrf = login(client)
    assert unlock(client, csrf).status_code == 403
    for ctype, word in (("kill", "KILL"), ("pause", None), ("close_position", "CERRAR"), ("rearm", None)):
        r = command(client, csrf, ctype, word=word)
        assert r.status_code == 403
        assert "SOLO LECTURA" in r.json()["error"]
    assert prov.commands == {}                                   # nada llegó al "bot"
    assert ("command", "rejected: La interfaz está en SOLO LECTURA") in audit_actions(store)
    html = client.get("/").text
    assert 'id="controlbar"' not in html and "SOLO LECTURA" in html  # ni siquiera se muestran los botones


def test_command_requires_unlocked_control(env):
    client, prov, store, clock = env
    csrf = login(client)
    r = command(client, csrf, "kill", word="KILL")
    assert r.status_code == 403 and "bloqueado" in r.json()["error"]
    assert prov.commands == {}


def test_unlock_needs_password_and_expires(env):
    client, prov, store, clock = env
    csrf = login(client)
    assert unlock(client, csrf, "incorrecta-123456").status_code == 403
    r = unlock(client, csrf)
    assert r.status_code == 200 and r.json()["control_seconds_left"] == 5 * 60
    clock.advance(5 * 60 + 1)                                    # pasan los 5 minutos
    assert command(client, csrf, "pause").status_code == 403
    assert ("control_unlock", "fail") in audit_actions(store) and ("control_unlock", "ok") in audit_actions(store)


# ------------------------------------------------------------------ kill switch
def test_kill_requires_exact_word(env):
    client, prov, store, clock = env
    csrf = login(client)
    unlock(client, csrf)
    for word in (None, "", "kill", "KILL ME", "CERRAR"):
        r = command(client, csrf, "kill", word=word)
        assert r.status_code == 422, word
    assert prov.commands == {}


def test_kill_switch_closes_everything_and_halts(env):
    client, prov, store, clock = env
    csrf = login(client)
    unlock(client, csrf)
    assert prov.position is not None and prov.orders
    r = command(client, csrf, "kill", word="KILL")
    assert r.status_code == 202
    res = wait_result(client, clock, r.json()["id"])
    assert res["status"] == "done"
    assert prov.position is None and prov.orders == []
    snap = client.get("/api/state").json()["snapshot"]
    assert snap["bot"]["state"] == "halted" and "KILL" in snap["bot"]["reason"]
    actions = audit_actions(store)
    assert ("command", "submitted") in actions
    assert any(a == "command_result" and r.startswith("done") for a, r in actions)


# ------------------------------------------------------------------ cerrar posición
def test_close_position_closes_and_pauses(env):
    client, prov, store, clock = env
    csrf = login(client)
    unlock(client, csrf)
    assert command(client, csrf, "close_position").status_code == 422          # sin palabra
    r = command(client, csrf, "close_position", word="CERRAR")
    res = wait_result(client, clock, r.json()["id"])
    assert res["status"] == "done" and "Reanudar" in res["result"]
    assert prov.position is None and prov.state == "paused"
    # sin posición: el bot lo rechaza
    r = command(client, csrf, "close_position", word="CERRAR")
    assert wait_result(client, clock, r.json()["id"])["status"] == "rejected"
    # reanudar
    r = command(client, csrf, "resume")
    assert wait_result(client, clock, r.json()["id"])["status"] == "done"
    assert prov.state == "active"


# ------------------------------------------------------------------ rearme
def test_rearm_same_day_daily_limit_is_rejected(tmp_path):
    client, prov, store, clock = make_env(tmp_path, scenario="limite_diario")
    csrf = login(client)
    unlock(client, csrf)
    r = command(client, csrf, "rearm")
    res = wait_result(client, clock, r.json()["id"])
    assert res["status"] == "rejected" and "00:00 UTC" in res["result"]
    assert prov.state == "halted"
    # resume tampoco saca de una parada
    r = command(client, csrf, "resume")
    assert wait_result(client, clock, r.json()["id"])["status"] == "rejected"


def test_rearm_after_kill_and_when_nothing_to_rearm(env):
    client, prov, store, clock = env
    csrf = login(client)
    unlock(client, csrf)
    r = command(client, csrf, "rearm")
    assert wait_result(client, clock, r.json()["id"])["status"] == "rejected"   # no hay parada
    wait_result(client, clock, command(client, csrf, "kill", word="KILL").json()["id"])
    r = command(client, csrf, "rearm")
    assert wait_result(client, clock, r.json()["id"])["status"] == "done"
    assert prov.state == "active"


def test_rearm_frozen_with_unknown_position_rejected(tmp_path):
    client, prov, store, clock = make_env(tmp_path, scenario="discrepancia")
    csrf = login(client)
    unlock(client, csrf)
    r = command(client, csrf, "rearm")
    res = wait_result(client, clock, r.json()["id"])
    assert res["status"] == "rejected" and "desconocida" in res["result"]


# ------------------------------------------------------------------ reducir parámetros
def test_reduce_param_only_lowers_risk(env):
    client, prov, store, clock = env
    csrf = login(client)
    unlock(client, csrf)
    r = command(client, csrf, "reduce_param", {"name": "risk.risk_per_trade", "value": 0.02})
    assert r.status_code == 422 and "solo se puede reducir" in r.json()["error"]
    r = command(client, csrf, "reduce_param", {"name": "risk.risk_per_trade", "value": 0.01})   # igual: no es reducir
    assert r.status_code == 422
    r = command(client, csrf, "reduce_param", {"name": "api.key", "value": 1})
    assert r.status_code == 422
    r = command(client, csrf, "reduce_param", {"name": "risk.risk_per_trade", "value": 0.005})
    assert wait_result(client, clock, r.json()["id"])["status"] == "done"
    cfg = client.get("/api/config").json()
    p = next(x for x in cfg["params"] if x["name"] == "risk.risk_per_trade")
    assert p["file"] == 0.01 and p["override"] == 0.005 and p["effective"] == 0.005
    # después de reducir, volver a subir sigue prohibido
    assert command(client, csrf, "reduce_param", {"name": "risk.risk_per_trade", "value": 0.008}).status_code == 422


def test_breaker_thresholds_can_only_be_tightened(env):
    client, prov, store, clock = env
    csrf = login(client)
    unlock(client, csrf)
    assert command(client, csrf, "reduce_param", {"name": "circuit_breaker.min_book_depth_multiple", "value": 2}).status_code == 422
    r = command(client, csrf, "reduce_param", {"name": "circuit_breaker.min_book_depth_multiple", "value": 5})
    assert wait_result(client, clock, r.json()["id"])["status"] == "done"
    assert command(client, csrf, "reduce_param", {"name": "circuit_breaker.max_spread_bps", "value": 30}).status_code == 422


def test_leverage_reduction_below_open_position_blocks_entries_without_closing(env):
    client, prov, store, clock = env
    csrf = login(client)
    unlock(client, csrf)
    lev = client.get("/api/state").json()["snapshot"]["position"]["effective_leverage"]
    r = command(client, csrf, "reduce_param", {"name": "risk.max_leverage", "value": round(lev / 2, 2)})
    assert wait_result(client, clock, r.json()["id"])["status"] == "done"
    snap = client.get("/api/state").json()["snapshot"]
    assert snap["position"] is not None                          # NO la cierra
    assert snap["risk"]["entries_blocked"] and any("supera" in x for x in snap["risk"]["block_reasons"])


# ------------------------------------------------------------------ robustez
def test_unknown_command_type_rejected(env):
    client, prov, store, clock = env
    csrf = login(client)
    unlock(client, csrf)
    for t in ("set_mode_live", "withdraw", "increase_risk", ""):
        assert command(client, csrf, t).status_code == 422
    assert command(client, csrf, "pause", params={"x": 1}).status_code == 422


def test_same_command_id_is_idempotent(env):
    client, prov, store, clock = env
    csrf = login(client)
    unlock(client, csrf)
    cid = str(uuid.uuid4())
    r1 = command(client, csrf, "pause", cmd_id=cid)
    r2 = command(client, csrf, "pause", cmd_id=cid)
    assert r1.json()["id"] == r2.json()["id"] == cid
    assert len(prov.commands) == 1


def test_bot_down_rejects_commands(tmp_path):
    client, prov, store, clock = make_env(tmp_path, scenario="caido")
    csrf = login(client)
    unlock(client, csrf)
    st = client.get("/api/state").json()
    assert st["snapshot"]["bot"]["effective_state"] == "down"
    r = command(client, csrf, "kill", word="KILL")
    assert r.status_code == 409 and "no responde" in r.json()["error"]


def test_expired_command_is_never_executed(env):
    client, prov, store, clock = env
    cmd = Command("pause")
    prov.submit(cmd)
    clock.advance(61)                  # el "bot" no lo ha recogido en 60 s
    assert prov.command(cmd.id).status == "expired"
    assert prov.state == "active"


def test_bot_source_has_no_control(tmp_path):
    from xrpbot_ui.providers.botdb import BotDBProvider
    prov = BotDBProvider(tmp_path / "no-existe.sqlite", "sim", "config/settings.yaml")
    client, _, store, clock = make_env(tmp_path, provider=prov)
    csrf = login(client)
    unlock(client, csrf)
    r = command(client, csrf, "kill", word="KILL")
    assert r.status_code == 409 and "contrato" in r.json()["error"]
