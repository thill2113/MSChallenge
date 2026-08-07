# System Audit and Architecture Redesign — 2026-08-06

**Real-money trading: DISABLED.** Enforced in code (`risk_controls.Mode.PAPER` default).
Account flat, equity $4,123.88.

Companion artefacts:
- `TRADE_AUDIT_2026-08-06.csv` — per-trade audit, all 26 closes, machine-readable
- `STRATEGY_REVIEW_2026-08-06.md` — prior review (superseded in part by this document)
- `risk_controls.py` / `test_risk_controls.py` / `replay_controls.py` — P0 controls, 20 tests

---

# PART I — THE ANSWER TO YOUR FIRST QUESTION

> *"Does our trading system currently have a measurable edge, and can we prove it?"*

## **No. And the data says so unambiguously.**

Three independent tests, all pointing the same way:

**1. Every confidence interval crosses zero.** Not just the headline — *every segment*:

| Segment | n | Mean | 95% CI | Verdict |
|---|---|---|---|---|
| All trades | 26 | +$6.98 | [−$8.95, +$23.11] | crosses zero |
| Calls | 19 | −$0.79 | [−$17.95, +$16.04] | crosses zero |
| Puts | 7 | +$28.07 | [−$2.34, +$61.90] | crosses zero |
| Regime-aligned | 17 | +$15.24 | [−$5.09, +$35.71] | crosses zero |
| Holds ≥6h | 10 | +$25.25 | [−$8.23, +$57.35] | crosses zero |
| Holds ≥24h | 5 | +$33.35 | [−$15.63, +$75.53] | crosses zero |

There is no subset of this strategy with statistically demonstrated positive expectancy.

**2. The entire profit is two trades.**

| | |
|---|---|
| Total realized | **+$181.44** |
| Top 1 winner (IREN put, 07-28) | +$106.90 = **58.9%** of total |
| Top 2 winners | +$189.84 = **104.6%** of total |
| **Campaign without the top 2** | **−$8.40** |
| Campaign without the top 3 | −$66.32 |

Remove two trades out of twenty-six and this system is **net negative**. That is the
definition of outlier-dependent.

**3. Risk-adjusted performance is weak.** Per-trade mean/SD = **0.165**. Sortino-like
(mean / downside SD) = 0.275. For scale, a per-trade ratio below ~0.2 is weak. This is
descriptive only — there is no risk-free rate and the sample is too short to annualize.

### What actually produced the profits

Ranked by explanatory power:

1. **A few outlier winners** — decisive. Two trades = 104.6% of P&L.
2. **Longer holding periods** — real and the strongest structural signal (below).
3. **Favorable regime alignment** — real but confounded with holding period.
4. **Insufficient sample size** — n=26 cannot resolve a ~$7/trade edge against $42 SD.
5. **Excessive risk** — contributed to *losses* via the v1.1 stop widening, not to gains.
6. **Luck** — cannot be excluded, and is the null hypothesis the data fails to reject.

---

## Your holding-period observation — tested

**You were right, and it is the single strongest pattern in the data.**

| Bucket | n | Net | Win rate | Profit factor | Expectancy |
|---|---|---|---|---|---|
| **< 1h** | 10 | **−$61.68** | 30.0% | **0.34** | −$6.17 |
| 1–6h | 6 | −$9.36 | 66.7% | 0.91 | −$1.56 |
| 6–24h | 5 | +$85.74 | 60.0% | 2.26 | +$17.15 |
| **≥ 24h** | 5 | **+$166.74** | 60.0% | **4.40** | +$33.35 |

Monotonic across all four buckets. Permutation test (≥6h vs <6h): **p = 0.0398**.

**Is it statistically meaningful? Partially — and I have to qualify it.**

- The direction is consistent and monotonic, which is more convincing than the p-value alone.
- But p=0.0398 is **one of ~8 tests I ran**. At α=0.05 across 8 tests, family-wise
  false-positive probability is ~34%. A Bonferroni-corrected threshold would be ~0.006.
  **This result does not survive that correction.**
- The ≥24h bucket is n=5 and contains both of the outlier winners. Holding period and
  "was an outlier" are confounded in this sample.

**Verdict: suggestive, directionally credible, not established.** It is strong enough to
*design around* (as a filter that costs little if wrong) but not strong enough to *bet on*.

---

## Two findings that were not in the prior review

### Direction: the call side is the entire drag

| Direction | n | Net | Win rate | Profit factor | Expectancy |
|---|---|---|---|---|---|
| **Calls (long)** | 19 | **−$15.08** | 47.4% | **0.95** | −$0.79 |
| **Puts (short)** | 7 | **+$196.52** | 57.1% | **6.43** | +$28.07 |

Nineteen call trades produced a net loss. Seven put trades produced more than the entire
campaign P&L. Permutation test p=0.066 — not significant, and n=7 is far too small to
conclude "trade only puts." But it does say the call-side setup has **no demonstrated edge
whatsoever** across a reasonable sample.

### Regime: reconstructed for the first time

The prior review reported regime as "not computable" because it was never logged. I fetched
actual QQQ daily history for the full campaign and classified every trade date deterministically
(≥+0.5% = TREND_UP, ≤−0.5% = TREND_DOWN, else CHOP).

