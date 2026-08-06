# Strategy Review — 2026-08-06

**Status: TRADING HALTED.** No real-money orders until the go/no-go criteria in §9 are met.
Account flat, no resting orders, equity $4,123.88.

Every number below is computed from `trades.csv` (26 matched closes, all fields populated).
The analysis script is reproducible. Where a question could not be answered from the data,
that is stated rather than estimated.

---

## 1. Executive summary

Six findings, in order of importance.

1. **The strategy's edge is not statistically established.** Expectancy is +$6.98/trade, but the
   95% bootstrap CI is **[−$8.83, +$23.13]** — it crosses zero. At n=26 we cannot distinguish this
   strategy from a coin flip. Everything else in this review is conditional on that.

2. **The v1.1 stop widening (15% → 35%) was a clear, quantifiable error.** No winning trade in the
   entire ledger ever drew down past **−14.70%**. The extra 20 points of room rescued **zero**
   winners and enlarged every loser. Replaying the v1.1 era with the old 15% stop: **+$88.84 → +$191.54,
   an improvement of +$102.70.**

3. **The premise behind that change was logically inverted.** `state.json` records the v1.1 rationale as
   "6 of 10 losers never traded above entry" → therefore the stop is too tight. A trade that never
   trades above entry cannot be saved by a wider stop; it can only lose more. **MFE=0 is evidence of a
   bad entry, not a tight stop.** 8 of 26 trades (31%) are MFE=0, and they account for −$215.42.

4. **Short-duration trading is net negative and is the entire drag on the system.**
   - `<6h`: n=16, net **−$71.04**, PF **0.64**, expectancy **−$4.44**
   - `≥6h`: n=10, net **+$252.48**, PF **3.15**, expectancy **+$25.25**
   - Permutation test on the difference: **p = 0.043**. Your read on holding period is correct.

5. **Exit management is not the problem.** Median MFE capture on winners is **87.7%**. The trailing
   stop is doing its job. Effort spent on exit tuning is misdirected.

6. **No multi-session drawdown control exists.** `MAX_DAILY_LOSS_PCT` is 6% (~$252/day) and
   `LOSS_STREAK_CIRCUIT_BREAKER` resets each session. −$110 Wednesday plus −$83 Thursday tripped
   neither. The system is structurally blind to the exact failure you experienced.

**Recommendation: move to paper trading.** Supported by finding 1 alone.

---

## 2. Root-cause analysis

### 2.1 Failure taxonomy across all 13 losing trades

| Cause | Trades | P&L | Evidence |
|---|---|---|---|
| **Entry quality (MFE=0)** | 8 | −$215.42 | Never traded above entry. No stop setting helps. |
| **Stop width too wide (v1.1)** | 3 | −$102.70 excess | RIVN, APLD, IREN — all would have cut smaller at 15%. |
| **Regime discipline breach** | 1 | −$83.00 | IREN 08-06 entered in chop the directive says to pass on. |
| **Execution / tooling** | 1 | ~$0 realized | APLD: stop cancelled before replacement confirmed → 4 min naked. |

Categories overlap: IREN 08-06 is simultaneously MFE=0, a stop-width case, and a regime breach.

### 2.2 The three most recent losers, individually

**RIVN $15.50C — 08-05, −$68.00, MFE 0.0%, MAE −38.2%**
- *Signals supporting entry:* momentum trigger; spread 5.59% passing the 8% gate.
- *What was wrong:* the spread had widened to **7.91%** at confirmation — clearing the gate by 0.09pp
  — while **bid depth collapsed 93 → 18 lots**. Depth was not a gate at the time. The trade was
  admitted on a technicality.
- *Category:* **entry quality + missing liquidity control.** Not stop placement.
- *Unverified data used:* none. The failure was a real measurement the ruleset did not consult.

**APLD $32.00C — 08-05, −$42.00, MFE 14.2%, MAE −24.6%**
- *Signals supporting entry:* APLD held at −0.5% while AMD fell 4.84% on 08-04 — a decoupling thesis.
- *What was wrong:* the decoupling failed one day later. The thesis was **single-observation
  inference** — one day of relative strength treated as a durable relationship. That is an
  unsupported assumption, and it is the clearest instance of inferred-not-verified reasoning in the ledger.
- *Category:* **unsupported assumption + execution error** (stop cancelled before the replacement
  was confirmed placeable, leaving the position naked ~4 minutes when the MCP gate refused the order).

