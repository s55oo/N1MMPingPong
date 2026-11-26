"""Read the master's CQ WW operating status and format it for a lamp."""
import json
import math

MAGIC = b"PPS1"
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
        return msg
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
    for row in rows.values():
        if row is not None and (not isinstance(row, dict) or row.get("band") not in BANDS or
                                set(row) != {"band", "until" if category == "MS" else "used"} or
                                not integer(row.get("until" if category == "MS" else "used"))):
            return None
    return msg


def clock(seconds):
    minutes, sec = divmod(seconds, 60)
    return "{}:{:02d}".format(minutes, sec)


def display(msg, now, station_names, show_last, stale=False):
    if msg is None or msg["state"] is None:
        return ""
    state = msg["state"]
    if state["category"] == "activity" and not show_last:
        return ""
    if stale or not -2 <= now - msg["sent"] <= msg["ttl"]:
        return "Timer data unavailable"
    if state["category"] == "M2" and int(now) // 3600 != state["hour"]:
        return "Waiting for current-hour counts"
    lines = []
    if state["category"] == "unknown":
        if state.get("reason") == "contest_dates":
            return "Check contest dates · all log QSOs excluded"
        lines.append("Timer data unavailable")
    for name in ("RUN", "MULT", "TX1", "TX2"):
        if name not in state["signals"]:
            continue
        row = state["signals"][name]
        if row is None:
            lines.append(name + " · No QSO yet")
            continue
        if state["category"] == "MS":
            left = math.ceil(row["until"] - now)
            status = clock(left) + " left" if left > 0 else "Timer clear"
        else:
            used = row["used"]
            status = "{}/8 changes".format(used)
        lines.append("{} · {} m · {}".format(name, row["band"], status))
    if show_last and state["category"] != "unknown":
        latest = max((state["last"].get(n.upper(), 0) for n in station_names), default=0)
        lines.append("Last QSO · " + (clock(max(0, int(now - latest))) + " ago" if latest else "None yet"))
    return "\n".join(lines)