| Regime | n | Net | Win rate | Profit factor |
|---|---|---|---|---|
| TREND_UP | 11 | +$117.46 | 72.7% | 1.93 |
| TREND_DOWN | 9 | +$100.32 | 33.3% | 2.29 |
| **CHOP** | 6 | **−$36.34** | 33.3% | **0.67** |

Crossed with direction, the result is sharper:

| Combination | n | Net | Profit factor |
|---|---|---|---|
| **Regime-ALIGNED** (up+call, down+put) | 17 | **+$259.06** | **2.60** |
| **FIGHTING the regime** (down+call, up+put) | 3 | **−$41.28** | **0.00** — zero wins |
| CHOP (no regime) | 6 | −$36.34 | 0.67 |
| CHOP + call specifically | 5 | **−$91.26** | **0.17** |

**Fighting the regime went 0-for-3. Buying calls in chop went 1-for-5 and lost $91.**

*Caveat, stated plainly:* this uses QQQ daily close-to-close as a regime proxy. The directive
used an **intraday** reading, which can differ. The classification is a defensible
reconstruction, not what the system actually saw at decision time.

---

# PART II — PER-TRADE AUDIT

Full machine-readable table: **`TRADE_AUDIT_2026-08-06.csv`** (26 rows, 22 columns).

## Fields that could NOT be reconstructed — because they never existed

You asked for target, confidence level, and several others. I will not fabricate them.

| Field | Status |
|---|---|
| **Target** | **NEVER SET on any of 26 trades.** No profit target was defined at entry, ever. Consequence: **risk/reward was never computable pre-trade**, and "minimum R:R" was never a gate. |
| **Confidence at entry** | **NEVER ASSIGNED.** Confidence appeared as prose adjectives ("moderate not high") on roughly 3 trades. **Performance-by-confidence-score is NOT COMPUTABLE** and I have not estimated it. |
| **Stop (structured)** | Present in prose notes for most trades, never as a structured column. Not reliably machine-readable. |
| **Regime at entry** | Not logged. Reconstructed retroactively (see caveat above). |
| **Time of day** | Computable. Reported below. |

This absence is itself a primary finding: **the system could not have been evaluated even
in principle**, because the fields required to evaluate it were not captured.

## Which rule would have prevented each loss

Across the 13 losing trades:

| Rule | Losses it would have prevented |
|---|---|
| Minimum 6h intended hold | **7** |
| Entry-quality filter (depth / confirmation) | **7** |
| Chop hard-block | **4** |
| Regime-alignment block | **3** |
| 15% stop (reduces, not prevents) | 3 |
| *No rule identified — trade was sound* | **1** |

Twelve of thirteen losses map to a proposed deterministic rule. That is encouraging for the
redesign — but note it is **in-sample rule fitting**, which is exactly the trap this whole
exercise exists to escape. These rules must be validated out-of-sample before they mean anything.

## Failure-mode inventory (your explicit list)

| Failure mode | Instances | Evidence |
|---|---|---|
| **Inferred data not verified** | 1 material | APLD/AMD "decoupling" thesis built on a **single day** of relative strength. Failed the next day. |
| **Hallucinated information** | **0 found** | All prices, greeks, spreads and depth in the ledger trace to actual tool calls. |
| **Stale information** | 2 historical | Stale earnings file admitted HIMS (07-31); name-omitting filter admitted IONQ. Both logged and policy-fixed at the time. |
| **Ignored conflicting signals** | **1 severe** | IREN 08-06: four caveats logged (chop, decelerating momentum, third crypto-beta attempt, IV 113%) and the trade was recommended anyway. −$83. |
| **Changed reasoning after the trade** | 0 clear | Caveats were consistently logged *before* outcomes. |
| **Moved goalposts** | **1 structural** | The v1.1 stop widening reinterpreted "losers never traded above entry" as a stop problem rather than an entry problem, then used the wider stop to justify larger positions. |
| **Overweighted one indicator** | Systemic | Spread% was treated as the liquidity gate while **bid depth was not measured at all** until 08-05. RIVN was admitted at 7.91% spread — clearing the gate by 0.09pp — with depth collapsing 93→18 lots. |
| **Ignored market regime** | **1 explicit + 3 structural** | IREN 08-06 explicitly. Plus 3 trades that fought the regime (0-for-3). |
| **Entered without sufficient confirmation** | 8 | The MFE=0 trades — 31% of all trades never traded above entry once. |
| **Not reproducible deterministically** | **Most of them** | See below. |

### On reproducibility — the most important item

**A majority of these 26 entries could not be reproduced deterministically**, because the
decision inputs included:
- a subjective read of whether momentum was "decelerating"
- a judgement about whether a caveat was disqualifying or merely worth noting
- an intraday regime reading that was never recorded
- candidate selection from a watchlist that changed by hand between sessions

Two trades on identical market data could have received opposite recommendations depending on
how the caveats were weighed. **That is the core architectural defect, and it is what the
redesign must eliminate.**

---

# PART III — QUANTIFIED PERFORMANCE

All figures **calculated** from `trades.csv` unless marked. Nothing estimated.

## Headline

