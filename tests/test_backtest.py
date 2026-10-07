import numpy as np
import pandas as pd
import pytest

from conftest import make_candles, random_walk
from xrpbot.backtest.engine import BacktestConfig, run_backtest
from xrpbot.backtest.metrics import compute_metrics, max_drawdown
from xrpbot.config import WalkForwardCfg
from xrpbot.backtest.walkforward import walk_forward
from xrpbot.strategy.donchian_atr import StrategyParams

P = StrategyParams(entry_lookback=20, exit_lookback=10, atr_period=14, atr_stop_mult=2.0, take_profit_r=4.0)


def cfg(spec, **kw):
    base = dict(initial_capital=10_000, spec=spec, taker_fee=0.0005, maker_fee=0.0002, slippage_bps=5,
                max_daily_loss=0.5)
    base.update(kw)
    return BacktestConfig(**base)


def scenario(next_bars):
    """40 velas planas, ruptura alcista en la 41, y luego las velas indicadas (o, h, l, c)."""
    o = [0.5] * 41
    h = [0.501] * 40 + [0.521]
    lo = [0.499] * 40 + [0.5]
    c = [0.5] * 40 + [0.52]
    for bo, bh, bl, bc in next_bars:
        o.append(bo); h.append(bh); lo.append(bl); c.append(bc)
    return make_candles(c, opens=o, highs=h, lows=lo)


def test_entry_at_next_open_not_signal_close(spec):
    df = scenario([(0.53, 0.54, 0.525, 0.535), (0.535, 0.536, 0.534, 0.535)])
    r = run_backtest(df, P, cfg(spec))
    t = r.trades.iloc[0]
    assert t["entry_time"] == df.index[41]
    assert t["entry_price"] == pytest.approx(0.53 * 1.0005)   # apertura siguiente + slippage
    assert t["fees"] >= t["size"] * t["entry_price"] * 0.0005


def test_stop_has_priority_when_both_hit(spec):
    df = scenario([(0.52, 0.9, 0.3, 0.52)])
    r = run_backtest(df, P, cfg(spec))
    t = r.trades.iloc[0]
    assert t["exit_reason"] == "stop"
    assert t["exit_price"] == pytest.approx(t["initial_stop"] * (1 - 0.0005))
    assert t["net_pnl"] < 0


def test_gap_through_stop_fills_at_open(spec):
    df = scenario([(0.52, 0.521, 0.519, 0.52), (0.30, 0.31, 0.29, 0.30)])
    t = run_backtest(df, P, cfg(spec)).trades.iloc[0]
    assert t["exit_reason"] == "stop_gap"
    assert t["exit_price"] == pytest.approx(0.30 * (1 - 0.0005))
    assert t["r_multiple"] < -1   # el gap hace perder más de 1R: el 1 % no es un máximo garantizado


def test_take_profit_requires_penetration_and_uses_maker_fee(spec):
    df = scenario([(0.52, 0.9, 0.519, 0.6)])
    t = run_backtest(df, P, cfg(spec)).trades.iloc[0]
    assert t["exit_reason"] == "take_profit"
    assert t["exit_price"] == pytest.approx(t["take_profit"])
    entry_fee = t["size"] * t["entry_price"] * 0.0005
    exit_fee = t["size"] * t["exit_price"] * 0.0002
    assert t["fees"] == pytest.approx(entry_fee + exit_fee)


def test_funding_charged_to_longs_when_positive(spec):
    df = scenario([(0.52, 0.521, 0.519, 0.52)] * 3)
    f = pd.DataFrame({"funding_rate": 0.0, "relative_rate": 0.001}, index=df.index)
    t = run_backtest(df, P, cfg(spec), funding=f).trades.iloc[0]
    assert t["funding"] < 0
    # 3 horas con posición abierta (la última se cierra por fin de datos tras su funding)
    assert t["funding"] == pytest.approx(-3 * t["size"] * 0.52 * 0.001)


def test_signal_beyond_stop_at_open_is_skipped(spec):
    df = scenario([(0.30, 0.31, 0.29, 0.30)])
    r = run_backtest(df, P, cfg(spec))
    assert r.trades.empty and r.skipped["gap_through_stop"] == 1


def test_equity_accounting_matches_trades(spec):
    df = random_walk(3000, seed=7)
    r = run_backtest(df, P, cfg(spec))
    assert len(r.trades) > 5
    assert r.equity.iloc[-1] == pytest.approx(10_000 + r.trades["net_pnl"].sum(), rel=1e-9)


def test_leverage_never_exceeds_cap(spec):
    df = random_walk(3000, seed=11)
    r = run_backtest(df, P.with_(atr_stop_mult=0.2), cfg(spec, max_leverage=2.0))
    lev = r.trades["size"] * r.trades["entry_price"] / 10_000
    # capital cambia con el tiempo; con margen holgado nunca debe superar ~2x del capital inicial*crecimiento
    assert (lev <= 2.0 * r.equity.max() / 10_000 + 1e-9).all()


