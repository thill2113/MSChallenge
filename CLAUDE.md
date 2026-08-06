# MSChallenge — live options trading workspace

This repo drives a **real Robinhood brokerage account with real money** (Agentic, ••••4177,
`account_number` **404924177**, option_level_2, agentic_allowed) via the Robinhood MCP tools.

## 🛑 TRADING HALTED — 2026-08-06

**NO REAL-MONEY ORDERS.** Halted by user instruction pending strategy review.
Read `STRATEGY_REVIEW_2026-08-06.md` before proposing anything.

Headline: expectancy 95% CI is **[−$8.83, +$23.13] — it crosses zero at n=26.** The edge is not
statistically established. Resume live trading only when every go/no-go criterion in §11 of the
review is met (≥50 paper trades, CI lower bound > 0, PF ≥ 1.60, drawdown ≤ 8%, ≥10 trades per
regime, no 4-loss run, short-hold book not negative, P0+P1 controls in code).

Paper trading and analysis are permitted. Live orders are not.

## ⚠️ PENDING ACTIONS — check these first

None currently pending.

Remove rows here once done or stale. If the date has passed and it wasn't run, say so plainly rather than
silently dropping it.

## Start of every session

1. Read `state.json` — the single source of truth: open positions, resting stops, sizing, ruleset version,
   logged signals, weekly target, pending actions.
2. **Re-poll the broker before acting.** `get_option_positions` + `get_option_orders`. The user trades
   manually between sessions and broker stops fill silently. Never act on remembered state.
3. Check `performance_target` for the current week's progress.

## Hard rules — do not violate

- **Every entry requires explicit human confirmation.** Autonomous entry was proposed and **declined**:
  *"We are not there yet. Let's make some more $ then when I'm comfortable we can make that change."*
  Do not re-propose unsolicited.
- **Re-quote immediately before placing.** A recommendation's spread has blown out between recommendation
  and confirmation 7 times. Re-check spread/leverage/cost on fresh quotes; block the trade if it now fails.
- **Every open position carries a GTC stop.** After any cancel, confirm `state == "cancelled"` via
  `get_option_orders` before replacing, and replace immediately — the position is naked in between.
- **The weekly target never justifies a failing entry.** Missing $500 is acceptable; forcing a marginal
  trade is not.
- **Scans report BOTH calls and puts**, always, even when the regime gate blocks one side. Reporting one
  side only is a documented past failure.
- **Earnings gate:** pull a *fresh* calendar every time and check the *specific* candidate by name across
  the *entire* contract life. A stale file let HIMS through; a filter that omitted the name let IONQ through.
- Validate JSON (`json.load`) before every `state.json` commit — trailing commas have broken it twice.
- Commit and push after every file change. Branch: `claude/market-scanner-instructions-kxp5lg`. Never push
  elsewhere without permission. No PR unless explicitly asked.

## Files

| File | Purpose |
|---|---|
| `state.json` | Live state. Read first, update always. |
| `trades.csv` | Append-only ledger. Use `cat >> ... << 'EOF'` — `Edit` fails, a linter rewrites the file. |
| `scanner_instructions.txt` | Ruleset v1.1 + evidence base. |
| `dynamic_scanner_directive.txt` | Scan procedure. |

## Known gaps (logged, not yet enforced)

- **Stops are `regular_hours` only** — no overnight protection exists for equity options, and none can be
  created (chains show `extended_hours_state: disabled`; stop orders can't use extended sessions at all).
  Quoted per-position risk figures are **intraday-only**.
- **Earnings gate ignores sector peers** — a position can be "earnings-clear" while its closest comps report
  that night.
- **The 2.5% implied-stop floor is calibrated for single names**, not 3x ETFs. Leveraged ETFs are pass-only.

## Tone

The user wants decisive recommendations, not option menus. Give a call and the reasoning. Report losses and
your own errors plainly — that has been the basis of trust here. When the user overrides a recommendation,
log both sides so the outcome can be scored honestly.
