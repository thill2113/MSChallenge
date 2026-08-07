"""The execution boundary.

Everything that reaches a venue passes through :meth:`ExecutionGateway.submit`,
which accepts an :class:`~execution.models.OrderIntent` and nothing else — not a
candidate, not a dict, not a "close enough" duck-typed object.

Live order placement is not implemented in Phase 0/1. ``ExecutionMode.LIVE`` is
refused at the gate rather than left as a flag someone can flip before the
approval workflow exists (see docs/PHASE_1_STATUS.md, open decision D-5).
"""

from __future__ import annotations

from enum import StrEnum, unique

from domain.enums import ExecutionStatus
from domain.errors import AuthorityViolationError, RiskBypassError
from domain.identifiers import DeterministicId
from execution.models import ExecutionResult, OrderIntent
from execution.protocols import BrokerAdapter


@unique
class ExecutionMode(StrEnum):
    """How far an order is allowed to travel."""

    SIMULATED = "SIMULATED"
    """In-process simulation. Nothing leaves the machine."""

    PAPER = "PAPER"
    """A venue's paper-trading endpoint. Requires an adapter that supports it."""

    LIVE = "LIVE"
    """Real money. Not implemented in Phase 0/1."""


class ExecutionGateway:
    """Validates and transmits order intents."""

    def __init__(
        self, broker: BrokerAdapter, *, mode: ExecutionMode = ExecutionMode.SIMULATED
    ) -> None:
        if mode is ExecutionMode.LIVE:
            raise AuthorityViolationError(
                "live execution is not implemented in Phase 0/1. Enabling it requires "
                "the human-approval workflow described in ADR-005 and an explicit "
                "decision recorded in docs/PHASE_1_STATUS.md."
            )
        if mode is ExecutionMode.PAPER and not broker.supports_live_orders:
            raise AuthorityViolationError(
                f"adapter for venue {broker.venue!r} cannot place orders; "
                "PAPER mode is unavailable with it"
            )
        self._broker = broker
        self._mode = mode

    @property
    def mode(self) -> ExecutionMode:
        """The mode this gateway was constructed with."""
        return self._mode

    @property
    def venue(self) -> str:
        """Venue of the configured adapter."""
        return self._broker.venue

    def submit(self, intent: OrderIntent) -> ExecutionResult:
        """Re-verify authorisation and transmit.

        The risk binding is checked here as well as at construction. The
        duplication is intentional: an intent may have been deserialised from a
        queue or a database between the two points, and the gate is the last
        place anything can be stopped.
        """
        if not isinstance(intent, OrderIntent):
            raise TypeError(
                f"execution accepts OrderIntent only; received {type(intent).__name__}. "
                "Candidates must be approved by the risk engine first."
            )
        if not intent.risk_decision.is_approved:
            raise RiskBypassError(
                f"order intent {intent.order_intent_id} carries a "
                f"{intent.risk_decision.verdict} risk decision"
            )
        if intent.risk_decision.candidate_fingerprint != intent.candidate_fingerprint:
            raise RiskBypassError(
                f"order intent {intent.order_intent_id} no longer matches the candidate "
                "its approval was issued for"
            )
        return self._broker.submit(intent)


class SimulatedBroker:
    """Records intents and acknowledges them without contacting any venue.

    Used by tests and backtests. It reports ``SIMULATED`` rather than
    ``ACCEPTED`` so a simulated result can never be mistaken for a real one in
    the ledger.
    """

    def __init__(self, venue: str = "simulator") -> None:
        self._venue = venue
        self._submitted: list[OrderIntent] = []

    @property
    def venue(self) -> str:
        """Venue identifier."""
        return self._venue

    @property
    def supports_live_orders(self) -> bool:
        """Always False."""
        return False

    @property
    def submitted(self) -> tuple[OrderIntent, ...]:
        """Every intent this broker received, in order."""
        return tuple(self._submitted)

    def submit(self, intent: OrderIntent) -> ExecutionResult:
        """Acknowledge ``intent`` without executing anything."""
        self._submitted.append(intent)
        fingerprint = intent.authoritative_fingerprint()
        return ExecutionResult(
            result_id=DeterministicId.derive("execution_result", self._venue, fingerprint),
            order_intent_id=intent.order_intent_id,
            order_intent_fingerprint=fingerprint,
            status=ExecutionStatus.SIMULATED,
            venue=self._venue,
            submitted_at=intent.created_at,
            reported_at=intent.created_at,
            message="simulated acknowledgement; no order was transmitted",
        )
