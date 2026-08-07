"""SQLAlchemy schema for the normalized trade ledger.

Targets PostgreSQL; ``JSONB`` degrades to ``JSON`` on SQLite so the test suite
runs without a server. Money and size columns are ``Numeric``, never floats.

Three tables:

``raw_documents``
    Immutable source evidence, addressed by content digest (ADR-003).
``ingestion_runs``
    One row per import attempt, so a partial or failed import is visible rather
    than inferred from missing rows.
``trade_records``
    The normalized ledger every downstream query reads.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONColumn = JSON().with_variant(JSONB(), "postgresql")
"""JSONB on PostgreSQL, plain JSON elsewhere."""

MONEY = Numeric(28, 10)
"""Wide enough for share counts and prices alike, exact in both."""


class Base(DeclarativeBase):
    """Declarative base for every ledger table."""


class IngestionRun(Base):
    """One import attempt against one source."""

    __tablename__ = "ingestion_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    importer_name: Mapped[str] = mapped_column(String(128), nullable=False)
    importer_version: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RUNNING")
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)

    documents: Mapped[list[RawDocumentRow]] = relationship(back_populates="ingestion_run")

    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','FAILED','PARTIAL')", name="ck_ingestion_status"
        ),
        Index("ix_ingestion_runs_source_started", "source_system", "started_at"),
    )


class RawDocumentRow(Base):
    """A pointer to one immutable source document.

    The bytes live on disk under ``data/raw/``; this table records what was seen,
    when, and where. ``sha256`` is unique, so importing the same document twice
    is a no-op rather than a duplicate.
    """

    __tablename__ = "raw_documents"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingestion_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("ingestion_runs.id"))
    notes: Mapped[str | None] = mapped_column(Text)

    ingestion_run: Mapped[IngestionRun | None] = relationship(back_populates="documents")
    records: Mapped[list[TradeRecordRow]] = relationship(back_populates="raw_document")

    __table_args__ = (
        CheckConstraint("byte_size >= 0", name="ck_raw_documents_size"),
        Index("ix_raw_documents_source", "source_system"),
    )


class TradeRecordRow(Base):
    """One normalized ledger event.

    Mirrors :class:`~journaling.models.TradeRecord`. ``fingerprint`` is unique:
    re-importing the same source event cannot create a second row, which is what
    makes imports safely repeatable.
    """

    __tablename__ = "trade_records"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    symbol: Mapped[str | None] = mapped_column(String(16))
    asset_class: Mapped[str | None] = mapped_column(String(16))
    side: Mapped[str | None] = mapped_column(String(8))
    quantity: Mapped[Decimal | None] = mapped_column(MONEY)
    price: Mapped[Decimal | None] = mapped_column(MONEY)
    fees: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    strategy_id: Mapped[str | None] = mapped_column(String(64))
    strategy_version: Mapped[str | None] = mapped_column(String(32))
    candidate_id: Mapped[UUID | None] = mapped_column(Uuid)
    risk_decision_id: Mapped[UUID | None] = mapped_column(Uuid)
    order_intent_id: Mapped[UUID | None] = mapped_column(Uuid)

    external_id: Mapped[str | None] = mapped_column(String(128))
    raw_document_id: Mapped[UUID | None] = mapped_column(ForeignKey("raw_documents.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)

    raw_document: Mapped[RawDocumentRow | None] = relationship(back_populates="records")

    __table_args__ = (
        UniqueConstraint("source_system", "external_id", name="uq_trade_records_source_external"),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_trade_records_quantity"),
        CheckConstraint("price IS NULL OR price > 0", name="ck_trade_records_price"),
        CheckConstraint("side IS NULL OR side IN ('BUY','SELL')", name="ck_trade_records_side"),
        CheckConstraint("ingested_at >= occurred_at", name="ck_trade_records_time_order"),
        Index("ix_trade_records_symbol_time", "symbol", "occurred_at"),
        Index("ix_trade_records_source_time", "source_system", "occurred_at"),
        Index("ix_trade_records_strategy", "strategy_id", "strategy_version"),
    )


__all__ = ["Base", "IngestionRun", "RawDocumentRow", "TradeRecordRow"]