| Metric | Value |
|---|---|
| Total realized P&L | +$181.44 |
| Trades | 26 |
| Win rate / loss rate | 50.0% / 50.0% (Wilson 95% CI: 32.1%–67.9%) |
| Average win / loss | +$38.09 / −$24.14 |
| Win/loss ratio | 1.58 |
| Profit factor | 1.58 |
| Expectancy | +$6.98 (95% CI −$8.95 to +$23.11) |
| Max drawdown | −$193.00 (current trough) |
| Max consecutive losses | 5 |
| Per-trade mean/SD | 0.165 |

**On the 5-loss run:** P(at least one 5-loss run in 26 trades at a 50% win rate) ≈ **50%**.
The observed streak is *expected noise*, not evidence of breakage. It should not be used to
justify a change — but it does set the floor for any circuit-breaker threshold.

## By stop-loss era

| | v1.0 (15%) | v1.1 (35%) |
|---|---|---|
| n | 19 | 7 |
| Net | +$92.60 | +$88.84 |
| Average loss | −$12.08 | **−$64.33** |
| Win/loss ratio | 1.96 | **1.10** |
| Profit factor | **1.77** | **1.46** |

Average loss grew **5.3×**; average win grew 3.0×. Profit factor fell. The higher v1.1
expectancy (+$12.69) is a 4-trade winning streak followed by 3 straight losses.

## By time of day (entry hour, ET)

| Hour | n | Net | Profit factor |
|---|---|---|---|
| 09:00 | 1 | +$18.92 | — (n=1) |
| **10:00** | 10 | **+$139.46** | **2.93** |
| 11:00 | 4 | +$39.82 | 1.32 |
| 12:00 | 2 | −$24.16 | 0.00 |
| **13:00** | 5 | **−$36.36** | **0.47** |
| 14:00 | 4 | +$43.76 | 2.81 |

Suggestive (midday entries underperform) but **n per bucket is 1–10. I would not build a
rule on this.** Reported because you asked; flagged as insufficient.

## By setup

**NOT COMPUTABLE as requested.** The `trigger_type` column mixes entry triggers with exit
reasons and was populated inconsistently. There was never a `strategy_id`. This is a data-model
failure, and Part VI fixes it.

---

# PART IV — TARGET ARCHITECTURE

```
Market Data → Deterministic Strategy Engine → Deterministic Risk Engine
    → Claude Analysis/Critique Layer → Execution Validation → Robinhood MCP
```

## Authority model — the governing principle

| Component | May do | May NEVER do |
|---|---|---|
| **Deterministic engines** | Detect setups, size positions, set stops/targets, enforce limits, execute | — |
| **Claude layer** | **VETO** a trade; add contextual risk; generate counter-thesis; flag anomalies; review performance; propose hypotheses | Increase risk, widen a stop, increase size, bypass validation, place a trade, override any gate |

**Claude's output is a boolean veto plus structured commentary. It is monotonic: it can only
ever remove trades from the candidate set, never add or enlarge them.** This is the
architectural expression of the finding that the −$83 loss came from an agent overriding a
rule it had correctly read.

## Component responsibilities

**Deterministic (20 items, per your list)** — market data ingestion; indicator calculation;
regime classification; setup detection; entry qualification; stop calculation; target
calculation; position sizing; R:R calculation; max portfolio exposure; daily loss limit;
weekly loss limit; consecutive-loss protection; correlation controls; trade cooldowns; order
validation; execution; lifecycle management; logging; kill switches.

**Claude** — market context analysis; contradictory-evidence detection; news/unusual-context
evaluation; challenging the deterministic strategy; counter-thesis generation; failure-mode
explanation; detecting situations the rules cannot model; opportunity summarization;
performance review; offline hypothesis generation; **recommending NO TRADE under uncertainty**.

**Rule for changes:** any Claude proposal becomes a *testable strategy rule*, passes offline
validation, and is version-controlled before it can influence real money. No exceptions,
including "obviously correct" ones — the v1.1 stop change was "obviously correct" and cost $104.

---

# PART V — TRADE PIPELINE

Every stage is a hard gate. **Any failure → NO TRADE.** No stage may be skipped, and there is
no "override" path at any stage.

| # | Stage | Owner | Fails when |
|---|---|---|---|
| 1 | Market data | Det | Feed stale, gapped, or unreachable |
| 2 | **Data quality validation** | Det | Quote age > threshold; missing greeks; interpolated bar |
| 3 | **Regime classification** | Det | Regime = CHOP → **STOP** |
| 4 | Strategy setup detection | Det | No registered setup matches |
| 5 | **Multi-timeframe confirmation** | Det | Intraday and daily disagree on direction |
| 6 | **Volume / liquidity** | Det | Bid depth < floor; OI < floor; spread > max |
| 7 | **Volatility validation** | Det | IV outside band; ATR outside band |
| 8 | Entry qualification | Det | Delta/DTE/cost outside spec |
| 9 | **Structural invalidation** | Det | No definable invalidation level → **STOP** |
| 10 | **Minimum R:R** | Det | R:R < 2.0 |
| 11 | Position sizing | Det | Computed size < 1 contract |
| 12 | Portfolio risk check | Det | Exposure/correlation/drawdown/streak limits |
| 13 | **Claude counter-thesis / red team** | AI | **Claude vetoes** |
| 14 | Final deterministic validation | Det | Any input changed since stage 1 (re-quote) |
| 15 | TRADE or NO TRADE | Det | — |
| 16 | Broker order review | MCP | Broker alert |
| 17 | Execution | Det | Fill outside tolerance |
| 18 | Monitoring | Det | — |
| 19 | Trade journal | Det | — |
| 20 | Post-trade analysis | AI | — |

