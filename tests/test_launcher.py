"""Lanzador (xrpbot.exe): primera ejecución y subcomandos."""
import pytest

from xrpbot import launcher


def test_first_run_creates_config_and_folders(tmp_path):
    launcher.first_run_setup(tmp_path)
    assert (tmp_path / "config" / "settings.yaml").exists() and (tmp_path / "config" / "ui.yaml").exists()
    assert (tmp_path / ".env.example").exists() and not (tmp_path / ".env").exists()
    for d in ("state", "data", "logs", "reports", "ui-state"):
        assert (tmp_path / d).is_dir()
    (tmp_path / "config" / "ui.yaml").write_text("port: 9999\n")
    launcher.first_run_setup(tmp_path)                       # nunca sobrescribe la configuración del usuario
    assert (tmp_path / "config" / "ui.yaml").read_text() == "port: 9999\n"


def test_dispatch_routes_and_rejects_unknown(capsys):
    with pytest.raises(SystemExit) as exc:
        launcher.dispatch(["bot", "--help"])
    assert exc.value.code == 0 and "download" in capsys.readouterr().out
    assert launcher.dispatch(["desconocido"]) == 2


def test_live_mode_is_not_in_the_menu():
    assert "live" not in launcher.MENU.lower().replace("el modo real no está", "")
