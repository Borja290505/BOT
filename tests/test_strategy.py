import numpy as np
import pandas as pd
import pytest

from conftest import make_candles, random_walk
from xrpbot.models import Side
from xrpbot.strategy.donchian_atr import (StrategyParams, compute_indicators, funding_allows,
                                          funding_rate_at, signal_at, trailing_stop)
from xrpbot.strategy.indicators import donchian_prev

P = StrategyParams(entry_lookback=20, exit_lookback=10, atr_period=14, atr_stop_mult=2.0, take_profit_r=4.0)


def breakout_df(n_flat=40, breakout=0.52):
    closes = [0.5] * n_flat + [breakout]
    highs = [0.501] * n_flat + [breakout + 0.001]
    lows = [0.499] * n_flat + [0.5]
    opens = [0.5] * (n_flat + 1)
    return make_candles(closes, opens=opens, highs=highs, lows=lows)


def test_donchian_excludes_current_bar():
    df = breakout_df()
    upper, _ = donchian_prev(df, 20)
    assert upper.iloc[-1] == pytest.approx(0.501)   # no incluye el máximo de la propia vela


def test_long_signal_on_breakout():
    df = breakout_df()
    ind = compute_indicators(df, P)
    sig = signal_at(ind, len(df) - 1, P)
    assert sig is not None and sig.side is Side.LONG
    assert sig.ref_price == 0.52
    assert sig.stop_price == pytest.approx(0.52 - 2 * ind["atr"].iloc[-1])
    assert sig.take_profit_price == pytest.approx(0.52 + 4 * (0.52 - sig.stop_price))
    assert signal_at(ind, len(df) - 2, P) is None


def test_short_signal_on_breakdown():
    df = breakout_df(breakout=0.48)
    df.loc[df.index[-1], ["high", "low"]] = [0.5, 0.479]
    sig = signal_at(compute_indicators(df, P), len(df) - 1, P)
    assert sig is not None and sig.side is Side.SHORT and sig.stop_price > 0.48


def test_no_lookahead_indicators_and_signals():
    df = random_walk(800, seed=3)
    full = compute_indicators(df, P)
    k = 500
    altered = df.copy()
    rng = np.random.default_rng(0)
    altered.iloc[k + 1:] *= rng.uniform(0.5, 1.5, size=(len(df) - k - 1, 1))
    part = compute_indicators(altered, P)
    pd.testing.assert_frame_equal(full.iloc[: k + 1], part.iloc[: k + 1])
    # y lo mismo truncando los datos: el pasado no depende del futuro
    trunc = compute_indicators(df.iloc[: k + 1], P)
    pd.testing.assert_frame_equal(full.iloc[: k + 1], trunc)


def test_funding_filter():
    pf = P.with_(funding_filter=True, funding_max_abs_hourly=0.0001)
    assert not funding_allows(Side.LONG, 0.0005, pf)
    assert funding_allows(Side.SHORT, 0.0005, pf)        # los cortos cobran
    assert not funding_allows(Side.SHORT, -0.0005, pf)
    assert funding_allows(Side.LONG, 0.0005, P)            # filtro desactivado
    assert funding_allows(Side.LONG, None, pf)


def test_funding_rate_uses_only_past_records():
    idx = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
    f = pd.DataFrame({"funding_rate": 0.0, "relative_rate": [0.1, 0.2, 0.3]}, index=idx)
    assert funding_rate_at(f, idx[1]) == 0.2
    assert funding_rate_at(f, idx[1] + pd.Timedelta("30min")) == 0.2
    assert funding_rate_at(f, idx[0] - pd.Timedelta("1h")) is None


def test_funding_filter_blocks_signal():
    df = breakout_df()
    f = pd.DataFrame({"funding_rate": 0.0, "relative_rate": 0.001}, index=df.index)
    assert signal_at(compute_indicators(df, P), len(df) - 1, P.with_(funding_filter=True), f) is None


def test_trailing_never_moves_back():
    assert trailing_stop(Side.LONG, 0.50, 0.49, np.nan) == 0.50
    assert trailing_stop(Side.LONG, 0.50, 0.51, np.nan) == 0.51
    assert trailing_stop(Side.SHORT, 0.50, np.nan, 0.51) == 0.50
    assert trailing_stop(Side.SHORT, 0.50, np.nan, 0.49) == 0.49
    assert trailing_stop(Side.LONG, 0.50, np.nan, np.nan) == 0.50