**Design commitments:**
- Stages 9 and 10 are new and directly address that **no trade in 26 had a target or a
  defined invalidation level**.
- Stage 14 re-runs every check against fresh quotes. This already exists informally and blocked
  6 of 9 recommendations — it is the single most reliable control the system has.
- The system does **not** scan for reasons to enter. `NO_TRADE` is the default return value;
  a trade requires affirmative passage of all 20 stages.

---

# PART VI — STRATEGY SPECIFICATION

## The subjective-to-measurable translation

| Was (subjective) | Becomes (measurable) |
|---|---|
| "looks bullish" | `close > EMA(20) AND EMA(20) > EMA(50) AND ADX(14) > 20` |
| "momentum seems strong" | `pct_change_1d >= 3.0 AND RVOL >= 1.5 AND close within 1% of session high` |
| "probably reversing" | **NOT DETERMINISTIC** → AI-assisted contextual input, veto-only |
| "appears oversold" | `RSI(14) < 30 AND close < lower_bollinger(20,2)` |
| "decelerating momentum" | `pct_change(last_30min) < 0.5 * pct_change(prior_30min)` |
| "chop" | `abs(QQQ_pct_change) < 0.5` — already implemented in `risk_controls.is_chop()` |
| "clean setup" | **NOT DETERMINISTIC** → forbidden as a decision input entirely |

Anything that cannot be reduced to a measurable condition is labelled an **AI-assisted
contextual input** and may only ever contribute to a **veto**.

## Formal spec — STRAT-001 (the only setup with supporting evidence)

```yaml
strategy_id: STRAT-001-MOMENTUM-CONTINUATION
version: 1.0.0
status: UNVALIDATED          # no live use until Stage 3 passes

applicable_instruments:
  type: equity_options, long_only, single_leg
  underlying_universe: registered watchlist, version-controlled
  min_underlying_price: 5.00
  requires_weekly_chain: true

required_market_regime:
  - TREND_UP for calls
  - TREND_DOWN for puts
  - CHOP: FORBIDDEN            # evidence: 6 trades, PF 0.67, -$36.34
  - fighting regime: FORBIDDEN # evidence: 3 trades, 0 wins, -$41.28

required_timeframe:
  primary: 5-minute
  confirmation: daily          # both must agree on direction

setup_definition:
  underlying_pct_change_1d: ">= +3.0 (calls) | <= -3.0 (puts)"
  position_in_range: "within 1.0% of session high (calls) / low (puts)"
  rvol: ">= 1.5 OR underlying in registered watchlist"   # see note

entry_conditions:
  delta: 0.35 - 0.55
  dte: 7 - 14
  spread_pct: "<= 8.0"
  bid_depth_lots: ">= 50"           # ABSOLUTE floor - new
  bid_depth_change_pct: ">= -50"    # relative floor - existing
  open_interest: ">= 250"
  leverage: "<= 14x"
  implied_underlying_stop: ">= 2.5%"

required_confirmations:
  - multi_timeframe_agreement
  - earnings_clear_full_contract_life
  - regime_alignment

disqualifying_conditions:
  - regime == CHOP
  - direction fights regime
  - earnings inside contract life
  - gap_day (underlying gapped >5% at today's open)
  - intended_hold < 6h          # evidence: <1h bucket PF 0.34
  - correlated cluster already at 2 positions
  - any conflicting-signal count >= 2

stop_methodology:
  primary: "min(15% of premium, 1.5 * ATR(14) of underlying translated via delta)"
  # 15% ceiling is evidence-based: no winner in 26 ever drew past -14.70%
  never_widen_after_entry: true

target_methodology:
  primary: "2.0 * risk"          # NEW - no trade in 26 ever had a target
  secondary: "structural level (prior swing high/low)"
  take_partial: "50% at +1.0R when qty > 1"

minimum_rr: 2.0
position_sizing: "floor(risk_budget / (entry * stop_width_pct * 100))"
max_risk_per_trade_pct: 1.0      # halved from 2.5 during validation
max_simultaneous_exposure_pct: 4.0
max_correlated_exposure: 2

time_based_exit: "close at 15:45 ET on expiry-week Friday; hard exit at DTE=1"
structural_exit: "underlying closes beyond invalidation level"
profit_taking: "50% at +1R (qty>1); trail remainder at 15% from HWM"
trailing_logic: "ratchet on new HWM only; never loosen"
cooldown: "no re-entry same symbol same direction for 24h after a stop-out"

invalid_market_conditions:
  - first 5 minutes of session
  - last 45 minutes of session (existing 15:15 ET cutoff)
  - QQQ absolute change > 3% (crisis tape, untested)
  - underlying halted or in auction

expected_holding_period: "6-48h"   # evidence: >=24h bucket PF 4.40
```

**Note on RVOL:** a plain RVOL ≥ 1.5 filter would have excluded IREN, CIFR and RIOT — names
that had already been heavily traded, so their 30-day average had risen to meet current volume.
The `OR in registered watchlist` branch exists for exactly that case and must be validated
separately, not assumed.

