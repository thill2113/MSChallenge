"""Test doubles.

**Nothing in this module is a trading strategy.** These are harness objects with
no market thesis, no edge and no intended profitability. They exist because you
cannot test "identical inputs produce identical decisions" without something to
evaluate, and you cannot test that the determinism check *works* without
something that deliberately fails it.

They live under ``tests/`` rather than ``src/`` so they can never be registered,
promoted or executed by the real system.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from agents.models import AgentReview
from domain.enums import AssetClass, OrderType, ReviewVerdict, Side, TimeInForce
from strategies.context import EvaluationContext
from strategies.models import (
    NoTrade,
    NoTradeReason,
    StrategyDecision,
    StrategyMetadata,
    TradeCandidate,
)

CENT = Decimal("0.0001")

FIXTURE_METADATA = StrategyMetadata(
    strategy_id="fixture_double",
    display_name="Fixture Double",
    description="Harness object used to exercise the decision pipeline. Not a strategy.",
    owner="engineering",
    asset_classes=(AssetClass.EQUITY,),
)


class DeterministicFixtureStrategy:
    """Maps a snapshot to a decision by a fixed arithmetic rule.

    The rule is arbitrary on purpose: it exists to be reproducible, not to be
    right. A price at or above ``threshold`` yields a candidate; anything below
    yields a no-trade.
    """

    def __init__(self, *, threshold: Decimal = Decimal("10"), version: str = "1.0.0") -> None:
        self._threshold = threshold
        self._version = version

    @property
    def metadata(self) -> StrategyMetadata:
        return FIXTURE_METADATA

    @property
    def version(self) -> str:
        return self._version

    def evaluate(self, context: EvaluationContext) -> StrategyDecision:
        reference = context.snapshot.last_price
        strategy_id = self.metadata.strategy_id

        if reference < self._threshold:
            return NoTrade(
                decision_id=NoTrade.derive_id(
                    strategy_id=strategy_id,
                    strategy_version=self._version,
                    symbol=context.symbol,
                    as_of=context.as_of,
                ),
                strategy_id=strategy_id,
                strategy_version=self._version,
                symbol=context.symbol,
                as_of=context.as_of,
                reason=NoTradeReason.NO_SETUP,
                detail="reference price is below the fixture threshold",
                inputs_fingerprint=context.fingerprint(),
            )

        stop = (reference * Decimal("0.98")).quantize(CENT)
        target = (reference * Decimal("1.04")).quantize(CENT)
        return TradeCandidate(
            candidate_id=TradeCandidate.derive_id(
                strategy_id=strategy_id,
                strategy_version=self._version,
                symbol=context.symbol,
                as_of=context.as_of,
            ),
            strategy_id=strategy_id,
            strategy_version=self._version,
            symbol=context.symbol,
            as_of=context.as_of,
            asset_class=AssetClass.EQUITY,
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            limit_price=reference,
            quantity=Decimal("10"),
            reference_price=reference,
            stop_price=stop,
            target_price=target,
            time_in_force=TimeInForce.DAY,
            account_risk_fraction=Decimal("0.005"),
            rationale="fixture candidate; carries no market view",
            inputs_fingerprint=context.fingerprint(),
        )


class NonDeterministicStrategy(DeterministicFixtureStrategy):
    """Deliberately unreliable: reads the clock and a random source.

    Used to prove the determinism harness fails when it should. Without this,
    a passing determinism test proves only that the harness runs.
    """

    def evaluate(self, context: EvaluationContext) -> StrategyDecision:
        decision = super().evaluate(context)
        jitter = Decimal(random.randint(1, 9_999)) / Decimal(10_000)  # noqa: S311 - test double
        if isinstance(decision, NoTrade):
            return decision.model_copy(update={"decision_id": uuid.uuid4()})
        drifted = (decision.stop_price - jitter).quantize(CENT)
        return decision.model_copy(update={"stop_price": max(drifted, Decimal("0.01"))})


class AffirmingReviewer:
    """An agent that always lets the candidate stand."""

    def __init__(self, reviewer: str = "fixture-reviewer", version: str = "1") -> None:
        self._reviewer = reviewer
        self._version = version

    @property
    def reviewer(self) -> str:
        return self._reviewer

    @property
    def reviewer_version(self) -> str:
        return self._version

    def _build(self, candidate: TradeCandidate, verdict: ReviewVerdict, why: str) -> AgentReview:
        fingerprint = candidate.authoritative_fingerprint()
        return AgentReview(
            review_id=AgentReview.derive_id(
                candidate_fingerprint=fingerprint,
                reviewer=self._reviewer,
                reviewer_version=self._version,
            ),
            candidate_id=candidate.candidate_id,
            candidate_fingerprint=fingerprint,
            verdict=verdict,
            reviewer=self._reviewer,
            reviewer_version=self._version,
            reviewed_at=datetime(2026, 1, 2, 15, 0, tzinfo=UTC),
            rationale=why,
        )

    def review(self, candidate: TradeCandidate, context: EvaluationContext) -> AgentReview:
        return self._build(candidate, ReviewVerdict.AFFIRM, "no objection")


class VetoingReviewer(AffirmingReviewer):
    """An agent that always stops the candidate."""

    def review(self, candidate: TradeCandidate, context: EvaluationContext) -> AgentReview:
        return self._build(candidate, ReviewVerdict.VETO, "fixture veto")
