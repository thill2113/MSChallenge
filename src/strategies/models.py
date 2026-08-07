"""Strategy outputs and versioning records.

The strategy engine is the **only** component permitted to author trading
parameters (side, quantity, stop, target, order type, risk fraction). Every
other layer either approves, vetoes or transmits what is written here — see
ADR-001 and ADR-002.

A strategy evaluation always terminates in exactly one of two records:
:class:`TradeCandidate` or :class:`NoTrade`. There is no third "maybe" state, and
``None`` is never a valid strategy result.
"""

from __future__ import annotations

from enum import StrEnum, unique
from typing import ClassVar, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain.base import AuthoritativeModel, FrozenModel
from domain.enums import AssetClass, OrderType, PromotionStage, Side, TimeInForce
from domain.identifiers import DeterministicId
from domain.values import NonEmptyText, Price, Quantity, Ratio, Symbol, TimestampUTC

STRATEGY_ID_PATTERN = r"^[a-z0-9_]{3,64}$"
SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"


@unique
class NoTradeReason(StrEnum):
    """Why a strategy declined to act.

    Recording the reason is not bookkeeping for its own sake: "we saw nothing"
    and "we could not see" are different failures, and only the second one is a
    bug.
    """

    NO_SETUP = "NO_SETUP"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    FILTERED_BY_REGIME = "FILTERED_BY_REGIME"
    INSTRUMENT_NOT_TRADABLE = "INSTRUMENT_NOT_TRADABLE"
    MARKET_CLOSED = "MARKET_CLOSED"
    STRATEGY_DISABLED = "STRATEGY_DISABLED"


class StrategyParameter(FrozenModel):
    """One named parameter of a strategy version.

    Values are held as strings so a version record round-trips through JSON and
    the ledger with exactly one spelling. Typing is the strategy's own concern.
    """

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    value: str = Field(max_length=256)


class StrategyMetadata(FrozenModel):
    """Identity of a strategy, independent of any particular version."""

    strategy_id: str = Field(pattern=STRATEGY_ID_PATTERN)
    display_name: str = Field(min_length=1, max_length=128)
    description: NonEmptyText
    owner: str = Field(
        min_length=1, max_length=128, description="Human accountable for this strategy."
    )
    asset_classes: tuple[AssetClass, ...] = Field(min_length=1)
    required_signals: tuple[str, ...] = ()
    required_regime_classifier: str | None = None


