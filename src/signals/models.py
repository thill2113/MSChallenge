"""Signal value objects."""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from domain.base import AuthoritativeModel, FrozenModel
from domain.values import ExactDecimal, Symbol, TimestampUTC


class Signal(AuthoritativeModel):
    """One measurement, attributable to a named and versioned computation.

    ``computation_version`` is mandatory: a signal whose maths changed silently
    would invalidate every backtest that referenced it by name alone (ADR-004).
    """

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "name",
        "computation_version",
        "symbol",
        "as_of",
        "value",
    )

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    computation_version: str = Field(min_length=1, max_length=32)
    symbol: Symbol
    as_of: TimestampUTC
    value: ExactDecimal


class SignalSet(FrozenModel):
    """All signals available to a strategy at a single instant."""

    symbol: Symbol
    as_of: TimestampUTC
    signals: tuple[Signal, ...] = ()

    def get(self, name: str) -> Signal | None:
        """Return the signal named ``name``, or ``None`` if it was not computed."""
        return next((s for s in self.signals if s.name == name), None)

    def require(self, name: str) -> Signal:
        """Return the signal named ``name`` or raise.

        Strategies should prefer this over :meth:`get`: silently treating a
        missing input as neutral is how a strategy starts trading on data it
        never actually received.
        """
        signal = self.get(name)
        if signal is None:
            raise KeyError(f"signal {name!r} was not computed for {self.symbol} at {self.as_of}")
        return signal
