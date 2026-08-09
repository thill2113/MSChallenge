"""The backtest simulator resolves trades pessimistically and reports honestly.

A simulator that flatters a strategy is worse than no simulator, because it
produces confidence rather than information. These tests pin the three
conservative choices in place so a later "optimisation" cannot quietly relax
them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backtesting.simulation import BacktestReport, EntryStyle, ExitReason, simulate_trade
from domain.enums import AssetClass, OrderType, Side, TimeInForce
from market_data.models import Bar, MarketSnapshot
from strategies.models import TradeCandidate

START = datetime(2026, 1, 2, 21, 0, tzinfo=UTC)


def _snap(day: int, *, open_: str, high: str, low: str, close: str) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="TEST",
        asset_class=AssetClass.EQUITY,
        as_of=START + timedelta(days=day),
        last_price=Decimal(close),
        bar=Bar(
            interval_seconds=86_400,
            open=Decimal(open_),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
            volume=Decimal("1000000"),
        ),
        provider="fixture",
    )


def _candidate(entry: str = "100", stop: str = "98", target: str = "103") -> TradeCandidate:
    return TradeCandidate(
        candidate_id=TradeCandidate.derive_id(
            strategy_id="trend_breakout",
            strategy_version="1.0.0",
            symbol="TEST",
            as_of=START,
        ),
        strategy_id="trend_breakout",
        strategy_version="1.0.0",
        symbol="TEST",
        as_of=START,
        asset_class=AssetClass.EQUITY,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        limit_price=Decimal(entry),
        quantity=Decimal("10"),
        reference_price=Decimal(entry),
        stop_price=Decimal(stop),
        target_price=Decimal(target),
        time_in_force=TimeInForce.DAY,
        account_risk_fraction=Decimal("0.005"),
    )


NO_SLIP = Decimal("0")


class TestPessimisticResolution:
    def test_a_bar_touching_both_stop_and_target_resolves_to_the_stop(self):
        # The order of touches is unknowable from a daily bar. Assume the worse.
        forward = [
            _snap(1, open_="100", high="100", low="100", close="100"),
            _snap(2, open_="100", high="104", low="97", close="100"),
        ]
        trade = simulate_trade(
            _candidate(), forward, entry_style=EntryStyle.NEXT_OPEN, slippage_fraction=NO_SLIP
        )
        assert trade is not None
        assert trade.exit_reason is ExitReason.STOP
        assert trade.r_multiple == Decimal("-1.0000")

    def test_a_gap_through_the_stop_fills_at_the_open_not_the_stop(self):
        # A gap costs more than the stop level. Pretending otherwise invents money.
        forward = [
            _snap(1, open_="100", high="101", low="99.5", close="100"),
            _snap(2, open_="90", high="91", low="89", close="90"),
        ]
        trade = simulate_trade(
            _candidate(), forward, entry_style=EntryStyle.NEXT_OPEN, slippage_fraction=NO_SLIP
        )
        assert trade is not None
        assert trade.exit_reason is ExitReason.STOP
        assert trade.exit_price == Decimal("90")
        assert trade.r_multiple < Decimal("-1")  # worse than a clean stop-out

    def test_a_gap_through_the_target_fills_at_the_open(self):
        forward = [
            _snap(1, open_="100", high="101", low="99.5", close="100"),
            _snap(2, open_="110", high="111", low="109", close="110"),
        ]
        trade = simulate_trade(
            _candidate(), forward, entry_style=EntryStyle.NEXT_OPEN, slippage_fraction=NO_SLIP
        )
        assert trade is not None
        assert trade.exit_reason is ExitReason.TARGET
        assert trade.exit_price == Decimal("110")

    def test_slippage_is_charged_on_both_sides(self):
        forward = [
            _snap(1, open_="100", high="101", low="99.5", close="100"),
            _snap(2, open_="100", high="104", low="99.5", close="103"),
        ]
        clean = simulate_trade(
            _candidate(), forward, entry_style=EntryStyle.NEXT_OPEN, slippage_fraction=NO_SLIP
        )
        costed = simulate_trade(
            _candidate(),
            forward,
            entry_style=EntryStyle.NEXT_OPEN,
            slippage_fraction=Decimal("0.001"),
        )
        assert clean is not None and costed is not None
        assert costed.entry_price > clean.entry_price
        assert costed.exit_price < clean.exit_price
        assert costed.r_multiple < clean.r_multiple


class TestEntryStyles:
    def test_a_limit_that_never_trades_down_returns_none(self):
        # A missed trade is not a losing trade. Conflating them overstates both
        # the win rate and the trade count.
        forward = [_snap(i, open_="105", high="107", low="104", close="106") for i in range(1, 5)]
        assert (
            simulate_trade(
                _candidate(),
                forward,
                entry_style=EntryStyle.LIMIT_AT_SIGNAL,
                slippage_fraction=NO_SLIP,
            )
            is None
        )

    def test_a_limit_fills_when_price_trades_down_to_it(self):
        forward = [
            _snap(1, open_="101", high="102", low="99", close="100"),
            _snap(2, open_="101", high="104", low="100", close="103"),
        ]
        trade = simulate_trade(
            _candidate(),
            forward,
            entry_style=EntryStyle.LIMIT_AT_SIGNAL,
            slippage_fraction=NO_SLIP,
        )
        assert trade is not None
        assert trade.entry_price == Decimal("100")

    def test_next_open_always_fills(self):
        forward = [_snap(i, open_="105", high="107", low="104", close="106") for i in range(1, 4)]
        trade = simulate_trade(
            _candidate(), forward, entry_style=EntryStyle.NEXT_OPEN, slippage_fraction=NO_SLIP
        )
        assert trade is not None
        assert trade.entry_price == Decimal("105")


class TestTermination:
    def test_a_time_stop_closes_at_the_close(self):
        forward = [_snap(i, open_="100", high="101", low="99.5", close="100") for i in range(1, 20)]
        trade = simulate_trade(
            _candidate(),
            forward,
            entry_style=EntryStyle.NEXT_OPEN,
            slippage_fraction=NO_SLIP,
            time_stop_bars=5,
        )
        assert trade is not None
        assert trade.exit_reason is ExitReason.TIME_STOP
        assert trade.bars_held == 5

    def test_running_out_of_data_is_reported_separately(self):
        # An unresolved winner at the end of the series must not be counted as
        # a clean target hit.
        forward = [_snap(i, open_="100", high="101", low="99.5", close="100") for i in range(1, 4)]
        trade = simulate_trade(
            _candidate(), forward, entry_style=EntryStyle.NEXT_OPEN, slippage_fraction=NO_SLIP
        )
        assert trade is not None
        assert trade.exit_reason is ExitReason.END_OF_DATA

    def test_an_empty_forward_window_returns_none(self):
        assert simulate_trade(_candidate(), []) is None

    def test_a_candidate_without_a_target_is_refused(self):
        with pytest.raises(ValueError, match="declared target"):
            simulate_trade(
                _candidate().model_copy(update={"target_price": None}),
                [_snap(1, open_="100", high="101", low="99", close="100")],
            )


class TestReportStatistics:
    def _report(self, r_multiples: list[str]) -> BacktestReport:
        trades = []
        for i, r in enumerate(r_multiples):
            forward = [
                _snap(1, open_="100", high="101", low="99.5", close="100"),
                _snap(2, open_="100", high="104", low="99.5", close="103")
                if Decimal(r) > 0
                else _snap(2, open_="100", high="101", low="97", close="98"),
            ]
            trade = simulate_trade(
                _candidate(), forward, entry_style=EntryStyle.NEXT_OPEN, slippage_fraction=NO_SLIP
            )
            assert trade is not None
            trades.append(
                trade.model_copy(
                    update={
                        "r_multiple": Decimal(r),
                        "exit_at": START + timedelta(days=10 + i),
                    }
                )
            )
        return BacktestReport(
            strategy_key="t@1.0.0",
            entry_style=EntryStyle.NEXT_OPEN,
            trades=tuple(trades),
            signals=len(trades),
            symbols=1,
        )

    def test_expectancy_is_the_mean_r(self):
        report = self._report(["1.5", "-1.0", "1.5", "-1.0"])
        assert report.expectancy_r == Decimal("0.2500")
        assert report.win_rate == Decimal("0.5000")
        assert report.payoff_ratio == Decimal("1.500")

    def test_max_drawdown_tracks_the_cumulative_curve(self):
        report = self._report(["1.5", "-1.0", "-1.0", "-1.0", "1.5"])
        assert report.max_drawdown_r == Decimal("-3.00")

    def test_max_consecutive_losses_is_counted_in_exit_order(self):
        assert self._report(["-1", "-1", "1.5", "-1", "-1", "-1"]).max_consecutive_losses == 3

    def test_a_thin_edge_is_reported_as_insignificant(self):
        # The point of the t-statistic: +0.05 R over a handful of trades is not
        # an edge, however positive it looks.
        report = self._report(["1.5", "-1.0"] * 10 + ["1.6"])
        assert report.expectancy_r > 0
        assert abs(report.t_statistic) < 2

    def test_an_empty_report_does_not_divide_by_zero(self):
        empty = BacktestReport(
            strategy_key="t@1.0.0", entry_style=EntryStyle.NEXT_OPEN, signals=0, symbols=0
        )
        assert empty.expectancy_r == 0
        assert empty.win_rate == 0
        assert empty.t_statistic == 0
        assert empty.payoff_ratio == 0
