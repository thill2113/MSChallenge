"""Agent layer — asynchronous intelligence with veto authority only.

Boundary contract (ADR-002, ADR-006):

* **Runs out of band.** Agents analyse market context on their own schedule and
  publish :class:`~agents.context.AgentContext` into a store. The execution path
  reads the cache and never waits for an inference, so the trading system keeps
  working when Claude is slow, rate-limited or down.
* **May** veto within a scope, and attach rationale, concerns and confidence.
* **May not** modify stop, quantity, target, account risk, order type, time in
  force, execution mode, kill-switch state or strategy promotion. The models
  have no field for any of them and reject undeclared keys.
* **May not** create a trade. A veto can only ever subtract.

:mod:`agents.gate` retains the synchronous review path for research and
backtesting, where waiting for a model is acceptable. Production execution uses
:mod:`agents.store`.
"""

from agents.context import (
    FORBIDDEN_CONTEXT_FIELDS,
    AgentContext,
    ContextReasonCode,
    ContextualRisk,
    assert_context_carries_no_trading_authority,
)
from agents.gate import AgentGateResult, AgentReviewer, apply_reviews, run_reviewers
from agents.models import (
    FORBIDDEN_REVIEW_FIELDS,
    AgentReview,
    assert_review_carries_no_trading_authority,
)
from agents.store import (
    AgentContextStore,
    AgentGateOutcome,
    InMemoryAgentContextStore,
    resolve_agent_gate,
)

__all__ = [
    "FORBIDDEN_CONTEXT_FIELDS",
    "FORBIDDEN_REVIEW_FIELDS",
    "AgentContext",
    "AgentContextStore",
    "AgentGateOutcome",
    "AgentGateResult",
    "AgentReview",
    "AgentReviewer",
    "ContextReasonCode",
    "ContextualRisk",
    "InMemoryAgentContextStore",
    "apply_reviews",
    "assert_context_carries_no_trading_authority",
    "assert_review_carries_no_trading_authority",
    "resolve_agent_gate",
    "run_reviewers",
]