def test_daily_halt_blocks_rest_of_day(spec):
    df = random_walk(4000, seed=5, vol=0.02)
    r = run_backtest(df, P, cfg(spec, max_daily_loss=0.005))
    t = r.trades
    assert not t.empty
    for _, loser in t[t["exit_reason"] == "daily_loss_halt"].iterrows():
        same_day_later = t[(t["signal_time"] > loser["exit_time"]) &
                           (t["signal_time"].dt.date == loser["exit_time"].date())]
        assert same_day_later.empty


def test_metrics_basic():
    idx = pd.date_range("2024-01-01", periods=5, freq="1D", tz="UTC")
    eq = pd.Series([100, 110, 99, 120, 108], index=idx, dtype=float)
    assert max_drawdown(eq) == pytest.approx(99 / 110 - 1)
    trades = pd.DataFrame({"net_pnl": [10, -5, 20, -5], "r_multiple": [1, -0.5, 2, -0.5],
                           "bars_held": [1, 1, 1, 1], "fees": [0.1] * 4, "funding": [0.0] * 4,
                           "side": ["long", "short", "long", "short"]})
    m = compute_metrics(eq, trades, 100)
    assert m["win_rate"] == 0.5
    assert m["profit_factor"] == pytest.approx(3.0)
    assert m["expectativa_usd"] == pytest.approx(5.0)
    assert m["num_operaciones"] == 4
    assert m["racha_perdedora_max"] == 1


def test_walkforward_runs_out_of_sample(spec):
    df = random_walk(24 * 30 * 9, seed=2)
    wf = WalkForwardCfg(train_months=3, test_months=1, holdout_months=1, min_train_trades=1,
                        grid={"entry_lookback": [20, 40], "exit_lookback": [10], "atr_stop_mult": [2.0]})
    res = walk_forward(df, P, cfg(spec), wf)
    assert not res.windows.empty
    # los tramos de test no se solapan y terminan antes del holdout
    assert (res.windows["test_fin"] <= res.holdout_start).all()
    assert (res.windows["test_inicio"].diff().dropna() > pd.Timedelta(0)).all()
    if not res.oos_trades.empty:
        assert res.oos_trades["signal_time"].min() >= res.windows["test_inicio"].min()
        assert res.oos_trades["signal_time"].max() < res.holdout_start


def test_no_edge_on_martingale_without_costs():
    """Sobre un paseo aleatorio sin deriva y sin costes, la expectativa debe ser ~0 R.

    Si saliera claramente positiva, habría sesgo de anticipación o ejecuciones
    optimistas en el motor. Es la prueba de humo más importante del backtest.
    """
    from conftest import path_candles
    from xrpbot.models import InstrumentSpec

    spec = InstrumentSpec("PF_XRPUSD", 1.0, 0.00001, 1.0, 1.0, 0.01)
    c0 = BacktestConfig(initial_capital=10_000, spec=spec, taker_fee=0, maker_fee=0, slippage_bps=0,
                        max_daily_loss=0.99)
    rs = []
    for seed in range(12):
        t = run_backtest(path_candles(24 * 365, seed=seed), P, c0).trades
        sign = np.where(t["side"] == "long", 1, -1)
        rs.append(sign * (t["exit_price"] - t["entry_price"]) / (t["entry_price"] - t["initial_stop"]).abs())
    r = pd.concat(rs)
    se = r.std() / np.sqrt(len(r))
    assert len(r) > 2000
    assert abs(r.mean()) < 3.5 * se, f"expectativa {r.mean():.4f}R con error típico {se:.4f}"


def test_costs_make_martingale_negative(spec):
    from conftest import path_candles
    rs = []
    for seed in range(6):
        t = run_backtest(path_candles(24 * 365, seed=seed), P, cfg(spec)).trades
        rs.append(t["r_multiple"])
    assert pd.concat(rs).mean() < 0


def test_margin_costs_open_fee_and_rollover(spec):
    """Perfil de margen: comisión de apertura y rollover cada 4 h completas sobre el nocional."""
    df = scenario([(0.52, 0.521, 0.519, 0.52)] * 9)   # 9 velas con la posición abierta, sin salida
    base = run_backtest(df, P, cfg(spec, taker_fee=0.004, maker_fee=0.0025)).trades.iloc[0]
    m = run_backtest(df, P, cfg(spec, taker_fee=0.004, maker_fee=0.0025, open_fee=0.0002,
                                rollover_fee_per_4h=0.0002)).trades.iloc[0]
    assert m["size"] == base["size"]
    extra = m["fees"] - base["fees"]
    size = m["size"]
    expected = size * m["entry_price"] * 0.0002 + 2 * size * 0.52 * 0.0002   # apertura + 2 rollovers (4 h y 8 h)
    assert extra == pytest.approx(expected, rel=1e-6)
    assert m["funding"] == 0
