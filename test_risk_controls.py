"""Tests for the P0 risk controls.

Run: python3 -m unittest test_risk_controls -v

The load-bearing tests are in HistoricalRegressionTests: they assert that the
trades which actually lost money would have been blocked. A control that cannot
be shown to catch the failure it was written for is not a control.
"""

from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

import risk_controls as rc


def _ledger(rows: list[tuple[str, str, float]]) -> str:
    """Write a temp trades.csv from (timestamp, symbol, pnl) triples."""
    fh = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
    w = csv.writer(fh)
    w.writerow(
        ["timestamp_utc", "action", "symbol", "contract", "qty", "fill_price",
         "fees", "realized_pnl", "trigger_type", "mfe_pct", "mae_pct", "notes"]
    )
    for stamp, sym, pnl in rows:
        w.writerow([stamp, "SELL", sym, f"{sym} C", 1, 1.0, 0.04, pnl, "TEST", 0, 0, ""])
    fh.close()
    return fh.name


class StopTests(unittest.TestCase):
    def test_width_is_15_percent(self):
        self.assertEqual(rc.STOP_WIDTH_PCT, 15.0, "P0-1: must be reverted from 35%")

    def test_trigger_and_risk(self):
        trigger, limit, risk = rc.compute_stop(2.41, qty=1)
        self.assertAlmostEqual(trigger, 2.05, places=2)
        self.assertLess(limit, trigger)
        self.assertAlmostEqual(risk, 36.00, places=2)

    def test_risk_scales_with_qty(self):
        _, _, r1 = rc.compute_stop(1.00, qty=1)
        _, _, r2 = rc.compute_stop(1.00, qty=2)
        self.assertAlmostEqual(r2, r1 * 2, places=2)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            rc.compute_stop(0)
        with self.assertRaises(ValueError):
            rc.compute_stop(1.0, qty=0)

    def test_iren_loss_would_have_been_capped(self):
        """The 08-06 IREN trade lost $83. A 15% stop caps it near $36."""
        _, _, risk = rc.compute_stop(2.41, qty=1)
        self.assertLess(risk, 40.0)
        self.assertLess(risk, 83.00, "must be materially better than the actual loss")


class ChopTests(unittest.TestCase):
    def test_flat_tape_is_chop(self):
        self.assertTrue(rc.is_chop(0.07))   # actual QQQ at IREN recommendation
        self.assertTrue(rc.is_chop(-0.11))  # actual QQQ at IREN fill
        self.assertTrue(rc.is_chop(0.0))

    def test_directional_tape_is_not_chop(self):
        self.assertFalse(rc.is_chop(0.9))
        self.assertFalse(rc.is_chop(-1.75))

    def test_boundary_is_exclusive(self):
        self.assertFalse(rc.is_chop(0.5))
        self.assertFalse(rc.is_chop(-0.5))
        self.assertTrue(rc.is_chop(0.49))


class DrawdownTests(unittest.TestCase):
    def test_no_drawdown_when_rising(self):
        led = _ledger([("2026-08-03T15:00:00", "A", 38.0),
                       ("2026-08-04T15:00:00", "B", 54.0)])
        self.assertEqual(rc.drawdown_from_peak(rc.load_closes(led), date(2026, 8, 4)), 0.0)

    def test_measures_peak_to_trough(self):
        led = _ledger([("2026-08-03T15:00:00", "A", 100.0),
                       ("2026-08-04T15:00:00", "B", -30.0),
                       ("2026-08-05T15:00:00", "C", -20.0)])
        self.assertAlmostEqual(rc.drawdown_from_peak(rc.load_closes(led), date(2026, 8, 5)), -50.0)

    def test_same_session_closes_aggregate(self):
        led = _ledger([("2026-08-05T14:00:00", "RIVN", -68.0),
                       ("2026-08-05T15:00:00", "APLD", -42.0)])
        sessions = rc.session_pnl(rc.load_closes(led))
        self.assertEqual(len(sessions), 1)
        self.assertAlmostEqual(sessions[0][1], -110.0)

    def test_rolling_sum_would_have_missed_this(self):
        """Documents why peak-to-trough was chosen over a rolling sum.

        Across 07-31..08-05 the 5-session SUM is +$64.94 because two big winners
        mask the -$110 day. Peak-to-trough correctly reports -$110.
        """
        led = _ledger([("2026-07-31T15:00:00", "HIMS", 82.94),
                       ("2026-08-03T15:00:00", "SOFI", 38.0),
                       ("2026-08-04T15:00:00", "IREN", 54.0),
                       ("2026-08-05T14:00:00", "RIVN", -68.0),
                       ("2026-08-05T15:00:00", "APLD", -42.0)])
        closes = rc.load_closes(led)
        self.assertAlmostEqual(sum(c["pnl"] for c in closes), 64.94, places=2)
        self.assertAlmostEqual(rc.drawdown_from_peak(closes, date(2026, 8, 5)), -110.0, places=2)


