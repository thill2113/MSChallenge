"""A deterministic broker simulator."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from brokers.base import (
    AccountState,
    BrokerHealth,
    BrokerOrder,
    BrokerOrderRequest,
    BrokerPosition,
    Fill,
)
from domain.enums import BrokerCapability, OrderState
from domain.errors import BrokerRejectedError, BrokerTimeoutError, BrokerUnavailableError
from domain.values import TimestampUTC

SIMULATED_CAPABILITIES: frozenset[BrokerCapability] = frozenset(
    {
        BrokerCapability.SUBMIT,
        BrokerCapability.CANCEL,
        BrokerCapability.REPLACE,
        BrokerCapability.PAPER_TRADING,
        BrokerCapability.BRACKET,
        BrokerCapability.OCO,
        BrokerCapability.STOP_LOSS_CHILD,
        BrokerCapability.TAKE_PROFIT_CHILD,
    }
)
"""Deliberately excludes ``LIVE_TRADING``. The simulator can stand in for a paper
venue; it can never stand in for a live one."""


@unique
class FailureMode(StrEnum):
    """How the simulator misbehaves, for exercising the failure paths."""

    NONE = "NONE"
    TIMEOUT = "TIMEOUT"
    """Raises BrokerTimeoutError *after* recording the order — the genuinely
    dangerous case, where the caller cannot tell whether the order is working."""

    REJECT = "REJECT"
    UNAVAILABLE = "UNAVAILABLE"
    PARTIAL_FILL = "PARTIAL_FILL"
    NO_FILL = "NO_FILL"


class SimulatedBroker:
    """Deterministic, in-process venue.

    Fills at the requested limit price, or at the reference for market orders.
    No slippage and no queue modelling: inventing those numbers would make
    simulated results look like predictions, and they are not.
    """

    def __init__(
        self,
        *,
        broker_id: str = "simulator",
        account_id: str = "SIM-ACCOUNT",
        equity: Decimal = Decimal("100000"),
        failure_mode: FailureMode = FailureMode.NONE,
        healthy: bool = True,
        clock: TimestampUTC | None = None,
    ) -> None:
        self._broker_id = broker_id
        self._account_id = account_id
        self._equity = equity
        self._failure_mode = failure_mode
        self._healthy = healthy
        self._clock = clock
        self._orders: dict[str, BrokerOrder] = {}
        self._by_idempotency: dict[str, str] = {}
        self._positions: dict[str, BrokerPosition] = {}
        self._sequence = 0

    # -- configuration -------------------------------------------------------

    @property
    def broker_id(self) -> str:
        """Identifier recorded on orders and events."""
        return self._broker_id

    @property
    def capabilities(self) -> frozenset[BrokerCapability]:
        """What this simulator supports."""
        return SIMULATED_CAPABILITIES

    def set_failure_mode(self, mode: FailureMode) -> None:
        """Change how the simulator misbehaves."""
        self._failure_mode = mode

    def set_healthy(self, healthy: bool) -> None:
        """Toggle reported health."""
        self._healthy = healthy

    def set_clock(self, moment: TimestampUTC) -> None:
        """Pin the simulator's clock so results are reproducible."""
        self._clock = moment

    def _now(self) -> TimestampUTC:
        if self._clock is None:
            raise RuntimeError(
                "SimulatedBroker has no clock. Call set_clock() or pass clock= — the "
                "simulator never reads the wall clock, so replays stay deterministic."
            )
        return self._clock

    # -- Broker protocol ------------------------------------------------------

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        """Place an order, honouring idempotency and the configured failure mode."""
        if self._failure_mode is FailureMode.UNAVAILABLE:
            raise BrokerUnavailableError(f"{self._broker_id} is unavailable (simulated)")

        existing_id = self._by_idempotency.get(request.idempotency_key)
        if existing_id is not None:
            # Idempotent replay: return what the venue already has rather than
            # opening a second position. This is the behaviour a real adapter
            # must emulate, by key or by client order id.
            return self._orders[existing_id]

        now = self._now()
        self._sequence += 1
        broker_order_id = f"{self._broker_id}-{self._sequence:06d}"

        if self._failure_mode is FailureMode.REJECT:
            self._record(
                broker_order_id,
                request,
                state=OrderState.REJECTED,
                now=now,
                message="simulated rejection",
            )
            raise BrokerRejectedError(f"order {broker_order_id} rejected (simulated)")

        if self._failure_mode is FailureMode.TIMEOUT:
            # The order IS recorded before the raise. That is the point: a
            # timeout does not mean nothing happened, and a caller that assumes
            # otherwise and retries opens a duplicate position.
            self._record(
                broker_order_id,
                request,
                state=OrderState.UNKNOWN,
                now=now,
                message="simulated timeout; final state unknown",
            )
            raise BrokerTimeoutError(
                f"submission of {request.idempotency_key[:12]} timed out; state unknown. "
                "Reconcile before retrying."
            )

        if self._failure_mode is FailureMode.NO_FILL:
            return self._record(broker_order_id, request, state=OrderState.ACCEPTED, now=now)

        fill_price = request.limit_price if request.limit_price is not None else request.stop_price
        quantity = (
            request.quantity / Decimal(2)
            if self._failure_mode is FailureMode.PARTIAL_FILL
            else request.quantity
        )
        state = (
            OrderState.PARTIALLY_FILLED
            if self._failure_mode is FailureMode.PARTIAL_FILL
            else OrderState.FILLED
        )
        fill = Fill(
            fill_id=f"{broker_order_id}-F1",
            quantity=quantity,
            price=fill_price,
            filled_at=now,
        )
        order = self._record(
            broker_order_id,
            request,
            state=state,
            now=now,
            fills=(fill,),
            filled_quantity=quantity,
            average_fill_price=fill_price,
        )
        self._apply_fill(request, quantity, fill_price)
        return order

    def cancel_order(self, broker_order_id: str) -> BrokerOrder:
        """Cancel a working order."""
        order = self.get_order(broker_order_id)
        if order.is_terminal:
            return order
        cancelled = order.model_copy(
            update={"state": OrderState.CANCELLED, "updated_at": self._now()}
        )
        self._orders[broker_order_id] = cancelled
        return cancelled

    def replace_order(self, broker_order_id: str, request: BrokerOrderRequest) -> BrokerOrder:
        """Amend a working order."""
        order = self.get_order(broker_order_id)
        if order.is_terminal:
            raise BrokerRejectedError(f"cannot replace {broker_order_id}: already {order.state}")
        replaced = order.model_copy(
            update={
                "requested_quantity": request.quantity,
                "idempotency_key": request.idempotency_key,
                "updated_at": self._now(),
            }
        )
        self._orders[broker_order_id] = replaced
        self._by_idempotency[request.idempotency_key] = broker_order_id
        return replaced

    def get_order(self, broker_order_id: str) -> BrokerOrder:
        """Fetch one order."""
        try:
            return self._orders[broker_order_id]
        except KeyError:
            raise BrokerRejectedError(f"unknown order {broker_order_id}") from None

    def get_orders(self, *, since: datetime | None = None) -> Sequence[BrokerOrder]:
        """Fetch orders, newest last."""
        orders = sorted(self._orders.values(), key=lambda o: o.submitted_at or self._now())
        if since is None:
            return tuple(orders)
        return tuple(o for o in orders if o.submitted_at is not None and o.submitted_at >= since)

    def get_positions(self) -> Sequence[BrokerPosition]:
        """Fetch open positions."""
        return tuple(self._positions.values())

    def get_account_state(self) -> AccountState:
        """Fetch account state."""
        return AccountState(
            account_id=self._account_id,
            as_of=self._now(),
            equity=self._equity,
            cash=self._equity,
            buying_power=self._equity,
            positions=tuple(self._positions.values()),
        )

    def get_execution_updates(self, *, since: datetime | None = None) -> Sequence[BrokerOrder]:
        """Drain execution updates. The simulator fills synchronously, so this
        returns orders that changed since ``since``."""
        return self.get_orders(since=since)

    def health_check(self) -> BrokerHealth:
        """Report connectivity."""
        return BrokerHealth(
            broker_id=self._broker_id,
            healthy=self._healthy and self._failure_mode is not FailureMode.UNAVAILABLE,
            checked_at=self._now(),
            latency_ms=0,
            detail=None if self._healthy else "simulated outage",
        )

    # -- internals -------------------------------------------------------------

    def _record(
        self,
        broker_order_id: str,
        request: BrokerOrderRequest,
        *,
        state: OrderState,
        now: TimestampUTC,
        fills: tuple[Fill, ...] = (),
        filled_quantity: Decimal = Decimal(0),
        average_fill_price: Decimal | None = None,
        message: str | None = None,
    ) -> BrokerOrder:
        order = BrokerOrder(
            broker_order_id=broker_order_id,
            idempotency_key=request.idempotency_key,
            order_intent_id=request.order_intent_id,
            state=state,
            symbol=request.symbol,
            side=request.side,
            requested_quantity=request.quantity,
            filled_quantity=filled_quantity,
            average_fill_price=average_fill_price,
            fills=fills,
            submitted_at=now,
            updated_at=now,
            venue_message=message,
        )
        self._orders[broker_order_id] = order
        self._by_idempotency[request.idempotency_key] = broker_order_id
        return order

    def _apply_fill(self, request: BrokerOrderRequest, quantity: Decimal, price: Decimal) -> None:
        existing = self._positions.get(request.symbol)
        if existing is None:
            self._positions[request.symbol] = BrokerPosition(
                symbol=request.symbol,
                asset_class=request.asset_class,
                side=request.side,
                quantity=quantity,
                average_price=price,
                mark_price=price,
            )
            return
        total = existing.quantity + quantity
        blended = (existing.average_price * existing.quantity + price * quantity) / total
        self._positions[request.symbol] = existing.model_copy(
            update={"quantity": total, "average_price": blended, "mark_price": price}
        )


__all__ = ["SIMULATED_CAPABILITIES", "FailureMode", "SimulatedBroker"]
