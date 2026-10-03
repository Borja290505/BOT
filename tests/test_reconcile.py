from xrpbot.execution.oco import Alert, CancelOrder, PlaceProtection, ResizeOrder, TradeState
from xrpbot.models import OpenOrder, Position, Side
from xrpbot.state.reconcile import (AdoptExchangeSize, MarkCancelled, MarkClosed, MarkEntryFilled,
                                    PlaceEmergencyStop, reconcile)

SYM = "PF_XRPUSD"
TID = "xrpusd-20260105T1000-L"


def open_trade(size=1000):
    return TradeState(trade_id=TID, side=Side.LONG, status="open", target_size=size, stop_price=0.48,
                      tp_price=0.6, filled_size=size, entry_avg=0.5, entry_cli=f"{TID}-entry-0",
                      sl_cli=f"{TID}-sl-1", tp_cli=f"{TID}-tp-2")


def order(cli, otype="stp", size=1000, reduce_only=True, side="sell", oid=None):
    return OpenOrder(order_id=oid or f"id-{cli}", cli_ord_id=cli, symbol=SYM, side=side, order_type=otype,
                     size=size, reduce_only=reduce_only)


POS = Position(SYM, Side.LONG, 1000, 0.5)


def test_all_consistent_is_clean():
    st = open_trade()
    plan = reconcile(symbol=SYM, local=st, positions=[POS],
                     open_orders=[order(st.sl_cli), order(st.tp_cli, "post")])
    assert plan.clean


def test_missing_stop_is_replaced():
    st = open_trade()
    plan = reconcile(symbol=SYM, local=st, positions=[POS], open_orders=[order(st.tp_cli, "post")])
    assert not plan.freeze
    assert PlaceProtection("sl", 1000, 0.48) in plan.actions


def test_closed_while_offline():
    st = open_trade()
    plan = reconcile(symbol=SYM, local=st, positions=[], open_orders=[order(st.tp_cli, "post")])
    assert any(isinstance(a, MarkClosed) for a in plan.actions)
    assert any(isinstance(a, CancelOrder) and a.cli_ord_id == st.tp_cli for a in plan.actions)
    assert not plan.freeze


def test_unknown_position_freezes_and_protects():
    plan = reconcile(symbol=SYM, local=None, positions=[POS], open_orders=[])
    assert plan.freeze
    assert PlaceEmergencyStop(Side.LONG, 1000) in plan.actions
    assert any(isinstance(a, Alert) and a.level == "critical" for a in plan.actions)


def test_unknown_position_with_existing_stop_not_double_protected():
    plan = reconcile(symbol=SYM, local=None, positions=[POS], open_orders=[order("manual-stop")])
    assert plan.freeze
    assert not any(isinstance(a, PlaceEmergencyStop) for a in plan.actions)


def test_unknown_position_protection_can_be_disabled():
    plan = reconcile(symbol=SYM, local=None, positions=[POS], open_orders=[], protect_unknown=False)
    assert plan.freeze and not any(isinstance(a, PlaceEmergencyStop) for a in plan.actions)


def test_side_mismatch_freezes():
    plan = reconcile(symbol=SYM, local=open_trade(), positions=[Position(SYM, Side.SHORT, 1000, 0.5)],
                     open_orders=[])
    assert plan.freeze


def test_size_mismatch_adopts_exchange_and_resizes():
    st = open_trade()
    plan = reconcile(symbol=SYM, local=st, positions=[Position(SYM, Side.LONG, 600, 0.5)],
                     open_orders=[order(st.sl_cli), order(st.tp_cli, "post")])
    assert AdoptExchangeSize(600, 0.5) in plan.actions
    assert ResizeOrder("sl", st.sl_cli, 600) in plan.actions
    assert ResizeOrder("tp", st.tp_cli, 600) in plan.actions


def test_orphan_orders_without_trade_are_cancelled():
    plan = reconcile(symbol=SYM, local=None, positions=[], open_orders=[order("viejo-sl-1")])
    assert plan.actions == [CancelOrder(order_id="id-viejo-sl-1", cli_ord_id="viejo-sl-1",
                                        why="orden huérfana sin operación activa")]


def test_foreign_and_stale_orders_cancelled_during_trade():
    st = open_trade()
    plan = reconcile(symbol=SYM, local=st, positions=[POS],
                     open_orders=[order(st.sl_cli), order(st.tp_cli, "post"), order("ajena"),
                                  order(f"{TID}-sl-0")])
    cancelled = {a.cli_ord_id for a in plan.actions if isinstance(a, CancelOrder)}
    assert cancelled == {"ajena", f"{TID}-sl-0"}


def test_other_symbols_ignored():
    other = OpenOrder("x", "x", "PF_XBTUSD", "buy", "lmt", 1)
    plan = reconcile(symbol=SYM, local=None, positions=[Position("PF_XBTUSD", Side.LONG, 1, 1)],
                     open_orders=[other])
    assert plan.clean


def test_pending_entry_resolution():
    st = open_trade()
    st.status, st.filled_size = "entry_pending", 0
    plan = reconcile(symbol=SYM, local=st, positions=[POS], open_orders=[])
    assert MarkEntryFilled(1000, 0.5) in plan.actions
    plan = reconcile(symbol=SYM, local=st, positions=[], open_orders=[])
    assert any(isinstance(a, MarkCancelled) for a in plan.actions)
    plan = reconcile(symbol=SYM, local=st, positions=[], open_orders=[], entry_order_live=True)
    assert plan.clean
