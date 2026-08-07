"""Context/veto store and the policy that reads it.

The store is a cache with two jobs: hand the hot path an answer immediately, and
be honest when it does not have one. It never blocks, never calls out, and never
triggers an inference — a lookup is a dictionary read.

:func:`resolve_agent_gate` turns "what does the store say" plus "what did the
human configure" into a single allow/block decision. All three policies are
implemented; none is the default, because choosing between them is a risk
decision reserved for a human (ADR-006).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from typing import Protocol, runtime_checkable

from pydantic import Field

from agents.context import AgentContext, ContextReasonCode
from domain.base import FrozenModel
from domain.enums import AgentContextPolicy
from domain.values import NonEmptyText, Symbol, TimestampUTC


@runtime_checkable
class AgentContextStore(Protocol):
    """Read side of the context cache, as the execution path sees it.

    Implementations **must not** block on network I/O. If a backing store could
    be slow, front it with an in-process cache and let the refresh happen out of
    band; a store that occasionally takes 400 ms is a store that occasionally
    costs a fill.
    """

    def get(self, symbol: Symbol, strategy_id: str) -> AgentContext | None:
        """Return the most recent context governing this scope, if any."""
        ...

    def is_healthy(self) -> bool:
        """Whether the publishing agent is believed to be alive and current.

        Distinct from having a context: an agent can be healthy with nothing to
        say, and can be dead while a recent opinion is still cached.
        """
        ...


class AgentGateOutcome(FrozenModel):
    """Result of consulting the context store."""

    allowed: bool
    policy: AgentContextPolicy
    context_id: str | None = None
    reason: NonEmptyText = Field(description="Why the gate allowed or blocked.")
    reason_codes: tuple[ContextReasonCode, ...] = ()
    context_was_stale: bool = False
    context_was_absent: bool = False
    agent_unhealthy: bool = False


class InMemoryAgentContextStore:
    """Non-blocking, in-process context cache.

    Keyed by ``(symbol, strategy_id | "*")``. Publishing a context for a scope
    replaces the previous one — an agent's latest word wins, and superseded
    opinions are journalled rather than retained here.
    """

    def __init__(self, *, healthy: bool = True) -> None:
        self._contexts: dict[tuple[str, str], AgentContext] = {}
        self._healthy = healthy

    def publish(self, context: AgentContext) -> None:
        """Store ``context``, replacing any prior context for the same scope."""
        self._contexts[(context.symbol, context.strategy_id or "*")] = context

    def publish_all(self, contexts: Iterable[AgentContext]) -> None:
        """Publish several contexts."""
        for context in contexts:
            self.publish(context)

    def get(self, symbol: Symbol, strategy_id: str) -> AgentContext | None:
        """Most specific match wins: a strategy-scoped context beats a wildcard."""
        specific = self._contexts.get((symbol, strategy_id))
        if specific is not None:
            return specific
        return self._contexts.get((symbol, "*"))

    def is_healthy(self) -> bool:
        """Reported agent health."""
        return self._healthy

    def set_healthy(self, healthy: bool) -> None:
        """Update health, e.g. from a publisher heartbeat monitor."""
        self._healthy = healthy

    def clear(self) -> None:
        """Drop every cached context."""
        self._contexts.clear()

    def __len__(self) -> int:
        return len(self._contexts)


def resolve_agent_gate(
    *,
    store: AgentContextStore | None,
    symbol: str,
    strategy_id: str,
    now: TimestampUTC,
    policy: AgentContextPolicy,
    max_age: timedelta,
) -> AgentGateOutcome:
    """Decide whether agent state permits this trade.

    A valid, unexpired veto blocks under **every** policy — that is the agent's
    one power and no configuration disables it. The policies differ only in what
    happens when the agent has *not* spoken, or spoke too long ago.
    """
    context = store.get(symbol, strategy_id) if store is not None else None
    unhealthy = store is not None and not store.is_healthy()

    if context is not None and context.is_valid_at(now, max_age=max_age):
        if context.veto:
            return AgentGateOutcome(
                allowed=False,
                policy=policy,
                context_id=str(context.context_id),
                reason=f"agent veto by {context.publisher}: "
                f"{context.summary or 'no summary supplied'}",
                reason_codes=context.reason_codes,
            )
        return AgentGateOutcome(
            allowed=True,
            policy=policy,
            context_id=str(context.context_id),
            reason=f"valid agent context from {context.publisher} raises no veto",
            reason_codes=context.reason_codes,
        )

    stale = context is not None
    absent = context is None

    if policy is AgentContextPolicy.ALLOW_WITHOUT_AGENT_CONTEXT:
        return AgentGateOutcome(
            allowed=True,
            policy=policy,
            reason="no valid agent context; policy permits trading without one",
            context_was_stale=stale,
            context_was_absent=absent,
            agent_unhealthy=unhealthy,
        )

    if policy is AgentContextPolicy.REQUIRE_VALID_AGENT_CONTEXT:
        return AgentGateOutcome(
            allowed=False,
            policy=policy,
            reason=("agent context has expired" if stale else "no agent context is available")
            + "; policy requires a valid one",
            context_was_stale=stale,
            context_was_absent=absent,
            agent_unhealthy=unhealthy,
        )

    # FAIL_CLOSED_ON_AGENT_FAILURE: silence is acceptable, failure is not.
    if stale or unhealthy:
        return AgentGateOutcome(
            allowed=False,
            policy=policy,
            reason=("agent context expired" if stale else "agent reported unhealthy")
            + "; policy fails closed on agent failure",
            context_was_stale=stale,
            context_was_absent=absent,
            agent_unhealthy=unhealthy,
        )
    return AgentGateOutcome(
        allowed=True,
        policy=policy,
        reason="agent has published no context and is healthy; treated as no opinion",
        context_was_absent=True,
    )
