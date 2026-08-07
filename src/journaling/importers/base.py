"""Import contract for historical sources.

Phase 1 defines *how* an importer is invoked, not *how any particular format is
parsed*. Writing a parser against a format nobody has looked at produces code
that is confidently wrong: it silently mis-reads a column, and the error surfaces
much later as a ledger that disagrees with the brokerage.

The workflow is therefore:

1. Capture real payloads into ``data/raw/`` via
   :class:`~journaling.evidence.RawDocumentStore`.
2. A human inspects them and records the field mapping.
3. The corresponding :class:`SourceImporter` subclass is implemented and tested
   against fixtures copied from the captured evidence.

Until step 2 happens for a source, its importer raises
:class:`~domain.errors.ImporterNotImplementedError`.
"""

from __future__ import annotations

import abc
from collections.abc import Iterable, Sequence

from domain.enums import SourceSystem
from domain.errors import ImporterNotImplementedError
from journaling.evidence import StoredDocument
from journaling.models import TradeRecord


class SourceImporter(abc.ABC):
    """Turns one stored raw document into normalized ledger records."""

    source_system: SourceSystem
    importer_name: str
    importer_version: str

    @abc.abstractmethod
    def can_handle(self, document: StoredDocument) -> bool:
        """Whether this importer recognises ``document``.

        Recognition must be based on inspected structure, not on a filename.
        """

    @abc.abstractmethod
    def parse(self, document: StoredDocument, content: bytes) -> Sequence[TradeRecord]:
        """Return the ledger records ``document`` contains.

        Implementations must be pure with respect to ``content`` and must set
        ``raw_document_sha256`` on every record they emit, so each row remains
        traceable to the evidence it came from.
        """


class UninspectedFormatImporter(SourceImporter):
    """Base for sources whose payload format has not been inspected yet.

    Subclasses declare what they will import and what still needs to be captured
    and reviewed. They deliberately fail loudly rather than guessing.
    """

    #: What a human still needs to inspect before this importer can be written.
    open_questions: tuple[str, ...] = ()

    def can_handle(self, document: StoredDocument) -> bool:
        """Never claims a document; the format is unknown."""
        return False

    def parse(self, document: StoredDocument, content: bytes) -> Sequence[TradeRecord]:
        """Always raises :class:`~domain.errors.ImporterNotImplementedError`."""
        questions = "\n  - ".join(self.open_questions) or "(none recorded)"
        raise ImporterNotImplementedError(
            f"{type(self).__name__} has no parser: the {self.source_system} payload "
            f"format has not been inspected.\nCapture samples under data/raw/ and "
            f"resolve:\n  - {questions}"
        )


class ImporterRegistry:
    """Maps a stored document to the importer that understands it."""

    def __init__(self, importers: Iterable[SourceImporter] = ()) -> None:
        self._importers: list[SourceImporter] = list(importers)

    def register(self, importer: SourceImporter) -> None:
        """Add an importer. Later registrations take precedence on ties."""
        self._importers.insert(0, importer)

    def for_source(self, source_system: SourceSystem) -> tuple[SourceImporter, ...]:
        """Every importer registered for ``source_system``."""
        return tuple(i for i in self._importers if i.source_system is source_system)

    def resolve(self, document: StoredDocument) -> SourceImporter:
        """Return the importer that recognises ``document``, or raise."""
        for importer in self._importers:
            if importer.source_system is document.source_system and importer.can_handle(document):
                return importer
        raise ImporterNotImplementedError(
            f"no importer recognises document {document.sha256} from "
            f"{document.source_system}. Registered for this source: "
            f"{[type(i).__name__ for i in self.for_source(document.source_system)] or 'none'}"
        )

    def __len__(self) -> int:
        return len(self._importers)
