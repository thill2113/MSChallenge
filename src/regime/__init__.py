"""Market regime classification.

A regime assessment is context, not authority: it may be an input to a strategy
and it may be an input to risk sizing policy, but it can never by itself create
or approve a trade.

Phase 1 defines the vocabulary and the classifier contract. No classifier is
implemented — the boundaries between regimes are a trading decision that
requires human definition (see docs/PHASE_1_STATUS.md, open decision D-4).
"""

from regime.models import MarketRegime, RegimeAssessment
from regime.protocols import RegimeClassifier

__all__ = ["MarketRegime", "RegimeAssessment", "RegimeClassifier"]
