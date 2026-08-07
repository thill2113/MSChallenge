"""The normalized ledger holds every source without a bespoke table (task 10).

Runs against in-memory SQLite so the suite needs no server. The schema targets
PostgreSQL — JSONB and the numeric widths are exercised there by the compose
stack, not here.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from domain.enums import AssetClass, LedgerEventType, Side, SourceSystem
from journaling.models import TradeRecord
from journaling.repository import TradeLedgerRepository
from journaling.schema import Base, IngestionRun, RawDocumentRow, TradeRecordRow

OCCURRED = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
INGESTED = OCCURRED + timedelta(hours=1)
DIGEST = "a" * 64


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _record(source: SourceSystem, event: LedgerEventType, **overrides: object) -> TradeRecord:
    payload: dict[str, object] = {
        "source_system": source,
        "event_type": event,
        "occurred_at": OCCURRED,
        "ingested_at": INGESTED,
        "symbol": "ACME",
        "asset_class": AssetClass.EQUITY,
        "side": Side.BUY,
        "quantity": Decimal("10"),
        "price": Decimal("100.25"),
        "external_id": f"{source.value}-1",
        "raw_document_sha256": DIGEST if source in _external() else None,
    }
    payload.update(overrides)
    payload["record_id"] = TradeRecord.derive_id(
        source_system=source,
        event_type=event,
        occurred_at=payload["occurred_at"],  # type: ignore[arg-type]
        external_id=payload.get("external_id"),  # type: ignore[arg-type]
        raw_document_sha256=payload.get("raw_document_sha256"),  # type: ignore[arg-type]
    )
    return TradeRecord(**payload)  # type: ignore[arg-type]


def _external() -> frozenset[SourceSystem]:
    from journaling.models import EXTERNALLY_SOURCED

    return EXTERNALLY_SOURCED


class TestOneShapeHoldsEverySource:
    @pytest.mark.parametrize(
        ("source", "event"),
        [
            (SourceSystem.CLAUDE_RECOMMENDATION, LedgerEventType.RECOMMENDATION),
            (SourceSystem.ROBINHOOD_ORDER, LedgerEventType.ORDER),
            (SourceSystem.ROBINHOOD_FILL, LedgerEventType.FILL),
            (SourceSystem.MCP_TOOL_CALL, LedgerEventType.TOOL_CALL),
            (SourceSystem.STRATEGY_DECISION, LedgerEventType.STRATEGY_DECISION),
        ],
    )
    def test_each_source_round_trips(self, session, source, event):
        repo = TradeLedgerRepository(session)
        record = _record(source, event)
        repo.add(record)
        session.flush()

        stored = repo.by_fingerprint(record.authoritative_fingerprint())
        assert stored is not None
        assert stored.source_system == source.value
        assert stored.event_type == event.value
        assert stored.quantity == Decimal("10")
        assert stored.price == Decimal("100.25")

    def test_tool_call_rows_need_no_symbol_or_size(self, session):
        repo = TradeLedgerRepository(session)
        record = _record(
            SourceSystem.MCP_TOOL_CALL,
            LedgerEventType.TOOL_CALL,
            symbol=None,
            asset_class=None,
            side=None,
            quantity=None,
            price=None,
            payload={"tool": "get_equity_orders", "arguments": {"symbol": "ACME"}},
        )
        repo.add(record)
        session.flush()
        stored = repo.by_fingerprint(record.authoritative_fingerprint())
        assert stored is not None
        assert stored.payload["tool"] == "get_equity_orders"


class TestProvenance:
    def test_external_records_must_reference_raw_evidence(self):
        with pytest.raises(ValidationError, match="must reference raw evidence"):
            _record(
                SourceSystem.ROBINHOOD_ORDER,
                LedgerEventType.ORDER,
                raw_document_sha256=None,
            )

    def test_internal_records_need_no_raw_evidence(self):
        record = _record(
            SourceSystem.STRATEGY_DECISION,
            LedgerEventType.STRATEGY_DECISION,
            raw_document_sha256=None,
        )
        assert record.raw_document_sha256 is None

    def test_ingestion_cannot_predate_the_event(self):
        with pytest.raises(ValidationError, match="cannot precede occurred_at"):
            _record(
                SourceSystem.STRATEGY_DECISION,
                LedgerEventType.STRATEGY_DECISION,
                ingested_at=OCCURRED - timedelta(seconds=1),
                raw_document_sha256=None,
            )

    def test_rows_link_back_to_their_raw_document(self, session):
        run = IngestionRun(
            id=uuid4(),
            source_system=SourceSystem.ROBINHOOD_ORDER.value,
            importer_name="robinhood_order",
            importer_version="0.0.0",
            started_at=INGESTED,
            status="RUNNING",
        )
        document = RawDocumentRow(
            id=uuid4(),
            sha256=DIGEST,
            source_system=SourceSystem.ROBINHOOD_ORDER.value,
            relative_path=f"robinhood_order/{DIGEST}.json",
            content_type="application/json",
            byte_size=42,
            ingested_at=INGESTED,
            ingestion_run=run,
        )
        session.add_all([run, document])

        repo = TradeLedgerRepository(session)
        repo.add(
            _record(SourceSystem.ROBINHOOD_ORDER, LedgerEventType.ORDER), raw_document=document
        )
        session.flush()

        stored = session.scalar(select(TradeRecordRow))
        assert stored is not None
        assert stored.raw_document is not None
        assert stored.raw_document.sha256 == DIGEST
        assert stored.raw_document.ingestion_run is run


class TestIdempotentImport:
    def test_reimporting_the_same_event_adds_nothing(self, session):
        repo = TradeLedgerRepository(session)
        record = _record(SourceSystem.ROBINHOOD_FILL, LedgerEventType.FILL)
        repo.add(record)
        session.flush()
        repo.add(record)
        session.flush()
        assert len(session.scalars(select(TradeRecordRow)).all()) == 1

    def test_derived_ids_are_stable_across_runs(self):
        first = _record(SourceSystem.ROBINHOOD_ORDER, LedgerEventType.ORDER)
        second = _record(SourceSystem.ROBINHOOD_ORDER, LedgerEventType.ORDER)
        assert first.record_id == second.record_id
        assert first.authoritative_fingerprint() == second.authoritative_fingerprint()

    def test_a_record_without_any_identity_cannot_be_derived(self):
        with pytest.raises(ValueError, match="external_id or raw evidence"):
            TradeRecord.derive_id(
                source_system=SourceSystem.MANUAL_ENTRY,
                event_type=LedgerEventType.ORDER,
                occurred_at=OCCURRED,
                external_id=None,
                raw_document_sha256=None,
            )


class TestQueries:
    def test_history_for_a_symbol_is_chronological(self, session):
        repo = TradeLedgerRepository(session)
        repo.add(_record(SourceSystem.CLAUDE_RECOMMENDATION, LedgerEventType.RECOMMENDATION))
        repo.add(
            _record(
                SourceSystem.ROBINHOOD_ORDER,
                LedgerEventType.ORDER,
                occurred_at=OCCURRED + timedelta(minutes=5),
            )
        )
        repo.add(
            _record(
                SourceSystem.ROBINHOOD_FILL,
                LedgerEventType.FILL,
                occurred_at=OCCURRED + timedelta(minutes=6),
            )
        )
        session.flush()

        rows = repo.for_symbol("ACME")
        assert [r.event_type for r in rows] == ["RECOMMENDATION", "ORDER", "FILL"]

    def test_filtering_by_source_and_event(self, session):
        repo = TradeLedgerRepository(session)
        repo.add(_record(SourceSystem.ROBINHOOD_ORDER, LedgerEventType.ORDER))
        repo.add(_record(SourceSystem.ROBINHOOD_FILL, LedgerEventType.FILL))
        session.flush()

        rows = repo.for_source(SourceSystem.ROBINHOOD_ORDER, LedgerEventType.ORDER)
        assert len(rows) == 1
        assert rows[0].source_system == "ROBINHOOD_ORDER"
