"""After-contest report (Overview → Activity → Contest report…)."""

import os
import tempfile
import time
import unittest

import pingpong_report as report

STATS = {
    "pc:pc1": {"name": "RUN1", "keys": {"F1": 120, "F2": 40}, "other": 3, "tx_time": 5400.0,
               "hours": {"2026-11-28 01": 1800.0, "2026-11-28 02": 3600.0},
               "locked": 0, "locked_time": 0.0, "f12": 4, "urgent": 1,
               "ping_n": 10, "ping_sum": 12.0, "ping_max": 3.1, "ping_lost": 0},
    "pc:pc2": {"name": "INB1", "keys": {"F4": 30}, "other": 0, "tx_time": 600.0,
               "hours": {"2026-11-28 02": 600.0},
               "locked": 12, "locked_time": 95.0, "f12": 0, "urgent": 0,
               "ping_n": 10, "ping_sum": 20.0, "ping_max": 5.0, "ping_lost": 2},
}


class ReportTest(unittest.TestCase):
    def test_page_has_all_parts(self):
        events = [
            {"date": "2026-11-28", "time": "02:10:00", "station": "INB1",
             "event": "ALARM lamp not heard", "details": "for 31 s"},
            {"date": "2026-11-28", "time": "02:10:05", "station": "INB1",
             "event": "ping-pong WAIT", "details": ""},
            {"date": "2026-11-28", "time": "02:12:00", "station": "INB1",
             "event": "lamp restarted", "details": "v1.87"},
        ]
        page = report.build_report(STATS, 0, time.time(), events)
        for text in ("RUN1", "INB1", "1:40:00", "TX minutes per hour", "Function keys",
                     "ALARM lamp not heard", "lamp restarted", "90%", "transmissions started"):
            self.assertIn(text, page)
        self.assertNotIn("ping-pong WAIT</td>", page)  # routine lines are only counted
        self.assertIn("<td>193</td>", page)  # all transmissions: 163 + 30

    def test_empty(self):
        page = report.build_report({}, 0, time.time(), [])
        self.assertIn("No statistics yet", page)
        self.assertTrue(page.endswith("</html>"))

    def test_events_without_statistics(self):
        events = [{"date": "2026-11-28", "time": "02:12:00", "station": "INB1",
                   "event": "lamp restarted", "details": "v1.87"}]
        page = report.build_report({}, 0, time.time(), events)
        self.assertIn("lamp restarted", page)

    def test_names_are_escaped(self):
        stats = {"x": dict(STATS["pc:pc1"], name="<b>RUN</b>")}
        self.assertNotIn("<b>RUN</b>", report.build_report(stats, 0, time.time(), []))

    def test_read_events_from_since(self):
        path = os.path.join(tempfile.mkdtemp(), "events.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            fh.write("date;time;station;event;details\r\n")
            fh.write("2020-01-01;10:00:00;RUN1;lamp on;v1\r\n")
            fh.write("2030-01-01;10:00:00;RUN1;lamp restarted;v2\r\n")
        since = time.mktime(time.strptime("2025-01-01", "%Y-%m-%d"))
        rows = report.read_events(path, since)
        self.assertEqual([r["event"] for r in rows], ["lamp restarted"])
        self.assertEqual(report.read_events(path + "missing", 0), [])


if __name__ == "__main__":
    unittest.main()
