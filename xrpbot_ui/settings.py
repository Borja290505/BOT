"""Configuración de la interfaz (config/ui.yaml).

La interfaz no carga .env ni conoce claves de Kraken: solo lee parámetros del
bot (settings.yaml) para mostrarlos y sus bases de datos en modo lectura.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, fields
from pathlib import Path

import yaml


class UISettingsError(ValueError):
    pass


@dataclass
class UISettings:
    host: str = "127.0.0.1"
    port: int = 8050
    read_only: bool = True
    control_unlock_minutes: int = 5
    session_idle_minutes: int = 30
    session_max_hours: int = 12
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15
    secure_cookies: bool = False
    source: str = "demo"
    mode: str = "sim"
    bot_settings_path: str = "config/settings.yaml"
    state_dir: str = "state"
    ui_db_path: str = "ui-state/ui.sqlite"
    display_timezone: str = "Europe/Madrid"

    def validate(self) -> None:
        try:
            ip = ipaddress.ip_address(self.host)
        except ValueError:
            raise UISettingsError("host debe ser una IP; usa 127.0.0.1") from None
        if not ip.is_loopback:
            raise UISettingsError(
                "La interfaz solo escucha en 127.0.0.1. Para acceso remoto usa Tailscale "
                "(tailscale serve), nunca un puerto abierto a internet.")
        if self.source not in ("demo", "bot"):
            raise UISettingsError("source debe ser demo o bot")
        if self.mode not in ("sim", "live"):
            raise UISettingsError("mode debe ser sim o live")
        if not 1 <= self.control_unlock_minutes <= 30:
            raise UISettingsError("control_unlock_minutes debe estar entre 1 y 30")

    @property
    def bot_state_db(self) -> Path:
        return Path(self.state_dir) / f"xrpbot-{self.mode}.sqlite"

    @property
    def commands_db(self) -> Path:
        return Path(self.state_dir) / f"commands-{self.mode}.sqlite"


def load_ui_settings(path: str | Path = "config/ui.yaml", **overrides) -> UISettings:
    raw = {}
    if path and Path(path).exists():
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    known = {f.name for f in fields(UISettings)}
    unknown = set(raw) - known
    if unknown:
        raise UISettingsError(f"Claves desconocidas en ui.yaml: {sorted(unknown)}")
    raw.update({k: v for k, v in overrides.items() if v is not None})
    s = UISettings(**raw)
    s.validate()
    return s
