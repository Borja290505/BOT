from datetime import datetime, timedelta, timezone

import pytest

from xrpbot.models import Side
from xrpbot.risk.limits import (DailyLossGuard, DictStore, estimate_liquidation_price,
                                liquidation_is_safe)

T0 = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)


def test_daily_guard_trips_at_limit():
    g = DailyLossGuard(DictStore(), 0.03)
    assert not g.check(10_000, T0).halted
    assert not g.check(9_750, T0 + timedelta(hours=1)).halted   # -2.5 %
    st = g.check(9_700, T0 + timedelta(hours=2))                  # -3 %
    assert st.halted and st.just_tripped
    assert "3.00%" in st.reason


def test_daily_guard_requires_manual_rearm_even_next_day():
    store = DictStore()
    g = DailyLossGuard(store, 0.03)
    g.check(10_000, T0)
    g.check(9_600, T0)
    nxt = g.check(9_600, T0 + timedelta(days=1))
    assert nxt.halted and not nxt.just_tripped
    # persiste: una instancia nueva (reinicio del bot) sigue detenida
    assert DailyLossGuard(store, 0.03).halted
    g.rearm()
    st = g.check(9_600, T0 + timedelta(days=1))
    assert not st.halted and st.day_start_equity == 9_600


def test_daily_guard_resets_reference_at_utc_midnight():
    g = DailyLossGuard(DictStore(), 0.03)
    g.check(10_000, datetime(2026, 1, 5, 23, 59, tzinfo=timezone.utc))
    g.check(9_800, datetime(2026, 1, 5, 23, 59, 30, tzinfo=timezone.utc))
    st = g.check(9_800, datetime(2026, 1, 6, 0, 0, 1, tzinfo=timezone.utc))
    assert st.day_start_equity == 9_800 and st.loss_pct == 0
    assert not g.check(9_600, datetime(2026, 1, 6, 1, tzinfo=timezone.utc)).halted  # -2.04 % del nuevo día


def test_profit_never_trips():
    g = DailyLossGuard(DictStore(), 0.03)
    g.check(10_000, T0)
    assert not g.check(12_000, T0).halted


def test_isolated_liquidation_2x():
    liq = estimate_liquidation_price(side=Side.LONG, entry=0.5, size=40_000, contract_size=1,
                                     maintenance_margin=0.01, margin_mode="isolated",
                                     collateral_usd=10_000, isolated_leverage=2)
    assert liq == pytest.approx(0.5 * (1 - 0.49))
    liq_s = estimate_liquidation_price(side=Side.SHORT, entry=0.5, size=40_000, contract_size=1,
                                       maintenance_margin=0.01, margin_mode="isolated",
                                       collateral_usd=10_000, isolated_leverage=2)
    assert liq_s == pytest.approx(0.5 * 1.49)


def test_cross_liquidation():
    # 10.000 USD de equity, 40.000 XRP a 0.5 (2x): liq largo = (20000-10000)/(40000*0.99)
    liq = estimate_liquidation_price(side=Side.LONG, entry=0.5, size=40_000, contract_size=1,
                                     maintenance_margin=0.01, margin_mode="cross", collateral_usd=10_000)
    assert liq == pytest.approx(10_000 / 39_600)
    # posición pequeña: no hay liquidación por precio en largo
    assert estimate_liquidation_price(side=Side.LONG, entry=0.5, size=100, contract_size=1,
                                      maintenance_margin=0.01, margin_mode="cross",
                                      collateral_usd=10_000) == 0.0


def test_liquidation_safety():
    ok, _ = liquidation_is_safe(side=Side.LONG, entry=0.5, stop=0.45, liq_price=0.25, min_multiple=3)
    assert ok
    ok, why = liquidation_is_safe(side=Side.LONG, entry=0.5, stop=0.45, liq_price=0.40, min_multiple=3)
    assert not ok and "3" in why
    ok, _ = liquidation_is_safe(side=Side.LONG, entry=0.5, stop=0.45, liq_price=0.46, min_multiple=3)
    assert not ok
    ok, _ = liquidation_is_safe(side=Side.SHORT, entry=0.5, stop=0.55, liq_price=0.75, min_multiple=3)
    assert ok
