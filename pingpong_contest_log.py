"""Read the local N1MM contest selected by AppInfo and derive lamp status."""
import calendar
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

from pingpong_contest import MAGIC

BAND_EDGES = ((1.8, 2.0, "160"), (3.5, 4.0, "80"), (7.0, 7.3, "40"),
              (14.0, 14.35, "20"), (21.0, 21.45, "15"), (28.0, 29.7, "10"))
METRES = {low: band for low, _, band in BAND_EDGES}


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


def signal_row(contacts, category, now):
    hour = int(now) // 3600
    previous = set()
    since = used = 0
    ambiguous = False
    restore = None
    uncertain_since = None
    for ts, group in groupby(contacts, key=lambda item: item[0]):
        bands = {band for _, band in group}
        if category == "M2":
            if ts // 3600 == hour:
                if len(bands) > 1 or (len(previous) > 1 and previous & bands):
                    ambiguous = True
                if previous and previous.isdisjoint(bands):
                    used += 1
        elif bands != previous or len(bands) > 1:
            minute = ts - ts % 60
            if len(bands) > 1 or uncertain_since is not None:
                # A change to a distinct band after every possible old hold
                # has expired establishes the same deadline under all orders.
                if (len(bands) == 1 and bands.isdisjoint(previous)
                        and uncertain_since is not None and minute >= uncertain_since + 600):
                    since, restore, uncertain_since = minute, None, None
                else:
                    uncertain_since = minute
                previous = bands
                continue
            if restore and bands == restore[0] and minute < restore[1] + 600:
                since, restore = restore[1], None
            else:
                restore = (previous, since) if previous and minute < since + 600 else None
                since = minute
        previous = bands
    if not previous:
        return None
    if ambiguous or len(previous) != 1:
        return {"reason": "qso_order"}
    band = next(iter(previous))
    if uncertain_since is not None:
        # The last band is known. Once even the latest possible hold is over,
        # all admissible orders agree that the timer is clear.
        until = uncertain_since + 600
        if now < until:
            return {"reason": "qso_order", "_retry_at": until}
        return {"band": band, "until": until}
    if category == "MS":
        return {"band": band, "until": since + 600}
    return {"band": band, "used": used}


def snapshot(db, nr, announced, now):
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
    rows = db.execute("SELECT TS,ID,Band,Freq,Run1Run2,NetBiosName FROM DXLOG WHERE ContestNR=? ORDER BY TS", (nr,))
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
                    band = next((label for low, high, label in BAND_EDGES if low <= freq <= high), None)
            except (TypeError, ValueError):
                band = None
            if not band:
                return unknown("invalid_band")
            role = str(row["Run1Run2"])
            if role not in ("1", "2"):
                return unknown()
            signals[names[int(role) - 1]].append((ts, band))
    if skipped and not kept:
        return unknown("contest_dates")
    state = {"category": category, "last": last, "signals": {}}
    for name, items in signals.items():
        row = signal_row(items, category, now)
        if row and "_retry_at" in row:
            state["_retry_at"] = min(state.get("_retry_at", float("inf")), row.pop("_retry_at"))
        state["signals"][name] = row
    if category == "M2":
        state["hour"] = hour
    return state


class LogReader:
    def __init__(self):
        self.db = self.key = self.revision = self.state = None
        self.last_read = 0
        self.retry_at = float("inf")

    def close(self):
        if self.db is not None:
            self.db.close()
        self.db = self.key = self.revision = None

    def read(self, selected, folder, now):
        name, nr, contest = selected
        if contest and not cqww(contest):
            self.close()
            return None
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
            revision = self.db.execute("PRAGMA data_version").fetchone()[0], int(now) // 3600
            if revision != self.revision or now < self.last_read or now >= self.retry_at:
                self.db.execute("BEGIN")
                try:
                    self.state = snapshot(self.db, int(nr), contest, now)
                    self.retry_at = self.state.pop("_retry_at", float("inf")) if self.state else float("inf")
                finally:
                    self.db.rollback()
                self.revision = revision
            self.last_read = now
            return self.state
        except (sqlite3.Error, OSError, ValueError, OverflowError, IndexError):
            self.close()
            return unknown()


def publish_loop(app):
    reader = LogReader()
    instance = uuid.uuid4().hex
    seq = 0
    try:
        while not app.stop.is_set():
            selected = app._contest_selection
            if app._i_am_master and selected is not None:
                seen = app._contest_radio_seen
                if cqww(selected[2]) and (seen is None or time.monotonic() - seen > 30):
                    state = unknown("radio_offline")
                else:
                    state = reader.read(selected, app._contest_folder, time.time())
                seq += 1
                msg = dict(v=1, instance=instance, seq=seq, sent=int(time.time()), ttl=10, state=state)
                packet = MAGIC + json.dumps(msg, separators=(",", ":")).encode()
                with app.lock:
                    if app._i_am_master and app._contest_selection == selected:
                        app._publish_contest(packet)
            else:
                reader.close()
            app.stop.wait(2)
    finally:
        reader.close()
