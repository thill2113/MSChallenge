"""Strategy engine — the sole author of trading parameters.

Boundary contract (ADR-001):

* **Produces** exactly one :class:`~strategies.models.TradeCandidate` or
  :class:`~strategies.models.NoTrade` per evaluation.
* **Reads** only its :class:`~strategies.context.EvaluationContext`.
* **Never** consults the risk engine, the agent layer, the broker or the clock.

No trading strategy is implemented in this package. Phase 1 delivers the
contract, the versioning model and the determinism harness; strategy logic is
authored separately and promoted through :mod:`strategies.promotion`.
"""

from strategies.context import EvaluationContext, PortfolioView
from strategies.determinism import assert_deterministic, decision_fingerprint
from strategies.models import (
    NoTrade,
    NoTradeReason,
    StrategyDecision,
    StrategyMetadata,
    StrategyParameter,
    StrategyVersion,
    TradeCandidate,
)
from strategies.promotion import HumanApproval, PromotionRecord, authorize_promotion
from strategies.protocols import Strategy
from strategies.registry import StrategyRegistry

__all__ = [
    "EvaluationContext",
    "HumanApproval",
    "NoTrade",
    "NoTradeReason",
    "PortfolioView",
    "PromotionRecord",
    "Strategy",
    "StrategyDecision",
    "StrategyMetadata",
    "StrategyParameter",
    "StrategyRegistry",
    "StrategyVersion",
    "TradeCandidate",
    "assert_deterministic",
    "authorize_promotion",
    "decision_fingerprint",
]
