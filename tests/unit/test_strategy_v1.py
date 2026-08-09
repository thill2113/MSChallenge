"""Strategy v1 — indicators, entry logic, sizing, and determinism.

The strategy is an untested hypothesis, so these tests do not assert that it is
profitable. They assert that it is *correct*: that it computes what it claims
to, refuses to trade when it cannot see enough, sizes to the risk budget, and
produces byte-identical decisions on identical inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from domain.enums import AssetClass, OrderType, Side, TimeInForce
from market_data.models import Bar, MarketSnapshot
from signals.indicators import (
    atr_percent,
    average_true_range,
    donchian_high,
    simple_moving_average,
    true_range,
)
from strategies.context import EvaluationContext, PortfolioView
from strategies.determinism import assert_deterministic, decision_fingerprint
from strategies.library import TrendBreakoutV1
from strategies.models import NoTrade, NoTradeReason, TradeCandidate

START = datetime(2026, 1, 2, 21, 0, tzinfo=UTC)
EQUITY = PortfolioView(account_equity="6474.38")


def _bar(close: Decimal, *, spread: Decimal = Decimal("1.00")) -> Bar:
    """A day bar centred on ``close`` with a fixed range."""
    return Bar(
        interval_seconds=86_400,
        open=close,
        high=close + spread,
        low=close - spread,
        close=close,
        volume=Decimal("5000000"),
    )


def _series(closes: list[str], *, spread: Decimal = Decimal("1.00")) -> list[MarketSnapshot]:
    return [
        MarketSnapshot(
            symbol="TEST",
            asset_class=AssetClass.EQUITY,
            as_of=START + timedelta(days=i),
            last_price=Decimal(c),
            bar=_bar(Decimal(c), spread=spread),
            provider="fixture",
        )
        for i, c in enumerate(closes)
    ]


def _uptrend(n: int = 260, start: Decimal = Decimal("50"), step: Decimal = Decimal("0.25")):
    """A steadily rising series long enough to fill a 200-bar average."""
    return _series([str(start + step * i) for i in range(n)])


def _context(snapshots: list[MarketSnapshot], portfolio: PortfolioView | None = EQUITY):
    return EvaluationContext.build(
        snapshot=snapshots[-1], history=tuple(snapshots[:-1]), portfolio=portfolio
    )


class TestIndicators:
    def test_sma_is_the_mean_of_closes(self):
        series = _series(["10", "20", "30", "40"])
        assert simple_moving_average(series, 4) == Decimal("25.0000")
        assert simple_moving_average(series, 2) == Decimal("35.0000")

    def test_sma_returns_none_rather_than_padding(self):
        # A 200-day average computed from 40 days is not a 200-day average.
        assert simple_moving_average(_series(["10", "20"]), 5) is None

    def test_true_range_takes_the_widest_of_the_three_measures(self):
        series = _series(["100", "110"])
        # bar range is 2.00; gap from prior close 100 to today's high 111 is 11.
        assert true_range(series[1], series[0]) == Decimal("11.00")

    def test_true_range_needs_ohlc_on_both_bars(self):
        with_bar = _series(["100", "110"])
        without = with_bar[0].model_copy(update={"bar": None})
        assert true_range(with_bar[1], without) is None

    def test_atr_averages_the_true_ranges(self):
        series = _series(["100"] * 20)  # flat: range is exactly the 2.00 bar width
        assert average_true_range(series, 14) == Decimal("2.0000")

    def test_atr_needs_one_more_bar_than_its_period(self):
        assert average_true_range(_series(["100"] * 14), 14) is None
        assert average_true_range(_series(["100"] * 15), 14) is not None

    def test_atr_percent_is_scale_free(self):
        cheap = _series(["10"] * 20, spread=Decimal("0.10"))
        rich = _series(["1000"] * 20, spread=Decimal("10"))
        assert atr_percent(cheap, 14) == atr_percent(rich, 14)

    def test_donchian_high_uses_closes(self):
        series = _series(["10", "30", "20"])
        assert donchian_high(series, 3) == Decimal("30")
        assert donchian_high(series, 2) == Decimal("30")

    def test_periods_must_be_positive(self):
        for fn in (simple_moving_average, donchian_high):
            with pytest.raises(ValueError, match="period must be positive"):
                fn(_series(["10", "20"]), 0)


class TestEntryConditions:
    def test_a_clean_breakout_in_an_uptrend_produces_a_candidate(self):
        decision = TrendBreakoutV1().evaluate(_context(_uptrend()))
        assert isinstance(decision, TradeCandidate)
        assert decision.side is Side.BUY
        assert decision.order_type is OrderType.LIMIT
        assert decision.time_in_force is TimeInForce.DAY
        assert decision.target_price is not None
        assert decision.stop_price < decision.reference_price < decision.target_price

    def test_insufficient_history_is_a_no_trade_not_a_crash(self):
        decision = TrendBreakoutV1().evaluate(_context(_uptrend(n=50)))
        assert isinstance(decision, NoTrade)
        assert decision.reason is NoTradeReason.INSUFFICIENT_DATA
        assert "200 required" in (decision.detail or "")

    def test_price_below_the_long_average_is_filtered_by_regime(self):
        # A long uptrend that has rolled over: last close beneath the 200-bar mean.
        series = _uptrend() + _series(["20"] * 5)
        decision = TrendBreakoutV1().evaluate(_context(series))
        assert isinstance(decision, NoTrade)
        assert decision.reason is NoTradeReason.FILTERED_BY_REGIME

    def test_no_breakout_is_a_no_setup(self):
        # Rising into a 200-bar uptrend, then flat: no new 20-bar closing high.
        series = _uptrend() + _series(["115"] * 25)
        decision = TrendBreakoutV1().evaluate(_context(series))
        assert isinstance(decision, NoTrade)
        assert decision.reason is NoTradeReason.NO_SETUP
        assert "did not exceed" in (decision.detail or "")

    def test_dead_volatility_is_rejected(self):
        # A stop inside the noise band is not a stop.
        quiet = _series(
            [str(Decimal("50") + Decimal("0.25") * i) for i in range(260)], spread=Decimal("0.001")
        )
        decision = TrendBreakoutV1().evaluate(_context(quiet))
        assert isinstance(decision, NoTrade)
        assert "below the floor" in (decision.detail or "")

    def test_berserk_volatility_is_rejected(self):
        wild = _series(
            [str(Decimal("50") + Decimal("0.25") * i) for i in range(260)], spread=Decimal("30")
        )
        decision = TrendBreakoutV1().evaluate(_context(wild))
        assert isinstance(decision, NoTrade)
        assert "exceeds the ceiling" in (decision.detail or "")

    def test_an_existing_position_blocks_a_second_entry(self):
        held = PortfolioView(account_equity="6474.38", open_position_symbols=("TEST",))
        decision = TrendBreakoutV1().evaluate(_context(_uptrend(), portfolio=held))
        assert isinstance(decision, NoTrade)
        assert "already holding" in (decision.detail or "")

    def test_without_a_portfolio_view_it_refuses_to_size(self):
        decision = TrendBreakoutV1().evaluate(_context(_uptrend(), portfolio=None))
        assert isinstance(decision, NoTrade)
        assert decision.reason is NoTradeReason.INSUFFICIENT_DATA
        assert "account equity" in (decision.detail or "")

    def test_bars_without_ohlc_are_a_no_trade(self):
        series = [s.model_copy(update={"bar": None}) for s in _uptrend()]
        decision = TrendBreakoutV1().evaluate(_context(series))
        assert isinstance(decision, NoTrade)
        assert decision.reason is NoTradeReason.INSUFFICIENT_DATA


class TestSizingAndRisk:
    def test_risk_never_exceeds_the_configured_fraction(self):
        strategy = TrendBreakoutV1(risk_fraction=Decimal("0.005"))
        candidate = strategy.evaluate(_context(_uptrend()))
        assert isinstance(candidate, TradeCandidate)
        risk = candidate.quantity * (candidate.reference_price - candidate.stop_price)
        assert risk <= Decimal("6474.38") * Decimal("0.005")
        assert candidate.account_risk_fraction <= Decimal("0.005")

    def test_quantity_is_whole_shares(self):
        candidate = TrendBreakoutV1().evaluate(_context(_uptrend()))
        assert isinstance(candidate, TradeCandidate)
        assert candidate.quantity == candidate.quantity.to_integral_value()

    def test_reward_to_risk_matches_the_configured_multiples(self):
        candidate = TrendBreakoutV1(
            stop_atr_multiple=Decimal("2"), target_atr_multiple=Decimal("3")
        ).evaluate(_context(_uptrend()))
        assert isinstance(candidate, TradeCandidate)
        assert candidate.target_price is not None
        reward = candidate.target_price - candidate.reference_price
        risk = candidate.reference_price - candidate.stop_price
        assert (reward / risk).quantize(Decimal("0.01")) == Decimal("1.50")

    def test_an_account_too_small_for_one_share_declines(self):
        tiny = PortfolioView(account_equity="157.94")  # the agentic account
        decision = TrendBreakoutV1().evaluate(_context(_uptrend(), portfolio=tiny))
        assert isinstance(decision, NoTrade)
        assert "too small" in (decision.detail or "")

    def test_a_target_inside_the_stop_is_rejected_at_construction(self):
        with pytest.raises(ValueError, match="further from entry than the stop"):
            TrendBreakoutV1(stop_atr_multiple=Decimal("3"), target_atr_multiple=Decimal("2"))

    def test_volatility_bounds_must_be_ordered(self):
        with pytest.raises(ValueError, match="must be below"):
            TrendBreakoutV1(min_atr_percent=Decimal("0.5"), max_atr_percent=Decimal("0.1"))


class TestDeterminism:
    def test_repeated_evaluation_is_byte_identical(self):
        context = _context(_uptrend())
        assert_deterministic(TrendBreakoutV1(), context, runs=8)

    def test_independently_built_contexts_agree(self):
        series = _uptrend()
        strategy = TrendBreakoutV1()
        first = strategy.evaluate(_context(series))
        second = strategy.evaluate(_context([s.model_copy(deep=True) for s in series]))
        assert decision_fingerprint(first) == decision_fingerprint(second)

    def test_no_trades_are_deterministic_too(self):
        assert_deterministic(TrendBreakoutV1(), _context(_uptrend(n=50)), runs=4)

    def test_a_different_parameter_set_is_a_different_decision(self):
        # ADR-004: parameters are part of identity, so tuning must be visible.
        context = _context(_uptrend())
        base = TrendBreakoutV1().evaluate(context)
        tuned = TrendBreakoutV1(stop_atr_multiple=Decimal("1.5")).evaluate(context)
        assert decision_fingerprint(base) != decision_fingerprint(tuned)

    def test_the_version_record_carries_every_parameter(self):
        strategy = TrendBreakoutV1()
        record = strategy.version_record(code_fingerprint="a" * 64, created_at=START)
        assert record.parameter_map()["stop_atr_multiple"] == "2.0"
        assert len(record.parameters) == 9

    def test_decisions_record_their_input_fingerprint(self):
        context = _context(_uptrend())
        decision = TrendBreakoutV1().evaluate(context)
        assert decision.inputs_fingerprint == context.fingerprint()
