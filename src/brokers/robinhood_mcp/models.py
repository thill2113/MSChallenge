"""Records of MCP tool invocations.

A tool call is evidence: it is what actually happened between this system and
the brokerage. The request and response payloads are referenced by digest rather
than embedded, because the authoritative copy lives immutably under
``data/raw/`` (ADR-003).
"""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from pydantic import Field

from domain.base import AuthoritativeModel
from domain.identifiers import DeterministicId
from domain.values import NonEmptyText, TimestampUTC


class MCPToolCall(AuthoritativeModel):
    """One invocation of one MCP tool.

    ``read_only`` is recorded per call rather than inferred from the tool name,
    so an audit can answer "did this system ever call a mutating tool" without
    depending on a naming convention staying true.
    """

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "server",
        "tool_name",
        "called_at",
        "arguments_sha256",
        "read_only",
    )

    call_id: UUID
    server: str = Field(min_length=1, max_length=64)
    tool_name: str = Field(min_length=1, max_length=128)
    called_at: TimestampUTC
    arguments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    read_only: bool = Field(
        description="True when the tool only reads. Phase 0/1 permits read-only calls only."
    )
    succeeded: bool = True
    error_message: NonEmptyText | None = None
    raw_document_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="Digest of the stored raw exchange under data/raw (ADR-003).",
    )

    @classmethod
    def derive_id(
        cls, *, server: str, tool_name: str, called_at: TimestampUTC, arguments_sha256: str
    ) -> UUID:
        """Derive a stable id so re-importing the same call is idempotent."""
        return DeterministicId.derive(
            "mcp_tool_call", server, tool_name, called_at.isoformat(), arguments_sha256
        )
