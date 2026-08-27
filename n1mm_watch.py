"""N1MM Logger+ UDP 12060 traffic watcher.

Listens for the N1MM external UDP broadcast (XML) on port 12060 and
shows every packet in a small window. Traffic is grouped per station
(StationName) so the same tool works for two up to six networked PCs.

Usage:
    python n1mm_watch.py [--port 12060] [--bind 0.0.0.0] [--log FILE] [--selftest]
"""

import argparse
import json
import os
import queue
import socket
import threading
import time
import tkinter as tk
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET
from tkinter import ttk

DEFAULT_PORT = 12060
MAX_QUEUE = 20000

CANONICAL_TAGS = {
    "radioinfo": "RadioInfo",
    "contactinfo": "ContactInfo",
    "appinfo": "AppInfo",
    "spot": "Spot",
    "dynamicresults": "dynamicresults",
    "scoreresults": "ScoreResults",
    "score": "Score",
    "lookupinfo": "LookupInfo",
    "possiblecall": "PossibleCall",
    "rotatorinfo": "RotatorInfo",
}

TYPE_LABELS = {
    "RadioInfo": "Radio",
    "ContactInfo": "Kontakt",
    "Spot": "Spota",
    "dynamicresults": "Dinamika",
    "ScoreResults": "Tabela",
    "Score": "Tabela",
    "AppInfo": "Aplikacija",
    "LookupInfo": "Lookup",
    "PossibleCall": "Klic",
    "RotatorInfo": "Rotator",
    "RAW": "Neznano",
}

TYPE_TAGS = {
    "RadioInfo": "radio",
    "ContactInfo": "contact",
    "Spot": "spot",
    "dynamicresults": "score",
    "ScoreResults": "score",
    "Score": "score",
    "AppInfo": "appinfo",
    "LookupInfo": "lookup",
    "PossibleCall": "lookup",
    "RAW": "raw",
}

SUMMARY_FIELDS = {
    "RadioInfo": ["FunctionKeyCaption", "RadioNr", "Freq", "Mode", "OpCall", "IsTransmitting"],
    "ContactInfo": ["Timestamp", "Callsign", "Operator", "Mode", "Freq", "Exchange1"],
    "Spot": ["dxcall", "frequency", "mode", "spottercall", "status"],
    "dynamicresults": ["contest", "call", "ops", "score", "grid6"],
    "ScoreResults": ["ContestName", "OpCall", "Score"],
    "AppInfo": ["StationName", "contestname", "dbname"],
    "LookupInfo": ["Call", "Operator", "Name"],
    "PossibleCall": ["Call", "Old", "New"],
}


def decode_packet(data):
    raw = data.decode("utf-8", errors="replace")
    start = raw.find("<")
    text = raw[start:] if start >= 0 else ""
    if not text:
        return "RAW", {}, ""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return "RAW", {}, text
    fields = {}
    for el in root.iter():
        if el.text and el.text.strip():
            fields.setdefault(el.tag, el.text.strip())
    return CANONICAL_TAGS.get(root.tag.lower(), root.tag), fields, text


def format_freq(freq):
    try:
        value = float(freq)
    except (TypeError, ValueError):
        return freq
    if value <= 0:
        return freq
    if value >= 100000:
        return "{:.3f} MHz".format(value / 100000.0)
    return freq


def summarize(msg, fields):
    keys = SUMMARY_FIELDS.get(msg, list(fields)[:5])
    parts = []
    for key in keys:
        value = fields.get(key)
        if value:
            parts.append("{}={}".format(key, value))
    return "  ".join(parts)


