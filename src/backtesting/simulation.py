"""Trade outcome simulation for backtesting.

Turns a :class:`~strategies.models.TradeCandidate` into a completed trade by
walking forward through subsequent bars until the stop, the target, or a time
limit resolves it.

**This is a backtest simulator, not position management.** Live position
lifecycle is unbuilt (open question Q-7). Nothing here runs in the execution
path.

Three modelling choices, all of which bias results *downward*, on the principle
that a backtest which flatters a strategy is worse than useless:

1. **Intrabar ambiguity resolves to the stop.** When a daily bar's low reaches
   the stop *and* its high reaches the target, the order they were touched in is
   unknowable from daily data. The pessimistic reading is assumed every time.
2. **Gaps fill at the open, not the level.** A gap straight through the stop
   fills at the opening price, which is worse than the stop. Real trades work
   this way; assuming otherwise invents money.
3. **Slippage is charged on both sides.** Zero-commission does not mean
   zero-cost.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum, unique

from pydantic import Field

from domain.base import FrozenModel
from domain.values import ExactDecimal, Price, Quantity, Symbol, TimestampUTC
from market_data.models import MarketSnapshot
from strategies.models import TradeCandidate


@unique
class ExitReason(StrEnum):
    """Why a simulated trade ended."""

    STOP = "STOP"
    TARGET = "TARGET"
    TIME_STOP = "TIME_STOP"
    END_OF_DATA = "END_OF_DATA"
    """The series ran out while the position was still open. Counted separately
    so an optimistic tail of unresolved winners cannot masquerade as edge."""


@unique
class EntryStyle(StrEnum):
    """How the candidate's entry is assumed to fill."""

    LIMIT_AT_SIGNAL = "LIMIT_AT_SIGNAL"
    """Faithful to the candidate: a limit at the signal close, filled only if a
    later bar trades down to it. A breakout that never looks back is missed."""

    NEXT_OPEN = "NEXT_OPEN"
    """Market-on-open the bar after the signal. Always fills, at whatever the
    gap gives you."""


class SimulatedTrade(FrozenModel):
    """One resolved trade."""

    symbol: Symbol
    entry_at: TimestampUTC
    entry_price: Price
    quantity: Quantity
    stop_price: Price
    target_price: Price
    exit_at: TimestampUTC
    exit_price: Price
    exit_reason: ExitReason
    bars_held: int = Field(ge=0)
    pnl: ExactDecimal
    r_multiple: ExactDecimal = Field(
        description="Profit measured in units of the risk taken. -1.0 is a clean stop-out."
    )


TICK = Decimal("0.0001")
"""Prices are quantised to four places, matching the indicator quantum."""


def _tick(value: Decimal) -> Decimal:
    """Round a price to the standard quantum."""
    return value.quantize(TICK)


def _bar_of(snapshot: MarketSnapshot) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    if snapshot.bar is None:
        raise ValueError(f"{snapshot.symbol} at {snapshot.as_of} has no OHLC bar")
    bar = snapshot.bar
    return bar.open, bar.high, bar.low, bar.close