class DrawdownWindowTests(unittest.TestCase):
    """The window is measured in trading days ending on the evaluation date.

    Regression for 2026-10-22: the window used to be the last 5 sessions that
    had closes, so with no trades after 08-06 it froze on the August losses and
    blocked every proposal indefinitely.
    """

    AUG_LOSSES = [("2026-08-03T15:00:00", "SOFI", 38.0),
                  ("2026-08-04T15:00:00", "IREN", 54.0),
                  ("2026-08-05T14:00:00", "RIVN", -68.0),
                  ("2026-08-05T15:00:00", "APLD", -42.0),
                  ("2026-08-06T18:00:00", "IREN", -83.0)]

    def test_window_counts_weekdays(self):
        # Thu 10-22 back five weekdays -> Fri 10-16
        self.assertEqual(rc.window_start(date(2026, 10, 22), 5), date(2026, 10, 16))

    def test_window_from_weekend(self):
        # Sat 10-24: weekend days are not sessions -> Mon 10-19 .. Fri 10-23
        self.assertEqual(rc.window_start(date(2026, 10, 24), 5), date(2026, 10, 19))

    def test_days_without_trades_still_count(self):
        closes = rc.load_closes(_ledger(self.AUG_LOSSES))
        # 08-07: window 08-03..08-07 still holds the whole drawdown
        self.assertAlmostEqual(rc.drawdown_from_peak(closes, date(2026, 8, 7)), -193.0)
        # 08-12: window 08-06..08-12 -- only the -83 day remains
        self.assertAlmostEqual(rc.drawdown_from_peak(closes, date(2026, 8, 12)), -83.0)
        # 08-13: window 08-07..08-13 -- nothing left; the losses have aged out
        self.assertEqual(rc.drawdown_from_peak(closes, date(2026, 8, 13)), 0.0)

    def test_closes_after_as_of_are_ignored(self):
        """Replaying a past date must not see trades that had not happened yet."""
        closes = rc.load_closes(_ledger(self.AUG_LOSSES))
        self.assertEqual(rc.drawdown_from_peak(closes, date(2026, 8, 4)), 0.0)

    def test_frozen_window_no_longer_blocks(self):
        led = _ledger(self.AUG_LOSSES)
        on_0807 = rc.evaluate(rc.TradeProposal("X", "put", 1.0, 1, 4123.88),
                              rc.MarketState(-2.0), mode=rc.Mode.LIVE,
                              ledger_path=led, as_of=date(2026, 8, 7))
        on_1022 = rc.evaluate(rc.TradeProposal("X", "put", 1.0, 1, 4123.88),
                              rc.MarketState(-2.0), mode=rc.Mode.LIVE,
                              ledger_path=led, as_of=date(2026, 10, 22))
        self.assertTrue(any("DRAWDOWN_HALT" in b for b in on_0807.blocks))
        self.assertFalse(any("DRAWDOWN_HALT" in b for b in on_1022.blocks), on_1022.render())


class LossStreakTests(unittest.TestCase):
    def test_counts_across_sessions(self):
        led = _ledger([("2026-08-04T15:00:00", "IREN", 54.0),
                       ("2026-08-05T14:00:00", "RIVN", -68.0),
                       ("2026-08-05T15:00:00", "APLD", -42.0),
                       ("2026-08-06T18:00:00", "IREN", -83.0)])
        self.assertEqual(rc.consecutive_losses(rc.load_closes(led)), 3)

    def test_win_resets(self):
        led = _ledger([("2026-08-05T14:00:00", "A", -10.0),
                       ("2026-08-06T15:00:00", "B", 5.0)])
        self.assertEqual(rc.consecutive_losses(rc.load_closes(led)), 0)


