"""Lanzador único (xrpbot.exe en Windows).

Doble clic -> menú. También acepta subcomandos:

    xrpbot.exe                          menú
    xrpbot.exe bot <args>               = python -m xrpbot.cli <args>   (p. ej. bot status --mode sim)
    xrpbot.exe panel <args>             = python -m xrpbot_ui <args>
    xrpbot.exe research <args>          = python -m xrpbot.research <args>
    xrpbot.exe ops <args>               = python -m xrpbot.ops <args>

Trabaja en la carpeta donde está el .exe: ahí crea config/, state/, data/, logs/,
reports/ y ui-state/. La primera vez copia la configuración por defecto.

El MODO REAL no está en el menú a propósito: solo se arranca escribiendo
`xrpbot.exe bot run --mode live --live`, que pide la confirmación escrita.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

PANEL_URL = "http://127.0.0.1:8050"
FROZEN = getattr(sys, "frozen", False)


def base_dir() -> Path:
    return Path(sys.executable).parent if FROZEN else Path.cwd()


def bundle_dir() -> Path:
    """Donde están los ficheros incluidos en el .exe (o la raíz del repositorio)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def first_run_setup(base: Path) -> None:
    for rel in ("config/settings.yaml", "config/ui.yaml", ".env.example"):
        dst = base / rel
        src = bundle_dir() / rel
        if not dst.exists() and src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"Creado {rel} (configuración por defecto)")
    for d in ("state", "data", "logs", "reports", "ui-state"):
        (base / d).mkdir(exist_ok=True)


def self_cmd(*args: str) -> list[str]:
    return [sys.executable, *args] if FROZEN else [sys.executable, "-m", "xrpbot.launcher", *args]


def spawn(title: str, *args: str) -> None:
    """Abre una ventana nueva con el subcomando (el menú sigue disponible)."""
    if os.name == "nt":
        subprocess.Popen(self_cmd(*args), creationflags=subprocess.CREATE_NEW_CONSOLE)  # type: ignore[attr-defined]
        print(f"→ «{title}» abierto en otra ventana. Ciérrala (o Ctrl+C dentro) para pararlo.")
    else:
        subprocess.call(self_cmd(*args))


def dispatch(argv: list[str]) -> int:
    cmd, rest = argv[0], argv[1:]
    if cmd == "bot":
        from xrpbot.cli import main
        main(rest)
    elif cmd == "panel":
        from xrpbot_ui.__main__ import main
        if "set-password" not in rest and "--no-browser" not in rest:
            threading.Thread(target=lambda: (time.sleep(4), webbrowser.open(PANEL_URL)), daemon=True).start()
        main([a for a in rest if a != "--no-browser"])
    elif cmd == "research":
        from xrpbot.research.__main__ import main
        return main(rest)
    elif cmd == "ops":
        from xrpbot.ops.__main__ import main
        return main(rest)
    else:
        print(__doc__)
        return 2
    return 0


def ensure_password() -> None:
    from xrpbot_ui.__main__ import make_store
    from xrpbot_ui.settings import load_ui_settings
    if not make_store(load_ui_settings("config/ui.yaml")).has_users():
        print("\nPrimero crea la contraseña del panel (usuario «admin», mínimo 12 caracteres).")
        dispatch(["panel", "set-password"])


MENU = """
================  xrpbot  ================
  1  Panel de DEMOSTRACIÓN (datos simulados)
  2  Bot en SIM + panel conectado     (sin dinero real)
  3  Solo el bot en SIM
  4  Descargar / actualizar datos de Kraken
  5  Backtest
  6  Investigación de la estrategia (informe)
  7  Estado del bot (SIM)
  8  KILL SWITCH del bot SIM
  9  Rearmar el bot SIM tras una parada
  P  Crear o cambiar la contraseña del panel
  0  Salir
------------------------------------------
  El modo REAL no está en este menú (ver README).
"""


def menu() -> int:
    while True:
        print(MENU)
        try:
            op = input("Opción: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            return 0
        try:
            if op == "1":
                ensure_password()
                spawn("Panel demo", "panel", "--demo")
            elif op == "2":
                ensure_password()
                spawn("Bot SIM", "bot", "run", "--mode", "sim")
                spawn("Panel (bot SIM)", "panel", "--source", "bot", "--mode", "sim")
            elif op == "3":
                spawn("Bot SIM", "bot", "run", "--mode", "sim")
            elif op == "4":
                dispatch(["bot", "download", "--since", "2022-01-01"])
            elif op == "5":
                dispatch(["bot", "backtest", "--funding-filter", "off"])
            elif op == "6":
                dispatch(["research"])
                print(f"\nInforme guardado en {Path('reports/research/informe.md').resolve()}")
            elif op == "7":
                dispatch(["bot", "status", "--mode", "sim"])
            elif op == "8":
                if input("Escribe KILL para confirmar: ").strip() == "KILL":
                    dispatch(["bot", "kill", "--mode", "sim"])
                else:
                    print("Cancelado.")
            elif op == "9":
                dispatch(["bot", "rearm", "--mode", "sim"])
            elif op == "P":
                dispatch(["panel", "set-password"])
            elif op == "0":
                return 0
            else:
                print("Opción no válida.")
        except SystemExit as exc:          # los comandos terminan con SystemExit: volver al menú
            if exc.code not in (None, 0):
                print(f"\n{exc.code}")
        except Exception as exc:  # noqa: BLE001 - mostrar el error y seguir en el menú
            print(f"\nERROR: {exc!r}")
        input("\nPulsa Enter para volver al menú…")


def main() -> int:
    import multiprocessing
    multiprocessing.freeze_support()
    for stream in (sys.stdout, sys.stderr):    # consola de Windows: nunca fallar por un carácter
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass           # el panel usa un proceso aparte para los backtests
    base = base_dir()
    os.chdir(base)
    first_run_setup(base)
    if len(sys.argv) > 1:
        return dispatch(sys.argv[1:]) or 0
    print(f"Carpeta de trabajo: {base}")
    return menu()


if __name__ == "__main__":
    sys.exit(main())
