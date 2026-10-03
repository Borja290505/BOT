import numpy as np
import pandas as pd
import pytest

from xrpbot.models import InstrumentSpec


@pytest.fixture
def spec():
    # Valores sintéticos para tests; en demo/live se leen de /instruments.
    return InstrumentSpec(symbol="PF_XRPUSD", contract_size=1.0, tick_size=0.0001, size_step=1.0,
                          min_size=1.0, maintenance_margin=0.01)


def make_candles(closes, start="2024-01-01", spread=0.002, opens=None, highs=None, lows=None):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range(start, periods=len(closes), freq="1h", tz="UTC")
    o = np.asarray(opens, dtype=float) if opens is not None else np.r_[closes[0], closes[:-1]]
    h = np.asarray(highs, dtype=float) if highs is not None else np.maximum(o, closes) * (1 + spread)
    lo = np.asarray(lows, dtype=float) if lows is not None else np.minimum(o, closes) * (1 - spread)
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": closes, "volume": 1000.0}, index=idx)


def path_candles(n=3000, seed=1, start_price=0.5, vol=0.008, drift=None, sub=30):
    """Velas construidas a partir de un camino intrabarra real (sub pasos por vela).

    Máximos y mínimos salen del propio camino: no se fabrican, así que el
    backtest no gana ni pierde por un artefacto de los datos sintéticos.
    drift: array opcional de deriva por vela (fracción del precio inicial).
    """
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, vol * start_price / np.sqrt(sub), (n, sub))
    if drift is not None:
        steps += (np.asarray(drift) * start_price / sub)[:, None]
    p = np.maximum(start_price + np.cumsum(steps.ravel()), start_price * 0.05).reshape(n, sub)
    o = np.r_[start_price, p[:-1, -1]]
    c = p[:, -1]
    h = np.maximum(p.max(axis=1), o)
    lo = np.minimum(p.min(axis=1), o)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": 1000.0}, index=idx)


def random_walk(n=3000, seed=1, start_price=0.5, vol=0.01):
    """Camino con tramos de tendencia (para que haya rupturas y operaciones)."""
    drift = np.zeros(n)
    drift[n // 4: n // 2] = 0.002
    drift[3 * n // 4:] = -0.002
    return path_candles(n, seed, start_price, vol, drift)
