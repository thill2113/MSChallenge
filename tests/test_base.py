import time
from decimal import Decimal

from trading.base import Balance, TokenBucketRateLimiter


def test_balance_total():
    b = Balance(asset="BTC", free=Decimal("1.5"), locked=Decimal("0.5"))
    assert b.total == Decimal("2.0")


def test_limiter_allows_burst_up_to_capacity():
    lim = TokenBucketRateLimiter(rate_per_second=10, burst=5)
    start = time.monotonic()
    for _ in range(5):
        lim.acquire()
    assert time.monotonic() - start < 0.1


def test_limiter_blocks_beyond_capacity():
    lim = TokenBucketRateLimiter(rate_per_second=10, burst=2, utilization=1.0)
    start = time.monotonic()
    for _ in range(4):
        lim.acquire()
    # 2 burst tokens free, 2 more need refill at 10/s => >= ~0.2s
    assert time.monotonic() - start >= 0.15


def test_limiter_weighted_cost():
    lim = TokenBucketRateLimiter(rate_per_second=100, burst=20, utilization=1.0)
    start = time.monotonic()
    lim.acquire(cost=20)  # drains the bucket
    lim.acquire(cost=10)  # needs 0.1s of refill
    assert time.monotonic() - start >= 0.08
