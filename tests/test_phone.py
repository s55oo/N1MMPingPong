"""Phone lamp: addresses in the lamp header, page state, web server."""

import json
import time
import unittest
import urllib.request

from helpers import MY_IP, make_lamp, station

import pingpong_phone as phone


class AddressTest(unittest.TestCase):
    def test_lan_and_zerotier(self):
        addrs = phone.pick_addresses(
            [("10.147.17.3", "ZeroTier Virtual Port"), ("192.168.1.20", "Intel(R) Ethernet")],
            "192.168.1.20")
        self.assertEqual(addrs, {"lan": "192.168.1.20", "zt": "10.147.17.3"})
        self.assertEqual(phone.address_text(addrs, 8047),
                         "192.168.1.20:8047  ·  10.147.17.3:8047")

    def test_no_zerotier(self):
        addrs = phone.pick_addresses([("192.168.1.20", "Wi-Fi"), ("10.0.0.5", "Ethernet")],
                                     "10.0.0.5")
        self.assertEqual(addrs, {"lan": "10.0.0.5", "zt": ""})
        self.assertEqual(phone.address_text(addrs, 8047), "10.0.0.5:8047")

    def test_route_over_zerotier_still_shows_lan(self):
        addrs = phone.pick_addresses(
            [("10.147.17.3", "ZeroTier Virtual Port"), ("192.168.1.20", "Ethernet")],
            "10.147.17.3")
        self.assertEqual(addrs["lan"], "192.168.1.20")


class Canvas:
    def __init__(self, bg, border):
        self.opts = {"bg": bg, "highlightbackground": border}

    def cget(self, key):
        return self.opts[key]

    def itemcget(self, item, key):
        return "CQ"


class Info:
    def cget(self, key):
        return "RUN1 · transmitting"


def phone_lamp():
    app = make_lamp(phone_port=8047, _phone_req=0.0, _phone_state={}, _phone_addrs={},
                    _phone_error="", _hexcache={}, _who_text="RUN1",
                    _freq_text="14025.00 kHz", _window_bg="SystemButtonFace",
                    text_id=1, tracked=("14", "CW"))
    app.canvas = Canvas("red", "SystemButtonFace")
    app.info = Info()
    app.root.winfo_rgb = lambda c: {"red": (65535, 0, 0)}.get(c, (0, 0, 0))
    return app


class SnapshotTest(unittest.TestCase):
    def test_lamp_and_band_tiles(self):
        app = phone_lamp()
        st = station(app, MY_IP, "RUN1", running=True, tx=True)
        st.msg = "CQ"
        station(app, "10.0.0.5", "INB1", band="7", running=False)
        snap = app._phone_snapshot(time.time())
        self.assertEqual(snap["lamp"]["bg"], "#ff0000")
        self.assertEqual(snap["lamp"]["border"], "")
        self.assertEqual(snap["lamp"]["text"], "CQ")
        self.assertEqual([b["band"] for b in snap["bands"]], ["7 MHz CW", "14 MHz CW"])
        mine = snap["bands"][1]
        self.assertTrue(mine["mine"])
        self.assertEqual((mine["who"], mine["msg"], mine["color"]), ("RUN1", "CQ", "#ff0000"))
        self.assertEqual(snap["bands"][0]["who"], "")

    def test_state_built_only_while_watched(self):
        app = phone_lamp()
        app._phone_tail()
        self.assertEqual(app._phone_state, {})
        app._phone_seen()
        app._phone_tail()
        self.assertIn("lamp", app._phone_state)


class ServerTest(unittest.TestCase):
    def test_page_and_state(self):
        seen = []
        server, err = phone.start_server(0, lambda: {"name": "RUN1"}, lambda: seen.append(1))
        self.assertEqual(err, "")
        try:
            port = server.server_address[1]
            base = "http://127.0.0.1:{}/".format(port)
            self.assertIn(b"PingPong lamp", urllib.request.urlopen(base, timeout=5).read())
            state = json.loads(urllib.request.urlopen(base + "state", timeout=5).read())
            self.assertEqual(state, {"name": "RUN1"})
            self.assertEqual(len(seen), 2)
        finally:
            server.shutdown()
            server.server_close()

    def test_busy_port_reported(self):
        first, _ = phone.start_server(0, dict, lambda: None)
        try:
            second, err = phone.start_server(first.server_address[1], dict, lambda: None)
            if second is not None:  # some systems allow it; then nothing to check
                second.shutdown()
                second.server_close()
            else:
                self.assertIn("busy", err)
        finally:
            first.shutdown()
            first.server_close()


class QrTest(unittest.TestCase):
    def test_sizes_and_finder_patterns(self):
        import pingpong_qr as qr

        for text, size in (("hi", 21), ("http://10.147.17.61:8047/", 25),
                           ("http://192.168.178.123:8047/", 29), ("w" * 100, 41)):
            m = qr.qr_matrix(text)
            self.assertEqual(len(m), size)
            self.assertTrue(all(len(row) == size for row in m))
            # 7x7 finder: dark ring, light ring, dark 3x3 centre (all three corners)
            for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
                self.assertTrue(all(m[r0][c0 + i] for i in range(7)))
                self.assertFalse(m[r0 + 1][c0 + 1])
                self.assertTrue(m[r0 + 3][c0 + 3])

    def test_too_long(self):
        import pingpong_qr as qr

        with self.assertRaises(ValueError):
            qr.qr_matrix("x" * 200)

    def test_known_format_bits(self):
        import pingpong_qr as qr

        # level M, mask 0 = 101010000010010 (QR spec table)
        self.assertEqual(qr._format_bits(0), 0b101010000010010)

    def test_phone_urls(self):
        app = phone_lamp()
        app._phone_addrs = {"lan": "192.168.1.20", "zt": "10.147.17.61"}
        self.assertEqual(app._phone_urls(), [("LAN", "http://192.168.1.20:8047/"),
                                             ("ZeroTier", "http://10.147.17.61:8047/")])
        app._phone_addrs = {"lan": "192.168.1.20", "zt": ""}
        self.assertEqual(len(app._phone_urls()), 1)


if __name__ == "__main__":
    unittest.main()
