"""Every hard gate fires, and none can be bypassed (ADR-008).

Per-trade human confirmation was removed. These tests are what replaced it, so
they check each gate individually *and* check that the validator has no escape
hatch: no force flag, no partial run, no agent path around it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from agents.context import AgentContext, ContextReasonCode
from agents.store import InMemoryAgentContextStore
from brokers.base import AccountState, BrokerHealth
from domain.enums import (
    AgentContextPolicy,
    AssetClass,
    ExecutionMode,
    KillSwitchScope,
    PromotionStage,
)
from domain.errors import ValidationGateError
from execution.killswitch import KillSwitch, KillSwitchRegistry, KillSwitchTrigger
from execution.models import OrderIntent
from execution.validator import FinalValidator, RejectionCode, ValidationRequest
from portfolio.models import PortfolioState, Position

pytestmark = pytest.mark.invariant

NOW = datetime(2026, 1, 2, 14, 30, 30, tzinfo=UTC)


def _account(as_of=NOW, buying_power=Decimal("100000")) -> AccountState:
    return AccountState(
        account_id="TEST-ACCOUNT",
        as_of=as_of,
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        buying_power=buying_power,
    )


def _health(healthy=True) -> BrokerHealth:
    return BrokerHealth(broker_id="simulator", healthy=healthy, checked_at=NOW)


@pytest.fixture
def shadow_version(strategy_version):
    return strategy_version.model_copy(update={"stage": PromotionStage.SHADOW})


@pytest.fixture
def intent(candidate, risk_engine, portfolio, config, base_time) -> OrderIntent:
    decision = risk_engine.evaluate(candidate, portfolio, evaluated_at=base_time)
    return OrderIntent.from_approved(
        candidate, decision, created_at=base_time, configuration_hash=config.configuration_hash
    )


def _request(intent, config, limits, portfolio, snapshot, version, **kw) -> ValidationRequest:
    defaults: dict[str, object] = {
        "intent": intent,
        "now": NOW,
        "config": config,
        "limits": limits,
        "portfolio": portfolio,
        "snapshot": snapshot,
        "strategy_version": version,
    }
    defaults.update(kw)
    return ValidationRequest(**defaults)  # type: ignore[arg-type]


def _validate(request, *, kill_switches=None, agent_store=None):
    return FinalValidator(
        kill_switches=kill_switches or KillSwitchRegistry(), agent_store=agent_store
    ).validate(request)


class TestBaseline:
    def test_a_well_formed_intent_passes_every_gate(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        outcome = _validate(_request(intent, config, limits, portfolio, snapshot, shadow_version))
        assert outcome.approved, outcome.codes
        assert outcome.checks_run >= 18

    def test_every_gate_runs_even_after_one_fails(
        self, intent, config, limits, snapshot, shadow_version, base_time
    ):
        # An operator fixing one breach needs to know what is queued behind it.
        broken = PortfolioState(
            as_of=base_time,
            account_equity=Decimal("1000"),
            cash=Decimal("1000"),
            realized_pnl_today=Decimal("-500"),
            realized_pnl_week=Decimal("-500"),
            consecutive_losses=9,
            peak_equity=Decimal("5000"),
        )
        outcome = _validate(_request(intent, config, limits, broken, snapshot, shadow_version))
        assert not outcome.approved
        assert len(outcome.failures) >= 4

    def test_rejection_raises_with_every_code(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        disabled = config.model_copy(
            update={"revision": 2, "execution_mode": ExecutionMode.DISABLED}
        )
        outcome = _validate(_request(intent, disabled, limits, portfolio, snapshot, shadow_version))
        with pytest.raises(ValidationGateError, match="ORDER_REJECTED") as excinfo:
            outcome.raise_if_rejected()
        assert RejectionCode.EXECUTION_MODE_DISALLOWS_TRADING.value in excinfo.value.codes

    def test_there_is_no_override_parameter(self):
        # The absence of an escape hatch is the point; assert it structurally
        # rather than trusting that nobody adds one.
        import inspect

        signature = inspect.signature(FinalValidator.validate)
        assert list(signature.parameters) == ["self", "request"]
        for banned in ("force", "skip", "override", "bypass"):
            assert banned not in str(signature)


class TestControlPlaneGates:
    def test_disabled_mode_blocks(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        disabled = config.model_copy(
            update={"revision": 2, "execution_mode": ExecutionMode.DISABLED}
        )
        codes = _validate(
            _request(intent, disabled, limits, portfolio, snapshot, shadow_version)
        ).codes
        assert RejectionCode.EXECUTION_MODE_DISALLOWS_TRADING.value in codes

    def test_a_strategy_not_enabled_in_configuration_blocks(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        disabled = config.model_copy(
            update={
                "revision": 2,
                "execution_mode": ExecutionMode.DISABLED,
                "enabled_strategy_keys": (),
            }
        )
        codes = _validate(
            _request(intent, disabled, limits, portfolio, snapshot, shadow_version)
        ).codes
        assert RejectionCode.STRATEGY_NOT_ENABLED.value in codes

    def test_an_intent_built_under_another_configuration_blocks(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        stale = intent.model_copy(update={"configuration_hash": "d" * 64})
        codes = _validate(
            _request(stale, config, limits, portfolio, snapshot, shadow_version)
        ).codes
        assert RejectionCode.CONFIGURATION_MISMATCH.value in codes

    def test_limits_the_configuration_never_approved_block(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        swapped = limits.model_copy(update={"revision": 99})
        codes = _validate(
            _request(intent, config, swapped, portfolio, snapshot, shadow_version)
        ).codes
        assert RejectionCode.RISK_LIMITS_MISMATCH.value in codes

    def test_an_instrument_outside_the_permitted_list_blocks(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        # A permitted list that covers a different symbol. (An *empty* list is
        # rejected by the config model itself: enabled strategies with nothing
        # to trade is incoherent, not merely restrictive.)
        narrowed = config.model_copy(
            update={
                "revision": 2,
                "instruments": (config.instruments[0].model_copy(update={"symbol": "OTHR"}),),
            }
        )
        codes = _validate(
            _request(intent, narrowed, limits, portfolio, snapshot, shadow_version)
        ).codes
        assert RejectionCode.INSTRUMENT_NOT_PERMITTED.value in codes

    def test_outside_the_trading_session_blocks(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        # 03:00 UTC is 22:00 the previous day in New York.
        codes = _validate(
            _request(
                intent,
                config,
                limits,
                portfolio,
                snapshot,
                shadow_version,
                now=datetime(2026, 1, 3, 3, 0, tzinfo=UTC),
            )
        ).codes
        assert RejectionCode.OUTSIDE_TRADING_SESSION.value in codes


class TestStrategyStageGates:
    def test_a_shadow_stage_strategy_cannot_run_in_paper_mode(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        paper = config.model_copy(update={"revision": 2, "execution_mode": ExecutionMode.PAPER})
        codes = _validate(
            _request(
                intent,
                paper,
                limits,
                portfolio,
                snapshot,
                shadow_version,
                account_state=_account(),
                broker_health=_health(),
            )
        ).codes
        assert RejectionCode.STRATEGY_STAGE_MODE_MISMATCH.value in codes

    def test_a_paper_stage_strategy_cannot_trade_money(
        self, intent, config, limits, portfolio, snapshot, strategy_version
    ):
        limited_live = config.model_copy(
            update={"revision": 2, "execution_mode": ExecutionMode.LIMITED_LIVE}
        )
        paper_stage = strategy_version.model_copy(update={"stage": PromotionStage.PAPER})
        codes = _validate(
            _request(
                intent,
                limited_live,
                limits,
                portfolio,
                snapshot,
                paper_stage,
                account_state=_account(),
                broker_health=_health(),
            )
        ).codes
        assert RejectionCode.STRATEGY_STAGE_MODE_MISMATCH.value in codes

    def test_an_unregistered_version_blocks(self, intent, config, limits, portfolio, snapshot):
        codes = _validate(_request(intent, config, limits, portfolio, snapshot, None)).codes
        assert RejectionCode.STRATEGY_VERSION_NOT_APPROVED.value in codes


class TestFreshnessGates:
    def test_stale_market_data_blocks(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        codes = _validate(
            _request(
                intent,
                config,
                limits,
                portfolio,
                snapshot,
                shadow_version,
                now=snapshot.as_of + timedelta(minutes=10),
            )
        ).codes
        assert RejectionCode.MARKET_DATA_STALE.value in codes

    def test_market_data_from_the_future_blocks(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        codes = _validate(
            _request(
                intent,
                config,
                limits,
                portfolio,
                snapshot,
                shadow_version,
                now=snapshot.as_of - timedelta(minutes=1),
            )
        ).codes
        assert RejectionCode.MARKET_DATA_STALE.value in codes

    def test_stale_account_state_blocks_outside_shadow(
        self, intent, config, limits, portfolio, snapshot, strategy_version
    ):
        paper = config.model_copy(update={"revision": 2, "execution_mode": ExecutionMode.PAPER})
        codes = _validate(
            _request(
                intent,
                paper,
                limits,
                portfolio,
                snapshot,
                strategy_version.model_copy(update={"stage": PromotionStage.PAPER}),
                account_state=_account(as_of=NOW - timedelta(minutes=30)),
                broker_health=_health(),
            )
        ).codes
        assert RejectionCode.ACCOUNT_STATE_STALE.value in codes

    def test_missing_account_state_blocks_outside_shadow(
        self, intent, config, limits, portfolio, snapshot, strategy_version
    ):
        paper = config.model_copy(update={"revision": 2, "execution_mode": ExecutionMode.PAPER})
        codes = _validate(
            _request(
                intent,
                paper,
                limits,
                portfolio,
                snapshot,
                strategy_version.model_copy(update={"stage": PromotionStage.PAPER}),
                broker_health=_health(),
            )
        ).codes
        assert RejectionCode.ACCOUNT_STATE_STALE.value in codes

    def test_shadow_mode_does_not_require_account_state(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        outcome = _validate(_request(intent, config, limits, portfolio, snapshot, shadow_version))
        assert RejectionCode.ACCOUNT_STATE_STALE.value not in outcome.codes


class TestRiskGates:
    def test_an_existing_position_blocks(
        self, intent, config, limits, snapshot, shadow_version, base_time
    ):
        held = PortfolioState(
            as_of=base_time,
            account_equity=Decimal("100000"),
            cash=Decimal("50000"),
            positions=(
                Position(
                    symbol="ACME",
                    side=intent.side,
                    quantity=Decimal("5"),
                    average_price=Decimal("99"),
                    mark_price=Decimal("100"),
                    stop_price=Decimal("97"),
                ),
            ),
        )
        codes = _validate(_request(intent, config, limits, held, snapshot, shadow_version)).codes
        assert RejectionCode.DUPLICATE_POSITION.value in codes

    def test_the_position_count_limit_blocks(
        self, intent, config, limits, snapshot, shadow_version, base_time
    ):
        crowded = PortfolioState(
            as_of=base_time,
            account_equity=Decimal("1000000"),
            cash=Decimal("500000"),
            positions=tuple(
                Position(
                    symbol=f"SYM{i}",
                    side=intent.side,
                    quantity=Decimal("1"),
                    average_price=Decimal("10"),
                    mark_price=Decimal("10"),
                    stop_price=Decimal("9"),
                )
                for i in range(limits.max_open_positions)
            ),
        )
        codes = _validate(_request(intent, config, limits, crowded, snapshot, shadow_version)).codes
        assert RejectionCode.POSITION_LIMIT_EXCEEDED.value in codes

    def test_per_trade_risk_is_measured_against_allocated_equity(
        self, intent, config, limits, snapshot, shadow_version, base_time
    ):
        # Half the account is allocated, so the same trade is twice the risk.
        half = config.model_copy(
            update={
                "revision": 2,
                "allocation": config.allocation.model_copy(
                    update={"allocated_equity_fraction": Decimal("0.001")}
                ),
            }
        )
        codes = _validate(
            _request(
                intent, half, limits, portfolio_with_equity(base_time), snapshot, shadow_version
            )
        ).codes
        assert RejectionCode.MAX_RISK_PER_TRADE_EXCEEDED.value in codes

    def test_open_portfolio_risk_blocks(
        self, intent, config, limits, snapshot, shadow_version, base_time
    ):
        exposed = PortfolioState(
            as_of=base_time,
            account_equity=Decimal("100000"),
            cash=Decimal("50000"),
            positions=(
                Position(
                    symbol="OTHR",
                    side=intent.side,
                    quantity=Decimal("1000"),
                    average_price=Decimal("100"),
                    mark_price=Decimal("100"),
                    stop_price=Decimal("95"),
                ),
            ),
        )
        codes = _validate(_request(intent, config, limits, exposed, snapshot, shadow_version)).codes
        assert RejectionCode.MAX_OPEN_PORTFOLIO_RISK_EXCEEDED.value in codes

    def test_an_unprotected_position_counts_its_whole_notional_as_risk(self, base_time):
        # Reporting zero risk for a stopless position would make the portfolio
        # gate read best-case. It reads worst-case instead.
        unprotected = Position(
            symbol="X",
            side=__import__("domain.enums", fromlist=["Side"]).Side.BUY,
            quantity=Decimal("10"),
            average_price=Decimal("100"),
            mark_price=Decimal("100"),
        )
        assert unprotected.open_risk == Decimal("1000")

    @pytest.mark.parametrize(
        ("field", "value", "code"),
        [
            ("realized_pnl_today", Decimal("-5000"), RejectionCode.DAILY_LOSS_LIMIT_BREACHED),
            ("realized_pnl_week", Decimal("-9000"), RejectionCode.WEEKLY_LOSS_LIMIT_BREACHED),
            ("consecutive_losses", 4, RejectionCode.CONSECUTIVE_LOSS_LIMIT_BREACHED),
            ("peak_equity", Decimal("200000"), RejectionCode.DRAWDOWN_LIMIT_BREACHED),
        ],
    )
    def test_loss_and_drawdown_gates(
        self, intent, config, limits, snapshot, shadow_version, base_time, field, value, code
    ):
        state = PortfolioState(
            as_of=base_time,
            account_equity=Decimal("100000"),
            cash=Decimal("100000"),
            **{field: value},
        )
        codes = _validate(_request(intent, config, limits, state, snapshot, shadow_version)).codes
        assert code.value in codes

    def test_correlated_exposure_blocks(
        self, intent, config, limits, snapshot, shadow_version, base_time
    ):
        clustered = PortfolioState(
            as_of=base_time,
            account_equity=Decimal("100000"),
            cash=Decimal("50000"),
            positions=(
                Position(
                    symbol="OTHR",
                    side=intent.side,
                    quantity=Decimal("400"),
                    average_price=Decimal("100"),
                    mark_price=Decimal("100"),
                    stop_price=Decimal("99.9"),
                    correlation_group="fixture_group",
                ),
            ),
        )
        codes = _validate(
            _request(intent, config, limits, clustered, snapshot, shadow_version)
        ).codes
        assert RejectionCode.CORRELATED_EXPOSURE_EXCEEDED.value in codes

    def test_insufficient_buying_power_blocks(
        self, intent, config, limits, portfolio, snapshot, strategy_version
    ):
        paper = config.model_copy(update={"revision": 2, "execution_mode": ExecutionMode.PAPER})
        codes = _validate(
            _request(
                intent,
                paper,
                limits,
                portfolio,
                snapshot,
                strategy_version.model_copy(update={"stage": PromotionStage.PAPER}),
                account_state=_account(buying_power=Decimal("10")),
                broker_health=_health(),
            )
        ).codes
        assert RejectionCode.INSUFFICIENT_BUYING_POWER.value in codes


class TestLiquidityAndSpreadGates:
    def _with_instrument(self, config, **overrides):
        instrument = config.instruments[0].model_copy(update=overrides)
        return config.model_copy(update={"revision": 2, "instruments": (instrument,)})

    def test_thin_volume_blocks(self, intent, config, limits, portfolio, snapshot, shadow_version):
        strict = self._with_instrument(config, min_average_daily_volume=Decimal("10000000"))
        codes = _validate(
            _request(intent, strict, limits, portfolio, snapshot, shadow_version)
        ).codes
        assert RejectionCode.INSUFFICIENT_LIQUIDITY.value in codes

    def test_a_missing_volume_is_treated_as_a_failure_not_a_pass(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        strict = self._with_instrument(config, min_average_daily_volume=Decimal("1"))
        codes = _validate(
            _request(
                intent,
                strict,
                limits,
                portfolio,
                snapshot.model_copy(update={"bar": None}),
                shadow_version,
            )
        ).codes
        assert RejectionCode.INSUFFICIENT_LIQUIDITY.value in codes

    def test_a_wide_spread_blocks(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        strict = self._with_instrument(config, max_spread_fraction=Decimal("0.00001"))
        codes = _validate(
            _request(intent, strict, limits, portfolio, snapshot, shadow_version)
        ).codes
        assert RejectionCode.SPREAD_TOO_WIDE.value in codes


class TestSafetyGates:
    def test_a_duplicate_idempotency_key_blocks(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        codes = _validate(
            _request(
                intent,
                config,
                limits,
                portfolio,
                snapshot,
                shadow_version,
                submitted_idempotency_keys=frozenset({intent.idempotency_key}),
            )
        ).codes
        assert RejectionCode.DUPLICATE_ORDER.value in codes

    @pytest.mark.parametrize(
        ("scope", "target"),
        [
            (KillSwitchScope.SYSTEM, "*"),
            (KillSwitchScope.STRATEGY, "fixture_double@1.0.0"),
            (KillSwitchScope.SYMBOL, "ACME"),
            (KillSwitchScope.BROKER, "simulator"),
            (KillSwitchScope.ACCOUNT, "TEST-ACCOUNT"),
        ],
    )
    def test_every_kill_switch_scope_blocks(
        self, intent, config, limits, portfolio, snapshot, shadow_version, base_time, scope, target
    ):
        switches = KillSwitchRegistry()
        switches.engage(
            KillSwitch(
                scope=scope,
                target=target,
                trigger=KillSwitchTrigger.MANUAL,
                reason="halted for test",
                engaged_at=base_time,
                engaged_by="operator",
            )
        )
        codes = _validate(
            _request(intent, config, limits, portfolio, snapshot, shadow_version),
            kill_switches=switches,
        ).codes
        assert RejectionCode.KILL_SWITCH_ACTIVE.value in codes

    def test_an_agent_veto_blocks(
        self, intent, config, limits, portfolio, snapshot, shadow_version, base_time
    ):
        store = InMemoryAgentContextStore()
        store.publish(
            AgentContext(
                context_id=AgentContext.derive_id(
                    symbol="ACME", strategy_id=None, publisher="claude", created_at=base_time
                ),
                symbol="ACME",
                veto=True,
                reason_codes=(ContextReasonCode.EARNINGS_IMMINENT,),
                publisher="claude",
                publisher_version="1",
                created_at=base_time,
                expires_at=base_time + timedelta(hours=1),
            )
        )
        codes = _validate(
            _request(intent, config, limits, portfolio, snapshot, shadow_version),
            agent_store=store,
        ).codes
        assert RejectionCode.AGENT_CONTEXT_BLOCKS.value in codes

    def test_an_unhealthy_broker_blocks_outside_shadow(
        self, intent, config, limits, portfolio, snapshot, strategy_version
    ):
        paper = config.model_copy(update={"revision": 2, "execution_mode": ExecutionMode.PAPER})
        codes = _validate(
            _request(
                intent,
                paper,
                limits,
                portfolio,
                snapshot,
                strategy_version.model_copy(update={"stage": PromotionStage.PAPER}),
                account_state=_account(),
                broker_health=_health(healthy=False),
            )
        ).codes
        assert RejectionCode.BROKER_UNAVAILABLE.value in codes

    def test_a_strict_agent_policy_blocks_when_claude_is_silent(
        self, intent, config, limits, portfolio, snapshot, shadow_version
    ):
        strict = config.model_copy(
            update={
                "revision": 2,
                "agent_context_policy": AgentContextPolicy.REQUIRE_VALID_AGENT_CONTEXT,
            }
        )
        codes = _validate(
            _request(intent, strict, limits, portfolio, snapshot, shadow_version),
            agent_store=InMemoryAgentContextStore(),
        ).codes
        assert RejectionCode.AGENT_CONTEXT_BLOCKS.value in codes


def portfolio_with_equity(base_time) -> PortfolioState:
    """A flat 100k account."""
    return PortfolioState(as_of=base_time, account_equity=Decimal("100000"), cash=Decimal("100000"))


def test_asset_class_mismatch_blocks(intent, config, limits, portfolio, snapshot, strategy_version):
    shadow = strategy_version.model_copy(update={"stage": PromotionStage.SHADOW})
    option_only = config.model_copy(
        update={
            "revision": 2,
            "instruments": (
                config.instruments[0].model_copy(update={"asset_class": AssetClass.OPTION}),
            ),
        }
    )
    codes = _validate(_request(intent, option_only, limits, portfolio, snapshot, shadow)).codes
    assert RejectionCode.INSTRUMENT_NOT_PERMITTED.value in codes
