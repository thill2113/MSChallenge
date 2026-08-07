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
    """Lifecycle stage of a strategy version (ADR-004, ADR-005).

    A strategy cannot move itself between stages, and no agent may promote one.
    ``PAPER`` and ``LIMITED_LIVE`` additionally require a recorded human
    approval — those are the two transitions where the consequences change
    kind, not just degree.
    """

    DEVELOPMENT = "DEVELOPMENT"
    BACKTEST = "BACKTEST"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"
    WALK_FORWARD = "WALK_FORWARD"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    LIMITED_LIVE = "LIMITED_LIVE"
    RETIRED = "RETIRED"


@unique
class ExecutionMode(StrEnum):
    """How far an order is allowed to travel.

    Set in the control plane, which only a human can change. No agent and no
    strategy can widen this.
    """

    DISABLED = "DISABLED"
    """No order submission of any kind."""

    SHADOW = "SHADOW"
    """Build the full OrderIntent and simulate execution. Nothing is sent."""

    PAPER = "PAPER"
    """Submit to a broker's paper/sandbox endpoint."""

    LIMITED_LIVE = "LIMITED_LIVE"
    """Automatically execute approved strategies under strict risk limits."""

    LIVE = "LIVE"
    """Reserved. Not implemented; the execution engine refuses it."""


@unique
class AgentContextPolicy(StrEnum):
    """What to do when cached agent context is missing, stale or degraded.

    Deliberately has **no default**. The three policies encode materially
    different risk appetites and picking one on the system's behalf would be
    inventing the fallback behaviour the amendment explicitly reserves for a
    human.
    """

    REQUIRE_VALID_AGENT_CONTEXT = "REQUIRE_VALID_AGENT_CONTEXT"
    """A fresh, unexpired context must exist. Absent or expired ⇒ reject."""

    ALLOW_WITHOUT_AGENT_CONTEXT = "ALLOW_WITHOUT_AGENT_CONTEXT"
    """Trade regardless of context availability. Only an explicit, unexpired
    veto blocks."""

    FAIL_CLOSED_ON_AGENT_FAILURE = "FAIL_CLOSED_ON_AGENT_FAILURE"
    """Absence is fine — the agent simply has no opinion. But a context that has
    *expired*, or an agent the store reports as unhealthy, is treated as a
    failure and rejects. Distinguishes "never spoke" from "was speaking and
    stopped"."""


@unique
class KillSwitchScope(StrEnum):
    """Granularity at which trading can be halted."""

    SYSTEM = "SYSTEM"
    STRATEGY = "STRATEGY"
    SYMBOL = "SYMBOL"
    BROKER = "BROKER"
    ACCOUNT = "ACCOUNT"


@unique
class OrderState(StrEnum):
    """Lifecycle state of an order at a venue.

    ``UNKNOWN`` is load-bearing, not a placeholder: when a submission times out
    the order may or may not be working, and recording that honestly is what
    stops the engine from re-sending it.
    """

    PENDING_NEW = "PENDING_NEW"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


TERMINAL_ORDER_STATES: frozenset[OrderState] = frozenset(
    {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED, OrderState.EXPIRED}
)
"""States from which an order will not change again."""


@unique
class BrokerCapability(StrEnum):
    """What a venue can actually do.

    Capability differences stay inside adapters. The engine asks rather than
    assumes, so an adapter that cannot express a bracket order fails loudly
    instead of silently dropping the protective legs.
    """

    SUBMIT = "SUBMIT"
    CANCEL = "CANCEL"
    REPLACE = "REPLACE"
    PAPER_TRADING = "PAPER_TRADING"
    LIVE_TRADING = "LIVE_TRADING"
    EXECUTION_STREAM = "EXECUTION_STREAM"
    BRACKET = "BRACKET"
    OCO = "OCO"
    OTO = "OTO"
    OTOCO = "OTOCO"
    STOP_LOSS_CHILD = "STOP_LOSS_CHILD"
    TAKE_PROFIT_CHILD = "TAKE_PROFIT_CHILD"
    FRACTIONAL_QUANTITY = "FRACTIONAL_QUANTITY"


@unique
class ProtectionStyle(StrEnum):
    """How a candidate's stop and target are attached to the entry."""

    NONE = "NONE"
    """No native protection. The application must manage exits itself."""

    BRACKET = "BRACKET"
    """Entry with stop-loss and take-profit children attached at the venue."""

    STOP_ONLY = "STOP_ONLY"
    """Entry with a stop-loss child only."""


@unique
class EventType(StrEnum):
    """Internal domain events (see :mod:`observability.events`)."""

    MARKET_STATE_UPDATED = "MarketStateUpdated"
    SIGNAL_DETECTED = "SignalDetected"
    TRADE_CANDIDATE_GENERATED = "TradeCandidateGenerated"
    NO_TRADE_RECORDED = "NoTradeRecorded"
    RISK_APPROVED = "RiskApproved"
    RISK_REJECTED = "RiskRejected"
    AGENT_VETO_PUBLISHED = "AgentVetoPublished"
    AGENT_CONTEXT_PUBLISHED = "AgentContextPublished"
    VALIDATION_REJECTED = "ValidationRejected"
    ORDER_INTENT_CREATED = "OrderIntentCreated"
    ORDER_SUBMITTED = "OrderSubmitted"
    ORDER_ACCEPTED = "OrderAccepted"
    ORDER_REJECTED = "OrderRejected"
    PARTIAL_FILL_RECEIVED = "PartialFillReceived"
    ORDER_FILLED = "OrderFilled"
    ORDER_CANCELLED = "OrderCancelled"
    ORDER_STATE_UNKNOWN = "OrderStateUnknown"
    POSITION_OPENED = "PositionOpened"
    POSITION_CLOSED = "PositionClosed"
    KILL_SWITCH_ACTIVATED = "KillSwitchActivated"
    KILL_SWITCH_CLEARED = "KillSwitchCleared"


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
