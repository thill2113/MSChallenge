"""End-to-end: market data → strategy → risk → validator → execution → broker.

Proves the layers compose, that each boundary holds when wired together, and —
critically for the amended architecture — that **Claude is not in the path**.
The pipeline below never touches an agent store, and it still trades.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backtesting.replay import run_replay
from brokers.robinhood_mcp.adapter import RobinhoodMCPAdapter
from brokers.simulated import FailureMode, SimulatedBroker
from domain.enums import (
    AssetClass,
    BrokerCapability,
    ExecutionMode,
    ExecutionStatus,
    OrderState,
    PromotionStage,
)
from domain.errors import AuthorityViolationError, ImporterNotImplementedError
from execution.engine import ExecutionEngine
from execution.killswitch import KillSwitchRegistry
from execution.models import OrderIntent
from execution.validator import FinalValidator, ValidationRequest
from market_data.models import MarketSnapshot
from market_data.providers import InMemoryMarketDataProvider
from tests.doubles import DeterministicFixtureStrategy

START = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)


def _series(prices: list[str]) -> list[MarketSnapshot]:
    return [
        MarketSnapshot(
            symbol="ACME",
            asset_class=AssetClass.EQUITY,
            as_of=START + timedelta(minutes=5 * i),
            last_price=Decimal(p),
            provider="fixture",
        )
        for i, p in enumerate(prices)
    ]


def _shadow_version(strategy_version):
    """A version record standing at SHADOW, as the SHADOW-mode gate requires."""
    return strategy_version.model_copy(update={"stage": PromotionStage.SHADOW})


def _pipeline(kill_switches, agent_store=None):
    broker = SimulatedBroker(clock=START)
    engine = ExecutionEngine(
        broker=broker,
        validator=FinalValidator(kill_switches=kill_switches, agent_store=agent_store),
        kill_switches=kill_switches,
    )
    return broker, engine


def _submit(engine, intent, *, config, limits, portfolio, snapshot, version, now=START, **kw):
    return engine.submit(
        intent,
        config=config,
        validation=ValidationRequest(
            intent=intent,
            now=now,
            config=config,
            limits=limits,
            portfolio=portfolio,
            snapshot=snapshot,
            strategy_version=version,
            **kw,
        ),
    )


def _intent(candidate, risk_engine, portfolio, config, base_time):
    decision = risk_engine.evaluate(candidate, portfolio, evaluated_at=base_time)
    assert decision.is_approved
    return OrderIntent.from_approved(
        candidate, decision, created_at=base_time, configuration_hash=config.configuration_hash
    )


class TestShadowPathEndToEnd:
    """The first working concept: SHADOW mode, no Claude, no live money."""

    def test_intent_passes_every_gate_and_records_a_shadow_result(
        self, candidate, risk_engine, portfolio, config, limits, snapshot, strategy_version
    ):
        switches = KillSwitchRegistry()
        broker, engine = _pipeline(switches)
        intent = _intent(candidate, risk_engine, portfolio, config, snapshot.as_of)

        result = _submit(
            engine,
            intent,
            config=config,
            limits=limits,
            portfolio=portfolio,
            snapshot=snapshot,
            version=_shadow_version(strategy_version),
            now=snapshot.as_of,
        )

        assert result.status is ExecutionStatus.SIMULATED
        assert result.venue == "shadow:simulator"
        assert result.order_intent_fingerprint == intent.authoritative_fingerprint()
        # SHADOW validated everything and transmitted nothing.
        assert broker.get_orders() == ()

    def test_the_pipeline_runs_with_no_agent_store_at_all(
        self, candidate, risk_engine, portfolio, config, limits, snapshot, strategy_version
    ):
        # The load-bearing claim of the amendment: no Claude, no agent store,
        # still trades.
        switches = KillSwitchRegistry()
        _, engine = _pipeline(switches, agent_store=None)
        intent = _intent(candidate, risk_engine, portfolio, config, snapshot.as_of)
        result = _submit(
            engine,
            intent,
            config=config,
            limits=limits,
            portfolio=portfolio,
            snapshot=snapshot,
            version=_shadow_version(strategy_version),
            now=snapshot.as_of,
        )
        assert result.status is ExecutionStatus.SIMULATED

    def test_paper_mode_reaches_the_broker_and_fills(
        self, candidate, risk_engine, portfolio, config, limits, snapshot, strategy_version
    ):
        paper_config = config.model_copy(
            update={"revision": 2, "execution_mode": ExecutionMode.PAPER}
        )
        switches = KillSwitchRegistry()
        broker, engine = _pipeline(switches)
        broker.set_clock(snapshot.as_of)
        intent = _intent(candidate, risk_engine, portfolio, paper_config, snapshot.as_of)

        result = _submit(
            engine,
            intent,
            config=paper_config,
            limits=limits,
            portfolio=portfolio,
            snapshot=snapshot,
            version=strategy_version.model_copy(update={"stage": PromotionStage.PAPER}),
            now=snapshot.as_of,
            account_state=broker.get_account_state(),
            broker_health=broker.health_check(),
        )

        assert result.status is ExecutionStatus.FILLED
        assert result.order_state is OrderState.FILLED
        assert result.filled_quantity == candidate.quantity
        assert len(broker.get_orders()) == 1

    def test_events_are_published_for_the_whole_journey(
        self, candidate, risk_engine, portfolio, config, limits, snapshot, strategy_version
    ):
        from observability.events import EventType

        paper_config = config.model_copy(
            update={"revision": 2, "execution_mode": ExecutionMode.PAPER}
        )
        switches = KillSwitchRegistry()
        broker, engine = _pipeline(switches)
        broker.set_clock(snapshot.as_of)
        intent = _intent(candidate, risk_engine, portfolio, paper_config, snapshot.as_of)
        _submit(
            engine,
            intent,
            config=paper_config,
            limits=limits,
            portfolio=portfolio,
            snapshot=snapshot,
            version=strategy_version.model_copy(update={"stage": PromotionStage.PAPER}),
            now=snapshot.as_of,
            account_state=broker.get_account_state(),
            broker_health=broker.health_check(),
        )

        published = {e.event_type for e in engine.events}
        assert EventType.ORDER_INTENT_CREATED in published
        assert EventType.ORDER_SUBMITTED in published
        assert EventType.ORDER_FILLED in published
        assert EventType.POSITION_OPENED in published


class TestReplay:
    def test_replay_is_reproducible(self, risk_engine, portfolio, config, strategy_version):
        snapshots = _series(["101", "5", "102", "8", "103"])
        kwargs = {
            "strategy": DeterministicFixtureStrategy(),
            "snapshots": snapshots,
            "risk_engine": risk_engine,
            "portfolio": portfolio,
            "config": config,
            "strategy_version": _shadow_version(strategy_version),
        }
        first = run_replay(**kwargs)
        second = run_replay(**kwargs)
        assert first.fingerprint() == second.fingerprint()
        assert first.steps == second.steps

    def test_replay_counts_each_outcome(self, risk_engine, portfolio, config, strategy_version):
        report = run_replay(
            strategy=DeterministicFixtureStrategy(),
            snapshots=_series(["101", "5", "102"]),
            risk_engine=risk_engine,
            portfolio=portfolio,
            config=config,
            strategy_version=_shadow_version(strategy_version),
        )
        assert report.no_trade_count == 1
        assert report.candidate_count == 2
        assert report.transmitted_count == 2
        assert report.execution_mode == "SHADOW"

    def test_a_cached_veto_stops_transmission_without_stopping_the_replay(
        self, risk_engine, portfolio, config, strategy_version, agent_store
    ):
        from agents.context import AgentContext, ContextReasonCode
        from domain.enums import AgentContextPolicy

        agent_store.publish(
            AgentContext(
                context_id=AgentContext.derive_id(
                    symbol="ACME", strategy_id=None, publisher="claude", created_at=START
                ),
                symbol="ACME",
                veto=True,
                reason_codes=(ContextReasonCode.EARNINGS_IMMINENT,),
                summary="earnings tomorrow",
                publisher="claude",
                publisher_version="1",
                created_at=START,
                expires_at=START + timedelta(hours=6),
            )
        )
        vetoing_config = config.model_copy(
            update={
                "revision": 2,
                "agent_context_policy": AgentContextPolicy.ALLOW_WITHOUT_AGENT_CONTEXT,
            }
        )
        report = run_replay(
            strategy=DeterministicFixtureStrategy(),
            snapshots=_series(["101", "102"]),
            risk_engine=risk_engine,
            portfolio=portfolio,
            config=vetoing_config,
            strategy_version=_shadow_version(strategy_version),
            agent_store=agent_store,
        )
        assert report.agent_vetoed_count == 2
        assert report.transmitted_count == 0

    def test_risk_rejections_are_counted_and_explained(
        self, risk_engine, base_time, config, strategy_version
    ):
        from portfolio.models import PortfolioState

        tiny_account = PortfolioState(
            as_of=base_time, account_equity=Decimal("1000"), cash=Decimal("1000")
        )
        report = run_replay(
            strategy=DeterministicFixtureStrategy(),
            snapshots=_series(["101"]),
            risk_engine=risk_engine,
            portfolio=tiny_account,
            config=config,
            strategy_version=_shadow_version(strategy_version),
        )
        assert report.risk_rejected_count == 1
        assert "POSITION_NOTIONAL_EXCEEDED" in report.steps[0].risk_violation_codes

    def test_strategies_only_see_prior_snapshots(
        self, risk_engine, portfolio, config, strategy_version
    ):
        seen: list[int] = []

        class RecordingStrategy(DeterministicFixtureStrategy):
            def evaluate(self, context):
                seen.append(len(context.history))
                return super().evaluate(context)

        run_replay(
            strategy=RecordingStrategy(),
            snapshots=_series(["101", "102", "103"]),
            risk_engine=risk_engine,
            portfolio=portfolio,
            config=config,
            strategy_version=_shadow_version(strategy_version),
        )
        assert seen == [0, 1, 2]


class TestBrokerBoundary:
    def test_robinhood_adapter_declares_no_trading_capability(self):
        adapter = RobinhoodMCPAdapter(_NullSession())
        assert adapter.capabilities == frozenset()
        assert BrokerCapability.SUBMIT not in adapter.capabilities

    def test_engine_refuses_to_translate_for_an_incapable_adapter(
        self, candidate, risk_engine, portfolio, config, base_time
    ):
        from domain.errors import BrokerUnavailableError

        switches = KillSwitchRegistry()
        engine = ExecutionEngine(
            broker=RobinhoodMCPAdapter(_NullSession()),
            validator=FinalValidator(kill_switches=switches),
            kill_switches=switches,
        )
        intent = _intent(candidate, risk_engine, portfolio, config, base_time)
        # Capability check fires before any order is built — the refusal is
        # structural, not a raise buried in submit_order.
        with pytest.raises(BrokerUnavailableError, match="does not support order submission"):
            engine.build_request(intent)

    def test_robinhood_submit_raises_as_a_second_stop(
        self, candidate, risk_engine, portfolio, config, base_time
    ):
        switches = KillSwitchRegistry()
        _, engine = _pipeline(switches)
        intent = _intent(candidate, risk_engine, portfolio, config, base_time)
        request = engine.build_request(intent)
        with pytest.raises(AuthorityViolationError, match="cannot place orders"):
            RobinhoodMCPAdapter(_NullSession()).submit_order(request)

    def test_adapter_refuses_tools_outside_the_read_only_allowlist(self):
        session = _RecordingSession()
        adapter = RobinhoodMCPAdapter(session)
        assert adapter.fetch("get_equity_positions", {}) == {"ok": True}
        with pytest.raises(AuthorityViolationError, match="read-only allowlist"):
            adapter.fetch("place_equity_order", {"symbol": "ACME"})
        assert session.calls == ["get_equity_positions"]

    def test_robinhood_reads_refuse_to_guess_at_payload_shapes(self):
        adapter = RobinhoodMCPAdapter(_NullSession())
        for call in (adapter.get_positions, adapter.get_account_state, adapter.get_orders):
            with pytest.raises(ImporterNotImplementedError, match="not yet inspected"):
                call()

    def test_simulator_cannot_stand_in_for_a_live_venue(self):
        assert BrokerCapability.LIVE_TRADING not in SimulatedBroker(clock=START).capabilities
        assert BrokerCapability.PAPER_TRADING in SimulatedBroker(clock=START).capabilities

    def test_bracket_protection_is_selected_when_the_venue_supports_it(
        self, candidate, risk_engine, portfolio, config, base_time
    ):
        from domain.enums import ProtectionStyle

        switches = KillSwitchRegistry()
        _, engine = _pipeline(switches)
        intent = _intent(candidate, risk_engine, portfolio, config, base_time)
        assert engine.build_request(intent).protection is ProtectionStyle.BRACKET


class TestBrokerFailures:
    """Deterministic behaviour when the venue misbehaves (ADR-008)."""

    def _paper(self, config):
        return config.model_copy(update={"revision": 2, "execution_mode": ExecutionMode.PAPER})

    def test_timeout_records_unknown_and_halts_the_strategy(
        self, candidate, risk_engine, portfolio, config, limits, snapshot, strategy_version
    ):
        paper = self._paper(config)
        switches = KillSwitchRegistry()
        broker, engine = _pipeline(switches)
        broker.set_clock(snapshot.as_of)
        healthy_state = broker.get_account_state()
        health = broker.health_check()
        broker.set_failure_mode(FailureMode.TIMEOUT)

        intent = _intent(candidate, risk_engine, portfolio, paper, snapshot.as_of)
        result = _submit(
            engine,
            intent,
            config=paper,
            limits=limits,
            portfolio=portfolio,
            snapshot=snapshot,
            version=strategy_version.model_copy(update={"stage": PromotionStage.PAPER}),
            now=snapshot.as_of,
            account_state=healthy_state,
            broker_health=health,
        )

        assert result.order_state is OrderState.UNKNOWN
        # The order may be working, so the strategy is halted rather than retried.
        blocking = switches.blocking(strategy_key=intent.strategy_key)
        assert len(blocking) == 1
        assert blocking[0].trigger.value == "UNKNOWN_ORDER_STATE"

    def test_a_timed_out_order_can_be_reconciled_rather_than_resent(
        self, candidate, risk_engine, portfolio, config, limits, snapshot, strategy_version
    ):
        paper = self._paper(config)
        switches = KillSwitchRegistry()
        broker, engine = _pipeline(switches)
        broker.set_clock(snapshot.as_of)
        state, health = broker.get_account_state(), broker.health_check()
        broker.set_failure_mode(FailureMode.TIMEOUT)
        intent = _intent(candidate, risk_engine, portfolio, paper, snapshot.as_of)
        _submit(
            engine,
            intent,
            config=paper,
            limits=limits,
            portfolio=portfolio,
            snapshot=snapshot,
            version=strategy_version.model_copy(update={"stage": PromotionStage.PAPER}),
            now=snapshot.as_of,
            account_state=state,
            broker_health=health,
        )
        # The venue does have the order. Reconciliation finds it.
        recovered = engine.reconcile(intent)
        assert recovered is not None
        assert recovered.idempotency_key == intent.idempotency_key

    def test_resubmitting_the_same_intent_is_refused(
        self, candidate, risk_engine, portfolio, config, limits, snapshot, strategy_version
    ):
        from domain.errors import DuplicateOrderError

        paper = self._paper(config)
        switches = KillSwitchRegistry()
        broker, engine = _pipeline(switches)
        broker.set_clock(snapshot.as_of)
        version = strategy_version.model_copy(update={"stage": PromotionStage.PAPER})
        intent = _intent(candidate, risk_engine, portfolio, paper, snapshot.as_of)
        common = {
            "config": paper,
            "limits": limits,
            "portfolio": portfolio,
            "snapshot": snapshot,
            "version": version,
            "now": snapshot.as_of,
            "account_state": broker.get_account_state(),
            "broker_health": broker.health_check(),
        }
        _submit(engine, intent, **common)
        with pytest.raises(DuplicateOrderError, match="already been submitted"):
            _submit(engine, intent, **common)

    def test_broker_outage_engages_a_broker_kill_switch(
        self, candidate, risk_engine, portfolio, config, limits, snapshot, strategy_version
    ):
        paper = self._paper(config)
        switches = KillSwitchRegistry()
        broker, engine = _pipeline(switches)
        broker.set_clock(snapshot.as_of)
        state, health = broker.get_account_state(), broker.health_check()
        broker.set_failure_mode(FailureMode.UNAVAILABLE)
        intent = _intent(candidate, risk_engine, portfolio, paper, snapshot.as_of)
        result = _submit(
            engine,
            intent,
            config=paper,
            limits=limits,
            portfolio=portfolio,
            snapshot=snapshot,
            version=strategy_version.model_copy(update={"stage": PromotionStage.PAPER}),
            now=snapshot.as_of,
            account_state=state,
            broker_health=health,
        )
        assert result.order_state is OrderState.REJECTED
        assert switches.blocking(broker_id="simulator")
        # Nothing reached the venue, so the key is free for a later retry.
        assert intent.idempotency_key not in engine.submitted_keys

    def test_partial_fills_are_reported_as_partial(
        self, candidate, risk_engine, portfolio, config, limits, snapshot, strategy_version
    ):
        paper = self._paper(config)
        switches = KillSwitchRegistry()
        broker, engine = _pipeline(switches)
        broker.set_clock(snapshot.as_of)
        state, health = broker.get_account_state(), broker.health_check()
        broker.set_failure_mode(FailureMode.PARTIAL_FILL)
        intent = _intent(candidate, risk_engine, portfolio, paper, snapshot.as_of)
        result = _submit(
            engine,
            intent,
            config=paper,
            limits=limits,
            portfolio=portfolio,
            snapshot=snapshot,
            version=strategy_version.model_copy(update={"stage": PromotionStage.PAPER}),
            now=snapshot.as_of,
            account_state=state,
            broker_health=health,
        )
        assert result.status is ExecutionStatus.PARTIALLY_FILLED
        assert result.filled_quantity == candidate.quantity / 2

    def test_broker_idempotency_returns_the_original_order(self, candidate):
        broker = SimulatedBroker(clock=START)
        switches = KillSwitchRegistry()
        engine = ExecutionEngine(
            broker=broker,
            validator=FinalValidator(kill_switches=switches),
            kill_switches=switches,
        )
        from domain.enums import RiskVerdict
        from risk.models import RiskDecision

        fingerprint = candidate.authoritative_fingerprint()
        decision = RiskDecision(
            decision_id=RiskDecision.derive_id(
                candidate_fingerprint=fingerprint, limits_fingerprint="b" * 64
            ),
            candidate_id=candidate.candidate_id,
            candidate_fingerprint=fingerprint,
            verdict=RiskVerdict.APPROVED,
            limits_name="fixture_limits",
            limits_fingerprint="b" * 64,
            evaluated_at=START,
        )
        intent = OrderIntent.from_approved(
            candidate, decision, created_at=START, configuration_hash="c" * 64
        )
        request = engine.build_request(intent)
        first = broker.submit_order(request)
        second = broker.submit_order(request)
        assert first.broker_order_id == second.broker_order_id
        assert len(broker.get_orders()) == 1


class TestMarketDataProvider:
    def test_snapshots_are_addressable_by_instant(self):
        snapshots = _series(["101", "102", "103"])
        provider = InMemoryMarketDataProvider(snapshots)
        assert provider.get_snapshot("ACME", snapshots[1].as_of) == snapshots[1]
        assert provider.get_snapshot("ACME", START - timedelta(days=1)) is None

    def test_history_is_bounded_and_ordered(self):
        snapshots = _series(["101", "102", "103"])
        provider = InMemoryMarketDataProvider(reversed(snapshots))
        window = provider.history("ACME", snapshots[0].as_of, snapshots[1].as_of)
        assert [s.last_price for s in window] == [Decimal("101"), Decimal("102")]


class _NullSession:
    def call_tool(self, name, arguments):
        return {}


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def call_tool(self, name, arguments):
        self.calls.append(name)
        return {"ok": True}
