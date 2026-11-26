from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import pingpong_contest as wire
import pingpong_contest_log as log
from test_band_alert import band_lamp


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
