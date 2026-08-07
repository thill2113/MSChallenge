"""Regime vocabulary and assessment record."""

from __future__ import annotations

from enum import StrEnum, unique
from typing import ClassVar

from pydantic import Field

from domain.base import AuthoritativeModel
from domain.values import NonEmptyText, Ratio, Symbol, TimestampUTC


@unique
class MarketRegime(StrEnum):
    """Coarse market states.

    ``UNKNOWN`` is the default and is not a failure mode — it is the honest
    answer when the classifier has insufficient history, and downstream policy
    is expected to handle it explicitly rather than assume a benign regime.
    """

    UNKNOWN = "UNKNOWN"
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE_BOUND = "RANGE_BOUND"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"


class RegimeAssessment(AuthoritativeModel):
    """A classifier's view of the market at an instant."""

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "classifier_name",
        "classifier_version",
        "scope",
        "as_of",
        "regime",
    )

    classifier_name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    classifier_version: str = Field(min_length=1, max_length=32)
    scope: Symbol = Field(description="Instrument symbol, or a market proxy such as 'SPY'.")
    as_of: TimestampUTC
    regime: MarketRegime = MarketRegime.UNKNOWN
    confidence: Ratio | None = None
    rationale: NonEmptyText | None = None
