"""Copia de seguridad diaria de las bases de datos SQLite.

Usa la API de copia en caliente de SQLite (Connection.backup): copia
coherente aunque el bot esté escribiendo, sin pararlo. Después comprueba la
integridad de la copia y borra las de más de N días.

    backups/2026-10-26/xrpbot-sim.sqlite
    backups/2026-10-26/ui.sqlite
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)


class BackupError(RuntimeError):
    pass


def backup_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    with sqlite3.connect(src, timeout=30) as s, sqlite3.connect(tmp) as d:
        s.backup(d)
        ok = d.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        tmp.unlink(missing_ok=True)
        raise BackupError(f"Copia de {src} corrupta: {ok}")
    tmp.replace(dst)


def run_backup(sources: list[Path], dest_root: Path, keep_days: int = 14,
               now: datetime | None = None) -> list[Path]:
    now = now or datetime.now(timezone.utc)
    day_dir = Path(dest_root) / now.strftime("%Y-%m-%d")
    done = []
    for src_dir in sources:
        for db in sorted(Path(src_dir).glob("*.sqlite")):
            dst = day_dir / db.name
            backup_file(db, dst)
            done.append(dst)
            log.info("Copia OK: %s -> %s", db, dst)
    prune(dest_root, keep_days, now)
    return done


def prune(dest_root: Path, keep_days: int, now: datetime) -> list[Path]:
    """Borra las carpetas de copia (AAAA-MM-DD) con más de keep_days días."""
    removed = []
    limit = (now - timedelta(days=keep_days)).date()
    for d in Path(dest_root).glob("????-??-??"):
        try:
            day = datetime.strptime(d.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < limit:
            shutil.rmtree(d)
            removed.append(d)
    return removed
