"""Lamp rules: F12 calls, urgent calls, ping-pong lock, master, notes, commands."""

import time
import unittest

from helpers import MY_IP, m, make_lamp, peer, station


class F12CallTest(unittest.TestCase):
    def setUp(self):
        self.app = make_lamp()
        self.me = station(self.app, MY_IP, "G4", running=False)
        self.caller = peer(self.app, "A", ["10.0.0.2"], alias="RUN1",
                           alert=True, alert_band="14", alert_time=1000.0, alert_run=False)

    def calls(self):
        return self.app._incoming_calls(time.time())

    def test_sp_call_reaches_sp_only(self):
        self.me.running = False
        self.assertEqual(len(self.calls()), 1)
        self.me.running = True
        self.assertEqual(self.calls(), [])

    def test_run_call_reaches_run_and_sp_with_run_time(self):
        self.caller.alert_run = True
        for running in (False, True):
            self.me.running = running
            self.app._call_seen = {}
            self.assertEqual(len(self.calls()), 1)
            self.assertAlmostEqual(self.app._block_left(self.caller, time.time()), 5.0, delta=0.2)

    def test_sp_call_block_time_and_expiry(self):
        now = time.time()
        self.assertAlmostEqual(self.app._block_left(self.caller, now), 30.0, delta=0.2)
        self.assertEqual(self.app._incoming_calls(now + 31), [])

    def test_other_band_and_silenced(self):
        self.caller.alert_band = "7"
        self.assertEqual(self.calls(), [])
        self.caller.alert_band = "14"
        self.app.silenced.add(("A", 1000.0))
        self.assertEqual(self.calls(), [])

    def test_sp_cannot_call_while_band_busy_run_can(self):
        app = self.app
        app._band_busy = app._band_call(time.time())
        app._f12_pressed()
        self.assertFalse(app.alert_on)
        self.assertGreater(app._f12_refused, 0)
        self.me.running = True
        app._band_busy = app._band_call(time.time())
        app._f12_pressed()
        self.assertTrue(app.alert_on)
        self.assertTrue(app.alert_run)  # pressed in Run: reaches everybody

    def test_my_call_ends_by_itself(self):
        app = self.app
        self.me.running = True
        app._f12_pressed()
        self.assertEqual(app._my_call_left(app.alert_time), 5)
        app._f12_pressed()
        self.assertFalse(app.alert_on)


class UrgentTest(unittest.TestCase):
    def setUp(self):
        self.app = make_lamp()
        self.me = station(self.app, MY_IP, "G4", running=True)

    def test_urgent_reaches_same_band_and_mode_run_or_sp(self):
        app = self.app
        p = peer(app, "A", ["10.0.0.2"], alias="RUN1", urgent=True, urgent_band="14",
                 urgent_mode="CW", urgent_time=5.0)
        for running in (True, False):
            self.me.running = running
            self.assertEqual(len(app._incoming_urgent(time.time())), 1)
        p.urgent_mode = "SSB"
        self.assertEqual(app._incoming_urgent(time.time()), [])
        p.urgent_mode = "CW"
        app.silenced.add(("urgent", "A", 5.0))
        self.assertEqual(app._incoming_urgent(time.time()), [])

    def test_urgent_follows_my_transmission(self):
        app = self.app
        t0 = time.time()
        app._urgent_pressed("F2")
        self.assertTrue(app.urgent_on)
        self.assertEqual((app.urgent_key, app.urgent_fkey), (("14", "CW"), "F2"))
        app._urgent_check(t0 + 1)
        self.assertTrue(app.urgent_on)  # waiting for the transmission
        self.me.tx = True
        app._urgent_check(t0 + 20)
        self.assertTrue(app.urgent_on)  # no time limit while transmitting
        self.me.tx = False
        app._urgent_check(t0 + 21)
        self.assertFalse(app.urgent_on)

    def test_urgent_ends_if_no_transmission(self):
        app = self.app
        app._urgent_pressed("F4")
        app._urgent_check(time.time() + m.URGENT_WAIT + 0.5)
        self.assertFalse(app.urgent_on)

    def test_urgent_needs_f12_option(self):
        app = self.app
        app.f12_on = False
        app._urgent_pressed("F4")
        self.assertFalse(app.urgent_on)


