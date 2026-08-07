"""Agent layer — advisory review with veto authority only.

Boundary contract (ADR-002):

* **May** veto a :class:`~strategies.models.TradeCandidate`.
* **May** attach rationale, concerns and a confidence score.
* **May not** modify stop, quantity, target, account risk, order type, time in
  force, or any other execution parameter. The review model has no field for
  any of them and rejects undeclared keys.
* **May not** create a trade. A veto can only ever subtract.

No LLM client is implemented in Phase 1. This package defines the contract an
agent must satisfy and the gate that enforces its limits.
"""

from agents.gate import AgentGateResult, AgentReviewer, apply_reviews, run_reviewers
from agents.models import (
    FORBIDDEN_REVIEW_FIELDS,
    AgentReview,
    assert_review_carries_no_trading_authority,
)

__all__ = [
    "FORBIDDEN_REVIEW_FIELDS",
    "AgentGateResult",
    "AgentReview",
    "AgentReviewer",
    "apply_reviews",
    "assert_review_carries_no_trading_authority",
    "run_reviewers",
]
