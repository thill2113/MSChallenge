"""Concrete strategy implementations.

Separate from the rest of :mod:`strategies`, which is framework: models,
versioning, promotion and the determinism harness. Everything in here is a
trading hypothesis and is subject to ADR-004 (a behaviour change is a new
version, never an edit) and ADR-005 (only a human promotes it).

No strategy in this package has been backtested.
"""

from strategies.library.trend_breakout import TrendBreakoutV1

__all__ = ["TrendBreakoutV1"]
