"""Carga y validación de la configuración.

- Parámetros no secretos: config/settings.yaml
- Secretos: variables de entorno / fichero .env (nunca en el código ni en el YAML)

Se valida todo al arrancar: es preferible no arrancar a arrancar con un valor
absurdo (p. ej. un riesgo del 10 % por un error de tecleo).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


class Mode(str, Enum):
    BACKTEST = "backtest"
    DEMO = "demo"
    SIM = "sim"
    LIVE = "live"


# URLs oficiales de Kraken Futures. demo-futures es el entorno de pruebas.
URLS = {
    "live": {
        "rest": "https://futures.kraken.com/derivatives/api/v3",
        "charts": "https://futures.kraken.com/api/charts/v1",
        "ws": "wss://futures.kraken.com/ws/v1",
    },
    "demo": {
        "rest": "https://demo-futures.kraken.com/derivatives/api/v3",
        "charts": "https://demo-futures.kraken.com/api/charts/v1",
        "ws": "wss://demo-futures.kraken.com/ws/v1",
    },
}


class ConfigError(ValueError):
    pass


@dataclass
class CapitalCfg:
    max_capital_usd: float | None = None
    backtest_initial_usd: float = 10_000.0


@dataclass
class RiskCfg:
    risk_per_trade: float = 0.01
    max_daily_loss: float = 0.03
    max_leverage: float = 2.0
    max_open_positions: int = 1
    min_liq_distance_multiple: float = 3.0
    margin_mode: str = "isolated"
    protect_unknown_positions: bool = True
    emergency_stop_atr_mult: float = 3.0


@dataclass
class FundingFilterCfg:
    enabled: bool = False
    max_abs_hourly_rate: float = 0.0001


@dataclass
class StrategyCfg:
    name: str = "donchian_atr"
    entry_lookback: int = 20
    exit_lookback: int = 10
    atr_period: int = 14
    atr_stop_mult: float = 2.0
    take_profit_r: float = 4.0
    funding_filter: FundingFilterCfg = field(default_factory=FundingFilterCfg)


@dataclass
class ExecutionCfg:
    entry_max_slippage_bps: float = 25
    stop_trigger_signal: str = "mark"
    take_profit_post_only: bool = True
    candle_close_delay_s: int = 20
    poll_interval_s: int = 5
    reconcile_interval_s: int = 60


@dataclass
class CircuitBreakerCfg:
    max_spread_bps: float = 15
    max_data_age_s: float = 30
    max_candle_delay_bars: int = 2
    max_atr_ratio: float = 3.0
    max_mark_last_divergence_bps: float = 50
    min_book_depth_multiple: float = 3.0


@dataclass
class OfflineSpecCfg:
    contract_size: float = 1.0
    tick_size: float = 0.0001
    size_step: float = 1.0
    min_size: float = 1.0
    maintenance_margin: float = 0.01


@dataclass
class BacktestCfg:
    data_dir: str = "data"
    slippage_bps: float = 5
    fallback_taker_fee: float = 0.0005
    fallback_maker_fee: float = 0.0002
    stop_trigger_source: str = "mark"
    offline_spec: OfflineSpecCfg = field(default_factory=OfflineSpecCfg)


@dataclass
class WalkForwardCfg:
    train_months: int = 12
    test_months: int = 3
    holdout_months: int = 6
    min_train_trades: int = 20
    grid: dict[str, list] = field(default_factory=lambda: {
        "entry_lookback": [20, 40, 55],
        "exit_lookback": [10, 20],
        "atr_stop_mult": [2.0, 3.0],
    })


@dataclass
class StorageCfg:
    db_path: str = "state/xrpbot-{mode}.sqlite"   # una base de datos por modo
    log_dir: str = "logs"


@dataclass
class AlertsCfg:
    telegram_enabled: bool = False


@dataclass
class Secrets:
    api_key: str | None = None
    api_secret: str | None = None
    telegram_token: str | None = None
    telegram_chat_id: str | None = None

    def __repr__(self) -> str:  # nunca imprimir secretos en logs
        return (f"Secrets(api_key={'***' if self.api_key else None}, "
                f"api_secret={'***' if self.api_secret else None}, "
                f"telegram={'***' if self.telegram_token else None})")


@dataclass
class Settings:
    mode: Mode = Mode.DEMO
    symbol: str = "PF_XRPUSD"
    timeframe: str = "1h"
    capital: CapitalCfg = field(default_factory=CapitalCfg)
    risk: RiskCfg = field(default_factory=RiskCfg)
    strategy: StrategyCfg = field(default_factory=StrategyCfg)
    execution: ExecutionCfg = field(default_factory=ExecutionCfg)
    circuit_breaker: CircuitBreakerCfg = field(default_factory=CircuitBreakerCfg)
    backtest: BacktestCfg = field(default_factory=BacktestCfg)
    walkforward: WalkForwardCfg = field(default_factory=WalkForwardCfg)
    storage: StorageCfg = field(default_factory=StorageCfg)
    alerts: AlertsCfg = field(default_factory=AlertsCfg)
    secrets: Secrets = field(default_factory=Secrets)

    @property
    def urls(self) -> dict[str, str]:
        """URLs del entorno. sim usa datos públicos de producción."""
        return URLS["demo"] if self.mode == Mode.DEMO else URLS["live"]

    def validate(self) -> None:
        r = self.risk
        if not 0 < r.risk_per_trade <= 0.02:
            raise ConfigError("risk_per_trade debe estar en (0, 2 %]")
        if not 0 < r.max_daily_loss <= 0.10:
            raise ConfigError("max_daily_loss debe estar en (0, 10 %]")
        if r.max_daily_loss < r.risk_per_trade:
            raise ConfigError("max_daily_loss no puede ser menor que risk_per_trade")
        if not 0 < r.max_leverage <= 5:
            raise ConfigError("max_leverage debe estar en (0, 5]")
        if r.max_open_positions != 1:
            raise ConfigError("Esta versión solo soporta max_open_positions = 1")
        if r.margin_mode not in ("isolated", "cross"):
            raise ConfigError("margin_mode debe ser isolated o cross")
        if self.timeframe != "1h":
            raise ConfigError("Esta versión solo soporta timeframe 1h")
        if self.execution.stop_trigger_signal not in ("mark", "last"):
            raise ConfigError("stop_trigger_signal debe ser mark o last")
        s = self.strategy
        if s.exit_lookback >= s.entry_lookback:
            raise ConfigError("exit_lookback debe ser menor que entry_lookback")
        if s.atr_stop_mult <= 0 or s.take_profit_r <= 0:
            raise ConfigError("atr_stop_mult y take_profit_r deben ser > 0")


def _build(cls, data: dict[str, Any] | None):
    """Construye un dataclass anidado a partir de un dict, rechazando claves desconocidas."""
    data = data or {}
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ConfigError(f"Claves desconocidas en {cls.__name__}: {sorted(unknown)}")
    kwargs = {}
    for name, value in data.items():
        f = known[name]
        default = f.default_factory() if callable(f.default_factory) else f.default  # type: ignore[misc]
        if is_dataclass(default) and isinstance(value, dict):
            kwargs[name] = _build(type(default), value)
        else:
            kwargs[name] = value
    return cls(**kwargs)


def load_settings(path: str | Path = "config/settings.yaml", mode_override: str | None = None,
                  env_file: str | Path | None = ".env") -> Settings:
    if env_file and Path(env_file).exists():
        load_dotenv(env_file, override=False)
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    raw.pop("secrets", None)  # los secretos jamás vienen del YAML
    mode = mode_override or raw.pop("mode", "demo")
    raw.pop("mode", None)
    settings = _build(Settings, raw)
    try:
        settings.mode = Mode(mode)
    except ValueError as e:
        raise ConfigError(f"Modo desconocido: {mode}") from e
    settings.storage.db_path = settings.storage.db_path.format(mode=settings.mode.value)
    settings.secrets = _load_secrets(settings.mode)
    settings.validate()
    return settings


def _load_secrets(mode: Mode) -> Secrets:
    # Claves separadas para demo y real: en demo nunca se leen las de real y viceversa.
    prefix = {Mode.DEMO: "KRAKEN_DEMO_", Mode.LIVE: "KRAKEN_LIVE_"}.get(mode)
    s = Secrets(
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
    )
    if prefix:
        s.api_key = os.getenv(prefix + "API_KEY") or None
        s.api_secret = os.getenv(prefix + "API_SECRET") or None
    return s
