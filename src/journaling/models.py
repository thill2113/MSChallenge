"""The normalized trade ledger record.

One shape holds every kind of history this system cares about: a Claude
recommendation, a Robinhood order, a fill, an MCP tool call, and a strategy
decision produced by this system. They differ in which optional columns are
populated and in what sits in ``payload``; they do not differ in structure.

That choice is deliberate. Five bespoke tables would each need their own
queries, their own joins and their own migrations, and answering "what happened
to this idea between recommendation and fill" would mean stitching them back
together by hand.
"""

from __future__ import annotations

from typing import Any, ClassVar, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain.base import AuthoritativeModel
from domain.enums import AssetClass, LedgerEventType, Side, SourceSystem
from domain.identifiers import DeterministicId
from domain.values import ExactDecimal, NonEmptyText, Price, Quantity, Symbol, TimestampUTC

EXTERNALLY_SOURCED: frozenset[SourceSystem] = frozenset(
    {
        SourceSystem.CLAUDE_RECOMMENDATION,
        SourceSystem.ROBINHOOD_ORDER,
        SourceSystem.ROBINHOOD_FILL,
        SourceSystem.ROBINHOOD_POSITION,
        SourceSystem.MCP_TOOL_CALL,
    }
)
"""Sources whose rows must point at stored raw evidence.

``STRATEGY_DECISION`` and ``MANUAL_ENTRY`` originate inside the system, so there
is no external document to preserve.
"""


class TradeRecord(AuthoritativeModel):
    """One normalized event in the trade ledger."""

    AUTHORITATIVE_FIELDS: ClassVar[tuple[str, ...]] = (
        "source_system",
        "event_type",
        "occurred_at",
        "symbol",
        "side",
        "quantity",
        "price",
        "external_id",
    )

    record_id: UUID
    source_system: SourceSystem
    event_type: LedgerEventType
    occurred_at: TimestampUTC = Field(description="When the event happened at its source.")
    ingested_at: TimestampUTC = Field(description="When this system recorded it.")

    symbol: Symbol | None = None
    asset_class: AssetClass | None = None
    side: Side | None = None
    quantity: Quantity | None = None
    price: Price | None = None
    fees: ExactDecimal | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")

    strategy_id: str | None = None
    strategy_version: str | None = None
    candidate_id: UUID | None = None
    risk_decision_id: UUID | None = None
    order_intent_id: UUID | None = None

    external_id: str | None = Field(
        default=None,
        max_length=128,
        description="Identifier at the source system, e.g. a Robinhood order id.",
    )
    raw_document_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="Digest of the immutable source document under data/raw (ADR-003).",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Source-specific fields preserved verbatim. Never authoritative: "
        "nothing reads a trading parameter out of here.",
    )
    notes: NonEmptyText | None = None

    @model_validator(mode="after")
    def _require_provenance(self) -> Self:
        """Externally sourced rows must be traceable to stored evidence."""
        if self.source_system in EXTERNALLY_SOURCED and self.raw_document_sha256 is None:
            raise ValueError(
                f"{self.source_system} records must reference raw evidence via "
                "raw_document_sha256; import the source document first (ADR-003)"
            )
        if self.ingested_at < self.occurred_at:
            raise ValueError("ingested_at cannot precede occurred_at")
        return self

    @property
    def notional(self) -> ExactDecimal | None:
        """Quantity times price, when both are known."""
        if self.quantity is None or self.price is None:
            return None
        return self.quantity * self.price

    @classmethod
    def derive_id(
        cls,
        *,
        source_system: SourceSystem,
        event_type: LedgerEventType,
        occurred_at: TimestampUTC,
        external_id: str | None,
        raw_document_sha256: str | None,
    ) -> UUID:
        """Derive a stable id so re-importing the same source is idempotent.

        Falls back to the document digest when the source has no identifier of
        its own — a free-text Claude recommendation, for instance.
        """
        discriminator = external_id or raw_document_sha256
        if discriminator is None:
            raise ValueError(
                "a ledger record needs either an external_id or raw evidence to be "
                "identified idempotently"
            )
        return DeterministicId.derive(
            "trade_record",
            source_system.value,
            event_type.value,
            occurred_at.isoformat(),
            discriminator,
        )
