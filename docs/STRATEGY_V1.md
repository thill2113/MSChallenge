# Strategy v1 — `trend_breakout@1.0.0`

**Status:** DEVELOPMENT. Not backtested. **This is a hypothesis, not an edge.**

I was asked for the best strategy I can produce. This is it — but "best I can
produce" and "profitable" are different claims, and I am only making the first
one. Nothing below has been tested against a single day of real price history.
Its purpose is to be the thing that goes through BACKTEST → OUT_OF_SAMPLE →
WALK_FORWARD → SHADOW and *finds out*. It may not survive that. Most don't.

---

## 1. What the account's actual history says

From the capture on 2026-08-09 — 62 closed trades, all time:

| | |
|---|---|
| Total realized | **−$18,811** |
| ‑ crypto | −$14,562 (PEPE alone −$12,987) |
| ‑ equities + options | −$4,250 |
| Win rate | **24.2%** (15 W / 47 L) |
| Average win | $102 |
| Average loss | **−$433** |
| Payoff ratio | **0.24** |
| Largest single loss | −$12,987 — **2× the current account value** |
| Stop orders ever used | **0**, across 8 years and 141 orders |
| Option pattern | 122 single-leg long calls/puts; **55 of 87 opens at 0DTE** |
| Current state | $6,632 cash across 2 accounts, **no open positions** |

A 24% win rate is survivable — trend systems live there. A 24% win rate with a
**0.24 payoff ratio** is not: it means roughly −$303 of expectancy per trade. The
mechanism is visible in one line of the data — *the average loss is four times
the average win*. Losers were held; winners were cut.

**That is a stop-loss problem, not a stock-picking problem.** It is the only
finding here specific enough to design against, and every choice below is aimed
at it.

## 2. Design, and the reason for each choice

| Choice | Reason |
|---|---|
| **Mandatory stop, priced before entry** | The single largest change. The system's `TradeCandidate` cannot even be constructed without one. |
| **Trend continuation, not mean reversion** | Mean reversion buys weakness. Buying weakness that kept going *is* the −$12,987 trade. A breakout is wrong more often but wrong cheaply. |
| **Fixed 1.5 : 1 reward-to-risk target** | Directly inverts the 0.24 payoff ratio. Combined with a stop, this makes the average win larger than the average loss by construction. |
| **Limit orders, never market** | 17 of 19 equity orders were market orders. That pays the spread on every single trade, invisibly. |
| **Multi-day holds, DAY time-in-force** | A sub-$25k margin account is bound by the pattern-day-trader rule — it *cannot* day-trade. It is also the opposite of the 0DTE pattern that produced most of the option losses. |
| **Volatility band (1%–6% ATR)** | Below 1%, the stop sits inside the noise and gets hit at random. Above 6%, position size collapses to nothing useful. |
| **One position per symbol, long only** | No stacking, no shorting, no leverage. Concentration is how a bad trade becomes a catastrophic one. |
| **Risk-based sizing (0.5% of equity)** | Size follows from the stop distance rather than from conviction. On $6,474 that is ~$32 of risk per trade — small enough that 20 consecutive losses cost ~10% of the account. |

## 3. The rules

**Universe:** liquid equities and ETFs, defined in control-plane configuration.
The strategy does not choose its own universe — that is a human decision, and a
strategy that picks its own instruments can escape a limit by picking a new one.

**Entry** — every condition must hold on the evaluation bar:

1. `close > SMA(200)` — long-term uptrend
2. `SMA(50) > SMA(200)` — trend alignment
3. `close > max(close, last 20 bars excluding today)` — 20-bar closing-high breakout
4. `0.01 ≤ ATR(14)/close ≤ 0.06` — volatility band
5. No open position in the symbol
6. ≥ 200 bars of history available

**Order:** BUY, LIMIT at the signal close, DAY.

**Stop:** `entry − 2.0 × ATR(14)`
**Target:** `entry + 3.0 × ATR(14)` → reward:risk = **1.5**

**Size:** `floor(0.005 × allocated_equity ÷ (entry − stop))`, whole shares.
Fewer than 1 share ⇒ no trade.