**STRAT-002 (put/breakdown) is deliberately NOT specified.** The put side has n=7. Writing a
spec for it now would be fitting a strategy to seven observations.

---

# PART VII — STOP MANAGEMENT REDESIGN

| Option | Verdict | Quantitative basis |
|---|---|---|
| **A. Original 15%** | **ADOPT as the ceiling** | Replayed: v1.1 era +$88.84 → **+$192.84**, delta **+$104.00**. All 4 winners unaffected; all 3 losers cut earlier. No winner in 26 ever drew past −14.70%. |
| **B. Current 35%** | **REJECT** | Rescued zero winners. Average loss 5.3×. PF 1.77 → 1.46. |
| **C. ATR-based** | **EVALUATE — cannot decide yet** | Requires underlying ATR at entry. **Never captured.** Recommending it now would repeat the v1.1 error of changing stops without data. |
| **D. Volatility-adjusted** | **EVALUATE — cannot decide yet** | IV recorded on only a handful of trades. Same reasoning. |
| **E. Market-structure** | **EVALUATE — cannot decide yet** | Requires swing levels at entry. Never captured. |
| **F. Time-based invalidation** | **ADOPT — evidence-backed** | <1h bucket PF 0.34; ≥24h PF 4.40. A minimum-hold *intent* filter plus a hard time exit is supported. |
| **G. Combination** | **ADOPT (A + F), then re-evaluate C/D/E** | Use `min(15%, ATR-derived)` once ATR is captured, so ATR can only ever *tighten*. |

## Stop and size are one decision

Your constraint is adopted as a hard invariant:

```
risk_dollars = entry_premium × stop_width_pct × 100 × qty  ≤  risk_budget
qty = floor(risk_budget / (entry_premium × stop_width_pct × 100))
```

**A wider stop mechanically reduces size.** The v1.1 change violated this: the stop went
15%→35% while position size was *also* raised (risk cap 2.5% of a larger equity base),
so dollars-at-risk more than doubled. That is the "moved goalposts" instance from Part II.

Encoded as a test that must fail loudly if anyone reintroduces it.

---

# PART VIII — HARD RISK LIMITS (paper-testing values)

Deliberately conservative. **Risk controls override strategy signals unconditionally.**

| Limit | Value | Basis |
|---|---|---|
| Max risk per trade | **1.0% of equity** | Halved from 2.5% during validation |
| Max daily loss | **2.0%** | Old 6% permitted ~$250/day |
| Max weekly loss | **4.0%** | −$193 over two days = 4.5%, which you said must never happen |
| Max rolling 5-session drawdown | **2.5%** | Implemented. See calibration note below |
| Max portfolio exposure | **4.0%** of equity at risk | 4 concurrent max-risk positions |
| Max correlated exposure | **2 positions per cluster** | Existing rule, retained |
| Max consecutive losses | **4 → halt** (warn at 3) | P(5-run at 50% WR over 26) ≈ 50%, so 5 is noise; 4 is the defensible action point |
| Max drawdown before strategy disable | **6%** | Auto-disables STRAT-001, requires human re-enable |
| System kill switch | **8%**, or any risk-control bypass, or 2 consecutive data-quality failures | Full stop, human-only restart |

**Calibration honesty on the 2.5% drawdown limit:** it is the *loosest* threshold that would
have blocked the 08-06 trade (drawdown entering that session was 2.61%). 3.0% would not have.
It is anchored on **one event**, not fitted to a distribution, and is labelled as such in the
code. Re-derive it from the paper sample.

---

# PART IX — VALIDATION STAGES

## Stage 1 — Historical backtest

Requirements: realistic spreads (use *recorded* spreads from `trades.csv`, which are real),
slippage, $0.04/contract fees (actual observed), option liquidity floors, **look-ahead bias
protection**, train/test separation, walk-forward validation.

**The hard problem, stated plainly:** historical options chain data with greeks and depth is
expensive and not available from the Robinhood MCP retroactively. Underlying OHLCV is
available; option-level history is not, beyond what is already in the ledger.

**Consequence: a fully faithful options backtest may not be achievable at reasonable cost.**
The honest fallback is to backtest the *underlying* signal (regime, momentum, hold period) on
equity data, and validate the option-selection layer in Stage 2 forward. I would rather tell
you this now than deliver a backtest built on synthesized option prices.

## Stage 2 — Shadow mode

Run the full pipeline on live data. Emit `TradeCandidate` objects. **Submit nothing.** Record
the quote at decision time, then re-poll at +5/+15/+60min and at close to measure what would
have happened. This validates option selection and execution assumptions that Stage 1 cannot.

**Duration: minimum 4 weeks and ≥30 candidates.**

## Stage 3 — Paper trading

Simulated account, real-time, full journaling, all controls live. **Minimum 8 weeks and
≥50 executed paper trades.**

---

# PART X — GO / NO-GO CRITERIA

**Defined before testing. Not modifiable because results disappoint** — if these fail, the
strategy is wrong, not the criteria.

