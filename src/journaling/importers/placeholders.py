"""Declared-but-unimplemented importers.

Each class below names a source we intend to import and lists exactly what a
human must inspect before its parser can be written. They exist so the gap is
visible in code and in the test suite, rather than living only in a document.
"""

from __future__ import annotations

from domain.enums import SourceSystem
from journaling.importers.base import UninspectedFormatImporter


class ClaudeRecommendationImporter(UninspectedFormatImporter):
    """Historical trade recommendations produced by Claude conversations."""

    source_system = SourceSystem.CLAUDE_RECOMMENDATION
    importer_name = "claude_recommendation"
    importer_version = "0.0.0"
    open_questions = (
        "What is the export format — conversation JSON, Markdown transcript, or "
        "hand-curated notes?",
        "Is a recommendation always a single symbol, or can one message cover several?",
        "Are entry, stop and target always stated numerically, or sometimes prose "
        "('stop below the recent low')?",
        "Which timestamp is authoritative: message time, or a stated trade date?",
        "How are follow-up messages that revise an earlier recommendation represented?",
    )


class RobinhoodOrderImporter(UninspectedFormatImporter):
    """Historical Robinhood equity and option orders."""

    source_system = SourceSystem.ROBINHOOD_ORDER
    importer_name = "robinhood_order"
    importer_version = "0.0.0"
    open_questions = (
        "Exact field names and nesting of the get_equity_orders / get_option_orders "
        "payloads as this account receives them.",
        "Which timestamp field represents order creation vs. last update, and in which timezone.",
        "How partially filled and cancelled-after-partial orders are represented.",
        "Whether option orders need a distinct normalized shape or fit the equity one "
        "with an instrument descriptor in payload.",
    )


class RobinhoodFillImporter(UninspectedFormatImporter):
    """Historical Robinhood executions."""

    source_system = SourceSystem.ROBINHOOD_FILL
    importer_name = "robinhood_fill"
    importer_version = "0.0.0"
    open_questions = (
        "Are fills exposed as their own records, or only as an 'executions' array "
        "nested inside an order?",
        "How are fees and regulatory charges reported, and are they per fill or per order?",
        "How should a multi-fill order be reconciled to a single ledger position event?",
    )


class MCPToolCallImporter(UninspectedFormatImporter):
    """Historical MCP tool invocations against the Robinhood server."""

    source_system = SourceSystem.MCP_TOOL_CALL
    importer_name = "mcp_tool_call"
    importer_version = "0.0.0"
    open_questions = (
        "Where are historical tool calls logged, and in what format?",
        "Do the logs retain request arguments, or only responses?",
        "Do any historical calls include credentials or account identifiers that must "
        "be redacted before the payload is stored under data/raw?",
    )


def default_registry_importers() -> tuple[UninspectedFormatImporter, ...]:
    """Every declared importer, implemented or not."""
    return (
        ClaudeRecommendationImporter(),
        RobinhoodOrderImporter(),
        RobinhoodFillImporter(),
        MCPToolCallImporter(),
    )
