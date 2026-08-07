"""End-to-end: strategy → risk → agent → execution.

Proves the layers compose and that each boundary holds when wired together, not
just in isolation. The replay is also checked for reproducibility: the same
inputs must produce the same sequence of decisions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backtesting.replay import run_replay
from brokers.robinhood_mcp.adapter import RobinhoodMCPAdapter
from domain.enums import AssetClass, ExecutionStatus
from domain.errors import AuthorityViolationError
from execution.gateway import ExecutionGateway, ExecutionMode, SimulatedBroker
from execution.models import OrderIntent
from market_data.models import MarketSnapshot
from market_data.providers import InMemoryMarketDataProvider
from tests.doubles import AffirmingReviewer, DeterministicFixtureStrategy, VetoingReviewer

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


class TestHappyPath:
    def test_candidate_reaches_the_simulated_broker(
        self, candidate, risk_engine, portfolio, context, base_time
    ):
        decision = risk_engine.evaluate(candidate, portfolio, evaluated_at=base_time)
        assert decision.is_approved

        from agents.gate import run_reviewers

        gate = run_reviewers(candidate, context, [AffirmingReviewer()])
        approved = gate.proceed()
        assert approved is candidate

        intent = OrderIntent.from_approved(
            approved, decision, created_at=base_time, agent_reviews=gate.reviews
        )
        broker = SimulatedBroker()
        result = ExecutionGateway(broker, mode=ExecutionMode.SIMULATED).submit(intent)

        assert result.status is ExecutionStatus.SIMULATED
        assert result.order_intent_fingerprint == intent.authoritative_fingerprint()
        assert broker.submitted == (intent,)


class TestReplay:
    def test_replay_is_reproducible(self, risk_engine, portfolio):
        snapshots = _series(["101", "5", "102", "8", "103"])
        strategy = DeterministicFixtureStrategy()
        kwargs = {
            "strategy": strategy,
            "snapshots": snapshots,
            "risk_engine": risk_engine,
            "portfolio": portfolio,
        }
        first = run_replay(**kwargs)
        second = run_replay(**kwargs)
        assert first.fingerprint() == second.fingerprint()
        assert first.steps == second.steps

    def test_replay_counts_each_outcome(self, risk_engine, portfolio):
        report = run_replay(
            strategy=DeterministicFixtureStrategy(),
            snapshots=_series(["101", "5", "102"]),
            risk_engine=risk_engine,
            portfolio=portfolio,
        )
        assert report.no_trade_count == 1
        assert report.candidate_count == 2
        assert report.transmitted_count == 2

    def test_a_veto_stops_transmission_without_stopping_the_replay(self, risk_engine, portfolio):
        report = run_replay(
            strategy=DeterministicFixtureStrategy(),
            snapshots=_series(["101", "102"]),
            risk_engine=risk_engine,
            portfolio=portfolio,
            reviewers=[VetoingReviewer()],
        )
        assert report.agent_vetoed_count == 2
        assert report.transmitted_count == 0

    def test_risk_rejections_are_counted_and_explained(self, risk_engine, base_time):
        from portfolio.models import PortfolioState

        tiny_account = PortfolioState(
            as_of=base_time,
            account_equity=Decimal("1000"),
            cash=Decimal("1000"),
        )
        report = run_replay(
            strategy=DeterministicFixtureStrategy(),
            snapshots=_series(["101"]),
            risk_engine=risk_engine,
            portfolio=tiny_account,
        )
        assert report.risk_rejected_count == 1
        assert "POSITION_NOTIONAL_EXCEEDED" in report.steps[0].risk_violation_codes

    def test_strategies_only_see_prior_snapshots(self, risk_engine, portfolio):
        # The history handed to each evaluation grows by exactly one per step,
        # so nothing can read a bar that had not happened yet.
        snapshots = _series(["101", "102", "103"])
        seen: list[int] = []

        class RecordingStrategy(DeterministicFixtureStrategy):
            def evaluate(self, context):
                seen.append(len(context.history))
                return super().evaluate(context)

        run_replay(
            strategy=RecordingStrategy(),
            snapshots=snapshots,
            risk_engine=risk_engine,
            portfolio=portfolio,
        )
        assert seen == [0, 1, 2]


class TestBrokerBoundary:
    def test_robinhood_adapter_refuses_to_place_orders(
        self, candidate, risk_engine, portfolio, base_time
    ):
        class NullSession:
            def call_tool(self, name, arguments):
                return {}

        adapter = RobinhoodMCPAdapter(NullSession())
        decision = risk_engine.evaluate(candidate, portfolio, evaluated_at=base_time)
        intent = OrderIntent.from_approved(candidate, decision, created_at=base_time)

        assert adapter.supports_live_orders is False
        with pytest.raises(AuthorityViolationError, match="cannot place orders"):
            adapter.submit(intent)

    def test_adapter_refuses_tools_outside_the_read_only_allowlist(self):
        class RecordingSession:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def call_tool(self, name, arguments):
                self.calls.append(name)
                return {"ok": True}

        session = RecordingSession()
        adapter = RobinhoodMCPAdapter(session)

        assert adapter.fetch("get_equity_positions", {}) == {"ok": True}
        with pytest.raises(AuthorityViolationError, match="read-only allowlist"):
            adapter.fetch("place_equity_order", {"symbol": "ACME"})
        assert session.calls == ["get_equity_positions"]

    def test_paper_mode_is_unavailable_without_a_capable_adapter(self):
        with pytest.raises(AuthorityViolationError, match="cannot place orders"):
            ExecutionGateway(SimulatedBroker(), mode=ExecutionMode.PAPER)


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
