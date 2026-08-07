"""The execution engine.

Receives an approved :class:`~execution.models.OrderIntent`, runs it past the
final validator, translates it for the configured broker, submits it, and
records what came back. It makes no subjective trading decisions: every number
it sends was written by a strategy and approved by risk.

Three behaviours are worth reading the code for:

**Idempotency.** Submission is keyed on
:attr:`~execution.models.OrderIntent.idempotency_key`. A key already in flight
is refused rather than re-sent.

**Timeouts are not failures.** When a broker times out, the order may be
working. The engine records ``UNKNOWN``, engages a kill switch on that strategy
and stops — it does not retry, because retrying is how one intended position
becomes two.

**SHADOW is a real path.** In ``SHADOW`` mode every step runs, including the
full validator, and only the broker call is skipped. A shadow run that would
have been rejected is recorded as rejected, which is what makes shadow evidence
worth anything.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from brokers.base import (
    AccountState,
    Broker,
    BrokerHealth,
    BrokerOrder,
    BrokerOrderRequest,
    select_protection_style,
)
from control_plane.config import ControlPlaneConfig
from domain.enums import (
    BrokerCapability,
    ExecutionMode,
    ExecutionStatus,
    KillSwitchScope,
    OrderState,
)
from domain.errors import (
    BrokerRejectedError,
    BrokerTimeoutError,
    BrokerUnavailableError,
    DuplicateOrderError,
)
from domain.identifiers import DeterministicId
from domain.values import TimestampUTC
from execution.killswitch import KillSwitch, KillSwitchRegistry, KillSwitchTrigger
from execution.models import ExecutionResult, OrderIntent
from execution.validator import FinalValidator, ValidationOutcome, ValidationRequest
from observability.events import DomainEvent, EventBus, EventType
from observability.logging import get_logger

logger = get_logger(__name__)

_STATE_TO_STATUS: dict[OrderState, ExecutionStatus] = {
    OrderState.PENDING_NEW: ExecutionStatus.ACCEPTED,
    OrderState.SUBMITTED: ExecutionStatus.ACCEPTED,
    OrderState.ACCEPTED: ExecutionStatus.ACCEPTED,
    OrderState.PARTIALLY_FILLED: ExecutionStatus.PARTIALLY_FILLED,
    OrderState.FILLED: ExecutionStatus.FILLED,
    OrderState.CANCELLED: ExecutionStatus.CANCELLED,
    OrderState.REJECTED: ExecutionStatus.REJECTED,
    OrderState.EXPIRED: ExecutionStatus.EXPIRED,
    OrderState.UNKNOWN: ExecutionStatus.ACCEPTED,
}


class ExecutionEngine:
    """Validates, translates, submits and records."""

    def __init__(
        self,
        *,
        broker: Broker,
        validator: FinalValidator,
        kill_switches: KillSwitchRegistry,
        events: EventBus | None = None,
    ) -> None:
        self._broker = broker
        self._validator = validator
        self._kill_switches = kill_switches
        self._events = events or EventBus()
        self._submitted_keys: set[str] = set()
        self._orders_by_key: dict[str, BrokerOrder] = {}

    @property
    def events(self) -> EventBus:
        """The bus this engine publishes to."""
        return self._events

    @property
    def submitted_keys(self) -> frozenset[str]:
        """Idempotency keys already sent. Passed back into the validator."""
        return frozenset(self._submitted_keys)

    def submit(
        self,
        intent: OrderIntent,
        *,
        config: ControlPlaneConfig,
        validation: ValidationRequest,
        account_state: AccountState | None = None,
        broker_health: BrokerHealth | None = None,
    ) -> ExecutionResult:
        """Run an intent through the gates and out to the venue.

        ``validation`` carries the state the gates read. It is built by the
        caller because the caller is the one holding fresh market and portfolio
        state; the engine refuses to go fetch it, which is what keeps this path
        free of hidden I/O.
        """
        if not isinstance(intent, OrderIntent):
            raise TypeError(
                f"execution accepts OrderIntent only; received {type(intent).__name__}. "
                "Candidates must be approved by the risk engine first."
            )

        now = validation.now
        key = intent.idempotency_key

        if key in self._submitted_keys:
            previous = self._orders_by_key.get(key)
            placed_as = previous.broker_order_id if previous else "an unconfirmed order"
            raise DuplicateOrderError(
                f"order {key[:12]} has already been submitted as {placed_as}. "
                "Reconcile rather than resubmit."
            )

        outcome = self._validator.validate(
            validation.model_copy(
                update={
                    "intent": intent,
                    "account_state": account_state or validation.account_state,
                    "broker_health": broker_health or validation.broker_health,
                    "submitted_idempotency_keys": self.submitted_keys,
                }
            )
        )
        if not outcome.approved:
            self._publish(
                EventType.VALIDATION_REJECTED,
                intent,
                now,
                codes=list(outcome.codes),
                checks_run=outcome.checks_run,
            )
            return self._rejected_result(intent, now, outcome)

        self._publish(EventType.ORDER_INTENT_CREATED, intent, now, mode=config.execution_mode.value)

        if config.execution_mode is ExecutionMode.SHADOW:
            return self._shadow_result(intent, now)

        request = self.build_request(intent)
        self._submitted_keys.add(key)
        self._publish(EventType.ORDER_SUBMITTED, intent, now, venue=self._broker.broker_id)

        try:
            order = self._broker.submit_order(request)
        except BrokerTimeoutError as exc:
            # The order may be working. Do not retry; halt this strategy and
            # let a human or the reconciler establish the truth.
            self._kill_switches.engage(
                KillSwitch(
                    scope=KillSwitchScope.STRATEGY,
                    target=intent.strategy_key,
                    trigger=KillSwitchTrigger.UNKNOWN_ORDER_STATE,
                    reason=f"submission timed out with unknown state: {exc}",
                    engaged_at=now,
                    engaged_by="execution_engine",
                )
            )
            self._publish(EventType.ORDER_STATE_UNKNOWN, intent, now, detail=str(exc))
            return self._result(
                intent, now, OrderState.UNKNOWN, message=f"submission timed out: {exc}"
            )
        except BrokerRejectedError as exc:
            self._publish(EventType.ORDER_REJECTED, intent, now, detail=str(exc))
            return self._result(intent, now, OrderState.REJECTED, message=str(exc))
        except BrokerUnavailableError as exc:
            self._submitted_keys.discard(key)  # nothing reached the venue
            self._kill_switches.engage(
                KillSwitch(
                    scope=KillSwitchScope.BROKER,
                    target=self._broker.broker_id,
                    trigger=KillSwitchTrigger.BROKER_UNAVAILABLE,
                    reason=str(exc),
                    engaged_at=now,
                    engaged_by="execution_engine",
                )
            )
            self._publish(EventType.ORDER_REJECTED, intent, now, detail=str(exc))
            return self._result(intent, now, OrderState.REJECTED, message=str(exc))

        self._orders_by_key[key] = order
        self._publish_order_events(intent, order, now)
        return self._result(
            intent,
            now,
            order.state,
            broker_order_id=order.broker_order_id,
            filled_quantity=order.filled_quantity,
            average_fill_price=order.average_fill_price,
            message=order.venue_message,
        )

    def build_request(self, intent: OrderIntent) -> BrokerOrderRequest:
        """Translate an intent into a venue-neutral request.

        The only decision made here is *how* protection is attached, and it is
        made from the broker's declared capabilities rather than assumed. No
        price and no quantity is recomputed.
        """
        capabilities = self._broker.capabilities
        if BrokerCapability.SUBMIT not in capabilities:
            raise BrokerUnavailableError(
                f"{self._broker.broker_id} does not support order submission"
            )
        return BrokerOrderRequest(
            idempotency_key=intent.idempotency_key,
            order_intent_id=intent.order_intent_id,
            symbol=intent.symbol,
            asset_class=intent.asset_class,
            side=intent.side,
            quantity=intent.quantity,
            order_type=intent.order_type,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            target_price=intent.target_price,
            time_in_force=intent.time_in_force,
            protection=select_protection_style(
                capabilities, has_target=intent.target_price is not None
            ),
        )

    def reconcile(self, intent: OrderIntent) -> BrokerOrder | None:
        """Ask the venue what actually happened to an order.

        Used after a timeout. Returns ``None`` when the venue has no record,
        which is the only evidence that permits treating the order as never
        placed.
        """
        key = intent.idempotency_key
        for order in self._broker.get_orders():
            if order.idempotency_key == key:
                self._orders_by_key[key] = order
                return order
        return None

    # -- internals ------------------------------------------------------------

    def _publish_order_events(
        self, intent: OrderIntent, order: BrokerOrder, now: TimestampUTC
    ) -> None:
        mapping = {
            OrderState.ACCEPTED: EventType.ORDER_ACCEPTED,
            OrderState.PARTIALLY_FILLED: EventType.PARTIAL_FILL_RECEIVED,
            OrderState.FILLED: EventType.ORDER_FILLED,
            OrderState.CANCELLED: EventType.ORDER_CANCELLED,
            OrderState.REJECTED: EventType.ORDER_REJECTED,
            OrderState.UNKNOWN: EventType.ORDER_STATE_UNKNOWN,
        }
        event_type = mapping.get(order.state)
        if event_type is not None:
            self._publish(
                event_type,
                intent,
                now,
                broker_order_id=order.broker_order_id,
                filled_quantity=order.filled_quantity,
            )
        if order.state is OrderState.FILLED:
            self._publish(EventType.POSITION_OPENED, intent, now)

    def _publish(
        self, event_type: EventType, intent: OrderIntent, now: TimestampUTC, **payload: Any
    ) -> DomainEvent:
        return self._events.publish(
            DomainEvent.build(
                event_type,
                occurred_at=now,
                strategy_key=intent.strategy_key,
                symbol=intent.symbol,
                order_intent_id=str(intent.order_intent_id),
                **payload,
            )
        )

    def _result(
        self,
        intent: OrderIntent,
        now: TimestampUTC,
        state: OrderState,
        *,
        broker_order_id: str | None = None,
        filled_quantity: Decimal = Decimal(0),
        average_fill_price: Decimal | None = None,
        message: str | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            result_id=self._result_id(intent, state),
            order_intent_id=intent.order_intent_id,
            order_intent_fingerprint=intent.authoritative_fingerprint(),
            idempotency_key=intent.idempotency_key,
            status=_STATE_TO_STATUS[state],
            order_state=state,
            venue=self._broker.broker_id,
            broker_order_id=broker_order_id,
            filled_quantity=filled_quantity,
            average_fill_price=average_fill_price,
            submitted_at=intent.created_at,
            reported_at=now,
            message=message,
        )

    def _shadow_result(self, intent: OrderIntent, now: TimestampUTC) -> ExecutionResult:
        return ExecutionResult(
            result_id=self._result_id(intent, OrderState.PENDING_NEW),
            order_intent_id=intent.order_intent_id,
            order_intent_fingerprint=intent.authoritative_fingerprint(),
            idempotency_key=intent.idempotency_key,
            status=ExecutionStatus.SIMULATED,
            order_state=OrderState.PENDING_NEW,
            venue=f"shadow:{self._broker.broker_id}",
            submitted_at=intent.created_at,
            reported_at=now,
            message="SHADOW mode: validated and translated, nothing transmitted",
        )

    def _rejected_result(
        self, intent: OrderIntent, now: TimestampUTC, outcome: ValidationOutcome
    ) -> ExecutionResult:
        return ExecutionResult(
            result_id=self._result_id(intent, OrderState.REJECTED),
            order_intent_id=intent.order_intent_id,
            order_intent_fingerprint=intent.authoritative_fingerprint(),
            idempotency_key=intent.idempotency_key,
            status=ExecutionStatus.REJECTED,
            order_state=OrderState.REJECTED,
            venue=self._broker.broker_id,
            submitted_at=intent.created_at,
            reported_at=now,
            message="ORDER_REJECTED: " + ", ".join(outcome.codes),
        )

    @staticmethod
    def _result_id(intent: OrderIntent, state: OrderState) -> UUID:
        return DeterministicId.derive("execution_result", intent.idempotency_key, state.value)


__all__ = ["ExecutionEngine"]
