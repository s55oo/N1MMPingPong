"""Messages and parsing: hello, relay, command, N1MM RadioInfo, bands."""

import unittest

from helpers import m


RADIO = (
    b"<?xml version=\"1.0\"?><RadioInfo><StationName>RUN1</StationName>"
    b"<Freq>1402500</Freq><Mode>CW</Mode><IsTransmitting>True</IsTransmitting>"
    b"<FunctionKeyCaption>F1 CQ</FunctionKeyCaption><IsRunning>True</IsRunning></RadioInfo>"
)


class HelloTest(unittest.TestCase):
    def test_roundtrip_all_fields(self):
        data = m.make_hello(
            "ID1", "OO840G4", ["10.0.0.1", "192.168.56.1"], True,
            color="gold", color_time=12.5, alert=True, alert_band="14", alert_time=99.0,
            status="N1MM 14 CW from 10.0.0.1, Run", alias="RUN1", role="RUN",
            n1mm_master="1", started=5.0, colors="pc:a=red;pc:b=gold", block_time=20,
            alert_run=True, block_time_run=5, urgent=True, urgent_band="14",
            urgent_mode="CW", urgent_time=77.0,
        )
        h = m.parse_hello(data)
        self.assertEqual(h["id"], "ID1")
        self.assertEqual(h["host"], "OO840G4")
        self.assertEqual(h["version"], m.__version__)
        self.assertEqual(h["ips"], ["10.0.0.1", "192.168.56.1"])
        self.assertTrue(h["local_n1mm"])
        self.assertEqual((h["color"], h["color_time"]), ("gold", 12.5))
        self.assertEqual((h["alert"], h["alert_band"], h["alert_time"]), (True, "14", 99.0))
        self.assertEqual((h["alias"], h["role"], h["n1mm_master"]), ("RUN1", "RUN", "1"))
        self.assertEqual(h["colors"], {"pc:a": "red", "pc:b": "gold"})
        self.assertEqual((h["block_time"], h["block_time_run"]), (20.0, 5.0))
        self.assertTrue(h["alert_run"])
        self.assertEqual((h["urgent"], h["urgent_band"], h["urgent_mode"], h["urgent_time"]),
                         (True, "14", "CW", 77.0))

    def test_not_a_hello(self):
        self.assertIsNone(m.parse_hello(b"<RadioInfo/>"))
        self.assertIsNone(m.parse_hello(b"garbage"))
        self.assertIsNone(m.parse_hello(b"<PingPongHello><Host>x</Host></PingPongHello>"))  # no Id


class RelayCommandTest(unittest.TestCase):
    def test_relay_roundtrip(self):
        self.assertEqual(m.parse_relay(m.make_relay("ID1", RADIO)), ("ID1", RADIO))
        self.assertIsNone(m.parse_relay(RADIO))
        self.assertIsNone(m.parse_relay(b"PPR1nolinebreak"))

    def test_command_roundtrip(self):
        c = m.parse_command(m.make_command("update"))
        self.assertEqual((c["cmd"], c["to"]), ("update", "*"))
        self.assertTrue(c["id"])
        self.assertNotEqual(c["id"], m.parse_command(m.make_command("update"))["id"])
        self.assertIsNone(m.parse_command(b"PPC1{broken"))
        self.assertIsNone(m.parse_command(b"PPC1[]"))
        self.assertIsNone(m.parse_command(RADIO))


class RadioInfoTest(unittest.TestCase):
    def test_packet_info(self):
        is_radio, caption, station, tx, freq, mode = m.packet_info(RADIO)
        self.assertTrue(is_radio)
        self.assertEqual((caption, station, tx, freq, mode), ("CQ", "RUN1", True, "1402500", "CW"))
        self.assertTrue(m.radio_running(RADIO))
        self.assertFalse(m.packet_info(b"<contactinfo/>")[0])
        self.assertFalse(m.packet_info(b"not xml")[0])

    def test_bands_and_modes(self):
        self.assertEqual(m.group_of("1402500", "CW"), ("14", "CW"))
        self.assertEqual(m.group_of("701500", "USB"), ("7", "SSB"))
        self.assertEqual(m.group_of("701500", "LSB"), ("7", "SSB"))
        self.assertIsNone(m.group_of("1012000", "CW"))  # 10 MHz not a contest band
        self.assertIsNone(m.group_of("1402500", "FT8"))
        self.assertEqual(m.freq_khz("1402500"), "14025.00 kHz")

    def test_caption_and_block_time(self):
        self.assertEqual(m.clean_caption("F1 CQ"), "CQ")
        self.assertEqual(m.clean_caption("  F12 NULL"), "NULL")
        self.assertEqual(m.clamp_block(0), m.BLOCK_LIMIT)
        self.assertEqual(m.clamp_block(0, m.BLOCK_LIMIT_RUN), m.BLOCK_LIMIT_RUN)
        self.assertEqual(m.clamp_block(99), 30.0)
        self.assertEqual(m.clamp_block(0.2), 1.0)


if __name__ == "__main__":
    unittest.main()
