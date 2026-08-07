"""Market data provider interface.

Phase 1 ships the protocol and an in-memory implementation used by tests and
backtests. Live providers arrive in a later phase; when they do they implement
:class:`MarketDataProvider` and nothing downstream changes.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from domain.values import Symbol
from market_data.models import MarketSnapshot


@runtime_checkable
class MarketDataProvider(Protocol):
    """Source of normalised snapshots.

    Implementations must be *referentially transparent for a given ``as_of``*:
    asking twice for the same instant returns the same snapshot. A provider that
    cannot promise that (because it proxies a live feed) belongs behind a
    recorder that persists what it saw under ``data/raw/``.
    """

    def get_snapshot(self, symbol: Symbol, as_of: datetime) -> MarketSnapshot | None:
        """Return the observation for ``symbol`` at exactly ``as_of``."""
        ...

    def history(self, symbol: Symbol, start: datetime, end: datetime) -> Sequence[MarketSnapshot]:
        """Return snapshots in ``[start, end]``, ordered oldest first."""
        ...


class InMemoryMarketDataProvider:
    """Deterministic provider backed by an explicit list of snapshots."""

    def __init__(self, snapshots: Iterable[MarketSnapshot]) -> None:
        ordered = sorted(snapshots, key=lambda s: (s.symbol, s.as_of))
        self._by_key: dict[tuple[str, datetime], MarketSnapshot] = {
            (s.symbol, s.as_of): s for s in ordered
        }
        self._ordered: tuple[MarketSnapshot, ...] = tuple(ordered)

    def get_snapshot(self, symbol: Symbol, as_of: datetime) -> MarketSnapshot | None:
        return self._by_key.get((symbol, as_of))

    def history(self, symbol: Symbol, start: datetime, end: datetime) -> Sequence[MarketSnapshot]:
        return tuple(s for s in self._ordered if s.symbol == symbol and start <= s.as_of <= end)
