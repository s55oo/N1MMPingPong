"""PingPong Overview - one live table of all PingPong stations.

A separate, read-only program, meant for the master PC. It shows the same
live table in its own window and as a web page (http://<this PC>:8046).
It only LISTENS to what the PingPong lamps broadcast on UDP 46070 and
never opens N1MM's ports, so it runs next to a lamp.

Code:
- pingpong_overview.py         - this file: window, table, alarm, start
- pingpong_overview_common.py  - constants, helpers, the Lamp record
- pingpong_overview_admin.py   - commands to the lamps, setup, password,
                                 ping, self-update
- pingpong_overview_stats.py   - statistics, Activity, event log, report,
                                 planned reset
- pingpong_overview_web.py     - the web page and its firewall rule
"""

import argparse
import configparser
import os
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk  # noqa: F401  (simpledialog: tests)

import n1mm_lamps as pp
from pingpong_overview_admin import AdminMixin
from pingpong_overview_common import *  # noqa: F401,F403
from pingpong_overview_stats import StatsMixin
from pingpong_overview_web import WebMixin


class Overview(AdminMixin, StatsMixin, WebMixin):
    def __init__(self, root, port, web_port):
        self.root = root
        self.port = port
        self.web_port = web_port
        self.lamps = {}
        self.lock = threading.Lock()
        self.slots = {}  # band -> [node_id of INB holding the slot, time]
        self.error = ""
        self.web_error = ""
        self.fw_open = None  # web page's firewall rule: True / False / None = unknown
        self.snapshot = {"head": "starting…", "rows": [], "notes": []}
        self.operating = set()  # stats keys of PCs that transmitted since the ready check
        self.silenced = set()  # stats keys whose "not heard" alarm was acknowledged
        self._alarm_beeped = 0.0
        self._alarm_logged = set()
        self.newest = ""  # newest release on GitHub ("" = unknown)
        self._ping_now = threading.Event()
        self._ping_seq = 0
        self.self_update = None  # newer pingpong_overview.exe release, if any
        self._asked_update = False
        self._offer_scheduled = False
        self.stats_path = os.path.join(app_dir(), STATS_FILE)
        self.events_path = os.path.join(app_dir(), EVENTS_FILE)
        self._seen_state = {}  # what the event log last saw (master, lock, ...)
        self.stats = {}  # stats key -> {"name", "keys": {F1: n}, "other", "tx_time"}
        self.stats_since = time.time()
        self._load_stats()
        self._stats_saved = time.time()
        self._hex = {}
        self.stop = threading.Event()
        self.cfg = configparser.ConfigParser()
        self.cfg_path = os.path.join(app_dir(), SETTINGS_FILE)
        try:
            self.cfg.read(self.cfg_path, encoding="utf-8")
        except (OSError, configparser.Error):
            pass
        if not self.cfg.has_section("window"):
            self.cfg.add_section("window")
        if not self.cfg.has_section("stats"):
            self.cfg.add_section("stats")
        self._load_network_key()
        try:
            self.reset_at = float(self.cfg.get("stats", "reset_at", fallback="0") or 0)
        except ValueError:
            self.reset_at = 0.0
        self._build()
        threading.Thread(target=self._listen, daemon=True).start()
        if web_port:
            threading.Thread(target=self._serve_web, daemon=True).start()
            self._check_firewall()
        threading.Thread(target=self._release_loop, daemon=True).start()
        threading.Thread(target=self._ping_loop, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(REFRESH_MS, self._refresh)

    # ------------------------------------------------------------ network

    def _listen(self):
        try:
            sock = pp.open_socket("0.0.0.0", self.port)
        except OSError as exc:
            self.error = "cannot listen on UDP {}: {}".format(self.port, exc)
            return
        while not self.stop.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                self.stop.wait(0.3)
                continue
            data, status = pp.unseal(data, addr[0])
            if not pp.accepted(status):
                continue  # network password: not ours
            try:
                self._packet(addr[0], data)
            except Exception:
                pp.log_error("overview packet")

    def _lamp(self, node_id):
        lamp = self.lamps.get(node_id)
        if lamp is None:
            lamp = self.lamps[node_id] = Lamp(node_id)
        return lamp

    def _packet(self, src, data):
        now = time.time()
        relay = pp.parse_relay(data)
        if relay is not None:
            node_id, payload = relay
            is_radio, caption, station, is_tx, freq, mode = pp.packet_info(payload)
            if not is_radio:
                return
            with self.lock:
                lamp = self._lamp(node_id)
                lamp.radio_seen = now
                lamp.src = lamp.src or src
                if station:
                    lamp.n1mm_name = station
                if freq:
                    lamp.freq = freq
                if mode:
                    lamp.mode = mode
                running = pp.radio_running(payload)
                if running is not None:
                    lamp.running = running
                if is_tx and not lamp.tx:
                    lamp.tx_start = now
                    self._pingpong_tx(lamp, now)
                    self.operating.add(lamp.stats_key())  # ready check ends for it
                    self._count_key(lamp, payload, caption)
                if not is_tx and lamp.tx:
                    lamp.last_tx_end = now
                    dur = min(now - lamp.tx_start, 600.0)
                    st = self._stat(lamp)
                    st["tx_time"] += dur
                    hour = time.strftime("%Y-%m-%d %H", time.localtime(lamp.tx_start))
                    st["hours"][hour] = st["hours"].get(hour, 0.0) + dur
                if is_tx and caption:
                    lamp.caption = caption
                    lamp.last_caption = caption
                lamp.tx = is_tx
            return
        info = pp.parse_hello(data)
        if info:
            with self.lock:
                is_new = info["id"] not in self.lamps
                lamp = self._lamp(info["id"])
                lamp.hello = info
                lamp.hello_seen = now
                lamp.src = src
                replaced = self._replace_old(lamp)
                if replaced:
                    self._event(lamp.name(), "lamp restarted", "v" + info.get("version", "?"))
                elif is_new:
                    self._event(lamp.name(), "lamp on", "v" + info.get("version", "?"))
                if getattr(lamp, "gone", False):
                    lamp.gone = False
                    self._event(lamp.name(), "lamp heard again")
                self._track_activity(lamp, info, now)

    def _replace_old(self, lamp):
        """One row per PC: a restarted lamp (e.g. after an update) has a new
        id - drop the old entry of the same PC, keep its last transmission."""
        host = (lamp.hello.get("host") or "").lower()
        if not host:
            return False
        replaced = False
        for old_id, old in list(self.lamps.items()):
            if old is lamp or (old.hello.get("host") or "").lower() != host:
                continue
            if not lamp.last_tx_end and old.last_tx_end:
                lamp.last_caption, lamp.last_tx_end = old.last_caption, old.last_tx_end
            lamp.locked_since = lamp.locked_since or old.locked_since
            lamp.last_alert = lamp.last_alert or old.last_alert
            lamp.last_urgent = lamp.last_urgent or old.last_urgent
            for band, slot in self.slots.items():
                if slot[0] == old_id:
                    slot[0] = lamp.id
            del self.lamps[old_id]
            replaced = True
        return replaced

    # ------------------------------------------------------------ ready check / update

    # ------------------------------------------------------------ self-update

    def _problems(self, lamp, now, masters, target):
        """What is not ready on this lamp's PC (empty list = all fine)."""
        h = lamp.hello
        status = h.get("status", "")
        live = now - max(lamp.hello_seen, lamp.radio_seen)
        if live > GONE_AFTER:
            return ["not heard for {}".format(ago(live))]
        out = []
        ver = h.get("version", "")
        if target and ver and version_key(ver) < version_key(target):
            out.append("v{} – update to v{}".format(ver, target))
        if now - lamp.radio_seen > 30:
            out.append("no N1MM data")
        elif not lamp.key():
            out.append("band/mode not supported")
        if not masters:
            out.append("no master")
        elif len(masters) > 1 and lamp in masters:
            out.append("two masters")
        needs_keys = (
            "F12 call option on" in status or "block option on" in status
            or h.get("role") == "INB"
        )
        if needs_keys and "keyboard watcher off" in status:
            out.append("keyboard watcher not running")
        if lamp.ping_lost:
            out.append("no ping reply")
        elif lamp.ping_ms is not None and lamp.ping_ms > PING_SLOW:
            out.append("slow network ({:.0f} ms)".format(lamp.ping_ms))
        for problem in h.get("problems") or []:  # reported by the lamp itself (v1.69+)
            if problem not in out:
                out.append(problem)
        return out

    def _ready_check(self):
        """Start the ready check again: every row green/red until it transmits."""
        with self.lock:
            self.operating.clear()

    # ------------------------------------------------------------ ping

    EMERGENCY = {
        "restart": ("Restart all lamps",
                    "Every PingPong lamp closes and starts again (a few seconds without lamps).\n"
                    "Use it only if lamps hang or behave wrongly."),
        "stop": ("Stop all lamps",
                 "Every PingPong lamp is CLOSED: no lamps, no F12 calls, no ping-pong lock\n"
                 "until each lamp is started again by hand on its PC."),
    }

    # ------------------------------------------------------------ network password

    # ------------------------------------------------------------ setup

    # ------------------------------------------------------------ statistics

    # ------------------------------------------------------------ event log

    # ------------------------------------------------------------ alarm

    def _row_menu(self, event):
        """Right click on a station row: remove it from the list if its lamp
        is not heard any more (it comes back by itself when heard again)."""
        node_id = self.tree.identify_row(event.y)
        if not node_id:
            return
        now = time.time()
        with self.lock:
            lamp = self.lamps.get(node_id)
            if lamp is None:
                return
            name = lamp.name()
            quiet = now - lamp.hello_seen
        menu = tk.Menu(self.root, tearoff=0)
        if quiet > GONE_AFTER:
            menu.add_command(label="Remove {} from the list (not heard for {})".format(name, ago(quiet)),
                             command=lambda: self._remove_station(node_id))
        else:
            menu.add_command(label="{} is heard – only a station that is not heard can be "
                                   "removed".format(name), state=tk.DISABLED)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _remove_station(self, node_id):
        """Drop a dead / disconnected station: its rows, its alarm and its
        place in the ready check. Its statistics stay. If its lamp is heard
        again, it simply comes back (in the ready check until it transmits)."""
        now = time.time()
        with self.lock:
            lamp = self.lamps.get(node_id)
            if lamp is None or now - lamp.hello_seen <= GONE_AFTER:
                return ""
            key, name = lamp.stats_key(), lamp.name()
            for other_id in [k for k, l in self.lamps.items()
                             if l.stats_key() == key and now - l.hello_seen > GONE_AFTER]:
                del self.lamps[other_id]
            if not any(l.stats_key() == key for l in self.lamps.values()):
                self.operating.discard(key)
            self.silenced.discard(key)
            self._alarm_logged.discard(key)
            for band in [b for b, slot in self.slots.items() if slot[0] not in self.lamps]:
                del self.slots[band]
        self._event(name, "removed from the list")
        return name

    def _forget(self, lamp, now, heard_keys):
        """Drop a lamp from the table? An operating station that went
        silent stays (and keeps the alarm) until it is heard again under
        any lamp id, or for FORGET_SILENT."""
        quiet = now - max(lamp.hello_seen, lamp.radio_seen)
        if quiet <= FORGET_AFTER:
            return False
        key = lamp.stats_key()
        if key in self.operating and key not in heard_keys:
            return quiet > FORGET_SILENT
        return True

    def _silent(self, lamps, now):
        """[(name, seconds, acknowledged)] of operating stations whose lamp
        has not been heard for SILENT_AFTER (caller holds the lock)."""
        newest = {}
        for lamp in lamps:
            key = lamp.stats_key()
            if key in self.operating and lamp.hello_seen:
                if key not in newest or lamp.hello_seen > newest[key].hello_seen:
                    newest[key] = lamp
        result = []
        for key, lamp in sorted(newest.items()):
            quiet = now - lamp.hello_seen
            if quiet > SILENT_AFTER:
                result.append((lamp.name(), quiet, key in self.silenced))
            else:
                self.silenced.discard(key)  # heard again: a new silence alarms again
                self._alarm_logged.discard(key)
        return result

    def _show_alarm(self, alarm):
        """Flash the status text, beep, offer 'Silence alarm' (UI thread)."""
        now = time.time()
        if not alarm:
            if self.alarm_button.winfo_ismapped():
                self.alarm_button.pack_forget()
            if self.head.cget("bg") != self._head_bg:
                self.head.configure(bg=self._head_bg)
            return
        if not self.alarm_button.winfo_ismapped():
            self.alarm_button.pack(side=tk.LEFT, padx=(0, 6), before=self.pp_button)
        flash = int(now * 2) % 2 == 0
        self.head.configure(bg="#ffd0d0" if flash else self._head_bg)
        with self.lock:
            new = [l for l in self.lamps.values()
                   if l.name() in alarm and l.stats_key() not in self._alarm_logged]
            for lamp in new:
                self._alarm_logged.add(lamp.stats_key())
        for lamp in new:
            self._event(lamp.name(), "ALARM lamp not heard",
                        "for {:.0f} s".format(now - lamp.hello_seen))
        if now - self._alarm_beeped >= ALARM_BEEP:
            self._alarm_beeped = now
            self._beep()

    def _beep(self):
        def run():
            try:
                import winsound

                for _ in range(2):
                    winsound.Beep(1000, 180)
                    time.sleep(0.08)
            except Exception:
                pass

        threading.Thread(target=run, daemon=True).start()

    def _silence_alarm(self):
        now = time.time()
        with self.lock:
            names = []
            for lamp in self.lamps.values():
                key = lamp.stats_key()
                if key in self.operating and now - lamp.hello_seen > SILENT_AFTER:
                    self.silenced.add(key)
                    names.append(lamp.name())
        self._event("-", "alarm silenced", ", ".join(sorted(set(names))))

    # ----- planned reset (contest start)

    def _pp_limit(self):
        """Ping-pong release time the master reports (default 40 s)."""
        now = time.time()
        masters = self._masters(now)
        if len(masters) == 1 and masters[0].hello.get("pp_limit", "") != "":
            return pp.clamp_pp(masters[0].hello["pp_limit"])
        return PP_LIMIT

    def _pingpong_tx(self, lamp, now):
        """Same slot rule as the lamps: RUN frees the band, the first INB
        after that holds the slot until RUN transmits again."""
        key = lamp.key()
        if not key:
            return
        band = key[0]
        role = lamp.hello.get("role", "")
        if role == "RUN":
            self.slots.pop(band, None)
        elif role == "INB":
            slot = self.slots.get(band)
            if slot is None or pp.pp_expired(slot[1], now, self._pp_limit()):
                self.slots[band] = [lamp.id, now]
            elif slot[0] == lamp.id:
                slot[1] = now

    # ------------------------------------------------------------ web page

    # ------------------------------------------------------------ colors

    def _masters(self, now):
        """Lamps acting as master (only the master sends <BlockTime>)."""
        return [
            l for l in self.lamps.values()
            if now - l.hello_seen <= GONE_AFTER and l.hello.get("block_time")
        ]

    def _color_of(self, lamp, masters):
        color = lamp.hello.get("color", "")
        if color:
            return color
        key = "pc:" + (lamp.hello.get("host") or "").lower()
        for m in masters:
            got = (m.hello.get("colors") or {}).get(key)
            if got:
                return got
        return "red"

    def _hex_of(self, color):
        """Any Tk color name -> #rrggbb (the web page needs CSS colors)."""
        if color not in self._hex:
            try:
                r, g, b = (v // 256 for v in self.root.winfo_rgb(color))
                self._hex[color] = "#{:02x}{:02x}{:02x}".format(r, g, b)
            except tk.TclError:
                self._hex[color] = "#ff0000"
        return self._hex[color]

    @staticmethod
    def _text_on(hexcolor):
        r, g, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
        return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"

    # ------------------------------------------------------------ UI

    def _build(self):
        self.root.title("{}  v{}".format(TITLE, pp.__version__))
        # Two rows: status text + contest controls, then maintenance -
        # the window stays narrow.
        top = tk.Frame(self.root, padx=8, pady=4)
        top.pack(fill=tk.X)
        line1 = tk.Frame(top)  # status text left, contest buttons right
        line1.pack(fill=tk.X)
        row1 = tk.Frame(line1, pady=2)
        row1.pack(side=tk.RIGHT)
        self.head = tk.Label(line1, text="", anchor="w", justify=tk.LEFT,
                             font=("Segoe UI", 10, "bold"))
        self.head.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._head_bg = self.head.cget("bg")
        # The status text wraps in whatever room the buttons leave.
        line1.bind("<Configure>", lambda e: self.head.configure(
            wraplength=max(200, e.width - row1.winfo_reqwidth() - 12)))
        row2 = tk.Frame(top, pady=2)
        row2.pack(fill=tk.X)
        pad = {"side": tk.LEFT, "padx": (0, 6)}
        # Shown only while an operating station is not heard (alarm).
        self.alarm_button = tk.Button(row1, text="🔔 Silence alarm", bg="#c00000", fg="white",
                                      activebackground="#c00000", command=self._silence_alarm)
        self.pp_button = tk.Button(row1, text="Ping-pong lock: ?", width=20,
                                   command=self._toggle_pingpong)
        self.pp_button.pack(**pad)
        tk.Button(row1, text="Ready check", command=self._ready_check).pack(**pad)
        tk.Button(row1, text="Test all lamps", command=self._test_lamps).pack(**pad)
        emergency = tk.Menubutton(row1, text="Emergency ▾", fg="#b00020", relief=tk.RAISED,
                                  activeforeground="#b00020")
        menu = tk.Menu(emergency, tearoff=0)
        menu.add_command(label="Restart all lamps…", command=lambda: self._emergency("restart"))
        menu.add_command(label="Stop all lamps…", command=lambda: self._emergency("stop"))
        emergency.configure(menu=menu)
        emergency.pack(**pad)
        self.update_all_button = tk.Button(row2, text="Update all lamps…", command=self._update_all)
        self.update_all_button.pack(**pad)
        # Settings: station setup, network password, web page firewall, window.
        self.var_top = tk.BooleanVar(value=self.cfg.get("window", "on_top", fallback="no") == "yes")
        self.settings_button = tk.Menubutton(row2, text="Settings ▾", relief=tk.RAISED)
        self.settings_menu = tk.Menu(self.settings_button, tearoff=0,
                                     postcommand=self._fill_settings_menu)
        self.settings_button.configure(menu=self.settings_menu)
        self.settings_button.pack(**pad)
        # Statistics: reset now, or at the contest start (planned, shown on the button).
        self.stats_button = tk.Menubutton(row2, text="Statistics ▾", relief=tk.RAISED)
        self.stats_menu = tk.Menu(self.stats_button, tearoff=0, postcommand=self._fill_stats_menu)
        self.stats_button.configure(menu=self.stats_menu)
        self.stats_button.pack(**pad)
        self._stats_button_bg = self.stats_button.cget("bg")
        # Shown only when a newer Overview is on GitHub (exe only).
        self.update_button = tk.Button(row2, text="Update Overview…", command=self._update_self,
                                       bg="#ffe08a")
        self._update_slot = tk.Frame(row2)  # keeps the yellow button's place in the row
        self._update_slot.pack(side=tk.LEFT)
        self._pp_sent = 0.0
        self._on_top(save=False)

        style = ttk.Style(self.root)
        style.configure("Treeview", rowheight=24, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        self.book = ttk.Notebook(self.root)
        self.book.pack(fill=tk.BOTH, expand=True, padx=8)
        frame = tk.Frame(self.book)
        self.book.add(frame, text="Stations")
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in COLUMNS], show="headings",
                                 selectmode="none")
        for cid, text, width, anchor in COLUMNS:
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=width, anchor=anchor,
                             stretch=False)  # keeps widths: scroll sideways in a narrow window
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        xscroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=xscroll.set)
        xscroll.pack(side=tk.BOTTOM, fill=tk.X)  # narrow window: scroll sideways
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Button-3>", self._row_menu)  # right click: remove a dead station
        sframe = tk.Frame(self.book)
        self.book.add(sframe, text="Function keys")
        bar = tk.Frame(sframe, pady=4)
        bar.pack(fill=tk.X)
        self.stats_label = tk.Label(bar, text="", anchor="w")
        self.stats_label.pack(side=tk.LEFT)
        tk.Button(bar, text="Reset…", command=self._reset_stats).pack(side=tk.RIGHT)
        tk.Button(bar, text="Save CSV…", command=self._export_csv).pack(side=tk.RIGHT, padx=6)
        scols = ["station"] + FKEYS + ["other", "total", "txtime"]
        self.stree = ttk.Treeview(sframe, columns=scols, show="headings", selectmode="none")
        for cid, text in zip(scols, ["Station"] + FKEYS + ["Other", "Total", "TX time"]):
            self.stree.heading(cid, text=text)
            self.stree.column(cid, width=110 if cid == "station" else (75 if cid in ("total", "txtime") else 48),
                              anchor="w" if cid == "station" else "center", stretch=cid == "station")
        self.stree.tag_configure("all", font=("Segoe UI", 10, "bold"))
        self.stree.pack(fill=tk.BOTH, expand=True)

        aframe = tk.Frame(self.book)
        self.book.add(aframe, text="Activity")
        abar = tk.Frame(aframe, pady=4)
        abar.pack(fill=tk.X)
        tk.Label(abar, text="Ping-pong waiting, calls and TX time per station (same Reset / "
                 "Save CSV as Function keys)", anchor="w").pack(side=tk.LEFT)
        tk.Button(abar, text="Contest report…", command=self._contest_report).pack(side=tk.RIGHT)
        tk.Button(abar, text="Open event log", command=self._open_events).pack(side=tk.RIGHT, padx=6)
        tk.Button(abar, text="Ping now", command=self._ping_now.set).pack(side=tk.RIGHT, padx=6)
        acols = ["station", "locked", "ltime", "f12", "urgent", "tx", "pavg", "pmax", "plost"]
        self.atree = ttk.Treeview(aframe, columns=acols, show="headings", selectmode="none", height=5)
        for cid, text, w in zip(acols, ["Station", "Locked (WAIT)", "Locked time", "F12 calls",
                                         "Urgent calls", "TX time", "Ping avg", "Ping max", "Ping lost"],
                                (140, 100, 90, 80, 90, 80, 75, 75, 70)):
            self.atree.heading(cid, text=text)
            self.atree.column(cid, width=w, anchor="w" if cid == "station" else "center",
                              stretch=cid == "station")
        self.atree.tag_configure("all", font=("Segoe UI", 10, "bold"))
        self.atree.pack(fill=tk.X)
        tk.Label(aframe, text="TX minutes per hour", anchor="w", pady=4,
                 font=("Segoe UI", 9, "bold")).pack(fill=tk.X)
        self.heat = tk.Canvas(aframe, height=120, bg="white", highlightthickness=0)
        self.heat.pack(fill=tk.BOTH, expand=True)
        self._heat_key = None
        for tag, (bg, fg) in ROW_STYLE.items():
            opts = {"foreground": fg}
            if bg:
                opts["background"] = bg
            self.tree.tag_configure(tag, **opts)

        self.foot = tk.Label(self.root, text="", anchor="w", justify=tk.LEFT, padx=8, pady=6,
                             font=("Segoe UI", 9), wraplength=1200)
        self.foot.pack(fill=tk.X)
        geo = self.cfg.get("window", "geometry", fallback="")
        self.root.geometry(geo or "900x400")

    def _fill_tree(self, tree, attr, rows):
        """Put rows into a Treeview, but only if they differ from what it shows."""
        rows = [list(r) for r in rows]
        if getattr(self, attr, None) == rows:
            return False
        setattr(self, attr, rows)
        tree.delete(*tree.get_children())
        for row in rows:
            tree.insert("", tk.END, values=row, tags=("all",) if row[0] == "All" else ())
        return True

    def _draw_heat(self, act):
        """TX minutes per hour: one row per station, darker = more TX."""
        key = repr((act["hours"], act["heat"], self.heat.winfo_width()))
        if key == self._heat_key:
            return
        self._heat_key = key
        c = self.heat
        c.delete("all")
        if not act["hours"]:
            c.create_text(10, 20, anchor="w", text="no transmissions yet", fill="#888")
            return
        left, top, cw, ch = 130, 20, 52, 22
        for i, label in enumerate(act["hours"]):
            c.create_text(left + i * cw + cw / 2, 10, text=label, font=("Segoe UI", 8), fill="#555")
        for r, (name, mins) in enumerate(act["heat"]):
            y = top + r * ch
            c.create_text(6, y + ch / 2, anchor="w", text=name, font=("Segoe UI", 9))
            for i, v in enumerate(mins):
                share = min(v / 30.0, 1.0)  # 30 min of TX in an hour = darkest
                shade = int(240 - 180 * share)
                fill = "#{:02x}{:02x}ff".format(shade, shade) if v else "#f4f4f4"
                x = left + i * cw
                c.create_rectangle(x + 1, y + 1, x + cw - 1, y + ch - 1, fill=fill, outline="")
                if v:
                    c.create_text(x + cw / 2, y + ch / 2, text="{:g}'".format(v),
                                  font=("Segoe UI", 8), fill="white" if share > 0.5 else "#222")
        c.configure(height=max(60, top + len(act["heat"]) * ch + 6))

    def _fill_settings_menu(self):
        """Settings ▾, built when opened so it shows the current state."""
        m = self.settings_menu
        m.delete(0, tk.END)
        m.add_command(label="Save setup…  (names, colors, master settings)",
                      command=self._save_setup)
        m.add_command(label="Restore setup…", command=self._restore_setup)
        m.add_separator()
        m.add_command(label="Network password…  ({})".format("ON ✔" if pp.AUTH["key"] else "off"),
                      command=self._choose_network_password)
        if self.web_port and sys.platform == "win32":
            m.add_command(
                label="Web page in Windows Firewall: allowed ✔  (add the rule again…)"
                if self.fw_open else "Allow web page in Windows Firewall…  (TCP {})".format(
                    self.web_port),
                command=self._allow_firewall)
        m.add_separator()
        m.add_checkbutton(label="Always on top", variable=self.var_top, command=self._on_top)

    def _on_top(self, save=True):
        self.root.attributes("-topmost", bool(self.var_top.get()))
        if save:
            self.cfg.set("window", "on_top", "yes" if self.var_top.get() else "no")
            self._save()

    def _save(self):
        try:
            with open(self.cfg_path, "w", encoding="utf-8") as fh:
                self.cfg.write(fh)
        except OSError:
            pass

    def _close(self):
        self._save_stats()
        try:
            self.cfg.set("window", "geometry", self.root.geometry())
            self._save()
        except Exception:
            pass
        self.stop.set()
        self.root.destroy()

    # ------------------------------------------------------------ table data

    def _row(self, lamp, now, masters, target=""):
        h = lamp.hello
        problems = self._problems(lamp, now, masters, target)
        ready_mode = lamp.stats_key() not in self.operating
        key = lamp.key()
        band, mode = key if key else ("", "")
        status = h.get("status", "")
        live = now - max(lamp.hello_seen, lamp.radio_seen)
        tx = ""
        if lamp.tx and now - lamp.radio_seen < 10:
            tx = "TX  {}  ({})".format(lamp.caption or "?", ago(now - lamp.tx_start))
        last = ""
        if lamp.last_caption and lamp.last_tx_end:
            last = "{}  · {} ago".format(lamp.last_caption, ago(now - lamp.last_tx_end))
        calls = []
        if h.get("urgent"):
            calls.append("{} ({} {})".format("PRIORITY" if h.get("urgent_priority") else "URGENT",
                                             h.get("urgent_band", ""), h.get("urgent_mode", "")))
        if h.get("alert"):
            calls.append("F12 {} call ({} MHz)".format(
                "Run" if h.get("alert_run") else "S&P", h.get("alert_band", "?")))
        keys = []
        if "F-keys BLOCKED" in status:
            keys.append("F-keys blocked")
        if "(LOCKED)" in status:
            keys.append("ping-pong WAIT")
        slot = self.slots.get(band)
        if slot and slot[0] == lamp.id and not pp.pp_expired(slot[1], now, self._pp_limit()):
            keys.append("holds slot")
        name = lamp.name() + ("  ★" if lamp in masters else "")
        values = {
            "station": name,
            "check": ("✖ " + "; ".join(problems)) if problems else "✔ OK",
            "pc": h.get("host", "") or lamp.src,
            "band": band,
            "mode": mode,
            "freq": pp.freq_khz(lamp.freq) if lamp.freq and now - lamp.radio_seen < 60 else "",
            "run": {True: "Run", False: "S&P", None: "?"}[lamp.is_running()],
            "role": h.get("role", "") or "–",
            "tx": tx,
            "last": last,
            "calls": ", ".join(calls),
            "keys": ", ".join(keys),
            "ver": h.get("version", "") or "?",
            "ping": ("lost" if lamp.ping_lost else
                     fmt_ms(lamp.ping_ms) if lamp.ping_ms is not None else ""),
            "seen": ago(live),
        }
        bg, fg = "", ""
        if ready_mode:
            # Before this station starts operating: green = ready, red = not.
            tag = "ready_bad" if problems else "ready_ok"
        elif lamp.stats_key() in self.operating and now - lamp.hello_seen > SILENT_AFTER:
            tag = "silent"
        elif live > GONE_AFTER:
            tag = "gone"
        elif h.get("urgent"):
            tag = "urgent"
        elif tx:
            tag = "tx"
            bg = self._hex_of(self._color_of(lamp, masters))
            fg = self._text_on(bg)
        elif h.get("alert"):
            tag = "call"
        else:
            tag = ""
        if tag in ROW_STYLE:
            bg, fg = ROW_STYLE[tag]
        return {
            "id": lamp.id, "values": values, "tag": tag, "bg": bg, "fg": fg,
            "sort": (band_sort(band), mode, name.lower()),
        }

    def _make_snapshot(self):
        now = time.time()
        with self.lock:
            heard_keys = {l.stats_key() for l in self.lamps.values()
                          if now - l.hello_seen <= SILENT_AFTER}
            for node_id in [
                k for k, l in self.lamps.items()
                if self._forget(l, now, heard_keys)
            ]:
                del self.lamps[node_id]
            lamps = list(self.lamps.values())
            masters = self._masters(now)
            live_all = [l for l in lamps if now - l.hello_seen <= GONE_AFTER]
            target = self._target_version(live_all)
            rows = [self._row(lamp, now, masters, target) for lamp in lamps]
            waiting = [l for l in lamps if l.stats_key() not in self.operating]
            slots = dict(self.slots)
            silent = self._silent(lamps, now)
        rows.sort(key=lambda r: r["sort"])
        live = [l for l in lamps if now - l.hello_seen <= GONE_AFTER]
        warn = False
        pp_state = ""  # ping-pong lock as the master reports it ("1", "0", "")
        if len(masters) == 1:
            m = masters[0]
            head = "{} lamps  ·  master: {}  ·  F12: S&P {:g} s, Run {:g} s".format(
                len(live), m.name(), m.hello.get("block_time", 0) or 30,
                m.hello.get("block_time_run", 0) or 5)
            pp_set = m.hello.get("pingpong", "")
            pp_state = pp_set
            if pp_set:
                head += "  ·  ping-pong lock: " + ("ON" if pp_set == "1" else "OFF")
                limit = m.hello.get("pp_limit", "")
                if pp_set == "1" and limit != "":
                    secs = pp.clamp_pp(limit)
                    head += " (release {})".format("{:g} s".format(secs) if secs else "only by RUN")
        elif masters:
            warn = True
            head = "{} lamps  ·  ⚠ TWO MASTERS: {}".format(
                len(live), ", ".join(sorted(m.name() for m in masters)))
        else:
            head = "{} lamps  ·  no master heard".format(len(live))
        if self.fw_open and self.web_port:
            head += "  ·  web 0.0.0.0:{}".format(self.web_port)
        ready = None
        alarm = [name for name, _secs, acked in silent if not acked]
        if silent:
            warn = True
            head = "🔔 NOT HEARD: {}  ·  ".format(", ".join(
                "{} {}{}".format(name, ago(secs), " (silenced)" if acked else "")
                for name, secs, acked in silent)) + head
        if waiting:
            bad = [r for r in rows if r["tag"] == "ready_bad"]
            if bad:
                ready = "bad"
                head = "✖ NOT READY ({} of {})  ·  ".format(len(bad), len(waiting)) + head
            else:
                ready = "ok"
                head = "✔ READY  ·  " + head
        notes = []
        for err in (self.error, self.web_error):
            if err:
                notes.append("⚠ " + err)
        versions = sorted({l.hello.get("version") for l in live if l.hello.get("version")},
                          key=version_key)
        if len(versions) > 1:
            notes.append("⚠ different versions: " + ", ".join("v" + v for v in versions))
        for band, slot in sorted(slots.items(), key=lambda s: band_sort(s[0])):
            if not pp.pp_expired(slot[1], now, self._pp_limit()) and slot[0] in self.lamps:
                notes.append("ping-pong {} MHz: slot held by {}".format(band, self.lamps[slot[0]].name()))
        for text in pp.auth_problems(now, where="Settings ▾ → Network password…"):
            warn = True
            notes.insert(0, "⚠ " + text)
        no_relay = [l.name() for l in live if now - l.radio_seen > 30]
        if no_relay:
            notes.append("no N1MM data from: {} (N1MM not running, or 'Share my N1MM' off)".format(
                ", ".join(sorted(no_relay))))
        notes.append("★ = master")
        for r in rows:
            r.pop("sort")
        activity, stats = self._tables(now)
        return {
            "activity": activity,
            "stats": stats,
            "title": "{} v{}".format(TITLE, pp.__version__),
            "head": head, "warn": warn, "ready": ready, "rows": rows, "notes": notes,
            "alarm": alarm,
            "pingpong": pp_state,
            "master_name": masters[0].name() if len(masters) == 1 else "",
            "pp_limit": "{:g}".format(self._pp_limit()) if pp_state else "",
            "versions": {"target": target, "old": sum(
                1 for l in live_all
                if version_key(l.hello.get("version", "")) < version_key(target))},
            "columns": [[cid, dict((c[0], c[1]) for c in COLUMNS)[cid]] for cid in WEB_ORDER],
            "time": time.strftime("%H:%M:%S"),
        }

    def _refresh(self):
        try:
            self._refresh_body()
        except Exception:
            pp.log_error("overview display")
        self.root.after(REFRESH_MS, self._refresh)

    def _refresh_body(self):
        if (pp.password_needed(time.time())[0] or pp.password_removed(time.time())[0]) \
                and time.time() - getattr(self, "_key_checked", 0) > 5:
            self._load_network_key()  # the master's lamp may have just set or removed one
        self._check_planned_reset(time.time())
        self._show_reset_plan()
        if self.self_update is not None:
            if not self.update_button.winfo_ismapped():
                self.update_button.configure(
                    text="Update Overview to v{}…".format(self.self_update["version"]))
                self.update_button.pack(side=tk.LEFT, padx=(0, 6), before=self._update_slot)
            if not self._offer_scheduled:
                self._offer_scheduled = True  # ask once per start
                self.root.after(0, self._offer_self_update)
        snap = self._make_snapshot()
        self.snapshot = snap  # served to the web page as it is
        with self.lock:
            lamps = list(self.lamps.values())
        self._log_changes(snap, lamps, time.time())
        wanted = [r["id"] for r in snap["rows"]]
        for item in self.tree.get_children():
            if item not in wanted:
                self.tree.delete(item)
        for index, row in enumerate(snap["rows"]):
            tag = row["tag"]
            if tag == "tx":
                tag = "tx_" + row["id"]
                self.tree.tag_configure(tag, background=row["bg"], foreground=row["fg"])
            vals = [row["values"][c[0]] for c in COLUMNS]
            tags = (tag,) if tag else ()
            if self.tree.exists(row["id"]):
                self.tree.item(row["id"], values=vals, tags=tags)
                if self.tree.index(row["id"]) != index:
                    self.tree.move(row["id"], "", index)
            else:
                self.tree.insert("", index, iid=row["id"], values=vals, tags=tags)
        vers = snap.get("versions") or {}
        if vers.get("old"):
            text = "Update {} lamp{}…".format(vers["old"], "" if vers["old"] == 1 else "s")
        elif vers.get("target"):
            text = "All lamps v{} ✔".format(vers["target"])
        else:
            text = "Update all lamps…"
        if self.update_all_button.cget("text") != text:
            self.update_all_button.configure(text=text)
        pp_state = snap.get("pingpong", "")
        if time.time() - self._pp_sent > 3:  # after a click, wait for the master's answer
            text, bg = {"1": ("Ping-pong lock: ON", "#3fb950"),
                        "0": ("Ping-pong lock: OFF", "#d0d0d0")}.get(pp_state, ("Ping-pong lock: ?", "#d0d0d0"))
            if self.pp_button.cget("text") != text:
                self.pp_button.configure(text=text, bg=bg, activebackground=bg)
        self.head.configure(text=snap["head"], fg=(
            "#c00000" if snap["warn"] or snap["ready"] == "bad"
            else "#1f8a3b" if snap["ready"] == "ok" else "black"))
        self._show_alarm(snap.get("alarm") or [])
        label = "Transmissions started by each function key, since {}".format(snap["stats"]["since"])
        if self.stats_label.cget("text") != label:
            self.stats_label.configure(text=label)
        # Rebuild the two tables only when their content changed.
        self._fill_tree(self.stree, "_shown_stats", snap["stats"]["rows"])
        act = snap["activity"]
        self._fill_tree(self.atree, "_shown_activity", act["rows"])
        self._draw_heat(act)
        if time.time() - self._stats_saved > STATS_SAVE:
            self._save_stats()
        notes = list(snap["notes"])
        if self.fw_open is False and self.web_port:
            notes.append("web page: other devices need Settings ▾ → Allow web page in Windows Firewall")
        if self.reset_at:
            notes.insert(0, "statistics reset planned: {} (in {})".format(
                fmt_when(self.reset_at), ago(max(0, self.reset_at - time.time()))))
        if self.web_port and not self.web_error:
            notes.append("web page: " + "  or  ".join(self.web_urls()[:3]))
        self.foot.configure(text="   ·   ".join(notes))


def main():
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--discovery-port", type=int, default=46070,
                        help="port the PingPong lamps talk on (default 46070)")
    parser.add_argument("--web-port", type=int, default=WEB_PORT,
                        help="port of the web page (default 8046, 0 = no web page)")
    args = parser.parse_args()
    after_update = os.environ.pop("PINGPONG_AFTER_UPDATE", "") == "1"
    if not pp.claim_single_instance(15.0 if after_update else 3.0, pp.OVERVIEW_MUTEX):
        hidden = tk.Tk()
        hidden.withdraw()
        messagebox.showinfo(TITLE, "PingPong Overview is already running on this PC.",
                            parent=hidden)
        hidden.destroy()
        return
    root = tk.Tk()
    try:
        root.iconbitmap(default=resource("pingpong.ico"))
    except tk.TclError:
        pass
    Overview(root, args.discovery_port, args.web_port)
    root.mainloop()


if __name__ == "__main__":
    main()
