"""Closed enumerations shared across bounded contexts.

Every enum is a ``StrEnum`` so that values round-trip through JSON and through
the PostgreSQL ledger without a bespoke codec.
"""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class Side(StrEnum):
    """Direction of a position or order."""

    BUY = "BUY"
    SELL = "SELL"


@unique
class AssetClass(StrEnum):
    """Instrument class. Phase 1 models equities only; the enum is open for
    later phases so the ledger schema does not need a migration to record an
    option fill that already exists in historical data."""

    EQUITY = "EQUITY"
    OPTION = "OPTION"
    INDEX = "INDEX"
    CASH = "CASH"


@unique
class OrderType(StrEnum):
    """Order type an :class:`~execution.models.OrderIntent` may express."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


@unique
class TimeInForce(StrEnum):
    """How long an order remains working."""

    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


@unique
class RiskVerdict(StrEnum):
    """Outcome of risk validation. Only ``APPROVED`` permits an OrderIntent."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@unique
class ReviewVerdict(StrEnum):
    """Outcome of an agent review.

    The agent layer has exactly two moves: let the deterministic decision stand
    (``AFFIRM``) or stop it (``VETO``). There is deliberately no ``AMEND``
    verdict — see ADR-002.
    """

    AFFIRM = "AFFIRM"
    VETO = "VETO"


@unique
class ExecutionStatus(StrEnum):
    """Terminal or in-flight state reported back by an execution venue."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    SIMULATED = "SIMULATED"


@unique
class PromotionStage(StrEnum):
    """Lifecycle stage of a strategy version (ADR-004, ADR-005)."""

    RESEARCH = "RESEARCH"
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    PRODUCTION = "PRODUCTION"
    RETIRED = "RETIRED"


@unique
class SourceSystem(StrEnum):
    """Origin of a record imported into the normalized trade ledger."""

    CLAUDE_RECOMMENDATION = "CLAUDE_RECOMMENDATION"
    ROBINHOOD_ORDER = "ROBINHOOD_ORDER"
    ROBINHOOD_FILL = "ROBINHOOD_FILL"
    ROBINHOOD_POSITION = "ROBINHOOD_POSITION"
    MCP_TOOL_CALL = "MCP_TOOL_CALL"
    STRATEGY_DECISION = "STRATEGY_DECISION"
    MANUAL_ENTRY = "MANUAL_ENTRY"


@unique
class LedgerEventType(StrEnum):
    """The normalized shape a ledger row describes."""

    RECOMMENDATION = "RECOMMENDATION"
    ORDER = "ORDER"
    FILL = "FILL"
    POSITION_SNAPSHOT = "POSITION_SNAPSHOT"
    TOOL_CALL = "TOOL_CALL"
    STRATEGY_DECISION = "STRATEGY_DECISION"
