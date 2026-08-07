"""MCP session interface.

The session is supplied by the host process, already authenticated. Nothing in
this repository acquires, stores, refreshes or logs a credential — there is no
code path here that could leak one, because there is no code path here that
ever holds one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MCPSession(Protocol):
    """An authenticated MCP connection owned by the caller."""

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        """Invoke an MCP tool and return its raw response.

        Implementations must not mutate ``arguments``. The raw response should
        be persisted verbatim under ``data/raw/`` before anything normalises it
        (ADR-003).
        """
        ...
