"""PingPong Overview: function key stats, ready check, snapshot for the web page."""

import os
import tempfile
import time
import unittest

from helpers import Root, m
import pingpong_overview as ov


def radio(name, tx, caption="", freq="1402500", run="True"):
    return (
        "<RadioInfo><StationName>{}</StationName><Freq>{}</Freq><Mode>CW</Mode>"
        "<IsTransmitting>{}</IsTransmitting><FunctionKeyCaption>{}</FunctionKeyCaption>"
        "<IsRunning>{}</IsRunning></RadioInfo>".format(name, freq, tx, caption, run)
    ).encode()


def make_overview():
    o = ov.Overview.__new__(ov.Overview)
    import threading

    o.root = Root()
    o.port = 46070
    o.lamps = {}
    o.lock = threading.Lock()
    o.slots = {}
    o.error = o.web_error = ""
    o._hex = {}
    o.operating = set()
    o.newest = ""
    o.stats = {}
    o.stats_since = time.time()
    tmp = tempfile.mkdtemp()
    o.stats_path = os.path.join(tmp, "stats.json")
    o.events_path = os.path.join(tmp, "events.csv")
    o._seen_state = {}
    o._stats_saved = time.time()
    o.web_port = 8046
    o.fw_open = None
    o.silenced = set()
    o._alarm_logged = set()
    return o


def hello(o, node_id, host, alias, role="", status="", ver=None, **kw):
    data = m.make_hello(node_id, host, ["10.0.0.9"], True, alias=alias, role=role,
                        status=status, started=1, **kw)
    if ver:
        data = data.replace(b"<Version>" + m.__version__.encode(), b"<Version>" + ver.encode())
    o._packet("10.0.0.9", data)


def relay(o, node_id, *args, **kw):
    o._packet("10.0.0.9", m.make_relay(node_id, radio(*args, **kw)))


