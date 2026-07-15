# Non-Agentic Trading Scan Bot — Design

A rules-based (non-agentic) pipeline that scans the market on a schedule,
surfaces trade candidates that pass hard criteria, and hands them off for
execution — manual by default, with an optional review-gated semi-auto path
through the Robinhood connector.

"Non-agentic" here means: no AI judgment anywhere in the signal path. Every
candidate that appears passed a fixed, written-down rule set. The only place
an agent is involved is as plumbing (running the scan on a schedule and
formatting the alert), never as the decision-maker.

---

## What's live today

**Scan: "Unusual Options Flow — Liquid Names"** (rename in Legend; MCP can't set titles yet)
- `scan_id`: `6558236c-7882-4606-b2d5-18d35d1a4dd8`
- Sorted by: Relative options volume, descending

| Filter | Rule | Why |
|---|---|---|
| Relative options volume | > 3x (30-day avg) | The core signal — someone is trading 3x+ normal options volume today |
| Options volume | > 10,000 contracts (1d) | Kills noise from tiny chains where 3x = nothing |
| Open interest | > 10,000 | Ensures an established, tradeable chain |
| Last price | > $5 | No penny-stock junk |
| Market cap | > $2B | Liquid underlyings, tighter option spreads |
| Average volume | > 1M shares (30d) | You can actually get in and out of the stock/options |

First live run returned 10 names (EQPT at 6.7x relative options volume,
IBM, DINO, ARR, CLSK, AEHR, NTSK, INFY, FIG, C).

**Pre-existing scan** (untitled, `5ef6974d-...`): % change > 3% + relative
volume > 1.5x — a basic momentum screen. Keep it as a secondary confirmation
list: names appearing on BOTH scans are the highest-conviction candidates.

---

## Architecture: three tiers

### Tier 1 — Saved scans (deterministic filters)
Robinhood Legend scans, created and maintained via MCP. Each scan is one
strategy expressed as hard filters. Add these next:

**"Oversold Quality" (mean reversion)**
- RSI(14, 1d) < 30
- Market cap > $10B
- Average volume (30d) > 2M
- P/E BETWEEN 5 and 40 (excludes broken businesses and bubble names)
- Play: sell 30–45 DTE cash-secured puts or buy the dip on shares.

**"Momentum Breakout"**
- % change (1d) > 4%
- Relative volume (30d) > 2x
- Last > EMA(50, 1d) — price above trend
- Market cap > $2B
- Play: call debit spreads 30–45 DTE, or shares with a stop.

**"Earnings IV Crush Setup"**
- Earnings date BETWEEN today and +7 days
- Implied volatility > 60%
- Open interest > 10,000
- Play: defined-risk premium selling (iron condors) into earnings, or skip
  the trade and just watch — this one needs the most discipline.

### Tier 2 — Scheduled runner + second-stage contract rules
A Routine (scheduled trigger) runs each scan at fixed times — e.g. 9:45am ET
(after opening chop), 12:30pm, and 3:30pm ET — then applies **contract
selection rules** to each hit using the option-chain tools. These rules are
also fixed, not judgment calls:

1. Expiry: 30–45 DTE (weeklies only for the earnings scan).
2. Delta: 0.30–0.45 for directional longs; 0.20–0.30 short strikes for
   premium selling.
3. Liquidity gates (all must pass, otherwise the name is dropped):
   - Bid-ask spread < 5% of mid
   - Contract open interest > 500
   - Contract volume today > 100
4. Skip any underlying with earnings inside the trade window (except the
   earnings scan, where that's the point).

Output: a ranked alert (push/email) listing ticker, scan that fired, the
specific contract, mid price, and the max-loss for a 1-lot. No trade is
placed by the runner.

### Tier 3 — Execution (pick one, start at level 0)

- **Level 0 — Manual (start here):** you get the alert, you place the trade
  in the app. Run this for 3–4 weeks and log every alert (taken or not) so
  the criteria can be judged on data, not vibes.
- **Level 1 — Review-gated semi-auto:** the Robinhood connector in this
  workspace already supports `review_option_order` → `place_option_order`.
  The runner can pre-stage the exact order and present it; nothing executes
  until you approve that specific order. This is the sane ceiling.
- **Level 2 — Full auto:** not recommended, and specifically **not via a
  third-party agent framework holding your Robinhood credentials** (the
  "OpenClaw" idea). Handing session tokens for a real-money brokerage
  account to open-source agent software is an account-compromise and
  fat-finger risk that no scan edge pays for. If full auto ever makes sense,
  it's after months of Level 0/1 logs prove the rules have positive
  expectancy — and even then with the guardrails below hard-coded.

---

## Risk guardrails (non-negotiable, enforced before any order is staged)

- Max risk per trade: 1–2% of account value (defined-risk structures only —
  spreads, long options, CSPs; no naked short options).
- Max new positions per day: 2. Max total open positions from the bot: 6.
- Max capital deployed by the bot: 25% of account.
- No trades in the first 15 minutes of the session.
- Kill switch: if the bot's realized P&L over any rolling 10 trades is worse
  than −6% of account, it stops staging orders until manually re-enabled.
- Every alert and every fill logged to a journal (CSV in this repo is fine)
  — the log is what tells you whether a scan earns its slot.

---

## Reality check: asset coverage

| Asset class | Status |
|---|---|
| Stocks / ETFs | ✅ Scanner + full order flow available now |
| Options | ✅ Scanner filters + chains + quotes + review/place order flow |
| Futures | ❌ Robinhood offers them in-app, but the scanner and this connector don't expose them. Would need a separate data/execution path (e.g. a futures broker API) — out of scope for v1 |
| Prediction markets (event contracts) | ❌ Same story — in-app only, no scanner/API surface here. Also structurally different: no screening edge from volume/IV filters; skip for v1 |
| Crypto | ❌ Not exposed via this connector |

v1 scope = **stocks + options**. That's where the tooling is real and where
liquidity filters actually mean something.

---

## Roadmap

1. **Done:** Unusual Options Flow scan live and sorted.
2. Rename scans in Legend; add the three Tier-1 scans above.
3. Stand up the Tier-2 scheduled runner (3x/day during market hours) with
   alert-only output + journal logging.
4. After ~20 sessions of logs: review hit rate per scan, cut or tune
   underperformers, adjust thresholds.
5. Only then consider Level-1 review-gated order staging.

---

*Nothing in this document is financial advice; it's an engineering plan for
a screening tool. Options can lose 100% of premium; position sizing is the
only guardrail that always works.*
