"""Read the local N1MM contest selected by AppInfo and derive lamp status."""
import calendar
import hashlib
import json
import os
import socket
import sqlite3
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from itertools import groupby
from pathlib import Path

from pingpong_contest import FLASH_SECONDS, MAGIC

BAND_EDGES = ((1.8, 2.0, "160"), (3.5, 4.0, "80"), (7.0, 7.3, "40"),
              (14.0, 14.35, "20"), (21.0, 21.45, "15"), (28.0, 29.7, "10"))
METRES = {low: band for low, _, band in BAND_EDGES}


def band_metres(mhz):
    return next((label for low, high, label in BAND_EDGES if low <= mhz <= high), None)


def local_input(src, interfaces):
    if src == "127.0.0.1":
        return True
    if src not in interfaces:
        return False
    # The interface list can lag a DHCP/VPN change. Binding to the address
    # checks that the OS still owns it before it can select or refresh a log.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind((src, 0))
        return True
    except OSError:
        return False


def selection(data):
    if b"<appinfo" not in data.lower():
        return None
    try:
        root = ET.fromstring(data[data.index(b"<"):])
    except (ET.ParseError, ValueError):
        return None
    if root.tag.lower() != "appinfo":
        return None
    fields = {el.tag.lower(): (el.text or "").strip() for el in root}
    return fields.get("dbname", ""), fields.get("contestnr", ""), fields.get("contestname", "").upper()


def database_paths(name, folder):
    path = Path(name)
    if not name or path.suffix.lower() != ".s3db" or str(path).startswith(("//", "\\\\")):
        raise ValueError("invalid local N1MM database")
    if path.is_absolute():
        return [path] if path.is_file() else []
    if folder:
        path = Path(folder) / path.name
        return [path] if path.is_file() else []
    home = Path.home()
    dirs = []
    for base in (home, Path(os.environ.get("OneDrive") or home / "OneDrive")):
        for docs in ("Documents", "Dokumenti"):
            dirs.append(base / docs / "N1MM Logger+" / "Databases")
    return sorted({(d / path.name).resolve() for d in dirs if (d / path.name).is_file()})


def unknown(reason=""):
    return {"category": "unknown", "signals": {}, "last": {}, "reason": reason}


def contest_name(name):
    return str(name or "").upper().replace("-", "").replace(" ", "").replace("CQWWDX", "CQWW")


def cqww(name):
    return contest_name(name) in ("CQWWCW", "CQWWSSB")


