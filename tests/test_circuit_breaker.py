from xrpbot.config import CircuitBreakerCfg
from xrpbot.models import MarketSnapshot
from xrpbot.risk.circuit_breaker import book_depth_within, entry_block_reasons

CFG = CircuitBreakerCfg()


def snap(**kw):
    base = dict(symbol="PF_XRPUSD", bid=0.4999, ask=0.5001, last=0.5, mark=0.5, ts=1000.0)
    base.update(kw)
    return MarketSnapshot(**base)


def test_normal_market_ok():
    assert entry_block_reasons(snapshot=snap(), now_s=1005, cfg=CFG, atr_ratio=1.0, candles_stale=False) == []


def test_each_condition_blocks():
    r = entry_block_reasons(snapshot=snap(bid=0.49, ask=0.51), now_s=1005, cfg=CFG, atr_ratio=1, candles_stale=False)
    assert any("spread" in x for x in r)
    r = entry_block_reasons(snapshot=snap(), now_s=1100, cfg=CFG, atr_ratio=1, candles_stale=False)
    assert any("desfasado" in x for x in r)
    r = entry_block_reasons(snapshot=snap(), now_s=1005, cfg=CFG, atr_ratio=5, candles_stale=False)
    assert any("volatilidad" in x for x in r)
    r = entry_block_reasons(snapshot=snap(mark=0.51), now_s=1005, cfg=CFG, atr_ratio=1, candles_stale=False)
    assert any("mark/last" in x for x in r)
    r = entry_block_reasons(snapshot=snap(), now_s=1005, cfg=CFG, atr_ratio=1, candles_stale=True)
    assert "velas desfasadas" in r
    r = entry_block_reasons(snapshot=None, now_s=1005, cfg=CFG, atr_ratio=1, candles_stale=False)
    assert "sin datos de ticker" in r
    r = entry_block_reasons(snapshot=snap(bid=0.6, ask=0.5), now_s=1005, cfg=CFG, atr_ratio=1, candles_stale=False)
    assert any("incoherente" in x for x in r)


def test_thin_book():
    s = snap(bid_depth=1000, ask_depth=50_000)
    r = entry_block_reasons(snapshot=s, now_s=1005, cfg=CFG, atr_ratio=1, candles_stale=False, intended_size=1000)
    assert any("libro fino" in x for x in r)


def test_book_depth_within():
    book = {"bids": [[0.499, 100], [0.40, 1e6]], "asks": [[0.501, 200], [0.60, 1e6]]}
    assert book_depth_within(book, 0.5) == (100, 200)