def simulate_trade(
    candidate: TradeCandidate,
    forward: Sequence[MarketSnapshot],
    *,
    entry_style: EntryStyle = EntryStyle.NEXT_OPEN,
    slippage_fraction: Decimal = Decimal("0.0002"),
    time_stop_bars: int | None = None,
    limit_patience_bars: int = 3,
) -> SimulatedTrade | None:
    """Resolve ``candidate`` against the bars that followed it.

    ``forward`` must begin with the bar *after* the signal bar. Returns ``None``
    when a limit entry never filled — a missed trade, not a losing one, and
    conflating the two overstates both the win rate and the trade count.
    """
    if candidate.target_price is None:
        raise ValueError("simulation requires a candidate with a declared target")
    if not forward:
        return None

    stop, target = candidate.stop_price, candidate.target_price
    risk_per_share = candidate.reference_price - stop
    if risk_per_share <= 0:
        raise ValueError("candidate stop is not below its entry reference")

    # --- entry ------------------------------------------------------------
    if entry_style is EntryStyle.NEXT_OPEN:
        entry_index = 0
        entry_price = _bar_of(forward[0])[0]
    else:
        limit = candidate.limit_price or candidate.reference_price
        entry_index = -1
        entry_price = limit
        for index, snapshot in enumerate(forward[:limit_patience_bars]):
            open_, _, low, _ = _bar_of(snapshot)
            if open_ <= limit:  # gapped below the limit: you get the better price
                entry_index, entry_price = index, open_
                break
            if low <= limit:
                entry_index, entry_price = index, limit
                break
        if entry_index < 0:
            return None

    # Quantise after applying slippage: a price with fifteen decimal places is
    # not a price, and the domain models correctly refuse it.
    entry_price = _tick(entry_price + entry_price * slippage_fraction)  # pay up on the way in
    if entry_price >= stop:
        pass
    else:  # a gap straight through the stop: the trade is over before it starts
        return _close(
            candidate,
            forward[entry_index],
            entry_price,
            entry_price,
            ExitReason.STOP,
            0,
            risk_per_share,
            slippage_fraction,
        )

    # --- walk forward ------------------------------------------------------
    for offset, snapshot in enumerate(forward[entry_index + 1 :], start=1):
        open_, high, low, _ = _bar_of(snapshot)

        if open_ <= stop:  # gapped through the stop overnight
            return _close(
                candidate,
                snapshot,
                entry_price,
                open_,
                ExitReason.STOP,
                offset,
                risk_per_share,
                slippage_fraction,
            )
        if open_ >= target:  # gapped through the target
            return _close(
                candidate,
                snapshot,
                entry_price,
                open_,
                ExitReason.TARGET,
                offset,
                risk_per_share,
                slippage_fraction,
            )
        if low <= stop:
            # Checked before the target: when both are touched in one bar the
            # order is unknowable, so assume the worse of the two.
            return _close(
                candidate,
                snapshot,
                entry_price,
                stop,
                ExitReason.STOP,
                offset,
                risk_per_share,
                slippage_fraction,
            )
        if high >= target:
            return _close(
                candidate,
                snapshot,
                entry_price,
                target,
                ExitReason.TARGET,
                offset,
                risk_per_share,
                slippage_fraction,
            )
        if time_stop_bars is not None and offset >= time_stop_bars:
            return _close(
                candidate,
                snapshot,
                entry_price,
                _bar_of(snapshot)[3],
                ExitReason.TIME_STOP,
                offset,
                risk_per_share,
                slippage_fraction,
            )

    last = forward[-1]
    return _close(
        candidate,
        last,
        entry_price,
        _bar_of(last)[3],
        ExitReason.END_OF_DATA,
        len(forward) - entry_index - 1,
        risk_per_share,
        slippage_fraction,
    )


def _close(
    candidate: TradeCandidate,
    snapshot: MarketSnapshot,
    entry_price: Decimal,
    raw_exit: Decimal,
    reason: ExitReason,
    bars_held: int,
    risk_per_share: Decimal,
    slippage_fraction: Decimal,
) -> SimulatedTrade:
    exit_price = _tick(raw_exit - raw_exit * slippage_fraction)  # and pay up on the way out
    pnl = (exit_price - entry_price) * candidate.quantity
    if candidate.target_price is None:  # pragma: no cover - guarded by the caller
        raise ValueError("candidate lost its target between validation and close")
    return SimulatedTrade(
        symbol=candidate.symbol,
        entry_at=candidate.as_of,
        entry_price=entry_price,
        quantity=candidate.quantity,
        stop_price=candidate.stop_price,
        target_price=candidate.target_price,
        exit_at=snapshot.as_of,
        exit_price=exit_price,
        exit_reason=reason,
        bars_held=bars_held,
        pnl=pnl.quantize(Decimal("0.0001")),
        r_multiple=((exit_price - entry_price) / risk_per_share).quantize(Decimal("0.0001")),
    )