def hexdump(data):
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hexpart = " ".join("{:02x}".format(b) for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append("{:04x}  {:<47}  {}".format(i, hexpart, asc))
    return "\n".join(lines)


def open_socket(bind_ip, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((bind_ip, port))
    sock.settimeout(0.5)
    return sock


def local_interfaces():
    ips = []
    try:
        host = socket.gethostbyname_ex(socket.gethostname())[2]
        ips.extend(host)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if info[4][0] not in ips:
                ips.append(info[4][0])
    except OSError:
        pass
    return [ip for ip in ips if not ip.startswith("127.")]


class Receiver:
    def __init__(self, bind_ip, port, queue_obj):
        self.q = queue_obj
        self.bind_ip = bind_ip
        self.port = port
        self.sock = None
        self.thread = None
        self.running = False
        self.mode = ""

    def start(self):
        try:
            self.sock = open_socket(self.bind_ip, self.port)
            self.mode = "skupna vezava 0.0.0.0"
        except OSError:
            bound = False
            for ip in local_interfaces():
                try:
                    self.sock = open_socket(ip, self.port)
                    self.bind_ip = ip
                    self.mode = "unicast vezava {}".format(ip)
                    bound = True
                    break
                except OSError:
                    continue
            if not bound:
                raise
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        if self.thread is not None:
            self.thread.join(timeout=1.5)
            self.thread = None

    def _loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if self.q.qsize() < MAX_QUEUE:
                self.q.put((time.time(), addr[0], data))


class App:
    def __init__(self, root, port, bind_ip, log_path):
        self.root = root
        self.port = port
        self.bind_ip = bind_ip
        self.log_path = log_path
        self.q = queue.Queue()
        self.receiver = None
        self.stations = {}
        self.station_iids = {}
        self.rec_by_iid = {}
        self.counters = {}
        self.total = 0
        self.listening = False

        self.paused = tk.BooleanVar(value=False)
        self.autoscroll = tk.BooleanVar(value=True)
        self.raw = tk.BooleanVar(value=False)
        self.saving = tk.BooleanVar(value=False)

        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.set_listening(True)
        self.root.after(150, self._poll)

    def _build_ui(self):
        root = self.root
        root.title("N1MM PingPong  -  UDP {}".format(DEFAULT_PORT))
        root.geometry("1020x640")
        root.minsize(760, 420)

        bar = ttk.Frame(root, padding=(6, 4))
        bar.pack(side=tk.TOP, fill=tk.X)
        self.listen_btn = ttk.Button(bar, text="Ustavi", width=8, command=self.toggle_listening)
        self.listen_btn.pack(side=tk.LEFT)
        ttk.Checkbutton(bar, text="Zamrzni", variable=self.paused).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Checkbutton(bar, text="Avtopomik", variable=self.autoscroll).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Checkbutton(bar, text="Hex/surovo", variable=self.raw).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Checkbutton(bar, text="Zapisuj v datoteko", variable=self.saving).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Button(bar, text="Počisti", width=8, command=self.clear_traffic).pack(side=tk.RIGHT)

        paned = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=(2, 0))

        left = ttk.Frame(paned, width=300)
        paned.add(left, weight=1)
        ttk.Label(left, text="Postaje").pack(anchor=tk.W)
        cols = ("station", "ip", "freq", "mode", "call", "lastcall", "packets", "last")
        self.stations_view = ttk.Treeview(
            left, columns=cols, show="headings", height=8
        )
        headings = {
            "station": ("Postaja", 90),
            "ip": ("IP", 110),
            "freq": ("Frekvenca", 95),
            "mode": ("Mod", 50),
            "call": ("Operator", 70),
            "lastcall": ("Zadnji klic", 90),
            "packets": ("Paketi", 60),
            "last": ("Zadnje", 70),
        }
        for col, (text, width) in headings.items():
            self.stations_view.heading(col, text=text)
            self.stations_view.column(col, width=width, anchor=tk.W, stretch=False)
        self.stations_view.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        right = ttk.Frame(paned)
        paned.add(right, weight=3)
        ttk.Label(right, text="Promet").pack(anchor=tk.W)
        tcols = ("time", "src", "station", "type", "summary")
        self.traffic = ttk.Treeview(right, columns=tcols, show="headings")
        thead = {
            "time": ("Čas", 80),
            "src": ("Vir", 115),
            "station": ("Postaja", 90),
            "type": ("Tip", 80),
            "summary": ("Vsebina", 330),
        }
        for col, (text, width) in thead.items():
            self.traffic.heading(col, text=text)
            self.traffic.column(col, width=width, anchor=tk.W, stretch=(col == "summary"))
        vsb = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.traffic.yview)
        self.traffic.configure(yscrollcommand=vsb.set)
        self.traffic.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.detail = tk.Text(right, height=8, wrap=tk.NONE, state=tk.DISABLED)
        self.detail.pack(side=tk.TOP, fill=tk.BOTH, expand=False, pady=(4, 0))

        self.traffic.tag_configure("radio", foreground="#0b5394")
        self.traffic.tag_configure("contact", foreground="#38761d")
        self.traffic.tag_configure("spot", foreground="#b45f06")
        self.traffic.tag_configure("score", foreground="#674ea7")
        self.traffic.tag_configure("appinfo", foreground="#666666")
        self.traffic.tag_configure("lookup", foreground="#0f7a75")
        self.traffic.tag_configure("raw", foreground="#c00000")
        self.traffic.bind("<<TreeviewSelect>>", self.on_select)

        self.status = ttk.Label(root, relief=tk.SUNKEN, anchor=tk.W, padding=(6, 2))
        self.status.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar()
        self.status.configure(textvariable=self.status_var)

    def toggle_listening(self):
        self.set_listening(not self.listening)

    def set_listening(self, state):
        if state == self.listening:
            return
        if state:
            try:
                self.receiver = Receiver(self.bind_ip, self.port, self.q)
                self.receiver.start()
            except OSError as exc:
                self.status_var.set("NAPAKA: ni mogoče odpreti porta {} ({})".format(self.port, exc))
                return
            self.listening = True
            self.listen_btn.configure(text="Ustavi")
        else:
            if self.receiver is not None:
                self.receiver.stop()
                self.receiver = None
            self.listening = False
            self.listen_btn.configure(text="Poslušaj")

    def clear_traffic(self):
        for iid in self.traffic.get_children():
            self.traffic.delete(iid)
        self.rec_by_iid.clear()
        self.counters.clear()

    def _poll(self):
        while True:
            try:
                ts, src, data = self.q.get_nowait()
            except queue.Empty:
                break
            self._handle(ts, src, data)
        self._refresh_status()
        self.root.after(150, self._poll)

    def _handle(self, ts, src, data):
        self.total += 1
        tag, fields, text = decode_packet(data)
        station = fields.get("StationName") or fields.get("app") or src

        rec = {
            "ts": ts,
            "src": src,
            "station": station,
            "tag": tag,
            "fields": fields,
            "text": text,
            "data": data,
            "summary": summarize(tag, fields),
        }
        self._update_station(rec)

        if self.saving.get():
            self._write_log(rec)

        if self.paused.get():
            return

        time_str = time.strftime("%H:%M:%S", time.localtime(ts))
        label = TYPE_LABELS.get(tag, tag)
        self.counters[tag] = self.counters.get(tag, 0) + 1
        iid = self.traffic.insert(
            "",
            tk.END,
            values=(time_str, src, station, label, rec["summary"]),
            tags=(TYPE_TAGS.get(tag, ""),),
        )
        self.rec_by_iid[iid] = rec
        trimmed = self.traffic.get_children()
        if len(trimmed) > 4000:
            for old in trimmed[:500]:
                self.traffic.delete(old)
                self.rec_by_iid.pop(old, None)
        if self.autoscroll.get():
            self.traffic.see(iid)

    def _update_station(self, rec):
        name = rec["station"]
        freq = rec["fields"].get("Freq") or rec["fields"].get("TXFreq") or ""
        mode = rec["fields"].get("Mode") or ""
        call = rec["fields"].get("OpCall") or ""
        last_call = rec["fields"].get("Callsign") or ""

        if name not in self.stations:
            self.stations[name] = {
                "ip": rec["src"],
                "count": 0,
                "freq": "",
                "mode": "",
                "call": "",
                "last_call": "",
                "last": 0.0,
            }
            self.station_iids[name] = self.stations_view.insert(
                "", tk.END, values=(name, rec["src"], "", "", "", "", 0, "")
            )
        st = self.stations[name]
        st["count"] += 1
        st["ip"] = rec["src"]
        st["last"] = rec["ts"]
        if freq:
            st["freq"] = freq
        if mode:
            st["mode"] = mode
        if call:
            st["call"] = call
        if last_call:
            st["last_call"] = last_call

        time_str = time.strftime("%H:%M:%S", time.localtime(st["last"]))
        self.stations_view.item(
            self.station_iids[name],
            values=(
                name,
                st["ip"],
                format_freq(st["freq"]),
                st["mode"],
                st["call"],
                st["last_call"],
                st["count"],
                time_str,
            ),
        )

    def _write_log(self, rec):
        line = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec["ts"])),
            "src": rec["src"],
            "station": rec["station"],
            "type": rec["tag"],
            "data": rec["text"] or rec["data"].hex(),
        }
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def on_select(self, event):
        selection = self.traffic.selection()
        if not selection:
            return
        rec = self.rec_by_iid.get(selection[0])
        if rec is None:
            return
        time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec["ts"]))
        if self.raw.get() or rec["tag"] == "RAW":
            body = hexdump(rec["data"])
            if rec["text"]:
                body = body + "\n\n" + rec["text"]
        else:
            try:
                body = minidom.parseString(rec["text"]).toprettyxml(indent="  ")
            except Exception:
                body = rec["text"]
        header = "[{}]  {}  Postaja: {}  Tip: {}\n".format(
            time_str, rec["src"], rec["station"], TYPE_LABELS.get(rec["tag"], rec["tag"])
        )
        self.detail.configure(state=tk.NORMAL)
        self.detail.delete("1.0", tk.END)
        self.detail.insert("1.0", header + "\n" + body)
        self.detail.configure(state=tk.DISABLED)

    def _refresh_status(self):
        parts = []
        if self.listening:
            parts.append("Poslušam UDP {}".format(self.port))
            if self.receiver is not None and self.receiver.mode:
                parts.append(self.receiver.mode)
        else:
            parts.append("Zaustavljeno")
        parts.append("postaj: {}".format(len(self.stations)))
        parts.append("paketov: {}".format(self.total))
        counts = "  ".join("{}: {}".format(TYPE_LABELS.get(k, k), v) for k, v in self.counters.items())
        if counts:
            parts.append(counts)
        if not self.log_path or not self.saving.get():
            self.status_var.set("  |  ".join(parts))
        else:
            self.status_var.set("  |  ".join(parts) + "  |  zapis: " + self.log_path)

    def on_close(self):
        self.set_listening(False)
        self.root.destroy()


