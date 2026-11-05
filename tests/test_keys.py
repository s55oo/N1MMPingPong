"""Keyboard decisions: F12, F-key block, ping-pong lock keys, Shift+F2/F4."""

import unittest

import helpers  # noqa: F401  (sets the import path)
import pingpong_keys as k

N1MM = lambda: "n1mmlogger.net.exe"  # noqa: E731
OTHER = lambda: "notepad.exe"  # noqa: E731
SHIFT = lambda: True  # noqa: E731
NOSHIFT = lambda: False  # noqa: E731
D, U = k.WM_KEYDOWN, k.WM_KEYUP


def watcher(block=None):
    hits = []
    w = k.KeyWatcher(
        lambda: hits.append("F12"),
        should_block=lambda: block,
        on_blocked=lambda name: hits.append("blocked " + name),
        on_urgent=lambda key: hits.append("urgent " + key),
    )
    return w, hits


class F12Test(unittest.TestCase):
    def test_f12_in_n1mm_only_once_per_press(self):
        w, hits = watcher()
        self.assertEqual(w.decide(k.VK_F12, 0, D, N1MM, NOSHIFT), "press")
        self.assertIsNone(w.decide(k.VK_F12, 0, D, N1MM, NOSHIFT))  # auto-repeat
        w.decide(k.VK_F12, 0, U, N1MM, NOSHIFT)
        self.assertEqual(hits, ["F12"])

    def test_f12_in_other_program_ignored(self):
        w, hits = watcher()
        self.assertIsNone(w.decide(k.VK_F12, 0, D, OTHER, NOSHIFT))
        self.assertEqual(hits, [])


class BlockTest(unittest.TestCase):
    def test_f_keys_blocked_during_call(self):
        w, hits = watcher(block=True)
        for vk in range(0x70, 0x7B):  # F1-F11
            self.assertEqual(w.decide(vk, 0, D, N1MM, NOSHIFT), "block")
        self.assertIsNone(w.decide(0x0D, 0, D, N1MM, NOSHIFT))  # Enter free in an F12 call
        self.assertIsNone(w.decide(0x1B, 0, D, N1MM, NOSHIFT))  # Esc always free
        self.assertEqual(w.decide(k.VK_F12, 0, D, N1MM, NOSHIFT), "press")  # F12 never blocked

    def test_alt_and_other_programs_never_blocked(self):
        w, _ = watcher(block=True)
        self.assertIsNone(w.decide(0x70, k.LLKHF_ALTDOWN, k.WM_SYSKEYDOWN, N1MM, NOSHIFT))
        self.assertIsNone(w.decide(0x70, 0, D, OTHER, NOSHIFT))

    def test_pingpong_lock_also_blocks_enter_insert(self):
        w, hits = watcher(block=k.PINGPONG_KEYS)
        self.assertEqual(w.decide(0x0D, 0, D, N1MM, NOSHIFT), "block")
        self.assertEqual(w.decide(0x2D, 0, D, N1MM, NOSHIFT), "block")
        self.assertEqual(w.decide(0x70, 0, D, N1MM, NOSHIFT), "block")
        self.assertIn("blocked Enter", hits)

    def test_nothing_blocked_normally(self):
        w, _ = watcher(block=None)
        self.assertIsNone(w.decide(0x70, 0, D, N1MM, NOSHIFT))


class UrgentKeyTest(unittest.TestCase):
    def test_shift_f2_f4_start_urgent_and_pass_to_n1mm(self):
        w, hits = watcher()
        for vk, name in ((k.VK_F2, "F2"), (k.VK_F4, "F4")):
            self.assertIsNone(w.decide(vk, 0, D, N1MM, SHIFT))  # N1MM still gets it
            self.assertIsNone(w.decide(vk, 0, D, N1MM, SHIFT))  # auto-repeat: once
            w.decide(vk, 0, U, N1MM, SHIFT)
        self.assertEqual(hits, ["urgent F2", "urgent F4"])

    def test_plain_f4_and_shift_f3_are_not_urgent(self):
        w, hits = watcher()
        w.decide(k.VK_F4, 0, D, N1MM, NOSHIFT)
        w.decide(0x72, 0, D, N1MM, SHIFT)
        self.assertEqual(hits, [])

    def test_urgent_not_from_other_programs(self):
        w, hits = watcher()
        w.decide(k.VK_F4, 0, D, OTHER, SHIFT)
        self.assertEqual(hits, [])

    def test_urgent_still_sent_while_f_keys_blocked(self):
        w, hits = watcher(block=True)
        self.assertEqual(w.decide(k.VK_F4, 0, D, N1MM, SHIFT), "block")
        self.assertIn("urgent F4", hits)


if __name__ == "__main__":
    unittest.main()