class StatsTest(unittest.TestCase):
    def test_counts_keys_at_transmission_start(self):
        o = make_overview()
        hello(o, "A", "OO840G4", "RUN1", "RUN", "N1MM 14 CW from x, Run", block_time=30)
        for cap in ["F1 CQ", "F1 CQ", "F2 5NN", "TEST"]:
            relay(o, "A", "RUN1", "True", cap)
            relay(o, "A", "RUN1", "True", cap)  # repeat while sending: not counted again
            relay(o, "A", "RUN1", "False", cap)
        st = o.stats["pc:oo840g4"]
        self.assertEqual(st["keys"], {"F1": 2, "F2": 1})
        self.assertEqual(st["other"], 1)
        cols, rows = o._stats_table()
        self.assertEqual(rows[0][0], "RUN1")
        self.assertEqual(rows[0][cols.index("Total")], 4)

    def test_stats_saved_and_loaded(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1")
        relay(o, "A", "RUN1", "True", "F3 TU")
        o._save_stats()
        o2 = make_overview()
        o2.stats_path = o.stats_path
        o2._load_stats()
        self.assertEqual(o2.stats["pc:pc1"]["keys"], {"F3": 1})

    def test_restart_of_lamp_keeps_counting_same_pc(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1")
        relay(o, "A", "RUN1", "True", "F1 CQ")
        hello(o, "B", "PC1", "RUN1")  # same PC, new lamp id after restart
        relay(o, "B", "RUN1", "True", "F1 CQ")
        self.assertEqual(o.stats["pc:pc1"]["keys"]["F1"], 2)


class ReadyTest(unittest.TestCase):
    def setUp(self):
        self.o = make_overview()
        hello(self.o, "A", "OO840G4", "RUN1", "RUN", "N1MM 14 CW from x, Run", block_time=30)
        relay(self.o, "A", "RUN1", "False")

    def snap(self):
        return self.o._make_snapshot()

    def row(self, snap, name):
        return next(r for r in snap["rows"] if r["values"]["station"].startswith(name))

    def test_green_when_all_fine(self):
        s = self.snap()
        self.assertEqual(s["ready"], "ok")
        self.assertEqual(self.row(s, "RUN1")["tag"], "ready_ok")
        self.assertEqual(self.row(s, "RUN1")["values"]["check"], "✔ OK")

    def test_red_with_reasons(self):
        hello(self.o, "B", "OO840G3", "INB1", "INB", "N1MM: not heard", ver="1.0")
        s = self.snap()
        self.assertEqual(s["ready"], "bad")
        check = self.row(s, "INB1")["values"]["check"]
        self.assertIn("update to", check)
        self.assertIn("no N1MM data", check)
        self.assertEqual(self.row(s, "INB1")["tag"], "ready_bad")

    def test_no_master_is_a_problem(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1")
        relay(o, "A", "RUN1", "False")
        self.assertIn("no master", self.row(o._make_snapshot(), "RUN1")["values"]["check"])

    def test_normal_colors_after_first_transmission(self):
        relay(self.o, "A", "RUN1", "True", "F1 CQ")
        s = self.snap()
        self.assertEqual(self.row(s, "RUN1")["tag"], "tx")
        self.assertIsNone(s["ready"])
        self.o._ready_check()
        self.assertEqual(self.row(self.snap(), "RUN1")["tag"], "ready_ok")

    def test_newest_release_counts(self):
        self.o.newest = "99.0"
        self.assertIn("update to v99.0", self.row(self.snap(), "RUN1")["values"]["check"])


class SelfUpdateTest(unittest.TestCase):
    def test_offer_once_and_install(self):
        o = make_overview()
        o.self_update = {"version": "99.0"}
        o._asked_update = False
        asked, installed = [], []
        ov.messagebox.askyesno = lambda *a, **k: asked.append(1) or True
        o._update_self = lambda rel=None: installed.append(rel["version"])
        o._offer_self_update()
        o._offer_self_update()
        self.assertEqual((len(asked), installed), (1, ["99.0"]))

    def test_asset_names(self):
        import pingpong_update as upd
        self.assertEqual(upd.ASSET_NAME, "n1mm_lamps.exe")
        self.assertEqual(ov.OVERVIEW_ASSET, "pingpong_overview.exe")


class RestartTest(unittest.TestCase):
    def test_restarted_lamp_replaces_old_row(self):
        o = make_overview()
        hello(o, "OLD", "OO840G4", "RUN1", block_time=30)
        relay(o, "OLD", "RUN1", "True", "F1 CQ")
        relay(o, "OLD", "RUN1", "False", "F1 CQ")
        hello(o, "NEW", "OO840G4", "RUN1", block_time=30)  # same PC after an update
        self.assertEqual(list(o.lamps), ["NEW"])
        self.assertEqual(o.lamps["NEW"].last_caption, "CQ")
        self.assertEqual(len(o._make_snapshot()["rows"]), 1)

    def test_different_pcs_stay(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1")
        hello(o, "B", "PC2", "RUN2")
        self.assertEqual(sorted(o.lamps), ["A", "B"])


class PingPongSettingTest(unittest.TestCase):
    def test_head_shows_lock_setting(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1", block_time=30, pingpong="0")
        self.assertIn("ping-pong lock: OFF", o._make_snapshot()["head"])
        hello(o, "A", "PC1", "RUN1", block_time=30, pingpong="1")
        self.assertIn("ping-pong lock: ON", o._make_snapshot()["head"])
        self.assertEqual(o._make_snapshot()["pingpong"], "1")

    def test_head_shows_web_address_when_firewall_open(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1", block_time=30, pingpong="1")
        self.assertNotIn("0.0.0.0", o._make_snapshot()["head"])
        o.fw_open = True
        head = o._make_snapshot()["head"]
        self.assertIn("web 0.0.0.0:8046", head)
        self.assertLess(head.index("ping-pong lock"), head.index("web 0.0.0.0"))

    def test_head_shows_release_time(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1", block_time=30, pingpong="1", pp_limit="20")
        self.assertIn("release 20 s", o._make_snapshot()["head"])
        hello(o, "A", "PC1", "RUN1", block_time=30, pingpong="1", pp_limit="0")
        self.assertIn("release only by RUN", o._make_snapshot()["head"])
        self.assertEqual(o._pp_limit(), 0.0)

    def test_no_master_no_setting(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1")  # no master heard
        self.assertEqual(o._make_snapshot()["pingpong"], "")


class VersionStateTest(unittest.TestCase):
    def test_counts_lamps_behind(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1", block_time=30)
        hello(o, "B", "PC2", "INB1", ver="1.0")
        v = o._make_snapshot()["versions"]
        self.assertEqual((v["target"], v["old"]), (m.__version__, 1))

    def test_all_current(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1", block_time=30)
        self.assertEqual(o._make_snapshot()["versions"]["old"], 0)

    def test_update_all_does_not_send_when_current(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1", block_time=30)
        shown, sent = [], []
        ov.messagebox.showinfo = lambda *a, **k: shown.append(a[1])
        o._send_command = lambda payload: sent.append(payload) or 1
        o._update_all()
        self.assertEqual(sent, [])
        self.assertIn("Nothing to update", shown[0])


class ActivityTest(unittest.TestCase):
    def test_locks_calls_and_hours(self):
        o = make_overview()
        hello(o, "A", "PC1", "INB1", "INB", "N1MM 7 CW from x, S&P")
        hello(o, "A", "PC1", "INB1", "INB", "N1MM 7 CW from x, S&P; ping-pong role INB (LOCKED)")
        hello(o, "A", "PC1", "INB1", "INB", "N1MM 7 CW from x, S&P; ping-pong role INB (LOCKED)")
        hello(o, "A", "PC1", "INB1", "INB", "N1MM 7 CW from x, S&P")  # WAIT over
        hello(o, "A", "PC1", "INB1", alert=True, alert_band="7", alert_time=100.0)
        hello(o, "A", "PC1", "INB1", alert=True, alert_band="7", alert_time=100.0)  # same call
        hello(o, "A", "PC1", "INB1", urgent=True, urgent_band="7", urgent_mode="CW", urgent_time=200.0)
        relay(o, "A", "INB1", "True", "F1 CQ", freq="701500")
        relay(o, "A", "INB1", "False", "F1 CQ", freq="701500")
        st = o.stats["pc:pc1"]
        self.assertEqual((st["locked"], st["f12"], st["urgent"]), (1, 1, 1))
        self.assertEqual(len(st["hours"]), 1)
        act = o._activity_table()
        self.assertEqual(act["rows"][0][0], "INB1")
        self.assertEqual(act["rows"][0][1], 1)  # locked once
        self.assertEqual(len(act["hours"]), 1)
        self.assertEqual(act["heat"][0][0], "INB1")

    def test_old_stats_file_still_works(self):
        o = make_overview()
        o.stats = {"pc:pc1": {"name": "RUN1", "keys": {"F1": 3}, "other": 0, "tx_time": 5.0}}
        self.assertEqual(o._activity_table()["rows"][0][0], "RUN1")
        hello(o, "A", "PC1", "RUN1")
        relay(o, "A", "RUN1", "True", "F1 CQ")
        relay(o, "A", "RUN1", "False", "F1 CQ")
        self.assertEqual(o.stats["pc:pc1"]["keys"]["F1"], 4)


class LampProblemsTest(unittest.TestCase):
    def test_lamp_reported_problems_in_check(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1", block_time=30, problems="UDP 12060 also used by Callbooker.exe")
        relay(o, "A", "RUN1", "False")
        row = o._make_snapshot()["rows"][0]
        self.assertIn("Callbooker.exe", row["values"]["check"])
        self.assertEqual(row["tag"], "ready_bad")


class EmergencyTest(unittest.TestCase):
    def run_emergency(self, yes, typed, master_pc=True):
        o = make_overview()
        sent = []
        o._send_command = lambda payload: sent.append(m.parse_command(payload)["cmd"]) or 1
        o._on_master_pc = lambda now: master_pc
        ov.messagebox.askyesno = lambda *a, **k: yes
        ov.messagebox.showinfo = lambda *a, **k: None
        ov.messagebox.showwarning = lambda *a, **k: None
        ov.simpledialog.askstring = lambda *a, **k: typed
        o._emergency("stop")
        return sent

    def test_needs_yes_and_typed_yes(self):
        self.assertEqual(self.run_emergency(True, "YES"), ["stop"])

    def test_no_or_wrong_word_sends_nothing(self):
        self.assertEqual(self.run_emergency(False, "YES"), [])
        self.assertEqual(self.run_emergency(True, "yes"), [])
        self.assertEqual(self.run_emergency(True, None), [])  # dialog closed
        self.assertEqual(self.run_emergency(True, "Y"), [])

    def test_not_from_other_pc(self):
        self.assertEqual(self.run_emergency(True, "YES", master_pc=False), [])


class EventLogTest(unittest.TestCase):
    def read(self, o):
        with open(o.events_path, encoding="utf-8-sig") as fh:
            return fh.read().splitlines()

    def test_contest_timeline(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1", block_time=30, pingpong="1")
        hello(o, "B", "PC2", "INB1", "INB", "N1MM 7 CW from x, S&P; ping-pong role INB (LOCKED)")
        hello(o, "B", "PC2", "INB1", "INB", "N1MM 7 CW from x, S&P")
        hello(o, "B", "PC2", "INB1", alert=True, alert_band="7", alert_time=5.0)
        hello(o, "C", "PC2", "INB1")  # same PC, new lamp id: restarted
        lines = self.read(o)
        self.assertEqual(lines[0], "date;time;station;event;details")
        events = [l.split(";")[3] for l in lines[1:]]
        self.assertEqual(events, ["lamp on", "lamp on", "ping-pong WAIT", "ping-pong free",
                                  "F12 call", "lamp restarted"])

    def test_changes_and_lost_lamps(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1", block_time=30, pingpong="1")
        lamps = list(o.lamps.values())
        o._log_changes(o._make_snapshot(), lamps, time.time())  # first look: no events
        hello(o, "A", "PC1", "RUN1", block_time=30, pingpong="0")
        o._log_changes(o._make_snapshot(), lamps, time.time() + 30)  # lamp silent 30 s
        events = [l.split(";")[3] + ";" + l.split(";")[4] for l in self.read(o)[1:]]
        self.assertIn("ping-pong lock;OFF", events)
        self.assertTrue(any(e.startswith("lamp not heard") for e in events))


class PingResultsTest(unittest.TestCase):
    def test_results_lost_and_slow(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1", block_time=30)
        hello(o, "B", "PC2", "INB1", ver="1.73")  # answers pings
        hello(o, "C", "PC3", "OLD1", ver="1.60")  # too old to answer: not "lost"
        for l in o.lamps.values():
            l.radio_seen = time.time()
        o._ping_results({"A": 3.2, "B": 750.0}, time.time())
        o._ping_results({"A": 4.8}, time.time())
        a, b, c = o.lamps["A"], o.lamps["B"], o.lamps["C"]
        self.assertEqual((round(a.ping_ms, 1), a.ping_lost), (4.8, False))
        self.assertTrue(b.ping_lost)
        self.assertFalse(c.ping_lost)
        st = o.stats["pc:pc1"]
        self.assertEqual((st["ping_n"], round(st["ping_max"], 1)), (2, 4.8))
        snap = o._make_snapshot()
        rows = {r["values"]["station"].split()[0]: r for r in snap["rows"]}
        self.assertEqual(rows["RUN1"]["values"]["ping"], "4.8 ms")
        self.assertIn("no ping reply", rows["INB1"]["values"]["check"])
        events = [l.split(";")[3] for l in open(o.events_path, encoding="utf-8-sig").read().splitlines()[1:]]
        self.assertIn("slow ping", events)
        self.assertIn("no ping reply", events)
        act = o._activity_table()
        run1 = next(r for r in act["rows"] if r[0] == "RUN1")
        self.assertEqual(run1[6], "4.0 ms")  # RUN1 average of 3.2 and 4.8
        self.assertEqual(run1[7], "4.8 ms")  # max


class SnapshotTest(unittest.TestCase):
    def test_web_snapshot_shape(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1", "RUN", block_time=30)
        relay(o, "A", "RUN1", "True", "F1 CQ")
        s = o._make_snapshot()
        for key in ("title", "head", "rows", "notes", "columns", "stats", "time"):
            self.assertIn(key, s)
        self.assertEqual([c[0] for c in s["columns"]], ov.WEB_ORDER)
        self.assertTrue(s["rows"][0]["bg"].startswith("#"))
        import json
        json.dumps(s)  # must be serialisable for /data.json


class SilentAlarmTest(unittest.TestCase):
    """An operating station whose lamp is not heard for 30 s raises the alarm."""

    def setUp(self):
        self.o = make_overview()
        hello(self.o, "A", "PC1", "RUN1", block_time=30)
        hello(self.o, "B", "PC2", "INB1")
        self.o.operating.add("pc:pc1")  # RUN1 has transmitted; INB1 has not

    def test_operating_station_silent_alarms(self):
        self.o.lamps["A"].hello_seen -= 40
        self.o.lamps["B"].hello_seen -= 40  # not operating yet: no alarm
        snap = self.o._make_snapshot()
        self.assertEqual(snap["alarm"], ["RUN1"])
        self.assertIn("NOT HEARD: RUN1", snap["head"])
        self.assertTrue(snap["warn"])
        row = [r for r in snap["rows"] if r["id"] == "A"][0]
        self.assertEqual(row["tag"], "silent")

    def test_no_alarm_when_heard(self):
        snap = self.o._make_snapshot()
        self.assertEqual(snap["alarm"], [])
        self.assertNotIn("NOT HEARD", snap["head"])

    def test_silenced_until_heard_again(self):
        self.o.lamps["A"].hello_seen -= 40
        self.o.silenced.add("pc:pc1")
        snap = self.o._make_snapshot()
        self.assertEqual(snap["alarm"], [])
        self.assertIn("(silenced)", snap["head"])
        hello(self.o, "A", "PC1", "RUN1", block_time=30)  # back
        self.o._make_snapshot()
        self.assertNotIn("pc:pc1", self.o.silenced)

    def test_restarted_lamp_with_new_id_ends_alarm(self):
        self.o.lamps["A"].hello_seen -= 40
        hello(self.o, "A2", "PC1", "RUN1", block_time=30)  # same PC, new lamp id
        self.assertEqual(self.o._make_snapshot()["alarm"], [])

    def test_silent_station_kept_in_table(self):
        self.o.lamps["A"].hello_seen -= 600
        self.o.lamps["A"].radio_seen -= 600
        self.o.lamps["B"].hello_seen -= 600
        self.o.lamps["B"].radio_seen -= 600
        snap = self.o._make_snapshot()
        self.assertIn("A", self.o.lamps)  # operating: stays, alarm goes on
        self.assertNotIn("B", self.o.lamps)  # never operated: forgotten
        self.assertEqual(snap["alarm"], ["RUN1"])


class SetupSaveTest(unittest.TestCase):
    def test_setup_data_has_names_colors_and_master(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1", block_time=20, block_time_run=4, pingpong="1",
              pp_limit="25", color="#ff0000")
        hello(o, "B", "PC2", "INB1")
        data = o._setup_data()
        self.assertEqual(data["pcs"]["pc1"], {"alias": "RUN1", "color": "#ff0000"})
        self.assertEqual(data["pcs"]["pc2"]["alias"], "INB1")
        self.assertEqual(data["master"]["block_time"], "20")
        self.assertEqual(data["master"]["block_time_run"], "4")
        self.assertEqual(data["master"]["pingpong"], "1")
        self.assertEqual(data["master"]["pp_limit"], "25")
        text = o._setup_summary(data)
        self.assertIn("RUN1", text)
        self.assertIn("ping-pong lock ON", text)


class TablesCacheTest(unittest.TestCase):
    def test_tables_built_at_most_once_per_second(self):
        o = make_overview()
        calls = []
        orig = o._stats_table
        o._stats_table = lambda: calls.append(1) or orig()
        now = time.time()
        o._tables(now)
        o._tables(now + 0.3)
        self.assertEqual(len(calls), 1)
        o._tables(now + 1.1)
        self.assertEqual(len(calls), 2)

    def test_tree_filled_only_on_change(self):
        o = make_overview()

        class Tree:
            def __init__(self):
                self.inserts = 0

            def get_children(self):
                return []

            def delete(self, *items):
                pass

            def insert(self, *a, **kw):
                self.inserts += 1

        tree = Tree()
        self.assertTrue(o._fill_tree(tree, "_shown_x", [["RUN1", 1], ["All", 1]]))
        self.assertFalse(o._fill_tree(tree, "_shown_x", [("RUN1", 1), ("All", 1)]))
        self.assertEqual(tree.inserts, 2)


class RemoveStationTest(unittest.TestCase):
    """Right click → remove a dead station; it comes back when heard again."""

    def setUp(self):
        self.o = make_overview()
        hello(self.o, "A", "PC1", "RUN1", block_time=30)
        hello(self.o, "B", "PC2", "INB1")
        self.o.operating.update({"pc:pc1", "pc:pc2"})

    def test_dead_station_removed_and_alarm_gone(self):
        self.o.lamps["B"].hello_seen -= 60
        self.assertEqual(self.o._make_snapshot()["alarm"], ["INB1"])
        self.assertEqual(self.o._remove_station("B"), "INB1")
        self.assertNotIn("B", self.o.lamps)
        self.assertNotIn("pc:pc2", self.o.operating)
        snap = self.o._make_snapshot()
        self.assertEqual(snap["alarm"], [])
        self.assertEqual([r["id"] for r in snap["rows"]], ["A"])
        self.assertIn("removed from the list", open(self.o.events_path, encoding="utf-8-sig").read())

    def test_live_station_not_removed(self):
        self.assertEqual(self.o._remove_station("A"), "")
        self.assertIn("A", self.o.lamps)

    def test_comes_back_when_heard(self):
        self.o.lamps["B"].hello_seen -= 60
        self.o._remove_station("B")
        hello(self.o, "B", "PC2", "INB1")  # the lamp is back
        self.assertIn("B", self.o.lamps)
        row = [r for r in self.o._make_snapshot()["rows"] if r["id"] == "B"][0]
        self.assertIn(row["tag"], ("ready_ok", "ready_bad"))  # in the ready check again


if __name__ == "__main__":
    unittest.main()
