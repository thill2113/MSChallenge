"""The strategy contract.

A strategy is a pure function of its inputs. That is not a style preference: it
is what makes a backtest predictive of a replay, and what makes the determinism
tests in ``tests/property/`` able to prove anything at all.

Implementations must not read the clock, call the network, consume randomness,
or mutate their own state between evaluations.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from regime.models import RegimeAssessment
from signals.models import SignalSet
from strategies.context import EvaluationContext
from strategies.models import StrategyDecision, StrategyMetadata


@runtime_checkable
class Strategy(Protocol):
    """Produces exactly one :class:`~strategies.models.TradeCandidate` or
    :class:`~strategies.models.NoTrade` per evaluation."""

    @property
    def metadata(self) -> StrategyMetadata:
        """Static identity of this strategy."""
        ...

    @property
    def version(self) -> str:
        """Semantic version of this implementation (ADR-004)."""
        ...

    def evaluate(self, context: EvaluationContext) -> StrategyDecision:
        """Decide. Must be deterministic and side-effect free."""
        ...


__all__ = ["EvaluationContext", "RegimeAssessment", "SignalSet", "Strategy"]
