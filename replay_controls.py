"""Replay the P0 controls over the real ledger.

Answers two questions with data rather than assertion:

  1. Which historical trades would each control have blocked?
  2. Is the drawdown threshold defensible, or fitted to one event?

Run: python3 replay_controls.py
"""

from __future__ import annotations

import csv
from datetime import datetime

import risk_controls as rc

LEDGER = "trades.csv"
V11_CUTOVER = datetime(2026, 7, 28, 15, 40)

# QQQ day-change at each entry. Recorded values only -- entries where the tape
# was not logged are marked None and excluded from the chop replay rather than
# guessed. This is why the chop column below covers 3 trades, not 26.
QQQ_AT_ENTRY = {
    ("2026-08-06", "IREN"): 0.07,   # verified: recommendation 15:33Z
    ("2026-08-05", "RIVN"): -0.59,  # verified: puts-only regime that session
    ("2026-08-03", "SOFI"): 1.20,   # verified: risk-on session
}


def paired_trades():
    rows = list(csv.DictReader(open(LEDGER)))
    opens, out = [], []
    for r in rows:
        if r["action"] in rc.OPEN_ACTIONS:
            opens.append(r)
        elif r["action"] in rc.CLOSE_ACTIONS and (r["realized_pnl"] or "").strip():
            match = None
            for i in range(len(opens) - 1, -1, -1):
                if opens[i]["contract"] == r["contract"] and not opens[i].get("_used"):
                    match = opens[i]
                    opens[i]["_used"] = True
                    break
            if match:
                out.append((match, r))
    return out


def main() -> None:
    trades = paired_trades()

    print("=" * 76)
    print("P0-1  STOP WIDTH 35% -> 15%")
    print("=" * 76)
    print("Only v1.1-era trades are replayed. v0.1-era trades already ran a 15%")
    print("stop, so replaying them is tautological; and asking whether a WIDER")
    print("stop would have helped them is unanswerable, because they were")
    print("terminated at 15% and their true excursion beyond that is censored.\n")

    actual = counter = 0.0
    for m, c in trades:
        if datetime.fromisoformat(m["timestamp_utc"].strip().rstrip("Z")) < V11_CUTOVER:
            continue
        entry, pnl, mae, qty = (
            float(m["fill_price"]), float(c["realized_pnl"]),
            float(c["mae_pct"]), float(m["qty"]),
        )
        if mae <= -rc.STOP_WIDTH_PCT:
            _, _, risk = rc.compute_stop(entry, int(qty))
            cf = -risk
            tag = f"stopped at {rc.STOP_WIDTH_PCT:.0f}%"
        else:
            cf, tag = pnl, "unaffected"
        actual += pnl
        counter += cf
        print(f"  {c['timestamp_utc'][:10]}  {c['symbol']:5s}  MAE {mae:7.2f}%   "
              f"actual {pnl:+8.2f}   at-15% {cf:+8.2f}   {tag}")
    print(f"\n  actual {actual:+.2f}   ->   with 15% stop {counter:+.2f}   "
          f"delta {counter - actual:+.2f}")

    print("\n" + "=" * 76)
    print("P0-2  DRAWDOWN THRESHOLD -- is 2.5% defensible or fitted?")
    print("=" * 76)
    closes = rc.load_closes(LEDGER)
    sessions = rc.session_pnl(closes)
    equity = 4206.96  # equity at the start of 2026-08-06

    print("  Session-by-session realized P&L and running drawdown from peak:\n")
    eq = peak = 0.0
    for d, pnl in sessions:
        eq += pnl
        peak = max(peak, eq)
        print(f"    {d}   pnl {pnl:+8.2f}   cum {eq:+8.2f}   dd-from-peak {eq - peak:+8.2f}")

    # State entering 2026-08-06, i.e. what a pre-trade check would have seen.
    prior = [c for c in closes if c["date"].isoformat() < "2026-08-06"]
    dd_before = rc.drawdown_from_peak(prior)
    print(f"\n  Drawdown entering 2026-08-06: ${dd_before:.2f} "
          f"({abs(dd_before) / equity * 100:.2f}% of equity)\n")
    print("  Would each candidate threshold have blocked the 08-06 trade?")
    for pct in (1.5, 2.0, 2.5, 3.0, 4.0, 6.0):
        limit = -(equity * pct / 100)
        verdict = "BLOCKS" if dd_before <= limit else "allows"
        note = ""
        if pct == 6.0:
            note = "   <- the pre-existing MAX_DAILY_LOSS_PCT"
        if pct == rc.MAX_DRAWDOWN_FROM_PEAK_PCT:
            note = "   <- CHOSEN"
        print(f"    {pct:4.1f}%  limit ${limit:8.2f}   {verdict}{note}")
    print("\n  HONEST NOTE: 2.5% is the loosest threshold that still blocks 08-06.")
    print("  That is a judgement anchored on ONE event, not a fitted optimum, and")
    print("  it should be re-derived once the paper-trading sample exists.")

    print("\n" + "=" * 76)
    print("P0-3  CHOP REGIME")
    print("=" * 76)
    print("  Replayed only where the QQQ reading at entry was actually recorded.")
    print("  Unlogged sessions are excluded rather than guessed.\n")
    saved = 0.0
    for m, c in trades:
        key = (c["timestamp_utc"][:10], c["symbol"])
        if key not in QQQ_AT_ENTRY:
            continue
        q = QQQ_AT_ENTRY[key]
        pnl = float(c["realized_pnl"])
        if rc.is_chop(q):
            saved += -pnl if pnl < 0 else 0.0
            print(f"    {key[0]}  {key[1]:5s}  QQQ {q:+.2f}%  CHOP -> BLOCKED   "
                  f"(actual {pnl:+.2f})")
        else:
            print(f"    {key[0]}  {key[1]:5s}  QQQ {q:+.2f}%  trending -> allowed "
                  f"(actual {pnl:+.2f})")
    print(f"\n  Losses avoided on the logged subset: ${saved:.2f}")

    print("\n" + "=" * 76)
    print("CURRENT ACCOUNT STATE")
    print("=" * 76)
    res = rc.evaluate(
        rc.TradeProposal("ANY", "call", 1.00, 1, 4123.88),
        rc.MarketState(qqq_change_pct=2.0),   # deliberately trending, to isolate
        mode=rc.Mode.LIVE,
        ledger_path=LEDGER,
    )
    print(res.render())
    print("\n  (Evaluated in LIVE mode against a trending tape so the halt shown")
    print("   is driven by ledger state, not by paper mode or the chop gate.)")


if __name__ == "__main__":
    main()
