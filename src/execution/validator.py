"""The final validator — the last thing between a decision and a venue.

This is where per-trade human confirmation used to sit. Removing a human from
the loop is only defensible if something checks, every single time, what a
careful person would have checked. That is this module's entire job.

Design rules:

* **Every gate runs.** No short-circuiting. An operator fixing one breach needs
  to know whether four more are queued behind it.
* **Every gate is deterministic.** Same inputs, same verdict. No clock reads
  beyond the ``now`` that is passed in, no I/O, no inference.
* **No override exists.** There is no ``force``, no ``skip_checks``, no agent
  path that reaches past this. A failed gate is
  :class:`~domain.errors.ValidationGateError` and the order does not exist.
* **Fast.** Every check is arithmetic or a dictionary lookup against state the
  caller already holds. Nothing here reaches the network.
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum, unique
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field

from agents.store import AgentContextStore, resolve_agent_gate
from brokers.base import AccountState, BrokerHealth
from control_plane.config import ControlPlaneConfig
from domain.base import FrozenModel
from domain.enums import ExecutionMode, PromotionStage
from domain.errors import ValidationGateError
from domain.values import NonEmptyText, TimestampUTC
from execution.killswitch import KillSwitchRegistry
from execution.models import OrderIntent
from market_data.models import MarketSnapshot
from portfolio.models import PortfolioState
from risk.limits import RiskLimits
from strategies.models import StrategyVersion


@unique
class RejectionCode(StrEnum):
    """Closed vocabulary of validator failures.

    Closed so rejections can be counted, alerted on and compared over time. A
    spike in ``SPREAD_TOO_WIDE`` is a market condition; a spike in
    ``ACCOUNT_STATE_STALE`` is an outage. Free text cannot tell you that.
    """

    STRATEGY_NOT_ENABLED = "STRATEGY_NOT_ENABLED"
    STRATEGY_VERSION_NOT_APPROVED = "STRATEGY_VERSION_NOT_APPROVED"
    STRATEGY_STAGE_MODE_MISMATCH = "STRATEGY_STAGE_MODE_MISMATCH"
    EXECUTION_MODE_DISALLOWS_TRADING = "EXECUTION_MODE_DISALLOWS_TRADING"
    CONFIGURATION_MISMATCH = "CONFIGURATION_MISMATCH"
    RISK_LIMITS_MISMATCH = "RISK_LIMITS_MISMATCH"
    MARKET_DATA_STALE = "MARKET_DATA_STALE"
    ACCOUNT_STATE_STALE = "ACCOUNT_STATE_STALE"
    INSTRUMENT_NOT_PERMITTED = "INSTRUMENT_NOT_PERMITTED"
    OUTSIDE_TRADING_SESSION = "OUTSIDE_TRADING_SESSION"
    POSITION_LIMIT_EXCEEDED = "POSITION_LIMIT_EXCEEDED"
    DUPLICATE_POSITION = "DUPLICATE_POSITION"
    MAX_RISK_PER_TRADE_EXCEEDED = "MAX_RISK_PER_TRADE_EXCEEDED"
    MAX_OPEN_PORTFOLIO_RISK_EXCEEDED = "MAX_OPEN_PORTFOLIO_RISK_EXCEEDED"
    DAILY_LOSS_LIMIT_BREACHED = "DAILY_LOSS_LIMIT_BREACHED"
    WEEKLY_LOSS_LIMIT_BREACHED = "WEEKLY_LOSS_LIMIT_BREACHED"
    DRAWDOWN_LIMIT_BREACHED = "DRAWDOWN_LIMIT_BREACHED"
    CONSECUTIVE_LOSS_LIMIT_BREACHED = "CONSECUTIVE_LOSS_LIMIT_BREACHED"
    CORRELATED_EXPOSURE_EXCEEDED = "CORRELATED_EXPOSURE_EXCEEDED"
    INSUFFICIENT_BUYING_POWER = "INSUFFICIENT_BUYING_POWER"
    INSUFFICIENT_LIQUIDITY = "INSUFFICIENT_LIQUIDITY"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    AGENT_CONTEXT_BLOCKS = "AGENT_CONTEXT_BLOCKS"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"


MODE_REQUIRED_STAGE: dict[ExecutionMode, PromotionStage] = {
    ExecutionMode.SHADOW: PromotionStage.SHADOW,
    ExecutionMode.PAPER: PromotionStage.PAPER,
    ExecutionMode.LIMITED_LIVE: PromotionStage.LIMITED_LIVE,
}
"""The minimum stage a strategy must have reached to run in each mode.

