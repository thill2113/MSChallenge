# Strategy v1 — `trend_breakout@1.0.0`

**Status:** BACKTEST. **The edge is real but small, and the portfolio limit as
configured makes it worthless.** See §8 for the backtest, §11 for the
robustness and position-limit work that followed.

Short version: on a universe I picked with hindsight it looks excellent
(+0.30 R/trade, t = 5.6). On a control universe of large caps that
*underperformed* over the same period it drops to +0.058 R with **t = 0.83 —
statistically indistinguishable from zero**. Most of the headline was my own
selection bias. The truth is somewhere between, and the strategy is not yet
worth real money.

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


---

## 8. Backtest results (2026-08-09)

**Data:** Robinhood daily bars, split-adjusted, 2015-01-02 → 2026-08-07
(2,916 bars/symbol). **Entry:** limit at the signal close, exactly as the
strategy specifies. **Costs:** 0.02% slippage per side. **Intrabar ambiguity
always resolves to the stop.** Gaps fill at the open, not the level.

| Universe | Trades | Win rate | Payoff | **Expectancy** | **t-stat** | Total | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| 10 megacaps/ETFs *(cherry-picked)* | 592 | 50.0% | 1.62 | **+0.301 R** | **5.63** | +178 R | −17.0 R |
| 9 large-cap laggards *(control)* | 360 | 40.3% | 1.63 | **+0.058 R** | **0.83** | +21 R | −19.5 R |
| Combined, 19 symbols | 952 | 46.3% | 1.62 | **+0.209 R** | **4.91** | +199 R | −25.6 R |

Break-even win rate is 38.2%. Both universes clear it; the control clears it by
2.2 points, which is noise.

**Time splits** (cherry-picked universe):

| Period | Trades | Win rate | Expectancy |
|---|---:|---:|---:|
| 2015–2020 | 288 | 52.8% | +0.381 R |
| 2021–2026 *(holdout)* | 307 | 47.2% | +0.222 R |
| 2022 bear only | **10** | 10.0% | −0.729 R |

### What these say

**The regime filter works.** In 2022 the strategy took **10 trades all year**
instead of ~55 — the 200-bar filter kept it out of the market almost entirely.
It still lost 7.3 R, but at 0.5% risk that is −3.7% of the account in the worst
year of the sample. That is the behaviour the filter is for.

**The edge degrades but survives out of sample.** +0.38 R early, +0.22 R late.
Degradation of that size across a 6/5-year split is normal and not alarming.

**Selection bias was doing most of the work.** This is the finding that matters.
The gap between +0.301 and +0.058 is the difference between the symbols I chose
and the symbols I chose *as a control*. Neither is unbiased — I picked the
laggards with hindsight too, which biases that number *down*. The combined
19-symbol figure (+0.209 R) is the least-bad estimate I have, and it is still
not survivorship-free.

**Consecutive losses will trip the kill switch.** Observed max is 13–17. The
shadow limits set `max_consecutive_losses: 6`. As configured, this strategy
**will** halt itself and need a human to clear it, probably more than once a
year. Either the limit rises to ~15 or that is accepted as intended friction —
that is decision D-1, and it is now a concrete question rather than an abstract
one.

## 9. What the backtest does *not* cover

- **Survivorship.** Both universes contain only companies that still exist. No
  delistings, no bankruptcies, no acquisitions. A proper test needs point-in-time
  index constituents, which I do not have. This biases results **up**, and it is
  the largest remaining unknown.
- **One position per symbol, not one position total.** The backtest allows
  concurrent positions across 19 symbols. The shadow limits allow **one open
  position at a time**. Real deployment would take a small fraction of these 952
  trades — whichever fired first — so the realised sequence, and its drawdown,
  will differ from anything above.
- **No walk-forward.** Parameters were fixed before the first run and never
  tuned, so there is no overfitting *to this data* — but there is also no
  evidence they would have been chosen without hindsight about what works.
- **11.6 years, one bull market with two corrections.** 2022 is the only real
  bear in the sample and it produced 10 trades.
- **Liquidity and spread are assumed benign.** True for these 19 names, not for
  a wider universe.

## 10. Verdict

Against the kill criteria in §7: expectancy is positive after costs in every
cut except the 2022 bear ✓; max drawdown 25.6 R ≈ 12.8% of account at 0.5% risk,
inside the 20% limit ✓; 952 trades over 11 years, well past the 100 floor ✓;
win rate 46.3% with payoff 1.62 ✓.

**It passes, but the control universe says the honest expectancy is somewhere
between +0.06 and +0.21 R, and the low end is not distinguishable from zero.**