**IREN $41.00C — 08-06, −$83.00, MFE 0.0%, MAE −34.85%**
- *Signals supporting entry:* +3.57% momentum; 0.47% off intraday high; earnings-clear to 08-27;
  delta 0.481; spread 6.51%; leverage 8.36x; implied stop 4.19%; **bid depth rose 154 → 283 lots**.
  All contract-level gates genuinely passed, and the re-quote passed for the first time in six attempts.
- *What was wrong:* **the regime gate said do not trade.** QQQ was +0.07% at recommendation and
  −0.11% at fill — inside the ±0.5% chop band where directive §3.0 says "be selective and prefer to
  pass." Momentum was also already decelerating (40.4695 high → 40.09 fill). Both were flagged as
  caveats and both were overridden by the recommendation.
- *Category:* **regime detection breach — agent error.** Not a data, tooling, or market problem.
  Flagging a rule while overriding it is not compliance.
- *Unverified data used:* none. Every input was freshly polled. The failure was judgment.

### 2.3 Hallucinated / inferred / unverified data audit

Reviewing all 26 trades for fabricated or unverified inputs:

- **No hallucinated market data found.** Prices, greeks, spreads, and depth in the ledger trace to
  tool calls.
- **One material unsupported inference:** APLD's AMD-decoupling thesis (single observation).
- **Two historical data-quality incidents,** both already logged and both since fixed by policy:
  a stale earnings file admitted HIMS (07-31), and a name-omitting filter admitted IONQ.
- **One systematic gap, not a hallucination:** for much of the campaign, `get_equity_historicals`
  was erroring, so the "within 1% of intraday high/low" sub-trigger was verified against *observed
  prints only* rather than true session extremes. This is recorded in the ledger at the time. It
  means that gate was weaker than it appeared on several trades.

---

## 3. Quantified performance review

### 3.1 Headline (n=26, all closes)

| Metric | Value |
|---|---|
| Net realized | **+$181.44** |
| Win / loss / breakeven | 13 / 13 / 0 |
| Win rate | **50.0%** (Wilson 95% CI: 32.1% – 67.9%) |
| Gross win / gross loss | +$495.20 / −$313.76 |
| Average win | +$38.09 |
| Average loss | −$24.14 |
| Win/loss ratio | 1.58 |
| **Profit factor** | **1.58** |
| **Expectancy** | **+$6.98/trade** (95% CI **−$8.83 to +$23.13**) |
| **Max drawdown** | **−$193.00** — and it is the current trough (08-06) |

### 3.2 Before vs after the stop change

| | v1.0 (15% stop) | v1.1 (35% stop) |
|---|---|---|
| n | 19 | 7 |
| Net | +$92.60 | +$88.84 |
| Win rate | 47.4% | 57.1% |
| Avg win | +$23.71 | +$70.46 |
| **Avg loss** | **−$12.08** | **−$64.33** |
| Win/loss ratio | **1.96** | **1.10** |
| **Profit factor** | **1.77** | **1.46** |
| Expectancy | +$4.87 | +$12.69 |

v1.1 shows higher expectancy — but that is **entirely a four-trade winning streak** (+106.90, +82.94,
+38.00, +54.00) followed by three straight losses (−68, −42, −83). You were right to distrust it.
Profit factor **fell** 1.77 → 1.46, and average loss grew **5.3×** while average win grew only 3.0×.
The win/loss ratio nearly halved.

### 3.3 By holding period

| | Short (<6h) | Long (≥6h) |
|---|---|---|
| n | 16 | 10 |
| Net | **−$71.04** | **+$252.48** |
| Win rate | 43.8% | 60.0% |
| Profit factor | **0.64** | **3.15** |
| Expectancy | −$4.44 | +$25.25 |
| Median hold | 0.8h | 23.9h |

The five shortest holds (0.11h–0.39h) are **all losses**. Every trade over 20 hours except two is a win.
Permutation test p=0.043 — significant, though with the caveat that several tests were run.

### 3.4 Did the wider stop improve expectancy, or just enlarge losses?

**It only enlarged losses.** Direct evidence:

MAE of every winning trade, worst first: −14.70%, −8.00%, −5.00%, −5.00%, −4.00%, −3.00%, −2.40%,
−2.00%, −2.00%, 0.00%, 0.00%, 0.00%, 0.00%.

