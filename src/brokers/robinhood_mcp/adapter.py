"""Robinhood MCP broker adapter (read-only in Phase 0/1)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from brokers.robinhood_mcp.session import MCPSession
from domain.errors import AuthorityViolationError
from execution.models import ExecutionResult, OrderIntent

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
"""Tools this adapter is permitted to call in Phase 0/1.

An allowlist rather than a denylist: a tool nobody has reviewed is refused by
default, so adding a mutating tool to the upstream MCP server cannot silently
widen what this system can do.
"""


class RobinhoodMCPAdapter:
    """Read-only Robinhood access over MCP.

    Satisfies :class:`~execution.protocols.BrokerAdapter` structurally so the
    pipeline can be wired end to end, while refusing to place orders.
    """

    def __init__(self, session: MCPSession) -> None:
        self._session = session

    @property
    def venue(self) -> str:
        """Venue identifier recorded on results."""
        return VENUE

    @property
    def supports_live_orders(self) -> bool:
        """Always False in Phase 0/1."""
        return False

    def fetch(self, tool_name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        """Call a read-only MCP tool.

        The caller is responsible for persisting the response under
        ``data/raw/`` before normalising it.
        """
        if tool_name not in READ_ONLY_TOOLS:
            raise AuthorityViolationError(
                f"{tool_name!r} is not in the Phase 0/1 read-only allowlist. "
                f"Permitted tools: {sorted(READ_ONLY_TOOLS)}"
            )
        return self._session.call_tool(tool_name, arguments)

    def submit(self, intent: OrderIntent) -> ExecutionResult:
        """Always raises. Autonomous brokerage execution is out of scope.

        The signature exists so this adapter satisfies the broker protocol and
        so the refusal is explicit at the call site rather than a missing
        attribute discovered at runtime.
        """
        raise AuthorityViolationError(
            f"RobinhoodMCPAdapter cannot place orders (intent {intent.order_intent_id}). "
            "Autonomous brokerage execution is not implemented in Phase 0/1; it requires "
            "the human-approval workflow described in ADR-005."
        )
