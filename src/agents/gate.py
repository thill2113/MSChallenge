"""Applying agent reviews to a candidate.

The gate has exactly one degree of freedom: let the candidate through unchanged,
or stop it. When it lets a candidate through it returns *the same object*, not a
copy — so "the agent modified the trade" is not a bug that can hide in a diff of
two similar-looking records.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

from agents.models import AgentReview
from domain.base import FrozenModel
from domain.errors import AuthorityViolationError
from strategies.context import EvaluationContext
from strategies.models import TradeCandidate


@runtime_checkable
class AgentReviewer(Protocol):
    """An AI reviewer.

    Implementations produce a verdict and a rationale. They receive the
    candidate and its originating context as read-only inputs and return an
    :class:`~agents.models.AgentReview`; there is no return channel through
    which a trading parameter could travel.
    """

    @property
    def reviewer(self) -> str:
        """Reviewer identity, e.g. a model identifier."""
        ...

    @property
    def reviewer_version(self) -> str:
        """Version of the reviewing configuration or prompt."""
        ...

    def review(self, candidate: TradeCandidate, context: EvaluationContext) -> AgentReview:
        """Return a verdict on ``candidate``."""
        ...


class AgentGateResult(FrozenModel):
    """Outcome of applying every review to one candidate."""

    candidate: TradeCandidate
    reviews: tuple[AgentReview, ...]
    vetoing_reviews: tuple[AgentReview, ...]

    @property
    def vetoed(self) -> bool:
        """True when at least one reviewer objected."""
        return bool(self.vetoing_reviews)

    def proceed(self) -> TradeCandidate:
        """Return the untouched candidate, or raise if it was vetoed."""
        if self.vetoed:
            reasons = "; ".join(r.rationale for r in self.vetoing_reviews)
            raise AuthorityViolationError(
                f"candidate {self.candidate.candidate_id} was vetoed by "
                f"{len(self.vetoing_reviews)} reviewer(s): {reasons}"
            )
        return self.candidate


def apply_reviews(candidate: TradeCandidate, reviews: Iterable[AgentReview]) -> AgentGateResult:
    """Apply reviews to ``candidate`` without altering it.

    Every review must bind to this exact candidate — same id *and* same
    fingerprint. A review of a stale version of the candidate is rejected rather
    than ignored, because "the agent approved something else" is precisely the
    failure this binding exists to catch.
    """
    fingerprint = candidate.authoritative_fingerprint()
    collected: list[AgentReview] = []
    for review in reviews:
        if review.candidate_id != candidate.candidate_id:
            raise AuthorityViolationError(
                f"review {review.review_id} targets candidate {review.candidate_id}, "
                f"not {candidate.candidate_id}"
            )
        if review.candidate_fingerprint != fingerprint:
            raise AuthorityViolationError(
                f"review {review.review_id} was written against fingerprint "
                f"{review.candidate_fingerprint} but the candidate is {fingerprint}; "
                "the candidate changed after review"
            )
        collected.append(review)

    return AgentGateResult(
        candidate=candidate,
        reviews=tuple(collected),
        vetoing_reviews=tuple(r for r in collected if r.is_veto),
    )


def run_reviewers(
    candidate: TradeCandidate,
    context: EvaluationContext,
    reviewers: Sequence[AgentReviewer],
) -> AgentGateResult:
    """Collect verdicts from ``reviewers`` and apply them.

    Reviewers are consulted in order and every one is consulted, even after a
    veto: a full record of who objected and why is worth more than the
    microseconds saved by short-circuiting.
    """
    reviews = [reviewer.review(candidate, context) for reviewer in reviewers]
    return apply_reviews(candidate, reviews)
