"""PingPong Overview - shared constants, helpers and the Lamp record.

Used by pingpong_overview.py and its mixin modules.

(Original module description:)
PingPong Overview - one live table of all PingPong stations.

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

import json
import os
import re
import sys
import time

import n1mm_lamps as pp


TITLE = "PingPong Overview"
SETTINGS_FILE = "pingpong_overview.ini"
WEB_PORT = 8046
GONE_AFTER = pp.PEER_TIMEOUT  # s: lamp shown grey
FORGET_AFTER = 120.0  # s: lamp removed from the table
SILENT_AFTER = 30.0  # s: an operating station not heard this long raises the alarm
FORGET_SILENT = 3600.0  # s: an operating station that went silent stays this long
ALARM_BEEP = 5.0  # s: alarm beep interval
PP_LIMIT = pp.PP_LIMIT  # s: ping-pong slot frees itself
REFRESH_MS = 250
TABLES_EVERY = 1.0  # s: function key / activity tables are rebuilt at most this often
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
    "silent": ("#c00000", "#ffffff"),  # operating station not heard (alarm)
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


def next_contest_start(now):
    """Next Saturday 01:00 local time (CQ WW starts 00:00 UTC = 01:00 CET).
    On a Saturday before 01:00 that is the same day."""
    t = time.localtime(now)
    days = (5 - t.tm_wday) % 7  # Monday = 0 ... Saturday = 5
    start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + days, 1, 0, 0, 0, 0, -1))
    if start <= now:
        start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + days + 7, 1, 0, 0, 0, 0, -1))
    return start


def parse_when(text):
    """'2026-11-28 01:00' or '28.11.2026 01:00' (local time) -> epoch, else None."""
    text = " ".join((text or "").split())
    for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%d.%m.%Y %H.%M", "%Y-%m-%d %H.%M"):
        try:
            return time.mktime(time.strptime(text, fmt))
        except ValueError:
            pass
    return None


def fmt_when(epoch):
    """'Sat 28.11. 01:00'."""
    return time.strftime("%a %d.%m. %H:%M", time.localtime(epoch))


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
