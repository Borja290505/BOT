"""Arranque de la interfaz.

    python -m xrpbot_ui set-password              # crea/cambia la contraseña (pregunta dos veces)
    python -m xrpbot_ui --demo                    # datos simulados (sin bot ni Kraken)
    python -m xrpbot_ui --demo --scenario caido   # empieza en un escenario concreto
    python -m xrpbot_ui                           # según config/ui.yaml (source: demo | bot)

Escucha SOLO en 127.0.0.1. Para el móvil: Tailscale (ver docs/ui/DESPLIEGUE.md).
"""
from __future__ import annotations

import argparse
import getpass
import sys

from .security import MIN_PASSWORD_LEN, SecurityStore
from .settings import UISettingsError, load_ui_settings


def build_provider(settings, demo: bool, scenario: str | None):
    if demo or settings.source == "demo":
        from .providers.demo import DemoProvider
        return DemoProvider(scenario or "normal", settings_path=settings.bot_settings_path)
    from .providers.botdb import BotDBProvider
    return BotDBProvider(settings.bot_state_db, settings.mode, settings.bot_settings_path)


def make_store(s) -> SecurityStore:
    return SecurityStore(s.ui_db_path, idle_minutes=s.session_idle_minutes, max_hours=s.session_max_hours,
                         max_attempts=s.login_max_attempts, lockout_minutes=s.login_lockout_minutes)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="xrpbot_ui")
    ap.add_argument("command", nargs="?", choices=["serve", "set-password"], default="serve")
    ap.add_argument("--config", default="config/ui.yaml")
    ap.add_argument("--demo", action="store_true", help="datos simulados")
    ap.add_argument("--scenario", help="escenario inicial de la demo")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--port", type=int)
    args = ap.parse_args(argv)
    try:
        s = load_ui_settings(args.config, port=args.port)
    except UISettingsError as exc:
        raise SystemExit(f"Configuración no válida: {exc}") from None

    if args.command == "set-password":
        pw = getpass.getpass(f"Nueva contraseña para '{args.user}' (mín. {MIN_PASSWORD_LEN} caracteres): ")
        if pw != getpass.getpass("Repite la contraseña: "):
            raise SystemExit("Las contraseñas no coinciden")
        try:
            make_store(s).set_password(args.user, pw)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        print(f"Contraseña guardada para '{args.user}'. Las sesiones abiertas se han cerrado.")
        return

    import uvicorn

    from .server import create_app
    store = make_store(s)
    if not store.has_users():
        print("No hay usuarios. Crea uno primero:  python -m xrpbot_ui set-password", file=sys.stderr)
        raise SystemExit(2)
    provider = build_provider(s, args.demo, args.scenario)
    app = create_app(s, provider, store)
    print(f"Interfaz en http://{s.host}:{s.port}  ·  fuente: {provider.name}  ·  "
          f"{'SOLO LECTURA' if s.read_only else 'CONTROL HABILITADO (requiere desbloqueo)'}")
    # timeout_graceful_shutdown: las conexiones en tiempo real (SSE) no terminan nunca por sí solas;
    # sin este límite, al parar el servicio el proceso se quedaría a medio cerrar ocupando el puerto.
    uvicorn.run(app, host=s.host, port=s.port, log_level="warning", proxy_headers=False,
                timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
