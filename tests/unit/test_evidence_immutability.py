"""Raw evidence is write-once (ADR-003)."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from domain.enums import SourceSystem
from domain.errors import ImmutableEvidenceError
from journaling.evidence import RawDocumentStore, sha256_hex

pytestmark = pytest.mark.invariant

PAYLOAD = b'{"orders": [{"id": "abc", "symbol": "ACME"}]}'


@pytest.fixture
def store(tmp_path: Path) -> RawDocumentStore:
    return RawDocumentStore(tmp_path / "raw")


class TestWriteOnce:
    def test_stored_document_is_content_addressed(self, store):
        stored = store.store(PAYLOAD, source_system=SourceSystem.ROBINHOOD_ORDER)
        assert stored.sha256 == sha256_hex(PAYLOAD)
        assert stored.byte_size == len(PAYLOAD)
        assert stored.sha256 in stored.relative_path

    def test_storing_identical_content_twice_is_idempotent(self, store):
        first = store.store(PAYLOAD, source_system=SourceSystem.ROBINHOOD_ORDER)
        second = store.store(PAYLOAD, source_system=SourceSystem.ROBINHOOD_ORDER)
        assert first == second
        assert len(list(store.iter_documents())) == 1

    def test_stored_files_are_read_only(self, store):
        store.store(PAYLOAD, source_system=SourceSystem.ROBINHOOD_ORDER)
        assert store.assert_read_only() == ()

    def test_overwriting_a_stored_file_is_detected(self, store):
        stored = store.store(PAYLOAD, source_system=SourceSystem.ROBINHOOD_ORDER)
        path = store.root / stored.relative_path

        # Simulate someone forcing an edit past the read-only bit.
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        path.write_bytes(b'{"orders": []}')

        assert store.verify() == (path,)
        with pytest.raises(ImmutableEvidenceError, match="has been modified"):
            store.read(SourceSystem.ROBINHOOD_ORDER, stored.sha256, "application/json")

    def test_colliding_write_with_different_content_is_refused(self, store):
        stored = store.store(PAYLOAD, source_system=SourceSystem.ROBINHOOD_ORDER)
        path = store.root / stored.relative_path
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        path.write_bytes(b"tampered")

        # store() addresses by digest of the *new* content, so reproduce the
        # collision by writing different bytes under the original digest path.
        with pytest.raises(ImmutableEvidenceError, match="write-once"):
            store.store(PAYLOAD, source_system=SourceSystem.ROBINHOOD_ORDER)

    def test_harden_restores_permissions(self, store):
        stored = store.store(PAYLOAD, source_system=SourceSystem.ROBINHOOD_ORDER)
        path = store.root / stored.relative_path
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        assert store.assert_read_only() == (path,)
        assert store.harden() == 1
        assert store.assert_read_only() == ()

    def test_verify_passes_for_an_untouched_store(self, store):
        store.store(PAYLOAD, source_system=SourceSystem.ROBINHOOD_ORDER)
        store.store(b"another document", source_system=SourceSystem.CLAUDE_RECOMMENDATION)
        assert store.verify() == ()

    def test_reading_a_missing_document_raises(self, store):
        with pytest.raises(FileNotFoundError):
            store.read(SourceSystem.ROBINHOOD_ORDER, "0" * 64, "application/json")

    def test_documents_are_partitioned_by_source(self, store):
        stored = store.store(PAYLOAD, source_system=SourceSystem.MCP_TOOL_CALL)
        assert stored.relative_path.startswith("mcp_tool_call/")

    def test_round_trip_returns_original_bytes(self, store):
        stored = store.store(PAYLOAD, source_system=SourceSystem.ROBINHOOD_FILL)
        assert store.read(SourceSystem.ROBINHOOD_FILL, stored.sha256, "application/json") == PAYLOAD