class ModeTests(unittest.TestCase):
    def test_paper_is_default(self):
        led = _ledger([])
        res = rc.evaluate(rc.TradeProposal("X", "call", 1.0, 1, 4200.0),
                          rc.MarketState(2.0), ledger_path=led)
        self.assertFalse(res.allowed)
        self.assertTrue(any("PAPER_MODE" in b for b in res.blocks))

    def test_live_requires_explicit_opt_in(self):
        led = _ledger([])
        res = rc.evaluate(rc.TradeProposal("X", "call", 1.0, 1, 4200.0),
                          rc.MarketState(2.0), mode=rc.Mode.LIVE, ledger_path=led)
        self.assertTrue(res.allowed, res.render())


class HistoricalRegressionTests(unittest.TestCase):
    """The tests that matter: would these controls have caught the real losses?"""

    def test_iren_2026_08_06_blocked_by_chop_alone(self):
        """The -$83 trade. QQQ was +0.07%. Blocked even in LIVE mode."""
        led = _ledger([("2026-08-04T15:00:00", "IREN", 54.0),
                       ("2026-08-05T14:00:00", "RIVN", -68.0),
                       ("2026-08-05T15:00:00", "APLD", -42.0)])
        res = rc.evaluate(
            rc.TradeProposal("IREN", "call", 2.41, 1, 4206.96),
            rc.MarketState(qqq_change_pct=0.07),
            mode=rc.Mode.LIVE,
            ledger_path=led,
            as_of=date(2026, 8, 6),
        )
        self.assertFalse(res.allowed, "the 08-06 trade must not pass")
        self.assertTrue(any("CHOP_REGIME" in b for b in res.blocks))

    def test_iren_2026_08_06_also_blocked_by_drawdown(self):
        """Independently caught: -$110 into the session is 2.61% of equity."""
        led = _ledger([("2026-08-04T15:00:00", "IREN", 54.0),
                       ("2026-08-05T14:00:00", "RIVN", -68.0),
                       ("2026-08-05T15:00:00", "APLD", -42.0)])
        res = rc.evaluate(
            rc.TradeProposal("IREN", "call", 2.41, 1, 4206.96),
            rc.MarketState(qqq_change_pct=1.5),  # neutralise the chop gate
            mode=rc.Mode.LIVE,
            ledger_path=led,
            as_of=date(2026, 8, 6),
        )
        self.assertFalse(res.allowed)
        self.assertTrue(any("DRAWDOWN_HALT" in b for b in res.blocks),
                        f"expected drawdown block, got {res.blocks}")

    def test_clean_setup_in_trending_tape_passes(self):
        """The gates must not block everything -- a real setup still gets through."""
        led = _ledger([("2026-07-29T15:00:00", "IREN", 106.90),
                       ("2026-07-31T15:00:00", "HIMS", 82.94)])
        res = rc.evaluate(
            rc.TradeProposal("SOFI", "call", 0.75, 1, 4200.0),
            rc.MarketState(qqq_change_pct=1.2),
            mode=rc.Mode.LIVE,
            ledger_path=led,
            as_of=date(2026, 7, 31),
        )
        self.assertTrue(res.allowed, res.render())
        self.assertAlmostEqual(res.stop_trigger, 0.64, places=2)

    def test_real_ledger_halted_the_day_after(self):
        """Against the live trades.csv, the account was in a halt state on 08-07."""
        if not Path("trades.csv").exists():
            self.skipTest("trades.csv not present")
        res = rc.evaluate(
            rc.TradeProposal("ANY", "call", 1.00, 1, 4123.88),
            rc.MarketState(qqq_change_pct=2.0),
            mode=rc.Mode.LIVE,
            ledger_path="trades.csv",
            as_of=date(2026, 8, 7),
        )
        self.assertFalse(res.allowed, "3 straight losses + drawdown must halt")
        self.assertTrue(any("DRAWDOWN_HALT" in b for b in res.blocks))

    def test_real_ledger_drawdown_ages_out(self):
        """The 2026-10-22 defect: the August losses must no longer block in October.

        The loss streak still counts across sessions and still warns -- only the
        drawdown window was frozen.
        """
        if not Path("trades.csv").exists():
            self.skipTest("trades.csv not present")
        res = rc.evaluate(
            rc.TradeProposal("ANY", "call", 1.00, 1, 4123.88),
            rc.MarketState(qqq_change_pct=2.0),
            mode=rc.Mode.LIVE,
            ledger_path="trades.csv",
            as_of=date(2026, 10, 22),
        )
        self.assertFalse(any("DRAWDOWN_HALT" in b for b in res.blocks), res.render())


if __name__ == "__main__":
    unittest.main(verbosity=2)
