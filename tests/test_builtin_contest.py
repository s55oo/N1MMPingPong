from pathlib import Path
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import pingpong_contest as wire
import pingpong_contest_log as log
from test_band_alert import band_lamp
from test_messages import RADIO
from helpers import Var


class BuiltinContestTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "test.s3db"
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE ContestInstance(ContestNR, ContestName, OperatorCategory,
                TransmitterCategory, StationCategory, StartDate);
            CREATE TABLE DXLOG(ContestNR, TS, ID, Band, Freq, Run1Run2, NetBiosName);
            INSERT INTO ContestInstance VALUES (1,'CQWWCW','MULTI-OP','ONE','','2026-11-28');
            INSERT INTO ContestInstance VALUES (2,'CQWWSSB','MULTI-OP','TWO','','2026-11-28');
            """)
        self.reader = log.LogReader()
        self.addCleanup(self.reader.close)
        self.at = log.utc("2026-11-28 14:05:00")
        self.selected = (self.path.name, "1", "CQWWCW")

    def read(self):
        return self.reader.read(self.selected, self.path.parent, self.at)

    def qso(self, nr, stamp, band, tx, pc="RUN"):
        self.db.execute("INSERT INTO DXLOG VALUES (?,?,?,?,?,?,?)",
                        (nr, "2026-11-28 " + stamp, stamp + pc, band, band * 1000, tx, pc))
        self.db.commit()

    def test_read_correct_switch_edit_and_delete(self):
        self.qso(1, "14:00:58", 14, 1)
        self.qso(1, "14:04:00", 14, 1, "INB")
        self.qso(1, "14:02:00", 21, 2, "MULT")
        state = self.read()
        self.assertEqual(state["signals"]["RUN"], {"band": "20", "until": self.at + 300})
        self.assertEqual(state["signals"]["MULT"], {"band": "15", "until": self.at + 420})
        self.assertIn("INB", state["last"])
        self.db.execute("DELETE FROM DXLOG WHERE NetBiosName='RUN'")
        self.db.commit()
        self.assertEqual(self.read()["signals"]["RUN"]["until"], self.at + 540)
        self.selected = (self.path.name, "2", "CQWWSSB")
        self.assertEqual(self.read()["signals"], {"TX1": None, "TX2": None})
        self.qso(2, "13:59:00", 14, 1)
        self.qso(2, "14:01:00", 21, 1)
        self.assertEqual(self.read()["signals"]["TX1"], {"band": "15", "used": 1})
        self.qso(2, "14:01:00", 7, 1, "INB")
        self.assertEqual(self.read()["signals"]["TX1"], {"reason": "qso_order"})
        self.db.execute("UPDATE ContestInstance SET OperatorCategory='SINGLE-OP-ASSISTED' WHERE ContestNR=2")
        self.db.commit()
        self.assertEqual(self.read()["category"], "activity")

    def test_master_appinfo_to_lamp_without_external_sender(self):
        app = band_lamp(_i_am_master=True)
        xml = ("<AppInfo><dbname>test.s3db</dbname><contestnr>1</contestnr>"
               "<contestname>CQWWCW</contestname></AppInfo>").encode()
        app._contest_selection = app._contest_radio_seen = None
        app.local.add("10.20.30.40")  # discovery learned it, OS did not
        app.on_packet("10.20.30.40", xml)
        app.on_packet("127.0.0.1", xml, relayed=True)
        self.assertIsNone(app._contest_selection)
        app.on_packet("10.20.30.40", RADIO)
        app.on_packet("127.0.0.1", RADIO, relayed=True)
        self.assertIsNone(app._contest_radio_seen)
        app.on_packet("127.0.0.1", RADIO)
        app.on_packet("127.0.0.1", xml)
        self.assertEqual(app._contest_selection, self.selected)
        app._contest_folder = self.path.parent
        self.qso(1, "14:00:58", 14, 1)
        app.stop = threading.Event()
        def relay(share, packet):
            msg = wire.decode(packet, self.at)
            self.assertIn("RUN · 20 m · 5:00 left", wire.display(msg, self.at, set(), False))
            app.stop.set()
        with patch("time.time", return_value=self.at), patch("pingpong_lamp_band.send_relay", side_effect=relay) as send:
            log.publish_loop(app)
        send.assert_called_once()

    def test_feed_stops_then_recovers(self):
        app = band_lamp(_i_am_master=True)
        app._contest_selection, app._contest_folder = self.selected, self.path.parent
        app._contest_radio_seen = 1
        app.stop = Mock()
        app.stop.is_set.side_effect = [False, False, True]
        states = []
        def capture(data):
            states.append(wire.decode(data, self.at)["state"])
            app._contest_radio_seen = 601
        app._publish_contest = capture
        with patch("time.time", return_value=self.at), patch("time.monotonic", return_value=601):
            log.publish_loop(app)
        self.assertEqual(states[0]["reason"], "radio_offline")
        self.assertEqual(states[1]["category"], "MS")

    def test_log_identity_and_duplicate_names(self):
        home = self.path.parent
        candidates = [home / prefix / "Documents/N1MM Logger+/Databases" / self.path.name
                      for prefix in ("", "OneDrive")]
        for path in candidates:
            path.parent.mkdir(parents=True)
            shutil.copyfile(self.path, path)
        with patch.object(Path, "home", return_value=home), patch.dict(os.environ, OneDrive=str(home / "OneDrive")):
            read = lambda: self.reader.read(self.selected, "", self.at)
            self.assertEqual(read()["reason"], "duplicate_database")
            with sqlite3.connect(candidates[0]) as other:
                other.execute("UPDATE ContestInstance SET ContestName='CQWWSSB' WHERE ContestNR=1")
            other.close()
            self.assertEqual(read()["category"], "MS")
            self.assertEqual(self.reader.key[0], candidates[1].resolve())
            missing = self.reader.read(self.selected, str(home / "missing"), self.at)
            self.assertEqual(missing["category"], "unknown")
            self.assertEqual(self.reader.read(self.selected, candidates[0].parent, self.at)["reason"],
                             "contest_mismatch")
        self.assertEqual(self.read()["category"], "MS")
        self.selected = self.path.name, "1", "CQWWSSB"
        self.assertEqual(self.read()["reason"], "contest_mismatch")

    def test_ms_recovers_from_historical_tie(self):
        self.qso(1, "13:00:00", 14, 1)
        self.qso(1, "13:00:00", 21, 1, "INB")
        self.assertEqual(self.read()["signals"]["RUN"], {"reason": "qso_order"})
        self.qso(1, "14:00:00", 7, 1)
        self.assertEqual(self.read()["signals"]["RUN"], {"band": "40", "until": self.at + 300})

    def test_uncertain_start_clears_once_all_possible_holds_expire(self):
        self.qso(1, "14:00:00", 14, 1)
        self.qso(1, "14:00:00", 21, 1, "INB")
        self.qso(1, "14:02:00", 14, 1)
        self.assertEqual(self.read()["signals"]["RUN"], {"reason": "qso_order"})
        self.at = log.utc("2026-11-28 14:12:00")
        self.assertEqual(self.read()["signals"]["RUN"], {"band": "20", "until": self.at})

    def test_future_qso_warns_then_recovers_without_database_write(self):
        self.selected = self.path.name, "2", "CQWWSSB"
        self.qso(2, "14:00:00", 14, 1)
        self.qso(2, "14:55:00", 7, 1)
        self.assertEqual(self.read()["reason"], "future_qso")
        self.at += 50 * 60
        self.assertEqual(self.read()["signals"]["TX1"], {"band": "40", "used": 1})

    def test_waiting_for_application_info_when_display_enabled(self):
        app = band_lamp(var_last_qso=Var(True), _contest_label=Mock())
        app._contest_update("me", self.at)
        self.assertIn("Waiting for Application Info", app._contest_text)
        app.var_last_qso.set(False)
        app._contest_update("me", self.at)
        self.assertEqual(app._contest_text, "")
