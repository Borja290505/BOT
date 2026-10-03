import pytest

from xrpbot.exchange.fees import FeeParseError, select_fees
from xrpbot.exchange.instruments import InstrumentNotAvailable, parse_instrument

# Estructura según la documentación de /instruments; VALORES SINTÉTICOS.
INSTRUMENTS = [
    {"symbol": "PF_XBTUSD", "type": "flexible_futures", "tradeable": True, "tickSize": 1,
     "contractSize": 1, "contractValuePrecision": 4,
     "marginLevels": [{"numNonContractUnits": 0, "initialMargin": 0.02, "maintenanceMargin": 0.01}]},
    {"symbol": "PF_XRPUSD", "type": "flexible_futures", "tradeable": True, "tickSize": 0.0001,
     "contractSize": 1, "contractValuePrecision": 0, "maxPositionSize": 1000000,
     "feeScheduleUid": "fee-1", "postOnly": False,
     "marginLevels": [{"numNonContractUnits": 0, "initialMargin": 0.04, "maintenanceMargin": 0.02}]},
]


def test_parse_xrp():
    spec = parse_instrument(INSTRUMENTS, "pf_xrpusd")
    assert spec.symbol == "PF_XRPUSD"
    assert spec.tick_size == 0.0001
    assert spec.size_step == 1.0 and spec.min_size == 1.0
    assert spec.maintenance_margin == 0.02 and spec.initial_margin == 0.04
    assert spec.max_position_size == 1_000_000


def test_precision_to_step():
    spec = parse_instrument(INSTRUMENTS, "PF_XBTUSD")
    assert spec.size_step == pytest.approx(0.0001)


def test_missing_or_not_tradeable():
    with pytest.raises(InstrumentNotAvailable):
        parse_instrument(INSTRUMENTS, "PF_DOGEUSD")
    bad = [dict(INSTRUMENTS[1], tradeable=False)]
    with pytest.raises(InstrumentNotAvailable):
        parse_instrument(bad, "PF_XRPUSD")


def test_rounding(spec):
    assert spec.round_size_down(12.99) == 12
    assert spec.round_price(0.51237) == pytest.approx(0.5124)


SCHEDULES = [{"uid": "fee-1", "name": "Perps", "tiers": [
    {"makerFee": 0.02, "takerFee": 0.05, "usdVolume": 0},
    {"makerFee": 0.015, "takerFee": 0.04, "usdVolume": 100000},
]}]


def test_fee_tier_selection():
    f = select_fees(SCHEDULES, "fee-1", 0)
    assert f.maker == pytest.approx(0.0002) and f.taker == pytest.approx(0.0005)
    f = select_fees(SCHEDULES, "fee-1", 250000)
    assert f.taker == pytest.approx(0.0004)


def test_fee_sanity_check():
    with pytest.raises(FeeParseError):
        select_fees([{"uid": "x", "tiers": [{"makerFee": 2, "takerFee": 5, "usdVolume": 0}]}], "x")
    with pytest.raises(FeeParseError):
        select_fees(SCHEDULES, "otro")
