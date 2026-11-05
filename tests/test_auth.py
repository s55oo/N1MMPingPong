"""Network password: signed messages on UDP 46070, and spreadsheet-safe exports."""

import os
import socket
import tempfile
import time
import unittest

from helpers import m, make_lamp, peer

import pingpong_net as net


class AuthTest(unittest.TestCase):
    def setUp(self):
        net.set_network_key(b"")
        net.AUTH_SEEN.clear()
        self.key = net.derive_key("cq ww 2026")

    def tearDown(self):
        net.set_network_key(b"")
        net.AUTH_SEEN.clear()

    def test_key_is_derived_not_stored_plain(self):
        self.assertEqual(len(self.key), 32)
        self.assertNotIn(b"cq ww", self.key)
        self.assertEqual(net.derive_key(""), b"")
        self.assertEqual(self.key, net.derive_key("cq ww 2026"))

    def test_no_password_changes_nothing(self):
        msg = m.make_command("update")
        self.assertEqual(net.seal(msg), msg)
        self.assertEqual(net.unseal(msg), (msg, "none"))
        self.assertTrue(net.accepted("none"))

    def test_signed_roundtrip(self):
        net.set_network_key(self.key)
        msg = m.make_command("stop")
        sealed = net.seal(msg)
        self.assertNotEqual(sealed, msg)
        payload, status = net.unseal(sealed, "10.0.0.9")
        self.assertEqual((payload, status), (msg, "ok"))
        self.assertTrue(net.accepted(status))
        self.assertEqual(m.parse_command(payload)["cmd"], "stop")

    def test_wrong_password_rejected_and_reported(self):
        sealed = net.seal(m.make_command("stop"), key=net.derive_key("other"))
        net.set_network_key(self.key)
        _, status = net.unseal(sealed, "10.0.0.66")
        self.assertEqual(status, "bad")
        self.assertFalse(net.accepted(status))
        self.assertEqual(net.auth_problems(time.time()),
                         ["10.0.0.66: different network password – ignored"])

    def test_unsigned_rejected_when_password_set(self):
        net.set_network_key(self.key)
        _, status = net.unseal(m.make_command("stop"), "10.0.0.66")
        self.assertEqual(status, "none")
        self.assertFalse(net.accepted(status))
        self.assertIn("no network password", net.auth_problems(time.time())[0])

    def test_tampered_message_rejected(self):
        net.set_network_key(self.key)
        sealed = net.seal(m.make_command("test"))
        tampered = sealed.replace(b'"test"', b'"stop"')
        self.assertEqual(net.unseal(tampered)[1], "bad")

    def test_replay_after_window_rejected(self):
        net.set_network_key(self.key)
        old = net.seal(m.make_command("stop"), now=time.time() - 3600)
        self.assertEqual(net.unseal(old, "10.0.0.9")[1], "old")
        self.assertIn("wrong time", net.auth_problems(time.time())[0])
        recent = net.seal(m.make_command("stop"), now=time.time() - 60)
        self.assertEqual(net.unseal(recent)[1], "ok")

    def test_lamp_without_password_hears_protected_group(self):
        sealed = net.seal(m.make_hello("A", "PC1", [], True, alias="RUN1"), key=self.key)
        payload, status = net.unseal(sealed, "10.0.0.9")
        self.assertEqual(status, "foreign")
        self.assertTrue(net.accepted(status))
        self.assertEqual(m.parse_hello(payload)["alias"], "RUN1")
        self.assertIn("other lamps use a network password", net.auth_problems(time.time())[0])

    def test_signed_hello_and_relay_parse(self):
        net.set_network_key(self.key)
        for msg in (m.make_hello("A", "PC1", ["10.0.0.9"], True, alias="RUN1"),
                    m.make_relay("A", b"<RadioInfo></RadioInfo>")):
            payload, status = net.unseal(net.seal(msg))
            self.assertEqual((payload, status), (msg, "ok"))

    def test_pong_is_signed(self):
        net.set_network_key(self.key)
        app = make_lamp()
        recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv.bind(("127.0.0.1", 0))
        recv.settimeout(2)
        try:
            app._pong("127.0.0.1", {"seq": 7, "_port": recv.getsockname()[1]})
            data, _ = recv.recvfrom(4096)
        finally:
            recv.close()
        payload, status = net.unseal(data)
        self.assertEqual(status, "ok")
        self.assertEqual(m.parse_pong(payload)["seq"], 7)


class CsvSafeTest(unittest.TestCase):
    def test_formulas_neutralised(self):
        for bad in ("=HYPERLINK(\"x\")", "+1", "-2+3", "@SUM(A1)"):
            self.assertEqual(m.csv_safe(bad), "'" + bad)
        for ok in ("RUN1", "-", "", "1:04:14", 12, None):
            self.assertEqual(m.csv_safe(ok), ok)

    def test_event_log_and_report(self):
        from test_overview import make_overview
        import pingpong_report as report

        o = make_overview()
        o._event("=HYPERLINK(\"http://x\")", "lamp on", "@cmd")
        text = open(o.events_path, encoding="utf-8-sig").read()
        self.assertIn(";'=HYPERLINK", text)
        self.assertIn(";'@cmd", text)
        rows = report.read_events(o.events_path, 0)
        self.assertEqual(rows[0]["station"], "=HYPERLINK(\"http://x\")")  # shown as typed


if __name__ == "__main__":
    unittest.main()