def open_db(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    return db


def utc(value):
    try:
        dt = datetime.fromisoformat(str(value))
        return calendar.timegm(dt.utctimetuple())
    except (OverflowError, ValueError):
        return None


def contact_groups(contacts):
    # At N1MM's one-second resolution, repeated QSOs on one band have a
    # definite band. Two different bands in one second have no known order.
    for ts, group in groupby(contacts, key=lambda item: item[0]):
        group = list(group)
        bands = {band for _, band, _, _ in group}
        ids = frozenset(qid for _, _, qid, _ in group)
        flags = {flag for _, _, _, flag in group}
        qualifies = True in flags
        if not qualifies and None in flags:
            qualifies = None
        yield ts, bands, ids, qualifies


def multi_two_row(contacts, now, name, violations):
    hour = int(now) // 3600
    count_hour = None
    previous = set()
    used = 0
    ambiguous = False
    for ts, bands, ids, _ in contact_groups(contacts):
        if ts // 3600 != count_hour:
            used, ambiguous = 0, False
            count_hour = ts // 3600
        # Retain previous-hour events through the ten-second rollover window.
        if count_hour >= hour - 1:
            if len(bands) > 1 or (len(previous) > 1 and previous & bands):
                ambiguous = True
            if previous and previous.isdisjoint(bands):
                used += 1
                if used > 8 and not ambiguous and len(previous) == len(bands) == 1:
                    frm, to = next(iter(previous)), next(iter(bands))
                    violations[name, to, ids] = (ts, f"{name} · LIMIT EXCEEDED · {used}/8 · {frm} → {to} m")
        previous = bands
    if not previous:
        return None
    if count_hour != hour:
        used, ambiguous = 0, False
    if ambiguous or len(previous) != 1:
        return {"reason": "qso_order"}
    return {"band": next(iter(previous)), "used": used}


def multi_single_row(contacts, now, name, violations):
    held = None
    until = 0
    previous = set()
    uncertain_since = None
    for ts, bands, ids, qualifies in contact_groups(contacts):
        minute = ts - ts % 60
        if qualifies is None:
            return {"reason": "multiplier_data"}
        if len(bands) > 1 or uncertain_since is not None:
            # A qualifying change after all possible holds expired restores
            # a definite deadline even after conflicting simultaneous QSOs.
            if (len(bands) == 1 and qualifies and bands.isdisjoint(previous)
                    and uncertain_since is not None and minute >= uncertain_since + 600):
                held, until, uncertain_since = next(iter(bands)), minute + 600, None
            else:
                uncertain_since = minute
        else:
            band = next(iter(bands))
            if held != band and minute < until:
                if bands != previous:
                    frm = next(iter(previous))
                    at = time.strftime("%H:%M", time.gmtime(ts))
                    allowed = time.strftime("%H:%M", time.gmtime(until))
                    text = (f"{name} · EARLY BAND CHANGE · {frm} → {band} m at {at}"
                            f" · allowed {allowed} UTC")
                    violations[name, band, ids] = (ts, text)
            elif held != band and qualifies:
                held, until = band, minute + 600
        previous = bands
    if uncertain_since is not None:
        cutoff = uncertain_since + 600
        if len(previous) == 1 and now >= cutoff and name == "RUN":
            return {"band": next(iter(previous)), "until": cutoff}
        row = {"reason": "qso_order"}
        if now < cutoff:
            row["_retry_at"] = cutoff
        return row
    return {"band": held, "until": until} if held else None


def snapshot(db, nr, announced, now, violations):
    info = db.execute("SELECT * FROM ContestInstance WHERE ContestNR=?", (nr,)).fetchone()
    if info is None or contest_name(info["ContestName"]) != contest_name(announced):
        return unknown("contest_mismatch")
    if not cqww(info["ContestName"]):
        return None
    op = (info["OperatorCategory"] or "").upper()
    tx = (info["TransmitterCategory"] or "").upper()
    if op in ("SINGLE-OP", "SINGLE-OP-ASSISTED", "CHECKLOG"):
        category = "activity"
    elif op == "MULTI-OP" and (tx == "UNLIMITED" or (info["StationCategory"] or "").upper() == "DISTRIBUTED"):
        category = "activity"
    elif op in ("MULTI-OP", "MULTI-ONE") and tx == "ONE":
        category = "MS"
    elif op == "MULTI-OP" and tx == "TWO":
        category = "M2"
    else:
        return unknown()
    names = {"MS": ("RUN", "MULT"), "M2": ("TX1", "TX2")}.get(category, ())
    signals = {name: [] for name in names}
    last = {}
    skipped = kept = 0
    start = utc(info["StartDate"])
    hour = int(now) // 3600
    fields = "TS,ID,Band,Freq,Run1Run2,NetBiosName"
    if category == "MS":
        columns = {col[1].lower() for col in db.execute("PRAGMA table_info(DXLOG)")}
        for flag in ("IsMultiplier1", "IsMultiplier2"):
            fields += "," + (flag if flag.lower() in columns else "NULL AS " + flag)
    rows = db.execute(f"SELECT {fields} FROM DXLOG WHERE ContestNR=? ORDER BY TS", (nr,))
    for row in rows:
        ts = utc(row["TS"])
        if start and start > 0 and ts is not None and not start <= ts < start + 48 * 3600:
            skipped += 1
            continue
        if ts is None or not str(row["ID"] or "").strip():
            return unknown("incomplete_log")
        kept += 1
        if ts > now + 2:
            state = unknown("future_qso")
            state["_retry_at"] = ts - 2
            return state
        if category == "M2" and ts // 3600 > hour:
            continue
        station = (row["NetBiosName"] or "").strip().upper()
        if station:
            last[station] = max(ts, last.get(station, 0))
        if names:
            try:
                band = METRES.get(float(row["Band"] or 0))
                if not band:
                    freq = float(row["Freq"] or 0) / 1000
                    band = band_metres(freq)
            except (TypeError, ValueError):
                band = None
            if not band:
                return unknown("invalid_band")
            role = str(row["Run1Run2"])
            if role not in ("1", "2"):
                return unknown()
            qualifies = True
            if category == "MS" and role == "2":
                flags = (row["IsMultiplier1"], row["IsMultiplier2"])
                if all(value in (0, 1) for value in flags) or 1 in flags:
                    qualifies = 1 in flags
                else:
                    qualifies = None
            signals[names[int(role) - 1]].append((ts, band, str(row["ID"]), qualifies))
    if skipped and not kept:
        return unknown("contest_dates")
    state = {"category": category, "last": last, "signals": {}}
    for name, items in signals.items():
        derive = multi_two_row if category == "M2" else multi_single_row
        row = derive(items, now, name, violations)
        if row == {"reason": "multiplier_data"}:
            state["missing_multiplier_flags"] = True
            row = {"reason": "qso_order"}
        if row and "_retry_at" in row:
            state["_retry_at"] = min(state.get("_retry_at", float("inf")), row.pop("_retry_at"))
        state["signals"][name] = row
    if category == "M2":
        state["hour"] = hour
    return state


class WarningWindow:
    """Start a monotonic ten-second window once per confirmed band transition."""

    def __init__(self):
        self.known = None
        self.violations = {}
        self.active = {}

    def update(self, violations, now, baseline=False):
        mono = time.monotonic()
        known = {}
        active = {}
        for (name, band, ids), (logged, text) in violations.items():
            keys = [(name, band, qid) for qid in ids]
            prior = [self.known[key] for key in keys if self.known and key in self.known]
            if prior:
                end = max(prior)
            elif baseline:
                end = mono
            elif self.known is None:
                end = mono + FLASH_SECONDS - max(0, now - logged)
            else:
                end = mono + FLASH_SECONDS
            for key in keys:
                known[key] = end
            if end > mono:
                # Pick a representative ID for failover without publishing log IDs.
                event = self.event_id(min(keys))
                active[event] = (name, logged, text, end)
        self.known, self.active = known, active
        self.violations = violations

    @staticmethod
    def event_id(key):
        return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:32]

    def inherit(self, flashes):
        """Continue a prior master's live window only for a locally confirmed event."""
        if not flashes or not self.known:
            return
        mono, now = time.monotonic(), time.time()
        for (name, band, ids), (logged, text) in self.violations.items():
            keys = [(name, band, qid) for qid in ids]
            event = self.event_id(min(keys))
            # Replicas may have learned coincident QSOs in a different order.
            matches = [flashes[token] for key in keys if (token := self.event_id(key)) in flashes]
            if matches:
                left = max(0, min(FLASH_SECONDS, max(f["expires"] for f in matches) / 1000 - now))
                end = mono + left
                for key in keys:
                    self.known[key] = end
                self.active[event] = (name, logged, text, end)

    def packet(self):
        mono, wall = time.monotonic(), time.time()
        signals = {}
        for event, (name, ts, text, end) in self.active.items():
            if end > mono:
                signals[name] = max(signals.get(name, (0, 0, "", "")), (end, ts, text, event))
        return [{"text": text, "ms": max(1, int((end - mono) * 1000)),
                 "window": int(end * 1000), "event": event,
                 "expires": round((wall + end - mono) * 1000)}
                for end, _, text, event in signals.values()]


