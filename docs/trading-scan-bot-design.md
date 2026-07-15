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

**All five Tier-1 scans are now live** (updated 2026-07-15, v2):

| Scan | `scan_id` |
|---|---|
| Unusual Options Flow — pre-move (main signal) | `6558236c-7882-4606-b2d5-18d35d1a4dd8` |
| Flow After Move (lower priority) | `76a9a6a1-3322-4009-8ca3-adbf951c52d6` |
| Oversold Quality — Mean Reversion | `09464e44-f975-4237-b2a6-030d64526cc0` |
| Momentum Breakout | `1ae663e1-0219-48c6-997d-ffc847914a61` |
| Earnings IV Crush Setup | `53aa75e0-e8d9-4387-9e7b-3a06a0858715` |

Filter details as implemented (deviations from the original sketch):
- **Unusual Options Flow (pre-move)**: the base liquidity filters PLUS
  `% change BETWEEN -2% and +2%` — heavy options volume while price hasn't
  moved yet is positioning, not chasing. Also carries `Total call volume > 0`
  and `Total put volume > 0` as no-op filters purely to surface the call/put
  columns the runner uses for direction classification.
- **Flow After Move**: identical base filters but `% change OUTSIDE ±2%` —
  the names that already ran. Lower priority: the move happened and the
  options are repriced. First run proved the split: AEHR (+34% day) landed
  here, out of the main signal.
- **Oversold Quality**: RSI(14, 1d) < 30; market cap > $10B; avg volume (30d) > 2M; P/E BETWEEN 5–40. As designed.
- **Momentum Breakout**: % change (1d, close) > 4%; relative volume (30d) > 2x; market cap > $2B; last > $5. The "price above EMA(50)" idea was dropped — the scanner can only compare a datapoint against constants, not against another datapoint.
- **Earnings IV Crush**: built from the UPCOMING_EARNINGS preset so the earnings window stays *relative* (0–7 days out, never goes stale); plus IV > 60%, open interest > 10k, market cap > $2B, avg volume (30d) > 1M, stocks only.

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

### Tier 2 — Scheduled runner + second-stage contract rules (v2)
The runner fires weekdays at **10:30am, 12:45pm, and 3:45pm ET** (cron
`30 14 * * 1-5` + `45 16,19 * * 1-5` UTC during EDT; shift one hour for
EST). The first run is 10:30 rather than right after the open because the
scanner's "relative volume" metrics compare partial-day volume against
full-day averages — a 9:45 run is structurally muted.

Each firing runs the five scans and applies fixed second-stage logic:

1. **Direction classification** (flow scans only): from the Total call
   volume / Total put volume columns — call/put ratio > 1.5 = bullish,
   < 0.67 = bearish, in between = skip as unclear. No flow hit gets a
   contract without a direction.
2. **Ranking**: names on 2+ scans first, then by signal metric; pre-move
   flow outranks Flow After Move. Top 5 proceed.
3. **Cooldown**: any ticker already in `journal/alerts.csv` within the last
   3 trading days is skipped unless its current signal metric is ≥ 1.25x
   the last logged value.
4. **Position awareness**: candidates already held (equity or options
   positions) are flagged `HELD — no add` and get no contract pick; if 3+
   names across alerts + current positions share a sector, the alert
   carries a concentration warning.
5. **Outcome tracking**: every run backfills `journal/outcomes.csv` with
   T+1 / T+5 / T+20 underlying returns for past alerts that have come due.
6. **Weekly scorecard**: Friday afternoon run regenerates
   `journal/scorecard.md` — per scan: alert count, hit rate (T+5 return in
   the trade's direction), average T+5 return, best/worst. Any scan under
   40% hit rate across 10+ alerts is flagged as a cut candidate.

It never places, reviews, or stages orders. Contract-selection rules
(fixed, not judgment calls):

1. Expiry: 30–45 DTE (earnings scan: first weekly expiring AFTER the
   earnings date).
2. Delta by signal: bullish flow / momentum → call 0.30–0.45; bearish flow
   → put −0.30 to −0.45; oversold → put −0.20 to −0.30 (cash-secured put
   candidate); earnings → ATM straddle price noted only, no contract pick.
3. Liquidity gates (all must pass, otherwise the name is dropped):
   - Bid-ask spread < 5% of mid
   - Contract open interest > 500
   - Contract volume today > 100
4. Skip any underlying with earnings inside the trade window (except the
   earnings scan, where that's the point).

Output: a ranked alert (push/email) listing ticker, scan(s) that fired,
direction, the specific contract, mid price, max loss for a 1-lot, and any
HELD / concentration flags. Journal files written each run:
`journal/alerts.csv` (header: timestamp_utc,scan,ticker,last_price,
signal_metric,direction,contract,mid,max_loss_1lot,notes),
`journal/outcomes.csv`, and `journal/scorecard.md` (weekly). No trade is
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
