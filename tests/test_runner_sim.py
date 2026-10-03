"""Prueba de integración del bucle en modo sim con una API falsa (sin red)."""
import math
import time

import pytest

import xrpbot.live.runner as runner_mod
from xrpbot.config import load_settings
from xrpbot.models import MarketSnapshot

HOUR = 3600


def fake_candles(breakout: bool):
    last_open = (math.floor(time.time() / HOUR) - 1) * HOUR
    n = 800
    out = []
    for k in range(n):
        t = last_open - (n - 1 - k) * HOUR
        c, h, lo = 0.5, 0.501, 0.499
        if breakout and k == n - 1:
            c, h, lo = 0.52, 0.521, 0.5
        out.append({"time": t * 1000, "open": 0.5, "high": h, "low": lo, "close": c, "volume": 1000})
    return out


class FakeRest:
    breakout = True

    def __init__(self, *a, **kw):
        self.clock_offset_s = 0.0
        self.has_credentials = False

    async def open(self): ...
    async def close(self): ...

    def server_time(self):
        return time.time()

    async def get_instruments(self):
        return [{"symbol": "PF_XRPUSD", "tradeable": True, "tickSize": 0.0001, "contractSize": 1,
                 "contractValuePrecision": 0, "feeScheduleUid": "f",
                 "marginLevels": [{"initialMargin": 0.02, "maintenanceMargin": 0.01}]}]

    async def get_fee_schedules(self):
        return [{"uid": "f", "tiers": [{"makerFee": 0.02, "takerFee": 0.05, "usdVolume": 0}]}]

    async def get_candles(self, *a, **kw):
        return {"candles": fake_candles(self.breakout)}

    async def get_historical_funding(self, symbol):
        return [{"timestamp": "2020-01-01T00:00:00Z", "fundingRate": 0, "relativeFundingRate": 0.00001}]

    async def get_ticker(self, symbol):
        return {"bid": 0.5199, "ask": 0.5201, "last": 0.52, "markPrice": 0.52}


class FakeWS:
    def __init__(self, *a, **kw):
        self.snapshot = MarketSnapshot("PF_XRPUSD", 0.5199, 0.5201, 0.52, 0.52, time.time())

    async def run_forever(self): ...
    def stop(self): ...


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_mod, "KrakenFuturesRest", FakeRest)
    monkeypatch.setattr(runner_mod, "KrakenFuturesWS", FakeWS)
    cfg = tmp_path / "s.yaml"
    cfg.write_text(f"mode: sim\nstorage:\n  db_path: {tmp_path}/db-{{mode}}.sqlite\n  log_dir: {tmp_path}\n")
    s = load_settings(cfg, env_file=None)
    return runner_mod.LiveBot(s)


async def test_breakout_opens_protected_trade(bot):
    await bot.setup()
    bot.ws.snapshot.ts = time.time()
    await bot._feed_paper()
    await bot.on_bar_close()
    st = bot.db.active_trade()
    assert st is not None and st.status == "open" and st.side.value == "long"
    orders = await bot.broker.open_orders()
    assert {o.order_type for o in orders} == {"stp", "post"}
    assert all(o.reduce_only for o in orders)
    # riesgo: tamaño * distancia al stop ~ 1 % de 10.000 (algo menos por costes)
    assert st.target_size * abs(st.entry_avg - st.stop_price) <= 100

    # misma vela otra vez (p. ej. reinicio): no se duplica la entrada
    n = len(await bot.broker.recent_fills())
    await bot.on_bar_close()
    assert len(await bot.broker.recent_fills()) == n

    # el precio cae por debajo del stop -> cierre por OCO
    bot.broker.on_market(MarketSnapshot("PF_XRPUSD", 0.40, 0.4002, 0.40, 0.40, time.time()))
    await bot.poll()
    assert bot.db.active_trade() is None
    assert await bot.broker.open_orders() == []
    assert await bot.broker.positions() == []


async def test_halt_blocks_new_entries(bot):
    await bot.setup()
    await bot._feed_paper()
    bot.guard.halt("prueba")
    await bot.on_bar_close()
    assert bot.db.active_trade() is None
    assert await bot.broker.positions() == []


async def test_reconcile_freezes_on_unknown_position(bot):
    await bot.setup()
    await bot._feed_paper()
    from xrpbot.models import OrderRequest
    await bot.broker.send(OrderRequest("PF_XRPUSD", "buy", "mkt", 100, "manual-1"))
    await bot.reconcile_now()
    assert bot.frozen_reason
    stops = [o for o in await bot.broker.open_orders() if o.order_type == "stp"]
    assert len(stops) == 1     # stop de emergencia
    await bot.on_bar_close()   # congelado: no abre nada nuevo
    assert bot.db.active_trade() is None