class StrategyVersion(AuthoritativeModel):
    """An immutable, promotable revision of a strategy.

    Backtests, candidates and ledger rows all reference ``(strategy_id,
    version)``. Because this record is frozen and fingerprinted, a result can
    always be traced to the exact parameter set that produced it (ADR-004).
    """

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "strategy_id",
        "version",
        "code_fingerprint",
        "parameters",
    )

    strategy_id: str = Field(pattern=STRATEGY_ID_PATTERN)
    version: str = Field(pattern=SEMVER_PATTERN)
    code_fingerprint: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="SHA-256 of the strategy implementation as promoted. Any change "
        "to behaviour must produce a new version, not a new fingerprint on an old one.",
    )
    parameters: tuple[StrategyParameter, ...] = ()
    stage: PromotionStage = PromotionStage.DEVELOPMENT
    created_at: TimestampUTC
    parent_version: str | None = Field(default=None, pattern=SEMVER_PATTERN)
    notes: NonEmptyText | None = None

    @property
    def key(self) -> str:
        """``strategy_id@version`` — the registry lookup key."""
        return f"{self.strategy_id}@{self.version}"

    def parameter_map(self) -> dict[str, str]:
        """Parameters as a plain dict, for readability at call sites."""
        return {p.name: p.value for p in self.parameters}

    @model_validator(mode="after")
    def _reject_duplicate_parameters(self) -> Self:
        names = [p.name for p in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("duplicate parameter names in strategy version")
        return self


class TradeCandidate(AuthoritativeModel):
    """A fully specified, deterministic proposal to trade.

    Every field the market would feel is set here and nowhere else. Downstream
    layers bind to :meth:`~domain.base.AuthoritativeModel.authoritative_fingerprint`,
    so swapping one candidate for another after approval is detectable.

    A candidate always carries a stop. A proposal without a defined loss is not
    a trade this system knows how to size, approve, or journal.
    """

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "strategy_id",
        "strategy_version",
        "symbol",
        "asset_class",
        "side",
        "as_of",
        "order_type",
        "limit_price",
        "quantity",
        "reference_price",
        "stop_price",
        "target_price",
        "time_in_force",
        "account_risk_fraction",
    )

    candidate_id: UUID
    strategy_id: str = Field(pattern=STRATEGY_ID_PATTERN)
    strategy_version: str = Field(pattern=SEMVER_PATTERN)

    symbol: Symbol
    asset_class: AssetClass = AssetClass.EQUITY
    side: Side
    as_of: TimestampUTC

    order_type: OrderType
    limit_price: Price | None = None
    quantity: Quantity
    reference_price: Price = Field(
        description="Price the decision was reasoned from. Defines risk-per-share "
        "together with stop_price."
    )
    stop_price: Price
    target_price: Price | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    account_risk_fraction: Ratio = Field(
        description="Fraction of account equity this candidate puts at risk if the "
        "stop is hit. Authored by the strategy, validated by the risk engine."
    )

    rationale: NonEmptyText | None = Field(
        default=None,
        description="Human-readable explanation. Excluded from the fingerprint: "
        "re-wording a rationale must not change the trade.",
    )
    inputs_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="Fingerprint of the snapshot/signals this decision was derived "
        "from. Lineage only.",
    )

    @model_validator(mode="after")
    def _check_price_geometry(self) -> Self:
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and self.limit_price is None:
            raise ValueError(f"{self.order_type} candidate requires a limit_price")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("MARKET candidate must not carry a limit_price")

        if self.side is Side.BUY:
            if self.stop_price >= self.reference_price:
                raise ValueError("long candidate requires stop_price below reference_price")
            if self.target_price is not None and self.target_price <= self.reference_price:
                raise ValueError("long candidate requires target_price above reference_price")
        else:
            if self.stop_price <= self.reference_price:
                raise ValueError("short candidate requires stop_price above reference_price")
            if self.target_price is not None and self.target_price >= self.reference_price:
                raise ValueError("short candidate requires target_price below reference_price")
        return self

    @property
    def risk_per_unit(self) -> Price:
        """Absolute distance between entry reference and stop."""
        return abs(self.reference_price - self.stop_price)

    @classmethod
    def derive_id(
        cls, *, strategy_id: str, strategy_version: str, symbol: str, as_of: TimestampUTC
    ) -> UUID:
        """Derive the candidate id from its origin, so replays are idempotent."""
        return DeterministicId.derive(
            "trade_candidate", strategy_id, strategy_version, symbol, as_of.isoformat()
        )


class NoTrade(AuthoritativeModel):
    """The strategy evaluated the market and declined to act.

    This is a first-class, journalled outcome. Silence is not an acceptable
    alternative: a system that only records its trades cannot tell "the strategy
    passed" from "the strategy never ran".
    """

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "strategy_id",
        "strategy_version",
        "symbol",
        "as_of",
        "reason",
    )

    decision_id: UUID
    strategy_id: str = Field(pattern=STRATEGY_ID_PATTERN)
    strategy_version: str = Field(pattern=SEMVER_PATTERN)
    symbol: Symbol
    as_of: TimestampUTC
    reason: NoTradeReason
    detail: NonEmptyText | None = None
    inputs_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def derive_id(
        cls, *, strategy_id: str, strategy_version: str, symbol: str, as_of: TimestampUTC
    ) -> UUID:
        """Derive the decision id from its origin, so replays are idempotent."""
        return DeterministicId.derive(
            "no_trade", strategy_id, strategy_version, symbol, as_of.isoformat()
        )


StrategyDecision = TradeCandidate | NoTrade
"""The complete result space of a strategy evaluation."""
