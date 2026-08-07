"""Journaling — the normalized trade ledger and its evidence store.

Two responsibilities:

* **Evidence.** Raw source payloads are stored write-once and content-addressed
  under ``data/raw/`` (ADR-003).
* **Ledger.** Everything the system knows — recommendations, orders, fills, MCP
  tool calls and its own strategy decisions — is normalized into one
  :class:`~journaling.models.TradeRecord` shape.

Import *interfaces* are defined here. Parsers are not: see
:mod:`journaling.importers`.
"""

from journaling.evidence import RawDocumentStore, StoredDocument, sha256_hex
from journaling.models import TradeRecord
from journaling.repository import TradeLedgerRepository, to_row
from journaling.schema import Base, IngestionRun, RawDocumentRow, TradeRecordRow

__all__ = [
    "Base",
    "IngestionRun",
    "RawDocumentRow",
    "RawDocumentStore",
    "StoredDocument",
    "TradeLedgerRepository",
    "TradeRecord",
    "TradeRecordRow",
    "sha256_hex",
    "to_row",
]
