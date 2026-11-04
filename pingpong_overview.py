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

It never sends anything to the lamps and never opens N1MM's port 12060,
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
from tkinter import messagebox, ttk

import n1mm_lamps as pp

TITLE = "PingPong Overview"
SETTINGS_FILE = "pingpong_overview.ini"
WEB_PORT = 8046
GONE_AFTER = pp.PEER_TIMEOUT  # s: lamp shown grey
FORGET_AFTER = 120.0  # s: lamp removed from the table
PP_LIMIT = pp.PP_LIMIT  # s: ping-pong slot frees itself
REFRESH_MS = 250
FIREWALL_RULE = "PingPong Overview web page"

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
# Web page order: what matters during the contest first (phones scroll).
WEB_ORDER = ["station", "band", "mode", "run", "role", "tx", "calls", "keys",
             "freq", "last", "pc", "ver", "seen"]
ROW_STYLE = {  # tag -> (background, text); "tx" uses the station color
    "gone": ("", "#999999"),
    "urgent": ("#ff3030", "#ffffff"),
    "call": ("#ffe08a", "#000000"),
}


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
        self.snapshot = {"head": "starting…", "rows": [], "notes": []}
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
        top = tk.Frame(self.root, padx=8, pady=6)
        top.pack(fill=tk.X)
        self.head = tk.Label(top, text="", anchor="w", font=("Segoe UI", 10, "bold"))
        self.head.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.var_top = tk.BooleanVar(value=self.cfg.get("window", "on_top", fallback="no") == "yes")
        tk.Checkbutton(top, text="Always on top", variable=self.var_top,
                       command=self._on_top).pack(side=tk.RIGHT)
        if self.web_port and sys.platform == "win32":
            tk.Button(top, text="Allow web page in firewall…",
                      command=self._allow_firewall).pack(side=tk.RIGHT, padx=8)
        self._on_top(save=False)

        style = ttk.Style(self.root)
        style.configure("Treeview", rowheight=24, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        frame = tk.Frame(self.root, padx=8)
        frame.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in COLUMNS], show="headings",
                                 selectmode="none")
        for cid, text, width, anchor in COLUMNS:
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=width, anchor=anchor,
                             stretch=cid in ("tx", "last", "calls"))
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        for tag, (bg, fg) in ROW_STYLE.items():
            opts = {"foreground": fg}
            if bg:
                opts["background"] = bg
            self.tree.tag_configure(tag, **opts)

        self.foot = tk.Label(self.root, text="", anchor="w", justify=tk.LEFT, padx=8, pady=6,
                             font=("Segoe UI", 9), wraplength=1200)
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

    # ------------------------------------------------------------ table data

    def _row(self, lamp, now, masters):
        h = lamp.hello
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
        if slot and slot[0] == lamp.id and now - slot[1] <= PP_LIMIT:
            keys.append("holds slot")
        name = lamp.name() + ("  ★" if lamp in masters else "")
        values = {
            "station": name,
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
            "seen": ago(live),
        }
        bg, fg = "", ""
        if live > GONE_AFTER:
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
            rows = [self._row(lamp, now, masters) for lamp in lamps]
            slots = dict(self.slots)
        rows.sort(key=lambda r: r["sort"])
        live = [l for l in lamps if now - l.hello_seen <= GONE_AFTER]
        warn = False
        if len(masters) == 1:
            m = masters[0]
            head = "{} lamps  ·  master: {}  ·  F12 block time: S&P call {:g} s, Run call {:g} s".format(
                len(live), m.name(), m.hello.get("block_time", 0) or 30,
                m.hello.get("block_time_run", 0) or 5)
        elif masters:
            warn = True
            head = "{} lamps  ·  ⚠ TWO MASTERS: {}".format(
                len(live), ", ".join(sorted(m.name() for m in masters)))
        else:
            head = "{} lamps  ·  no master heard".format(len(live))
        notes = []
        for err in (self.error, self.web_error):
            if err:
                notes.append("⚠ " + err)
        versions = sorted({l.hello.get("version") for l in live if l.hello.get("version")},
                          key=version_key)
        if len(versions) > 1:
            notes.append("⚠ different versions: " + ", ".join("v" + v for v in versions))
        for band, slot in sorted(slots.items(), key=lambda s: band_sort(s[0])):
            if now - slot[1] <= PP_LIMIT and slot[0] in self.lamps:
                notes.append("ping-pong {} MHz: slot held by {}".format(band, self.lamps[slot[0]].name()))
        no_relay = [l.name() for l in live if now - l.radio_seen > 30]
        if no_relay:
            notes.append("no N1MM data from: {} (N1MM not running, or 'Share my N1MM' off)".format(
                ", ".join(sorted(no_relay))))
        notes.append("★ = master")
        for r in rows:
            r.pop("sort")
        return {
            "title": "{} v{}".format(TITLE, pp.__version__),
            "head": head, "warn": warn, "rows": rows, "notes": notes,
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
        snap = self._make_snapshot()
        self.snapshot = snap  # served to the web page as it is
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
        self.head.configure(text=snap["head"], fg="#c00000" if snap["warn"] else "black")
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
  head.textContent = d.head; head.className = d.warn ? "warn" : "";
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
    root = tk.Tk()
    try:
        root.iconbitmap(default=resource("pingpong.ico"))
    except tk.TclError:
        pass
    Overview(root, args.discovery_port, args.web_port)
    root.mainloop()


if __name__ == "__main__":
    main()
