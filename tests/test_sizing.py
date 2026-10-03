import pytest

from xrpbot.models import InstrumentSpec
from xrpbot.risk.sizing import position_size

KW = dict(risk_per_trade=0.01, max_leverage=2.0, taker_fee=0.0, slippage_bps=0.0)


def test_risk_limited(spec):
    # 10.000 USD, 1 % = 100 USD. Stop a 0.05 -> 2000 contratos; nocional 1000 USD (0.1x)
    r = position_size(capital_usd=10_000, entry_price=0.50, stop_price=0.45, spec=spec, **KW)
    assert r.size == 2000
    assert r.limited_by == "risk"
    assert r.risk_usd == pytest.approx(100)
    assert r.effective_leverage == pytest.approx(0.1)


def test_leverage_cap(spec):
    # Stop muy cerca (0.1 %) => por riesgo saldrían 200.000 contratos; el 2x lo limita a 40.000
    r = position_size(capital_usd=10_000, entry_price=0.50, stop_price=0.4995, spec=spec, **KW)
    assert r.limited_by == "leverage"
    assert r.size == 40_000
    assert r.effective_leverage == pytest.approx(2.0)
    assert r.risk_usd < 100   # con el tope de apalancamiento se arriesga MENOS del 1 %


def test_costs_reduce_size(spec):
    no_cost = position_size(capital_usd=10_000, entry_price=0.5, stop_price=0.45, spec=spec, **KW)
    cost = position_size(capital_usd=10_000, entry_price=0.5, stop_price=0.45, spec=spec,
                         risk_per_trade=0.01, max_leverage=2, taker_fee=0.0005, slippage_bps=5)
    assert cost.size < no_cost.size
    assert cost.risk_usd <= 100 + 1e-9


def test_rounds_down_never_up():
    spec = InstrumentSpec("PF_XRPUSD", 1.0, 0.0001, 10.0, 10.0, 0.01)
    r = position_size(capital_usd=10_000, entry_price=0.5, stop_price=0.47, spec=spec, **KW)
    # 100 / 0.03 = 3333.3 -> 3330 (múltiplo de 10, hacia abajo)
    assert r.size == 3330
    assert r.risk_usd <= 100


def test_below_minimum_rejected():
    spec = InstrumentSpec("PF_XRPUSD", 1.0, 0.0001, 1000.0, 1000.0, 0.01)
    r = position_size(capital_usd=100, entry_price=0.5, stop_price=0.45, spec=spec, **KW)
    assert not r.ok and r.limited_by == "below_min"


def test_max_position_size():
    spec = InstrumentSpec("PF_XRPUSD", 1.0, 0.0001, 1.0, 1.0, 0.01, max_position_size=500)
    r = position_size(capital_usd=10_000, entry_price=0.5, stop_price=0.45, spec=spec, **KW)
    assert r.size == 500 and r.limited_by == "max_position"


@pytest.mark.parametrize("entry,stop", [(0.5, 0.5), (0.5, 0.49999), (0, 0.4), (0.5, -1)])
def test_invalid_inputs(spec, entry, stop):
    assert not position_size(capital_usd=10_000, entry_price=entry, stop_price=stop, spec=spec, **KW).ok


def test_short_symmetric(spec):
    long_ = position_size(capital_usd=10_000, entry_price=0.5, stop_price=0.45, spec=spec, **KW)
    short = position_size(capital_usd=10_000, entry_price=0.5, stop_price=0.55, spec=spec, **KW)
    assert long_.size == short.size


def test_contract_size_scaling():
    spec = InstrumentSpec("PF_X", 10.0, 0.0001, 1.0, 1.0, 0.01)
    r = position_size(capital_usd=10_000, entry_price=0.5, stop_price=0.45, spec=spec, **KW)
    assert r.size == 200
