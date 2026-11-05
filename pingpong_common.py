"""PingPong - shared constants, N1MM packet parsing and station records.

Used by the lamp (n1mm_lamps.py), its network and rules parts and by
PingPong Overview. No windows and no sockets here.
"""

import os
import re
import sys
import time
import xml.etree.ElementTree as ET

from pingpong_version import __version__

# The lamp listens for its own N1MM on 12062 (since v1.76), so it never
# shares a port with other programs that use N1MM's usual 12060 (e.g.
# Callbooker). N1MM's Broadcast Address must include 127.0.0.1:12062.
DEFAULT_PORT = 12062

DEFAULT_STALE = 5.0

# Keep well away from N1MM's own ports (12060 external broadcast and
# multi-computer networking, 12070 multi-computer UDP): N1MM tries to
# parse anything arriving there and throws IndexOutOfRangeException.
DEFAULT_DISCOVERY_PORT = 46070

HELLO_INTERVAL = 3.0  # s: lamp-to-lamp hello (sent at once on F12 / name change)

PEER_TIMEOUT = 12.0  # s: a lamp not heard this long is gone

NETWORK_REFRESH = 30.0  # s: re-read this PC's network adapters

LOCAL_N1MM_RECENT = 10.0  # s: own N1MM counts as running if heard this recently

TEST_LOG = "pingpong_test.log"

ERROR_LOG = "pingpong_error.log"

ERROR_COUNT = [0]  # errors written since start (reported to the Overview)


def log_error(where):
    """Write the current exception to pingpong_error.log (next to the exe)
    instead of letting a network thread die silently."""
    import traceback

    ERROR_COUNT[0] += 1

    try:
        path = os.path.join(app_dir(), ERROR_LOG)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("{}  v{}  {}\n{}\n".format(
                time.strftime("%Y-%m-%d %H:%M:%S"), __version__, where,
                traceback.format_exc(),
            ))
    except Exception:
        pass

def guarded(where, func):
    """Call func(*args); on any error log it and keep the thread alive."""
    def call(*args):
        try:
            return func(*args)
        except Exception:
            log_error(where)
    return call


def locked(method):
    """Run a lamp method while holding self.lock.

    The N1MM listener, the discovery thread and the window all read and
    change the station and lamp lists; one lock (re-entrant) keeps them
    from seeing half-updated state. The keyboard hook never takes it:
    it reads plain flags only, so key presses are never delayed.
    """
    import functools

    @functools.wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


BLINK_HALF = 0.4  # s: on / off time when a call (F12) blinks the lamp

MAX_ALIAS = 12  # characters for "My name"

SIMILAR_COLOR = 60  # RGB distance below which two chosen colors count as "the same"

CALL_BORDER = 13  # px: width of the F12 call border around the lamp

URGENT_WAIT = 3.0  # s: urgent call ends if N1MM starts no transmission by then

URGENT_HALF = 0.15  # s: fast flash of an urgent call

STATION_FORGET = 60.0  # s: stations not heard this long leave the footer

# N1MM may also send its broadcasts to 127.0.0.1; such packets are
# always from this PC's own N1MM.
LOOPBACK = {"127.0.0.1"}

BLOCK_LIMIT = 30.0  # s: default time of an S&P station's F12 call (master can change)

BLOCK_LIMIT_RUN = 5.0  # s: default time of a Run station's F12 call (master can change)

BLOCK_RANGE = (1, 30)  # s: allowed F12 call / block times

def clamp_block(value, default=BLOCK_LIMIT):
    """F12 call/block time within BLOCK_RANGE (0 or missing = default)."""
    if not value:
        return default
    return float(min(max(value, BLOCK_RANGE[0]), BLOCK_RANGE[1]))

GO_TIME = 3.0  # s: green GO on an inband lamp after RUN's transmission ends
PP_LIMIT = 40.0  # s: default - ping-pong slot lock ends by itself if RUN stays silent
PP_RANGE = (0, 40)  # s: release time set on the master; 0 = only RUN frees the slot


