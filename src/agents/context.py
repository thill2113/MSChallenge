"""Asynchronous agent context.

Claude no longer sits in the execution path. It analyses market context on its
own schedule and *publishes* what it found; the trading system reads the cached
result and never waits for an inference.

That inversion is what makes the system survive Claude being slow, rate-limited
or down. It also means every published context carries an explicit
:attr:`AgentContext.expires_at`: an opinion formed at 09:31 about a fast-moving
symbol is not an opinion about 15:45, and a cache that never expires is a cache
that eventually lies.

The authority limits from ADR-002 are unchanged and, if anything, tighter: an
:class:`AgentContext` still has no field capable of expressing a size, a stop or
a target. It can say *stop*, and it can say *why*. Nothing else.
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum, unique
from typing import ClassVar, Final, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain.base import AuthoritativeModel
from domain.errors import AuthorityViolationError
from domain.identifiers import DeterministicId
from domain.values import NonEmptyText, Ratio, Symbol, TimestampUTC
from strategies.models import TradeCandidate


@unique
class ContextReasonCode(StrEnum):
    """Closed vocabulary for why an agent is concerned.

    Closed so veto reasons can be counted, alerted on and compared across time.
    Free-text rationale rides alongside in :attr:`AgentContext.summary`.
    """

    EARNINGS_IMMINENT = "EARNINGS_IMMINENT"
    MATERIAL_NEWS = "MATERIAL_NEWS"
    REGIME_UNCERTAIN = "REGIME_UNCERTAIN"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    ABNORMAL_VOLATILITY = "ABNORMAL_VOLATILITY"
    ABNORMAL_LIQUIDITY = "ABNORMAL_LIQUIDITY"
    CORRELATED_EXPOSURE = "CORRELATED_EXPOSURE"
    STRATEGY_WEAKNESS_SUSPECTED = "STRATEGY_WEAKNESS_SUSPECTED"
    DATA_QUALITY_SUSPECT = "DATA_QUALITY_SUSPECT"
    HALTED_OR_ILLIQUID = "HALTED_OR_ILLIQUID"
    NO_CONCERN = "NO_CONCERN"


class ContextualRisk(AuthoritativeModel):
    """One concern the agent identified, with its severity."""

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = ("code", "severity")

    code: ContextReasonCode
    severity: Ratio = Field(description="0 = noted, 1 = maximal concern. Advisory only.")
    detail: NonEmptyText


SCOPING_FIELDS: Final[frozenset[str]] = frozenset({"symbol", "strategy_id"})
"""Candidate fields a context may legitimately name.

These two are *addressing*, not authorship. Saying "this concern is about ACME
under strategy foo" narrows what a veto applies to; it cannot widen anything,
and a context naming a symbol it was not asked about simply never matches. Every
other authoritative field determines what the market feels and stays off limits.
"""

FORBIDDEN_CONTEXT_FIELDS: Final[frozenset[str]] = (
    frozenset(TradeCandidate.AUTHORITATIVE_FIELDS) - SCOPING_FIELDS
)
"""Fields an agent context may never carry."""


class AgentContext(AuthoritativeModel):
    """Cached, expiring analysis published by an agent.

    Read by the execution path; never written by it. Scoped to
    ``(symbol, strategy_id)`` so a concern about one strategy's setup on a
    symbol does not silently halt an unrelated strategy on the same symbol.
    """

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "symbol",
        "strategy_id",
        "veto",
        "publisher",
        "publisher_version",
        "created_at",
        "expires_at",
    )

    context_id: UUID
    symbol: Symbol
    strategy_id: str | None = Field(
        default=None,
        description="Scope. None means the context applies to every strategy on this symbol.",
    )

    veto: bool = Field(
        description="True stops trading within this scope. This is the only field "
        "with any effect on execution, and it can only ever subtract."
    )
    confidence: Ratio | None = None

    contextual_risks: tuple[ContextualRisk, ...] = ()
    contradictory_evidence: tuple[NonEmptyText, ...] = ()
    reason_codes: tuple[ContextReasonCode, ...] = ()
    summary: NonEmptyText | None = None

    publisher: str = Field(min_length=1, max_length=128, description="Agent identity.")
    publisher_version: str = Field(min_length=1, max_length=64)
    created_at: TimestampUTC
    expires_at: TimestampUTC = Field(
        description="After this instant the context is stale and the configured "
        "AgentContextPolicy decides what happens. Mandatory: an opinion with no "
        "expiry is an opinion that outlives its evidence."
    )

    @model_validator(mode="after")
    def _check_lifetime(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        if self.veto and not self.reason_codes:
            raise ValueError(
                "a veto must state at least one reason code; an unexplained veto "
                "cannot be reviewed, counted or appealed"
            )
        return self

    def is_valid_at(self, moment: TimestampUTC, *, max_age: timedelta | None = None) -> bool:
        """Whether this context still counts at ``moment``.

        ``max_age`` is the control plane's independent ceiling. A publisher that
        sets a year-long expiry cannot thereby keep its opinion alive for a year.
        """
        if moment >= self.expires_at:
            return False
        return not (max_age is not None and moment - self.created_at > max_age)

    def applies_to(self, symbol: str, strategy_id: str) -> bool:
        """Whether this context governs the given symbol and strategy."""
        if self.symbol != symbol:
            return False
        return self.strategy_id is None or self.strategy_id == strategy_id

    @classmethod
    def derive_id(
        cls, *, symbol: str, strategy_id: str | None, publisher: str, created_at: TimestampUTC
    ) -> UUID:
        """Derive a stable id from the context's scope and origin."""
        return DeterministicId.derive(
            "agent_context", symbol, strategy_id or "*", publisher, created_at.isoformat()
        )


def assert_context_carries_no_trading_authority() -> None:
    """Fail at import time if :class:`AgentContext` grows a trading parameter.

    Same guard as :func:`agents.models.assert_review_carries_no_trading_authority`,
    applied to the async path. The async path is the one that reaches production
    execution, so if only one of the two were checked it should be this one.
    """
    overlap = set(AgentContext.model_fields) & FORBIDDEN_CONTEXT_FIELDS
    if overlap:
        raise AuthorityViolationError(
            "AgentContext declares trading parameters it has no authority over: "
            f"{sorted(overlap)}. The agent layer is veto-only (ADR-002, ADR-006)."
        )


assert_context_carries_no_trading_authority()
