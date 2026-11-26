"""BAND VIOLATION alert from N1MM_timer: validation, state, lamp and Overview."""

import json
import time
import unittest

from helpers import m, make_lamp, peer, station

import pingpong_bandalert as band
import pingpong_lamp_band as lamp_band

NOW = 1795000000.0  # a fixed "now" (2026-11-18)


def stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def violation(n=1, station="PC2"):
    return {"id": "TEN_MIN:%d" % n, "kind": "TEN_MIN", "signal": "RUN", "station": station,
            "from_band": "20", "to_band": "15", "first_utc": stamp(NOW - 80), "call": "K1AR",
            "qsos": 3, "text": "RUN 20->15 m 14:29 - %d QSOs" % n}


def msg(seq=1, status="violation", items=None, instance="3f9c1a7e", sent=NOW, ttl=10, **over):
    if items is None:
        items = [violation()] if status == "violation" else []
    data = {"type": "n1mm_timer.alert", "v": 1, "instance": instance, "seq": seq,
            "sent_utc": stamp(sent), "ttl_s": ttl, "status": status, "profile": "CQWW-CW-MS",
            "violations": items}
    data.update(over)
    return data


def raw(data):
    return json.dumps(data).encode("utf-8")


class ValidationTest(unittest.TestCase):
    def ok(self, data, now=NOW):
        got, why = band.validate(raw(data) if isinstance(data, dict) else data, now)
        self.assertIsNotNone(got, why)
        return got

    def bad(self, data, now=NOW):
        got, why = band.validate(raw(data) if isinstance(data, dict) else data, now)
        self.assertIsNone(got)
        return why

    def test_good_message(self):
        got = self.ok(msg())
        self.assertEqual(got["violations"][0]["text"], "RUN 20->15 m 14:29 - 1 QSOs")
        self.ok(msg(status="ok"))

    def test_wrong_type_or_version(self):
        self.bad(msg(type="something.else"))
        self.bad(msg(v=2))
        self.bad(msg(v=True))  # a bool is not a version number
        self.bad(msg(v="1"))

    def test_size_and_json(self):
        self.assertIn("too big", self.bad(b"{" + b" " * 1400 + b"}"))
        self.assertEqual(self.bad(b"not json"), "not JSON")
        self.assertEqual(self.bad(b"\xff\xfe"), "not JSON")
        self.bad(b"[1, 2]")
        self.bad(b"")

    def test_field_types(self):
        self.bad(msg(seq=-1))
        self.bad(msg(seq="5"))
        self.bad(msg(instance=""))
        self.bad(msg(ttl_s=0))
        self.bad(msg(status="warning"))
        self.bad(msg(sent_utc="yesterday"))
        self.bad(msg(items=[violation(n) for n in range(6)]))  # more than 5
        self.bad(msg(items=["text"]))
        for key, value in (("kind", "OTHER"), ("text", ""), ("text", "x" * 61), ("id", ""),
                           ("qsos", -1), ("qsos", "3"), ("station", 5), ("first_utc", "soon")):
            item = violation()
            item[key] = value
            self.bad(msg(items=[item]))

    def test_age(self):
        self.ok(msg(sent=NOW - 11))  # ttl 10 + 2 s clock difference allowed
        self.assertIn("too old", self.bad(msg(sent=NOW - 13)))


