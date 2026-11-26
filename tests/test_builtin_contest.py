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
            CREATE TABLE DXLOG(ContestNR, TS, ID, Band, Freq, Run1Run2, NetBiosName, IsMultiplier1 DEFAULT 1, IsMultiplier2 DEFAULT 0);
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
        self.db.execute("INSERT INTO DXLOG(ContestNR,TS,ID,Band,Freq,Run1Run2,NetBiosName) VALUES (?,?,?,?,?,?,?)",
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

    def test_m2_warning_from_master_log_on_every_lamp(self):
        from helpers import peer

        self.selected = self.path.name, "2", "CQWWSSB"
        for i in range(8):  # first QSO plus seven changes
            self.qso(2, f"14:01:{i:02d}", (14, 21)[i % 2], 1)
        self.qso(2, "14:00:00", 7, 2)
        lamps = [band_lamp(_i_am_master=i == 0, var_last_qso=Var(False),
                           _contest_label=Mock(), _contest_warning_label=Mock())
                 for i in range(3)]
        master, *clients = lamps
        master_ip = "10.0.0.9"
        for lamp in clients:
            lamp._master_peer = peer(lamp, "MASTER", [master_ip])
        master._contest_selection, master._contest_folder = self.selected, self.path.parent
        master._contest_radio_seen = 100
        master.stop = Mock()
        master.stop.is_set.return_value = False
        received = []
        def relay(share, packet):
            for lamp in clients:
                lamp.on_band(master_ip, packet)
            received.append(packet)

        with patch("time.time", return_value=self.at) as wall, patch(
                "time.monotonic", return_value=100) as mono, patch(
                "pingpong_lamp_band.send_relay", side_effect=relay):
            def check(kind, text=""):
                for lamp in lamps:
                    lamp._band_update_body(wall.return_value)
                    view = getattr(lamp, "_contest_warning", None)
                    if kind is None:
                        self.assertIsNone(view)
                    else:
                        self.assertEqual(view[0], kind)
                        self.assertIn(text, view[1])
                        self.assertIn(text, lamp._contest_warning_label.configure.call_args.kwargs["text"])
                    self.assertEqual(lamp.beeps, [])

            def advance(seconds):
                mono.return_value += seconds
                wall.return_value += seconds

            def tick(_):
                n = len(received)
                if n == 1:
                    check(None)
                    self.assertIn("7/8 changes · 1 change left", master._contest_text)
                    self.qso(2, "14:05:00", 14, 1)
                elif n == 2:
                    check("amber", "TX1 · LIMIT REACHED · stay on 20 m until 15:00 UTC")
                    self.qso(2, "14:05:02", 21, 1)
                elif n == 3:
                    check("red", "TX1 · LIMIT EXCEEDED · 9/8")
                    advance(1)
                    check("dark-red", "9/8")
                    self.qso(2, "14:05:02", 21, 1, "ANOTHER-PC")  # same second and band
                    advance(1)
                    return
                elif 4 <= n <= 7:
                    check("red", "9/8")
                elif n == 8:
                    check(None)  # ten seconds after the first red, despite five heartbeats
                    self.assertIn("9/8 changes", master._contest_text)
                    self.qso(2, "14:05:14", 14, 1)
                elif n == 9:
                    check("red", "10/8")
                    self.db.execute("DELETE FROM DXLOG WHERE TS LIKE '%14:05:14'")
                    self.db.commit()
                elif n == 10:
                    check(None)
                    self.assertIn("9/8 changes", master._contest_text)
                    self.assertIn("TX2 · 40 m · 0/8", master._contest_text)
                    master.stop.is_set.return_value = True
                advance(2)

            master.stop.wait.side_effect = tick
            log.publish_loop(master)
        self.assertEqual(len(received), 10)

    def test_ms_warning_windows_and_legal_boundary(self):
        self.qso(1, "13:55:59", 14, 1)
        self.qso(1, "14:05:00", 21, 1)  # first legal UTC minute
        self.qso(1, "14:04:59", 7, 2)
        with patch("time.monotonic", return_value=200) as mono:
            def flashes():
                self.read()
                return self.reader.warnings.packet()

            self.assertEqual(flashes(), [])
            self.qso(1, "14:05:01", 14, 2)
            self.at += 1
            first, = flashes()
            self.assertIn("MULT · EARLY BAND CHANGE · 40 → 20 m at 14:05 · allowed 14:14 UTC", first["text"])
            # A return during the original hold restores its deadline.
            msg = dict(state=self.read(), sent=self.at, ttl=10, _received=200)
            self.assertIsNone(wire.warning_view(msg, self.at, False, [(1, "MULT", "40")]))
            self.assertEqual(wire.warning_view(msg, self.at, False, [(1, "MULT", "10")])[0], "amber")
            mono.return_value += 4
            self.at += 4
            self.qso(1, "14:05:05", 28, 2)
            second, = flashes()
            self.assertIn("20 → 10 m", second["text"])
            self.assertEqual(second["ms"], 10000)
            self.qso(1, "14:05:05", 28, 1)
            self.assertEqual(len(flashes()), 2)
            self.db.execute("UPDATE DXLOG SET Band=21 WHERE TS LIKE '%14:05:05' AND Run1Run2=1")
            self.db.commit()
            after, = flashes()
            self.assertEqual((after["text"], after["ms"]), (second["text"], second["ms"]))
            mono.return_value += 10
            self.assertEqual(flashes(), [])
            self.at += 300
            self.reader.close()  # restart over historical contacts
            self.assertEqual(flashes(), [])

    def test_hour_rollover_carries_only_active_flash(self):
        self.selected = self.path.name, "2", "CQWWSSB"
        self.at = log.utc("2026-11-28 14:59:59")
        for i in range(10):
            self.qso(2, f"14:59:{50+i}", (14, 21)[i % 2], 1)
        with patch("time.monotonic", return_value=500) as mono:
            row = self.read()["signals"]
            self.assertEqual(row["TX1"]["used"], 9)
            self.assertEqual(self.reader.warnings.packet()[0]["ms"], 10000)
            mono.return_value += 1
            self.at += 1
            row = self.read()["signals"]
            self.assertEqual(row["TX1"]["used"], 0)
            self.assertEqual(self.reader.warnings.packet()[0]["ms"], 9000)
            mono.return_value += 9
            self.assertEqual(self.reader.warnings.packet(), [])

    def test_ms_local_tuning_uses_fresh_assigned_radio_only(self):
        from test_so2r import radio
        app = band_lamp(_i_am_master=True, var_last_qso=Var(False), _contest_label=Mock(),
                        _contest_warning_label=Mock(), _contest_roles={2: Var("Unassigned")})
        self.qso(1, "14:04:00", 14, 1)
        self.qso(1, "14:04:01", 21, 2)
        state = self.read()
        with patch("time.monotonic", return_value=500) as mono:
            app._contest_msg = dict(state=state, sent=self.at, ttl=60, _received=500)
            app._contest_master, app._contest_expires = "me", 560
            app.on_packet("127.0.0.1", radio(2, freq="1402500"))
            app._contest_update("me", self.at)
            self.assertIsNone(getattr(app, "_contest_warning", None))
            app._contest_roles[2].set("MULT")
            app._contest_update("me", self.at)
            self.assertEqual(app._contest_warning, ("amber", "Radio 2 · MULT · stay on 15 m until 14:14 UTC"))
            app.on_packet("127.0.0.1", radio(2, freq="2102500"))
            app._contest_update("me", self.at)
            self.assertIsNone(app._contest_warning)
            app.on_packet("127.0.0.1", radio(2, freq="1402500"))
            mono.return_value += 31
            app._contest_update("me", self.at + 31)
            self.assertIsNone(app._contest_warning)

    def test_early_moves_never_change_the_legal_run_band(self):
        self.qso(1, "14:00:00", 14, 1)
        self.read()
        for stamp, band in [("14:01:00", 21), ("14:02:00", 7), ("14:03:00", 21)]:
            self.qso(1, stamp, band, 1)
            state = self.read()
            self.assertEqual(state["signals"]["RUN"], {"band": "20", "until": self.at + 300})
            flash, = self.reader.warnings.packet()
            self.assertIn("at " + stamp[:5] + " · allowed 14:10 UTC", flash["text"])
        msg = dict(state=state, sent=self.at, ttl=10, _received=0)
        with patch("time.monotonic", return_value=0):
            for band in ("15", "40"):
                self.assertIn("stay on 20 m until 14:10 UTC", wire.warning_view(
                    msg, self.at, False, [(1, "RUN", band)])[1])
        self.qso(1, "14:10:00", 21, 1)
        self.at += 300
        self.assertEqual(self.read()["signals"]["RUN"], {"band": "15", "until": self.at + 600})

    def test_mult_clock_requires_a_new_zone_or_country(self):
        self.qso(1, "14:00:00", 21, 2)
        self.db.execute("UPDATE DXLOG SET IsMultiplier1=0")
        self.db.commit()
        self.assertIsNone(self.read()["signals"]["MULT"])
        self.qso(1, "14:05:00", 21, 2)
        self.db.execute("UPDATE DXLOG SET IsMultiplier1=0,IsMultiplier2=1 WHERE TS LIKE '%14:05:00'")
        self.db.commit()
        self.assertEqual(self.read()["signals"]["MULT"]["until"], self.at + 600)
        self.at += 360
        self.qso(1, "14:11:00", 7, 2)
        self.read()
        self.assertIn("allowed 14:15 UTC", self.reader.warnings.packet()[0]["text"])
        self.db.execute("ALTER TABLE DXLOG DROP COLUMN IsMultiplier2")
        self.db.commit()
        self.assertTrue(self.read()["missing_multiplier_flags"])

    def test_confirmed_change_survives_later_ambiguous_qsos(self):
        self.selected = self.path.name, "2", "CQWWSSB"
        self.read()
        for i in range(10):
            self.qso(2, f"14:00:{i:02d}", (14, 21)[i % 2], 1)
        self.qso(2, "14:00:12", 7, 1)
        self.qso(2, "14:00:12", 28, 1, "OTHER")
        self.assertEqual(self.read()["signals"]["TX1"], {"reason": "qso_order"})
        self.assertIn("9/8", self.reader.warnings.packet()[0]["text"])

    def test_timestamp_correction_and_category_edit_do_not_replay_history(self):
        for i in range(10):
            self.qso(1, f"13:00:{i:02d}", (14, 21)[i % 2], 1)
        self.read()
        self.db.execute("UPDATE DXLOG SET TS='2026-11-28 13:00:10' WHERE TS LIKE '%13:00:09'")
        self.db.commit()
        self.read()
        self.assertEqual(self.reader.warnings.packet(), [])
        self.db.execute("UPDATE ContestInstance SET TransmitterCategory='TWO' WHERE ContestNR=1")
        self.db.commit()
        self.assertEqual(self.read()["signals"]["TX1"]["used"], 0)
        self.assertEqual(self.reader.warnings.packet(), [])

    def test_active_window_survives_writer_lock_and_master_promotion(self):
        from test_band_alert import band_lamp
        with patch("time.monotonic", return_value=100) as mono, patch("time.time", return_value=self.at) as wall:
            self.qso(1, "14:00:00", 14, 1)
            self.read()
            self.qso(1, "14:04:00", 21, 1)  # arrives a minute after it was logged
            self.read()
            original = self.reader.warnings.packet()
            self.db.execute("BEGIN EXCLUSIVE")
            self.reader.db.execute("PRAGMA busy_timeout=0")
            try:
                self.assertEqual(self.read()["category"], "MS")
            finally:
                self.db.rollback()
            mono.return_value += 2
            self.at += 2
            wall.return_value = self.at
            self.read()
            self.assertEqual(self.reader.warnings.packet()[0]["ms"], 8000)
            standby = band_lamp(_i_am_master=True)
            standby._contest_selection, standby._contest_folder = self.selected, self.path.parent
            standby._contest_msg = {"flash": original}
            standby._contest_radio_seen = mono.return_value
            standby.stop = Mock()
            standby.stop.is_set.side_effect = [False, True]
            standby._publish_contest = Mock()
            log.publish_loop(standby)
            packet = standby._publish_contest.call_args.args[0]
            flash, = wire.decode(packet, self.at)["flash"]
            self.assertEqual((flash["event"], flash["ms"]), (original[0]["event"], 8000))
