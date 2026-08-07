"""Determinism harness for strategies.

The property "same input, same decision" is the load-bearing assumption behind
backtesting, replay and incident review. It is cheap to state and easy to break
(one ``datetime.now()``, one ``dict`` iteration over a set, one ``random``
seed), so it is checked mechanically rather than assumed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.base import AuthoritativeModel
from domain.errors import AuthorityViolationError
from strategies.context import EvaluationContext
from strategies.models import StrategyDecision

if TYPE_CHECKING:
    from strategies.protocols import Strategy


def decision_fingerprint(decision: StrategyDecision) -> str:
    """Fingerprint of a decision, whichever branch it took.

    The type is part of the fingerprint, so a ``NoTrade`` can never collide with
    a ``TradeCandidate``.
    """
    if not isinstance(decision, AuthoritativeModel):  # pragma: no cover - defensive
        raise TypeError(f"not a strategy decision: {type(decision)!r}")
    return f"{type(decision).__name__}:{decision.authoritative_fingerprint()}"


def evaluate_repeatedly(
    strategy: Strategy, context: EvaluationContext, *, runs: int = 5
) -> tuple[str, ...]:
    """Evaluate ``strategy`` ``runs`` times and return each decision fingerprint."""
    if runs < 2:
        raise ValueError("determinism needs at least two runs to be observable")
    return tuple(decision_fingerprint(strategy.evaluate(context)) for _ in range(runs))


def assert_deterministic(
    strategy: Strategy, context: EvaluationContext, *, runs: int = 5
) -> StrategyDecision:
    """Raise unless repeated evaluation of the same context is byte-identical.

    Returns the decision so callers can assert on it further.
    """
    fingerprints = evaluate_repeatedly(strategy, context, runs=runs)
    distinct = set(fingerprints)
    if len(distinct) != 1:
        raise AuthorityViolationError(
            f"{type(strategy).__name__} is non-deterministic: {runs} evaluations of an "
            f"identical context produced {len(distinct)} distinct decisions {sorted(distinct)}"
        )
    return strategy.evaluate(context)
