"""Read the master's CQ WW operating status and format it for a lamp."""
import json
import math
import time

MAGIC = b"PPS1"
FLASH_SECONDS = 10
BANDS = ("160", "80", "40", "20", "15", "10")


def integer(value):
    return type(value) is int and 0 <= value <= 2**53 - 1


def decode(data, now):
    try:
        msg = json.loads(data[len(MAGIC):].decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(msg, dict) or not integer(msg.get("v")) or msg["v"] != 1:
        return None
    if not all(integer(msg.get(k)) for k in ("seq", "sent", "ttl")):
        return None
    if not 1 <= msg["ttl"] <= 300 or not -2 <= now - msg["sent"] <= msg["ttl"]:
        return None
    if not isinstance(msg.get("instance"), str) or not 1 <= len(msg["instance"]) <= 64:
        return None
    if "state" not in msg:
        return None
    state = msg["state"]
    if state is None:
        return msg if msg.get("flash", []) == [] else None
    if not isinstance(state, dict) or state.get("category") not in ("MS", "M2", "activity", "unknown"):
        return None
    last = state.get("last")
    if not isinstance(last, dict) or not all(integer(t) for t in last.values()):
        return None
    category = state["category"]
    names = {"MS": ("RUN", "MULT"), "M2": ("TX1", "TX2")}.get(category, ())
    rows = state.get("signals")
    if not isinstance(rows, dict) or set(rows) != set(names):
        return None
    if category == "M2" and not integer(state.get("hour")):
        return None
    flashes = msg.get("flash", [])
    if not isinstance(flashes, list) or len(flashes) > len(names):
        return None
    for flash in flashes:
        if (not isinstance(flash, dict) or not integer(flash.get("ms"))
                or not 0 < flash["ms"] <= FLASH_SECONDS * 1000 or not integer(flash.get("window"))
                or not isinstance(flash.get("text"), str) or not 1 <= len(flash["text"]) <= 160
                or not flash["text"].isprintable()):
            return None
        expires = flash.get("expires", msg["sent"] * 1000 + flash["ms"])
        if not integer(expires) or expires > (msg["sent"] + FLASH_SECONDS + 1) * 1000:
            return None
        if "event" in flash:
            event = flash["event"]
            if not isinstance(event, str) or len(event) != 32 or any(c not in "0123456789abcdef" for c in event):
                return None
        # Subtract time spent in transit, including on the first packet seen.
        flash["expires"] = expires
        flash["ms"] = max(0, min(flash["ms"], expires - round(now * 1000)))
    for row in rows.values():
        if row == {"reason": "qso_order"}:
            continue
        if row is not None and (not isinstance(row, dict) or row.get("band") not in BANDS or
                                set(row) != {"band", "until" if category == "MS" else "used"} or
                                not integer(row.get("until" if category == "MS" else "used"))):
            return None
    msg["_received"] = time.monotonic()
    return msg


def clock(seconds):
    minutes, sec = divmod(seconds, 60)
    return "{}:{:02d}".format(minutes, sec)


def utc_minute(timestamp):
    return "{:02d}:{:02d} UTC".format(timestamp // 3600 % 24, timestamp // 60 % 60)


def warning_view(msg, now, stale, tuning):
    """Return the footer color and text for this lamp's current display tick."""
    if not msg or not msg["state"] or stale or not -2 <= now - msg["sent"] <= msg["ttl"]:
        return None
    elapsed = max(0, time.monotonic() - msg["_received"])
    active = [f for f in msg.get("flash", []) if f["ms"] > elapsed * 1000]
    if active:
        remaining = max(f["ms"] for f in active) / 1000 - elapsed
        phase = int(FLASH_SECONDS - remaining) % 2
        return ("dark-red" if phase else "red", "\n".join(f["text"] for f in active))
    master_now = msg["sent"] + elapsed
    state = msg["state"]
    lines = []
    if state["category"] == "M2" and int(master_now) // 3600 == state["hour"]:
        for name in ("TX1", "TX2"):
            row = state["signals"][name]
            if row and row.get("used") == 8:
                until = utc_minute((state["hour"] + 1) * 3600)
                lines.append(f"{name} · LIMIT REACHED · stay on {row['band']} m until {until}")
    elif state["category"] == "MS":
        for nr, name, band in tuning:
            row = state["signals"].get(name)
            if row and row.get("until", 0) > master_now and band != row["band"]:
                until = utc_minute(row["until"])
                lines.append(f"Radio {nr} · {name} · stay on {row['band']} m until {until}")
    return ("amber", "\n".join(lines)) if lines else None


def display(msg, now, station_names, show_last, stale=False):
    if msg is None or msg["state"] is None:
        return ""
    state = msg["state"]
    if state["category"] == "activity" and not show_last:
        return ""
    if stale or not -2 <= now - msg["sent"] <= msg["ttl"]:
        return "Timer data unavailable"
    # Transit delays can make this estimate late. A fast lamp clock must
    # never advance a timer past the time established by the master.
    master_now = msg["sent"] + max(0, time.monotonic() - msg["_received"])
    if state["category"] == "M2" and int(master_now) // 3600 != state["hour"]:
        return "Waiting for current-hour counts"
    lines = []
    if state["category"] == "unknown":
        warnings = {
            "radio_offline": "N1MM feed unavailable · check Radio on the master",
            "contest_mismatch": "Check log · database does not match Application Info",
            "duplicate_database": "Multiple matching logs · set database_folder on the master",
            "future_qso": "Check station clocks · future QSO in log",
        }
        if isinstance(state.get("reason"), str) and state["reason"] in warnings:
            return warnings[state["reason"]]
        if state.get("reason") == "contest_dates":
            return "Check contest dates · all log QSOs excluded"
        if state.get("reason") == "incomplete_log":
            return "Check log · QSO timestamp or ID missing"
        if state.get("reason") == "invalid_band":
            return "Check log · invalid CQ WW band"
        lines.append("Timer data unavailable")
    for name in ("RUN", "MULT", "TX1", "TX2"):
        if name not in state["signals"]:
            continue
        row = state["signals"][name]
        if row is None:
            lines.append("MULT · No new multiplier yet" if name == "MULT" else name + " · No QSO yet")
            continue
        if name == "MULT" and state.get("missing_multiplier_flags"):
            lines.append("MULT · Check log · multiplier flags unavailable")
            continue
        if row.get("reason") == "qso_order":
            lines.append(name + " · Check log · band order unclear")
            continue
        if state["category"] == "MS":
            left = math.ceil(row["until"] - master_now)
            status = clock(left) + " left" if left > 0 else "Timer clear"
        else:
            used = row["used"]
            status = "{}/8 changes".format(used)
            if used == 7:
                status += " · 1 change left"
        lines.append("{} · {} m · {}".format(name, row["band"], status))
    if show_last and state["category"] != "unknown":
        latest = max((state["last"].get(n.upper(), 0) for n in station_names), default=0)
        lines.append("Last QSO · " + (clock(max(0, int(master_now - latest))) + " ago" if latest else "None yet"))
    return "\n".join(lines)