| # | Criterion | Threshold |
|---|---|---|
| 1 | Validated trades | **≥ 50** (paper) |
| 2 | **Expectancy 95% CI lower bound** | **> 0** ← the real bar |
| 3 | Profit factor | ≥ 1.60 |
| 4 | Max drawdown | ≤ 6% |
| 5 | Max consecutive losses | ≤ 4 |
| 6 | Regime coverage | ≥ 10 trades in each of TREND_UP / TREND_DOWN / CHOP-avoided |
| 7 | Paper duration | ≥ 8 weeks |
| 8 | **Strategy deviation** | **Zero** unlogged deviations from spec |
| 9 | Critical system failures | **Zero** unresolved |
| 10 | **Risk-control bypasses** | **Zero. Any single bypass resets the clock.** |
| 11 | Outlier dependence | Removing top 2 winners leaves expectancy **> 0** |
| 12 | Short-hold book | Not net negative when reported separately |

Criterion 11 is new and non-negotiable: it is the test the current system fails most badly
(remove 2 of 26 and P&L is −$8.40).

**Sample-size derivation** (observed SD $42.29): n=31 → CI half-width $15; **n=48 → $12**;
n=69 → $10. The "50" in criterion 1 is derived, not chosen for roundness.

---

# PART XI — OPEN-SOURCE TOOLING

All metadata **verified live via GitHub API on 2026-08-06**, not recalled.

| Project | Stars | License | Last push | Class | Reasoning |
|---|---|---|---|---|---|
| **QuantConnect/Lean** | 21,101 | Apache-2.0 | 2026-08-06 | **ADOPT** | **The only mature engine with genuine options support** — chains, greeks, multi-leg, expiry. That is decisive for an options strategy; most alternatives are equity/futures-first. Permissive license, active. Cost: C# core, heavier learning curve. |
| **ranaroussi/quantstats** | 7,525 | Apache-2.0 | 2026-07-20 | **ADOPT** | Standard tearsheets — PF, expectancy, drawdown, Sortino. Replaces bespoke analysis. Days to integrate. |
| **TA-Lib/ta-lib-python** | 12,176 | BSD-2 | 2026-07-29 | **ADOPT** | Indicator ground truth. Removes hand-rolled indicator bugs. Note: needs the C library installed. |
| **open-telemetry/opentelemetry-python** | 2,575 | Apache-2.0 | 2026-08-07 | **ADOPT** | Observability for a multi-service pipeline. Lets you answer "which stage rejected this and why" after the fact. |
| **kernc/backtesting.py** | 8,758 | **AGPL-3.0** | 2026-08-05 | **ADAPT** | Simple, good docs, built-in walk-forward. **AGPL is strong copyleft** — fine privately, forces disclosure if hosted. Confirm acceptable. Equity-oriented; options need adaptation. |
| **nautechsystems/nautilus_trader** | 25,319 | LGPL-3.0 | 2026-08-06 | **ADAPT** | Excellent reference architecture for deterministic event-driven design — the model for the engine layer. Heavyweight (Rust). Study now, adopt only if this scales. |
| **dcajasn/Riskfolio-Lib** | 4,430 | BSD-3 | 2026-06-22 | **ADAPT** | Take drawdown/risk-contribution functions; ignore the portfolio optimiser (wrong problem). |
| **hmmlearn/hmmlearn** | 3,412 | BSD-3 | 2024-10-31 | **EVALUATE** | Would replace the QQQ threshold with a learned regime model. Stale-ish but mature/stable. Only after Stage 1 — a learned regime classifier on n=26 would be pure overfitting. |
| **langchain-ai/langgraph** | 39,079 | MIT | 2026-08-06 | **EVALUATE** | Orchestration for the agent layer. **But**: our agent layer is one veto call returning a structured object. LangGraph solves a problem we may not have. Adopt only if the agent layer grows past a single node. |
| **polakowo/vectorbt** | 8,600 | **NOASSERTION** | 2026-08-02 | **EVALUATE** | Fast parameter sweeps. **Non-standard license, successor is commercial.** Verify terms first. |
| **confident-ai/deepeval** | 17,451 | Apache-2.0 | 2026-08-06 | **EVALUATE** | Agent evaluation — regression-test that Claude's veto behaviour stays consistent across model versions. Genuinely relevant given the reproducibility finding. |
| **mementum/backtrader** | 22,751 | GPL-3.0 | **2024-08-19** | **REJECT** | **~2 years stale.** Popular but effectively unmaintained. GPL-3.0. Superseded. |
| **quantopian/zipline** | 20,028 | Apache-2.0 | **2024-02-13** | **REJECT** | **Quantopian is defunct.** Do not build on abandonware. |
| **freqtrade/freqtrade** | 53,026 | GPL-3.0 | 2026-08-07 | **REJECT** | Highest star count here and **wrong domain — crypto-only**. Does not do equity options. A clear case of popularity ≠ fit. |
| **AI4Finance/FinRL** | 15,939 | MIT | 2026-07-13 | **REJECT** | Deep RL for trading. We cannot validate a 26-trade rules system; adding a black-box RL layer inverts the entire goal of this redesign. |
| **hudson-and-thames/mlfinlab** | 4,901 | NOASSERTION | **2023-10-02** | **REJECT** | ~3 years stale, license unclear, went commercial. |
| **quantopian/pyfolio** | 6,389 | Apache-2.0 | **2023-12-23** | **REJECT** | Defunct. Use quantstats. |
| **HKUDS/Vibe-Trading** | 30,031 | MIT | 2026-08-06 | **REJECT** | **Created 2026-04-01 — four months old.** No track record. |
| `twopirllc/pandas-ta` | — | — | — | **UNVERIFIED** | **Did not resolve via GitHub search on 2026-08-06.** May be renamed, moved, or removed. Verify before considering. I will not classify what I could not confirm. |
| `stefan-jansen/zipline-reloaded` | — | — | — | **UNVERIFIED** | Same — did not resolve. |

