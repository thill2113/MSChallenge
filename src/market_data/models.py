"""Normalised market observations.

A :class:`MarketSnapshot` is the *only* market input a strategy may read. It is
frozen and self-dating, which means a strategy evaluation is a pure function of
its arguments: there is no hidden "current price" to drift underneath a replay.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar, Self

from pydantic import Field, model_validator

from domain.base import AuthoritativeModel, FrozenModel
from domain.enums import AssetClass
from domain.values import ExactDecimal, Price, Symbol, TimestampUTC


class Bar(FrozenModel):
    """An OHLCV bar covering ``interval_seconds`` ending at the snapshot instant."""

    interval_seconds: int = Field(gt=0, description="Bar width in seconds.")
    open: Price
    high: Price
    low: Price
    close: Price
    volume: ExactDecimal = Field(ge=0)

    @model_validator(mode="after")
    def _check_bounds(self) -> Self:
        if self.high < self.low:
            raise ValueError("bar high must be >= low")
        if not (self.low <= self.open <= self.high):
            raise ValueError("bar open must fall within [low, high]")
        if not (self.low <= self.close <= self.high):
            raise ValueError("bar close must fall within [low, high]")
        return self


class Quote(FrozenModel):
    """Top-of-book bid/ask."""

    bid: Price
    ask: Price
    bid_size: ExactDecimal | None = Field(default=None, ge=0)
    ask_size: ExactDecimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _check_crossed(self) -> Self:
        if self.ask < self.bid:
            raise ValueError("crossed quote: ask must be >= bid")
        return self

    @property
    def mid(self) -> Decimal:
        """Midpoint of the spread."""
        return (self.bid + self.ask) / Decimal(2)


class MarketSnapshot(AuthoritativeModel):
    """A point-in-time, venue-normalised view of one instrument.

    ``as_of`` is supplied by the caller rather than read from the clock, so the
    same snapshot can be replayed indefinitely.
    """

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "symbol",
        "asset_class",
        "as_of",
        "last_price",
        "bar",
        "quote",
    )

    symbol: Symbol
    asset_class: AssetClass = AssetClass.EQUITY
    as_of: TimestampUTC
    last_price: Price
    bar: Bar | None = None
    quote: Quote | None = None
    provider: str = Field(
        min_length=1,
        max_length=64,
        description="Which upstream produced this observation. Recorded for lineage; "
        "excluded from the fingerprint so re-sourcing identical data is a no-op.",
    )
