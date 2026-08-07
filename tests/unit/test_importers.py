"""Import interfaces exist; parsers deliberately do not (task 11)."""

from __future__ import annotations

import pytest

from domain.enums import SourceSystem
from domain.errors import ImporterNotImplementedError
from journaling.evidence import StoredDocument, sha256_hex
from journaling.importers import (
    ClaudeRecommendationImporter,
    ImporterRegistry,
    MCPToolCallImporter,
    RobinhoodFillImporter,
    RobinhoodOrderImporter,
    default_registry_importers,
)

ALL_PLACEHOLDERS = [
    ClaudeRecommendationImporter,
    RobinhoodOrderImporter,
    RobinhoodFillImporter,
    MCPToolCallImporter,
]


def _document(source: SourceSystem) -> StoredDocument:
    content = b"{}"
    return StoredDocument(
        sha256=sha256_hex(content),
        source_system=source,
        relative_path=f"{source.value.lower()}/{sha256_hex(content)}.json",
        content_type="application/json",
        byte_size=len(content),
    )


class TestPlaceholdersRefuseToGuess:
    @pytest.mark.parametrize("importer_cls", ALL_PLACEHOLDERS)
    def test_parse_raises_rather_than_guessing(self, importer_cls):
        importer = importer_cls()
        with pytest.raises(ImporterNotImplementedError, match="has not been inspected"):
            importer.parse(_document(importer.source_system), b"{}")

    @pytest.mark.parametrize("importer_cls", ALL_PLACEHOLDERS)
    def test_placeholder_never_claims_a_document(self, importer_cls):
        importer = importer_cls()
        assert importer.can_handle(_document(importer.source_system)) is False

    @pytest.mark.parametrize("importer_cls", ALL_PLACEHOLDERS)
    def test_open_questions_are_recorded(self, importer_cls):
        # The gap has to be legible to whoever picks this up, not just present.
        assert importer_cls.open_questions, f"{importer_cls.__name__} records no open questions"

    @pytest.mark.parametrize("importer_cls", ALL_PLACEHOLDERS)
    def test_error_message_names_the_open_questions(self, importer_cls):
        importer = importer_cls()
        with pytest.raises(ImporterNotImplementedError) as excinfo:
            importer.parse(_document(importer.source_system), b"{}")
        assert importer.open_questions[0] in str(excinfo.value)

    def test_every_declared_source_has_a_placeholder(self):
        declared = {i.source_system for i in default_registry_importers()}
        assert declared == {
            SourceSystem.CLAUDE_RECOMMENDATION,
            SourceSystem.ROBINHOOD_ORDER,
            SourceSystem.ROBINHOOD_FILL,
            SourceSystem.MCP_TOOL_CALL,
        }


class TestRegistry:
    def test_unresolvable_document_reports_what_is_registered(self):
        registry = ImporterRegistry(default_registry_importers())
        with pytest.raises(ImporterNotImplementedError, match="RobinhoodOrderImporter"):
            registry.resolve(_document(SourceSystem.ROBINHOOD_ORDER))

    def test_unknown_source_reports_none_registered(self):
        registry = ImporterRegistry(default_registry_importers())
        with pytest.raises(ImporterNotImplementedError, match="none"):
            registry.resolve(_document(SourceSystem.ROBINHOOD_POSITION))

    def test_for_source_filters_correctly(self):
        registry = ImporterRegistry(default_registry_importers())
        matched = registry.for_source(SourceSystem.ROBINHOOD_ORDER)
        assert len(matched) == 1
        assert isinstance(matched[0], RobinhoodOrderImporter)

    def test_a_real_importer_can_be_registered_later(self):
        registry = ImporterRegistry(default_registry_importers())
        before = len(registry)

        class WorkingImporter(RobinhoodOrderImporter):
            def can_handle(self, document: StoredDocument) -> bool:
                return True

        registry.register(WorkingImporter())
        assert len(registry) == before + 1
        resolved = registry.resolve(_document(SourceSystem.ROBINHOOD_ORDER))
        assert isinstance(resolved, WorkingImporter)
