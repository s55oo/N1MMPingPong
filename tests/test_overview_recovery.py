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
        with self.assertRaises(log.NoTransaction):
            log.latest_transaction([self.root], self.now + 2*86400)

    def test_date_and_radio_checks(self):
        self.assertEqual(wire.decode(self.recovery.packet(self.now, False), self.now)["state"]["reason"],
                         "radio_offline")
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE ContestInstance SET StartDate='2025-11-29'")
        db.close()
        state = wire.decode(self.recovery.packet(self.now, True), self.now)["state"]
        self.assertEqual(state["category"], "unknown")
        self.assertEqual(state["reason"], "contest_dates")

    def test_legacy_filename_requires_unique_local_database(self):
        import shutil
        self.trn.rename(self.folder / "CQWWCW - 2026-11-28 - 38.trn")
        self.assertEqual(log.latest_transaction([self.root], self.now)[0][0], str(self.db.resolve()))
        shutil.copyfile(self.db, self.root / "backup.s3db")
        with self.assertRaises(log.NoTransaction):
            log.latest_transaction([self.root], self.now)

    def test_unparseable_database_date_is_rejected(self):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE ContestInstance SET StartDate='2026-11-28(GARBAGE)'")
        db.close()
        state = wire.decode(self.recovery.packet(self.now, True), self.now)["state"]
        self.assertEqual(state["reason"], "contest_dates")

    def test_malformed_fields_do_not_stop_recovery(self):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE ContestInstance SET OperatorCategory=42")
            db.execute("UPDATE DXLOG SET NetBiosName=X'52554E'")
        db.close()
        self.assertEqual(wire.decode(self.recovery.packet(self.now, True), self.now)["state"]["category"], "unknown")
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE ContestInstance SET OperatorCategory='MULTI-OP'")
        db.close()
        state = wire.decode(self.recovery.packet(self.now, True), self.now)["state"]
        self.assertEqual((state["category"], state["last"]), ("M2", {}))

    def test_delayed_native_packet_suppresses_fallback_only_for_remaining_ttl(self):
        import json
        from test_overview import make_overview, hello
        app = make_overview()
        app._contest_instance = "fallback"
        with patch("time.time", return_value=self.now), patch("time.monotonic", return_value=100):
            hello(app, "master", "LOCAL", "RUN", block_time=30)
            packet = dict(v=1, instance="native", sent=self.now-9, ttl=10, seq=2, state=None)
            app._packet("10.0.0.9", wire.MAGIC + json.dumps(packet).encode())
            self.assertEqual(app._contest_source, ("master", 101))

    def test_locked_legacy_database_preserves_warning_history(self):
        self.trn.rename(self.folder / "CQWWCW - 2026-11-28 - 38.trn")
        self.recovery.packet(self.now, True)
        history = self.recovery.reader.warnings
        with sqlite3.connect(self.db) as writer:
            writer.execute("BEGIN EXCLUSIVE")
            self.assertIsNone(self.recovery.packet(self.now, True))
            self.assertIs(self.recovery.reader.warnings, history)
            self.assertIn("finish writing", self.recovery.note)
        writer.close()
        state = wire.decode(self.recovery.packet(self.now+2, True), self.now+2)["state"]
        self.assertEqual(state["category"], "M2")
        self.assertIs(self.recovery.reader.warnings, history)
