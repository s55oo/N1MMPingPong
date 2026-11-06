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
        net.unseal(net.seal(m.make_hello("B", "PC2", [], True)), "10.0.0.5")  # others still sign
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


class JoinTest(unittest.TestCase):
    """A lamp without (or with an old) password asks for it and checks it."""

    def setUp(self):
        net.set_network_key(b"")
        net.AUTH_SEEN.clear()
        del net.AUTH_SAMPLES[:]
        net.AUTH_NAMES.clear()
        self.key = net.derive_key("cqww")
        hello = m.make_hello("M", "PC1", ["10.0.0.9"], True, alias="RUN1")
        net.unseal(net.seal(hello, key=self.key), "10.0.0.9")  # heard from the master

    def tearDown(self):
        net.set_network_key(b"")
        net.AUTH_SEEN.clear()
        del net.AUTH_SAMPLES[:]

    def test_needed_and_checked(self):
        self.assertEqual(net.password_needed(time.time()), (True, "RUN1"))
        self.assertTrue(net.password_matches("cqww"))
        self.assertFalse(net.password_matches("wrong"))
        net.set_network_key(self.key)
        self.assertFalse(net.password_needed(time.time())[0])

    def test_no_sample_cannot_check(self):
        del net.AUTH_SAMPLES[:]
        self.assertIsNone(net.password_matches("cqww"))

    def lamp(self, answers):
        import pingpong_lamp_menu as menu

        class Settings:
            values = {}

            def set_text(self, sec, key, value):
                self.values[(sec, key)] = value

        app = make_lamp(settings=Settings())
        asked = []
        self.saved = (menu.simpledialog.askstring, menu.messagebox.showinfo)
        menu.simpledialog.askstring = lambda *a, **k: (asked.append(a[1]), answers.pop(0))[1]
        menu.messagebox.showinfo = lambda *a, **k: None
        self.addCleanup(self.restore, menu)
        return app, asked

    def restore(self, menu):
        menu.simpledialog.askstring, menu.messagebox.showinfo = self.saved

    def test_wrong_then_right(self):
        app, asked = self.lamp(["nope", "cqww"])
        app._ask_network_password()
        self.assertEqual(len(asked), 2)
        self.assertIn("Wrong password", asked[1])
        self.assertEqual(net.AUTH["key"], self.key)
        self.assertEqual(app.settings.values[("network", "auth_key")], self.key.hex())
        self.assertTrue(app.hello_now.is_set())

    def test_cancel_changes_nothing(self):
        app, _ = self.lamp([None])
        app._ask_network_password()
        self.assertEqual(net.AUTH["key"], b"")

    def test_red_line_and_popup_only_after_start(self):
        app = make_lamp(started=time.time())
        lines = app._password_warnings(time.time())
        self.assertIn("click here", lines[0])
        self.assertIn("RUN1", lines[0])
        self.assertEqual(len(app.root.scheduled), 1)  # one pop-up, right after start
        app._password_warnings(time.time())
        self.assertEqual(len(app.root.scheduled), 1)
        late = make_lamp(started=time.time() - 3600)  # mid-contest: never a pop-up
        late._password_warnings(time.time())
        self.assertEqual(late.root.scheduled, [])

    def test_names_in_warnings(self):
        net.set_network_key(net.derive_key("other"))
        net.unseal(net.seal(m.make_hello("C", "PC3", [], True)), "10.0.0.7")  # others still sign
        net.unseal(m.make_hello("B", "PC2", ["10.0.0.5"], True, alias="INB2"), "10.0.0.5")
        texts = net.auth_problems(time.time())
        self.assertIn("INB2 (10.0.0.5): no network password – ignored", texts)


class RemovedTest(unittest.TestCase):
    """The master dropped the password: lamps that still have it offer to remove it."""

    def setUp(self):
        net.AUTH_SEEN.clear()
        net.AUTH_NAMES.clear()
        net.set_network_key(net.derive_key("cqww"))
        self.unsigned = m.make_hello("M", "PC1", ["10.0.0.9"], True, alias="RUN1")

    def tearDown(self):
        net.set_network_key(b"")
        net.AUTH_SEEN.clear()

    def test_removed_when_only_unsigned_heard(self):
        net.unseal(self.unsigned, "10.0.0.9")
        self.assertEqual(net.password_removed(time.time()), (True, "RUN1"))
        self.assertEqual(net.auth_problems(time.time()), [])  # shown as "removed" instead

    def test_not_removed_while_others_still_sign(self):
        net.unseal(self.unsigned, "10.0.0.9")
        net.unseal(net.seal(m.make_hello("B", "PC2", [], True, alias="INB1")), "10.0.0.5")
        self.assertFalse(net.password_removed(time.time())[0])
        self.assertIn("no network password", net.auth_problems(time.time())[0])

    def test_own_signed_echo_does_not_count(self):
        net.unseal(self.unsigned, "10.0.0.9")
        net.unseal(net.seal(m.make_hello("ME", "PCX", [], True)), "10.0.0.1")
        self.assertTrue(net.password_removed(time.time(), local={"10.0.0.1"})[0])

    def lamp(self, answer):
        import pingpong_lamp_menu as menu

        class Settings:
            values = {}

            def set_text(self, sec, key, value):
                self.values[(sec, key)] = value

        saved = menu.messagebox.askyesno
        menu.messagebox.askyesno = lambda *a, **k: answer
        self.addCleanup(setattr, menu.messagebox, "askyesno", saved)
        return make_lamp(settings=Settings(), started=time.time())

    def test_yes_removes(self):
        net.unseal(self.unsigned, "10.0.0.9")
        app = self.lamp(True)
        lines = app._password_warnings(time.time())
        self.assertIn("no longer uses a password", lines[0])
        self.assertEqual(len(app.root.scheduled), 1)  # asked once, right after start
        app._offer_password_removal()
        self.assertEqual(net.AUTH["key"], b"")
        self.assertEqual(app.settings.values[("network", "auth_key")], "")

    def test_no_keeps_it(self):
        net.unseal(self.unsigned, "10.0.0.9")
        app = self.lamp(False)
        app._offer_password_removal()
        self.assertNotEqual(net.AUTH["key"], b"")


if __name__ == "__main__":
    unittest.main()
