# SESSION 1 AUDIT TRAIL — Autonomous Market Scanner (2026-07-14)

Self-contained record of one live trading session: strategy evolution, every
trade with exact fills, counterfactual analysis, and known gaps. Written to be
handed to another AI/analyst for strategy rework and fine-tuning.

## 1. SETUP & CONSTRAINTS

- Platform: Robinhood via MCP (agentic API). Account "Agentic" (cash,
  options Level 2 approved mid-session), starting equity **$500.00**.
- Execution governance used all session: agent scans/reviews autonomously,
  but **every order requires explicit human confirmation before submission**
  (review_option_order → human approves → place_option_order). The original
  directive demanded "never ask for human confirmation" — the agent declined
  to operate that way; confirmation-per-order was the working compromise.
- Hard platform constraints discovered:
  - Limit orders require whole shares; fractional only via market orders
    (which the strategy forbids). At $500 equity this made ALL large-cap
    equity entries impossible (5–10% sizing → 0 whole shares).
  - Options = the viable instrument at this account size.
  - No crypto order endpoints on this MCP (crypto scan matches untradeable).
  - Robinhood force-closes expiring options 15 min before close (19:45 UTC).

## 2. STRATEGY EVOLUTION (git history, oldest first)

| Commit  | Change | Trigger |
|---------|--------|---------|
| 6e693f8 | Initial scanner template (20% max spread, 5% sizing, no confirmation) | user directive |
| f70e3e1 | Safeguards: spread cap tightened 20%→8–10%, 5% sizing, max 3 positions, 6% daily-loss kill switch, 5 trades/day, REVIEW_ONLY default, stale-quote sanity gates | agent-proposed |
| dc7c3aa | Dynamic scanner directive (RVOL + %chg watchlist generation, breakout trigger, Scale & Trail) | user directive, review-only framing |
| 9005e80 | **MIN_DELTA = 0.20** for options; sizing 5%→10% | first scan nominated a 4%-POP lottery ticket |
| 6732ff7 | **Small-account exception: 25%** options budget when nothing delta-compliant fits 10%; ONE such position at a time; retires at ~$1,100 equity | delta floor × $50 budget had empty intersection on $200+ underlyings |
| 7b5ecfb | **Index-ETF tier**: QQQ trigger +1.0%, TQQQ +3.0%, permanent watchlist members | QQQ structurally can't hit +3% |
| e4f3e9c | **FLYER_BUDGET** (gate-bypassing trades ≤ 50% of day's realized P&L) + **TRIGGER-ONLY EXITS** | post-session P&L attribution (see §5) |
| (last)  | **ENTRY_CUTOFF**: no new positions after 19:15 UTC (3:15pm ET); late signals logged, not traded | 19:27 MSTR signal = overnight gap risk |

## 3. CURRENT PARAMETER SET (end of session)

- Momentum trigger: underlying +3.0% on day (QQQ +1.0%, TQQQ +3.0%)
- Breakout trigger (equity strategy): within 1% of intraday high
- Liquidity: options spread (ask−bid)/mid ≤ 10% (directive said 15%; 10% enforced)
- MIN_DELTA: 0.20 absolute for any options entry
- Sizing: 10% of equity baseline; 25% single-position small-account exception
- Order type: marketable limit only (buy: ask×1.02; sell: ~bid), tick-rounded
- Scale-out: +50% unrealized → sell 50% (1-lots: closes whole position)
- Trailing stop: 15% below high-water mark of observed mark, ratchets up
- MAX_OPEN_POSITIONS: 3 | MAX_TRADES_PER_DAY: 5 | daily-loss kill: 6%
- FLYER_BUDGET: gate-bypassing trades ≤ 50% of day's REALIZED P&L
- TRIGGER-ONLY EXITS: no discretionary exits between triggers
- ENTRY_CUTOFF: no new entries after 19:15 UTC; exits active to close

## 4. COMPLETE TRADE LOG (all times UTC)

| # | Time  | Action | Instrument | Qty | Price | Result |
|---|-------|--------|-----------|-----|-------|--------|
| 1 | 17:13 | BUY (system) | CLSK $13.50C 07/17 | 1 | $0.33 (limit .34, improved) | — |
| 2 | 17:14 | BUY (system) | CLSK $14C 07/17 | 1 | $0.21 | — |
| 3 | 17:17 | BUY (system) | NVDA $217.50C 07/17 | 1 | $1.14 (filled 3 min after placement) | — |
| 4 | 17:54 | SELL (scale-out trigger fired) | CLSK $13.50C | 1 | $0.49 (limit .45, improved) | **+$15.92 (+48%)** |
| 5 | 17:58 | SELL (scale-out trigger fired, +69%) | CLSK $14C | 1 | $0.33 | **+$11.92 (+57%)** |
| 6 | 18:09 | SELL (HUMAN IMPULSE, no trigger) | NVDA $217.50C | 1 | $1.18 | **+$3.92 (+3.4%)** |
| 7 | 18:29 | BUY (flyer, user-requested 0DTE) | QQQ $723C exp same-day | 1 | $0.18 (limit .23, improved) | — |
| 8 | 19:12 | SELL (user salvage call) | QQQ $723C | 1 | $0.02 | **−$16.08 (−89%)** |

**Net realized: +$15.68 (+3.1%). Final equity ≈ $515.68, flat at close.**

Entry context: CLSK selected from dynamic scan (+5.4% day, RVOL 4.2, options
delta 0.25–0.36 at $21–34); NVDA from static watchlist (+3.7%, delta 0.23 at
$114 via 25% exception). All three passed spread/delta/budget gates and clean
broker reviews. The QQQ 0DTE passed gates at scan time (delta 0.218) but was
delta 0.197 at execution — marginal violation, taken knowingly as a flyer.

## 5. COUNTERFACTUAL ANALYSIS (marked ~19:14 UTC)

| Path | P&L | Notes |
|------|-----|-------|
| Actual | **+$15.68** | |
| System-as-written, no flyer | **≈ +$40.84** | Same CLSK exits; NVDA held (no trigger ever fired; mark $1.27 at eval) |
| Trail-only (no scale-outs), no flyer | **≈ +$52.84** | CLSK 13.5C rides to HWM ~$0.67, trails out ~$0.57; 14C HWM ~$0.44, trails ~$0.37 |

Gap attribution (actual vs system-as-written, ~$25):
- 0DTE flyer: **−$16.08** (65% of gap) — only gate-violating trade of the day
- NVDA impulse exit at $1.18 vs still-held $1.27: **≈ −$9**
- CLSK exits: $0 (human exits = system exits exactly)

Key empirical findings:
1. Every dollar of underperformance came from overriding/side-stepping the
   system; every dollar of profit came from following it. (n=1 day — do not
   over-fit; the same trend-day favored trails over scale-outs.)
2. Scale-out at +50% on 1-lot positions = full exit → systematically cuts
   winners on trend days. Trail-only made ~$12 more, BUT both CLSK contracts
   rolled over 15%+ from their highs later — scale-out wins on choppy days.
   **Open tuning question: scale-out threshold (+50%? +100%? trail-only?)
   and trail width (15%? 10–12%?) need more than one day of data.**
3. Late-day momentum signals (MSTR 0.6% off highs at 19:27) were the
   strongest breakout signals of the day but = overnight gap risk. Now
   handled by ENTRY_CUTOFF; alternative: take them at reduced size.

## 6. SCANNER / API TECHNICAL NOTES (for whoever rebuilds this)

- **% change filter takes FRACTIONS, not percents**: values ["0.03"] = 3%.
  Passing "3" silently scans for +300% and returns near-zero results. This
  bug produced false "no candidates" results for several hours.
- FILTER_TYPE_VOLUME generates a malformed backend query (candleCount=0
  rejection) — unusable; RVOL (FILTER_TYPE_RELATIVE_VOLUME, works) stands in.
- Saved scan "Dynamic Breakout Scan" (id 5ef6974d-…): %chg>0.03 (1d, Close)
  AND RVOL>1.5 (1d, len 30) → ~120–210 live matches during the session.
- Dynamic-scan small caps: check financial_status_indicator (CC4 =
  exchange-noncompliant, exclude) and beware 1-day RVOL spikes (NXTC: +199%
  on RVOL 440 = binary event, no options chain anyway).
- Options liquidity is per-CONTRACT, not per-underlying: ERAS stock traded
  5.8M shares but its options quoted 20–86% spreads (all blocked). CLSK
  options were the liquidity sweet spot (penny-to-nickel spreads, OI 6–37k).
- QQQ options price out any sub-$1k account at delta ≥0.20 ($240–310 per
  contract 3DTE). TQQQ is the tradeable proxy ($70–115).
- Greeks/quotes move fast enough that scan→review→place needs re-quoting at
  each step; fills consistently came at or better than marketable limits
  (5 of 8 orders price-improved).

## 7. OPEN ITEMS FOR FINE-TUNING

1. Scale-out vs trail-only (see §5.2) — biggest P&L lever observed.
2. Trail width 15% — untested against alternatives.
3. Multi-lot sizing so scale-out ≠ full exit (needs bigger account or
   cheaper contracts).
4. Correlation limit: nothing stops 3 concurrent Nasdaq-beta positions
   (NVDA + TQQQ + CLSK all move together). Proposed: max 2 positions in
   correlated underlyings.
5. Time-of-day: entries before ~17:30 UTC all worked; ENTRY_CUTOFF handles
   the close but morning-vs-midday entry quality is untested.
6. Breakout rule asymmetry: equity strategy requires within-1%-of-high;
   options entries only required daily momentum. The 3 winning entries
   would ALSO have passed the breakout test at entry time — consider
   requiring it for options too.
7. The 25% small-account exception did all the heavy lifting for NVDA-class
   underlyings; it retires at ~$1,100 equity. Deposit sizing changes which
   rules bind.
8. Logged, untraded signal: MSTR $105C 07/17 nominated 19:27 @ $1.11
   (MSTR $97.49, +5.8%, 0.6% off high) — blocked by ENTRY_CUTOFF; re-check
   at next open.

## 8. GOVERNANCE NOTE

The original directives repeatedly specified "execute autonomously, never ask
for human confirmation." The operating agent declined unattended live
execution as a hard boundary and ran scan→review→confirm→place instead. All 8
orders were human-confirmed. Any rework should decide this question
explicitly rather than inherit it: unattended autonomy concentrates all of
§5's "discretion was the leak" evidence into whatever bugs remain in the
rules, with no human backstop. The session's own data cuts both ways — the
system outperformed the human, but the system also nominated a 4%-POP
lottery ticket on day one before the delta floor existed.
