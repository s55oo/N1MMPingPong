"""N1MM Logger+ UDP 12060 station display.

One compact always-on-top window that shows which station is
currently transmitting. The lamp lights up in the station's color
exactly while RadioInfo reports IsTransmitting=true, and goes off as
soon as the transmission ends, matching N1MM. It shows the text of
the last function key press (FunctionKeyCaption, "Fx" prefix
removed).

Group key: only stations on the same band and mode as the local
computer (running this window) are shown together. Bands 1.8, 3.5,
7, 14, 21, 28 MHz; modes CW, RTTY, USB, LSB. A station on another
band/mode is never shown in this window, so separate radios do not
clash with each other.

Stations are detected automatically from the traffic. Optional hints
in lamps.cfg ("IP,label,color" per line, '#' ignored) assign a wanted
label/color to known computers; any new station gets a label from
its packet and a color from the auto palette.

Made by S55OO with AI assistance.

Version: 1.4

Usage:
    python n1mm_lamps.py [--port 12060] [--stale 5.0] [--config lamps.cfg]
"""

__version__ = "1.4"

import argparse
import base64
import os
import re
import socket
import sys
import threading
import time
import tkinter as tk
import webbrowser
import xml.etree.ElementTree as ET

DEFAULT_PORT = 12060
DEFAULT_STALE = 5.0
HELP_URL = "https://github.com/s55oo/N1MMPingPong"
HELP_ICON_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAABY0lEQVR4nGNgoDJgxCcplb/1PzbxZxO9cepjJMUgYgxmxGcYIyMDQ4ajEkOCjTyDBD8Hw6N33xhm7r/PsOTYI5yGMuFzWaKtAkOJpypD08brDDrVuxmm7rnL0BqizRBpIYtTDxNOCUZGhmIPVYaFRx4xbL34guHzjz8MK04+YVhx4jFYHJdDmHAED4OGFC+DABcrw77rr1DELz76yCApwMHAw86CVR8TNteBwLWnnxikC7YxHLn1FkVcS5qP4dP33wzffv3F6komBhKArZoIQ6yVHMPsgw8Y/v3HnhCYiDXMWEGQYX6KMcP+G68ZJu66g1MdEzGGMTMxMvRF6jJcefqJIX3BOYa///5TZqCONB+DijgPQ+umGww/f//Dq5aJGAPF+NjB9K2XXwiqZSQ1u+ECz6A5higXRlnIMjyd4MWgL8tPvAspceUzpPzMRGyxRIxhDNi8TIqhRBVfyICcApbqAAC2c3+GEqNjHgAAAABJRU5ErkJggg=="
)
COLOR_IDLE = "#3a3a3a"
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
MODES_ALLOWED = {"CW", "RTTY", "USB", "LSB"}

FONT_SIZES = [(4, 26), (8, 22), (12, 18), (9999, 16)]


def band_of(freq_str):
    try:
        mhz = float(freq_str) / 100000.0
    except (TypeError, ValueError):
        return None
    for lo, hi, name in BANDS:
        if lo <= mhz < hi:
            return name
    return None


def group_of(freq, mode):
    band = band_of(freq)
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


def local_interfaces():
    ips = []
    try:
        ips.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if info[4][0] not in ips:
                ips.append(info[4][0])
    except OSError:
        pass
    return [ip for ip in ips if not ip.startswith("127.")]


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


