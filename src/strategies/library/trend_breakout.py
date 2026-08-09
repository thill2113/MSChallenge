"""Strategy v1 — long-only trend-continuation breakout.

**This has not been backtested. It is a starting hypothesis, not an edge.**
Its parameters are conventional defaults, deliberately not tuned, and the entire
point of running it through BACKTEST → OUT_OF_SAMPLE → WALK_FORWARD → SHADOW is
to find out whether it has any expectancy at all. It may not.

Why *this* shape, given the account's actual trading record
-----------------------------------------------------------

The recorded history (62 closed trades, 24% win rate, average win $102 against
average loss $433, largest single loss over 2x the current account value) shows
one mechanism repeating: **unbounded loss on a position that kept going the wrong
way**, in instruments with no defined exit. Every design choice below is the
structural opposite of that mechanism.

* **Every trade carries a stop, set before entry.** The prior history contains no
  stop orders at all. This is the single largest change.
* **Trend continuation, not mean reversion.** Mean reversion buys weakness, which
  is precisely the losing mechanism above. A breakout is wrong more often, but it
  is wrong *cheaply* — the stop is hit and the position is gone.
* **Limit orders, never market.** The prior equity history is almost entirely
  market orders, which pay the spread invisibly on every trade.
* **Multi-day holds.** A sub-$25k margin account is subject to the pattern
  day-trader rule; a strategy that needs intraday round trips cannot run in this
  account at all. It is also the opposite of the 0DTE options pattern that
  produced most of the equity/option losses.
* **One position at a time, liquid instruments only.** Concentration in a single
  illiquid name is how a bad trade becomes a catastrophic one.

The strategy authors every trading parameter and reads nothing but its
:class:`~strategies.context.EvaluationContext` (ADR-001). It is a pure function:
no clock, no network, no randomness, no state between evaluations.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from domain.enums import AssetClass, OrderType, Side, TimeInForce
from domain.values import TimestampUTC
from market_data.models import MarketSnapshot
from signals.indicators import (
    atr_percent,
    average_true_range,
    donchian_high,
    quantize_price,
    simple_moving_average,
)
from strategies.context import EvaluationContext
from strategies.models import (
    NoTrade,
    NoTradeReason,
    StrategyDecision,
    StrategyMetadata,
    StrategyParameter,
    StrategyVersion,
    TradeCandidate,
)

STRATEGY_ID = "trend_breakout"
VERSION = "1.0.0"

METADATA = StrategyMetadata(
    strategy_id=STRATEGY_ID,
    display_name="Trend Continuation Breakout v1",
    description=(
        "Long-only. Enters on a 20-bar closing-high breakout while price is above a "
        "rising 200-bar average, with a volatility-scaled stop and a fixed "
        "reward-to-risk target. Untested; a starting hypothesis for shadow evaluation."
    ),
    owner="timmy.hill23@gmail.com",
    asset_classes=(AssetClass.EQUITY,),
    required_signals=("sma_200", "sma_50", "atr_14", "donchian_high_20"),
)


class TrendBreakoutV1:
    """Long-only breakout entry with an ATR-scaled stop and fixed-R target.

    All parameters are constructor arguments so a version can be published with
    a different set without editing code — but a changed parameter is a new
    :class:`~strategies.models.StrategyVersion`, never a mutation of an existing
    one (ADR-004).
    """

    def __init__(
        self,
        *,
        trend_period: int = 200,
        fast_trend_period: int = 50,
        breakout_period: int = 20,
        atr_period: int = 14,
        stop_atr_multiple: Decimal = Decimal("2.0"),
        target_atr_multiple: Decimal = Decimal("3.0"),
        min_atr_percent: Decimal = Decimal("0.01"),
        max_atr_percent: Decimal = Decimal("0.06"),
        risk_fraction: Decimal = Decimal("0.005"),
        version: str = VERSION,
    ) -> None:
        if target_atr_multiple <= stop_atr_multiple:
            raise ValueError(
                "target must be further from entry than the stop, or the strategy "
                "cannot have positive expectancy at any win rate"
            )
        if min_atr_percent >= max_atr_percent:
            raise ValueError("min_atr_percent must be below max_atr_percent")
        self._trend_period = trend_period
        self._fast_trend_period = fast_trend_period
        self._breakout_period = breakout_period
        self._atr_period = atr_period
        self._stop_atr = stop_atr_multiple
        self._target_atr = target_atr_multiple
        self._min_atr_pct = min_atr_percent
        self._max_atr_pct = max_atr_percent
        self._risk_fraction = risk_fraction
        self._version = version

    # -- identity -------------------------------------------------------------

    @property
    def metadata(self) -> StrategyMetadata:
        """Static identity of this strategy."""
        return METADATA

    @property
    def version(self) -> str:
        """Semantic version of this parameterisation."""
        return self._version

    def parameters(self) -> tuple[StrategyParameter, ...]:
        """Parameter set, for the :class:`~strategies.models.StrategyVersion` record."""
        return (
            StrategyParameter(name="trend_period", value=str(self._trend_period)),
            StrategyParameter(name="fast_trend_period", value=str(self._fast_trend_period)),
            StrategyParameter(name="breakout_period", value=str(self._breakout_period)),
            StrategyParameter(name="atr_period", value=str(self._atr_period)),
            StrategyParameter(name="stop_atr_multiple", value=str(self._stop_atr)),
            StrategyParameter(name="target_atr_multiple", value=str(self._target_atr)),
            StrategyParameter(name="min_atr_percent", value=str(self._min_atr_pct)),
            StrategyParameter(name="max_atr_percent", value=str(self._max_atr_pct)),
            StrategyParameter(name="risk_fraction", value=str(self._risk_fraction)),
        )

    def required_history(self) -> int:
        """Minimum prior snapshots needed before this strategy can decide."""
        return max(self._trend_period, self._breakout_period, self._atr_period + 1)

    def version_record(self, *, code_fingerprint: str, created_at: TimestampUTC) -> StrategyVersion:
        """Build the registry record for this parameterisation."""
        return StrategyVersion(
            strategy_id=STRATEGY_ID,
            version=self._version,
            code_fingerprint=code_fingerprint,
            parameters=self.parameters(),
            created_at=created_at,
        )

    # -- decision -------------------------------------------------------------

    def evaluate(self, context: EvaluationContext) -> StrategyDecision:
        """Return exactly one candidate or one no-trade. Never ``None``."""
        window: tuple[MarketSnapshot, ...] = (*context.history, context.snapshot)

        def no_trade(reason: NoTradeReason, detail: str) -> NoTrade:
            return NoTrade(
                decision_id=NoTrade.derive_id(
                    strategy_id=STRATEGY_ID,
                    strategy_version=self._version,
                    symbol=context.symbol,
                    as_of=context.as_of,
                ),
                strategy_id=STRATEGY_ID,
                strategy_version=self._version,
                symbol=context.symbol,
                as_of=context.as_of,
                reason=reason,
                detail=detail,
                inputs_fingerprint=context.fingerprint(),
            )

        if len(window) < self.required_history():
            return no_trade(
                NoTradeReason.INSUFFICIENT_DATA,
                f"{len(window)} snapshots available, {self.required_history()} required",
            )

        slow_ma = simple_moving_average(window, self._trend_period)
        fast_ma = simple_moving_average(window, self._fast_trend_period)
        atr = average_true_range(window, self._atr_period)
        breakout_level = donchian_high(window[:-1], self._breakout_period)
        volatility = atr_percent(window, self._atr_period)

        if slow_ma is None or fast_ma is None or atr is None or breakout_level is None:
            return no_trade(
                NoTradeReason.INSUFFICIENT_DATA,
                "one or more indicators could not be computed; bars may lack OHLC",
            )
        if volatility is None or atr <= 0:
            return no_trade(NoTradeReason.INSUFFICIENT_DATA, "ATR is zero or unavailable")

        close = context.snapshot.last_price

        # --- entry conditions, each reported by name when it fails -----------
        if close <= slow_ma:
            return no_trade(
                NoTradeReason.FILTERED_BY_REGIME,
                f"close {close} is not above the {self._trend_period}-bar average {slow_ma}",
            )
        if fast_ma <= slow_ma:
            return no_trade(
                NoTradeReason.FILTERED_BY_REGIME,
                f"{self._fast_trend_period}-bar average {fast_ma} is not above the "
                f"{self._trend_period}-bar average {slow_ma}",
            )
        if close <= breakout_level:
            return no_trade(
                NoTradeReason.NO_SETUP,
                f"close {close} did not exceed the {self._breakout_period}-bar high "
                f"{breakout_level}",
            )
        if volatility < self._min_atr_pct:
            return no_trade(
                NoTradeReason.NO_SETUP,
                f"volatility {volatility} is below the floor {self._min_atr_pct}; the stop "
                "would sit inside the noise band",
            )
        if volatility > self._max_atr_pct:
            return no_trade(
                NoTradeReason.NO_SETUP,
                f"volatility {volatility} exceeds the ceiling {self._max_atr_pct}",
            )

        portfolio = context.portfolio
        if portfolio is None:
            return no_trade(
                NoTradeReason.INSUFFICIENT_DATA,
                "no portfolio view supplied; the strategy cannot size a position without "
                "knowing account equity",
            )
        if context.symbol in portfolio.open_position_symbols:
            return no_trade(
                NoTradeReason.NO_SETUP,
                f"already holding {context.symbol}; this strategy does not add to positions",
            )

        # --- sizing -----------------------------------------------------------
        equity = Decimal(portfolio.account_equity)
        stop_price = quantize_price(close - self._stop_atr * atr)
        target_price = quantize_price(close + self._target_atr * atr)
        risk_per_share = close - stop_price

        if stop_price <= 0 or risk_per_share <= 0:
            return no_trade(
                NoTradeReason.NO_SETUP,
                "computed stop is not below the entry; refusing to size a trade with no "
                "defined loss",
            )

        risk_budget = equity * self._risk_fraction
        quantity = (risk_budget / risk_per_share).to_integral_value(rounding=ROUND_DOWN)
        if quantity < 1:
            return no_trade(
                NoTradeReason.NO_SETUP,
                f"risk budget {risk_budget} buys less than one share at a risk-per-share "
                f"of {risk_per_share}; the account is too small for this setup",
            )

        risk_amount = quantity * risk_per_share
        account_risk_fraction = (risk_amount / equity).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        )

        return TradeCandidate(
            candidate_id=TradeCandidate.derive_id(
                strategy_id=STRATEGY_ID,
                strategy_version=self._version,
                symbol=context.symbol,
                as_of=context.as_of,
            ),
            strategy_id=STRATEGY_ID,
            strategy_version=self._version,
            symbol=context.symbol,
            as_of=context.as_of,
            asset_class=AssetClass.EQUITY,
            side=Side.BUY,
            # Limit at the signal close. A market order pays the spread on every
            # trade, which the recorded history did on almost all of them.
            order_type=OrderType.LIMIT,
            limit_price=close,
            quantity=quantity,
            reference_price=close,
            stop_price=stop_price,
            target_price=target_price,
            # DAY, not GTC: if the level is gone tomorrow, the setup is gone too.
            time_in_force=TimeInForce.DAY,
            account_risk_fraction=account_risk_fraction,
            rationale=(
                f"{self._breakout_period}-bar closing-high breakout at {close} with "
                f"{self._fast_trend_period}/{self._trend_period} trend alignment; "
                f"ATR({self._atr_period})={atr}, stop {self._stop_atr}x ATR at {stop_price}, "
                f"target {self._target_atr}x ATR at {target_price}, "
                f"{quantity} shares risking {risk_amount}"
            ),
            inputs_fingerprint=context.fingerprint(),
        )


__all__ = ["METADATA", "STRATEGY_ID", "VERSION", "TrendBreakoutV1"]