---

# PART XII — SOFTWARE ARCHITECTURE

## Structured objects, not prose

```python
@dataclass(frozen=True)
class TradeCandidate:
    strategy_id: str; version: str; symbol: str; direction: Literal["call","put"]
    option_id: str; expiry: date; strike: Decimal
    entry: Decimal; stop: Decimal; target: Decimal
    quantity: int; risk_amount: Decimal; risk_reward: Decimal
    regime: Literal["TREND_UP","TREND_DOWN","CHOP"]
    confirmations: list[Confirmation]      # each with name, value, threshold, passed
    invalidations: list[Invalidation]      # structural level + condition
    market_snapshot: MarketSnapshot        # spread, depth, IV, ATR, OI, greeks
    timestamp: datetime; data_quality: DataQualityReport

@dataclass(frozen=True)
class AgentReview:
    approved: bool                         # False = VETO
    confidence: Literal["HIGH","MEDIUM","LOW"]
    contradictory_evidence: list[str]
    contextual_risks: list[str]
    veto_reason: str | None
    # NOTE: no quantity, stop, target, or risk field exists on this type.
    # Claude cannot modify execution parameters because the schema
    # gives it nowhere to put them. Enforced by the type system.
```

The `AgentReview` schema is the enforcement mechanism. It is not a policy Claude is asked to
follow — **there is structurally no field through which risk parameters could be changed.**

## Services and interfaces

| Service | Responsibility | Consumes → Produces |
|---|---|---|
| `market-data-service` | Ingestion, caching, quality validation | Broker/feed → `MarketSnapshot` |
| `regime-engine` | Deterministic regime classification | `MarketSnapshot` → `RegimeLabel` |
| `signal-engine` | Indicators, setup detection | `MarketSnapshot`+`RegimeLabel` → `Setup[]` |
| `strategy-engine` | Spec application, entry qualification | `Setup[]` → `TradeCandidate[]` |
| `risk-engine` | Sizing, stops, targets, all limits. **Final authority.** | `TradeCandidate` → `TradeCandidate` \| `Rejection` |
| `portfolio-engine` | Exposure, correlation, drawdown state | Positions → `PortfolioState` |
| `agent-analysis-service` | Claude red-team | `TradeCandidate` → `AgentReview` |
| `execution-engine` | Re-quote validation, order lifecycle | Approved candidate → `Order` |
| `broker-adapter` / `robinhood-mcp` | Broker I/O | `Order` → `Fill` |
| `backtesting-engine` | Stage 1 | Spec + history → `BacktestResult` |
| `trade-journal` | Append-only structured log | All events → journal |
| `observability` | OTel traces/metrics | All services |
| `configuration-service` | Versioned, signed strategy specs | — |

**Critical ordering:** `risk-engine` runs **both before and after** the agent. The agent sees
a fully-sized candidate and can only veto it; the post-agent pass re-validates that nothing
changed. Free-form natural language never crosses a service boundary — only typed objects.

---

# PART XIII — BOT vs AGENT vs HYBRID

Scored 1–5, 5 best.

| Dimension | Claude-driven | Deterministic bot | **Hybrid** |
|---|---|---|---|
| Consistency | 1 | 5 | **5** |
| Profitability potential | 3 | 3 | **4** |
| Hallucination risk | 1 | 5 | **4** |
| Testability | 1 | 5 | **4** |
| Reproducibility | 1 | 5 | **5** |
| Adaptability | 5 | 2 | **4** |
| Latency | 2 | 5 | **3** |
| Explainability | 4 | 4 | **5** |
| Risk control | 1 | 5 | **5** |
| Development complexity | 5 | 3 | **2** |
| Operational complexity | 4 | 3 | **2** |
| Observability | 2 | 4 | **4** |
| Failure recovery | 2 | 4 | **4** |
| **Total** | **32** | **53** | **51** |

**Recommendation: HYBRID.**

The bot scores marginally higher in raw total, but the scoring understates one thing: the
hybrid's veto layer is *free optionality on the downside*. A veto can only prevent trades. If
Claude's judgement is worthless, the hybrid degrades to the deterministic bot; if it has value,
it removes losses the rules cannot see. The bot's two-point edge is in development and
operational complexity — a real cost, but a one-time one.

**Evidence from this campaign, both directions:**
- Claude-driven failed: it read the chop rule, restated it, and traded anyway. −$83.
- Claude-as-checker succeeded: re-quote validation blocked 6 of 9 recommendations correctly.

**The agent is reliable at mechanical verification and unreliable at discretionary judgement.**
The hybrid puts it only where it demonstrably works.

---

# PART XIV — IMPLEMENTATION ROADMAP

### PHASE 0 — Halt and preserve ✅ **COMPLETE**
**Objectives:** stop live trading, preserve evidence.
**Deliverables:** trading halted in code; `risk_controls.py` + 20 tests; ledgers committed; full transcript.
**Exit criteria:** ✅ no live orders possible; ✅ ledger intact and reconciled.
**Risks:** none remaining.

