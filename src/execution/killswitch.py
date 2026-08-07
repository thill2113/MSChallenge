"""Layered kill switches.

Five scopes, checked from broadest to narrowest. Any active switch covering an
order stops it.

The asymmetry is deliberate and is the whole design: **engaging is easy,
clearing is hard.** Deterministic controls may engage a switch automatically;
agents may only *recommend* engaging one; and a switch engaged by a risk failure
cannot be cleared by any automated process at all — it takes a named human.

The reasoning is the same as a circuit breaker in a building. The thing that
trips it is usually a symptom of something nobody has diagnosed yet, and an
automatic reset turns one fault into a repeating one.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum, unique

from pydantic import Field

from domain.base import FrozenModel
from domain.enums import KillSwitchScope
from domain.errors import AuthorityViolationError, KillSwitchEngagedError
from domain.values import NonEmptyText, TimestampUTC


@unique
class KillSwitchTrigger(StrEnum):
    """What engaged a switch. Determines whether it may be cleared automatically."""

    MANUAL = "MANUAL"
    """A human engaged it. A human clears it."""

    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    WEEKLY_LOSS_LIMIT = "WEEKLY_LOSS_LIMIT"
    DRAWDOWN_LIMIT = "DRAWDOWN_LIMIT"
    CONSECUTIVE_LOSSES = "CONSECUTIVE_LOSSES"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    UNKNOWN_ORDER_STATE = "UNKNOWN_ORDER_STATE"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
    STALE_MARKET_DATA = "STALE_MARKET_DATA"


AUTO_CLEARABLE_TRIGGERS: frozenset[KillSwitchTrigger] = frozenset(
    {KillSwitchTrigger.BROKER_UNAVAILABLE, KillSwitchTrigger.STALE_MARKET_DATA}
)
"""The only triggers an automated process may clear.

Both describe a *transient infrastructure* condition that is directly
observable: connectivity returned, or fresh data arrived. Every other trigger
describes a risk event, and a risk event that resolves itself without anyone
looking is exactly the situation this mechanism exists to prevent.
"""


class KillSwitch(FrozenModel):
    """One engaged switch."""

    scope: KillSwitchScope
    target: str = Field(
        default="*",
        max_length=128,
        description="What the scope names: strategy key, symbol, broker id, account id. "
        "'*' for SYSTEM.",
    )
    trigger: KillSwitchTrigger
    reason: NonEmptyText
    engaged_at: TimestampUTC
    engaged_by: str = Field(min_length=1, max_length=128)

    @property
    def key(self) -> tuple[KillSwitchScope, str]:
        """Registry key."""
        return (self.scope, self.target)

    @property
    def auto_clearable(self) -> bool:
        """Whether an automated process may clear this switch."""
        return self.trigger in AUTO_CLEARABLE_TRIGGERS


class KillSwitchRegistry:
    """Tracks engaged switches and answers "may this order proceed".

    Lookups are dictionary reads: this sits in the hot path and must not be the
    reason an order is late.
    """

    def __init__(self) -> None:
        self._active: dict[tuple[KillSwitchScope, str], KillSwitch] = {}
        self._history: list[tuple[str, KillSwitch]] = []

    def engage(self, switch: KillSwitch) -> KillSwitch:
        """Engage a switch. Re-engaging an active scope keeps the original.

        Keeping the first one matters: the earliest trigger is the one closest
        to the root cause, and overwriting it with a later downstream symptom
        loses the diagnosis.
        """
        existing = self._active.get(switch.key)
        if existing is not None:
            return existing
        self._active[switch.key] = switch
        self._history.append(("ENGAGED", switch))
        return switch

    def clear(
        self, scope: KillSwitchScope, target: str = "*", *, cleared_by: str, automated: bool = False
    ) -> None:
        """Clear a switch.

        ``automated=True`` callers may only clear transient infrastructure
        triggers; anything risk-related raises.
        """
        switch = self._active.get((scope, target))
        if switch is None:
            return
        if automated and not switch.auto_clearable:
            raise AuthorityViolationError(
                f"{scope}:{target} was engaged by {switch.trigger} and cannot be cleared "
                "automatically. A named human must review the cause and clear it "
                "explicitly (ADR-008)."
            )
        del self._active[(scope, target)]
        self._history.append((f"CLEARED by {cleared_by}", switch))

    def active(self) -> tuple[KillSwitch, ...]:
        """Every currently engaged switch."""
        return tuple(self._active.values())

    def blocking(
        self,
        *,
        strategy_key: str | None = None,
        symbol: str | None = None,
        broker_id: str | None = None,
        account_id: str | None = None,
    ) -> tuple[KillSwitch, ...]:
        """Switches that would stop an order with these attributes."""
        candidates = [
            (KillSwitchScope.SYSTEM, "*"),
            (KillSwitchScope.STRATEGY, strategy_key),
            (KillSwitchScope.SYMBOL, symbol),
            (KillSwitchScope.BROKER, broker_id),
            (KillSwitchScope.ACCOUNT, account_id),
        ]
        return tuple(
            self._active[(scope, target)]
            for scope, target in candidates
            if target is not None and (scope, target) in self._active
        )

    def assert_clear(
        self,
        *,
        strategy_key: str | None = None,
        symbol: str | None = None,
        broker_id: str | None = None,
        account_id: str | None = None,
    ) -> None:
        """Raise if any switch covers this order."""
        blocking = self.blocking(
            strategy_key=strategy_key, symbol=symbol, broker_id=broker_id, account_id=account_id
        )
        if blocking:
            detail = "; ".join(f"{s.scope}:{s.target} ({s.trigger}) {s.reason}" for s in blocking)
            raise KillSwitchEngagedError(
                f"trading halted by {len(blocking)} kill switch(es): {detail}"
            )

    def history(self) -> tuple[tuple[str, KillSwitch], ...]:
        """Append-only engage/clear log."""
        return tuple(self._history)

    def __len__(self) -> int:
        return len(self._active)

    def __iter__(self) -> Iterator[KillSwitch]:
        return iter(self._active.values())
