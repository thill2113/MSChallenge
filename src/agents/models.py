"""Agent review records.

An :class:`AgentReview` is *structurally* incapable of altering a trade. That is
not enforced by a code-review convention or a runtime whitelist that someone can
forget to call — it is enforced by the shape of the type:

* The model declares **no** trading parameter fields. There is nowhere to put a
  revised stop, quantity, target or risk fraction.
* ``extra="forbid"`` (inherited from :class:`~domain.base.FrozenModel`) rejects
  undeclared keys, so a caller cannot smuggle ``stop_price=...`` through
  ``model_validate``.
* :data:`ReviewVerdict` has two members. There is no ``AMEND``.
* :func:`assert_review_carries_no_trading_authority` re-checks the first point at
  import time, so adding an offending field to this module fails immediately
  rather than at the next code review.

See ADR-002.
"""

from __future__ import annotations

from typing import ClassVar, Final
from uuid import UUID

from pydantic import Field

from domain.base import AuthoritativeModel
from domain.enums import ReviewVerdict
from domain.errors import AuthorityViolationError
from domain.identifiers import DeterministicId
from domain.values import NonEmptyText, Ratio, TimestampUTC
from strategies.models import TradeCandidate

FORBIDDEN_REVIEW_FIELDS: Final[frozenset[str]] = frozenset(TradeCandidate.AUTHORITATIVE_FIELDS)
"""Fields an agent may never carry, derived from the candidate itself.

Deriving this set from ``TradeCandidate.AUTHORITATIVE_FIELDS`` rather than
hard-coding it means adding a new trading parameter to a candidate
automatically extends the prohibition.
"""


class AgentReview(AuthoritativeModel):
    """An AI reviewer's verdict on one candidate.

    The review binds to ``candidate_fingerprint``. A review of a 100-share
    candidate is not a review of a 1,000-share candidate, even if the candidate
    id is the same.
    """

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "candidate_id",
        "candidate_fingerprint",
        "verdict",
        "reviewer",
        "reviewer_version",
    )

    review_id: UUID
    candidate_id: UUID
    candidate_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: ReviewVerdict
    reviewer: str = Field(
        min_length=1,
        max_length=128,
        description="Identity of the reviewing agent, e.g. a model identifier.",
    )
    reviewer_version: str = Field(min_length=1, max_length=64)
    reviewed_at: TimestampUTC
    rationale: NonEmptyText = Field(
        description="Why. Advisory text only — it has no effect on any trading parameter."
    )
    concerns: tuple[NonEmptyText, ...] = ()
    confidence: Ratio | None = None

    @property
    def is_veto(self) -> bool:
        """True when this review stops the trade."""
        return self.verdict is ReviewVerdict.VETO

    @classmethod
    def derive_id(cls, *, candidate_fingerprint: str, reviewer: str, reviewer_version: str) -> UUID:
        """Derive the review id from what was reviewed and who reviewed it."""
        return DeterministicId.derive(
            "agent_review", candidate_fingerprint, reviewer, reviewer_version
        )


def assert_review_carries_no_trading_authority() -> None:
    """Fail loudly if :class:`AgentReview` ever grows a trading parameter.

    Called at import time. If someone adds ``stop_price`` to the review model,
    the package stops importing — which is a far better outcome than shipping an
    agent that can move a stop.
    """
    overlap = set(AgentReview.model_fields) & FORBIDDEN_REVIEW_FIELDS
    if overlap:
        raise AuthorityViolationError(
            "AgentReview declares trading parameters it has no authority over: "
            f"{sorted(overlap)}. The agent layer is veto-only (ADR-002)."
        )


assert_review_carries_no_trading_authority()
