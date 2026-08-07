"""Persistence for the normalized ledger.

The mapping between :class:`~journaling.models.TradeRecord` (validated domain
model) and :class:`~journaling.schema.TradeRecordRow` (storage) lives here and
nowhere else, so the two cannot drift apart in three different call sites.

Writes are idempotent on ``fingerprint``: importing the same source twice adds
nothing. That property is what makes it safe to re-run an import after a partial
failure.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from domain.enums import LedgerEventType, SourceSystem
from journaling.models import TradeRecord
from journaling.schema import RawDocumentRow, TradeRecordRow


def to_row(record: TradeRecord, *, raw_document: RawDocumentRow | None = None) -> TradeRecordRow:
    """Map a validated record onto its storage row."""
    return TradeRecordRow(
        id=record.record_id,
        fingerprint=record.authoritative_fingerprint(),
        source_system=record.source_system.value,
        event_type=record.event_type.value,
        occurred_at=record.occurred_at,
        ingested_at=record.ingested_at,
        symbol=record.symbol,
        asset_class=record.asset_class.value if record.asset_class else None,
        side=record.side.value if record.side else None,
        quantity=record.quantity,
        price=record.price,
        fees=record.fees,
        currency=record.currency,
        strategy_id=record.strategy_id,
        strategy_version=record.strategy_version,
        candidate_id=record.candidate_id,
        risk_decision_id=record.risk_decision_id,
        order_intent_id=record.order_intent_id,
        external_id=record.external_id,
        raw_document_id=raw_document.id if raw_document else None,
        payload=dict(record.payload),
        notes=record.notes,
    )


class TradeLedgerRepository:
    """Reads and writes normalized ledger rows."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self, record: TradeRecord, *, raw_document: RawDocumentRow | None = None
    ) -> TradeRecordRow:
        """Insert ``record`` unless an identical one is already stored."""
        fingerprint = record.authoritative_fingerprint()
        existing = self._session.scalar(
            select(TradeRecordRow).where(TradeRecordRow.fingerprint == fingerprint)
        )
        if existing is not None:
            return existing
        row = to_row(record, raw_document=raw_document)
        self._session.add(row)
        return row

    def add_all(
        self, records: Iterable[TradeRecord], *, raw_document: RawDocumentRow | None = None
    ) -> tuple[TradeRecordRow, ...]:
        """Insert many records idempotently, preserving input order."""
        return tuple(self.add(r, raw_document=raw_document) for r in records)

    def by_fingerprint(self, fingerprint: str) -> TradeRecordRow | None:
        """Look up a row by its content fingerprint."""
        return self._session.scalar(
            select(TradeRecordRow).where(TradeRecordRow.fingerprint == fingerprint)
        )

    def for_symbol(self, symbol: str) -> Sequence[TradeRecordRow]:
        """Every event for ``symbol``, oldest first."""
        return tuple(
            self._session.scalars(
                select(TradeRecordRow)
                .where(TradeRecordRow.symbol == symbol)
                .order_by(TradeRecordRow.occurred_at)
            )
        )

    def for_source(
        self, source_system: SourceSystem, event_type: LedgerEventType | None = None
    ) -> Sequence[TradeRecordRow]:
        """Every event from ``source_system``, optionally filtered by type."""
        statement = select(TradeRecordRow).where(
            TradeRecordRow.source_system == source_system.value
        )
        if event_type is not None:
            statement = statement.where(TradeRecordRow.event_type == event_type.value)
        return tuple(self._session.scalars(statement.order_by(TradeRecordRow.occurred_at)))
