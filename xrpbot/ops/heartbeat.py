"""Latido del bot: un fichero JSON pequeño que el bot reescribe en cada ciclo.

    state/heartbeat-{modo}.json
    {"ts": "...", "pid": 123, "mode": "sim", "state": "active",
     "last_ok_at": "...", "started_at": "...", "version": "0.1.0"}

- ts: el bucle sigue vivo (se escribe aunque el ciclo haya fallado).
- last_ok_at: último ciclo completo sin errores (p. ej. Kraken respondiendo).
Lo leen el healthcheck de Docker/systemd, el watchdog y la interfaz, sin abrir
la base de datos del bot. La escritura es atómica (fichero temporal + rename).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

VERSION = "0.1.0"


def heartbeat_path(state_dir: str | Path, mode: str) -> Path:
    return Path(state_dir) / f"heartbeat-{mode}.json"


def write_heartbeat(path: str | Path, **fields) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"ts": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(), "version": VERSION, **fields}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".hb-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_heartbeat(path: str | Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def age_seconds(iso: str | None, now: datetime | None = None) -> float | None:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return ((now or datetime.now(timezone.utc)) - then).total_seconds()