def selftest():
    samples = [
        (
            b'<?xml version="1.0" encoding="utf-8"?><AppInfo><app>N1MM</app>'
            b'<dbname>N8SL_FIELDDAY.s3db</dbname><contestnr>2</contestnr>'
            b'<contestname>ARRL-FIELD-DAY</contestname><StationName>80M-TENT</StationName></AppInfo>'
        ),
        (
            b'<?xml version="1.0"?><RadioInfo><app>N1MM</app><StationName>CW-80m</StationName>'
            b'<RadioNr>1</RadioNr><Freq>352211</Freq><TXFreq>352211</TXFreq><Mode>CW</Mode>'
            b'<OpCall>W1ABC</OpCall><IsRunning>False</IsRunning></RadioInfo>'
        ),
        (
            b'<?xml version="1.0"?><ContactInfo><app>N1MM</app><StationName>CW-80m</StationName>'
            b'<Timestamp>2026-08-27 12:34:56</Timestamp><Callsign>OK1ABC</Callsign>'
            b'<Operator>W1ABC</Operator><Mode>CW</Mode><Freq>352211</Freq>'
            b'<RSTSent>599</RSTSent><RSTRecv>579</RSTRecv></ContactInfo>'
        ),
        (
            b'<?xml version="1.0" encoding="utf-8"?><spot><app>N1MM</app>'
            b'<StationName>S51CAB</StationName><dxcall>CT1EXR</dxcall>'
            b'<frequency>14061,27</frequency><spottercall>DF7GB-#</spottercall>'
            b'<timestamp>2026-08-27 18:03:56</timestamp><action>add</action>'
            b'<mode>CW</mode><comment>CW 6DB Q:9+</comment></spot>'
        ),
        (
            b'<?xml version="1.0"?><dynamicresults><contest>CQ-WW-CW</contest>'
            b'<call>S55OO</call><ops>S55OO</ops><score>0</score>'
            b'<timestamp>2026-08-27 18:03:58</timestamp></dynamicresults>'
        ),
    ]
    for data in samples:
        tag, fields, text = decode_packet(data)
        print("[{}] {}".format(tag, summarize(tag, fields)))
        if tag == "RadioInfo":
            print("   frekvenca: {}".format(format_freq(fields.get("Freq", ""))))
    print("selftest OK")


def main():
    parser = argparse.ArgumentParser(description="N1MM Logger+ UDP 12060 watcher")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument(
        "--log",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "n1mm_pingpong.log"),
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        selftest()
        return

    root = tk.Tk()
    App(root, args.port, args.bind, args.log)
    root.mainloop()


if __name__ == "__main__":
    main()