That is enough to justify SHADOW. It is not enough to justify money. The next
evidence that would actually move this is a survivorship-free universe — every
S&P constituent as of each date, including the ones that no longer exist.


---

## 11. Robustness and the position limit (2026-08-09, second pass)

Two tests, chosen because they were the ones most likely to *kill* the strategy.
A classic walk-forward was skipped deliberately: the parameters were never
tuned, so re-fitting them per window would have introduced overfitting where
there currently is none.

### 11.1 The position limit is the binding constraint

The §8 backtest allowed one position per *symbol* — up to 19 at once. The risk
limits allow one position across the **whole book**. That is not a detail; it
decides whether the strategy is worth running.

| Max concurrent positions | Trades (11.6y) | Expectancy | t-stat | Total |
|---:|---:|---:|---:|---:|
| **1** *(as configured)* | 181 | +0.108 R | **1.13** | +19.5 R |
| 2 | 308 | +0.127 R | 1.69 | +39.0 R |
| 3 | 440 | +0.180 R | **2.89** | +79.3 R |
| 5 | 665 | +0.232 R | **4.58** | +154.5 R |
| 8 | 879 | +0.211 R | 4.79 | +185.6 R |
| unlimited | 949 | +0.200 R | 4.69 | +190.0 R |

**At one position the edge is not distinguishable from zero** — 181 trades in
11.6 years, ~16 a year, +19.5 R total. At 0.5% risk per trade that is about
**0.84% a year**, before tax. The machinery would work; the strategy would not
be worth the electricity.

Significance appears at **3** positions and the benefit plateaus around **5**.
Beyond 5 the extra trades are marginal ones that dilute rather than add.

### 11.2 Parameters are a plateau, not a spike

Every variant tested at 5 concurrent positions, one parameter moved at a time:

| Variant | Trades | Win rate | Expectancy | t-stat |
|---|---:|---:|---:|---:|
| **base** — stop 2.0, target 3.0, breakout 20, trend 200 | 665 | 37.9% | +0.232 R | 4.58 |
| stop 1.5 | 759 | 31.4% | +0.269 R | 4.76 |
| stop 2.5 | 606 | 43.9% | +0.141 R | 2.99 |
| stop 3.0 / target 4.5 | 390 | 39.0% | +0.170 R | 2.60 |
| target 2.5 (R:R 1.25) | 750 | 42.1% | +0.147 R | 3.41 |
| target 4.0 (R:R 2.0) | 513 | 32.2% | +0.247 R | 3.69 |
| target 6.0 (R:R 3.0) | 375 | 24.1% | **+0.415 R** | 4.12 |
| breakout 10 | 684 | 38.7% | +0.149 R | 2.99 |
| breakout 40 | 611 | 37.9% | +0.228 R | 4.32 |
| breakout 60 | 582 | 37.7% | +0.254 R | 4.66 |
| trend 100 | 662 | 38.4% | +0.242 R | 4.68 |
| trend 150 | 649 | 38.7% | +0.224 R | 4.36 |
| trend 250 | 649 | 38.1% | +0.217 R | 4.23 |

**All thirteen are positive**, spanning +0.14 to +0.41 R with t from 2.6 to 4.8.
That is the signature of a real effect rather than a curve fit: if the base
parameters had been tuned to this data, the neighbours would fall off a cliff.
They don't.

I am deliberately **not** adopting the best cell. Picking `target 6.0` because
it scored highest here is exactly the overfitting this test was designed to
detect. It is a hypothesis for v2, not a change to v1.

### 11.3 Three conflicts with the shadow limits

The backtest turned three of my placeholder limits from guesses into known
problems:

| Limit | Placeholder | Observed | Consequence |
|---|---|---|---|
| `max_open_positions` | 1 | needs 3–5 | **Edge disappears.** The single largest issue. |
| `max_open_portfolio_risk_fraction` | 0.01 | 5 × 0.5% = 0.025 | Caps the book at 2 positions regardless of the above. |
| `max_consecutive_losses` | 6 | 10–17 observed | Kill switch trips, needs a human, more than once a year. |

`data/fixtures/risk_limits.shadow.json` has been updated to values consistent
with the strategy, still clearly labelled shadow-only. **Production values
remain decision D-1 and remain yours.**

### 11.4 Revised verdict

The edge is real, robust to parameter choice, and survives out of sample — and
it is **small**. At 5 concurrent positions and 0.5% risk, +154 R over 11.6 years
is roughly **6.7% a year on risk deployed**, before tax, ignoring the
survivorship problem in §9 which biases even that upward.

That is worth running in SHADOW to validate the machinery end to end. It is not,
on this evidence, worth funding — and I would not change that view without a
survivorship-free universe.
