import pytest

from xrpbot.execution.oco import (Alert, CancelOrder, PlaceProtection, ResizeOrder, TradeState,
                                  make_trade_id, on_fill, role_of)
from xrpbot.models import Fill, Side


def state(**kw):
    base = dict(trade_id="xrpusd-20260105T1000-L", side=Side.LONG, status="entry_pending",
                target_size=1000, stop_price=0.48, tp_price=0.60, entry_cli="xrpusd-20260105T1000-L-entry-0")
    base.update(kw)
    return TradeState(**base)


def fill(fid, cli, size, price, side="buy"):
    return Fill(fill_id=fid, order_id="o" + fid, cli_ord_id=cli, symbol="PF_XRPUSD", side=side,
                size=size, price=price, time=1.0)


def test_trade_id_is_deterministic():
    a = make_trade_id("PF_XRPUSD", "2026-01-05T10:00:00+00:00", Side.LONG)
    assert a == make_trade_id("PF_XRPUSD", "2026-01-05T10:00:00+00:00", Side.LONG)
    assert a != make_trade_id("PF_XRPUSD", "2026-01-05T11:00:00+00:00", Side.LONG)
    assert a.endswith("-L") and len(a) < 40


def test_entry_fill_places_sl_and_tp():
    st = state()
    acts = on_fill(st, fill("1", st.entry_cli, 1000, 0.5))
    assert st.status == "open" and st.open_size == 1000 and st.entry_avg == 0.5
    assert PlaceProtection("sl", 1000, 0.48) in acts
    assert any(isinstance(a, PlaceProtection) and a.role == "tp" for a in acts)


def test_tp_recomputed_from_real_entry():
    st = state(tp_r=4.0)
    acts = on_fill(st, fill("1", st.entry_cli, 1000, 0.50))
    assert st.tp_price == pytest.approx(0.50 + 4 * 0.02)
    assert PlaceProtection("tp", 1000, st.tp_price) in acts


def test_partial_entry_then_more_resizes_protection():
    st = state()
    on_fill(st, fill("1", st.entry_cli, 400, 0.50))
    st.sl_cli, st.tp_cli = st.next_cli("sl"), st.next_cli("tp")
    acts = on_fill(st, fill("2", st.entry_cli, 600, 0.51))
    assert st.entry_avg == pytest.approx((400 * 0.5 + 600 * 0.51) / 1000)
    assert ResizeOrder("sl", st.sl_cli, 1000) in acts and ResizeOrder("tp", st.tp_cli, 1000) in acts


def opened():
    st = state()
    on_fill(st, fill("1", st.entry_cli, 1000, 0.50))
    st.sl_cli, st.tp_cli = st.next_cli("sl"), st.next_cli("tp")
    return st


def test_stop_fill_cancels_tp():
    st = opened()
    acts = on_fill(st, fill("2", st.sl_cli, 1000, 0.48, "sell"))
    assert st.status == "closed" and st.exit_reason == "stop"
    assert acts == [CancelOrder(cli_ord_id=st.tp_cli, why="OCO: sl ejecutado")]
    assert st.realized_pnl == pytest.approx(-20.0)


def test_tp_fill_cancels_stop():
    st = opened()
    acts = on_fill(st, fill("2", st.tp_cli, 1000, 0.60, "sell"))
    assert st.status == "closed" and st.exit_reason == "take_profit"
    assert acts == [CancelOrder(cli_ord_id=st.sl_cli, why="OCO: tp ejecutado")]
    assert st.realized_pnl == pytest.approx(100.0)


def test_partial_tp_resizes_stop():
    st = opened()
    acts = on_fill(st, fill("2", st.tp_cli, 300, 0.60, "sell"))
    assert st.status == "open" and st.open_size == 700
    assert acts == [ResizeOrder("sl", st.sl_cli, 700)]


def test_duplicate_fill_ignored():
    st = opened()
    on_fill(st, fill("2", st.tp_cli, 300, 0.60, "sell"))
    assert on_fill(st, fill("2", st.tp_cli, 300, 0.60, "sell")) == []
    assert st.open_size == 700


def test_unknown_order_fill_alerts():
    st = opened()
    acts = on_fill(st, fill("9", "otra-cosa", 10, 0.5))
    assert isinstance(acts[0], Alert)


def test_short_pnl_sign():
    st = state(side=Side.SHORT, stop_price=0.52, tp_price=0.40)
    on_fill(st, fill("1", st.entry_cli, 1000, 0.50, "sell"))
    st.sl_cli, st.tp_cli = st.next_cli("sl"), st.next_cli("tp")
    on_fill(st, fill("2", st.tp_cli, 1000, 0.40))
    assert st.realized_pnl == pytest.approx(100.0)


def test_roles_and_serialization():
    st = opened()
    assert role_of(st, st.entry_cli) == "entry"
    assert role_of(st, st.sl_cli) == "sl"
    assert role_of(st, f"{st.trade_id}-sl-99") == "sl"      # stop reemplazado por trailing
    assert role_of(st, f"{st.trade_id}-flat-3") == "flatten"
    assert TradeState.from_dict(st.to_dict()) == st


def test_entry_fill_after_reconcile_adoption_not_double_counted():
    st = state()
    # la reconciliación vio la posición antes que las ejecuciones
    st.status, st.filled_size, st.entry_avg = "open", 1000, 0.5
    acts = on_fill(st, fill("1", st.entry_cli, 1000, 0.5))
    assert st.filled_size == 1000
    assert all(isinstance(a, Alert) for a in acts)