**12 of 13 winners never drew past −8%.** A 15% stop preserves all 13.

Replay on the v1.1 era (valid because those trades ran to a wide stop, so their full excursion is observed):

| Trade | Actual | With 15% stop | Δ |
|---|---|---|---|
| IREN 07-29 | +106.90 | +106.90 | unaffected |
| HIMS 07-31 | +82.94 | +82.94 | unaffected |
| SOFI 08-03 | +38.00 | +38.00 | unaffected |
| IREN 08-04 | +54.00 | +54.00 | unaffected |
| RIVN 08-05 | −68.00 | −26.70 | **+41.30** |
| APLD 08-05 | −42.00 | −27.45 | **+14.55** |
| IREN 08-06 | −83.00 | −36.15 | **+46.85** |
| **Total** | **+88.84** | **+191.54** | **+102.70** |

**Methodological note on direction.** This replay is valid only for *tightening*. Testing whether a
*wider* stop would have rescued v1.0 losers is impossible — those trades were terminated at 15%, so
their true excursion beyond that point is censored and unknowable. Any claim that widening "would
have helped" is unfalsifiable from this data. That asymmetry is precisely why the v1.1 change should
never have been made on the evidence available at the time.

---

## 4. Stop-loss recommendation

**Revert to a 15% stop. Quantitative support: +$102.70 on the v1.1 sample, zero winners lost.**

Evaluation of each option you listed:

| Option | Verdict | Basis |
|---|---|---|
| **Revert to 15%** | **RECOMMENDED** | Replay above. Preserves 13/13 winners, cuts every loser earlier. |
| Volatility-adjusted stops | **Cannot recommend — insufficient data** | Requires per-trade IV/ATR at entry. IV is recorded for only the most recent handful of trades. Recommending this now would violate your "no change without quantitative support" rule. Collect IV at entry for every paper trade; revisit at n≥30. |
| Structure-based stops | **Cannot recommend — data not captured** | Requires intraday support/resistance levels at entry, which the ledger never recorded. Same instruction: capture first, decide later. |
| **Reduce position size** | **RECOMMENDED alongside 15%** | Independent of stop width and directly addresses your −$193 constraint. See §5. |
| Suspend the strategy | **Effectively yes** — see §5 | Paper trading is suspension of capital risk while evidence accumulates. |

**The deeper point:** a stop is a loss-limiter, not an edge-generator. With 31% of trades never
trading above entry, no stop setting produces a good strategy. Reverting to 15% stops the bleeding;
it does not create an edge. The edge problem is entry selection and holding period.

---

## 5. Production decision

**Recommendation: PAPER TRADING.** This matches your stated preference and the evidence
independently supports it — finding 1 (CI crosses zero) is sufficient on its own.

Interim risk posture if you ever override this and trade live before validation:
- Position size **halved** (1.25% risk per trade, not 2.5%)
- 15% stop
- **Long-duration setups only** — no entries with an intended hold under 6 hours
- Hard stand-down in chop regime, non-overridable

---

## 6. Validation criteria before real money resumes

Sample-size figures are derived from the observed per-trade SD of **$42.29**, not chosen for
roundness.

| Criterion | Threshold | Derivation |
|---|---|---|
| **Minimum trades** | **50 paper trades** | n=48 puts the 95% CI half-width under $12. n=31 only reaches $15. Below ~50 you cannot tell a real edge from noise. |
| **Minimum profit factor** | **≥ 1.60 sustained** | Current 1.58 over the full sample; v1.0 achieved 1.77. Requires beating the existing record, not matching it. |
| **Minimum expectancy** | **95% CI lower bound > 0** | This is the actual bar. A point estimate is not evidence — the CI must exclude zero. |
| **Max acceptable drawdown** | **≤ 8% of equity, peak-to-trough** | Current max is −$193 = 4.5%. This caps deterioration at roughly double the worst observed. |
| **Consecutive-loss threshold** | **Halt at 4; review at 3** | P(a 5-run in 26 trades at 50% WR) ≈ 50%, so 5 is *expected noise*, not breakage. 4 is the defensible action threshold. |
| **Regime coverage** | **≥ 10 trades in each of trending-up, trending-down, and chop** | The strategy has never been validated in chop — and chop is where it just lost $83. |
| **Holding-period split** | Report short vs long separately | Do not let long-hold performance mask a negative short-hold book. |

