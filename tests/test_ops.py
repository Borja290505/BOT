"""Operación 24/7: latido, healthcheck, copias de seguridad y watchdog."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from xrpbot.ops import __main__ as ops
from xrpbot.ops.backup import backup_file, prune, run_backup
from xrpbot.ops.heartbeat import age_seconds, heartbeat_path, read_heartbeat, write_heartbeat
from xrpbot.ops.watchdog import Alert, Watchdog, deliver

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def hb(ts, state="active", last_ok=None, started=None, reason=None):
    return {"ts": ts.isoformat(), "state": state, "reason": reason,
            "last_ok_at": (last_ok or ts).isoformat(), "started_at": (started or T0 - timedelta(hours=1)).isoformat()}


# ------------------------------------------------------------------ latido y healthcheck
def test_heartbeat_roundtrip_and_atomic_write(tmp_path):
    p = heartbeat_path(tmp_path, "sim")
    assert p.name == "heartbeat-sim.json"
    write_heartbeat(p, mode="sim", state="active")
    d = read_heartbeat(p)
    assert d["mode"] == "sim" and d["state"] == "active" and "pid" in d and "version" in d
    assert age_seconds(d["ts"]) < 5
    assert not list(tmp_path.glob(".hb-*")), "no quedan temporales"
    p.write_text("{roto")
    assert read_heartbeat(p) is None


def test_healthcheck_exit_codes(tmp_path):
    assert ops.healthcheck(str(tmp_path), "sim", 120) == 1           # sin fichero
    p = heartbeat_path(tmp_path, "sim")
    write_heartbeat(p, state="active")
    assert ops.healthcheck(str(tmp_path), "sim", 120) == 0
    old = json.loads(p.read_text())
    old["ts"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    p.write_text(json.dumps(old))
    assert ops.healthcheck(str(tmp_path), "sim", 120) == 1           # latido viejo = no sano
    assert ops.main(["healthcheck", "--mode", "sim", "--state-dir", str(tmp_path)]) == 1


# ------------------------------------------------------------------ copias de seguridad
def _db(path, rows=3):
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE t (x INTEGER)")
        c.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])


def test_backup_copies_consistent_db_and_prunes(tmp_path):
    src = tmp_path / "state"
    src.mkdir()
    _db(src / "xrpbot-sim.sqlite")
    (src / "heartbeat-sim.json").write_text("{}")                     # no es sqlite: no se copia
    dest = tmp_path / "backups"
    old = dest / "2026-10-01"
    old.mkdir(parents=True)
    keep = dest / "2026-10-20"
    keep.mkdir()
    done = run_backup([src], dest, keep_days=14, now=T0)
    assert [p.name for p in done] == ["xrpbot-sim.sqlite"]
    with sqlite3.connect(done[0]) as c:
        assert c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
    assert not old.exists() and keep.exists()
    assert prune(dest, 14, T0) == []


def test_backup_while_writer_has_open_transaction(tmp_path):
    src, dst = tmp_path / "a.sqlite", tmp_path / "out" / "a.sqlite"
    _db(src)
    writer = sqlite3.connect(src)
    writer.execute("INSERT INTO t VALUES (99)")                       # transacción sin confirmar
    backup_file(src, dst)
    writer.rollback()
    writer.close()
    with sqlite3.connect(dst) as c:
        assert c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3  # solo lo confirmado


# ------------------------------------------------------------------ watchdog
def test_watchdog_silent_when_healthy():
    wd = Watchdog("sim", max_silence_min=5)
    assert wd.check(hb(T0), T0 + timedelta(seconds=30)) == []


def test_watchdog_alerts_on_silence_reminds_and_recovers():
    wd = Watchdog("sim", max_silence_min=5, remind_min=30)
    beat = hb(T0)
    a = wd.check(beat, T0 + timedelta(minutes=6))
    assert [(x.level, x.key) for x in a] == [("critical", "silence")] and "SIN LATIDO" in a[0].title
    assert wd.check(beat, T0 + timedelta(minutes=10)) == []           # no repite cada 30 s
    r = wd.check(beat, T0 + timedelta(minutes=37))
    assert r and r[0].title.startswith("Recordatorio")
    now = T0 + timedelta(minutes=40)
    rec = wd.check(hb(now), now)
    assert [(x.level, x.title) for x in rec] == [("info", "[SIM] RECUPERADO")]
    assert wd.check(hb(now), now) == []


def test_watchdog_missing_file_is_critical():
    a = Watchdog("live").check(None, T0)
    assert a[0].level == "critical" and a[0].title.startswith("[LIVE]")


def test_watchdog_alive_but_no_ok_cycles():
    wd = Watchdog("sim", max_silence_min=5)                           # stale_ok = 15 min
    a = wd.check(hb(T0, last_ok=T0 - timedelta(minutes=20)), T0)
    assert [x.key for x in a] == ["not_ok"] and a[0].level == "warning"


def test_watchdog_reports_halted_and_frozen_with_reason():
    wd = Watchdog("sim")
    a = wd.check(hb(T0, state="frozen", reason="posición desconocida"), T0)
    assert a[0].key == "state" and a[0].level == "critical" and "CONGELADO" in a[0].title
    assert a[0].body == "posición desconocida"


def test_deliver_uses_telegram_as_fallback(monkeypatch):
    sent = []
    monkeypatch.setattr("xrpbot.ops.watchdog.send_telegram", lambda tok, chat, al: sent.append((tok, chat)) or True)
    res = deliver(Alert("critical", "silence", "t", "b"), env={"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "1"})
    assert res == {"push": 0, "telegram": True} and sent == [("x", "1")]
