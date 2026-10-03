"""Almacenamiento local de datos de mercado en Parquet.

Convención de velas: índice = hora de APERTURA (UTC). Una vela con índice t
cubre [t, t+1h) y solo se conoce completa en t+1h.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

CANDLE_COLS = ["open", "high", "low", "close", "volume"]


def candles_path(data_dir: str | Path, symbol: str, tick_type: str, resolution: str) -> Path:
    return Path(data_dir) / f"{symbol}_{tick_type}_{resolution}.parquet"


def funding_path(data_dir: str | Path, symbol: str) -> Path:
    return Path(data_dir) / f"{symbol}_funding.parquet"


def save_frame(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


def load_frame(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"No existe {path}. Ejecuta primero: python -m xrpbot.cli download")
    df = pd.read_parquet(path)
    df.index = pd.DatetimeIndex(df.index, tz="UTC") if df.index.tz is None else df.index
    return df.sort_index()


def merge_frames(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    """Une y quita duplicados; en caso de duplicado gana el dato más reciente."""
    if old is None or old.empty:
        out = new
    else:
        out = pd.concat([old, new])
    out = out[~out.index.duplicated(keep="last")]
    return out.sort_index()


def load_candles(data_dir: str | Path, symbol: str, tick_type: str = "trade",
                 resolution: str = "1h") -> pd.DataFrame:
    return load_frame(candles_path(data_dir, symbol, tick_type, resolution))


def load_funding(data_dir: str | Path, symbol: str) -> pd.DataFrame:
    return load_frame(funding_path(data_dir, symbol))
