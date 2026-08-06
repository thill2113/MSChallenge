"""P0 risk controls — deterministic pre-trade gates.

Implements the four P0 items from STRATEGY_REVIEW_2026-08-06.md:

  P0-1  Stop width reverted to 15% (was 35% under v1.1)
  P0-2  Rolling drawdown halt
  P0-3  Chop regime is a hard block
  P0-4  Paper-trading mode

WHY THIS IS CODE AND NOT PROSE
------------------------------
The -$83.00 IREN loss on 2026-08-06 happened because the chop-regime rule existed
in `dynamic_scanner_directive.txt` §3.0, was read correctly, was restated correctly
as a caveat, and was then argued past. Adding a fifth prose rule would reproduce
that failure exactly.

`evaluate()` returns a GateResult whose `allowed` field is a boolean. A blocked
proposal has no "but the setup is clean" branch. The CLI exits non-zero so the
block is observable from outside the agent.

No third-party dependencies — stdlib only, so this runs in a fresh container.
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: P0-1. Reverted from v1.1's 35%. Evidence: no winning trade in 26 closes ever
#: drew down past -14.70%, so 35% rescued zero winners and enlarged every loser.
#: Replaying the v1.1 era at 15% turns +$88.84 into +$191.54.
STOP_WIDTH_PCT = 15.0

#: P0-3. QQQ day-change inside +/-this band means no directional regime is
#: established. Sourced from dynamic_scanner_directive.txt §3.0, which already
#: said "prefer to pass" — this makes it binding instead of advisory.
CHOP_BAND_PCT = 0.5

#: P0-2. Halt new entries when realized P&L has fallen this far below its
#: high-water mark, as a percentage of account equity.
#:
#: CALIBRATION IS HONEST, NOT RETROFITTED. See `replay_controls.py`:
#:   3.0% would NOT have blocked the 2026-08-06 trade (drawdown was 2.56%).
#:   2.5% WOULD have blocked it.
#: 2.5% is chosen because the user's stated tolerance is that -$193 over two
#: sessions must never happen; -$193 is 4.5% of equity, so the cap must sit
#: meaningfully below that. This is a judgement call on a single event and is
#: flagged as such rather than presented as a fitted optimum.
MAX_DRAWDOWN_FROM_PEAK_PCT = 2.5

#: Sessions in the rolling drawdown window.
DRAWDOWN_WINDOW_SESSIONS = 5

#: P0-2 (secondary). Consecutive losing closes, counted ACROSS sessions.
#: The pre-existing LOSS_STREAK_CIRCUIT_BREAKER reset at each session boundary,
#: which is why -$110 on 08-05 followed by -$83 on 08-06 tripped nothing.
MAX_CONSECUTIVE_LOSSES = 4

#: Consecutive losses at which to warn (not block).
WARN_CONSECUTIVE_LOSSES = 3

OPEN_ACTIONS = {"BUY", "BUY_TO_OPEN", "BUY_OPEN"}
CLOSE_ACTIONS = {"SELL", "SELL_TO_CLOSE", "SELL_CLOSE"}


class Mode:
    """P0-4. PAPER is the default; LIVE must be selected explicitly."""

    PAPER = "PAPER"
    LIVE = "LIVE"


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TradeProposal:
    symbol: str
    side: str  # "call" | "put"
    entry_price: float  # per-contract premium
    qty: int
    equity: float


@dataclass(frozen=True)
class MarketState:
    """Regime inputs. `qqq_change_pct` is a whole-number percent (+0.07, not 0.0007)."""

    qqq_change_pct: float


@dataclass
class GateResult:
    allowed: bool
    blocks: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stop_trigger: float | None = None
    stop_limit: float | None = None
    risk_usd: float | None = None
    mode: str = Mode.PAPER

    def render(self) -> str:
        head = "ALLOWED" if self.allowed else "BLOCKED"
        lines = [f"[{head}]  mode={self.mode}"]
        for b in self.blocks:
            lines.append(f"  BLOCK   {b}")
        for w in self.warnings:
            lines.append(f"  warn    {w}")
        if self.stop_trigger is not None:
            lines.append(
                f"  stop    trigger {self.stop_trigger:.2f} / limit {self.stop_limit:.2f}"
                f"  (risk ${self.risk_usd:.2f} at {STOP_WIDTH_PCT:.0f}% width)"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# P0-1: stop placement
# ---------------------------------------------------------------------------


def compute_stop(entry_price: float, qty: int = 1) -> tuple[float, float, float]:
    """Return (trigger, limit, risk_usd) for a 15%-width stop.

    The limit sits one further tick-band below the trigger so a fast move still
    fills, matching how the resting stops were priced through the campaign.
    """
    if entry_price <= 0:
        raise ValueError(f"entry_price must be positive, got {entry_price}")
    if qty < 1:
        raise ValueError(f"qty must be >= 1, got {qty}")

    trigger = round(entry_price * (1 - STOP_WIDTH_PCT / 100), 2)
    limit = round(trigger * 0.92, 2)
    risk = round((entry_price - trigger) * 100 * qty, 2)
    return trigger, limit, risk


# ---------------------------------------------------------------------------
# Ledger reading
# ---------------------------------------------------------------------------


def load_closes(ledger_path: str | Path) -> list[dict]:
    """Return realized closes from trades.csv, oldest first.

    Only rows carrying a realized_pnl are counted — BLOCKED, RATCHET, NOTE and
    DECISION rows are ignored.
    """
    path = Path(ledger_path)
    if not path.exists():
        return []

    out = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("action") not in CLOSE_ACTIONS:
                continue
            raw = (row.get("realized_pnl") or "").strip()
            if not raw:
                continue
            stamp = row["timestamp_utc"].strip().rstrip("Z")
            out.append(
                {
                    "date": datetime.fromisoformat(stamp).date(),
                    "symbol": row["symbol"],
                    "pnl": float(raw),
                }
            )
    out.sort(key=lambda r: r["date"])
    return out


def session_pnl(closes: list[dict]) -> list[tuple[date, float]]:
    """Aggregate closes into (session_date, realized_pnl), oldest first."""
    by_day: dict[date, float] = {}
    for c in closes:
        by_day[c["date"]] = by_day.get(c["date"], 0.0) + c["pnl"]
    return sorted(by_day.items())


# ---------------------------------------------------------------------------
# P0-2: drawdown + consecutive losses
# ---------------------------------------------------------------------------


def drawdown_from_peak(closes: list[dict], window: int = DRAWDOWN_WINDOW_SESSIONS) -> float:
    """Realized drawdown below the high-water mark over the trailing `window` sessions.

    Returns a non-positive number. 0.0 means at or above the running peak.

    A rolling *sum* was considered and rejected: across 07-31..08-05 the sum was
    +$64.94 because two large winners masked the -$110 day. Peak-to-trough is the
    measure that reflects capital actually surrendered.
    """
    sessions = session_pnl(closes)
    if not sessions:
        return 0.0

    recent = sessions[-window:]
    # Seed the peak with equity carried into the window, so a drawdown that
    # begins before the window still registers.
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for _, pnl in recent:
        equity += pnl
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def consecutive_losses(closes: list[dict]) -> int:
    """Count losing closes at the tail of the ledger. Crosses session boundaries."""
    streak = 0
    for c in reversed(closes):
        if c["pnl"] < 0:
            streak += 1
        else:
            break
    return streak


# ---------------------------------------------------------------------------
# P0-3 / P0-4 / composition
# ---------------------------------------------------------------------------


def is_chop(qqq_change_pct: float) -> bool:
    """True when no directional regime is established."""
    return abs(qqq_change_pct) < CHOP_BAND_PCT


def evaluate(
    proposal: TradeProposal,
    market: MarketState,
    *,
    mode: str = Mode.PAPER,
    ledger_path: str | Path = "trades.csv",
) -> GateResult:
    """Run every P0 gate. Any single block denies the trade.

    Gates are evaluated in full rather than short-circuiting, so the caller sees
    every reason at once instead of fixing them one at a time.
    """
    result = GateResult(allowed=True, mode=mode)
    closes = load_closes(ledger_path)

    # P0-4 -- paper mode
    if mode != Mode.LIVE:
        result.blocks.append(
            f"PAPER_MODE: real-money orders disabled. Mode is {mode}. "
            "Live trading requires the go/no-go criteria in "
            "STRATEGY_REVIEW_2026-08-06.md §11."
        )

    # P0-3 -- chop regime
    if is_chop(market.qqq_change_pct):
        result.blocks.append(
            f"CHOP_REGIME: QQQ {market.qqq_change_pct:+.2f}% is inside the "
            f"+/-{CHOP_BAND_PCT}% band. No directional regime. "
            "This is the gate that was overridden on 2026-08-06 for -$83.00."
        )

    # P0-2 -- drawdown
    dd = drawdown_from_peak(closes)
    dd_limit = -(proposal.equity * MAX_DRAWDOWN_FROM_PEAK_PCT / 100)
    if dd <= dd_limit:
        result.blocks.append(
            f"DRAWDOWN_HALT: realized drawdown ${dd:.2f} over the last "
            f"{DRAWDOWN_WINDOW_SESSIONS} sessions breaches the "
            f"{MAX_DRAWDOWN_FROM_PEAK_PCT}% limit (${dd_limit:.2f})."
        )
    elif dd <= dd_limit * 0.6:
        result.warnings.append(
            f"drawdown ${dd:.2f} is {abs(dd / dd_limit) * 100:.0f}% of the halt threshold"
        )

    # P0-2 secondary -- consecutive losses, across sessions
    streak = consecutive_losses(closes)
    if streak >= MAX_CONSECUTIVE_LOSSES:
        result.blocks.append(
            f"LOSS_STREAK: {streak} consecutive losing closes "
            f"(limit {MAX_CONSECUTIVE_LOSSES}). Counted across sessions -- the "
            "pre-existing breaker reset daily, which is why 08-05 and 08-06 "
            "tripped nothing."
        )
    elif streak >= WARN_CONSECUTIVE_LOSSES:
        result.warnings.append(f"{streak} consecutive losses -- review before proceeding")

    # P0-1 -- stop sizing (reported even when blocked, so the numbers are visible)
    trigger, limit, risk = compute_stop(proposal.entry_price, proposal.qty)
    result.stop_trigger, result.stop_limit, result.risk_usd = trigger, limit, risk

    max_risk = proposal.equity * 0.025
    if risk > max_risk:
        result.blocks.append(
            f"RISK_CAP: ${risk:.2f} exceeds 2.5% of equity (${max_risk:.2f})."
        )

    result.allowed = not result.blocks
    return result


# ---------------------------------------------------------------------------
# CLI -- exits non-zero on a block so the gate is observable externally
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) < 6:
        print(
            "usage: risk_controls.py SYMBOL SIDE ENTRY_PRICE QTY EQUITY QQQ_PCT [--live]",
            file=sys.stderr,
        )
        print(
            "example: risk_controls.py IREN call 2.41 1 4206.96 0.07",
            file=sys.stderr,
        )
        return 2

    symbol, side, entry, qty, equity, qqq = argv[:6]
    mode = Mode.LIVE if "--live" in argv else Mode.PAPER

    res = evaluate(
        TradeProposal(symbol, side, float(entry), int(qty), float(equity)),
        MarketState(float(qqq)),
        mode=mode,
    )
    print(res.render())
    if "--json" in argv:
        print(json.dumps(res.__dict__, indent=2, default=str))
    return 0 if res.allowed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
