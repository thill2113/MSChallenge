"""Human-approved runtime configuration.

Every field here answers a question that used to be answered by a person
staring at an individual trade: *is this instrument allowed, in this session,
under this mode, with this much of the account, against this broker?* Answering
them once — deliberately, with an approval record — is what makes answering them
per-trade unnecessary.

The configuration carries a ``configuration_hash``. Every
:class:`~execution.models.OrderIntent` records the hash in force when it was
built, so any order can be traced to the exact envelope that permitted it.
"""

from __future__ import annotations

from datetime import time
from typing import ClassVar, Self

from pydantic import Field, model_validator

from domain.base import AuthoritativeModel, FrozenModel
from domain.enums import AgentContextPolicy, AssetClass, ExecutionMode
from domain.errors import ControlPlaneError
from domain.values import ExactDecimal, NonEmptyText, Ratio, Symbol, TimestampUTC
from strategies.promotion import HumanApproval


class TradingSession(FrozenModel):
    """A window during which trading is permitted, in exchange local time.

    Times are naive by design: a session is defined relative to the exchange's
    calendar, and pinning it to a UTC offset would silently shift it across a
    daylight-saving boundary.
    """

    name: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_]+$")
    exchange_timezone: str = Field(min_length=1, max_length=64, examples=["America/New_York"])
    opens_at: time
    closes_at: time
    weekdays: tuple[int, ...] = Field(
        default=(0, 1, 2, 3, 4), description="ISO weekday numbers, Monday = 0."
    )

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        if self.closes_at <= self.opens_at:
            raise ValueError("session close must be after open")
        if not self.weekdays or any(d < 0 or d > 6 for d in self.weekdays):
            raise ValueError("weekdays must be a non-empty subset of 0..6")
        return self


class InstrumentPermission(FrozenModel):
    """One instrument this system is allowed to trade, and how much of it."""

    symbol: Symbol
    asset_class: AssetClass = AssetClass.EQUITY
    max_position_notional_fraction: Ratio = Field(
        description="Per-instrument ceiling. Applied in addition to the global limit; "
        "the tighter of the two wins."
    )
    correlation_group: str | None = Field(
        default=None,
        max_length=64,
        description="Bucket for correlated-exposure limits, e.g. a sector or theme. "
        "Set here rather than by a strategy: what counts as correlated is a "
        "portfolio judgement, and a strategy that assigned its own groups could "
        "escape the limit by inventing a new one.",
    )
    min_average_daily_volume: ExactDecimal | None = Field(
        default=None, ge=0, description="Liquidity floor. None disables the check."
    )
    max_spread_fraction: Ratio | None = Field(
        default=None,
        description="Widest acceptable bid/ask spread as a fraction of mid. "
        "None disables the check.",
    )


class AccountAllocation(FrozenModel):
    """How much of an account this system may put to work."""

    account_id: str = Field(min_length=1, max_length=64)
    allocated_equity_fraction: Ratio = Field(
        description="Fraction of account equity this system may treat as its own. "
        "Risk limits are computed against the allocated amount, not the whole account."
    )
    broker_id: str = Field(min_length=1, max_length=32)


class ControlPlaneConfig(AuthoritativeModel):
    """The complete, human-approved operating envelope.

    Construction requires every field. There is no partially-configured state,
    and no field falls back to a value nobody chose.
    """

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "revision",
        "execution_mode",
        "agent_context_policy",
        "agent_context_max_age_seconds",
        "enabled_strategy_keys",
        "instruments",
        "allocation",
        "sessions",
        "risk_limits_name",
        "risk_limits_fingerprint",
    )

    revision: int = Field(ge=1, description="Monotonic. Every approved change increments it.")
    execution_mode: ExecutionMode = Field(
        description="How far orders may travel. Only a human changes this."
    )
    agent_context_policy: AgentContextPolicy = Field(
        description="Behaviour when agent context is missing or stale. No default exists; "
        "see ADR-006."
    )
    agent_context_max_age_seconds: int = Field(
        gt=0,
        description="Beyond this age a published AgentContext is stale regardless of its "
        "own expires_at. Bounds how long a dead agent's opinion keeps counting.",
    )

    enabled_strategy_keys: tuple[str, ...] = Field(
        default=(),
        description="strategy_id@version entries cleared to trade. An empty tuple means "
        "the system is configured but idle — a valid and safe state.",
    )
    instruments: tuple[InstrumentPermission, ...] = Field(default=())
    allocation: AccountAllocation
    sessions: tuple[TradingSession, ...] = Field(min_length=1)

    risk_limits_name: str = Field(min_length=1, max_length=64)
    risk_limits_fingerprint: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="Binds this configuration to one exact RiskLimits record, so "
        "'approved with these limits' is a checkable claim.",
    )

    approved_by: str = Field(min_length=1, max_length=128)
    approved_at: TimestampUTC
    note: NonEmptyText | None = None

    @model_validator(mode="after")
    def _check_coherence(self) -> Self:
        symbols = [i.symbol for i in self.instruments]
        if len(symbols) != len(set(symbols)):
            raise ValueError("duplicate symbols in instrument permissions")
        if len({s.name for s in self.sessions}) != len(self.sessions):
            raise ValueError("duplicate session names")
        if self.execution_mode is not ExecutionMode.DISABLED and not self.enabled_strategy_keys:
            raise ValueError(
                f"execution_mode {self.execution_mode} with no enabled strategies is "
                "incoherent; use DISABLED to express 'not trading'"
            )
        if self.enabled_strategy_keys and not self.instruments:
            raise ValueError("strategies are enabled but no instruments are permitted")
        return self

    @property
    def configuration_hash(self) -> str:
        """Fingerprint recorded on every order built under this configuration."""
        return self.authoritative_fingerprint()

    def permits_strategy(self, strategy_key: str) -> bool:
        """Whether ``strategy_id@version`` is cleared to trade."""
        return strategy_key in self.enabled_strategy_keys

    def instrument(self, symbol: str) -> InstrumentPermission | None:
        """Permission record for ``symbol``, or ``None`` if not permitted."""
        return next((i for i in self.instruments if i.symbol == symbol), None)


def apply_configuration_change(
    current: ControlPlaneConfig | None,
    proposed: ControlPlaneConfig,
    approval: HumanApproval,
) -> ControlPlaneConfig:
    """Adopt a new configuration, or raise.

    The approval must bind to the proposed configuration's fingerprint, so an
    approval of one revision cannot be replayed onto another. Revisions must
    increase, so a rollback is an explicit new revision rather than a silent
    reversion nobody notices.
    """
    if approval.version_fingerprint != proposed.configuration_hash:
        raise ControlPlaneError(
            f"approval fingerprint {approval.version_fingerprint} does not match the "
            f"proposed configuration {proposed.configuration_hash}; the configuration "
            "changed after it was approved"
        )
    if current is not None and proposed.revision <= current.revision:
        raise ControlPlaneError(
            f"configuration revision must increase: {proposed.revision} <= {current.revision}. "
            "To revert, publish a new revision with the previous content."
        )
    if proposed.execution_mode is ExecutionMode.LIVE:
        raise ControlPlaneError(
            "ExecutionMode.LIVE is reserved and not implemented. Reaching it requires "
            "the work tracked as decision D-5, not a configuration change."
        )
    return proposed
