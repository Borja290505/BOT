"""Compila el lanzador en un único ejecutable con PyInstaller.

    pip install -r requirements.txt pyinstaller pillow
    python scripts/build_exe.py            # -> dist/xrpbot.exe (Windows) o dist/xrpbot (Linux)

Lo usa también el workflow «Windows .exe» de GitHub Actions.
"""
import os
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
SEP = os.pathsep


def data(src: str, dst: str) -> str:
    return f"--add-data={ROOT / src}{SEP}{dst}"


PyInstaller.__main__.run([
    str(ROOT / "xrpbot" / "launcher.py"),
    "--name=xrpbot", "--onefile", "--console", "--noconfirm", "--clean",
    f"--icon={ROOT / 'xrpbot_ui' / 'static' / 'icons' / 'icon-512.png'}",
    f"--paths={ROOT}",
    data("xrpbot_ui/templates", "xrpbot_ui/templates"),
    data("xrpbot_ui/static", "xrpbot_ui/static"),
    data("config/settings.yaml", "config"),
    data("config/ui.yaml", "config"),
    data(".env.example", "."),
    # importaciones dinámicas que PyInstaller no ve solo
    "--collect-submodules=xrpbot", "--collect-submodules=xrpbot_ui",
    "--collect-submodules=uvicorn", "--hidden-import=multipart",
    "--collect-all=py_vapid", "--collect-all=pywebpush", "--collect-all=http_ece",
    f"--distpath={ROOT / 'dist'}", f"--workpath={ROOT / 'build'}", f"--specpath={ROOT / 'build'}",
])
