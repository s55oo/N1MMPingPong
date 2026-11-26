import json
import unittest

import pingpong_contest as contest


class ContestStatusTest(unittest.TestCase):
    now = 36000

    def receive(self, state):
        packet = contest.MAGIC + json.dumps(dict(v=1, sent=self.now, seq=2, instance="abc",
                                                ttl=10, state=state)).encode()
        return contest.decode(packet, self.now)

    def test_timer_clear_expiry_and_local_activity(self):
        msg = self.receive(dict(category="MS", signals={"RUN": {"band": "20", "until": self.now+4},
                                                        "MULT": None}, last={"PC": self.now-50}))
        self.assertEqual(contest.display(msg, self.now, {"pc"}, True),
                         "RUN · 20 m · 0:04 left\nMULT · No QSO yet\nLast QSO · 0:50 ago")
        self.assertIn("Timer clear", contest.display(msg, self.now+4, set(), False))
        self.assertEqual(contest.display(msg, self.now+11, set(), True), "Timer data unavailable")

    def test_hour_reset(self):
        self.now = 35999
        msg = self.receive(dict(category="M2", hour=9, signals={"TX1": {"band": "20", "used": 8},
                                                                "TX2": None}, last={}))
        self.assertIn("8/8", contest.display(msg, self.now, set(), False))
        self.assertIn("Waiting for current-hour", contest.display(msg, self.now+1, set(), False))

    def test_activity_and_invalid_state(self):
        self.assertEqual(contest.display(self.receive(dict(category="activity", signals={}, last={})),
                                         self.now, set(), False), "")
        activity = self.receive(dict(category="activity", signals={}, last={}))
        self.assertEqual(contest.display(activity, self.now+11, set(), False), "")
        conflicting = {"band": "20", "used": 1, "until": "invalid"}
        self.assertIsNone(self.receive(dict(category="M2", hour=10, signals={"TX1": conflicting, "TX2": None}, last={})))
        self.assertIsNone(self.receive(dict(category="MS", signals={}, last={})))
        self.assertEqual(contest.display(self.receive(None), self.now, set(), True), "")


class LampRelayTest(unittest.TestCase):
    def test_master_only_ordering_expiry_and_handoff(self):
        import time
        from unittest.mock import Mock, patch
        from helpers import Var, peer
        from test_band_alert import band_lamp

        now = int(time.time())
        msg = dict(v=1, sent=now, seq=2, instance="timer", ttl=10,
                   state=dict(category="MS", signals={"RUN": {"band": "20", "until": now+120},
                                                       "MULT": None}, last={}))
        packet = contest.MAGIC + json.dumps(msg).encode()
        master = band_lamp(_i_am_master=True)
        lamp = band_lamp(var_last_qso=Var(False), _contest_label=Mock())
        with patch("pingpong_lamp_band.send_relay") as relay:
            master.on_band_local("127.0.0.1", packet)
            relay.assert_called_once_with(master._share, packet)
        lamp._master_peer = peer(lamp, "master", ["10.0.0.2"])
        lamp.on_band("10.0.0.3", packet)
        self.assertIsNone(getattr(lamp, "_contest_msg", None))
        lamp.on_band("10.0.0.2", packet)
        lamp.on_band("10.0.0.2", contest.MAGIC + json.dumps(dict(msg, seq=1, state=None)).encode())
        lamp._band_update_body(now)
        self.assertIn("2:00 left", lamp._contest_text)
        lamp._band_update_body(now + 11)
        self.assertEqual(lamp._contest_text, "Timer data unavailable")
        lamp._master_peer = None
        lamp._band_update_body(now + 12)
        self.assertEqual(lamp._contest_text, "Timer data unavailable")
        lamp._master_peer = peer(lamp, "master", ["10.0.0.2"])
        newer = dict(msg, instance="new", seq=1, state=None)
        lamp.on_band("10.0.0.2", contest.MAGIC + json.dumps(newer).encode())
        lamp.on_band("10.0.0.2", contest.MAGIC + json.dumps(dict(msg, seq=4)).encode())
        lamp._band_update_body(now)
        self.assertEqual(lamp._contest_text, "")

    def test_local_activity_and_clock_correction(self):
        import time
        from unittest.mock import Mock, patch
        from helpers import Var
        from test_band_alert import band_lamp

        lamp = band_lamp(_i_am_master=True, var_last_qso=Var(True),
                         _activity_label=Mock(), _contest_label=Mock())
        now = int(time.time())
        msg = dict(v=1, sent=now, seq=1, instance="timer", ttl=10, state=dict(
            category="activity", signals={}, last={"LOGGER": now-60, "DISPLAY-ALIAS": now-2}))
        packet = contest.MAGIC + json.dumps(msg).encode()
        with patch.dict("os.environ", COMPUTERNAME="LOGGER"), patch.object(
                lamp, "_own_station_names", return_value={"DISPLAY-ALIAS"}), patch(
                "pingpong_lamp_band.time.monotonic", return_value=100) as mono:
            self.assertTrue(lamp._contest_take(packet, "me"))
            lamp._contest_update("me", now)
            self.assertEqual(lamp._contest_text, "Last QSO · 1:00 ago")
            mono.return_value = 111
            lamp._contest_update("me", now-1)
            self.assertEqual(lamp._contest_text, "Timer data unavailable")
