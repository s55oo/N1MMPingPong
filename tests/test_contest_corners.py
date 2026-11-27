"""CQ WW status on the lamp's lower corners: RUN / TX1 left, MULT / TX2 right."""
import json
import time
import unittest
from unittest.mock import Mock

import pingpong_contest as contest


class SplitCornersTest(unittest.TestCase):
    def test_multi_single(self):
        self.assertEqual(
            contest.split_corners("RUN · 20 m · 6:12 left\nMULT · 15 m · Timer clear\nLast QSO · 0:50 ago"),
            ("RUN 20 m · 6:12", "MULT 15 m · clear", "Last QSO · 0:50 ago"))

    def test_multi_two(self):
        self.assertEqual(
            contest.split_corners("TX1 · 20 m · 7/8 changes · 1 change left\nTX2 · 40 m · 0/8 changes"),
            ("TX1 20 m · 7/8 · 1 left", "TX2 40 m · 0/8", ""))
        self.assertEqual(contest.split_corners("TX1 · 160 m · 9/8 changes")[0], "TX1 160 m · 9/8")

    def test_no_qso_yet(self):
        self.assertEqual(contest.split_corners("RUN · No QSO yet\nMULT · No new multiplier yet"),
                         ("RUN · no QSO yet", "MULT · no mult yet", ""))

    def test_corner_is_fixed_by_signal_not_by_order(self):
        self.assertEqual(contest.split_corners("MULT · 15 m · 1:00 left"), ("", "MULT 15 m · 1:00", ""))

    def test_warnings_stay_under_the_lamp(self):
        for text in ("Timer data unavailable", "Waiting for current-hour counts",
                     "N1MM feed unavailable · check Radio on the master"):
            self.assertEqual(contest.split_corners(text), ("", "", text))
        self.assertEqual(
            contest.split_corners("RUN · Check log · band order unclear\nMULT · 15 m · Timer clear"),
            ("", "MULT 15 m · clear", "RUN · Check log · band order unclear"))
        self.assertEqual(contest.split_corners(""), ("", "", ""))

    def test_real_display_output_fits(self):
        now = 36000
        packet = contest.MAGIC + json.dumps(dict(v=1, sent=now, seq=2, instance="abc", ttl=10, state=dict(
            category="MS", signals={"RUN": {"band": "160", "until": now + 600},
                                    "MULT": {"band": "10", "until": now + 599}}, last={}))).encode()
        left, right, rest = contest.split_corners(contest.display(contest.decode(packet, now), now, set(), False))
        self.assertEqual((left, right, rest), ("RUN 160 m · 10:00", "MULT 10 m · 9:59", ""))
        self.assertLessEqual(len(left) + len(right), 40)  # both fit side by side on the lamp


class LampCornersTest(unittest.TestCase):
    def test_lamp_puts_signals_on_corners_and_keeps_the_rest_below(self):
        from helpers import Var
        from test_band_alert import band_lamp

        now = int(time.time())
        msg = dict(v=1, sent=now, seq=2, instance="timer", ttl=10,
                   state=dict(category="MS", signals={"RUN": {"band": "20", "until": now + 120},
                                                       "MULT": None}, last={}))
        lamp = band_lamp(var_last_qso=Var(False), _contest_label=Mock())
        lamp._contest_take(contest.MAGIC + json.dumps(msg).encode(), "me")
        lamp._contest_update("me", now)
        left, right = lamp._contest_corners
        self.assertTrue(left.startswith("RUN 20 m · "))
        self.assertEqual(right, "MULT · no mult yet")
        self.assertIn("RUN · 20 m", lamp._contest_text)  # the full text is still kept
        lamp._contest_label.pack_forget.assert_called()  # nothing left for the row under the lamp


if __name__ == "__main__":
    unittest.main()
