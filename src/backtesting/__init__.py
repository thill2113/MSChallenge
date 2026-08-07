"""Backtesting — deterministic replay of the decision chain.

Phase 1 delivers decision replay only. Fill simulation, slippage modelling and
performance statistics are deliberately absent: they require assumptions about
market microstructure that nobody has agreed yet, and half-modelled returns are
actively misleading.
"""

from backtesting.replay import ReplayReport, ReplayStep, run_replay

__all__ = ["ReplayReport", "ReplayStep", "run_replay"]
