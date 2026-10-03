import pytest

from xrpbot.exchange.rate_limiter import CostRateLimiter, cost_of


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_costs():
    assert cost_of("sendorder") == 10
    assert cost_of("cancelallorders") == 25


def test_bucket_blocks_and_refills():
    clk = FakeClock()
    lim = CostRateLimiter(capacity=500, window_s=10, safety=0.8, clock=clk)  # 400 útiles
    for _ in range(40):
        assert lim.try_acquire(10) == 0
    wait = lim.try_acquire(10)
    assert wait == pytest.approx(10 / 40)  # 40 puntos/s
    clk.t += wait
    assert lim.try_acquire(10) == 0


def test_cost_above_capacity_rejected():
    with pytest.raises(ValueError):
        CostRateLimiter(capacity=10, safety=1.0).try_acquire(11)
