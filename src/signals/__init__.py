"""Signal computation.

A signal is a *named, versioned, deterministic measurement* derived from market
data. Signals carry no trading authority: they are inputs a strategy may read.
Phase 1 defines the contract only — no indicator is implemented here, because
choosing indicators is a strategy decision and strategies are out of scope for
this phase.
"""

from signals.models import Signal, SignalSet
from signals.protocols import SignalComputer

__all__ = ["Signal", "SignalComputer", "SignalSet"]
