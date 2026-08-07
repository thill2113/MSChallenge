"""Broker adapter interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from execution.models import ExecutionResult, OrderIntent


@runtime_checkable
class BrokerAdapter(Protocol):
    """Transmits an approved order intent to a venue.

    An adapter is a transport, not a decision-maker. It may translate field
    names and units to the venue's dialect; it may not change what is being
    asked for. If a venue cannot express an intent faithfully, the adapter must
    fail rather than approximate.
    """

    @property
    def venue(self) -> str:
        """Venue identifier recorded on every result."""
        ...

    @property
    def supports_live_orders(self) -> bool:
        """Whether this adapter can place real orders. False for every Phase 1 adapter."""
        ...

    def submit(self, intent: OrderIntent) -> ExecutionResult:
        """Transmit ``intent`` and return the venue's response."""
        ...
