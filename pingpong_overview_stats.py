"""PingPong Overview - statistics, Activity, event log, contest report.

StatsMixin (used by Overview in pingpong_overview.py): function key and
activity counters, the event log (pingpong_events.csv), CSV export, the
contest report and the planned reset at the contest start.
"""

import json
import os
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

import n1mm_lamps as pp
import pingpong_report as report
from pingpong_overview_common import *  # noqa: F401,F403


class StatsMixin:
    def _stat(self, lamp):
        key = lamp.stats_key()
        st = self.stats.get(key)
        if st is None:
            st = self.stats[key] = {"name": lamp.name()}
        for field, empty in (("keys", {}), ("other", 0), ("tx_time", 0.0), ("hours", {}),
                             ("locked", 0), ("locked_time", 0.0), ("f12", 0), ("urgent", 0)):
            st.setdefault(field, type(empty)())  # also fills records from older versions
        st["name"] = lamp.name()  # keep the newest name (My name may change)
        return st

    def _track_activity(self, lamp, info, now):
        """From the hellos: ping-pong WAIT (count, time), F12 and urgent calls."""
        st = self._stat(lamp)
        locked = "(LOCKED)" in info.get("status", "")
        if locked and not lamp.locked_since:
            lamp.locked_since = now
            st["locked"] += 1
            self._event(lamp.name(), "ping-pong WAIT")
        elif not locked and lamp.locked_since:
            st["locked_time"] += now - lamp.locked_since
            self._event(lamp.name(), "ping-pong free", "waited {:.0f} s".format(now - lamp.locked_since))
            lamp.locked_since = 0.0
        if info.get("alert") and info.get("alert_time") and info["alert_time"] != lamp.last_alert:
            lamp.last_alert = info["alert_time"]
            st["f12"] += 1
            self._event(lamp.name(), "F12 call", "{} call, {} MHz".format(
                "Run" if info.get("alert_run") else "S&P", info.get("alert_band", "?")))
        if info.get("urgent") and info.get("urgent_time") and info["urgent_time"] != lamp.last_urgent:
            lamp.last_urgent = info["urgent_time"]
            st["urgent"] += 1
            self._event(lamp.name(), "priority call" if info.get("urgent_priority") else "urgent call",
                        "{} {}".format(info.get("urgent_band", "?"), info.get("urgent_mode", "")))

    def _event(self, station, what, detail=""):
        """One line in pingpong_events.csv: date;time;station;event;details."""
        new = not os.path.exists(self.events_path)
        try:
            with open(self.events_path, "a", encoding="utf-8-sig" if new else "utf-8",
                      newline="") as fh:
                if new:
                    fh.write("date;time;station;event;details\r\n")
                fh.write("{};{};{};{};{}\r\n".format(
                    time.strftime("%Y-%m-%d"), time.strftime("%H:%M:%S"),
                    pp.csv_safe(station.replace(";", ",")), what,
                    pp.csv_safe(str(detail).replace(";", ",")).replace("\n", " ")))
        except OSError:
            pass

    def _log_changes(self, snap, lamps, now):
        """Event log: master, ping-pong settings and lost lamps (UI thread)."""
        seen = self._seen_state
        for key, value, text in (
            ("master", snap.get("master_name", ""), "master is now"),
            ("pingpong", snap.get("pingpong", ""), "ping-pong lock"),
            ("pp_limit", snap.get("pp_limit", ""), "ping-pong release time"),
        ):
            if value and seen.get(key) != value:
                if key in seen:  # not at start: only real changes
                    shown = {"1": "ON", "0": "OFF"}.get(value, value) if key == "pingpong" else value
                    self._event("-", text, shown + (" s" if key == "pp_limit" else ""))
                seen[key] = value
        for lamp in lamps:
            if now - lamp.hello_seen > GONE_AFTER and lamp.hello_seen and not getattr(lamp, "gone", False):
                lamp.gone = True
                self._event(lamp.name(), "lamp not heard", "for {:.0f} s".format(now - lamp.hello_seen))

    def _open_events(self):
        if not os.path.exists(self.events_path):
            messagebox.showinfo(TITLE, "No events yet.", parent=self.root)
            return
        try:
            os.startfile(self.events_path)  # Windows: opens in Excel / the editor
        except (AttributeError, OSError) as exc:
            messagebox.showinfo(TITLE, "Event log: {}\n({})".format(self.events_path, exc),
                                parent=self.root)

    def _report_html(self, now=None):
        """The after-contest report page, from the statistics (since the
        last Reset) and the event log of the same period."""
        now = now or time.time()
        with self.lock:
            stats = json.loads(json.dumps(self.stats))  # a copy, outside the lock below
            since = self.stats_since
        events = report.read_events(self.events_path, since)
        return report.build_report(stats, since, now, events)

    def _contest_report(self):
        """Activity tab: write the report next to the exe and open it."""
        path = os.path.join(app_dir(), "pingpong_report_{}.html".format(time.strftime("%Y-%m-%d_%H%M")))
        try:
            page = self._report_html()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(page)
        except OSError as exc:
            messagebox.showerror(TITLE, "Could not write the report:\n{}".format(exc), parent=self.root)
            return
        self._event("-", "contest report", os.path.basename(path))
        try:
            os.startfile(path)  # Windows: opens in the default browser
        except (AttributeError, OSError):
            import webbrowser

            webbrowser.open("file://" + path)

    def _activity_table(self):
        """Activity per station + TX minutes per hour (window, web, CSV)."""
        cols = ["Station", "Locked (WAIT)", "Locked time", "F12 calls", "Urgent calls", "TX time",
                "Ping avg", "Ping max", "Ping lost"]
        rows, heat = [], []
        with self.lock:
            items = sorted(self.stats.values(), key=lambda s: s["name"].lower())
            hours = sorted({h for st in items for h in (st.get("hours") or {})})[-12:]
            tot = [0, 0.0, 0, 0, 0.0]
            for st in items:
                vals = [st.get("locked", 0), st.get("locked_time", 0.0), st.get("f12", 0),
                        st.get("urgent", 0), st.get("tx_time", 0.0)]
                tot = [a + b for a, b in zip(tot, vals)]
                n = st.get("ping_n", 0)
                rows.append([st["name"], vals[0] or "", ago_long(vals[1]) if vals[1] else "",
                             vals[2] or "", vals[3] or "", ago_long(vals[4]),
                             fmt_ms(st["ping_sum"] / n) if n else "",
                             fmt_ms(st.get("ping_max", 0)) if n else "",
                             st.get("ping_lost", 0) or ""])
                heat.append([st["name"], [tx_minutes((st.get("hours") or {}).get(h, 0.0))
                                          for h in hours]])
        if len(rows) > 1:
            rows.append(["All", tot[0] or "", ago_long(tot[1]) if tot[1] else "",
                         tot[2] or "", tot[3] or "", ago_long(tot[4]), "", "", ""])
        labels = [h[-2:] + ":00" for h in hours]
        return {"columns": cols, "rows": rows, "hours": labels, "heat": heat}

    def _count_key(self, lamp, payload, caption):
        """One transmission started: count the function key that started it."""
        st = self._stat(lamp)
        m = CAPTION_KEY.search(payload)
        if m and m.group(1).upper().decode() in FKEYS:
            fkey = m.group(1).upper().decode()
            st["keys"][fkey] = st["keys"].get(fkey, 0) + 1
        else:
            st["other"] += 1  # caption without an F-key number

    def _load_stats(self):
        try:
            with open(self.stats_path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.stats = data.get("stations", {})
            self.stats_since = float(data.get("since", time.time()))
        except (OSError, ValueError, AttributeError):
            self.stats, self.stats_since = {}, time.time()

    def _save_stats(self):
        with self.lock:
            data = {"since": self.stats_since, "stations": self.stats}
            text = json.dumps(data, indent=1)
        try:
            with open(self.stats_path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass
        self._stats_saved = time.time()

    def _stats_table(self):
        """(columns, rows) for the window and the web page."""
        cols = ["Station"] + FKEYS + ["Other", "Total", "TX time"]
        rows = []
        with self.lock:
            items = sorted(self.stats.values(), key=lambda s: s["name"].lower())
            totals = dict.fromkeys(FKEYS + ["other"], 0)
            tx_all = 0.0
            for st in items:
                counts = [st["keys"].get(k, 0) for k in FKEYS]
                total = sum(counts) + st["other"]
                for k, n in zip(FKEYS, counts):
                    totals[k] += n
                totals["other"] += st["other"]
                tx_all += st["tx_time"]
                rows.append([st["name"]] + [n or "" for n in counts]
                            + [st["other"] or "", total, ago_long(st["tx_time"])])
        if len(rows) > 1:
            counts = [totals[k] for k in FKEYS]
            rows.append(["All"] + [n or "" for n in counts]
                        + [totals["other"] or "", sum(counts) + totals["other"], ago_long(tx_all)])
        return cols, rows

    def _export_csv(self):
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save function key statistics",
            defaultextension=".csv", initialfile="pingpong_stats.csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        import csv

        cols, rows = self._stats_table()
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.writer(fh, delimiter=";")
                writer.writerow(["since", time.strftime("%Y-%m-%d %H:%M", time.localtime(self.stats_since))])
                writer.writerow(cols)
                writer.writerows([[pp.csv_safe(v) for v in row] for row in rows])
                act = self._activity_table()
                writer.writerow([])
                writer.writerow(act["columns"])
                writer.writerows([[pp.csv_safe(v) for v in row] for row in act["rows"]])
                writer.writerow([])
                writer.writerow(["TX minutes per hour"] + act["hours"])
                for name, mins in act["heat"]:
                    writer.writerow([pp.csv_safe(name)] + [str(v).replace(".", ",") for v in mins])
        except OSError as exc:
            messagebox.showerror(TITLE, "Could not save:\n{}".format(exc), parent=self.root)

    def _reset_stats(self):
        if not messagebox.askyesno(
            TITLE, "Reset all statistics (function keys and activity) to zero now?", parent=self.root
        ):
            return
        self._do_reset("by hand")

    def _do_reset(self, how):
        with self.lock:
            self.stats = {}
            self.stats_since = time.time()
        self._tables_cache = None  # show the empty tables at once
        self._save_stats()
        self._event("-", "statistics reset", how)

    def _fill_stats_menu(self):
        m = self.stats_menu
        m.delete(0, tk.END)
        m.add_command(label="Reset statistics now…", command=self._reset_stats)
        m.add_command(label="Reset at contest start…  (set date and time)", command=self._plan_reset)
        if self.reset_at:
            m.add_command(label="Cancel planned reset  ({})".format(fmt_when(self.reset_at)),
                          command=lambda: self._set_reset_at(0.0))
        m.add_separator()
        m.add_command(label="Contest report…", command=self._contest_report)
        m.add_command(label="Save CSV…", command=self._export_csv)

    def _plan_reset(self):
        """Ask when the statistics should be reset (default: next Saturday 01:00)."""
        default = self.reset_at if self.reset_at > time.time() else next_contest_start(time.time())
        while True:
            text = simpledialog.askstring(
                TITLE, "Reset the statistics automatically at (local time):\n"
                "format 2026-11-28 01:00 or 28.11.2026 01:00\n\n"
                "CQ WW starts Saturday 00:00 UTC = 01:00 local (CET).",
                initialvalue=time.strftime("%Y-%m-%d %H:%M", time.localtime(default)),
                parent=self.root)
            if text is None:
                return
            when = parse_when(text)
            if when is None:
                messagebox.showwarning(TITLE, "Not a date and time: {}".format(text), parent=self.root)
            elif when <= time.time():
                messagebox.showwarning(TITLE, "That time is already past.", parent=self.root)
            else:
                break
        self._set_reset_at(when)
        messagebox.showinfo(TITLE, "Statistics will be reset at {} (in {}).\n\nThe Overview must be "
                            "running then – if it starts later, it resets at once.".format(
                                fmt_when(when), ago(when - time.time())), parent=self.root)

    def _set_reset_at(self, when):
        self.reset_at = float(when or 0)
        self.cfg.set("stats", "reset_at", "{:.0f}".format(self.reset_at) if self.reset_at else "")
        self._save()
        self._event("-", "statistics reset planned" if when else "planned reset cancelled",
                    fmt_when(when) if when else "")
        self._show_reset_plan()

    def _show_reset_plan(self):
        if getattr(self, "stats_button", None) is None:
            return
        if self.reset_at:
            text, bg = "Statistics ▾  (reset {})".format(fmt_when(self.reset_at)), "#ffe08a"
        else:
            text, bg = "Statistics ▾", self._stats_button_bg
        if self.stats_button.cget("text") != text:
            self.stats_button.configure(text=text, bg=bg, activebackground=bg)

    def _check_planned_reset(self, now):
        """Planned reset due? Also when the Overview was not running at that
        moment: statistics older than the planned time are reset at start."""
        if not self.reset_at or now < self.reset_at:
            return False
        when = self.reset_at
        self.reset_at = 0.0
        self.cfg.set("stats", "reset_at", "")
        self._save()
        if self.stats_since < when:
            self._do_reset("planned for " + fmt_when(when))
            return True
        return False

    def _tables(self, now):
        """Function key and Activity tables: they change slowly, so they are
        built at most once per TABLES_EVERY instead of on every refresh."""
        cached = getattr(self, "_tables_cache", None)
        if cached is not None and now - cached[0] < TABLES_EVERY:
            return cached[1], cached[2]
        stat_cols, stat_rows = self._stats_table()
        stats = {
            "columns": stat_cols, "rows": stat_rows,
            "since": time.strftime("%d.%m. %H:%M", time.localtime(self.stats_since)),
        }
        activity = self._activity_table()
        self._tables_cache = (now, activity, stats)
        return activity, stats