Anything the strategy cannot evaluate produces a `NoTrade` with a reason code —
`INSUFFICIENT_DATA`, `FILTERED_BY_REGIME` or `NO_SETUP` — never a silent skip.

### Parameters

| Parameter | Value | Why this value |
|---|---|---|
| `trend_period` | 200 | Conventional long-term filter |
| `fast_trend_period` | 50 | Conventional intermediate filter |
| `breakout_period` | 20 | ~1 trading month |
| `atr_period` | 14 | Wilder's default |
| `stop_atr_multiple` | 2.0 | Wide enough to clear normal noise |
| `target_atr_multiple` | 3.0 | Gives 1.5 R:R, matching the risk engine's floor |
| `min/max_atr_percent` | 0.01 / 0.06 | Excludes dead and berserk instruments |
| `risk_fraction` | 0.005 | 0.5% per trade |

**None of these are optimised, deliberately.** There are 141 orders of history
here and no strategy-relevant price series. Tuning parameters against that would
be fitting noise and producing a backtest that looks good and means nothing. They
are textbook defaults; the walk-forward stage is where they earn or lose their
place.

## 4. What this does *not* do

- **No exits beyond the stop and target.** No trailing stop, no time stop, no
  partial profit-taking. Position lifecycle management is not built (open
  question Q-7) — until it is, this strategy can open a position but the system
  cannot manage it beyond the bracket.
- **No shorts, no options, no crypto.** All three appear in the loss record.
- **No universe selection, no ranking.** If two symbols signal at once, the
  execution engine's single-position gate decides, not the strategy.
- **No claim of profitability.** See §6.

## 5. Honest assessment of expectancy

At 1.5 R:R the break-even win rate is **40%**. Published results for 20-day
breakout systems with trend filters cluster in the 35–45% range depending on
universe, era and cost assumptions — which straddles break-even. This strategy
is *plausibly* around break-even before costs and may well be **negative after
them**.

I am telling you this before the backtest rather than after, because the useful
version of this claim is the one made in advance. What would change my mind is
a walk-forward result over ≥10 years and ≥200 trades that stays positive after
realistic slippage and commission. Anything less is a story.

**What it is unambiguously better at than the recorded history** is loss control:
a bounded loss on every trade, a payoff ratio above 1, and position sizing that
makes a 20-trade losing streak survivable. Even if its edge is zero, it fails
slowly and legibly instead of quickly and invisibly.

## 6. Two things that block running this for real

**B-5 — The funded account is not agent-accessible.** `agentic_allowed` is
`false` on the $6,474 margin account and `true` only on the $157.94 cash
account. At $157.94, `risk_fraction × equity` buys less than one share of
anything this strategy would trade — it correctly returns "account too small"
and never trades. Either the funded account gets agent access, or automation
runs against a paper broker.

**PDT.** Below $25,000, the margin account is capped at 3 day trades per rolling
5 business days. This strategy holds overnight so it does not hit that, but any
*future* strategy that needs intraday round trips cannot run in this account at
all. Worth knowing before designing v2.

## 7. Path to promotion

```
DEVELOPMENT  ← it is here
  ↓  backtest over ≥10y of daily bars, ≥200 trades, with slippage + commission
BACKTEST
  ↓  hold out the most recent 3 years, untouched during development
OUT_OF_SAMPLE
  ↓  rolling walk-forward; parameters re-fit per window, never globally
WALK_FORWARD
  ↓  SHADOW mode — full pipeline, every gate, nothing transmitted
SHADOW
  ↓  ← FIRST HUMAN APPROVAL REQUIRED
PAPER
  ↓  ← SECOND HUMAN APPROVAL REQUIRED
LIMITED_LIVE
```

Everything up to SHADOW is analysis against recorded data and can run unattended.
The two approvals are yours and cannot be automated (ADR-005).

**Kill criteria — decide these before the backtest, not after:**
walk-forward expectancy ≤ 0 after costs · max drawdown > 20% · fewer than 100
trades in 10 years (too rare to evaluate) · win rate below 30% *and* payoff
below 1.5.