### PHASE 1 — Reconstruct and audit ✅ **COMPLETE**
**Objectives:** answer whether an edge exists.
**Deliverables:** `TRADE_AUDIT_2026-08-06.csv`; this document; regime reconstruction.
**Exit criteria:** ✅ all 26 trades audited; ✅ edge question answered (**no**).
**Highest risk:** *realized* — several fields (target, confidence) never existed and cannot be recovered.

### PHASE 2 — Formalize deterministic rules
**Objectives:** convert STRAT-001 from YAML into executable, tested code.
**Deliverables:** `strategy_engine/` with spec loader, indicator layer, setup detection, full test suite; extended journal schema capturing IV/ATR/depth/regime/R:R.
**Dependencies:** Phase 1. **Exit:** every Part VI rule executable and tested; zero subjective inputs in the deterministic path.
**Highest risk:** in-sample rule fitting. 12 of 13 losses map to a proposed rule — that is *fitting*, not validation. Phase 3 must be honest about it.

### PHASE 3 — Backtesting environment
**Objectives:** test STRAT-001 out-of-sample.
**Deliverables:** Lean or backtesting.py harness; walk-forward framework; train/test separation; slippage/fee model from *actual* observed fills.
**Dependencies:** Phase 2. **Exit:** reproducible backtest; look-ahead protection proven by a deliberately-broken test that fails.
**Highest risk (largest in the plan):** **historical options data with greeks and depth may be unobtainable at reasonable cost.** Mitigation: backtest the underlying signal on equity data and defer option-selection validation to Phase 6. Decide this early — it may reshape everything after it.

### PHASE 4 — Deterministic risk engine
**Objectives:** all limits from Part VIII in code with authority over signals.
**Deliverables:** `risk_engine/` extending `risk_controls.py`; kill switches; sizing invariant (`wider stop ⇒ smaller size`) with tests.
**Dependencies:** Phase 2. **Exit:** no code path can place an order bypassing the risk engine — proven by test, not review.
**Highest risk:** a bypass path existing but untested. Mitigation: property-based tests asserting every order flows through the engine.

### PHASE 5 — Claude as red-team layer
**Objectives:** integrate the agent as veto-only.
**Deliverables:** `agent-analysis-service`; `TradeCandidate`/`AgentReview` schemas; veto logging; deepeval regression suite for veto consistency.
**Dependencies:** Phases 2 and 4. **Exit:** agent cannot modify any risk parameter (proven by schema and test); veto rate and accuracy measured.
**Highest risk:** the agent vetoing so much that nothing trades, or so little that it is decorative. Measure veto rate from day one.

### PHASE 6 — Shadow mode
**Objectives:** run the full pipeline live, submit nothing.
**Deliverables:** shadow runner; predicted-vs-actual execution comparison; ≥4 weeks, ≥30 candidates.
**Dependencies:** Phases 3–5. **Exit:** predicted fills within tolerance of observable market; zero critical failures.
**Highest risk:** shadow mode flatters itself by assuming fills. Mitigation: re-poll at +5/+15/+60min and compare against the actual book, not the mid.

### PHASE 7 — Paper trading
**Objectives:** validate under real-time conditions with full controls.
**Deliverables:** ≥50 paper trades over ≥8 weeks; full journal; quantstats tearsheet.
**Dependencies:** Phase 6. **Exit:** **all 12 go/no-go criteria in Part X met.**
**Highest risk:** the temptation to relax criteria when results disappoint. Mitigation: criteria are committed to git *now*, before results exist.

### PHASE 8 — Limited live
**Objectives:** smallest viable real-money deployment.
**Deliverables:** live at **0.5% risk per trade** (half the paper value); daily reconciliation; automatic reversion to paper on any criterion breach.
**Dependencies:** Phase 7 fully passed. **Exit:** 30 live trades within tolerance of paper performance.
**Highest risk:** live/paper divergence from slippage and fills. Mitigation: start at half size; any deviation beyond tolerance reverts to Phase 7 automatically.

---

## What I am not doing

Per your final instruction:

- **No new indicator or strategy has been invented.** STRAT-001 is a formalization of what
  already exists. STRAT-002 is deliberately unspecified because n=7 is not a basis for a spec.
- **No reassurance.** The system does not have a demonstrated edge, and removing two trades
  makes it net negative.
- **No reactive modification.** Every change proposed here is tied to a measured quantity, and
  the two places where I lack data (ATR/volatility stops, learned regime models) are marked
  EVALUATE rather than recommended.
- **No live trade.** Disabled in code, not just in policy.

The honest summary: **you have 26 trades, an edge indistinguishable from zero, and two
outliers holding up the entire P&L.** What you also have is a genuinely useful dataset about
*how* the system fails — regime breaches, MFE-zero entries, short holds — and those failure
modes are now specific enough to encode and test. That is a starting position, not a
finished strategy.

---

*Prepared 2026-08-06. All statistics calculated from `trades.csv` (26 closes, complete fields).
Regime reconstructed from QQQ daily history fetched 2026-08-06. Repository metadata verified
live via GitHub API 2026-08-06. Fields that were never recorded are marked as such and have
not been estimated.*
