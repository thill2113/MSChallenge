"""Executable orders and their outcomes.

An :class:`OrderIntent` is the only thing the execution layer will accept. It
cannot exist without an approved :class:`~risk.models.RiskDecision` embedded
inside it, and that decision must bind to the fingerprint of the very candidate
the intent describes.

That embedding is deliberate. Carrying only a ``risk_decision_id`` would make
the approval a claim; carrying the decision itself makes it evidence that every
downstream hop can re-verify without a database round trip (ADR-001).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import ClassVar, Self
from uuid import UUID

from pydantic import Field, model_validator

from agents.models import AgentReview
from domain.base import AuthoritativeModel
from domain.enums import AssetClass, ExecutionStatus, OrderType, Side, TimeInForce
from domain.errors import AuthorityViolationError, RiskBypassError
from domain.identifiers import DeterministicId
from domain.values import ExactDecimal, NonEmptyText, Price, Quantity, Symbol, TimestampUTC
from risk.models import RiskDecision
from strategies.models import TradeCandidate


def assert_authorized(
    *, candidate_id: UUID, candidate_fingerprint: str, decision: RiskDecision
) -> None:
    """Raise unless ``decision`` authorises exactly this candidate.

    Split out so the same four checks run both when an intent is built through
    :meth:`OrderIntent.from_approved` and when one is constructed directly. A
    caller that reaches for the constructor does not get a weaker guarantee.
    """
    if not decision.is_approved:
        raise RiskBypassError(
            f"OrderIntent requires an APPROVED risk decision; got {decision.verdict} "
            f"with violations {[v.code for v in decision.violations]}"
        )
    if decision.violations:
        # Reachable only if a decision was assembled without validation — for
        # example via ``model_copy(update=...)``, which skips validators. An
        # approval carrying unresolved violations is incoherent; refuse it.
        raise RiskBypassError(
            f"risk decision {decision.decision_id} claims APPROVED while carrying "
            f"violations {[v.code for v in decision.violations]}"
        )
    if decision.candidate_id != candidate_id:
        raise RiskBypassError(
            f"risk decision approves candidate {decision.candidate_id}, "
            f"but this intent is for {candidate_id}"
        )
    if decision.candidate_fingerprint != candidate_fingerprint:
        raise RiskBypassError(
            "risk decision approves a different version of this candidate "
            f"({decision.candidate_fingerprint} != {candidate_fingerprint})"
        )


class OrderIntent(AuthoritativeModel):
    """An immutable instruction to trade, carrying its own authorisation.

    Build these with :meth:`from_approved`. Constructing one directly still
    enforces every binding invariant below, so there is no shortcut that skips
    the checks — only a longer way of passing them.
    """

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "candidate_fingerprint",
        "symbol",
        "asset_class",
        "side",
        "order_type",
        "limit_price",
        "quantity",
        "stop_price",
        "target_price",
        "time_in_force",
    )

    order_intent_id: UUID
    candidate_id: UUID
    candidate_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy_id: str
    strategy_version: str

    risk_decision: RiskDecision = Field(
        description="The approval that authorises this order. Must be APPROVED and "
        "must bind to candidate_fingerprint."
    )
    agent_review_ids: tuple[UUID, ...] = Field(
        default=(),
        description="Reviews consulted before submission. Advisory record only.",
    )

    symbol: Symbol
    asset_class: AssetClass
    side: Side
    order_type: OrderType
    limit_price: Price | None = None
    quantity: Quantity
    stop_price: Price
    target_price: Price | None = None
    time_in_force: TimeInForce
    created_at: TimestampUTC

    @model_validator(mode="after")
    def _enforce_risk_binding(self) -> Self:
        """Refuse to exist without a valid, matching approval."""
        assert_authorized(
            candidate_id=self.candidate_id,
            candidate_fingerprint=self.candidate_fingerprint,
            decision=self.risk_decision,
        )
        return self

    @classmethod
    def from_approved(
        cls,
        candidate: TradeCandidate,
        risk_decision: RiskDecision,
        *,
        created_at: TimestampUTC,
        agent_reviews: Sequence[AgentReview] = (),
    ) -> OrderIntent:
        """Build an intent from an approved candidate.

        Every trading parameter is copied verbatim from ``candidate``. Nothing
        in this method computes, rounds or adjusts a price or a size — the
        execution layer transmits decisions, it does not make them.
        """
        fingerprint = candidate.authoritative_fingerprint()
        assert_authorized(
            candidate_id=candidate.candidate_id,
            candidate_fingerprint=fingerprint,
            decision=risk_decision,
        )

        for review in agent_reviews:
            if review.candidate_fingerprint != fingerprint:
                raise AuthorityViolationError(
                    f"agent review {review.review_id} was written against a different "
                    "version of this candidate"
                )
            if review.is_veto:
                raise AuthorityViolationError(
                    f"candidate {candidate.candidate_id} was vetoed by {review.reviewer}: "
                    f"{review.rationale}"
                )

        return cls(
            order_intent_id=DeterministicId.derive(
                "order_intent", fingerprint, str(risk_decision.decision_id)
            ),
            candidate_id=candidate.candidate_id,
            candidate_fingerprint=fingerprint,
            strategy_id=candidate.strategy_id,
            strategy_version=candidate.strategy_version,
            risk_decision=risk_decision,
            agent_review_ids=tuple(r.review_id for r in agent_reviews),
            symbol=candidate.symbol,
            asset_class=candidate.asset_class,
            side=candidate.side,
            order_type=candidate.order_type,
            limit_price=candidate.limit_price,
            quantity=candidate.quantity,
            stop_price=candidate.stop_price,
            target_price=candidate.target_price,
            time_in_force=candidate.time_in_force,
            created_at=created_at,
        )

    def matches_candidate(self, candidate: TradeCandidate) -> bool:
        """True when this intent still describes ``candidate`` exactly."""
        return self.candidate_fingerprint == candidate.authoritative_fingerprint()


class ExecutionResult(AuthoritativeModel):
    """What a venue reported back about an order intent."""

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "order_intent_id",
        "order_intent_fingerprint",
        "status",
        "filled_quantity",
        "average_fill_price",
        "venue",
    )

    result_id: UUID
    order_intent_id: UUID
    order_intent_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ExecutionStatus
    venue: str = Field(min_length=1, max_length=64)
    broker_order_id: str | None = Field(default=None, max_length=128)
    filled_quantity: ExactDecimal = Field(default=Decimal(0), ge=0)
    average_fill_price: Price | None = None
    submitted_at: TimestampUTC
    reported_at: TimestampUTC
    message: NonEmptyText | None = None
    raw_document_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="Digest of the immutable broker payload stored under data/raw (ADR-003).",
    )

    @model_validator(mode="after")
    def _check_fill_consistency(self) -> Self:
        if self.status is ExecutionStatus.FILLED and self.filled_quantity <= 0:
            raise ValueError("FILLED result must report a positive filled_quantity")
        if self.filled_quantity > 0 and self.average_fill_price is None:
            raise ValueError("a partially or fully filled result must report a fill price")
        if self.reported_at < self.submitted_at:
            raise ValueError("reported_at cannot precede submitted_at")
        return self