class StateTest(unittest.TestCase):
    def setUp(self):
        self.a = band.BandAlert()

    def offer(self, data, now=NOW):
        got, why = band.validate(raw(data), now)
        self.assertIsNotNone(got, why)
        return self.a.offer(got, now)

    def test_duplicate_and_old_seq_are_dropped(self):
        self.assertTrue(self.offer(msg(seq=5)))
        self.assertFalse(self.offer(msg(seq=5)))  # a copy (second network)
        self.assertFalse(self.offer(msg(seq=4)))  # out of order
        self.assertTrue(self.offer(msg(seq=6)))

    def test_new_instance_resets_the_seq(self):
        self.assertTrue(self.offer(msg(seq=900)))
        self.assertTrue(self.offer(msg(seq=1, instance="new-run")))  # N1MM_timer restarted

    def test_retired_ok_cannot_clear_restarted_timer_and_monotonic_expiry(self):
        from unittest.mock import patch
        with patch("pingpong_bandalert.time.monotonic", return_value=100) as mono:
            self.offer(msg(seq=7, status="ok"))
            self.offer(msg(instance="restarted"))
            self.assertFalse(self.offer(msg(seq=6, status="ok")))
            self.assertEqual(self.a.footer(NOW)[0], "violation")
            mono.return_value = 111
            self.assertFalse(self.a.online(NOW-1))

    def test_footer_one_violation(self):
        self.offer(msg())
        self.assertEqual(self.a.footer(NOW), ("violation", "BAND VIOLATION: RUN 20->15 m 14:29 - 1 QSOs"))

    def test_footer_three_violations(self):
        self.offer(msg(items=[violation(1), violation(2), violation(3)]))
        kind, text = self.a.footer(NOW)
        self.assertEqual(text, "BAND VIOLATION: RUN 20->15 m 14:29 - 1 QSOs (+2 more)")

    def test_your_station(self):
        self.offer(msg(items=[violation(1, "PC1"), violation(2, "PC2")]))
        self.assertTrue(self.a.footer(NOW, {"pc2"})[1].endswith("YOUR STATION"))  # any listed one
        self.assertNotIn("YOUR STATION", self.a.footer(NOW, {"PC3"})[1])
        self.assertNotIn("YOUR STATION", self.a.footer(NOW, set())[1])

    def test_ok_or_empty_list_clears(self):
        self.offer(msg(seq=1))
        self.offer(msg(seq=2, status="ok"))
        self.assertEqual(self.a.footer(NOW), ("", ""))
        self.offer(msg(seq=3, status="violation", items=[]))
        self.assertEqual(self.a.footer(NOW), ("", ""))

    def test_offline_after_ttl(self):
        self.assertEqual(self.a.footer(NOW), ("", ""))  # never heard: nothing
        self.offer(msg())
        self.assertEqual(self.a.footer(NOW + 10)[0], "violation")
        self.assertEqual(self.a.footer(NOW + 10.5), ("offline", "Band watcher offline"))

    def test_sound_once_per_id(self):
        self.offer(msg(seq=1, items=[violation(1)]))
        self.assertEqual(self.a.new_ids(NOW), ["TEN_MIN:1"])
        self.offer(msg(seq=2, items=[violation(2), violation(1)]))
        self.assertEqual(self.a.new_ids(NOW), ["TEN_MIN:2"])
        self.assertEqual(self.a.new_ids(NOW), [])
        self.offer(msg(seq=1, instance="new-run", items=[violation(1)]))
        self.assertEqual(self.a.new_ids(NOW), ["TEN_MIN:1"])  # new N1MM_timer run

    def test_reset_forgets(self):
        self.offer(msg())
        self.a.reset()
        self.assertEqual(self.a.footer(NOW), ("", ""))


class Label:
    def __init__(self):
        self.text, self.bg, self.mapped = "", "", False

    def configure(self, text="", bg=""):
        self.text, self.bg = text, bg

    def pack(self, **kw):
        self.mapped = True

    def pack_forget(self):
        self.mapped = False

    def winfo_ismapped(self):
        return self.mapped


def band_lamp(**over):
    app = make_lamp(**over)
    app.band, app._band_master, app._band_view = band.BandAlert(), None, None
    app._band_label = Label()
    app._band_footer = lambda: app._band_label
    app.beeps = []
    app._band_sound = lambda: app.beeps.append(1)
    app.relayed = []
    return app


