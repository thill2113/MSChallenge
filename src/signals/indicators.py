"""Deterministic technical indicators.

Pure functions over an ordered window of snapshots. No clock reads, no state,
exact decimal arithmetic throughout — a strategy's reproducibility is only as
good as the arithmetic underneath it, and binary floats would make two runs of
the same backtest disagree in the last digit.

Every function returns ``None`` rather than a guess when the window is too
short. Padding a 200-day average with 40 days of data produces a number that
looks like an answer and is not one.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from market_data.models import MarketSnapshot

PRICE_QUANTUM = Decimal("0.0001")
"""Indicator outputs are quantised to four decimal places.

Chosen so results are stable across platforms and short enough to satisfy the
``Price`` constraint of eight decimal places with room to spare.
"""


def quantize_price(value: Decimal) -> Decimal:
    """Round a derived price to the standard quantum."""
    return value.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)


def simple_moving_average(snapshots: Sequence[MarketSnapshot], period: int) -> Decimal | None:
    """Mean closing price over the last ``period`` snapshots."""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(snapshots) < period:
        return None
    window = snapshots[-period:]
    total = sum((s.last_price for s in window), Decimal(0))
    return quantize_price(total / Decimal(period))


def true_range(current: MarketSnapshot, previous: MarketSnapshot) -> Decimal | None:
    """Wilder's true range for one bar.

    Requires OHLC on both bars; a snapshot carrying only a last price cannot
    express a range, and inventing one from the close alone would understate
    volatility exactly when it matters most.
    """
    if current.bar is None or previous.bar is None:
        return None
    high, low = current.bar.high, current.bar.low
    prior_close = previous.bar.close
    return max(high - low, abs(high - prior_close), abs(low - prior_close))


def average_true_range(snapshots: Sequence[MarketSnapshot], period: int = 14) -> Decimal | None:
    """Simple average of the last ``period`` true ranges.

    Deliberately the simple mean rather than Wilder's smoothing: the simple form
    depends on a bounded window, so a replay starting at any point produces the
    same value. Wilder's recursive form depends on every bar since inception,
    which makes the answer a function of where the backtest happened to start.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if len(snapshots) < period + 1:
        return None
    ranges: list[Decimal] = []
    for current, previous in zip(snapshots[-period:], snapshots[-period - 1 : -1], strict=True):
        value = true_range(current, previous)
        if value is None:
            return None
        ranges.append(value)
    return quantize_price(sum(ranges, Decimal(0)) / Decimal(period))


def donchian_high(snapshots: Sequence[MarketSnapshot], period: int) -> Decimal | None:
    """Highest closing price over the last ``period`` snapshots.

    Closes rather than intraday highs: an intraday high can be a single print in
    a thin book, and a strategy that triggers on it is trading a data artifact.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if len(snapshots) < period:
        return None
    return max(s.last_price for s in snapshots[-period:])


def atr_percent(snapshots: Sequence[MarketSnapshot], period: int = 14) -> Decimal | None:
    """ATR as a fraction of the latest close — a scale-free volatility measure."""
    atr = average_true_range(snapshots, period)
    if atr is None:
        return None
    close = snapshots[-1].last_price
    if close <= 0:
        return None
    return (atr / close).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


__all__ = [
    "PRICE_QUANTUM",
    "atr_percent",
    "average_true_range",
    "donchian_high",
    "quantize_price",
    "simple_moving_average",
    "true_range",
]
