"""Windows-only parts, checked on GitHub's Windows build runner.

Skipped elsewhere. They cover what the other tests can't: the adapter list
(LAN / ZeroTier addresses for the phone lamp), the one-program-per-PC
mutex and the firewall rule lookup.
"""

import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pingpong_common as common  # noqa: E402
import pingpong_net as net  # noqa: E402

WINDOWS = sys.platform == "win32"


@unittest.skipUnless(WINDOWS, "Windows only")
class AdaptersTest(unittest.TestCase):
    def test_adapter_list(self):
        adapters = net._windows_adapters()
        self.assertTrue(adapters, "no IPv4 adapter found")
        for ip, descr in adapters:
            self.assertTrue(net._usable_ip(ip), ip)
            self.assertIsInstance(descr, str)
        self.assertTrue(any(descr for _, descr in adapters), "no adapter description read")

    def test_phone_addresses_have_lan(self):
        addrs = net.phone_addresses()
        self.assertTrue(addrs["lan"], addrs)
        self.assertNotEqual(addrs["lan"], addrs["zt"])


@unittest.skipUnless(WINDOWS, "Windows only")
class MutexTest(unittest.TestCase):
    def test_claim_and_detect(self):
        name = "Local\\PingPongTest" + uuid.uuid4().hex
        self.assertFalse(common.instance_running(name))
        saved = common._instance_mutex
        try:
            self.assertTrue(common.claim_single_instance(0.0, name))
            self.assertTrue(common.instance_running(name))
            self.assertFalse(common.instance_running(name + "x"))
        finally:
            common._instance_mutex = saved


@unittest.skipUnless(WINDOWS, "Windows only")
class FirewallTest(unittest.TestCase):
    def test_missing_rule(self):
        self.assertIs(net.firewall_rule_exists("PingPong test " + uuid.uuid4().hex), False)


@unittest.skipUnless(WINDOWS, "Windows only")
class SendKeyTest(unittest.TestCase):
    def test_press_key_structures(self):
        import pingpong_keys as keys

        # F24 - a key nothing uses; checks the SendInput structures load and
        # run (on a runner without a desktop Windows may refuse: False).
        self.assertIn(keys.press_key(0x87), (True, False))
        self.assertIn(keys.ctrl_down(), (True, False))


@unittest.skipUnless(WINDOWS, "Windows only")
class DarkTitleBarTest(unittest.TestCase):
    def test_runs_on_a_real_window(self):
        import tkinter as tk

        import n1mm_lamps as lamps

        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("no desktop on this runner")
        try:
            app = lamps.DisplayApp.__new__(lamps.DisplayApp)
            app.root = root
            app._dark_titlebar()  # must never raise, whatever Windows this is
        finally:
            root.destroy()


class NotWindowsTest(unittest.TestCase):
    @unittest.skipIf(WINDOWS, "not on Windows")
    def test_safe_answers_elsewhere(self):
        self.assertIsNone(net.firewall_rule_exists("anything"))
        self.assertFalse(common.instance_running("Local\\PingPongLamp"))
        self.assertTrue(common.claim_single_instance(0.0, "Local\\PingPongTest"))


if __name__ == "__main__":
    unittest.main()