def clamp_pp(value):
    """Ping-pong release time within PP_RANGE (unparseable = default)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return PP_LIMIT
    return float(min(max(value, PP_RANGE[0]), PP_RANGE[1]))


def pp_expired(slot_time, now, limit):
    """Has a ping-pong slot released itself? (limit 0 = never by time)"""
    return limit > 0 and now - slot_time > limit

NO_LAMP_AFTER = 30.0  # s after start before "no PingPong heard" notes

FIREWALL_RULE = "PingPong lamps UDP"

# Ping-pong role: always from N1MM (Run = RUN, S&P = INB); announced to
# the other lamps in the hello.
SENT_ROLES = ("RUN", "INB")

COLOR_IDLE = "#3a3a3a"
GO_COLOR = "#2ea043"  # green GO for inband stations
TEST_COLOR = "#8957e5"  # lamp test from the Overview
TEST_TIME = 3.0  # s
LAMP_SIZES = {"normal": 1.0, "large": 1.4, "xlarge": 1.8}  # lamp size option
NIGHT_LEVEL = 0.45  # night mode: lamp colors at 45 % brightness

COLOR_PALETTE = [
    "red",
    "orange",
    "light blue",
    "light green",
    "purple",
    "cyan",
    "gold",
    "pink",
    "brown",
    "lime green",
]

BANDS = [
    (1.8, 2.0, "1.8"),
    (3.5, 4.0, "3.5"),
    (7.0, 7.3, "7"),
    (14.0, 14.35, "14"),
    (21.0, 21.45, "21"),
    (28.0, 29.7, "28"),
]

MODES_ALLOWED = {"CW", "RTTY", "SSB"}

# N1MM reports phone as "SSB" (seen in RadioInfo) or USB/LSB depending on
# radio/setup; all of them are one group so stations always match.
MODE_ALIASES = {"USB": "SSB", "LSB": "SSB"}

def band_of(freq_str):
    try:
        mhz = float(freq_str) / 100000.0
    except (TypeError, ValueError):
        return None
    for lo, hi, name in BANDS:
        if lo <= mhz < hi:
            return name
    return None

def freq_khz(freq):
    """N1MM Freq (10 Hz units) as kHz text: '1402500' -> '14025.00 kHz'."""
    try:
        return "{:.2f} kHz".format(float(freq) / 100.0)
    except (TypeError, ValueError):
        return ""

def group_of(freq, mode):
    band = band_of(freq)
    mode = MODE_ALIASES.get(mode, mode)
    if not band or mode not in MODES_ALLOWED:
        return None
    return band, mode

def group_label(key):
    if not key:
        return ""
    return "{} {}".format(key[0], key[1])

def clean_caption(caption):
    caption = caption.strip()
    cleaned = re.sub(r"^\s*F\d+\s*", "", caption).strip()
    return cleaned or caption

def radio_running(data):
    """IsRunning from a RadioInfo packet: True = Run, False = S&P, None = n/a."""
    m = re.search(rb"<IsRunning>\s*(\w+)\s*</IsRunning>", data)
    if not m:
        return None
    return m.group(1).lower() == b"true"

def packet_info(data):
    raw = data.decode("utf-8", errors="replace")
    start = raw.find("<")
    if start < 0:
        return False, "", "", False, "", ""
    try:
        root = ET.fromstring(raw[start:])
    except ET.ParseError:
        return False, "", "", False, "", ""
    if root.tag != "RadioInfo":
        return False, "", "", False, "", ""
    caption = ""
    station = ""
    freq = ""
    mode = ""
    is_tx = False
    for el in root.iter():
        if el.tag == "FunctionKeyCaption":
            caption = clean_caption(el.text or "")
        elif el.tag == "StationName" and el.text and el.text.strip():
            station = el.text.strip()
        elif el.tag == "Freq" and el.text and el.text.strip():
            freq = el.text.strip()
        elif el.tag == "Mode" and el.text and el.text.strip():
            mode = el.text.strip().upper()
        elif el.tag == "IsTransmitting":
            is_tx = (el.text or "").strip().lower() == "true"
    return True, caption, station, is_tx, freq, mode

def parse_spec(spec):
    spec = spec.strip()
    if "," in spec:
        parts = [p.strip() for p in spec.split(",")]
    elif ":" in spec:
        parts = [p.strip() for p in spec.split(":")]
    else:
        parts = [spec]
    ip = parts[0]
    label = parts[1] if len(parts) > 1 else ""
    color = parts[2] if len(parts) > 2 else ""
    return ip, label, color

def read_config(config_path):
    hints = {}
    if config_path and os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                ip, label, color = parse_spec(line)
                hints[ip] = (label, color)
    return hints

def write_config(config_path, ip, label, color):
    if not config_path:
        return
    line = ",".join([ip, label or "", color])
    try:
        with open(config_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass

def n1mm_ini_path():
    """N1MM Logger+ settings file: Documents\\N1MM Logger+\\N1MM Logger.ini."""
    docs = ""
    if sys.platform == "win32":
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(1024)
            # CSIDL_PERSONAL = 5: the real Documents folder (also if moved).
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
                docs = buf.value
        except Exception:
            docs = ""
    if not docs:
        docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs, "N1MM Logger+", "N1MM Logger.ini")

def read_n1mm_master(path):
    """True/False from [NetworkStatus] IsNetworkMaster; None if no file."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            section = ""
            for line in fh:
                line = line.strip()
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1].strip().lower()
                elif section == "networkstatus" and "=" in line:
                    key, _, value = line.partition("=")
                    if key.strip().lower() == "isnetworkmaster":
                        return value.strip().lower() == "true"
    except OSError:
        return None
    return False

def _float(text):
    try:
        return float(text)
    except ValueError:
        return 0.0

class Peer:
    def __init__(self, node_id):
        self.id = node_id
        self.host = ""
        self.version = ""
        self.ips = set()
        self.local_n1mm = False
        self.alias = ""
        self.role = ""
        self.n1mm_master = None
        self.started = 0.0
        self.colors = {}
        self.block_time = 0.0
        self.block_time_run = 0.0
        self.pingpong = ""  # master only: "1" ping-pong lock on, "0" off
        self.pp_limit = ""  # master only: ping-pong release time in s ("0".."40")
        self.alert_run = False
        self.urgent = False
        self.urgent_band = ""
        self.urgent_mode = ""
        self.urgent_time = 0.0
        self.color = ""
        self.color_time = 0.0
        self.alert = False
        self.alert_band = ""
        self.alert_time = 0.0
        self.last_seen = 0.0

class Station:
    def __init__(self, ip, label, color):
        self.ip = ip
        self.label = label or ip
        self.color = color
        self.count = 0
        self.msg = ""
        self.msg_time = 0.0
        self.tx = False
        self.last_seen = 0.0
        self.freq = ""
        self.mode = ""
        self.key = None
        self.running = None  # True = Run, False = S&P, None = unknown
        self.direct_seen = 0.0  # last packet straight from N1MM (not relayed)

def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))