class PingPongTest(unittest.TestCase):
    def setUp(self):
        self.app = make_lamp(my_role="INB")
        self.me = station(self.app, MY_IP, "INB2", running=False)
        self.run = station(self.app, "10.0.0.2", "RUN1", running=True)
        self.inb1 = station(self.app, "10.0.0.3", "INB1", running=False)
        peer(self.app, "R", ["10.0.0.2"], alias="RUN1", role="RUN")
        peer(self.app, "I", ["10.0.0.3"], alias="INB1", role="INB")

    def state(self, now=None):
        return self.app._pingpong_state(now or time.time())

    def test_first_inb_after_run_takes_slot(self):
        app = self.app
        app._pingpong_tx(self.run)
        self.assertFalse(self.state()[0])
        app._pingpong_tx(self.inb1)
        locked, holder = self.state()
        self.assertTrue(locked)
        self.assertIs(holder, self.inb1)
        app._pingpong_tx(self.run)  # RUN transmits: free again
        self.assertFalse(self.state()[0])

    def test_holder_is_not_locked(self):
        app = self.app
        app._pingpong_tx(self.run)
        app._pingpong_tx(self.me)
        self.assertFalse(self.state()[0])
        app._pingpong_tx(self.inb1)  # INB1 too late: slot is mine
        self.assertFalse(self.state()[0])

    def test_lock_releases_after_limit(self):
        app = self.app
        app._pingpong_tx(self.inb1)
        self.assertTrue(self.state()[0])
        self.assertFalse(self.state(time.time() + m.PP_LIMIT + 1)[0])

    def test_no_run_on_band_no_lock(self):
        app = self.app
        self.run.key = ("7", "CW")
        app._pingpong_tx(self.inb1)
        self.assertFalse(self.state()[0])

    def test_lock_keys_include_enter_insert(self):
        app = self.app
        app._pp_locked = True
        self.assertEqual(app._keys_to_block(), m.keys.PINGPONG_KEYS)
        app._pp_locked, app._block_active = False, True
        self.assertIs(app._keys_to_block(), True)
        app._block_active = False
        self.assertIsNone(app._keys_to_block())


class AutoRoleTest(unittest.TestCase):
    def test_auto_follows_n1mm(self):
        app = make_lamp(my_role="auto")
        me = station(app, MY_IP, "G4", running=True)
        self.assertEqual(app._my_role_now(), "RUN")
        me.running = False
        self.assertEqual(app._my_role_now(), "INB")
        me.running = None
        self.assertEqual(app._my_role_now(), "off")
        app.my_role = "RUN"  # fixed role wins
        self.assertEqual(app._my_role_now(), "RUN")

    def test_auto_role_is_announced_and_locks(self):
        app = make_lamp(my_role="auto")
        me = station(app, MY_IP, "INB2", running=False)  # S&P -> INB
        run = station(app, "10.0.0.2", "RUN1", running=True)
        inb1 = station(app, "10.0.0.3", "INB1", running=False)
        peer(app, "R", ["10.0.0.2"], alias="RUN1", role="RUN")
        peer(app, "I", ["10.0.0.3"], alias="INB1", role="INB")
        app._status_text = lambda: ""
        self.assertEqual(app._hello_state()["role"], "INB")
        app._pingpong_tx(run)
        app._pingpong_tx(inb1)
        self.assertTrue(app._pingpong_state(time.time())[0])
        me.running = True  # operator switches N1MM to Run: now RUN, never locked
        self.assertFalse(app._pingpong_state(time.time())[0])
        self.assertEqual(app._hello_state()["role"], "RUN")

    def test_peer_never_announces_auto(self):
        self.assertNotIn("auto", m.SENT_ROLES)


