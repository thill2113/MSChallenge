"""Shared fixtures.

Every risk number in this file is **test data**. The values are chosen to make
boundary conditions easy to hit, not to reflect any real appetite for risk. No
production limit set exists in this repository — see docs/PHASE_1_STATUS.md,
open decision D-1.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from decimal import Decimal

import pytest

from agents.store import InMemoryAgentContextStore
from control_plane.config import (
    AccountAllocation,
    ControlPlaneConfig,
    InstrumentPermission,
    TradingSession,
)
from domain.enums import (
    AgentContextPolicy,
    AssetClass,
    ExecutionMode,
    OrderType,
    Side,
    TimeInForce,
)
from execution.killswitch import KillSwitchRegistry
from market_data.models import Bar, MarketSnapshot, Quote
from portfolio.models import PortfolioState
from risk.engine import RiskEngine
from risk.limits import RiskLimits
from strategies.context import EvaluationContext
from strategies.models import StrategyVersion, TradeCandidate

BASE_TIME = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
ZERO_DIGEST = "0" * 64


@pytest.fixture
def base_time() -> datetime:
    """A fixed, timezone-aware instant. Tests never read the real clock."""
    return BASE_TIME


@pytest.fixture
def snapshot(base_time: datetime) -> MarketSnapshot:
    """A well-formed equity snapshot."""
    return MarketSnapshot(
        symbol="ACME",
        asset_class=AssetClass.EQUITY,
        as_of=base_time,
        last_price=Decimal("100.00"),
        bar=Bar(
            interval_seconds=300,
            open=Decimal("99.50"),
            high=Decimal("100.75"),
            low=Decimal("99.10"),
            close=Decimal("100.00"),
            volume=Decimal("12500"),
        ),
        quote=Quote(bid=Decimal("99.98"), ask=Decimal("100.02")),
        provider="fixture",
    )


@pytest.fixture
def context(snapshot: MarketSnapshot) -> EvaluationContext:
    """Evaluation context wrapping the fixture snapshot."""
    return EvaluationContext.build(snapshot=snapshot)


@pytest.fixture
def candidate(base_time: datetime) -> TradeCandidate:
    """A valid long candidate: entry 100, stop 98, target 104."""
    return TradeCandidate(
        candidate_id=TradeCandidate.derive_id(
            strategy_id="fixture_double",
            strategy_version="1.0.0",
            symbol="ACME",
            as_of=base_time,
        ),
        strategy_id="fixture_double",
        strategy_version="1.0.0",
        symbol="ACME",
        as_of=base_time,
        asset_class=AssetClass.EQUITY,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal("100.00"),
        quantity=Decimal("10"),
        reference_price=Decimal("100.00"),
        stop_price=Decimal("98.00"),
        target_price=Decimal("104.00"),
        time_in_force=TimeInForce.DAY,
        account_risk_fraction=Decimal("0.005"),
        rationale="fixture candidate",
    )


@pytest.fixture
def limits() -> RiskLimits:
    """TEST-ONLY limit set. Not a recommendation of any kind."""
    return RiskLimits(
        name="fixture_limits",
        revision=1,
        max_account_risk_fraction=Decimal("0.01"),
        max_position_notional_fraction=Decimal("0.25"),
        max_gross_exposure_fraction=Decimal("1.5"),
        max_open_positions=5,
        max_daily_loss_fraction=Decimal("0.03"),
        max_weekly_loss_fraction=Decimal("0.06"),
        max_drawdown_fraction=Decimal("0.10"),
        max_consecutive_losses=4,
        max_open_portfolio_risk_fraction=Decimal("0.05"),
        max_correlated_exposure_fraction=Decimal("0.40"),
        max_market_data_age_seconds=60,
        max_account_state_age_seconds=120,
        min_reward_risk_ratio=Decimal("1.5"),
        allowed_asset_classes=(AssetClass.EQUITY,),
    )


@pytest.fixture
def risk_engine(limits: RiskLimits) -> RiskEngine:
    """Engine bound to the fixture limits."""
    return RiskEngine(limits)


@pytest.fixture
def portfolio(base_time: datetime) -> PortfolioState:
    """A flat account with 100k of equity."""
    return PortfolioState(
        as_of=base_time,
        account_equity=Decimal("100000"),
        cash=Decimal("100000"),
        positions=(),
        realized_pnl_today=Decimal("0"),
    )


@pytest.fixture
def strategy_version(base_time: datetime) -> StrategyVersion:
    """A registered-at-research-stage version record."""
    return StrategyVersion(
        strategy_id="fixture_double",
        version="1.0.0",
        code_fingerprint="a" * 64,
        created_at=base_time,
    )


@pytest.fixture
def kill_switches() -> KillSwitchRegistry:
    """An empty kill-switch registry."""
    return KillSwitchRegistry()


@pytest.fixture
def agent_store() -> InMemoryAgentContextStore:
    """An empty, healthy agent context store."""
    return InMemoryAgentContextStore()


@pytest.fixture
def config(limits: RiskLimits, base_time: datetime) -> ControlPlaneConfig:
    """TEST-ONLY control-plane configuration in SHADOW mode.

    SHADOW is the default for fixtures on purpose: a test that wants to reach a
    venue has to say so explicitly.
    """
    return ControlPlaneConfig(
        revision=1,
        execution_mode=ExecutionMode.SHADOW,
        agent_context_policy=AgentContextPolicy.ALLOW_WITHOUT_AGENT_CONTEXT,
        agent_context_max_age_seconds=900,
        enabled_strategy_keys=("fixture_double@1.0.0",),
        instruments=(
            InstrumentPermission(
                symbol="ACME",
                asset_class=AssetClass.EQUITY,
                max_position_notional_fraction=Decimal("0.25"),
                correlation_group="fixture_group",
            ),
        ),
        allocation=AccountAllocation(
            account_id="TEST-ACCOUNT",
            allocated_equity_fraction=Decimal("1"),
            broker_id="simulator",
        ),
        sessions=(
            TradingSession(
                name="regular",
                exchange_timezone="America/New_York",
                opens_at=time(9, 30),
                closes_at=time(16, 0),
            ),
        ),
        risk_limits_name=limits.name,
        risk_limits_fingerprint=limits.authoritative_fingerprint(),
        approved_by="timmy.hill23@gmail.com",
        approved_at=base_time,
    )