class LogReader:
    def __init__(self):
        self.db = self.key = self.revision = self.state = None
        self.warnings = WarningWindow()
        self.category = None
        self.last_read = 0
        self.retry_at = float("inf")

    def close(self):
        if self.db is not None:
            self.db.close()
        self.db = self.key = self.revision = self.state = None
        self.warnings = WarningWindow()
        self.category = None

    def read(self, selected, folder, now):
        name, nr, contest = selected
        if contest and not cqww(contest):
            self.close()
            return None
        same_log = False
        try:
            paths = database_paths(name, folder)
            if not paths or not nr.isdecimal():
                self.close()
                return unknown()
            if len(paths) > 1:
                matches = []
                for candidate in paths:
                    probe = open_db(candidate)
                    try:
                        row = probe.execute("SELECT ContestName FROM ContestInstance WHERE ContestNR=?", (int(nr),)).fetchone()
                        if row and contest_name(row[0]) == contest_name(contest):
                            matches.append(candidate)
                    finally:
                        probe.close()
                if len(matches) != 1:
                    self.close()
                    return unknown("duplicate_database" if matches else "contest_mismatch")
                path = matches[0]
            else:
                path = paths[0]
            key = path, path.stat().st_ino, nr, contest_name(contest)
            if key != self.key:
                self.close()
                self.db = open_db(path)
                self.key = key
            same_log = True
            revision = self.db.execute("PRAGMA data_version").fetchone()[0], int(now) // 3600
            if revision != self.revision or now < self.last_read or now >= self.retry_at:
                self.db.execute("BEGIN")
                try:
                    violations = {}
                    self.state = snapshot(self.db, int(nr), contest, now, violations)
                    if self.state and self.state["category"] in ("MS", "M2"):
                        changed = self.category is not None and self.category != self.state["category"]
                        if changed:
                            self.warnings = WarningWindow()
                        self.warnings.update(violations, now, baseline=changed)
                    if self.state and self.state["category"] != "unknown":
                        self.category = self.state["category"]
                    self.retry_at = self.state.pop("_retry_at", float("inf")) if self.state else float("inf")
                finally:
                    self.db.rollback()
                self.revision = revision
            self.last_read = now
            return self.state
        except sqlite3.OperationalError as exc:
            if same_log and (getattr(exc, "sqlite_errorcode", 0) & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                if self.state and self.warnings.packet() and now - self.last_read <= FLASH_SECONDS:
                    return self.state
                return unknown()
            self.close()
            return unknown()
        except (sqlite3.Error, OSError, ValueError, OverflowError, IndexError):
            self.close()
            return unknown()


def publish_loop(app):
    reader = LogReader()
    instance = uuid.uuid4().hex
    seq = 0
    was_master = False
    inherited = {}
    try:
        while not app.stop.is_set():
            selected = app._contest_selection
            if app._i_am_master and selected is not None:
                if not was_master:
                    with app.lock:
                        previous = getattr(app, "_contest_msg", None) or {}
                    inherited = {f["event"]: f for f in previous.get("flash", []) if "event" in f}
                    was_master = True
                seen = app._contest_radio_seen
                if cqww(selected[2]) and (seen is None or time.monotonic() - seen > 30):
                    reader.close()
                    state = unknown("radio_offline")
                else:
                    state = reader.read(selected, app._contest_folder, time.time())
                seq += 1
                msg = dict(v=1, instance=instance, seq=seq, sent=int(time.time()), ttl=10, state=state)
                if state and state.get("category") in ("MS", "M2"):
                    if inherited:
                        reader.warnings.inherit(inherited)
                        inherited = {}
                    msg["flash"] = reader.warnings.packet()
                packet = MAGIC + json.dumps(msg, separators=(",", ":")).encode()
                with app.lock:
                    if app._i_am_master and app._contest_selection == selected:
                        app._publish_contest(packet)
            else:
                was_master = False
                reader.close()
            app.stop.wait(2)
    finally:
        reader.close()
