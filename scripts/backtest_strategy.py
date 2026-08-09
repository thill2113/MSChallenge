"""Backtest a strategy over captured daily bars.

Reads a ``get_equity_historicals`` capture, replays the strategy bar by bar with
a bounded look-back window, simulates each candidate's outcome, and prints the
statistics that decide whether the strategy survives.

The strategy sees only bars strictly before the decision point. Its own
``required_history()`` bounds the window, so no evaluation can reach further
back than the strategy claims to need — and none can reach forward at all.

Usage::

    python scripts/backtest_strategy.py --bars capture.json
    python scripts/backtest_strategy.py --bars capture.json --entry LIMIT_AT_SIGNAL
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from backtesting.simulation import (  # noqa: E402
    BacktestReport,
    EntryStyle,
    SimulatedTrade,
    simulate_trade,
)
from domain.enums import AssetClass  # noqa: E402
from market_data.models import Bar, MarketSnapshot  # noqa: E402
from strategies.context import EvaluationContext, PortfolioView  # noqa: E402
from strategies.library import TrendBreakoutV1  # noqa: E402
from strategies.models import TradeCandidate  # noqa: E402


def load_series(path: Path) -> dict[str, list[MarketSnapshot]]:
    """Parse a historicals capture into snapshots, one list per symbol."""
    payload = json.loads(path.read_text())
    results = payload["data"]["results"] if "data" in payload else payload["results"]
    series: dict[str, list[MarketSnapshot]] = {}
    for entry in results:
        symbol = entry["symbol"]
        snapshots: list[MarketSnapshot] = []
        for raw in entry["bars"]:
            close = Decimal(raw["close_price"])
            snapshots.append(
                MarketSnapshot(
                    symbol=symbol,
                    asset_class=AssetClass.EQUITY,
                    as_of=datetime.fromisoformat(raw["begins_at"].replace("Z", "+00:00")).replace(
                        tzinfo=UTC
                    ),
                    last_price=close,
                    bar=Bar(
                        interval_seconds=86_400,
                        open=Decimal(raw["open_price"]),
                        high=Decimal(raw["high_price"]),
                        low=Decimal(raw["low_price"]),
                        close=close,
                        volume=Decimal(raw["volume"]),
                    ),
                    provider="robinhood_historicals",
                )
            )
        series[symbol] = snapshots
    return series


def run(
    series: dict[str, list[MarketSnapshot]],
    *,
    entry_style: EntryStyle,
    slippage: Decimal,
    equity: str,
    time_stop_bars: int | None,
    start: str | None = None,
    end: str | None = None,
) -> BacktestReport:
    """Replay the strategy across every symbol and resolve each candidate."""
    strategy = TrendBreakoutV1()
    window = strategy.required_history()
    portfolio = PortfolioView(account_equity=equity)

    trades: list[SimulatedTrade] = []
    signals = 0
    signal_dates: list[str] = []

    for symbol, snapshots in sorted(series.items()):
        held_until = -1  # index before which no new entry may be taken
        for index in range(window, len(snapshots) - 1):
            if index <= held_until:
                continue  # one position at a time, per the strategy's own rule
            context = EvaluationContext.build(
                snapshot=snapshots[index],
                history=snapshots[max(0, index - window) : index],
                portfolio=portfolio,
            )
            decision = strategy.evaluate(context)
            if not isinstance(decision, TradeCandidate):
                continue
            signal_date = snapshots[index].as_of.date().isoformat()
            if (start and signal_date < start) or (end and signal_date >= end):
                continue

            signals += 1
            signal_dates.append(signal_date)
            trade = simulate_trade(
                decision,
                snapshots[index + 1 :],
                entry_style=entry_style,
                slippage_fraction=slippage,
                time_stop_bars=time_stop_bars,
            )
            if trade is None:
                continue
            trades.append(trade)
            held_until = index + 1 + trade.bars_held
        print(f"  {symbol:6s} {len(snapshots):5d} bars", file=sys.stderr)

    return BacktestReport(
        strategy_key=f"{strategy.metadata.strategy_id}@{strategy.version}",
        entry_style=entry_style,
        trades=tuple(trades),
        signals=signals,
        symbols=len(series),
        first_signal=min(signal_dates) if signal_dates else "",
        last_signal=max(signal_dates) if signal_dates else "",
    )


def report(result: BacktestReport, slippage: Decimal) -> None:
    """Print the numbers that decide the strategy's fate."""
    print(f"\n{'=' * 62}")
    print(f"  {result.strategy_key}   entry={result.entry_style.value}")
    print(f"  {result.symbols} symbols   {result.first_signal} -> {result.last_signal}")
    print(f"  slippage {slippage * 100:.3f}% per side")
    print("=" * 62)
    print(f"  signals generated     {result.signals}")
    print(f"  trades filled         {result.count}")
    if not result.count:
        print("  no trades to evaluate")
        return
    print(f"  win rate              {result.win_rate * 100:.1f}%")
    print(f"  average win           {result.average_win_r:+.2f} R")
    print(f"  average loss          {result.average_loss_r:+.2f} R")
    print(f"  payoff ratio          {result.payoff_ratio:.2f}")
    print(f"  EXPECTANCY            {result.expectancy_r:+.4f} R per trade")
    print(
        f"  t-statistic           {result.t_statistic:+.2f}   "
        f"{'SIGNIFICANT' if abs(result.t_statistic) >= 2 else 'NOT distinguishable from zero'}"
    )
    print(f"  total                 {result.total_r:+.1f} R")
    print(f"  max drawdown          {result.max_drawdown_r:.1f} R")
    print(f"  max consec. losses    {result.max_consecutive_losses}")
    median_hold = sorted(t.bars_held for t in result.trades)[result.count // 2]
    print(f"  median bars held      {median_hold}")
    print(f"  exits                 {result.exit_breakdown()}")
    breakeven = (
        100 * abs(result.average_loss_r) / (result.average_win_r + abs(result.average_loss_r))
    )
    print(f"  break-even win rate   {breakeven:.1f}%")


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", required=True, type=Path)
    parser.add_argument("--entry", default="NEXT_OPEN", choices=[e.value for e in EntryStyle])
    parser.add_argument("--slippage", default="0.0002", help="Per side, as a fraction.")
    parser.add_argument("--equity", default="6474.38")
    parser.add_argument("--time-stop", type=int, default=None, help="Bars, or omit for none.")
    parser.add_argument("--start", default=None, help="Only signals on/after this ISO date.")
    parser.add_argument("--end", default=None, help="Only signals before this ISO date.")
    parser.add_argument("--by-symbol", action="store_true", help="Per-symbol contribution.")
    args = parser.parse_args()

    print("loading bars...", file=sys.stderr)
    series = load_series(args.bars)
    result = run(
        series,
        entry_style=EntryStyle(args.entry),
        slippage=Decimal(args.slippage),
        equity=args.equity,
        time_stop_bars=args.time_stop,
        start=args.start,
        end=args.end,
    )
    report(result, Decimal(args.slippage))
    if args.by_symbol:
        print("\n  per-symbol contribution (total R, trades, win rate):")
        for sym in sorted({t.symbol for t in result.trades}):
            sub = [t for t in result.trades if t.symbol == sym]
            total = sum((t.r_multiple for t in sub), Decimal(0))
            wins = sum(1 for t in sub if t.r_multiple > 0)
            rate = 100 * wins / len(sub)
            print(f"    {sym:6s} {total:+8.1f} R   {len(sub):4d} trades   {rate:5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