**Additional gate:** paper trades must be logged with the same rigor as live — entry rationale,
opposing evidence, IV, ATR, bid depth, regime label, intended hold. The v1.1 mistake happened
because the data needed to evaluate it was not being captured.

---

## 7. Required controls (per your §5)

Ranked by evidence of harm from their absence.

| # | Control | Status | Justification from data |
|---|---|---|---|
| 1 | **Multi-session drawdown limit** | **MISSING** | The −$193 event. No rule spans sessions. **3% over any rolling 5 sessions.** |
| 2 | **Mandatory no-trade state** | **EXISTS BUT OVERRIDABLE** | Chop rule was live and argued past. Must become a hard block, not advice. |
| 3 | **Minimum holding-period intent** | **MISSING** | Short-hold book PF 0.64. Reject setups whose thesis resolves in under 6h. |
| 4 | **Liquidity/depth validation** | **PARTIAL** | Added 08-05 after RIVN. Still lacks an absolute floor and does not check depth at recommendation. |
| 5 | **Consecutive-loss limit (cross-session)** | **PARTIAL** | Resets each session. Must persist. |
| 6 | **Conflicting-signal detection** | **MISSING** | IREN 08-06 had 4 negative caveats and still got recommended. Two or more → automatic no-trade. |
| 7 | **Confidence scoring** | **MISSING** | No quantified confidence; caveats were prose, easily rationalized past. |
| 8 | **Multi-timeframe confirmation** | **MISSING** | Entries used intraday momentum only. IREN was +3.6% intraday but flat on the day it round-tripped. |
| 9 | **Volatility analysis** | **MISSING** | IV recorded inconsistently; never used as a gate. |
| 10 | **Risk-to-reward validation** | **MISSING** | No target was ever defined at entry, so R:R was never computable. |
| 11 | **Invalidation criteria** | **MISSING** | No "if X, thesis is dead" defined pre-trade. IREN's +3% gate failed while the position stayed open. |
| 12 | **Regime classification** | **EXISTS** | Present and correct. It was ignored. |
| 13 | **Position-sizing controls** | **EXISTS** | Working as designed. |

---

## 8. Tooling assessment

Metadata verified live via GitHub API on 2026-08-06 — stars, license, and last-push are current, not recalled.

### Adopt

| Repo | Stars | License | Last push | Purpose & fit | Integration |
|---|---|---|---|---|---|
| **ranaroussi/quantstats** | 7,525 | Apache-2.0 | 2026-07-20 | Tearsheets: PF, expectancy, drawdown, Sharpe/Sortino. Replaces the ad-hoc analysis in this document with a standard, auditable one. | **Low.** Consumes a returns series. Days. |
| **kernc/backtesting.py** | 8,758 | **AGPL-3.0** | 2026-08-05 | Simple, well-documented backtester with built-in walk-forward optimisation. Right scale for a single-strategy book. | **Low-medium.** ⚠️ **AGPL is strong copyleft** — fine for private use; would force disclosure if you ever distribute or host it as a service. Confirm that's acceptable. |
| **hmmlearn/hmmlearn** | 3,412 | BSD-3 | 2024-10-31 | HMM regime classification — directly addresses control #1/#12: a *quantitative* trend/chop label instead of a QQQ threshold. | **Medium.** Stale-ish but mature and stable; BSD-3 is permissive. |

### Adapt

| Repo | Stars | License | Last push | Notes |
|---|---|---|---|---|
| **dcajasn/Riskfolio-Lib** | 4,430 | BSD-3 | 2026-06-22 | Excellent risk/drawdown machinery, but built for portfolio allocation. Take the drawdown and risk-contribution functions; ignore the optimiser. |
| **polakowo/vectorbt** | 8,600 | **NOASSERTION** | 2026-08-02 | Fast vectorised parameter sweeps — would answer "what stop width is optimal" across a grid in seconds. ⚠️ **License is non-standard**; the actively developed successor is commercial. Verify terms before adopting. |
| **nautechsystems/nautilus_trader** | 25,319 | LGPL-3.0 | 2026-08-06 | Production-grade, event-driven, deterministic — the reference architecture for the bot in §9. Genuinely excellent and very actively maintained. ⚠️ **Heavyweight**: Rust core, steep learning curve, far beyond a one-strategy retail book today. Study the architecture now; adopt only if this scales. |

