"""PingPong Overview - one live table of all PingPong stations.

A separate, read-only program, meant for the master PC. It shows the same
live table in two places:

- its own window, and
- a web page, http://<this PC>:8046, for any browser on the network
  (other PCs, a phone or a tablet) - nothing to install there.

It only LISTENS to what the PingPong lamps already broadcast on UDP 46070:

- PingPongHello (every 3 s): name, PC, version, role, master, F12 /
  urgent calls, F-key block and ping-pong lock state, N1MM band/mode;
- relay packets: each lamp's own N1MM RadioInfo (frequency, mode,
  transmitting, function key caption, Run/S&P).

It never opens N1MM's ports (12060, the lamps' 12062),
so it runs next to a lamp without taking N1MM's packets away from it.
Stations whose PC runs no lamp are not shown.
"""

import argparse
import configparser
import json
import os
import re
import socket
import sys
import threading
import time
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tkinter import filedialog, messagebox, simpledialog, ttk

import n1mm_lamps as pp
import pingpong_update as upd

TITLE = "PingPong Overview"
SETTINGS_FILE = "pingpong_overview.ini"
WEB_PORT = 8046
GONE_AFTER = pp.PEER_TIMEOUT  # s: lamp shown grey
FORGET_AFTER = 120.0  # s: lamp removed from the table
PP_LIMIT = pp.PP_LIMIT  # s: ping-pong slot frees itself
REFRESH_MS = 250
FIREWALL_RULE = "PingPong Overview web page"
EVENTS_FILE = "pingpong_events.csv"  # contest timeline, ";"-separated (Excel)
STATS_FILE = "pingpong_stats.json"  # function key counts, kept over restarts
STATS_SAVE = 30.0  # s
FKEYS = ["F{}".format(n) for n in range(1, 13)]
CAPTION_KEY = re.compile(rb"<FunctionKeyCaption>\s*(F\d{1,2})\b", re.IGNORECASE)

STATUS_N1MM = re.compile(r"N1MM (\S+) (\S+) from [^,]*, (Run|S&P)")

COLUMNS = [
    # id, heading, width, anchor
    ("station", "Station", 110, "w"),
    ("check", "Check", 170, "w"),
    ("pc", "PC", 100, "w"),
    ("band", "Band", 50, "center"),
    ("mode", "Mode", 50, "center"),
    ("freq", "Frequency", 95, "e"),
    ("run", "Run/S&P", 65, "center"),
    ("role", "Role", 50, "center"),
    ("tx", "Now", 150, "w"),
    ("last", "Last TX", 150, "w"),
    ("calls", "Calls", 150, "w"),
    ("keys", "Keys", 110, "w"),
    ("ver", "Version", 60, "center"),
    ("ping", "Ping", 60, "e"),
    ("seen", "Seen", 55, "e"),
]
# Web page order: what matters during the contest first (phones scroll).
WEB_ORDER = ["station", "check", "band", "mode", "run", "role", "tx", "calls", "keys",
             "freq", "last", "pc", "ver", "ping", "seen"]
ROW_STYLE = {  # tag -> (background, text); "tx" uses the station color
    "gone": ("", "#999999"),
    "urgent": ("#ff3030", "#ffffff"),
    "call": ("#ffe08a", "#000000"),
    "ready_ok": ("#3fb950", "#ffffff"),   # before the station starts operating
    "ready_bad": ("#8b1a1a", "#ffffff"),  # darker than the "red" station color
}
OVERVIEW_ASSET = "pingpong_overview.exe"
REMOTE_UPDATE_FROM = (1, 60)
PING_FROM = (1, 73)  # lamps from this version answer pings
PING_EVERY = 300.0  # s: automatic ping round every 5 minutes
PING_WAIT = 2.0  # s: answers later than this count as lost
PING_SLOW = 500.0  # ms: slower than this is a problem in the ready check  # lamps from this version accept "update"
RELEASE_CHECK = 1800.0  # s: how often to look for the newest release


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource(name):
    return os.path.join(getattr(sys, "_MEIPASS", app_dir()), name)


