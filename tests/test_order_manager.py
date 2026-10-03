import pytest

from xrpbot.config import ExecutionCfg
from xrpbot.exchange.fees import Fees
from xrpbot.exchange.rest import AmbiguousOrderResult
from xrpbot.execution.broker import PaperBroker
from xrpbot.execution.oco import TradeState
from xrpbot.execution.order_manager import OrderManager
from xrpbot.models import MarketSnapshot, OrderAck, OrderRequest, Side
from xrpbot.state.db import Database
from xrpbot.state.reconcile import reconcile

FEES = Fees(0.0002, 0.0005)
TID = "xrpusd-20260105T1000-L"


def snap(price, ts=1000.0):
    return MarketSnapshot("PF_XRPUSD", price - 0.0001, price + 0.0001, price, price, ts)


@pytest.fixture
def env(tmp_path, spec):
    broker = PaperBroker(spec, FEES, 10_000, slippage_bps=0)
    broker.on_market(snap(0.5))
    db = Database(tmp_path / "t.sqlite")
    alerts = []
    om = OrderManager(broker, db, spec, FEES, ExecutionCfg(), notify=lambda lvl, m: alerts.append((lvl, m)))
    return broker, db, om, alerts


def new_trade(db):
    st = TradeState(trade_id=TID, side=Side.LONG, status="entry_pending", target_size=1000, stop_price=0.48,
                    tp_price=0.58, tp_r=4.0, entry_cli=f"{TID}-entry-0")
    db.set_active_trade(st)
    return st


async def enter(broker, om, st):
    req = OrderRequest("PF_XRPUSD", "buy", "ioc", 1000, st.entry_cli, limit_price=0.51, role="entry")
    ack = await om.submit(req, st.trade_id)
    await om.process_fills(st, await broker.recent_fills())
    return ack


async def test_full_lifecycle_stop_cancels_tp(env):
    broker, db, om, alerts = env
    st = new_trade(db)
    ack = await enter(broker, om, st)
    assert ack.accepted and st.status == "open"
    orders = {o.cli_ord_id: o for o in await broker.open_orders()}
    sl, tp = orders[st.sl_cli], orders[st.tp_cli]
    assert sl.order_type == "stp" and sl.reduce_only and sl.side == "sell" and sl.size == 1000
    assert tp.order_type == "post" and tp.reduce_only and tp.limit_price == pytest.approx(0.5001 + 4 * (0.5001 - 0.48))

    broker.on_market(snap(0.47))            # salta el stop (mark <= 0.48)
    await om.process_fills(st, await broker.recent_fills())
    assert st.status == "closed" and st.exit_reason == "stop"
    assert await broker.open_orders() == []  # el TP se canceló (OCO)
    assert await broker.positions() == []
    assert db.active_trade() is None
    assert st.realized_pnl < 0 and st.fees > 0


async def test_take_profit_path(env):
    broker, db, om, _ = env
    st = new_trade(db)
    await enter(broker, om, st)
    broker.on_market(snap(0.60))
    await om.process_fills(st, await broker.recent_fills())
    assert st.status == "closed" and st.exit_reason == "take_profit"
    assert await broker.open_orders() == []


async def test_reprocessing_fills_is_idempotent(env):
    broker, db, om, _ = env
    st = new_trade(db)
    await enter(broker, om, st)
    n_orders = len(await broker.open_orders())
    await om.process_fills(st, await broker.recent_fills())
    await om.process_fills(st, await broker.recent_fills())
    assert len(await broker.open_orders()) == n_orders
    assert st.filled_size == 1000


async def test_trailing_stop_edits_order(env):
    broker, db, om, _ = env
    st = new_trade(db)
    await enter(broker, om, st)
    await om.move_stop(st, 0.495)
    sl = next(o for o in await broker.open_orders() if o.cli_ord_id == st.sl_cli)
    assert sl.stop_price == pytest.approx(0.495) and st.stop_price == pytest.approx(0.495)


async def test_flatten_closes_and_cancels(env):
    broker, db, om, _ = env
    st = new_trade(db)
    await enter(broker, om, st)
    await om.flatten(st, reason="kill")
    assert await broker.open_orders() == []
    await om.process_fills(st, await broker.recent_fills())
    assert await broker.positions() == [] and st.status == "closed"


async def test_rejected_stop_triggers_flatten(env, spec):
    broker, db, om, alerts = env

    real_send = broker.send

    async def send(req):
        if req.role == "sl":
            return OrderAck(None, req.cli_ord_id, "wouldCauseLiquidation")
        return await real_send(req)

    broker.send = send
    st = new_trade(db)
    await enter(broker, om, st)
    assert await broker.positions() == []         # nunca queda una posición sin stop
    assert any(lvl == "critical" for lvl, _ in alerts)


async def test_ambiguous_send_does_not_duplicate(env):
    broker, db, om, _ = env
    real_send = broker.send
    calls = {"n": 0}

    async def flaky(req):
        calls["n"] += 1
        if calls["n"] == 1:
            await real_send(req)                  # la orden SÍ llega...
            raise AmbiguousOrderResult("timeout")  # ...pero la respuesta se pierde
        return await real_send(req)

    await real_send(OrderRequest("PF_XRPUSD", "buy", "mkt", 1000, "pre", role="entry"))
    broker.send = flaky
    st = new_trade(db)
    req = OrderRequest("PF_XRPUSD", "sell", "stp", 1000, f"{TID}-sl-1", stop_price=0.3, reduce_only=True,
                       trigger_signal="mark", role="sl")
    ack = await om.submit(req, st.trade_id)
    assert ack.accepted
    stops = [o for o in await broker.open_orders() if o.order_type == "stp"]
    assert len(stops) == 1


async def test_restart_reconcile_restores_missing_stop(env, tmp_path, spec):
    broker, db, om, _ = env
    st = new_trade(db)
    await enter(broker, om, st)
    await broker.cancel(cli_ord_id=st.sl_cli)     # alguien/algo borró el stop
    st = db.active_trade()                         # "reinicio": estado desde SQLite
    plan = reconcile(symbol="PF_XRPUSD", local=st, positions=await broker.positions(),
                     open_orders=await broker.open_orders())
    await om.apply(st, plan.actions)
    stops = [o for o in await broker.open_orders() if o.order_type == "stp"]
    assert len(stops) == 1 and stops[0].cli_ord_id == st.sl_cli


async def test_db_export(env, tmp_path):
    broker, db, om, _ = env
    st = new_trade(db)
    await enter(broker, om, st)
    broker.on_market(snap(0.47))
    await om.process_fills(st, await broker.recent_fills())
    paths = db.export_csv(tmp_path / "csv")
    trades_csv = (tmp_path / "csv" / "trades.csv").read_text()
    assert "realized_pnl" in trades_csv and TID in trades_csv
    assert len(paths) == 5