def open_socket(bind_ip, port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((bind_ip, port))
    sock.settimeout(0.3)
    return sock


def listener_loop(bind_ip, port, on_packet, stop):
    try:
        sock = open_socket(bind_ip, port)
    except OSError:
        for ip in local_interfaces():
            try:
                sock = open_socket(ip, port)
                break
            except OSError:
                continue
        else:
            return
    while not stop.is_set():
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break
        on_packet(addr[0], data)
    try:
        sock.close()
    except OSError:
        pass


class Station:
    def __init__(self, ip, label, color):
        self.ip = ip
        self.label = label or ip
        self.color = color
        self.count = 0
        self.msg = ""
        self.tx = False
        self.last_seen = 0.0
        self.freq = ""
        self.mode = ""
        self.key = None


class DisplayApp:
    def __init__(self, root, config_path, port, stale):
        self.root = root
        self.port = port
        self.stale = stale
        self.config_path = config_path
        self.hints = read_config(config_path)
        self.local = set(local_interfaces())
        self.stations = {}
        self.used_colors = set(color for _, color in self.hints.values() if color)
        self.tracked = None
        self._build()
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=listener_loop,
            args=("0.0.0.0", port, self.on_packet, self.stop),
            daemon=True,
        )
        self.thread.start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(120, self._update)

    def _station(self, src, station_name):
        ip = src
        if ip in self.stations:
            return self.stations[ip]
        hint_label, hint_color = self.hints.get(ip, ("", ""))
        color = hint_color
        if not color:
            free = [c for c in COLOR_PALETTE if c not in self.used_colors]
            color = free[0] if free else COLOR_PALETTE[-1]
            self.used_colors.add(color)
            write_config(self.config_path, ip, station_name or ip, color)
        label = hint_label or station_name or ip
        station = Station(ip, label, color)
        self.stations[ip] = station
        return station

    def on_packet(self, src, data):
        is_radio, caption, station_name, is_tx, freq, mode = packet_info(data)
        station = self._station(src, station_name)
        station.count += 1
        if not station.label or station.label == station.ip:
            station.label = station_name or station.label
        if is_radio:
            station.tx = is_tx
            station.last_seen = time.time()
            if caption:
                station.msg = caption
            if freq:
                station.freq = freq
            if mode:
                station.mode = mode
            station.key = group_of(station.freq, station.mode)
            if station.ip in self.local:
                self.tracked = station.key

    def _is_tracked(self, station):
        return bool(self.tracked) and station.key == self.tracked

    def _build(self):
        self.root.title("PingPong  -  UDP {}  v{}".format(self.port, __version__))
        self.root.attributes("-topmost", True)
        frame = tk.Frame(self.root, padx=6, pady=4)
        frame.pack()
        top = tk.Frame(frame)
        top.pack(fill=tk.X)
        tk.Label(top, text="PingPong", font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)
        self.help_icon = tk.PhotoImage(
            data=base64.b64decode(HELP_ICON_B64.encode("ascii"))
        )
        self.help_link = tk.Label(top, image=self.help_icon, cursor="hand2")
        self.help_link.pack(side=tk.RIGHT)
        self.help_link.bind("<Button-1>", lambda e: self._open_help())
        self.canvas = tk.Canvas(
            frame,
            width=330,
            height=88,
            bg=COLOR_IDLE,
            highlightthickness=1,
            highlightbackground="#888888",
        )
        self.canvas.pack(fill=tk.X)
        self.text_id = self.canvas.create_text(
            165, 44, text="—", font=("Segoe UI", 20, "bold"), fill="white"
        )
        self.canvas.bind("<Configure>", self._recenter_text)
        self.info = tk.Label(frame, text="", font=("Segoe UI", 8), justify=tk.CENTER)
        self.info.pack(fill=tk.X)

    def _recenter_text(self, event):
        self.canvas.coords(self.text_id, event.width / 2.0, event.height / 2.0)

    def _open_help(self):
        try:
            webbrowser.open(HELP_URL)
        except Exception:
            pass

    def _font_for(self, text):
        length = len(text)
        for limit, size in FONT_SIZES:
            if length <= limit:
                return ("Segoe UI", size, "bold")
        return ("Segoe UI", 12, "bold")

    def _update(self):
        now = time.time()
        active = None
        latest = -1.0
        for station in self.stations.values():
            if (
                station.tx
                and (now - station.last_seen) <= self.stale
                and self._is_tracked(station)
                and station.last_seen > latest
            ):
                latest = station.last_seen
                active = station

        if active is not None:
            color = active.color or "red"
            shown = active.msg or "—"
            if self.canvas.cget("bg") != color:
                self.canvas.configure(bg=color)
            if self.canvas.itemcget(self.text_id, "text") != shown:
                self.canvas.itemconfigure(self.text_id, text=shown)
                self.canvas.itemconfigure(self.text_id, font=self._font_for(shown))
            self.info.configure(
                text="{}  ({})  oddaja  ·  {} paketov".format(
                    active.label, active.ip, active.count
                )
            )
        else:
            if self.canvas.cget("bg") != COLOR_IDLE:
                self.canvas.configure(bg=COLOR_IDLE)
            if self.canvas.itemcget(self.text_id, "text") != "—":
                self.canvas.itemconfigure(self.text_id, text="—")
                self.canvas.itemconfigure(self.text_id, font=self._font_for("—"))
            header = "spremljam {} · ".format(group_label(self.tracked) or "nezano")
            if self.stations:
                rows = "  |  ".join(
                    "{}: {}".format(st.label, st.count)
                    for st in self.stations.values()
                )
            else:
                rows = "ni podatkov"
            self.info.configure(text=header + rows)

        self.root.after(120, self._update)

    def on_close(self):
        self.stop.set()
        self.root.destroy()


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def main():
    parser = argparse.ArgumentParser(description="N1MM UDP 12060 display")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--stale", type=float, default=DEFAULT_STALE)
    parser.add_argument("--config", default=os.path.join(app_dir(), "lamps.cfg"))
    args = parser.parse_args()

    root = tk.Tk()
    DisplayApp(root, args.config, args.port, args.stale)
    root.mainloop()


if __name__ == "__main__":
    main()