"""Signal computation interface."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from market_data.models import MarketSnapshot
from signals.models import Signal


@runtime_checkable
class SignalComputer(Protocol):
    """Turns a window of snapshots into a signal.

    Implementations must be pure: same window in, same value out, no clock reads
    and no network calls.
    """

    @property
    def name(self) -> str:
        """Stable snake_case identifier, e.g. ``atr_14``."""
        ...

    @property
    def computation_version(self) -> str:
        """Bumped whenever the maths changes. See ADR-004."""
        ...

    def compute(self, window: Sequence[MarketSnapshot]) -> Signal | None:
        """Return the signal, or ``None`` when the window is too short."""
        ...