class MasterTest(unittest.TestCase):
    def test_n1mm_master_wins_else_first_started(self):
        app = make_lamp(started=100.0)
        a = peer(app, "A", ["10.0.0.2"], alias="A", started=50.0, n1mm_master=None)
        self.assertIs(app._pick_master(time.time()), a)  # started first
        b = peer(app, "B", ["10.0.0.3"], alias="B", started=200.0, n1mm_master=True)
        self.assertIs(app._pick_master(time.time()), b)  # N1MM's master
        app.n1mm_master = True
        b.n1mm_master = None
        self.assertEqual(app._pick_master(time.time()), "me")

    def test_two_master_warnings(self):
        app = make_lamp(_i_am_master=True, my_alias="RUN1")
        p = peer(app, "A", ["10.0.0.2"], alias="RUN2")
        self.assertEqual(app._master_warning(time.time()), "")
        p.block_time = 30.0
        self.assertIn("TWO MASTERS", app._master_warning(time.time()))
        p.block_time = 0.0
        app.n1mm_master = True
        p.n1mm_master = True
        self.assertIn("N1MM master on", app._master_warning(time.time()))


class NotesTest(unittest.TestCase):
    def test_version_rows(self):
        app = make_lamp()
        peer(app, "A", ["10.0.0.2"], alias="OLD", version="1.0")
        peer(app, "B", ["10.0.0.3"], alias="NEW", version="99.0")
        rows = app._version_rows(time.time())
        self.assertTrue(any("OLD: v1.0 – update it" in r for r in rows))
        self.assertTrue(any("NEW: v99.0 – update this PC" in r for r in rows))

    def test_no_lamp_and_firewall_notes(self):
        app = make_lamp()
        station(app, "10.0.0.5", "PC5")
        self.assertIn("firewall", app._no_lamp_rows(time.time())[0])
        peer(app, "A", ["10.0.0.2"], alias="RUN2")
        self.assertIn("no PingPong lamp there", app._no_lamp_rows(time.time())[0])

    def test_port_warning(self):
        app = make_lamp()
        self.assertEqual(app._port_warning(), "")
        app._port_users[12060] = ["Callbooker.exe"]
        self.assertIn("Callbooker.exe", app._port_warning())


class CommandTest(unittest.TestCase):
    def test_update_only_from_master_pc_once(self):
        app = make_lamp()
        app._master_peer = peer(app, "M", ["10.0.0.9"], alias="MASTER")
        app.on_command("10.0.0.5", m.parse_command(m.make_command("update")))
        self.assertEqual(app._ui_calls, [])  # not from the master PC
        cmd = m.parse_command(m.make_command("update"))
        app.on_command("10.0.0.9", cmd)
        app.on_command("10.0.0.9", cmd)
        self.assertEqual(len(app._ui_calls), 1)

    def test_master_lamp_takes_command_from_own_pc(self):
        app = make_lamp(_i_am_master=True)
        app.on_command(MY_IP, m.parse_command(m.make_command("update")))
        self.assertEqual(len(app._ui_calls), 1)
        app.on_command(MY_IP, m.parse_command(m.make_command("update", to="SOMEONE")))
        self.assertEqual(len(app._ui_calls), 1)


class LockTest(unittest.TestCase):
    def test_network_handlers_hold_the_lock(self):
        """While the window holds the lock, a hello from another thread waits."""
        import threading

        app = make_lamp()
        info = m.parse_hello(m.make_hello("P1", "PC2", ["10.0.0.2"], True, alias="RUN2"))
        done = threading.Event()
        with app.lock:
            t = threading.Thread(target=lambda: (app.on_hello("10.0.0.2", info), done.set()))
            t.start()
            self.assertFalse(done.wait(0.2))  # blocked by the lock
            self.assertNotIn("P1", app.peers)
        self.assertTrue(done.wait(2))
        self.assertIn("P1", app.peers)

    def test_lock_is_reentrant(self):
        app = make_lamp()
        with app.lock:
            app.on_relay("10.0.0.2", "X", b"<RadioInfo/>")  # takes the lock again


class RobustnessTest(unittest.TestCase):
    def test_display_loop_survives_errors(self):
        app = make_lamp()
        logged = []
        orig = m.log_error
        m.log_error = logged.append
        try:
            def boom():
                raise RuntimeError("test")
            app._update_body = boom
            app._update()
        finally:
            m.log_error = orig
        self.assertEqual(logged, ["display"])
        self.assertEqual(app.root.scheduled[-1][0], 120)


if __name__ == "__main__":
    unittest.main()
