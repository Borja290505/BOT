"""Descarga de datos históricos de Kraken Futures y guardado en Parquet.

Fuentes (API pública, sin claves):
- Velas de trades y de mark price: /api/charts/v1/{trade|mark}/{symbol}/1h
- Funding histórico: /derivatives/api/v3/historicalfundingrates?symbol=...

Límites a tener en cuenta:
- El histórico solo existe desde el listado del perpetuo (unos pocos años);
  faltan regímenes de mercado anteriores. Para ampliar se puede usar el
  histórico spot de XRP/USD de Kraken (CSV descargable), ver load_spot_csv; es
  un proxy: no tiene funding y la base spot-perpetuo no es exactamente cero.
- El número máximo de velas por petición no está garantizado, así que se
  pagina por ventanas y se continúa desde la última vela recibida.
- La descarga es incremental: si el Parquet existe, solo se pide lo nuevo.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from ..exchange.rest import KrakenFuturesRest
from .store import (CANDLE_COLS, candles_path, funding_path, load_frame, merge_frames,
                    save_frame)

log = logging.getLogger(__name__)

RES_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400,
               "12h": 43200, "1d": 86400}
WINDOW_BARS = 2000


def candles_to_frame(candles: list[dict]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=CANDLE_COLS, index=pd.DatetimeIndex([], tz="UTC"))
    df = pd.DataFrame(candles)
    df.index = pd.to_datetime(df["time"].astype("int64"), unit="ms", utc=True)
    df = df[CANDLE_COLS].astype(float)
    df.index.name = "time"
    return df


def funding_to_frame(rates: list[dict]) -> pd.DataFrame:
    """Columnas: funding_rate (absoluto, USD por contrato) y relative_rate (fracción del periodo)."""
    if not rates:
        return pd.DataFrame(columns=["funding_rate", "relative_rate"],
                            index=pd.DatetimeIndex([], tz="UTC"))
    df = pd.DataFrame(rates)
    df.index = pd.to_datetime(df["timestamp"], utc=True)
    df = df.rename(columns={"fundingRate": "funding_rate", "relativeFundingRate": "relative_rate"})
    df = df[["funding_rate", "relative_rate"]].astype(float)
    df.index.name = "time"
    return df[~df.index.duplicated(keep="last")].sort_index()


async def download_candles(rest: KrakenFuturesRest, data_dir: str | Path, symbol: str,
                           tick_type: str = "trade", resolution: str = "1h",
                           since: pd.Timestamp | None = None) -> pd.DataFrame:
    path = candles_path(data_dir, symbol, tick_type, resolution)
    old = load_frame(path) if path.exists() else None
    step = RES_SECONDS[resolution]
    now_s = int(pd.Timestamp.now(tz="UTC").timestamp())
    if old is not None and not old.empty:
        # se vuelve a pedir la última vela por si se guardó sin cerrar
        start_s = int(old.index[-1].timestamp())
    else:
        start_s = int((since or pd.Timestamp("2018-01-01", tz="UTC")).timestamp())

    frames = []
    while start_s < now_s:
        end_s = min(start_s + WINDOW_BARS * step, now_s)
        payload = await rest.get_candles(tick_type, symbol, resolution, start_s, end_s)
        df = candles_to_frame(payload.get("candles", []))
        if df.empty:
            start_s = end_s  # ventana sin datos (antes del listado): avanzar
            continue
        frames.append(df)
        start_s = int(df.index[-1].timestamp()) + step
        log.info("%s %s: %d velas hasta %s", symbol, tick_type, len(df), df.index[-1])

    new = pd.concat(frames) if frames else candles_to_frame([])
    out = merge_frames(old, new)
    # nunca se guarda la vela en curso: aún no ha cerrado
    out = out[out.index + pd.Timedelta(seconds=step) <= pd.Timestamp.now(tz="UTC")]
    save_frame(out, path)
    return out


async def download_funding(rest: KrakenFuturesRest, data_dir: str | Path, symbol: str) -> pd.DataFrame:
    path = funding_path(data_dir, symbol)
    old = load_frame(path) if path.exists() else None
    new = funding_to_frame(await rest.get_historical_funding(symbol))
    out = merge_frames(old, new)
    save_frame(out, path)
    return out


def load_spot_csv(path: str | Path, resolution: str = "1h") -> pd.DataFrame:
    """Lee un CSV OHLCVT de Kraken spot (sin cabecera: time,open,high,low,close,volume,trades).

    Úsalo solo para ampliar el histórico de la estrategia (proxy sin funding).
    """
    df = pd.read_csv(path, header=None,
                     names=["time", "open", "high", "low", "close", "volume", "trades"])
    df.index = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df[CANDLE_COLS].astype(float)
    rule = {"1h": "1h", "4h": "4h", "1d": "1D"}[resolution]
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    out.index.name = "time"
    return out
