"""Identical deterministic inputs produce identical strategy decisions (task 8).

Two halves, and both matter:

* **Positive.** Repeated evaluation of an identical context produces byte-equal
  decisions, over a wide space of generated inputs.
* **Negative.** The harness actually detects non-determinism. A determinism test
  that cannot fail proves only that the test runner works.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from domain.enums import AssetClass
from domain.errors import AuthorityViolationError
from market_data.models import Bar, MarketSnapshot, Quote
from strategies.context import EvaluationContext
from strategies.determinism import assert_deterministic, decision_fingerprint, evaluate_repeatedly
from strategies.models import NoTrade, TradeCandidate
from tests.doubles import DeterministicFixtureStrategy, NonDeterministicStrategy

pytestmark = pytest.mark.invariant

EPOCH = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)

prices = st.decimals(min_value=Decimal("1"), max_value=Decimal("5000"), places=2)
symbols = st.sampled_from(["ACME", "BETA", "GAMMA", "DELTA.X", "EPS-A"])
offsets = st.integers(min_value=0, max_value=5000)


@st.composite
def snapshots(draw: st.DrawFn) -> MarketSnapshot:
    """Generate a self-consistent market snapshot."""
    low = draw(prices)
    span = draw(st.decimals(min_value=Decimal("0"), max_value=Decimal("50"), places=2))
    high = low + span
    open_price = draw(st.decimals(min_value=low, max_value=high, places=2))
    close = draw(st.decimals(min_value=low, max_value=high, places=2))
    spread = draw(st.decimals(min_value=Decimal("0"), max_value=Decimal("0.50"), places=2))
    return MarketSnapshot(
        symbol=draw(symbols),
        asset_class=AssetClass.EQUITY,
        as_of=EPOCH + timedelta(seconds=draw(offsets)),
        last_price=close,
        bar=Bar(
            interval_seconds=300,
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=draw(st.decimals(min_value=0, max_value=10**6, places=0)),
        ),
        quote=Quote(bid=close, ask=close + spread),
        provider=draw(st.sampled_from(["fixture", "replay", "vendor_a"])),
    )


class TestDeterminism:
    """Same context in, same decision out."""

    @settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow])
    @given(snapshot=snapshots())
    def test_repeated_evaluation_is_identical(self, snapshot: MarketSnapshot) -> None:
        strategy = DeterministicFixtureStrategy()
        context = EvaluationContext.build(snapshot=snapshot)
        fingerprints = evaluate_repeatedly(strategy, context, runs=5)
        assert len(set(fingerprints)) == 1

    @settings(max_examples=100)
    @given(snapshot=snapshots())
    def test_independently_built_contexts_agree(self, snapshot: MarketSnapshot) -> None:
        # Rebuilding the context from scratch, rather than reusing one object,
        # rules out the decision being memoised on the context instance.
        strategy = DeterministicFixtureStrategy()
        first = strategy.evaluate(EvaluationContext.build(snapshot=snapshot))
        second = strategy.evaluate(EvaluationContext.build(snapshot=snapshot.model_copy()))
        assert decision_fingerprint(first) == decision_fingerprint(second)
        assert first == second

    @settings(max_examples=100)
    @given(snapshot=snapshots())
    def test_decision_ids_are_derived_not_random(self, snapshot: MarketSnapshot) -> None:
        strategy = DeterministicFixtureStrategy()
        context = EvaluationContext.build(snapshot=snapshot)
        first = strategy.evaluate(context)
        second = strategy.evaluate(context)
        if isinstance(first, TradeCandidate):
            assert isinstance(second, TradeCandidate)
            assert first.candidate_id == second.candidate_id
        else:
            assert isinstance(second, NoTrade)
            assert first.decision_id == second.decision_id

    @settings(max_examples=100)
    @given(snapshot=snapshots())
    def test_inputs_fingerprint_is_stable(self, snapshot: MarketSnapshot) -> None:
        a = EvaluationContext.build(snapshot=snapshot)
        b = EvaluationContext.build(snapshot=snapshot.model_copy(deep=True))
        assert a.fingerprint() == b.fingerprint()

    @settings(max_examples=75)
    @given(snapshot=snapshots(), bump=st.decimals(min_value="0.01", max_value="100", places=2))
    def test_different_inputs_generally_produce_different_fingerprints(
        self, snapshot: MarketSnapshot, bump: Decimal
    ) -> None:
        moved = snapshot.model_copy(
            update={"last_price": snapshot.last_price + bump, "bar": None, "quote": None}
        )
        base = snapshot.model_copy(update={"bar": None, "quote": None})
        assert base.authoritative_fingerprint() != moved.authoritative_fingerprint()


class TestHarnessDetectsNonDeterminism:
    """The negative control."""

    def test_assert_deterministic_passes_for_a_pure_strategy(self, context):
        decision = assert_deterministic(DeterministicFixtureStrategy(), context, runs=8)
        assert isinstance(decision, TradeCandidate)

    def test_assert_deterministic_fails_for_an_impure_strategy(self, context):
        with pytest.raises(AuthorityViolationError, match="non-deterministic"):
            assert_deterministic(NonDeterministicStrategy(), context, runs=8)

    def test_a_single_run_cannot_demonstrate_determinism(self, context):
        with pytest.raises(ValueError, match="at least two runs"):
            evaluate_repeatedly(DeterministicFixtureStrategy(), context, runs=1)


class TestProvenanceIsRecorded:
    """A decision knows which inputs produced it."""

    def test_candidate_records_the_context_fingerprint(self, context):
        decision = DeterministicFixtureStrategy().evaluate(context)
        assert decision.inputs_fingerprint == context.fingerprint()

    def test_no_trade_also_records_the_context_fingerprint(self, snapshot):
        quiet = snapshot.model_copy(
            update={"last_price": Decimal("5.00"), "bar": None, "quote": None}
        )
        context = EvaluationContext.build(snapshot=quiet)
        decision = DeterministicFixtureStrategy().evaluate(context)
        assert isinstance(decision, NoTrade)
        assert decision.inputs_fingerprint == context.fingerprint()
