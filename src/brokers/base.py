"""The generic broker interface.

No part of the system above this module knows what a Robinhood, a Tradier or an
Alpaca is. Strategies, risk, portfolio and agents operate on internal domain
models; an adapter translates those into one venue's dialect and translates the
response back.

Adapters **translate**. They do not reinterpret. If a venue cannot express an
order faithfully — no bracket support, no fractional quantities, a tick size the
intent violates — the adapter must fail rather than approximate, because an
approximation is a trade nobody authorised.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import ClassVar, Protocol, Self, runtime_checkable
from uuid import UUID

from pydantic import Field, model_validator

from domain.base import AuthoritativeModel, FrozenModel
from domain.enums import (
    AssetClass,
    BrokerCapability,
    OrderState,
    OrderType,
    ProtectionStyle,
    Side,
    TimeInForce,
)
from domain.values import (
    ExactDecimal,
    NonEmptyText,
    Price,
    Quantity,
    SignedAmount,
    Symbol,
    TimestampUTC,
)


class BrokerOrderRequest(AuthoritativeModel):
    """A venue-neutral order, ready for translation.

    Produced only from an approved :class:`~execution.models.OrderIntent`. The
    ``idempotency_key`` is derived from the intent, so a retry after a timeout
    reaches the venue as the same logical order rather than a second position.
    """

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "idempotency_key",
        "symbol",
        "asset_class",
        "side",
        "quantity",
        "order_type",
        "limit_price",
        "stop_price",
        "target_price",
        "time_in_force",
        "protection",
    )

    idempotency_key: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="Stable across retries of the same logical order.",
    )
    order_intent_id: UUID
    symbol: Symbol
    asset_class: AssetClass
    side: Side
    quantity: Quantity
    order_type: OrderType
    limit_price: Price | None = None
    stop_price: Price
    target_price: Price | None = None
    time_in_force: TimeInForce
    protection: ProtectionStyle = Field(
        description="How stop and target are attached. Chosen from the venue's "
        "declared capabilities, never assumed."
    )

    @model_validator(mode="after")
    def _check_protection(self) -> Self:
        if self.protection is ProtectionStyle.BRACKET and self.target_price is None:
            raise ValueError("a bracket order requires a target_price")
        return self


class Fill(FrozenModel):
    """One execution against an order."""

    fill_id: str = Field(min_length=1, max_length=128)
    quantity: Quantity
    price: Price
    filled_at: TimestampUTC
    fees: ExactDecimal = Field(default=ExactDecimal(0), ge=0)


class BrokerOrder(FrozenModel):
    """A venue's view of an order, normalised.

    ``state`` may legitimately be :attr:`~domain.enums.OrderState.UNKNOWN`. That
    is information, not an error: it means the venue has not told us, and the
    engine must reconcile rather than guess.
    """

    broker_order_id: str | None = Field(
        default=None,
        max_length=128,
        description="None when the venue never acknowledged — e.g. a timeout.",
    )
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    order_intent_id: UUID
    state: OrderState
    symbol: Symbol
    side: Side
    requested_quantity: Quantity
    filled_quantity: ExactDecimal = Field(default=ExactDecimal(0), ge=0)
    average_fill_price: Price | None = None
    fills: tuple[Fill, ...] = ()
    submitted_at: TimestampUTC | None = None
    updated_at: TimestampUTC | None = None
    venue_message: NonEmptyText | None = None
    raw_payload_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @property
    def is_terminal(self) -> bool:
        """Whether this order will not change state again."""
        from domain.enums import TERMINAL_ORDER_STATES

        return self.state in TERMINAL_ORDER_STATES

    @property
    def remaining_quantity(self) -> ExactDecimal:
        """Quantity still working."""
        return self.requested_quantity - self.filled_quantity


class BrokerPosition(FrozenModel):
    """A venue's view of one open position."""

    symbol: Symbol
    asset_class: AssetClass = AssetClass.EQUITY
    side: Side
    quantity: Quantity
    average_price: Price
    mark_price: Price | None = None


class AccountState(FrozenModel):
    """A venue's view of the account.

    ``as_of`` drives the staleness gate in the final validator. A broker that
    cannot say when its numbers were true forces the caller to supply the
    timestamp of the fetch, which is the honest fallback.
    """

    account_id: str = Field(min_length=1, max_length=64)
    as_of: TimestampUTC
    equity: ExactDecimal = Field(gt=0)
    cash: SignedAmount
    buying_power: ExactDecimal = Field(ge=0)
    maintenance_margin: ExactDecimal = Field(default=ExactDecimal(0), ge=0)
    positions: tuple[BrokerPosition, ...] = ()


class BrokerHealth(FrozenModel):
    """Result of a connectivity check."""

    broker_id: str
    healthy: bool
    checked_at: TimestampUTC
    latency_ms: int | None = Field(default=None, ge=0)
    detail: NonEmptyText | None = None


@runtime_checkable
class Broker(Protocol):
    """What every venue adapter must provide.

    Methods that reach a venue raise :class:`~domain.errors.BrokerTimeoutError`
    when the outcome is genuinely unknown, and
    :class:`~domain.errors.BrokerRejectedError` only when the venue gave a
    definite refusal. Conflating the two is how a timed-out order becomes two
    positions.
    """

    @property
    def broker_id(self) -> str:
        """Stable identifier recorded on every order and event."""
        ...

    @property
    def capabilities(self) -> frozenset[BrokerCapability]:
        """What this venue can actually do. The engine asks; it never assumes."""
        ...

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        """Place an order. Must be idempotent on ``request.idempotency_key``."""
        ...

    def cancel_order(self, broker_order_id: str) -> BrokerOrder:
        """Request cancellation and return the resulting order state."""
        ...

    def replace_order(self, broker_order_id: str, request: BrokerOrderRequest) -> BrokerOrder:
        """Amend a working order, where the strategy's rules permit it."""
        ...

    def get_order(self, broker_order_id: str) -> BrokerOrder:
        """Fetch current state for one order."""
        ...

    def get_orders(self, *, since: datetime | None = None) -> Sequence[BrokerOrder]:
        """Fetch orders, optionally limited to those updated since an instant."""
        ...

    def get_positions(self) -> Sequence[BrokerPosition]:
        """Fetch open positions."""
        ...

    def get_account_state(self) -> AccountState:
        """Fetch account equity, cash and buying power."""
        ...

    def get_execution_updates(self, *, since: datetime | None = None) -> Sequence[BrokerOrder]:
        """Drain execution updates — fills, cancels, rejections.

        Polling by default. An adapter with a streaming venue declares
        :attr:`~domain.enums.BrokerCapability.EXECUTION_STREAM` and buffers the
        stream so this call stays non-blocking.
        """
        ...

    def health_check(self) -> BrokerHealth:
        """Cheap connectivity probe. Must not place, cancel or amend anything."""
        ...


def select_protection_style(
    capabilities: frozenset[BrokerCapability], *, has_target: bool
) -> ProtectionStyle:
    """Pick the strongest venue-native protection the broker actually supports.

    Preferring native protection is a deliberate resilience choice: a stop that
    lives at the venue survives this application crashing, and one that lives in
    a Python process does not (ADR-007).
    """
    if has_target and BrokerCapability.BRACKET in capabilities:
        return ProtectionStyle.BRACKET
    if BrokerCapability.STOP_LOSS_CHILD in capabilities:
        return ProtectionStyle.STOP_ONLY
    return ProtectionStyle.NONE
