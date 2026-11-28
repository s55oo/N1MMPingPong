import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pingpong_contest as wire
import pingpong_contest_log as log
from test_band_alert import band_lamp


class OverviewRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.folder = self.root / "TransactionLogFiles"
        self.folder.mkdir()
        self.db = self.root / "Station - main.s3db"
        with sqlite3.connect(self.db) as db:
            db.executescript("""
                CREATE TABLE ContestInstance(ContestNR, ContestName, OperatorCategory,
                    TransmitterCategory, StationCategory, StartDate);
                INSERT INTO ContestInstance VALUES (38,'CQWWCW','MULTI-OP','TWO','','2026-11-28');
                CREATE TABLE DXLOG(ContestNR, TS, ID, Band, Freq, Run1Run2, NetBiosName);
                INSERT INTO DXLOG VALUES(38,'2026-11-28 12:01:00','abc',14,14025,1,'RUN');
            """)
        db.close()
        self.trn = self.folder / "Station - main.s3db – CQWWCW – 2026-11-28 – 38.TRN"
        self.trn.touch()
        self.now = log.utc("2026-11-28 12:05:00")
        self.recovery = log.OverviewRecovery(self.root)
        self.addCleanup(self.recovery.reader.close)

    def test_discovery_feeds_existing_master_and_clients(self):
        packet = self.recovery.packet(self.now, True)
        master = band_lamp(_i_am_master=True)
        client = band_lamp()
        with patch("time.time", return_value=self.now), patch("pingpong_lamp_band.send_relay") as relay:
            master.on_band_local("127.0.0.1", packet)
            relay.assert_called_once_with(master._share, packet)
            self.assertTrue(client._contest_take(packet, "master"))
            self.assertEqual(client._contest_msg["state"]["signals"]["TX1"], {"band":"20","used":0})
            master._contest_selection = ("other.s3db", "1", "OTHER")
            master.on_band_local("127.0.0.1", self.recovery.packet(self.now, True))
            self.assertEqual(relay.call_count, 1)

    def test_newest_file_controls_recovery_including_ties_and_switches(self):
        self.assertIsNotNone(self.recovery.packet(self.now, True))
        other = self.folder / "other.s3db - OTHER - 2026-11-28 - 2.trn"
        other.touch()
        stamp = self.trn.stat().st_mtime_ns
        os.utime(other, ns=(stamp, stamp))
        self.assertIsNone(wire.decode(self.recovery.packet(self.now, True), self.now)["state"])
        os.utime(other, ns=(stamp + 1000000000, stamp + 1000000000))
        self.assertIsNone(self.recovery.packet(self.now, True))
        other.unlink()
        self.assertIsNotNone(self.recovery.packet(self.now, True))
        self.assertIsNone(log.latest_transaction([self.root], self.now + 2*86400))

    def test_date_and_radio_checks(self):
        self.assertEqual(wire.decode(self.recovery.packet(self.now, False), self.now)["state"]["reason"],
                         "radio_offline")
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE ContestInstance SET StartDate='2025-11-29'")
        db.close()
        state = wire.decode(self.recovery.packet(self.now, True), self.now)["state"]
        self.assertEqual(state["category"], "unknown")
        self.assertEqual(state["reason"], "contest_dates")
