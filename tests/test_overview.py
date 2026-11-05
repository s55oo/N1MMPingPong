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
    o.stats_path = os.path.join(tempfile.mkdtemp(), "stats.json")
    o._stats_saved = time.time()
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

    def test_no_master_no_setting(self):
        o = make_overview()
        hello(o, "A", "PC1", "RUN1")  # no master heard
        self.assertEqual(o._make_snapshot()["pingpong"], "")


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


if __name__ == "__main__":
    unittest.main()
