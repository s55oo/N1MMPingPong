"""PingPong Overview - one table of all PingPong stations on the network.

A separate, read-only program for the captain's / station manager's PC.
It only LISTENS to what the PingPong lamps already broadcast on UDP 46070:

- PingPongHello (every 3 s): name, PC, version, role, master, F12 /
  urgent calls, F-key block and ping-pong lock state, N1MM band/mode;
- relay packets: each lamp's own N1MM RadioInfo (frequency, mode,
  transmitting, function key caption, Run/S&P).

It never sends anything and never opens N1MM's port 12060, so it can run
next to a lamp (or on a PC without N1MM) and never takes N1MM's packets
away from a lamp. Stations whose PC runs no lamp are not shown.
"""

import argparse
import configparser
import os
import re
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

import n1mm_lamps as pp

TITLE = "PingPong Overview"
SETTINGS_FILE = "pingpong_overview.ini"
GONE_AFTER = pp.PEER_TIMEOUT  # s: lamp shown grey
FORGET_AFTER = 120.0  # s: lamp removed from the table
PP_LIMIT = pp.PP_LIMIT  # s: ping-pong slot frees itself
REFRESH_MS = 250

STATUS_N1MM = re.compile(r"N1MM (\S+) (\S+) from [^,]*, (Run|S&P)")

