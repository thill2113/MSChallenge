"""Robinhood MCP broker adapter (read-only).

Implements :class:`~brokers.base.Broker` so the rest of the system can be wired
against it, and declares **no order-placement capability at all**. That is not a
flag: :attr:`capabilities` omits ``SUBMIT``, so
:meth:`~execution.engine.ExecutionEngine.build_request` refuses this adapter
before an order is ever translated. :meth:`submit_order` raises as a second,
redundant stop.

The read methods exist but do not parse. Robinhood's payload shapes have not
been inspected yet, and a parser written against an imagined schema produces a
position report that is confidently wrong — which is worse than no report at
all. They raise with the specific questions that need answering first
(``journaling.importers.placeholders``).

**No credentials.** The host supplies an authenticated
:class:`~brokers.robinhood_mcp.session.MCPSession`; nothing here reads a token.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from brokers.base import AccountState, BrokerHealth, BrokerOrder, BrokerOrderRequest, BrokerPosition
from brokers.robinhood_mcp.session import MCPSession
from domain.enums import BrokerCapability
from domain.errors import AuthorityViolationError, ImporterNotImplementedError
from domain.values import TimestampUTC

VENUE: Final[str] = "robinhood"

READ_ONLY_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "get_accounts",
        "get_portfolio",
        "get_equity_positions",
        "get_equity_orders",
        "get_equity_quotes",
        "get_equity_historicals",
        "get_option_orders",
        "get_option_positions",
        "get_realized_pnl",
        "get_pnl_trade_history",
    }
)
"""Tools this adapter may call.

An allowlist rather than a denylist: a tool nobody has reviewed is refused by
default, so adding a mutating tool to the upstream MCP server cannot silently
widen what this system can do.
"""

ROBINHOOD_CAPABILITIES: Final[frozenset[BrokerCapability]] = frozenset()
"""Empty on purpose.

Declaring no capability is what makes the refusal structural rather than
procedural — the engine checks capabilities before it builds a request, so this
adapter cannot reach a submission path even if someone deleted the raise below.
"""


class RobinhoodMCPAdapter:
    """Read-only Robinhood access over MCP."""

    def __init__(self, session: MCPSession, *, clock: TimestampUTC | None = None) -> None:
        self._session = session
        self._clock = clock

    @property
    def broker_id(self) -> str:
        """Venue identifier recorded on orders and events."""
        return VENUE

    @property
    def capabilities(self) -> frozenset[BrokerCapability]:
        """No trading capability. See :data:`ROBINHOOD_CAPABILITIES`."""
        return ROBINHOOD_CAPABILITIES

    def fetch(self, tool_name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        """Call a read-only MCP tool.

        The caller persists the response under ``data/raw/`` before normalising
        it (ADR-003).
        """
        if tool_name not in READ_ONLY_TOOLS:
            raise AuthorityViolationError(
                f"{tool_name!r} is not in the read-only allowlist. "
                f"Permitted tools: {sorted(READ_ONLY_TOOLS)}"
            )
        return self._session.call_tool(tool_name, arguments)

    # -- order placement: refused --------------------------------------------

    def submit_order(self, request: BrokerOrderRequest) -> BrokerOrder:
        """Always raises. Autonomous brokerage execution is not enabled here."""
        raise AuthorityViolationError(
            f"RobinhoodMCPAdapter cannot place orders (intent {request.order_intent_id}). "
            "This adapter declares no SUBMIT capability; reaching live execution requires "
            "the work tracked as decision D-5."
        )

    def cancel_order(self, broker_order_id: str) -> BrokerOrder:
        """Always raises: cancelling implies the ability to have placed."""
        raise AuthorityViolationError(
            f"RobinhoodMCPAdapter cannot cancel orders ({broker_order_id}); it places none."
        )

    def replace_order(self, broker_order_id: str, request: BrokerOrderRequest) -> BrokerOrder:
        """Always raises."""
        raise AuthorityViolationError(
            f"RobinhoodMCPAdapter cannot replace orders ({broker_order_id}); it places none."
        )

    # -- reads: interface present, parsing deliberately absent -----------------

    def get_order(self, broker_order_id: str) -> BrokerOrder:
        """Not implemented: the order payload shape has not been inspected."""
        raise ImporterNotImplementedError(
            f"cannot normalise Robinhood order {broker_order_id}: the get_equity_orders "
            "payload shape has not been inspected. Capture samples under data/raw/ and "
            "resolve the questions in RobinhoodOrderImporter.open_questions first."
        )

    def get_orders(self, *, since: datetime | None = None) -> Sequence[BrokerOrder]:
        """Not implemented; see :meth:`get_order`."""
        raise ImporterNotImplementedError(
            "cannot normalise Robinhood orders: payload shape not yet inspected. "
            "Use fetch('get_equity_orders', ...) to capture raw evidence instead."
        )

    def get_positions(self) -> Sequence[BrokerPosition]:
        """Not implemented: the position payload shape has not been inspected."""
        raise ImporterNotImplementedError(
            "cannot normalise Robinhood positions: payload shape not yet inspected. "
            "Use fetch('get_equity_positions', ...) to capture raw evidence instead."
        )

    def get_account_state(self) -> AccountState:
        """Not implemented: the account payload shape has not been inspected."""
        raise ImporterNotImplementedError(
            "cannot normalise Robinhood account state: payload shape not yet inspected. "
            "Use fetch('get_accounts', ...) to capture raw evidence instead."
        )

    def get_execution_updates(self, *, since: datetime | None = None) -> Sequence[BrokerOrder]:
        """Not implemented; this adapter has no orders to report on."""
        raise ImporterNotImplementedError("no execution updates: this adapter places no orders.")

    def health_check(self) -> BrokerHealth:
        """Probe connectivity with a cheap read-only call.

        Reports unhealthy rather than raising: a health check that throws is a
        health check every caller has to wrap.
        """
        checked_at = self._clock
        if checked_at is None:
            raise RuntimeError(
                "RobinhoodMCPAdapter has no clock; pass clock= so health results stay reproducible."
            )
        try:
            self.fetch("get_accounts", {})
        except Exception as exc:
            return BrokerHealth(
                broker_id=VENUE,
                healthy=False,
                checked_at=checked_at,
                detail=f"{type(exc).__name__}: {exc}"[:4000],
            )
        return BrokerHealth(broker_id=VENUE, healthy=True, checked_at=checked_at)


__all__ = ["READ_ONLY_TOOLS", "ROBINHOOD_CAPABILITIES", "VENUE", "RobinhoodMCPAdapter"]
