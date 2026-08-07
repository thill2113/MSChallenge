"""AgentReview cannot alter authoritative trading parameters (task 6, ADR-002).

The prohibition is attacked from five directions:

1. Constructing a review with a trading parameter.
2. Smuggling one through ``model_validate``.
3. Mutating a review after construction.
4. Mutating the candidate through the gate.
5. Getting a modified candidate past the execution boundary.

Every one must fail. A test that only checks the happy path would pass against
a model that has a ``stop_price`` field nobody happens to set.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agents.gate import apply_reviews, run_reviewers
from agents.models import FORBIDDEN_REVIEW_FIELDS, AgentReview
from domain.enums import ReviewVerdict
from domain.errors import AuthorityViolationError
from execution.models import OrderIntent
from strategies.models import TradeCandidate
from tests.doubles import AffirmingReviewer, VetoingReviewer

REVIEWED_AT = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)


def _review(candidate: TradeCandidate, verdict: ReviewVerdict, **overrides: object) -> AgentReview:
    fingerprint = candidate.authoritative_fingerprint()
    payload: dict[str, object] = {
        "review_id": AgentReview.derive_id(
            candidate_fingerprint=fingerprint, reviewer="r", reviewer_version="1"
        ),
        "candidate_id": candidate.candidate_id,
        "candidate_fingerprint": fingerprint,
        "verdict": verdict,
        "reviewer": "r",
        "reviewer_version": "1",
        "reviewed_at": REVIEWED_AT,
        "rationale": "test",
    }
    payload.update(overrides)
    return AgentReview(**payload)  # type: ignore[arg-type]


pytestmark = pytest.mark.invariant


class TestReviewShape:
    """The review model has nowhere to put a trading parameter."""

    def test_review_declares_no_trading_parameters(self):
        overlap = set(AgentReview.model_fields) & FORBIDDEN_REVIEW_FIELDS
        assert overlap == set(), f"AgentReview must not declare {sorted(overlap)}"

    def test_forbidden_set_covers_every_candidate_parameter(self):
        # The prohibition is derived from the candidate, so a new trading
        # parameter is covered automatically rather than needing a list update.
        assert set(TradeCandidate.AUTHORITATIVE_FIELDS) == FORBIDDEN_REVIEW_FIELDS
        for critical in ("stop_price", "quantity", "target_price", "account_risk_fraction"):
            assert critical in FORBIDDEN_REVIEW_FIELDS

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("stop_price", Decimal("50.00")),
            ("quantity", Decimal("9999")),
            ("target_price", Decimal("500.00")),
            ("account_risk_fraction", Decimal("0.99")),
            ("limit_price", Decimal("1.00")),
            ("side", "SELL"),
            ("order_type", "MARKET"),
            ("time_in_force", "GTC"),
            ("symbol", "OTHER"),
        ],
    )
    def test_construction_with_trading_parameter_is_rejected(self, candidate, field, value):
        with pytest.raises(ValidationError, match=r"[Ee]xtra inputs are not permitted"):
            _review(candidate, ReviewVerdict.AFFIRM, **{field: value})

    def test_smuggling_through_model_validate_is_rejected(self, candidate):
        fingerprint = candidate.authoritative_fingerprint()
        payload = {
            "review_id": str(
                AgentReview.derive_id(
                    candidate_fingerprint=fingerprint, reviewer="r", reviewer_version="1"
                )
            ),
            "candidate_id": str(candidate.candidate_id),
            "candidate_fingerprint": fingerprint,
            "verdict": "AFFIRM",
            "reviewer": "r",
            "reviewer_version": "1",
            "reviewed_at": REVIEWED_AT.isoformat(),
            "rationale": "looks fine, but tighten the stop",
            "stop_price": "99.50",
        }
        with pytest.raises(ValidationError, match=r"[Ee]xtra inputs are not permitted"):
            AgentReview.model_validate(payload)

    def test_verdict_vocabulary_has_no_amend(self):
        assert {v.value for v in ReviewVerdict} == {"AFFIRM", "VETO"}

    def test_review_is_immutable(self, candidate):
        review = _review(candidate, ReviewVerdict.AFFIRM)
        with pytest.raises(ValidationError):
            review.verdict = ReviewVerdict.VETO  # type: ignore[misc]
        with pytest.raises(ValidationError):
            review.rationale = "changed my mind"  # type: ignore[misc]


class TestGateLeavesCandidateUntouched:
    """Passing through the gate returns the same object, not a copy."""

    def test_affirmed_candidate_is_the_identical_object(self, candidate, context):
        result = run_reviewers(candidate, context, [AffirmingReviewer()])
        assert result.proceed() is candidate

    def test_fingerprint_survives_review(self, candidate, context):
        before = candidate.authoritative_fingerprint()
        result = run_reviewers(candidate, context, [AffirmingReviewer(), AffirmingReviewer("b")])
        assert result.proceed().authoritative_fingerprint() == before

    def test_veto_stops_the_trade_and_still_does_not_modify_it(self, candidate, context):
        before = candidate.authoritative_fingerprint()
        result = run_reviewers(candidate, context, [VetoingReviewer()])
        assert result.vetoed
        assert result.candidate.authoritative_fingerprint() == before
        with pytest.raises(AuthorityViolationError, match="vetoed"):
            result.proceed()

    def test_one_veto_among_many_affirmations_still_stops_the_trade(self, candidate, context):
        reviewers = [AffirmingReviewer("a"), VetoingReviewer("b"), AffirmingReviewer("c")]
        result = run_reviewers(candidate, context, reviewers)
        assert result.vetoed
        assert len(result.reviews) == 3, (
            "every reviewer is consulted, not just until the first veto"
        )

    def test_review_of_a_different_candidate_version_is_rejected(self, candidate):
        stale = _review(candidate, ReviewVerdict.AFFIRM)
        widened = candidate.model_copy(update={"quantity": Decimal("20")})
        with pytest.raises(AuthorityViolationError, match="candidate changed after review"):
            apply_reviews(widened, [stale])

    def test_review_targeting_another_candidate_id_is_rejected(self, candidate):
        other = candidate.model_copy(
            update={
                "symbol": "OTHR",
                "candidate_id": TradeCandidate.derive_id(
                    strategy_id=candidate.strategy_id,
                    strategy_version=candidate.strategy_version,
                    symbol="OTHR",
                    as_of=candidate.as_of,
                ),
            }
        )
        review = _review(other, ReviewVerdict.AFFIRM)
        with pytest.raises(AuthorityViolationError, match="targets candidate"):
            apply_reviews(candidate, [review])


class TestExecutionRejectsAgentInfluence:
    """A veto cannot be laundered into an order, and edits cannot ride along."""

    def test_vetoed_candidate_cannot_become_an_order_intent(
        self, candidate, risk_engine, portfolio, base_time
    ):
        decision = risk_engine.evaluate(candidate, portfolio, evaluated_at=base_time)
        assert decision.is_approved
        veto = _review(candidate, ReviewVerdict.VETO)
        with pytest.raises(AuthorityViolationError, match="vetoed"):
            OrderIntent.from_approved(
                candidate, decision, created_at=base_time, agent_reviews=[veto]
            )

    def test_order_intent_copies_parameters_verbatim(
        self, candidate, risk_engine, portfolio, base_time
    ):
        decision = risk_engine.evaluate(candidate, portfolio, evaluated_at=base_time)
        intent = OrderIntent.from_approved(
            candidate,
            decision,
            created_at=base_time,
            agent_reviews=[_review(candidate, ReviewVerdict.AFFIRM)],
        )
        assert intent.stop_price == candidate.stop_price
        assert intent.quantity == candidate.quantity
        assert intent.target_price == candidate.target_price
        assert intent.limit_price == candidate.limit_price
        assert intent.side is candidate.side
        assert intent.time_in_force is candidate.time_in_force
        assert intent.matches_candidate(candidate)

    def test_review_written_against_an_edited_candidate_is_rejected_at_the_gate(
        self, candidate, risk_engine, portfolio, base_time
    ):
        decision = risk_engine.evaluate(candidate, portfolio, evaluated_at=base_time)
        tightened = candidate.model_copy(update={"stop_price": Decimal("99.50")})
        review_of_edit = _review(tightened, ReviewVerdict.AFFIRM)
        with pytest.raises(AuthorityViolationError, match="different version"):
            OrderIntent.from_approved(
                candidate, decision, created_at=base_time, agent_reviews=[review_of_edit]
            )