COLUMNS = [
    # id, heading, width, anchor
    ("station", "Station", 110, "w"),
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
    ("seen", "Seen", 55, "e"),
]


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def ago(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return "{} s".format(seconds)
    if seconds < 3600:
        return "{}:{:02d} min".format(seconds // 60, seconds % 60)
    return "{} h".format(seconds // 3600)


def band_sort(band):
    try:
        return float(band)
    except (TypeError, ValueError):
        return 999.0


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

    def name(self):
        return self.hello.get("alias") or self.hello.get("host") or self.n1mm_name or self.id

    def key(self):
        """(band, mode) from the relayed N1MM data, else from the hello status."""
        if self.freq:
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
    def __init__(self, root, port):
        self.root = root
        self.port = port
        self.lamps = {}
        self.lock = threading.Lock()
        self.slots = {}  # band -> [node_id of INB holding the slot, time]
        self.error = ""
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
                if not is_tx and lamp.tx:
                    lamp.last_tx_end = now
                if is_tx and caption:
                    lamp.caption = caption
                    lamp.last_caption = caption
                lamp.tx = is_tx
            return
        info = pp.parse_hello(data)
        if info:
            with self.lock:
                lamp = self._lamp(info["id"])
                lamp.hello = info
                lamp.hello_seen = now
                lamp.src = src

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
            if slot is None or now - slot[1] > PP_LIMIT:
                self.slots[band] = [lamp.id, now]
            elif slot[0] == lamp.id:
                slot[1] = now

    # ------------------------------------------------------------ colors

    def _master(self, now):
        lamps = [l for l in self.lamps.values() if now - l.hello_seen <= GONE_AFTER]
        acting = [l for l in lamps if l.hello.get("block_time")]
        return acting

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

    def _text_on(self, color):
        try:
            r, g, b = (v / 256 for v in self.root.winfo_rgb(color))
        except tk.TclError:
            return "black"
        return "black" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "white"

    # ------------------------------------------------------------ UI

    def _build(self):
        self.root.title("{}  v{}".format(TITLE, pp.__version__))
        top = tk.Frame(self.root, padx=8, pady=6)
        top.pack(fill=tk.X)
        self.head = tk.Label(top, text="", anchor="w", font=("Segoe UI", 10, "bold"))
        self.head.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.var_top = tk.BooleanVar(
            value=self.cfg.get("window", "on_top", fallback="no") == "yes"
        )
        tk.Checkbutton(
            top, text="Always on top", variable=self.var_top, command=self._on_top
        ).pack(side=tk.RIGHT)
        self._on_top(save=False)

        style = ttk.Style(self.root)
        style.configure("Treeview", rowheight=24, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        frame = tk.Frame(self.root, padx=8)
        frame.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(
            frame, columns=[c[0] for c in COLUMNS], show="headings", selectmode="none"
        )
        for cid, text, width, anchor in COLUMNS:
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=width, anchor=anchor, stretch=cid in ("tx", "last", "calls"))
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("gone", foreground="#999999")
        self.tree.tag_configure("urgent", background="#ff3030", foreground="white")
        self.tree.tag_configure("call", background="#ffe08a")

        self.foot = tk.Label(
            self.root, text="", anchor="w", justify=tk.LEFT, padx=8, pady=6,
            font=("Segoe UI", 9), wraplength=1100,
        )
        self.foot.pack(fill=tk.X)
        geo = self.cfg.get("window", "geometry", fallback="")
        self.root.geometry(geo or "1250x360")

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
        try:
            self.cfg.set("window", "geometry", self.root.geometry())
            self._save()
        except Exception:
            pass
        self.stop.set()
        self.root.destroy()

    # ------------------------------------------------------------ refresh

    def _row(self, lamp, now, masters):
        h = lamp.hello
        key = lamp.key()
        band, mode = key if key else ("", "")
        running = lamp.is_running()
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
        if slot and slot[0] == lamp.id and now - slot[1] <= PP_LIMIT:
            keys.append("holds slot")
        name = lamp.name()
        if lamp in masters:
            name += "  ★"
        role = h.get("role", "") or "–"
        values = {
            "station": name,
            "pc": h.get("host", "") or lamp.src,
            "band": band,
            "mode": mode,
            "freq": pp.freq_khz(lamp.freq) if lamp.freq and now - lamp.radio_seen < 60 else "",
            "run": {True: "Run", False: "S&P", None: "?"}[running],
            "role": role,
            "tx": tx,
            "last": last,
            "calls": ", ".join(calls),
            "keys": ", ".join(keys),
            "ver": h.get("version", "") or "?",
            "seen": ago(live),
        }
        if live > GONE_AFTER:
            tag = "gone"
        elif h.get("urgent"):
            tag = "urgent"
        elif tx:
            tag = "tx_" + lamp.id
            color = self._color_of(lamp, masters)
            self.tree.tag_configure(tag, background=color, foreground=self._text_on(color))
        elif h.get("alert"):
            tag = "call"
        else:
            tag = ""
        return values, tag, (band_sort(band), mode, name.lower())

    def _refresh(self):
        try:
            self._refresh_body()
        except Exception:
            pp.log_error("overview display")
        self.root.after(REFRESH_MS, self._refresh)

    def _refresh_body(self):
        now = time.time()
        with self.lock:
            for node_id in [
                k for k, l in self.lamps.items()
                if now - max(l.hello_seen, l.radio_seen) > FORGET_AFTER
            ]:
                del self.lamps[node_id]
            lamps = list(self.lamps.values())
            masters = self._master(now)
            rows = [(lamp.id,) + self._row(lamp, now, masters) for lamp in lamps]
        rows.sort(key=lambda r: r[3])
        wanted = [r[0] for r in rows]
        for item in self.tree.get_children():
            if item not in wanted:
                self.tree.delete(item)
        for index, (node_id, values, tag, _) in enumerate(rows):
            vals = [values[c[0]] for c in COLUMNS]
            if self.tree.exists(node_id):
                self.tree.item(node_id, values=vals, tags=(tag,) if tag else ())
                if self.tree.index(node_id) != index:
                    self.tree.move(node_id, "", index)
            else:
                self.tree.insert("", index, iid=node_id, values=vals, tags=(tag,) if tag else ())

        live = [l for l in lamps if now - l.hello_seen <= GONE_AFTER]
        if len(masters) == 1:
            m = masters[0]
            head = "{} lamps  ·  master: {}  ·  F12 block time: S&P call {:g} s, Run call {:g} s".format(
                len(live), m.name(), m.hello.get("block_time", 0) or 30,
                m.hello.get("block_time_run", 0) or 5,
            )
        elif masters:
            head = "{} lamps  ·  ⚠ TWO MASTERS: {}".format(
                len(live), ", ".join(sorted(m.name() for m in masters)))
        else:
            head = "{} lamps  ·  no master heard".format(len(live))
        self.head.configure(text=head, fg="#c00000" if len(masters) > 1 else "black")

        notes = []
        if self.error:
            notes.append("⚠ " + self.error)
        versions = sorted({l.hello.get("version") for l in live if l.hello.get("version")},
                          key=lambda v: tuple(int(x) for x in re.findall(r"\d+", v)))
        if len(versions) > 1:
            notes.append("⚠ different versions: " + ", ".join("v" + v for v in versions))
        for band, slot in sorted(self.slots.items(), key=lambda s: band_sort(s[0])):
            if now - slot[1] <= PP_LIMIT and slot[0] in self.lamps:
                notes.append("ping-pong {} MHz: slot held by {}".format(
                    band, self.lamps[slot[0]].name()))
        no_relay = [l.name() for l in live if now - l.radio_seen > 30]
        if no_relay:
            notes.append("no N1MM data from: {} (N1MM not running, or 'Share my N1MM' off)".format(
                ", ".join(sorted(no_relay))))
        notes.append("★ = master  ·  listening on UDP {} (read only)".format(self.port))
        self.foot.configure(text="   ·   ".join(notes))


def main():
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument(
        "--discovery-port", type=int, default=46070,
        help="port the PingPong lamps talk on (default 46070)",
    )
    args = parser.parse_args()
    root = tk.Tk()
    try:
        root.iconbitmap(default=os.path.join(getattr(sys, "_MEIPASS", app_dir()), "pingpong.ico"))
    except tk.TclError:
        pass
    Overview(root, args.discovery_port)
    root.mainloop()


if __name__ == "__main__":
    main()
