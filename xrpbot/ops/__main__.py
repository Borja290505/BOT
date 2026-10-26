"""Herramientas de operación 24/7.

    python -m xrpbot.ops healthcheck --mode sim [--max-age 120]   # 0 = sano, 1 = no
    python -m xrpbot.ops backup [--keep 14] [--every-hours 24]    # copia de seguridad (una vez o en bucle)
    python -m xrpbot.ops watchdog --mode sim [--max-silence-min 5] # aviso si el bot deja de latir

El healthcheck y el watchdog solo leen el fichero de latido (state/heartbeat-{modo}.json).
Ninguno necesita ni lee claves de Kraken.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .heartbeat import age_seconds, heartbeat_path, read_heartbeat


def healthcheck(state_dir: str, mode: str, max_age: float) -> int:
    hb = read_heartbeat(heartbeat_path(state_dir, mode))
    if hb is None:
        print(f"UNHEALTHY: no hay latido en {heartbeat_path(state_dir, mode)}")
        return 1
    age = age_seconds(hb.get("ts"))
    if age is None or age > max_age:
        print(f"UNHEALTHY: último latido hace {age:.0f}s (> {max_age:.0f}s)" if age is not None else "UNHEALTHY: latido ilegible")
        return 1
    print(f"OK: latido hace {age:.0f}s · estado {hb.get('state')} · pid {hb.get('pid')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="xrpbot.ops")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("healthcheck")
    p.add_argument("--mode", default="sim")
    p.add_argument("--state-dir", default="state")
    p.add_argument("--max-age", type=float, default=120)
    p = sub.add_parser("backup")
    p.add_argument("--source", action="append", default=None, help="carpeta con *.sqlite (repetible)")
    p.add_argument("--dest", default="backups")
    p.add_argument("--keep", type=int, default=14, help="días de copias que se conservan")
    p.add_argument("--every-hours", type=float, default=0, help="0 = una vez; >0 = en bucle (Docker)")
    p = sub.add_parser("watchdog")
    p.add_argument("--mode", default="sim")
    p.add_argument("--state-dir", default="state")
    p.add_argument("--max-silence-min", type=float, default=None)
    p.add_argument("--remind-min", type=float, default=None)
    p.add_argument("--once", action="store_true", help="una sola comprobación (tests)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "healthcheck":
        return healthcheck(args.state_dir, args.mode, args.max_age)
    if args.cmd == "backup":
        from .backup import run_backup
        sources = [Path(s) for s in (args.source or ["state", "ui-state"]) if Path(s).exists()]
        while True:
            try:
                done = run_backup(sources, Path(args.dest), args.keep)
                print(f"Copias realizadas: {len(done)} en {args.dest}")
            except Exception as exc:  # noqa: BLE001 - en bucle, seguir y avisar en el log
                logging.exception("Copia de seguridad fallida: %r", exc)
                if not args.every_hours:
                    return 1
            if not args.every_hours:
                return 0
            time.sleep(args.every_hours * 3600)
    if args.cmd == "watchdog":
        from .watchdog import run_watchdog
        return run_watchdog(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