A strategy at ``SHADOW`` cannot trade paper, and one at ``PAPER`` cannot trade
money — regardless of what the control plane enables. Two independent controls
must agree before an order leaves the building.
"""

STAGE_ORDER: tuple[PromotionStage, ...] = (
    PromotionStage.DEVELOPMENT,
    PromotionStage.BACKTEST,
    PromotionStage.OUT_OF_SAMPLE,
    PromotionStage.WALK_FORWARD,
    PromotionStage.SHADOW,
    PromotionStage.PAPER,
    PromotionStage.LIMITED_LIVE,
)


class GateFailure(FrozenModel):
    """One failed check, with the numbers that failed it."""

    code: RejectionCode
    detail: NonEmptyText
    observed: str = ""
    limit: str = ""


class ValidationOutcome(FrozenModel):
    """Result of running every gate."""

    approved: bool
    order_intent_id: str
    failures: tuple[GateFailure, ...] = ()
    checks_run: int = Field(ge=0)
    agent_context_id: str | None = None

    @property
    def codes(self) -> tuple[str, ...]:
        """Failure codes, for logging and metrics."""
        return tuple(f.code.value for f in self.failures)

    def raise_if_rejected(self) -> None:
        """Raise :class:`~domain.errors.ValidationGateError` when any gate failed."""
        if self.approved:
            return
        detail = "; ".join(f"{f.code}: {f.detail}" for f in self.failures)
        raise ValidationGateError(
            f"ORDER_REJECTED for intent {self.order_intent_id}: {detail}", self.codes
        )


class ValidationRequest(FrozenModel):
    """Everything the validator needs, gathered by the caller.

    Passed as one frozen object rather than fifteen arguments so a new gate
    cannot quietly start reading state that was never supplied — adding a field
    here is a visible change at every call site.
    """

    intent: OrderIntent
    now: TimestampUTC
    config: ControlPlaneConfig
    limits: RiskLimits
    portfolio: PortfolioState
    snapshot: MarketSnapshot
    account_state: AccountState | None = None
    broker_health: BrokerHealth | None = None
    strategy_version: StrategyVersion | None = None
    submitted_idempotency_keys: frozenset[str] = frozenset()


class FinalValidator:
    """Runs every hard gate immediately before broker submission."""

    def __init__(
        self,
        *,
        kill_switches: KillSwitchRegistry,
        agent_store: AgentContextStore | None = None,
    ) -> None:
        self._kill_switches = kill_switches
        self._agent_store = agent_store

    def validate(self, request: ValidationRequest) -> ValidationOutcome:
        """Run all gates and return the verdict.

        Returns rather than raises so a SHADOW run can record *why* an order
        would have been rejected without an exception unwinding the replay.
        Callers that must not proceed use
        :meth:`ValidationOutcome.raise_if_rejected`.
        """
        intent = request.intent
        config = request.config
        limits = request.limits
        portfolio = request.portfolio
        failures: list[GateFailure] = []
        checks = 0

        def fail(
            code: RejectionCode, detail: str, observed: object = "", limit: object = ""
        ) -> None:
            failures.append(
                GateFailure(code=code, detail=detail, observed=str(observed), limit=str(limit))
            )

        # --- control plane -------------------------------------------------
        checks += 1
        if config.execution_mode is ExecutionMode.DISABLED:
            fail(
                RejectionCode.EXECUTION_MODE_DISALLOWS_TRADING,
                "execution mode is DISABLED",
                config.execution_mode,
            )
        elif config.execution_mode is ExecutionMode.LIVE:
            fail(
                RejectionCode.EXECUTION_MODE_DISALLOWS_TRADING,
                "ExecutionMode.LIVE is reserved and not implemented",
                config.execution_mode,
            )

        checks += 1
        if not config.permits_strategy(intent.strategy_key):
            fail(
                RejectionCode.STRATEGY_NOT_ENABLED,
                f"{intent.strategy_key} is not enabled in configuration revision {config.revision}",
                intent.strategy_key,
            )

        checks += 1
        if intent.configuration_hash != config.configuration_hash:
            fail(
                RejectionCode.CONFIGURATION_MISMATCH,
                "the intent was built under a different control-plane configuration",
                intent.configuration_hash[:12],
                config.configuration_hash[:12],
            )

        checks += 1
        if limits.authoritative_fingerprint() != config.risk_limits_fingerprint:
            fail(
                RejectionCode.RISK_LIMITS_MISMATCH,
                "the supplied risk limits are not the ones this configuration approved",
                limits.name,
                config.risk_limits_name,
            )

        # --- strategy stage ------------------------------------------------
        checks += 1
        version = request.strategy_version
        if version is None:
            fail(
                RejectionCode.STRATEGY_VERSION_NOT_APPROVED,
                "no registered strategy version was supplied for this intent",
                intent.strategy_key,
            )
        else:
            required = MODE_REQUIRED_STAGE.get(config.execution_mode)
            if required is not None and _stage_rank(version.stage) < _stage_rank(required):
                fail(
                    RejectionCode.STRATEGY_STAGE_MODE_MISMATCH,
                    f"strategy is at {version.stage} but {config.execution_mode} requires "
                    f"at least {required}",
                    version.stage,
                    required,
                )

        # --- freshness -----------------------------------------------------
        checks += 1
        data_age = (request.now - request.snapshot.as_of).total_seconds()
        if data_age > limits.max_market_data_age_seconds or data_age < 0:
            fail(
                RejectionCode.MARKET_DATA_STALE,
                "market snapshot is stale or from the future",
                f"{data_age:.1f}s",
                f"{limits.max_market_data_age_seconds}s",
            )

        checks += 1
        if request.account_state is None:
            if config.execution_mode in (ExecutionMode.PAPER, ExecutionMode.LIMITED_LIVE):
                fail(
                    RejectionCode.ACCOUNT_STATE_STALE,
                    "no broker account state was supplied; required outside SHADOW",
                )
        else:
            account_age = (request.now - request.account_state.as_of).total_seconds()
            if account_age > limits.max_account_state_age_seconds or account_age < 0:
                fail(
                    RejectionCode.ACCOUNT_STATE_STALE,
                    "broker account state is stale or from the future",
                    f"{account_age:.1f}s",
                    f"{limits.max_account_state_age_seconds}s",
                )

        # --- instrument and session ----------------------------------------
        checks += 1
        permission = config.instrument(intent.symbol)
        if permission is None:
            fail(
                RejectionCode.INSTRUMENT_NOT_PERMITTED,
                f"{intent.symbol} is not in the permitted instrument list",
                intent.symbol,
            )
        elif permission.asset_class is not intent.asset_class:
            fail(
                RejectionCode.INSTRUMENT_NOT_PERMITTED,
                f"{intent.symbol} is permitted for {permission.asset_class}, not "
                f"{intent.asset_class}",
                intent.asset_class,
                permission.asset_class,
            )

        checks += 1
        if not _within_any_session(config, request.now):
            fail(
                RejectionCode.OUTSIDE_TRADING_SESSION,
                "the current time falls outside every configured trading session",
                request.now.isoformat(),
                ",".join(s.name for s in config.sessions),
            )

        # --- position and risk ---------------------------------------------
        checks += 1
        if portfolio.position_for(intent.symbol) is not None:
            fail(
                RejectionCode.DUPLICATE_POSITION,
                f"an open position in {intent.symbol} already exists",
                intent.symbol,
            )
        elif portfolio.open_position_count >= limits.max_open_positions:
            fail(
                RejectionCode.POSITION_LIMIT_EXCEEDED,
                "opening this position would exceed the concurrent position limit",
                portfolio.open_position_count + 1,
                limits.max_open_positions,
            )

        allocated_equity = portfolio.account_equity * config.allocation.allocated_equity_fraction

        checks += 1
        if allocated_equity > 0:
            trade_risk_fraction = intent.risk_amount / allocated_equity
            if trade_risk_fraction > limits.max_account_risk_fraction:
                fail(
                    RejectionCode.MAX_RISK_PER_TRADE_EXCEEDED,
                    "risk on this trade exceeds the per-trade ceiling against allocated equity",
                    trade_risk_fraction,
                    limits.max_account_risk_fraction,
                )

        checks += 1
        if allocated_equity > 0:
            projected_risk = (portfolio.open_risk + intent.risk_amount) / allocated_equity
            if projected_risk > limits.max_open_portfolio_risk_fraction:
                fail(
                    RejectionCode.MAX_OPEN_PORTFOLIO_RISK_EXCEEDED,
                    "total open risk would exceed the portfolio ceiling",
                    projected_risk,
                    limits.max_open_portfolio_risk_fraction,
                )

        # --- loss and drawdown ---------------------------------------------
        checks += 1
        if portfolio.realized_pnl_today < 0:
            loss = -portfolio.realized_pnl_today / portfolio.account_equity
            if loss >= limits.max_daily_loss_fraction:
                fail(
                    RejectionCode.DAILY_LOSS_LIMIT_BREACHED,
                    "the daily loss limit has been reached",
                    loss,
                    limits.max_daily_loss_fraction,
                )

        checks += 1
        if portfolio.realized_pnl_week < 0:
            loss = -portfolio.realized_pnl_week / portfolio.account_equity
            if loss >= limits.max_weekly_loss_fraction:
                fail(
                    RejectionCode.WEEKLY_LOSS_LIMIT_BREACHED,
                    "the weekly loss limit has been reached",
                    loss,
                    limits.max_weekly_loss_fraction,
                )

        checks += 1
        drawdown = portfolio.drawdown_fraction
        if drawdown is not None and drawdown >= limits.max_drawdown_fraction:
            fail(
                RejectionCode.DRAWDOWN_LIMIT_BREACHED,
                "drawdown from the equity high-water mark exceeds the limit",
                drawdown,
                limits.max_drawdown_fraction,
            )

        checks += 1
        if portfolio.consecutive_losses >= limits.max_consecutive_losses:
            fail(
                RejectionCode.CONSECUTIVE_LOSS_LIMIT_BREACHED,
                "consecutive losing trades have reached the limit",
                portfolio.consecutive_losses,
                limits.max_consecutive_losses,
            )

        # --- correlation ----------------------------------------------------
        checks += 1
        group = _correlation_group(config, intent.symbol)
        if group is not None and portfolio.account_equity > 0:
            notional = intent.quantity * intent.reference_price
            projected = (portfolio.exposure_in_group(group) + notional) / portfolio.account_equity
            if projected > limits.max_correlated_exposure_fraction:
                fail(
                    RejectionCode.CORRELATED_EXPOSURE_EXCEEDED,
                    f"exposure to correlation group {group!r} would exceed the ceiling",
                    projected,
                    limits.max_correlated_exposure_fraction,
                )

        # --- buying power, liquidity, spread --------------------------------
        checks += 1
        if request.account_state is not None:
            notional = intent.quantity * intent.reference_price
            if notional > request.account_state.buying_power:
                fail(
                    RejectionCode.INSUFFICIENT_BUYING_POWER,
                    "order notional exceeds available buying power",
                    notional,
                    request.account_state.buying_power,
                )

        checks += 1
        if permission is not None and permission.min_average_daily_volume is not None:
            volume = request.snapshot.bar.volume if request.snapshot.bar else None
            if volume is None:
                fail(
                    RejectionCode.INSUFFICIENT_LIQUIDITY,
                    "a liquidity floor is configured but the snapshot carries no volume",
                    "unknown",
                    permission.min_average_daily_volume,
                )
            elif volume < permission.min_average_daily_volume:
                fail(
                    RejectionCode.INSUFFICIENT_LIQUIDITY,
                    "observed volume is below the configured floor",
                    volume,
                    permission.min_average_daily_volume,
                )

        checks += 1
        if permission is not None and permission.max_spread_fraction is not None:
            quote = request.snapshot.quote
            if quote is None:
                fail(
                    RejectionCode.SPREAD_TOO_WIDE,
                    "a spread limit is configured but the snapshot carries no quote",
                    "unknown",
                    permission.max_spread_fraction,
                )
            elif quote.mid > 0:
                spread = (quote.ask - quote.bid) / quote.mid
                if spread > permission.max_spread_fraction:
                    fail(
                        RejectionCode.SPREAD_TOO_WIDE,
                        "bid/ask spread is wider than permitted for this instrument",
                        spread,
                        permission.max_spread_fraction,
                    )

        # --- duplicate protection -------------------------------------------
        checks += 1
        if intent.idempotency_key in request.submitted_idempotency_keys:
            fail(
                RejectionCode.DUPLICATE_ORDER,
                "an order with this idempotency key has already been submitted",
                intent.idempotency_key[:12],
            )

        # --- kill switches ---------------------------------------------------
        checks += 1
        blocking = self._kill_switches.blocking(
            strategy_key=intent.strategy_key,
            symbol=intent.symbol,
            broker_id=config.allocation.broker_id,
            account_id=config.allocation.account_id,
        )
        for switch in blocking:
            fail(
                RejectionCode.KILL_SWITCH_ACTIVE,
                f"{switch.scope}:{switch.target} engaged by {switch.trigger} — {switch.reason}",
                switch.scope,
            )

        # --- agent context ----------------------------------------------------
        checks += 1
        agent_outcome = resolve_agent_gate(
            store=self._agent_store,
            symbol=intent.symbol,
            strategy_id=intent.strategy_id,
            now=request.now,
            policy=config.agent_context_policy,
            max_age=timedelta(seconds=config.agent_context_max_age_seconds),
        )
        if not agent_outcome.allowed:
            fail(
                RejectionCode.AGENT_CONTEXT_BLOCKS,
                agent_outcome.reason,
                ",".join(c.value for c in agent_outcome.reason_codes) or agent_outcome.policy,
            )

        # --- broker connectivity -----------------------------------------------
        checks += 1
        if config.execution_mode in (ExecutionMode.PAPER, ExecutionMode.LIMITED_LIVE):
            if request.broker_health is None:
                fail(
                    RejectionCode.BROKER_UNAVAILABLE,
                    "no broker health check was supplied; required outside SHADOW",
                )
            elif not request.broker_health.healthy:
                fail(
                    RejectionCode.BROKER_UNAVAILABLE,
                    request.broker_health.detail or "broker reported itself unhealthy",
                    request.broker_health.broker_id,
                )

        return ValidationOutcome(
            approved=not failures,
            order_intent_id=str(intent.order_intent_id),
            failures=tuple(failures),
            checks_run=checks,
            agent_context_id=agent_outcome.context_id,
        )


def _within_any_session(config: ControlPlaneConfig, now: TimestampUTC) -> bool:
    """Whether ``now`` falls inside a configured session.

    Sessions are defined in exchange local time, so the instant is converted
    with :mod:`zoneinfo` rather than by assuming a fixed offset — that
    assumption breaks twice a year, in the direction of trading when nobody
    intended to.
    """
    for session in config.sessions:
        try:
            local = now.astimezone(ZoneInfo(session.exchange_timezone))
        except ZoneInfoNotFoundError:
            # An unresolvable timezone is a configuration error, not a licence
            # to trade. Treat the session as not matching.
            continue
        if local.weekday() not in session.weekdays:
            continue
        if session.opens_at <= local.time() <= session.closes_at:
            return True
    return False


def _stage_rank(stage: PromotionStage) -> int:
    """Position in the promotion ladder. RETIRED ranks below everything."""
    if stage is PromotionStage.RETIRED:
        return -1
    return STAGE_ORDER.index(stage)


def _correlation_group(config: ControlPlaneConfig, symbol: str) -> str | None:
    """Correlation bucket for a symbol, from control-plane configuration."""
    permission = config.instrument(symbol)
    return permission.correlation_group if permission else None


__all__ = [
    "FinalValidator",
    "GateFailure",
    "RejectionCode",
    "ValidationOutcome",
    "ValidationRequest",
]
