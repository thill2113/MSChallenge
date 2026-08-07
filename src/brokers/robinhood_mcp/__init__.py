"""Robinhood MCP adapter.

**This adapter cannot place orders.** In Phase 0/1 it exists to (a) satisfy the
:class:`~execution.protocols.BrokerAdapter` contract so the rest of the system
can be wired and tested end to end, and (b) define the record shape for MCP tool
calls so historical calls can be imported into the ledger.

Two constraints are structural, not conventions:

* **No autonomous execution.** :meth:`RobinhoodMCPAdapter.submit` raises. Order
  placement requires an explicit human-approval workflow that does not exist
  yet (docs/PHASE_1_STATUS.md, open decision D-5).
* **No credentials.** This package never reads a token, a password, an
  environment variable or a config file. The host supplies an already
  authenticated :class:`~brokers.robinhood_mcp.session.MCPSession`; this code
  only calls it.
"""

from brokers.robinhood_mcp.adapter import RobinhoodMCPAdapter
from brokers.robinhood_mcp.models import MCPToolCall
from brokers.robinhood_mcp.session import MCPSession

__all__ = ["MCPSession", "MCPToolCall", "RobinhoodMCPAdapter"]