def ago(seconds):
    seconds = int(max(0, seconds))
    if seconds < 60:
        return "{} s".format(seconds)
    if seconds < 3600:
        return "{}:{:02d} min".format(seconds // 60, seconds % 60)
    return "{} h".format(seconds // 3600)


def ago_long(seconds):
    """Total time as h:mm:ss."""
    seconds = int(seconds)
    return "{}:{:02d}:{:02d}".format(seconds // 3600, seconds // 60 % 60, seconds % 60)


def tx_minutes(seconds):
    """TX seconds as minutes, 1 decimal; any transmission shows at least 0.1."""
    if seconds <= 0:
        return 0
    return max(0.1, round(seconds / 60.0, 1))


def fmt_ms(ms):
    """Round-trip time: one decimal below 10 ms (fast LAN), else whole ms."""
    return "{:.1f} ms".format(ms) if ms < 10 else "{:.0f} ms".format(ms)


def band_sort(band):
    try:
        return float(band)
    except (TypeError, ValueError):
        return 999.0


def version_key(text):
    return tuple(int(x) for x in re.findall(r"\d+", text or ""))


class Lamp:
    """Everything known about one PingPong lamp (= one PC)."""

    def __init__(self, node_id):
        self.id = node_id
        self.hello = {}
        self.hello_seen = 0.0
        self.src = ""
        # from relayed N1MM RadioInfo
        self.radio_seen = 0.0
        self.freq = ""
        self.mode = ""
        self.running = None
        self.tx = False
        self.caption = ""
        self.tx_start = 0.0
        self.last_caption = ""
        self.last_tx_end = 0.0
        self.n1mm_name = ""
        self.locked_since = 0.0  # ping-pong WAIT started (activity statistics)
        self.last_alert = 0.0  # last F12 call counted (its AlertTime)
        self.last_urgent = 0.0  # last urgent call counted (its UrgentTime)
        self.ping_ms = None  # last round-trip time to this lamp, ms
        self.ping_lost = False  # no answer to the last ping

    def stats_key(self):
        """Stats belong to the PC (the lamp id changes at every restart)."""
        host = (self.hello.get("host") or "").lower()
        return "pc:" + host if host else "n1mm:" + self.n1mm_name.lower()

    def name(self):
        return self.hello.get("alias") or self.hello.get("host") or self.n1mm_name or self.id

    def key(self):
        """(band, mode) from the relayed N1MM data, else from the hello status."""
        if self.freq and time.time() - self.radio_seen < 60:
            grp = pp.group_of(self.freq, self.mode)
            if grp:
                return grp
        m = STATUS_N1MM.search(self.hello.get("status", ""))
        if m:
            return m.group(1), m.group(2)
        return None

    def is_running(self):
        if self.running is not None and time.time() - self.radio_seen < 30:
            return self.running
        m = STATUS_N1MM.search(self.hello.get("status", ""))
        if m:
            return m.group(3) == "Run"
        return None


class Overview:
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

    def _release_loop(self):
        self.stop.wait(3.0)
        while not self.stop.is_set():
            try:
                releases = upd.fetch_releases()
                if releases:
                    self.newest = releases[0]["version"]
                if upd.is_frozen():
                    mine = upd.fetch_releases(OVERVIEW_ASSET)
                    self.self_update = upd.newer_release(mine, pp.__version__)
            except Exception:
                pass  # no internet: compare the lamps with each other only
            self.stop.wait(RELEASE_CHECK)

    # ------------------------------------------------------------ self-update

    def _offer_self_update(self):
        """UI thread: once per start, offer a newer Overview."""
        rel = self.self_update
        if rel is None or self._asked_update:
            return
        self._asked_update = True
        if messagebox.askyesno(
            TITLE + " - update",
            "PingPong Overview v{} is available (you have v{}).\n\n"
            "Update now? The Overview restarts in a few seconds;\n"
            "its statistics and window settings are kept.".format(rel["version"], pp.__version__),
            parent=self.root,
        ):
            self._update_self(rel)

    def _update_self(self, rel=None):
        rel = rel or self.self_update
        if rel is None:
            return
        target = upd.exe_path()
        self.update_button.configure(text="Downloading v{}…".format(rel["version"]), state=tk.DISABLED)

        def work():
            try:
                new_file = upd.download(rel, target)
                upd.swap_in(target, pp.__version__, new_file)
                self.root.after(0, lambda: self._restart(target))
            except Exception as exc:
                msg = str(exc)
                self.root.after(0, lambda: self._update_failed(rel, msg))

        threading.Thread(target=work, daemon=True).start()

    def _update_failed(self, rel, msg):
        self.update_button.configure(text="Update Overview…", state=tk.NORMAL)
        messagebox.showerror(
            TITLE, "Update to v{} failed:\n{}\n\nThis version (v{}) is unchanged.".format(
                rel["version"], msg, pp.__version__), parent=self.root)

    def _restart(self, target):
        self._save_stats()
        try:
            upd.relaunch(target, sys.argv[1:])
        except OSError as exc:
            messagebox.showerror(TITLE, "Could not start the new version:\n{}".format(exc),
                                 parent=self.root)
            return
        self._close()

    def _target_version(self, live):
        versions = [l.hello.get("version") for l in live if l.hello.get("version")]
        if self.newest:
            versions.append(self.newest)
        return max(versions, key=version_key) if versions else ""

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

    def _ping_loop(self):
        """Every 5 min (and on 'Ping now'): round-trip time to every lamp."""
        self.stop.wait(10.0)
        while not self.stop.is_set():
            try:
                self._ping_round()
            except Exception:
                pp.log_error("ping")
            self._ping_now.wait(PING_EVERY)
            self._ping_now.clear()

    def _ping_round(self):
        import select

        if not self._on_master_pc(time.time()):
            return  # lamps answer only the master PC
        self._ping_seq += 1
        seq = self._ping_seq
        payload = pp.make_command("ping", seq=seq)
        socks = []
        for ip, bcast in pp.local_networks():
            try:
                sock = pp._sender_socket(ip)
            except OSError:
                continue
            for target in (bcast, "255.255.255.255"):
                try:
                    sock.sendto(payload, (target, self.port))
                except OSError:
                    pass
            socks.append(sock)
        sent = time.time()
        replies = {}
        try:
            while socks:
                left = sent + PING_WAIT - time.time()
                if left <= 0:
                    break
                ready, _, _ = select.select(socks, [], [], left)
                for sock in ready:
                    try:
                        data, _addr = sock.recvfrom(4096)
                    except OSError:
                        continue
                    pong = pp.parse_pong(data)
                    if pong and pong["seq"] == seq and pong["id"] not in replies:
                        replies[pong["id"]] = (time.time() - sent) * 1000.0
        finally:
            for sock in socks:
                sock.close()
        self._ping_results(replies, time.time())

    def _ping_results(self, replies, now):
        with self.lock:
            for lamp in list(self.lamps.values()):
                if now - lamp.hello_seen > GONE_AFTER:
                    continue
                st = self._stat(lamp)
                if lamp.id in replies:
                    ms = replies[lamp.id]
                    lamp.ping_ms, lamp.ping_lost = ms, False
                    st["ping_sum"] = st.get("ping_sum", 0.0) + ms
                    st["ping_n"] = st.get("ping_n", 0) + 1
                    st["ping_max"] = max(st.get("ping_max", 0.0), ms)
                    if ms > PING_SLOW:
                        self._event(lamp.name(), "slow ping", "{:.0f} ms".format(ms))
                elif version_key(lamp.hello.get("version", "")) >= PING_FROM:
                    lamp.ping_ms, lamp.ping_lost = None, True
                    st["ping_lost"] = st.get("ping_lost", 0) + 1
                    self._event(lamp.name(), "no ping reply")

    def _send_command(self, payload):
        sent = 0
        try:
            nets = pp.local_networks()
        except Exception:
            nets = []
        for ip, bcast in nets:
            try:
                sock = pp._sender_socket(ip)
            except OSError:
                continue
            try:
                for target in (bcast, "255.255.255.255"):
                    try:
                        sock.sendto(payload, (target, self.port))
                        sent += 1
                    except OSError:
                        pass
            finally:
                sock.close()
        return sent

    EMERGENCY = {
        "restart": ("Restart all lamps",
                    "Every PingPong lamp closes and starts again (a few seconds without lamps).\n"
                    "Use it only if lamps hang or behave wrongly."),
        "stop": ("Stop all lamps",
                 "Every PingPong lamp is CLOSED: no lamps, no F12 calls, no ping-pong lock\n"
                 "until each lamp is started again by hand on its PC."),
    }

    def _emergency(self, action):
        """Emergency only: restart / stop every lamp. Asks twice - Yes, then
        the word YES typed and Enter - before anything is sent."""
        title, text = self.EMERGENCY[action]
        if not self._on_master_pc(time.time()):
            messagebox.showwarning(TITLE, "This PC is not the master PC – the lamps take this "
                                   "only from the master PC.", parent=self.root)
            return
        if not messagebox.askyesno(TITLE + " - EMERGENCY", "{} – are you sure?\n\n{}".format(title, text),
                                   icon="warning", default="no", parent=self.root):
            return
        typed = simpledialog.askstring(
            TITLE + " - EMERGENCY",
            "{}: type YES and press Enter to confirm.".format(title), parent=self.root)
        if (typed or "").strip() != "YES":
            messagebox.showinfo(TITLE, "Cancelled – nothing was sent.", parent=self.root)
            return
        self._event("-", title + " (sent)")
        if not self._send_command(pp.make_command(action)):
            messagebox.showerror(TITLE, "Could not send the request (no network).", parent=self.root)
            return
        messagebox.showinfo(TITLE, "{}: sent to all lamps.".format(title), parent=self.root)

    def _test_lamps(self):
        """Every lamp flashes its own name and role for 3 s."""
        if not self._on_master_pc(time.time()):
            messagebox.showwarning(TITLE, "This PC is not the master PC – the lamps take this "
                                   "only from the master PC.", parent=self.root)
            return
        self._event("-", "lamp test (sent)")
        if not self._send_command(pp.make_command("test")):
            messagebox.showerror(TITLE, "Could not send the request (no network).", parent=self.root)

    def _toggle_pingpong(self):
        """Ping-pong lock ON <-> OFF: asks the master lamp, which passes the
        setting on to every lamp (the button follows the master's hello)."""
        current = self.snapshot.get("pingpong", "")
        if not self._on_master_pc(time.time()):
            messagebox.showwarning(TITLE, "This PC is not the master PC – the lamps take this "
                                   "only from the master PC. Run the Overview there.",
                                   parent=self.root)
            return
        if current not in ("1", "0"):
            messagebox.showinfo(TITLE, "No master heard yet (or the master is older than "
                                "v1.67) - set it on the master's lamp under Options.",
                                parent=self.root)
            return
        value = "0" if current == "1" else "1"
        self._event("-", "ping-pong lock " + ("ON" if value == "1" else "OFF") + " requested")
        if not self._send_command(pp.make_command("pingpong", value=value)):
            messagebox.showerror(TITLE, "Could not send the request (no network).", parent=self.root)
            return
        self.pp_button.configure(text="Ping-pong lock: …")
        self._pp_sent = time.time()

    def _on_master_pc(self, now):
        """Is the master lamp on this PC? Lamps take commands only from it."""
        try:
            mine = {ip for ip, _ in pp.local_networks()} | {"127.0.0.1"}
        except Exception:
            return True
        masters = self._masters(now)
        return any(m.src in mine or set(m.hello.get("ips") or []) & mine for m in masters)

    def _update_all(self):
        now = time.time()
        with self.lock:
            live = [l for l in self.lamps.values() if now - l.hello_seen <= GONE_AFTER]
            target = self._target_version(live)
            old = sorted(
                (l.name(), l.hello.get("version", "?")) for l in live
                if version_key(l.hello.get("version", "")) < version_key(target)
            )
            manual = [n for n, v in old if version_key(v) < REMOTE_UPDATE_FROM]
            on_master = self._on_master_pc(now)
        if not old:
            # Nothing to do: just say which version everybody runs.
            messagebox.showinfo(
                TITLE + " - update all lamps",
                "All {} lamps run v{}{}.\nNothing to update.".format(
                    len(live), target or "?",
                    " – the newest release" if self.newest and target == self.newest else ""),
                parent=self.root)
            return
        lines = []
        if self.newest:
            lines.append("Newest release on GitHub: v{}.".format(self.newest))
        lines.append("Not on v{}: {}.".format(target, ", ".join("{} (v{})".format(n, v) for n, v in old)))
        if manual:
            lines.append("\nOlder than v1.60, update these by hand once: {}.".format(", ".join(manual)))
        if not on_master:
            lines.append("\n⚠ This PC is not the master PC – the lamps will ignore the request.")
        lines.append("\nAsk every lamp to install the newest release now?\n"
                     "Each lamp downloads it and restarts (a few seconds).")
        if not messagebox.askyesno(TITLE + " - update all lamps", "\n".join(lines), parent=self.root):
            return
        self._event("-", "update all lamps (sent)")
        sent = self._send_command(pp.make_command("update"))
        if not sent:
            messagebox.showerror(TITLE, "Could not send the request (no network).", parent=self.root)

    # ------------------------------------------------------------ statistics

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
            self._event(lamp.name(), "urgent call", "{} {}".format(
                info.get("urgent_band", "?"), info.get("urgent_mode", "")))

    # ------------------------------------------------------------ event log

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
                    station.replace(";", ","), what, str(detail).replace(";", ",")))
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
                writer.writerows(rows)
                act = self._activity_table()
                writer.writerow([])
                writer.writerow(act["columns"])
                writer.writerows(act["rows"])
                writer.writerow([])
                writer.writerow(["TX minutes per hour"] + act["hours"])
                for name, mins in act["heat"]:
                    writer.writerow([name] + [str(v).replace(".", ",") for v in mins])
        except OSError as exc:
            messagebox.showerror(TITLE, "Could not save:\n{}".format(exc), parent=self.root)

    def _reset_stats(self):
        if not messagebox.askyesno(
            TITLE, "Reset all statistics (function keys and activity) to zero?", parent=self.root
        ):
            return
        with self.lock:
            self.stats = {}
            self.stats_since = time.time()
        self._save_stats()

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

    def _serve_web(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/data.json"):
                    body = json.dumps(app.snapshot).encode("utf-8")
                    ctype = "application/json; charset=utf-8"
                elif self.path in ("/", "/index.html"):
                    body = PAGE.encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass  # quiet

        try:
            server = ThreadingHTTPServer(("0.0.0.0", self.web_port), Handler)
        except OSError as exc:
            self.web_error = "web page off - port {} busy: {}".format(self.web_port, exc)
            return
        server.daemon_threads = True
        server.timeout = 0.5
        while not self.stop.is_set():
            server.handle_request()
        server.server_close()

    def web_urls(self):
        ips = []
        try:
            ips = [ip for ip, _ in pp.local_networks()]
        except Exception:
            pass
        names = [socket.gethostname()] + ips
        return ["http://{}:{}".format(n, self.web_port) for n in names]

    def _check_firewall(self):
        """Look (in the background) for our inbound rule for the web page;
        when it is there, hide the firewall button and show the address in
        the status line."""
        if sys.platform != "win32" or not self.web_port:
            return

        def work():
            import subprocess

            try:
                done = subprocess.run(
                    ["netsh", "advfirewall", "firewall", "show", "rule",
                     "name={}".format(FIREWALL_RULE)],
                    capture_output=True, timeout=15,
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                )
                found = done.returncode == 0
            except Exception:
                found = None
            self.root.after(0, lambda: self._firewall_checked(found))

        threading.Thread(target=work, daemon=True).start()

    def _firewall_checked(self, found):
        self.fw_open = found
        if found:
            self.fw_button.pack_forget()

    def _allow_firewall(self):
        """Let other PCs / phones open the web page (asks for admin)."""
        if sys.platform != "win32":
            return
        if not messagebox.askyesno(
            TITLE,
            "Allow incoming TCP {} (the overview web page) on private and "
            "domain networks?\n\nWindows asks for administrator rights.".format(self.web_port),
            parent=self.root,
        ):
            return
        rule = (
            'netsh advfirewall firewall delete rule name="{0}" >nul 2>&1 & '
            'netsh advfirewall firewall add rule name="{0}" dir=in action=allow '
            "protocol=TCP localport={1} profile=private,domain"
        ).format(FIREWALL_RULE, self.web_port)
        try:
            import ctypes

            result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", "/c " + rule, None, 0)
        except Exception:
            result = 0
        if result > 32:
            messagebox.showinfo(TITLE, "Firewall rule added (TCP {}).".format(self.web_port), parent=self.root)
            self.root.after(2000, self._check_firewall)  # hides the button
        else:
            messagebox.showwarning(TITLE, "The rule was not added (no administrator rights given).",
                                   parent=self.root)

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
        # The status text wraps in whatever room the buttons leave.
        line1.bind("<Configure>", lambda e: self.head.configure(
            wraplength=max(200, e.width - row1.winfo_reqwidth() - 12)))
        row2 = tk.Frame(top, pady=2)
        row2.pack(fill=tk.X)
        pad = {"side": tk.LEFT, "padx": (0, 6)}
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
        # Shown only when a newer Overview is on GitHub (exe only).
        self.update_button = tk.Button(row2, text="Update Overview…", command=self._update_self,
                                       bg="#ffe08a")
        self._update_slot = tk.Frame(row2)  # keeps the yellow button's place in the row
        self._update_slot.pack(side=tk.LEFT)
        # Hidden as soon as the firewall rule for the web page is found.
        self.fw_button = tk.Button(row2, text="Allow web page in firewall…",
                                   command=self._allow_firewall)
        if self.web_port and sys.platform == "win32":
            self.fw_button.pack(**pad)
        self.var_top = tk.BooleanVar(value=self.cfg.get("window", "on_top", fallback="no") == "yes")
        tk.Checkbutton(row2, text="Always on top", variable=self.var_top,
                       command=self._on_top).pack(side=tk.LEFT)
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
        tk.Button(abar, text="Open event log", command=self._open_events).pack(side=tk.RIGHT)
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
            calls.append("URGENT ({} {})".format(h.get("urgent_band", ""), h.get("urgent_mode", "")))
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
            for node_id in [
                k for k, l in self.lamps.items()
                if now - max(l.hello_seen, l.radio_seen) > FORGET_AFTER
            ]:
                del self.lamps[node_id]
            lamps = list(self.lamps.values())
            masters = self._masters(now)
            live_all = [l for l in lamps if now - l.hello_seen <= GONE_AFTER]
            target = self._target_version(live_all)
            rows = [self._row(lamp, now, masters, target) for lamp in lamps]
            waiting = [l for l in lamps if l.stats_key() not in self.operating]
            slots = dict(self.slots)
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
        no_relay = [l.name() for l in live if now - l.radio_seen > 30]
        if no_relay:
            notes.append("no N1MM data from: {} (N1MM not running, or 'Share my N1MM' off)".format(
                ", ".join(sorted(no_relay))))
        notes.append("★ = master")
        for r in rows:
            r.pop("sort")
        stat_cols, stat_rows = self._stats_table()
        return {
            "activity": self._activity_table(),
            "stats": {
                "columns": stat_cols, "rows": stat_rows,
                "since": time.strftime("%d.%m. %H:%M", time.localtime(self.stats_since)),
            },
            "title": "{} v{}".format(TITLE, pp.__version__),
            "head": head, "warn": warn, "ready": ready, "rows": rows, "notes": notes,
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
        self.stats_label.configure(
            text="Transmissions started by each function key, since {}".format(snap["stats"]["since"]))
        self.stree.delete(*self.stree.get_children())
        for row in snap["stats"]["rows"]:
            self.stree.insert("", tk.END, values=row, tags=("all",) if row[0] == "All" else ())
        act = snap["activity"]
        self.atree.delete(*self.atree.get_children())
        for row in act["rows"]:
            self.atree.insert("", tk.END, values=row, tags=("all",) if row[0] == "All" else ())
        self._draw_heat(act)
        if time.time() - self._stats_saved > STATS_SAVE:
            self._save_stats()
        notes = list(snap["notes"])
        if self.web_port and not self.web_error:
            notes.append("web page: " + "  or  ".join(self.web_urls()[:3]))
        self.foot.configure(text="   ·   ".join(notes))


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PingPong Overview</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --text:#1d2330; --muted:#6b7280; --line:#e3e6eb; --warn:#c00000; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14171c; --card:#1c2027; --text:#e8eaee; --muted:#9aa1ad; --line:#2c323c; --warn:#ff6b6b; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:14px/1.4 "Segoe UI", system-ui, -apple-system, sans-serif; }
  header { padding:12px 16px 8px; }
  h1 { font-size:18px; margin:0 0 4px; }
  #head { font-weight:600; }
  #head.warn { color:var(--warn); }
  #head.ok { color:#1f8a3b; }
  #status { color:var(--muted); font-size:12px; }
  #status.lost { color:var(--warn); font-weight:600; }
  .wrap { overflow-x:auto; margin:0 16px; background:var(--card);
          border:1px solid var(--line); border-radius:8px; }
  table { border-collapse:collapse; width:100%; min-width:1000px; }
  th, td { padding:6px 8px; text-align:left; white-space:nowrap; border-bottom:1px solid var(--line); }
  th { font-size:12px; color:var(--muted); font-weight:600; position:sticky; top:0; background:var(--card); }
  td.c { text-align:center; } td.r { text-align:right; }
  th:first-child, td:first-child { position:sticky; left:0; background:inherit; font-weight:600; }
  th:first-child { z-index:1; } tr { background:var(--card); }
  tr.urgent td { animation: flash .3s steps(1) infinite; }
  @keyframes flash { 50% { background:#ffffff; color:#ff3030; } }
  #notes { padding:10px 16px 16px; color:var(--muted); font-size:13px; }
  #notes div.w { color:var(--warn); }
  .empty { padding:16px; color:var(--muted); }
  h2 { font-size:16px; margin:8px 16px 8px; }
  #since { font-weight:400; color:var(--muted); font-size:13px; }
  table.stats { min-width:760px; }
  table.stats td, table.stats th { text-align:center; }
  table.stats td:first-child, table.stats th:first-child { text-align:left; }
  table.stats tr.all td { font-weight:700; }
  table.heat td { min-width:44px; }
  .foot { color:var(--muted); font-size:12px; margin:8px 16px 24px; }
</style>
</head>
<body>
<header>
  <h1 id="title">PingPong Overview</h1>
  <div id="head">connecting…</div>
  <div id="status"></div>
</header>
<div class="wrap"><table><thead><tr id="cols"></tr></thead><tbody id="rows"></tbody></table>
<div class="empty" id="empty" hidden>No PingPong lamps heard yet.</div></div>
<div id="notes"></div>
<h2>Function keys <span id="since"></span></h2>
<div class="wrap"><table class="stats"><thead><tr id="scols"></tr></thead><tbody id="srows"></tbody></table></div>
<p class="foot">Transmissions started by each function key, per station. Reset and CSV export in the Overview window.</p>
<h2>Activity</h2>
<div class="wrap"><table class="stats"><thead><tr id="acols"></tr></thead><tbody id="arows"></tbody></table></div>
<h2>TX minutes per hour</h2>
<div class="wrap"><table class="stats heat"><thead><tr id="hcols"></tr></thead><tbody id="hrows"></tbody></table></div>
<p class="foot">Ping-pong waiting, F12 and urgent calls, and transmit time per station.</p>
<script>
const CENTER = new Set(["band","mode","run","role","ver"]), RIGHT = new Set(["freq","seen"]);
let cols = [];
function cell(id, text) {
  const td = document.createElement("td");
  td.textContent = text;
  if (CENTER.has(id)) td.className = "c"; else if (RIGHT.has(id)) td.className = "r";
  return td;
}
function render(d) {
  document.getElementById("title").textContent = d.title;
  document.title = d.title;
  const head = document.getElementById("head");
  head.textContent = d.head;
  head.className = (d.warn || d.ready === "bad") ? "warn" : (d.ready === "ok" ? "ok" : "");
  if (cols.length !== d.columns.length) {
    cols = d.columns;
    const tr = document.getElementById("cols"); tr.innerHTML = "";
    for (const [, name] of cols) { const th = document.createElement("th"); th.textContent = name; tr.appendChild(th); }
  }
  const body = document.getElementById("rows"); body.innerHTML = "";
  for (const r of d.rows) {
    const tr = document.createElement("tr");
    if (r.tag) tr.className = r.tag;
    if (r.bg) tr.style.background = r.bg;
    if (r.fg) tr.style.color = r.fg;
    for (const [id] of cols) tr.appendChild(cell(id, r.values[id] || ""));
    body.appendChild(tr);
  }
  document.getElementById("empty").hidden = d.rows.length > 0;
  const notes = document.getElementById("notes"); notes.innerHTML = "";
  for (const n of d.notes) {
    const div = document.createElement("div"); div.textContent = n;
    if (n.startsWith("\\u26a0")) div.className = "w";
    notes.appendChild(div);
  }
  if (d.stats) {
    document.getElementById("since").textContent = "since " + d.stats.since;
    const sc = document.getElementById("scols"); sc.innerHTML = "";
    for (const name of d.stats.columns) { const th = document.createElement("th"); th.textContent = name; sc.appendChild(th); }
    const sb = document.getElementById("srows"); sb.innerHTML = "";
    for (const row of d.stats.rows) {
      const tr = document.createElement("tr"); if (row[0] === "All") tr.className = "all";
      for (const v of row) { const td = document.createElement("td"); td.textContent = v; tr.appendChild(td); }
      sb.appendChild(tr);
    }
  }
  if (d.activity) {
    const a = d.activity;
    const ac = document.getElementById("acols"); ac.innerHTML = "";
    for (const name of a.columns) { const th = document.createElement("th"); th.textContent = name; ac.appendChild(th); }
    const ab = document.getElementById("arows"); ab.innerHTML = "";
    for (const row of a.rows) {
      const tr = document.createElement("tr"); if (row[0] === "All") tr.className = "all";
      for (const v of row) { const td = document.createElement("td"); td.textContent = v; tr.appendChild(td); }
      ab.appendChild(tr);
    }
    const hc = document.getElementById("hcols"); hc.innerHTML = "<th>Station</th>";
    for (const h of a.hours) { const th = document.createElement("th"); th.textContent = h; hc.appendChild(th); }
    const hb = document.getElementById("hrows"); hb.innerHTML = "";
    for (const [name, mins] of a.heat) {
      const tr = document.createElement("tr");
      const n = document.createElement("td"); n.textContent = name; tr.appendChild(n);
      for (const v of mins) {
        const td = document.createElement("td");
        const share = Math.min(v / 30, 1);
        if (v) { td.textContent = v + "'"; td.style.background = "rgba(31,111,235," + (0.15 + 0.75 * share) + ")";
                 if (share > 0.5) td.style.color = "#fff"; }
        tr.appendChild(td);
      }
      hb.appendChild(tr);
    }
  }
  const st = document.getElementById("status");
  st.textContent = "updated " + d.time; st.className = "";
}
async function tick() {
  try {
    const res = await fetch("data.json", {cache: "no-store"});
    render(await res.json());
  } catch (e) {
    const st = document.getElementById("status");
    st.textContent = "connection to the overview program lost - retrying…"; st.className = "lost";
  }
  setTimeout(tick, 1000);
}
tick();
</script>
</body>
</html>
"""


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
