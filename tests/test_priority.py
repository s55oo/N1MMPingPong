"""Ctrl+F2 / Ctrl+F4 priority call: others stop (Esc) and wait, then my F2/F4."""

import time
import unittest

from helpers import MY_IP, m, make_lamp, peer, station

import pingpong_keys as k
import pingpong_rules as rules

N1MM = lambda: "n1mmlogger.net.exe"  # noqa: E731
CTRL = lambda: True  # noqa: E731
NOCTRL = lambda: False  # noqa: E731
D, U = k.WM_KEYDOWN, k.WM_KEYUP


def watcher(block=None):
    hits = []
    w = k.KeyWatcher(lambda: hits.append("F12"), should_block=lambda: block,
                     on_blocked=lambda name: hits.append("blocked " + name),
                     on_urgent=lambda key: hits.append("urgent " + key),
                     on_priority=lambda key: hits.append("priority " + key))
    return w, hits


class KeyTest(unittest.TestCase):
    def test_ctrl_f4_is_swallowed_and_starts_priority_once(self):
        w, hits = watcher()
        self.assertEqual(w.decide(k.VK_F4, 0, D, N1MM, ctrl=CTRL), "block")
        self.assertEqual(w.decide(k.VK_F4, 0, D, N1MM, ctrl=CTRL), "block")  # auto-repeat
        self.assertEqual(w.decide(k.VK_F4, 0, U, N1MM, ctrl=CTRL), "block")  # its key-up too
        self.assertEqual(hits, ["priority F4"])

    def test_plain_and_shift_f4_untouched(self):
        w, hits = watcher()
        self.assertIsNone(w.decide(k.VK_F4, 0, D, N1MM, shift=NOCTRL, ctrl=NOCTRL))
        self.assertIsNone(w.decide(k.VK_F4, 0, U, N1MM, shift=NOCTRL, ctrl=NOCTRL))
        self.assertIsNone(w.decide(k.VK_F2, 0, D, N1MM, shift=CTRL, ctrl=NOCTRL))
        self.assertIsNone(w.decide(k.VK_F2, 0, U, N1MM, shift=CTRL, ctrl=NOCTRL))
        self.assertEqual(hits, ["urgent F2"])

    def test_only_in_n1mm(self):
        w, hits = watcher()
        self.assertIsNone(w.decide(k.VK_F2, 0, D, lambda: "notepad.exe", shift=NOCTRL, ctrl=CTRL))
        self.assertEqual(hits, [])

    def test_own_injected_keys_never_blocked(self):
        w, hits = watcher(block=k.PINGPONG_KEYS)
        self.assertIsNone(w.decide(k.VK_F4, k.LLKHF_INJECTED, D, N1MM, ctrl=NOCTRL))
        self.assertEqual(w.decide(0x70, 0, D, N1MM, ctrl=NOCTRL), "block")  # typed F1 still held


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.pressed = []
        self.saved = (k.press_key, k.n1mm_active, k.ctrl_down, rules.PRIORITY_DELAY)
        k.press_key = lambda vk, release_ctrl=False: self.pressed.append((vk, release_ctrl)) or True
        k.n1mm_active = lambda: True
        k.ctrl_down = lambda: False
        rules.PRIORITY_DELAY = 0.0

    def tearDown(self):
        k.press_key, k.n1mm_active, k.ctrl_down, rules.PRIORITY_DELAY = self.saved

    def test_my_priority_call(self):
        app = make_lamp()
        station(app, MY_IP, "RUN1", band="7", running=True)
        app._priority_pressed("F4")
        self.assertTrue(app.urgent_on and app.urgent_priority)
        self.assertEqual(app.urgent_key, ("7", "CW"))
        self.assertTrue(app._hello_state()["urgent_priority"])
        self.assertTrue(app.hello_now.is_set())
        for _ in range(50):  # the lamp sends F4 itself, a moment later
            if self.pressed:
                break
            time.sleep(0.02)
        self.assertEqual(self.pressed, [(k.VK_F4, False)])

    def test_f4_not_sent_if_n1mm_not_active(self):
        k.n1mm_active = lambda: False
        app = make_lamp()
        app._send_priority_key("F2", time.time())
        self.assertEqual(self.pressed, [])
        self.assertTrue(any("not sent" in e for e in app.events))

    def caller(self, app, band="7", mode="CW"):
        return peer(app, "P", ["10.0.0.2"], alias="RUN1", urgent=True, urgent_priority=True,
                    urgent_band=band, urgent_mode=mode, urgent_time=1000.0)

    def test_others_get_esc_once_and_keys_held(self):
        app = make_lamp()
        mine = station(app, MY_IP, "INB1", band="7", running=False, tx=True)
        p = self.caller(app)
        app._priority_heard(p, time.time())
        app._priority_heard(p, time.time())  # next hello of the same call
        self.assertEqual(self.pressed, [(k.VK_ESCAPE, False)])
        self.assertEqual(app._keys_to_block(), k.PINGPONG_KEYS)
        self.assertTrue(app._priority_active(time.time()))
        mine.tx = False
        p.urgent = False  # caller's message sent
        self.assertFalse(app._priority_active(time.time()))

    def test_not_transmitting_no_esc(self):
        app = make_lamp()
        station(app, MY_IP, "INB1", band="7", running=False, tx=False)
        app._priority_heard(self.caller(app), time.time())
        self.assertEqual(self.pressed, [])
        self.assertTrue(app._priority_block)

    def test_other_band_or_mode_untouched(self):
        for band, mode in (("14", "CW"), ("7", "SSB")):
            app = make_lamp()
            station(app, MY_IP, "INB1", band="7", running=False, tx=True)
            app._priority_heard(self.caller(app, band, mode), time.time())
            self.assertEqual(self.pressed, [])
            self.assertFalse(app._priority_block)

    def test_flashes_like_urgent(self):
        app = make_lamp()
        station(app, MY_IP, "INB1", band="7", running=False)
        self.caller(app)
        calls = app._incoming_urgent(time.time())
        self.assertEqual([c.alias for c in calls], ["RUN1"])

    def test_hello_field(self):
        info = m.parse_hello(m.make_hello("A", "PC", [], True, urgent=True, urgent_band="7",
                                          urgent_mode="CW", urgent_priority=True))
        self.assertTrue(info["urgent_priority"])
        self.assertFalse(m.parse_hello(m.make_hello("A", "PC", [], True))["urgent_priority"])


if __name__ == "__main__":
    unittest.main()
