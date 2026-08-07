"""The event bus records everything and can never break a trade.

Observability is bookkeeping. A metrics sink that raises must not abort an order
that has already passed every risk gate — so handler failures are counted and
logged, never propagated.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from domain.enums import EventType
from observability.events import DomainEvent, EventBus

NOW = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)


def _event(event_type: EventType = EventType.ORDER_SUBMITTED, **payload: Any) -> DomainEvent:
    return DomainEvent.build(
        event_type,
        occurred_at=NOW,
        strategy_key="fixture_double@1.0.0",
        symbol="ACME",
        **payload,
    )


class TestPublication:
    def test_events_are_recorded_in_order(self):
        bus = EventBus()
        bus.publish(_event(EventType.TRADE_CANDIDATE_GENERATED))
        bus.publish(_event(EventType.RISK_APPROVED))
        assert [e.event_type for e in bus] == [
            EventType.TRADE_CANDIDATE_GENERATED,
            EventType.RISK_APPROVED,
        ]

    def test_handlers_can_subscribe_to_one_type_or_all(self):
        bus = EventBus()
        specific: list[DomainEvent] = []
        everything: list[DomainEvent] = []
        bus.subscribe(specific.append, event_type=EventType.ORDER_FILLED)
        bus.subscribe(everything.append)

        bus.publish(_event(EventType.ORDER_SUBMITTED))
        bus.publish(_event(EventType.ORDER_FILLED))

        assert len(specific) == 1
        assert len(everything) == 2

    def test_payloads_are_canonicalised(self):
        # Decimals and enums must survive the trip to JSON with one spelling.
        event = _event(quantity=Decimal("10.50"), mode=EventType.ORDER_FILLED)
        assert event.payload["quantity"] == "10.5"
        assert event.payload["mode"] == "OrderFilled"

    def test_history_can_be_filtered(self):
        bus = EventBus()
        bus.publish(_event(EventType.ORDER_SUBMITTED))
        bus.publish(_event(EventType.ORDER_FILLED))
        assert len(bus.history(EventType.ORDER_FILLED)) == 1
        assert len(bus.history()) == 2

    def test_every_amendment_event_type_exists(self):
        # The amendment names these explicitly; a rename would silently drop
        # whatever subscribes to them.
        for name in (
            "MarketStateUpdated",
            "SignalDetected",
            "TradeCandidateGenerated",
            "RiskApproved",
            "RiskRejected",
            "AgentVetoPublished",
            "OrderIntentCreated",
            "OrderSubmitted",
            "OrderAccepted",
            "OrderRejected",
            "PartialFillReceived",
            "OrderFilled",
            "PositionOpened",
            "PositionClosed",
            "KillSwitchActivated",
        ):
            assert name in {e.value for e in EventType}


class TestObserversCannotBreakTrading:
    def test_a_raising_handler_does_not_propagate(self):
        bus = EventBus()

        def explode(event: DomainEvent) -> None:
            raise RuntimeError("metrics sink down")

        bus.subscribe(explode)
        bus.publish(_event())  # must not raise
        assert bus.handler_failures == 1

    def test_a_raising_handler_does_not_stop_the_others(self):
        bus = EventBus()
        seen: list[DomainEvent] = []

        def explode(event: DomainEvent) -> None:
            raise RuntimeError("boom")

        bus.subscribe(explode)
        bus.subscribe(seen.append)
        bus.publish(_event())
        assert len(seen) == 1


class TestDeferredHandlers:
    def test_deferred_handlers_do_not_run_inline(self):
        # Journalling and analytics must not lengthen the path between a signal
        # and a fill.
        bus = EventBus()
        journalled: list[DomainEvent] = []
        bus.subscribe_deferred(journalled.append)

        bus.publish(_event())
        assert journalled == []

        assert bus.drain() == 1
        assert len(journalled) == 1

    def test_draining_twice_does_not_replay(self):
        bus = EventBus()
        seen: list[DomainEvent] = []
        bus.subscribe_deferred(seen.append)
        bus.publish(_event())
        bus.drain()
        assert bus.drain() == 0
        assert len(seen) == 1

    def test_a_failing_deferred_handler_is_also_contained(self):
        bus = EventBus()

        def explode(event: DomainEvent) -> None:
            raise ValueError("nope")

        bus.subscribe_deferred(explode)
        bus.publish(_event())
        bus.drain()
        assert bus.handler_failures == 1


class TestEventImmutability:
    def test_events_cannot_be_edited_after_publication(self):
        from pydantic import ValidationError

        event = _event()
        with pytest.raises(ValidationError):
            event.symbol = "OTHR"  # type: ignore[misc]
