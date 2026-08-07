"""Regime classifier interface."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from market_data.models import MarketSnapshot
from regime.models import RegimeAssessment


@runtime_checkable
class RegimeClassifier(Protocol):
    """Classifies market state from an ordered window of observations.

    Implementations must be pure and must return
    :attr:`~regime.models.MarketRegime.UNKNOWN` rather than guessing when the
    window is inadequate.
    """

    @property
    def name(self) -> str:
        """Stable snake_case identifier."""
        ...

    @property
    def version(self) -> str:
        """Bumped whenever classification behaviour changes (ADR-004)."""
        ...

    def classify(self, window: Sequence[MarketSnapshot]) -> RegimeAssessment:
        """Return an assessment for the end of ``window``."""
        ...
