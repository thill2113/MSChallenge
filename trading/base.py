"""Shared abstractions for exchange clients.

Every exchange integration implements ExchangeClient so agents can be wired
to any venue interchangeably. Each agent owns its own client instance —
clients hold per-instance sessions and rate limiters, so no state is shared
across agents/threads.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Balance:
    asset: str
    free: Decimal
    locked: Decimal = Decimal("0")

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


@dataclass
class OrderResult:
    exchange: str
    order_id: str
    symbol: str
    side: Side
    quantity: Decimal
    limit_price: Decimal
    status: str
    raw: dict = field(default_factory=dict, repr=False)


class TokenBucketRateLimiter:
    """Thread-safe token bucket.

    Each client instance gets its own bucket sized to the venue's documented
    limit, with headroom left for retries (default 80% utilization target).
    """

    def __init__(self, rate_per_second: float, burst: Optional[int] = None,
                 utilization: float = 0.8):
        self._rate = rate_per_second * utilization
        self._capacity = burst if burst is not None else max(1, int(self._rate))
        self._tokens = float(self._capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, cost: float = 1.0) -> None:
        """Block until `cost` tokens are available, then consume them."""
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self._capacity,
                                   self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                wait = (cost - self._tokens) / self._rate
            time.sleep(wait)


class ExchangeClient(ABC):
    """Common interface implemented by every venue adapter."""

    name: str

    @abstractmethod
    def get_balances(self) -> list[Balance]:
        """Return all non-zero account balances."""

    @abstractmethod
    def place_limit_order(self, symbol: str, side: Side, quantity: Decimal,
                          limit_price: Decimal) -> OrderResult:
        """Submit a spot limit order (good-til-canceled)."""

    @abstractmethod
    def close(self) -> None:
        """Release network resources (sessions, sockets)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