class LampTest(unittest.TestCase):
    def setUp(self):
        self._send = lamp_band.send_relay
        self.sent = []
        lamp_band.send_relay = lambda share, payload: self.sent.append(payload)

    def tearDown(self):
        lamp_band.send_relay = self._send

    def test_loopback_message_is_relayed_and_shown_on_the_master(self):
        app = band_lamp(_i_am_master=True)
        station(app, "10.0.0.1", "PC2")
        data = raw(msg(sent=time.time()))
        app.on_band_local("127.0.0.1", data)
        self.assertEqual(self.sent, [band.BAND_MAGIC + data])  # passed on unchanged
        app._band_update(time.time())
        self.assertTrue(app._band_label.mapped)
        self.assertEqual(app._band_label.text, "BAND VIOLATION: RUN 20->15 m 14:29 - 1 QSOs  ·  YOUR STATION")
        self.assertEqual(app.beeps, [])
        app._band_update(time.time())
        self.assertEqual(app.beeps, [])

    def test_non_loopback_sender_is_refused(self):
        app = band_lamp(_i_am_master=True)
        app.on_band_local("10.0.0.7", raw(msg(sent=time.time())))
        self.assertEqual(self.sent, [])
        self.assertIsNone(app.band.msg)

    def test_not_master_does_not_relay(self):
        app = band_lamp(_i_am_master=False)
        app.on_band_local("127.0.0.1", raw(msg(sent=time.time())))
        self.assertEqual(self.sent, [])
        self.assertIsNone(app.band.msg)

    def test_network_alert_only_from_the_master(self):
        app = band_lamp()
        master = peer(app, "M1", ["10.0.0.9"], alias="RUN1")
        app.on_band("10.0.0.9", raw(msg(sent=time.time())))
        self.assertIsNone(app.band.msg)  # no master known yet
        app._master_peer = master
        app.on_band("10.0.0.5", raw(msg(seq=2, sent=time.time())))
        self.assertIsNone(app.band.msg)  # not from the master
        app.on_band("10.0.0.9", raw(msg(seq=3, sent=time.time())))
        self.assertEqual(app.band.msg["seq"], 3)
        app._band_update(time.time())
        self.assertNotIn("YOUR STATION", app._band_label.text)

    def test_master_change_forgets_the_alert(self):
        app = band_lamp()
        app._master_peer = peer(app, "M1", ["10.0.0.9"])
        app.on_band("10.0.0.9", raw(msg(sent=time.time())))
        app._band_update(time.time())
        self.assertTrue(app._band_label.mapped)
        app._master_peer = peer(app, "M2", ["10.0.0.8"])
        app._band_update(time.time())
        self.assertFalse(app._band_label.mapped)
        self.assertIsNone(app.band.msg)

    def test_sound_setting_is_respected(self):
        app = band_lamp(_i_am_master=True)
        app.var_sound.set(False)
        app.on_band_local("127.0.0.1", raw(msg(sent=time.time())))
        app._band_update(time.time())
        self.assertEqual(app.beeps, [])

    def test_offline_and_clear(self):
        app = band_lamp(_i_am_master=True)
        t = time.time()
        app.on_band_local("127.0.0.1", raw(msg(sent=t)))
        app._band_update(t + 11)
        self.assertEqual((app._band_label.text, app._band_label.bg), ("Band watcher offline", "#5f6368"))
        app.on_band_local("127.0.0.1", raw(msg(seq=2, status="ok", sent=time.time())))
        app._band_update(time.time())
        self.assertFalse(app._band_label.mapped)


class OldLampTest(unittest.TestCase):
    def test_old_parsers_ignore_the_alert(self):
        packet = band.BAND_MAGIC + raw(msg())
        self.assertIsNone(m.parse_command(packet))
        self.assertIsNone(m.parse_relay(packet))
        self.assertFalse(m.parse_hello(packet))
        self.assertIsNone(m.parse_log(packet))


class OverviewTest(unittest.TestCase):
    def test_overview_takes_the_alert_only_from_the_master(self):
        from test_overview import make_overview

        o = make_overview()
        o.band, o.band_master = band.BandAlert(), None
        master = o._lamp("M1")
        master.src = "10.0.0.9"
        master.hello = {"block_time": 30, "ips": ["10.0.0.9"]}
        master.hello_seen = time.time()
        packet = band.BAND_MAGIC + raw(msg(sent=time.time()))
        o._packet("10.0.0.5", packet)
        self.assertEqual(o._band_view(time.time()), ("", ""))  # not the master
        o._packet("10.0.0.9", packet)
        self.assertEqual(o._band_view(time.time())[0], "violation")
        master.hello = {"ips": ["10.0.0.9"]}  # no longer the master
        self.assertEqual(o._band_view(time.time()), ("", ""))


if __name__ == "__main__":
    unittest.main()


class LoopbackSocketTest(unittest.TestCase):
    def test_receives_on_127_0_0_1_and_the_port_is_exclusive(self):
        import socket
        import threading

        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        got, stop = [], threading.Event()
        t = threading.Thread(target=m.loopback_loop, args=(port, lambda s, d: got.append((s, d)), stop))
        t.start()
        try:
            time.sleep(0.2)
            self.assertFalse(m.loopback_loop(port, lambda s, d: None, threading.Event()))  # taken
            out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            out.sendto(b"hello", ("127.0.0.1", port))
            out.close()
            for _ in range(20):
                if got:
                    break
                time.sleep(0.05)
            self.assertEqual(got, [("127.0.0.1", b"hello")])
        finally:
            stop.set()
            t.join(2)