### Reject

| Repo | Stars | Why rejected |
|---|---|---|
| **hudson-and-thames/mlfinlab** | 4,901 | **Last push 2023-10-02 — ~3 years stale.** License NOASSERTION; the project moved commercial. Abandonware risk. |
| **quantopian/pyfolio** | 6,389 | **Last push 2023-12-23.** Quantopian is defunct. Superseded by quantstats. |
| **HKUDS/Vibe-Trading** | 30,031 | **Created 2026-04-01 — four months old.** Highest star count in the search and the weakest candidate: no track record, no stability guarantees, unproven in production. Exactly the "popular ≠ good" case you warned against. |

---

## 9. Agent vs. deterministic bot

**Your instinct is correct, and this session is the evidence.**

| Dimension | Agent | Deterministic bot |
|---|---|---|
| Consistency | **Poor** — same rule applied differently across trades | **Perfect** |
| Hallucination risk | Real (one unsupported inference found) | **Zero** |
| Testability | **Very poor** — cannot backtest a judgment call | **Excellent** |
| Execution reliability | Variable | **High** |
| Explainability | Good prose, but post-hoc | Exact, by construction |
| Adaptability | Good | Poor |
| Latency | Seconds–minutes | Milliseconds |

**The decisive evidence:** the −$83 IREN loss occurred because an agent read a hard rule, restated it
correctly, and then traded against it. A deterministic implementation of that same rule is
physically incapable of that failure. Separately, six re-quote blocks *were* correctly enforced — the
agent is reliable at mechanical checks and unreliable at discretionary ones, which is exactly the
line a hybrid should be drawn along.

### Recommended hybrid split

**Deterministic — no agent discretion, ever:**
- Regime classification and the trade/no-trade gate
- All contract gates (spread, delta, leverage, implied stop, DTE, bid depth)
- Position sizing and stop placement
- Earnings-gate lookup across full contract life
- Daily, weekly, and rolling drawdown limits
- Consecutive-loss circuit breaker
- Order placement, stop attachment, and re-quote validation

**Agent-assisted — proposes, never disposes:**
- Candidate discovery and universe construction beyond a fixed watchlist
- Narrative/catalyst interpretation (e.g. is a move M&A or momentum?)
- Anomaly flagging for human review
- Post-trade analysis and journaling — **the agent's genuine strength, and this document is the demonstration**

**The rule:** the agent may only ever *narrow* the deterministic system's candidate set. It may never
widen it, override a gate, or size a position.

---

## 10. Prioritised remediation plan

| P | Action | Effort | Rationale |
|---|---|---|---|
| **P0** | Revert stop to 15% | Trivial | +$102.70 on replay |
| **P0** | Rolling 5-session 3% drawdown halt | Small | The −$193 gap |
| **P0** | Make chop-regime a hard block | Small | The −$83 loss |
| **P0** | Switch to paper trading | None | CI crosses zero |
| **P1** | Minimum 6h intended hold | Small | Short book PF 0.64 |
| **P1** | Conflicting-signal auto-veto (≥2 caveats → no trade) | Small | IREN had 4 |
| **P1** | Capture IV, ATR, depth, regime, R:R on every paper trade | Medium | Prerequisite for vol/structure stops |
| **P2** | quantstats reporting | Small | Standardised, auditable metrics |
| **P2** | backtesting.py harness + walk-forward | Medium | Test changes before deploying |
| **P3** | hmmlearn regime classifier | Medium | Replace threshold with a model |
| **P3** | Port deterministic gates out of prose into code | Large | The structural fix |

---

## 11. Go / No-Go

**Return to real money only when ALL are true:**

1. ≥ 50 paper trades logged with full instrumentation
2. 95% CI lower bound on expectancy **> 0**
3. Profit factor ≥ 1.60
4. Max drawdown ≤ 8% of equity
5. ≥ 10 trades in each regime (trending-up / trending-down / chop)
6. No single consecutive-loss run ≥ 4
7. Short-hold and long-hold books reported separately; short-hold not net negative
8. All P0 and P1 controls implemented **in code**, not documentation

**Any one failing = no live trading.** These are conjunctive, not a scorecard.

---

*Prepared 2026-08-06 following three consecutive losing trades totalling −$193 over two sessions.
All statistics computed from `trades.csv`; repo metadata verified live via GitHub API.*
