"""Internal domain events.

Every meaningful transition publishes one. The bus is in-process and
synchronous by default — a modular monolith, not a message broker — but
handlers are explicitly permitted to be slow only if registered as
``deferred``, because the hot path must not wait on journalling or analytics.

A handler that raises does **not** propagate. Publishing an event is
bookkeeping; a broken metrics sink must never abort a trade that has already
been authorised. Failures are logged and counted instead.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from pydantic import Field

from domain.base import FrozenModel, canonical_form
from domain.enums import EventType
from domain.values import TimestampUTC
from observability.logging import get_logger

logger = get_logger(__name__)


class DomainEvent(FrozenModel):
    """One thing that happened.

    ``payload`` is a canonicalised dict rather than a typed union: events are
    read by journalling, metrics and humans, none of which benefit from twenty
    near-identical classes. The ``type`` field carries the discrimination.
    """

    event_type: EventType
    occurred_at: TimestampUTC
    correlation_id: str | None = None
    strategy_key: str | None = None
    symbol: str | None = None
    order_intent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def build(
        cls,
        event_type: EventType,
        *,
        occurred_at: TimestampUTC,
        correlation_id: str | None = None,
        strategy_key: str | None = None,
        symbol: str | None = None,
        order_intent_id: str | None = None,
        **payload: Any,
    ) -> DomainEvent:
        """Build an event, canonicalising the payload so it stays JSON-safe."""
        return cls(
            event_type=event_type,
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            strategy_key=strategy_key,
            symbol=symbol,
            order_intent_id=order_intent_id,
            payload={k: canonical_form(v) for k, v in payload.items()},
        )


EventHandler = Callable[[DomainEvent], None]


class EventBus:
    """In-process publish/subscribe.

    Two handler tiers:

    * **immediate** — runs inline. For anything the next step depends on.
    * **deferred** — buffered and drained by :meth:`drain` outside the hot path.
      Journalling, analytics and agent triggers belong here.

    The split is the whole point. Without it, adding an audit sink silently
    lengthens the path between a signal and a fill.
    """

    def __init__(self) -> None:
        self._immediate: dict[EventType | None, list[EventHandler]] = {}
        self._deferred: list[EventHandler] = []
        self._buffer: list[DomainEvent] = []
        self._log: list[DomainEvent] = []
        self._handler_failures = 0

    def subscribe(self, handler: EventHandler, *, event_type: EventType | None = None) -> None:
        """Register an inline handler. ``event_type=None`` subscribes to everything."""
        self._immediate.setdefault(event_type, []).append(handler)

    def subscribe_deferred(self, handler: EventHandler) -> None:
        """Register a handler that runs only when :meth:`drain` is called."""
        self._deferred.append(handler)

    def publish(self, event: DomainEvent) -> DomainEvent:
        """Record and dispatch ``event``."""
        self._log.append(event)
        if self._deferred:
            self._buffer.append(event)

        for handler in (
            *self._immediate.get(event.event_type, ()),
            *self._immediate.get(None, ()),
        ):
            self._safely(handler, event)
        return event

    def drain(self) -> int:
        """Run deferred handlers over everything buffered. Returns the count."""
        buffered, self._buffer = self._buffer, []
        for event in buffered:
            for handler in self._deferred:
                self._safely(handler, event)
        return len(buffered)

    def _safely(self, handler: EventHandler, event: DomainEvent) -> None:
        try:
            handler(event)
        except Exception:
            # An observer must never be able to fail a trade.
            self._handler_failures += 1
            logger.exception(
                "event handler failed",
                extra={"event_type": event.event_type.value, "handler": repr(handler)},
            )

    @property
    def handler_failures(self) -> int:
        """How many handler invocations raised. Non-zero deserves an alert."""
        return self._handler_failures

    def history(self, event_type: EventType | None = None) -> tuple[DomainEvent, ...]:
        """Every published event, oldest first, optionally filtered."""
        if event_type is None:
            return tuple(self._log)
        return tuple(e for e in self._log if e.event_type is event_type)

    def __len__(self) -> int:
        return len(self._log)

    def __iter__(self) -> Iterator[DomainEvent]:
        return iter(self._log)


__all__ = ["DomainEvent", "EventBus", "EventHandler", "EventType"]
