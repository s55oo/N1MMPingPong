"""BAND VIOLATION alerts from N1MM_timer (interface v1).

N1MM_timer (github.com/s55oo/N1MM_timer) checks the band-change rules of the
contest (10-minute rule, hourly band changes, MULT on the RUN band) on the
master PC. It sends a small JSON status to the lamp on that PC
(UDP 127.0.0.1:46071, loopback only). That lamp passes it on to every lamp and
the Overview over PingPong's own signed network (UDP 46070), and every screen
shows it in a footer. PingPong only displays and relays: it never checks the
rules, and an alert never touches the ping-pong lock, F-key blocking, radios
or N1MM.

This module has no Tk and no sockets: validation and the alert state, so both
can be tested on their own.
"""

import calendar
import json
import time

BAND_PORT = 46071  # N1MM_timer -> the lamp on the same PC (loopback only)
BAND_MAGIC = b"PPB1"  # the alert, passed on between PingPong PCs
MAX_SIZE = 1400  # bytes of JSON
MAX_VIOLATIONS = 5
MAX_TEXT = 60
SKEW = 2.0  # s of clock difference allowed between the PCs
KINDS = ("TEN_MIN", "HOURLY", "MULT_SAME_BAND")
STATUSES = ("ok", "violation")
LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def parse_utc(text):
    """'2026-11-28T14:31:05Z' -> seconds since 1970 (UTC), or None."""
    if not isinstance(text, str):
        return None
    try:
        return float(calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, OverflowError):
        return None


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value, limit, empty=True):
    return isinstance(value, str) and len(value) <= limit and (empty or value.strip() != "")


def _check_violation(item):
    """None if the violation is fine, else what is wrong."""
    if not isinstance(item, dict):
        return "violation is not an object"
    if not _text(item.get("id"), 120, empty=False):
        return "violation id"
    if item.get("kind") not in KINDS:
        return "violation kind"
    if not _text(item.get("text"), MAX_TEXT, empty=False):
        return "violation text (1-{} characters)".format(MAX_TEXT)
    for key in ("signal", "station", "from_band", "to_band", "call"):
        if key in item and not _text(item[key], 40):
            return "violation " + key
    if "first_utc" in item and parse_utc(item["first_utc"]) is None:
        return "violation first_utc"
    if "qsos" in item and not (_is_int(item["qsos"]) and item["qsos"] >= 0):
        return "violation qsos"
    return None


def validate(data, now):
    """Check one alert datagram. Returns (message, None) or (None, reason).

    `data` is the raw UTF-8 JSON (bytes). `now` is this PC's time."""
    if not isinstance(data, (bytes, bytearray)):
        return None, "not bytes"
    if len(data) > MAX_SIZE:
        return None, "too big ({} bytes)".format(len(data))
    try:
        msg = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "not JSON"
    if not isinstance(msg, dict):
        return None, "not a JSON object"
    if msg.get("type") != "n1mm_timer.alert":
        return None, "type"
    if not (_is_int(msg.get("v")) and msg["v"] == 1):
        return None, "version"
    if not _text(msg.get("instance"), 64, empty=False):
        return None, "instance"
    if not (_is_int(msg.get("seq")) and msg["seq"] >= 0):
        return None, "seq"
    sent = parse_utc(msg.get("sent_utc"))
    if sent is None:
        return None, "sent_utc"
    ttl = msg.get("ttl_s")
    if not (isinstance(ttl, (int, float)) and not isinstance(ttl, bool) and 1 <= ttl <= 300):
        return None, "ttl_s"
    if msg.get("status") not in STATUSES:
        return None, "status"
    if "profile" in msg and not _text(msg["profile"], 40):
        return None, "profile"
    items = msg.get("violations", [])
    if not isinstance(items, list) or len(items) > MAX_VIOLATIONS:
        return None, "violations (a list of at most {})".format(MAX_VIOLATIONS)
    for item in items:
        problem = _check_violation(item)
        if problem:
            return None, problem
    if sent - now > SKEW:
        return None, "future timestamp"
    if now - sent > ttl + SKEW:
        return None, "too old ({:.0f} s)".format(now - sent)
    msg["violations"] = items
    return msg, None


class BandAlert:
    """The latest alert, as one PingPong screen sees it.

    offer() takes a validated message; footer() says what to show; new_ids()
    says which violations are new (for the sound)."""

    def __init__(self):
        self.reset()

    def reset(self):
        """Forget everything (e.g. the master changed): nothing is shown."""
        self.instance = None
        self.high = -1  # the highest seq accepted from this instance
        self.msg = None
        self.got_at = 0.0
        self.expires = 0.0
        self.retired = {}
        self.sounded = set()  # violation ids that already beeped (this instance)

    def offer(self, msg, now):
        """Take a validated message. False if it is a copy or out of order."""
        mono = time.monotonic()
        if msg["instance"] != self.instance:
            self.retired = {key: end for key, end in self.retired.items() if end > mono}
            if msg["instance"] in self.retired:
                return False
            if self.instance is not None:
                self.retired[self.instance] = mono + 300 + SKEW
            self.instance = msg["instance"]  # N1MM_timer started again
            self.high = -1
            self.sounded = set()
        if msg["seq"] <= self.high:
            return False
        self.high = msg["seq"]
        self.msg = msg
        self.got_at = now
        self.expires = mono + msg["ttl_s"]
        return True

    def online(self, now):
        return (self.msg is not None and time.monotonic() <= self.expires and
                -SKEW <= now - self.got_at <= self.msg["ttl_s"])

    def violations(self, now):
        if not self.online(now) or self.msg["status"] != "violation":
            return []
        return self.msg["violations"]

    def new_ids(self, now):
        """Violations not beeped for yet; each id beeps once per N1MM_timer run."""
        fresh = [v["id"] for v in self.violations(now) if v["id"] not in self.sounded]
        self.sounded.update(fresh)
        return fresh

    def footer(self, now, my_stations=()):
        """(kind, text): kind is "violation", "offline" or "" (show nothing).

        `my_stations`: this PC's own N1MM StationName(s); a violation logged by
        one of them adds YOUR STATION."""
        if self.msg is None:
            return "", ""
        if not self.online(now):
            return "offline", "Band watcher offline"
        items = self.violations(now)
        if not items:
            return "", ""
        text = "BAND VIOLATION: " + items[0]["text"]
        if len(items) > 1:
            text += " (+{} more)".format(len(items) - 1)
        mine = {str(s).strip().upper() for s in my_stations if s and str(s).strip()}
        if mine and any(str(v.get("station", "")).strip().upper() in mine for v in items):
            text += "  ·  YOUR STATION"
        return "violation", text