class BacktestReport(FrozenModel):
    """Aggregate statistics over a set of simulated trades.

    Everything here is arithmetic over the trade list. No annualisation, no
    Sharpe ratio, no equity curve — those need assumptions about capital
    deployment that this simulator does not make, and a ratio computed on top of
    unstated assumptions is a number that looks rigorous and is not.
    """

    strategy_key: str
    entry_style: EntryStyle
    trades: tuple[SimulatedTrade, ...] = Field(default=(), repr=False)
    signals: int = Field(ge=0, description="Candidates produced, including unfilled limits.")
    symbols: int = Field(ge=0)
    first_signal: str = ""
    last_signal: str = ""

    @property
    def count(self) -> int:
        """Trades that actually filled."""
        return len(self.trades)

    @property
    def wins(self) -> int:
        """Trades closed above break-even."""
        return sum(1 for t in self.trades if t.r_multiple > 0)

    @property
    def win_rate(self) -> Decimal:
        """Fraction of filled trades that made money."""
        if not self.trades:
            return Decimal(0)
        return (Decimal(self.wins) / Decimal(self.count)).quantize(Decimal("0.0001"))

    @property
    def expectancy_r(self) -> Decimal:
        """Mean R per trade — the single number that decides whether this works."""
        if not self.trades:
            return Decimal(0)
        total = sum((t.r_multiple for t in self.trades), Decimal(0))
        return (total / Decimal(self.count)).quantize(Decimal("0.0001"))

    @property
    def total_r(self) -> Decimal:
        """Sum of R across every trade."""
        return sum((t.r_multiple for t in self.trades), Decimal(0)).quantize(Decimal("0.01"))

    @property
    def average_win_r(self) -> Decimal:
        """Mean R of winning trades."""
        won = [t.r_multiple for t in self.trades if t.r_multiple > 0]
        return (sum(won, Decimal(0)) / len(won)).quantize(Decimal("0.0001")) if won else Decimal(0)

    @property
    def average_loss_r(self) -> Decimal:
        """Mean R of losing trades (negative)."""
        lost = [t.r_multiple for t in self.trades if t.r_multiple <= 0]
        return (
            (sum(lost, Decimal(0)) / len(lost)).quantize(Decimal("0.0001")) if lost else Decimal(0)
        )

    @property
    def payoff_ratio(self) -> Decimal:
        """Average win divided by average loss, both in R."""
        if self.average_loss_r == 0:
            return Decimal(0)
        return (self.average_win_r / abs(self.average_loss_r)).quantize(Decimal("0.001"))

    @property
    def max_drawdown_r(self) -> Decimal:
        """Deepest peak-to-trough decline of the cumulative R curve."""
        peak = running = worst = Decimal(0)
        for trade in sorted(self.trades, key=lambda t: t.exit_at):
            running += trade.r_multiple
            peak = max(peak, running)
            worst = min(worst, running - peak)
        return worst.quantize(Decimal("0.01"))

    @property
    def max_consecutive_losses(self) -> int:
        """Longest run of losing trades, in exit order."""
        worst = run = 0
        for trade in sorted(self.trades, key=lambda t: t.exit_at):
            run = run + 1 if trade.r_multiple <= 0 else 0
            worst = max(worst, run)
        return worst

    @property
    def t_statistic(self) -> Decimal:
        """Expectancy divided by its standard error.

        The number that separates "this works" from "this sample happened to
        look like it works". Below roughly 2, an expectancy is not
        distinguishable from zero however good the headline looks.
        """
        if self.count < 2:
            return Decimal(0)
        mean = self.expectancy_r
        variance = sum(((t.r_multiple - mean) ** 2 for t in self.trades), Decimal(0)) / Decimal(
            self.count - 1
        )
        if variance <= 0:
            return Decimal(0)
        std_error = variance.sqrt() / Decimal(self.count).sqrt()
        return (mean / std_error).quantize(Decimal("0.01"))

    def exit_breakdown(self) -> dict[str, int]:
        """How trades ended, by reason."""
        counts: dict[str, int] = {}
        for trade in self.trades:
            counts[trade.exit_reason.value] = counts.get(trade.exit_reason.value, 0) + 1
        return counts


__all__ = [
    "BacktestReport",
    "EntryStyle",
    "ExitReason",
    "SimulatedTrade",
    "simulate_trade",
]
