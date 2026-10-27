"""N1MM Logger+ UDP 12060 station display.

One compact always-on-top window that shows which station is
currently transmitting. The lamp lights up in the station's color
exactly while RadioInfo reports IsTransmitting=true, and goes off as
soon as the transmission ends, matching N1MM. It shows the text of
the last function key press (FunctionKeyCaption, "Fx" prefix
removed).

Group key: only stations on the same band and mode as the local
computer (running this window) are shown together. Bands 1.8, 3.5,
7, 14, 21, 28 MHz; modes CW, RTTY, SSB (USB/LSB). A station on another
band/mode is never shown in this window, so separate radios do not
clash with each other.

Stations are detected automatically from the traffic. Optional hints
in lamps.cfg ("IP,label,color" per line, '#' ignored) assign a wanted
label/color to known computers; any new station gets a label from
its packet and a color from the auto palette.

Network discovery: all IPv4 adapters of this PC (LAN, Wi-Fi, VPN) are
enumerated reliably, so the local station is always recognised. PCs
running PingPong also announce themselves to each other with a small
hello broadcast on a separate port (46070), so a PC whose N1MM
broadcast does not arrive here is still listed in the footer.

Made by S55OO with AI assistance.

Version: 1.13

Usage:
    python n1mm_lamps.py [--port 12060] [--stale 5.0] [--config lamps.cfg]
                         [--discovery-port 46070] [--no-discovery]
"""

__version__ = "1.13"

import argparse
import base64
import os
import re
import socket
import struct
import sys
import threading
import time
import tkinter as tk
import webbrowser
import xml.etree.ElementTree as ET
from tkinter import messagebox

import pingpong_keys as keys
import pingpong_update as upd

DEFAULT_PORT = 12060
DEFAULT_STALE = 5.0
# Keep well away from N1MM's own ports (12060 external broadcast and
# multi-computer networking, 12070 multi-computer UDP): N1MM tries to
# parse anything arriving there and throws IndexOutOfRangeException.
DEFAULT_DISCOVERY_PORT = 46070
HELLO_INTERVAL = 3.0
PEER_TIMEOUT = 12.0
NETWORK_REFRESH = 30.0
LOCAL_N1MM_RECENT = 10.0
TEST_FLASH = 1.5
TEST_LOG = "pingpong_test.log"
BLINK_HALF = 0.4  # seconds on / off when a call (F12) blinks the lamp
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
MODES_ALLOWED = {"CW", "RTTY", "SSB"}
# N1MM reports phone as "SSB" (seen in RadioInfo) or USB/LSB depending on
# radio/setup; all of them are one group so stations always match.
MODE_ALIASES = {"USB": "SSB", "LSB": "SSB"}

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


def _usable_ip(ip):
    return bool(ip) and not ip.startswith("127.") and not ip.startswith("169.254.") \
        and ip != "0.0.0.0"


def _broadcast_of(ip, mask):
    ip_n = struct.unpack("!I", socket.inet_aton(ip))[0]
    mask_n = struct.unpack("!I", socket.inet_aton(mask))[0]
    return socket.inet_ntoa(struct.pack("!I", (ip_n | (~mask_n & 0xFFFFFFFF))))


def _windows_ipv4_table():
    """All IPv4 addresses of all adapters (LAN, Wi-Fi, VPN) via iphlpapi.

    Returns [(ip, netmask), ...]. Works regardless of Windows language and
    of how the host name resolves, unlike gethostbyname_ex().
    """
    import ctypes
    from ctypes import wintypes

    class MIB_IPADDRROW(ctypes.Structure):
        _fields_ = [
            ("dwAddr", wintypes.DWORD),
            ("dwIndex", wintypes.DWORD),
            ("dwMask", wintypes.DWORD),
            ("dwBCastAddr", wintypes.DWORD),
            ("dwReasmSize", wintypes.DWORD),
            ("unused1", ctypes.c_ushort),
            ("wType", ctypes.c_ushort),
        ]

    MIB_IPADDR_DISCONNECTED = 0x0008
    MIB_IPADDR_DELETED = 0x0040
    iphlpapi = ctypes.windll.iphlpapi
    size = wintypes.ULONG(0)
    iphlpapi.GetIpAddrTable(None, ctypes.byref(size), False)
    if not size.value:
        return []
    buf = ctypes.create_string_buffer(size.value)
    if iphlpapi.GetIpAddrTable(buf, ctypes.byref(size), False) != 0:
        return []
    count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0]
    rows = ctypes.cast(
        ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD),
        ctypes.POINTER(MIB_IPADDRROW),
    )
    result = []
    for i in range(count):
        row = rows[i]
        if row.wType & (MIB_IPADDR_DISCONNECTED | MIB_IPADDR_DELETED):
            continue
        ip = socket.inet_ntoa(struct.pack("<I", row.dwAddr))
        mask = socket.inet_ntoa(struct.pack("<I", row.dwMask))
        result.append((ip, mask))
    return result


def _route_ip():
    """IP of the adapter holding the default route (no packet is sent)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


def local_networks():
    """[(ip, broadcast), ...] for every active IPv4 adapter on this PC."""
    nets = []
    seen = set()

    def add(ip, mask):
        if _usable_ip(ip) and ip not in seen:
            seen.add(ip)
            try:
                nets.append((ip, _broadcast_of(ip, mask)))
            except OSError:
                pass

    if sys.platform == "win32":
        try:
            for ip, mask in _windows_ipv4_table():
                add(ip, mask)
        except Exception:
            pass
    # Fallbacks (non-Windows, or if the API call failed). Netmask unknown
    # here, so assume /24, which matches typical home/club networks.
    candidates = []
    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.append(info[4][0])
    except OSError:
        pass
    candidates.append(_route_ip())
    for ip in candidates:
        add(ip, "255.255.255.0")
    return nets


def local_interfaces():
    return [ip for ip, _ in local_networks()]


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


def make_hello(
    node_id, host, ips, local_n1mm, color="", color_time=0.0,
    alert=False, alert_band="", alert_time=0.0,
):
    root = ET.Element("PingPongHello")
    for tag, value in (
        ("Id", node_id),
        ("Host", host),
        ("Version", __version__),
        ("Ips", ",".join(ips)),
        ("LocalN1MM", "true" if local_n1mm else "false"),
        ("Color", color or ""),
        ("ColorTime", "{:.3f}".format(color_time or 0.0)),
        ("Alert", "1" if alert else "0"),
        ("AlertBand", alert_band or ""),
        ("AlertTime", "{:.3f}".format(alert_time or 0.0)),
    ):
        ET.SubElement(root, tag).text = value
    return ET.tostring(root, encoding="utf-8")


def parse_hello(data):
    try:
        root = ET.fromstring(data.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return None
    if root.tag != "PingPongHello":
        return None

    def get(tag):
        return (root.findtext(tag) or "").strip()

    node_id = get("Id")
    if not node_id:
        return None
    return {
        "id": node_id,
        "host": get("Host"),
        "version": get("Version"),
        "ips": [ip.strip() for ip in get("Ips").split(",") if ip.strip()],
        "local_n1mm": get("LocalN1MM").lower() == "true",
        "color": get("Color"),
        "color_time": _float(get("ColorTime")),
        "alert": get("Alert") == "1",
        "alert_band": get("AlertBand"),
        "alert_time": _float(get("AlertTime")),
    }


def _float(text):
    try:
        return float(text)
    except ValueError:
        return 0.0


def _sender_socket(ip):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((ip, 0))
    return sock


def discovery_loop(
    port, node_id, get_state, on_networks, on_hello, stop, hello_now=None
):
    """Active discovery between PCs running PingPong.

    Every HELLO_INTERVAL seconds a small PingPongHello packet is broadcast
    on every active adapter (LAN, Wi-Fi, VPN) on the discovery port, which
    is separate from N1MM's 12060 so N1MM and other loggers never see it.
    Hellos from other PCs make them known even if their N1MM sends nothing;
    our own hello coming back tells us which of our addresses are live.
    """
    try:
        recv = open_socket("0.0.0.0", port)
    except OSError:
        recv = None
    host = socket.gethostname()
    senders = {}
    nets = []
    next_refresh = 0.0
    next_hello = 0.0
    while not stop.is_set():
        now = time.time()
        if now >= next_refresh:
            nets = local_networks()
            wanted = set(ip for ip, _ in nets)
            for ip in list(senders):
                if ip not in wanted:
                    try:
                        senders.pop(ip).close()
                    except OSError:
                        pass
            for ip in wanted:
                if ip not in senders:
                    try:
                        senders[ip] = _sender_socket(ip)
                    except OSError:
                        pass
            on_networks(nets)
            next_refresh = now + NETWORK_REFRESH
        if hello_now is not None and hello_now.is_set():
            hello_now.clear()  # e.g. F12 pressed: announce right away
            next_hello = 0.0
        if now >= next_hello:
            payload = make_hello(
                node_id, host, [ip for ip, _ in nets], **get_state()
            )
            for ip, bcast in nets:
                sock = senders.get(ip)
                if sock is None:
                    continue
                for target in (bcast, "255.255.255.255"):
                    try:
                        sock.sendto(payload, (target, port))
                    except OSError:
                        pass
            next_hello = now + HELLO_INTERVAL
        if recv is None:
            stop.wait(0.3)
            continue
        try:
            data, addr = recv.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            stop.wait(0.3)
            continue
        info = parse_hello(data)
        if info:
            on_hello(addr[0], info)
    for sock in list(senders.values()) + ([recv] if recv else []):
        try:
            sock.close()
        except OSError:
            pass


class Peer:
    def __init__(self, node_id):
        self.id = node_id
        self.host = ""
        self.version = ""
        self.ips = set()
        self.local_n1mm = False
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


class DisplayApp:
    def __init__(self, root, config_path, port, stale, discovery_port=None):
        self.root = root
        self.port = port
        self.stale = stale
        self.config_path = config_path
        self.hints = read_config(config_path)
        self.local_nets = set(local_interfaces())
        self.self_seen = set()
        self.local = set(self.local_nets)
        self.stations = {}
        self.peers = {}
        self.node_id = base64.b16encode(os.urandom(6)).decode("ascii")
        self.used_colors = set(color for _, color in self.hints.values() if color)
        self.tracked = None
        self.settings = upd.Settings(app_dir())
        self.var_check = tk.BooleanVar(
            value=self.settings.get("check_at_startup", True)
        )
        self.var_auto = tk.BooleanVar(
            value=self.settings.get("install_automatically", False)
        )
        # Test mode is deliberately not saved: it always starts off.
        self.var_test = tk.BooleanVar(value=False)
        self.var_keys = tk.BooleanVar(value=self.settings.get("f12_call", False))
        self.key_watcher = None
        self.alert_on = False
        self.alert_time = 0.0
        self.silenced = set()  # (peer id, call time) clicked away here
        self._shown_call = None
        self.hello_now = threading.Event()
        self.test_on = False  # plain copy, safe to read from the UDP thread
        self.test_log_path = os.path.join(app_dir(), TEST_LOG)
        self.my_color = self.settings.get_text("station", "color", "")
        self.my_color_time = _float(
            self.settings.get_text("station", "color_time", "0")
        )
        self._color_dialog = None
        self._ui_calls = []
        self._busy = False
        self._status = ""
        self._build()
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=listener_loop,
            args=("0.0.0.0", port, self.on_packet, self.stop),
            daemon=True,
        )
        self.thread.start()
        if discovery_port:
            self.discovery = threading.Thread(
                target=discovery_loop,
                args=(
                    discovery_port,
                    self.node_id,
                    self._hello_state,
                    self.on_networks,
                    self.on_hello,
                    self.stop,
                    self.hello_now,
                ),
                daemon=True,
            )
            self.discovery.start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(120, self._update)
        if self.var_keys.get() and keys.supported():
            self.root.after(500, self._keys_toggled)
        if upd.is_frozen():
            upd.cleanup_leftovers(upd.exe_path())
            if self.var_check.get():
                self.root.after(3000, lambda: self._check_updates(manual=False))

    # ---------------------------------------------------------- updates

    def _run_bg(self, work, done):
        """Run work() in a thread, then done(result, error) in the UI."""

        def runner():
            try:
                result, error = work(), None
            except Exception as exc:  # reported to the user in done()
                result, error = None, exc
            self._ui_calls.append(lambda: done(result, error))

        threading.Thread(target=runner, daemon=True).start()

    def _show_options(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(
            label="My color…  ({})".format(self.my_color or "automatic"),
            command=self._choose_color,
        )
        menu.add_checkbutton(
            label="Test mode – show my button presses",
            variable=self.var_test,
            command=self._test_toggled,
        )
        menu.add_checkbutton(
            label="F12 call – blink S&P stations on my band",
            variable=self.var_keys,
            command=self._keys_toggled,
        )
        menu.add_separator()
        frozen = upd.is_frozen()
        state = tk.NORMAL if frozen and not self._busy else tk.DISABLED
        menu.add_command(
            label="Check for updates",
            state=state,
            command=lambda: self._check_updates(manual=True),
        )
        menu.add_command(
            label="Switch version…", state=state, command=self._switch_version
        )
        backup = upd.find_backup(upd.exe_path()) if frozen else None
        menu.add_command(
            label="Revert to previous version"
            + (" (v{})".format(backup[0]) if backup else ""),
            state=state if backup else tk.DISABLED,
            command=self._revert,
        )
        menu.add_separator()
        menu.add_checkbutton(
            label="Check for updates at startup",
            variable=self.var_check,
            command=lambda: self.settings.set(
                "check_at_startup", self.var_check.get()
            ),
        )
        menu.add_checkbutton(
            label="Install updates automatically",
            variable=self.var_auto,
            command=lambda: self.settings.set(
                "install_automatically", self.var_auto.get()
            ),
        )
        menu.add_separator()
        menu.add_command(
            label="PingPong v{}{}".format(
                __version__, "" if frozen else "  (source - updates only in exe)"
            ),
            state=tk.DISABLED,
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _check_updates(self, manual):
        if self._busy:
            return
        self._busy = True
        if manual:
            self._status = "iščem posodobitve…"
        self._run_bg(
            upd.fetch_releases,
            lambda rels, err: self._on_releases(rels, err, manual),
        )

    def _on_releases(self, releases, error, manual):
        self._busy = False
        self._status = ""
        if error is not None:
            if manual:
                messagebox.showerror(
                    "PingPong",
                    "Could not check for updates:\n{}".format(error),
                    parent=self.root,
                )
            return
        new = upd.newer_release(releases, __version__)
        if new is None:
            if manual:
                messagebox.showinfo(
                    "PingPong",
                    "You have the latest version (v{}).".format(__version__),
                    parent=self.root,
                )
            return
        if not manual and self.var_auto.get():
            self._install(new)
            return
        if messagebox.askyesno(
            "PingPong update",
            "PingPong v{} is available (you have v{}).\n\n"
            "Update now? PingPong will restart.\n"
            "You can switch back any time under Options.".format(
                new["version"], __version__
            ),
            parent=self.root,
        ):
            self._install(new)

    def _old_version_note(self, version):
        """Warning when going to a version without this Options menu."""
        if upd.parse_version(version) >= (1, 6):
            return ""
        return (
            "\n\nNote: v{} has no Options menu. To come back, double-click\n"
            "{} in the PingPong folder\n"
            "or download the newest release from GitHub.".format(
                version,
                os.path.basename(upd.backup_path(upd.exe_path(), __version__)),
            )
        )

    def _install(self, release):
        if self._busy:
            return
        self._busy = True
        target = upd.exe_path()
        self._status = "prenašam v{}…".format(release["version"])

        def progress(done, total):
            if total:
                self._status = "prenašam v{}… {}%".format(
                    release["version"], int(done * 100 / total)
                )

        def work():
            new_file = upd.download(release, target, progress)
            upd.swap_in(target, __version__, new_file)

        def done(_, error):
            self._busy = False
            self._status = ""
            if error is not None:
                messagebox.showerror(
                    "PingPong",
                    "Update to v{} failed:\n{}\n\n"
                    "Your current version v{} is unchanged.".format(
                        release["version"], error, __version__
                    ),
                    parent=self.root,
                )
                return
            self._restart(target)

        self._run_bg(work, done)

    def _switch_version(self):
        if self._busy:
            return
        self._busy = True
        self._status = "iščem verzije…"

        def done(releases, error):
            self._busy = False
            self._status = ""
            if error is not None:
                messagebox.showerror(
                    "PingPong",
                    "Could not get the list of versions:\n{}".format(error),
                    parent=self.root,
                )
                return
            self._version_dialog(releases)

        self._run_bg(upd.fetch_releases, done)

    def _version_dialog(self, releases):
        dlg = tk.Toplevel(self.root)
        dlg.title("Switch version")
        dlg.attributes("-topmost", True)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        tk.Label(
            dlg, text="Installed: v{}".format(__version__), font=("Segoe UI", 9)
        ).pack(padx=10, pady=(8, 4), anchor=tk.W)
        box = tk.Listbox(dlg, height=min(10, max(3, len(releases))), width=34)
        for rel in releases:
            mark = "   (installed)" if rel["version"] == __version__ else ""
            box.insert(tk.END, "v{}   {}{}".format(rel["version"], rel["date"], mark))
        box.pack(padx=10)
        if releases:
            box.selection_set(0)

        def install():
            sel = box.curselection()
            if not sel:
                return
            rel = releases[sel[0]]
            if rel["version"] == __version__:
                messagebox.showinfo(
                    "PingPong", "v{} is already installed.".format(__version__),
                    parent=dlg,
                )
                return
            if not messagebox.askyesno(
                "PingPong",
                "Install v{} now? PingPong will restart.{}".format(
                    rel["version"], self._old_version_note(rel["version"])
                ),
                parent=dlg,
            ):
                return
            dlg.destroy()
            self._install(rel)

        buttons = tk.Frame(dlg)
        buttons.pack(pady=8)
        tk.Button(buttons, text="Install", width=10, command=install).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(buttons, text="Cancel", width=10, command=dlg.destroy).pack(
            side=tk.LEFT, padx=4
        )
        box.bind("<Double-Button-1>", lambda e: install())

    def _revert(self):
        target = upd.exe_path()
        backup = upd.find_backup(target)
        if not backup or self._busy:
            return
        if not messagebox.askyesno(
            "PingPong",
            "Switch back to v{}? PingPong will restart.\n"
            "(v{} is kept, so you can switch forward again.){}".format(
                backup[0], __version__, self._old_version_note(backup[0])
            ),
            parent=self.root,
        ):
            return
        try:
            upd.revert(target, __version__)
        except OSError as exc:
            messagebox.showerror(
                "PingPong", "Could not switch back:\n{}".format(exc), parent=self.root
            )
            return
        self._restart(target)

    def _restart(self, target):
        try:
            upd.relaunch(target, sys.argv[1:])
        except OSError as exc:
            messagebox.showerror(
                "PingPong",
                "Installed, but could not restart automatically:\n{}\n\n"
                "Please start {} again.".format(exc, os.path.basename(target)),
                parent=self.root,
            )
        self.on_close()

    # ---------------------------------------------------------- stations

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
        if self.test_on and src in self.local:
            self._test_log(src, data)
        station = self._station(src, station_name)
        station.count += 1
        if not station.label or station.label == station.ip:
            station.label = station_name or station.label
        if is_radio:
            running = radio_running(data)
            if running is not None:
                station.running = running
            station.tx = is_tx
            station.last_seen = time.time()
            if caption:
                if caption != station.msg:
                    station.msg_time = time.time()
                station.msg = caption
            if freq:
                station.freq = freq
            if mode:
                station.mode = mode
            station.key = group_of(station.freq, station.mode)
            if station.ip in self.local:
                self.tracked = station.key

    def on_networks(self, nets):
        self.local_nets = set(ip for ip, _ in nets)
        self.local = self.local_nets | self.self_seen

    def on_hello(self, src, info):
        if info["id"] == self.node_id:
            # Our own broadcast came back: src is certainly one of our IPs.
            if src not in self.local:
                self.self_seen.add(src)
                self.local = self.local_nets | self.self_seen
            return
        peer = self.peers.get(info["id"])
        if peer is None:
            peer = Peer(info["id"])
            self.peers[info["id"]] = peer
        peer.host = info["host"] or src
        peer.version = info["version"]
        peer.ips = set(info["ips"]) | {src}
        peer.local_n1mm = info["local_n1mm"]
        peer.color = info.get("color", "")
        peer.color_time = info.get("color_time", 0.0)
        peer.alert = info.get("alert", False)
        peer.alert_band = info.get("alert_band", "")
        peer.alert_time = info.get("alert_time", 0.0)
        peer.last_seen = time.time()

    def _hello_state(self):
        return {
            "local_n1mm": self._local_n1mm_active(),
            "color": self.my_color,
            "color_time": self.my_color_time,
            "alert": self.alert_on,
            "alert_band": self._my_band(),
            "alert_time": self.alert_time,
        }

    # ---------------------------------------------------------- colors

    def _fresh_peers(self, now):
        return [
            p for p in list(self.peers.values())
            if (now - p.last_seen) <= PEER_TIMEOUT
        ]

    def _color_owners(self, now):
        """{color: owner} for chosen colors; owner is "me" or a Peer.

        If two PCs chose the same color, the one that chose it first keeps
        it (ties: host name), so every lamp reaches the same decision.
        """
        claims = []
        if self.my_color:
            claims.append((self.my_color_time, socket.gethostname(), "me", self.my_color))
        for peer in self._fresh_peers(now):
            if peer.color:
                claims.append((peer.color_time, peer.host, peer, peer.color))
        owners = {}
        for _, _, owner, color in sorted(claims, key=lambda c: (c[0], c[1])):
            owners.setdefault(color.lower(), owner)
        return owners

    def _color_for(self, station, owners, now):
        if station.ip in self.local:
            if self.my_color and owners.get(self.my_color.lower()) == "me":
                return self.my_color
        else:
            for peer in self._fresh_peers(now):
                if station.ip in peer.ips and peer.color:
                    if owners.get(peer.color.lower()) is peer:
                        return peer.color
                    break
        # Automatic / lamps.cfg color - but never one another PC has chosen.
        if (station.color or "").lower() in owners:
            taken = set(owners) | set(
                (st.color or "").lower()
                for st in list(self.stations.values())
                if st is not station
            )
            free = [c for c in COLOR_PALETTE if c.lower() not in taken]
            if free:
                station.color = free[0]
        return station.color

    def _my_color_note(self, owners):
        if not self.my_color:
            return ""
        owner = owners.get(self.my_color.lower())
        if owner == "me" or owner is None:
            return ""
        return "barva {} je zasedena ({})".format(self.my_color, owner.host)

    def _choose_color(self):
        if self._color_dialog is not None:
            try:
                self._color_dialog.lift()
                return
            except tk.TclError:
                self._color_dialog = None
        now = time.time()
        owners = self._color_owners(now)
        dlg = tk.Toplevel(self.root)
        self._color_dialog = dlg
        dlg.title("My color")
        dlg.attributes("-topmost", True)
        dlg.transient(self.root)
        dlg.resizable(False, False)

        def closed():
            self._color_dialog = None
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", closed)
        tk.Label(
            dlg,
            text="Color of this PC's station on every PingPong lamp.\n"
            "Colors chosen by other PCs are not available.",
            font=("Segoe UI", 9),
            justify=tk.LEFT,
        ).pack(padx=10, pady=(8, 6), anchor=tk.W)
        grid = tk.Frame(dlg)
        grid.pack(padx=10)

        def pick(color):
            if color != self.my_color:
                self.my_color = color
                self.my_color_time = time.time() if color else 0.0
                self.settings.set_text("station", "color", color)
                self.settings.set_text(
                    "station", "color_time", "{:.3f}".format(self.my_color_time)
                )
            closed()

        for i, color in enumerate(COLOR_PALETTE):
            owner = owners.get(color.lower())
            taken = owner is not None and owner != "me"
            mine = color == self.my_color and not taken
            text = owner.host if taken else ("✓ " + color if mine else color)
            tk.Button(
                grid,
                text=text,
                width=12,
                bg=color,
                activebackground=color,
                relief=tk.SUNKEN if mine else tk.RAISED,
                state=tk.DISABLED if taken else tk.NORMAL,
                command=lambda c=color: pick(c),
            ).grid(row=i // 5, column=i % 5, padx=2, pady=2)
        buttons = tk.Frame(dlg)
        buttons.pack(pady=8)
        tk.Button(
            buttons, text="Automatic", width=10, command=lambda: pick("")
        ).pack(side=tk.LEFT, padx=4)
        tk.Button(buttons, text="Close", width=10, command=closed).pack(
            side=tk.LEFT, padx=4
        )

    def _local_n1mm_active(self):
        now = time.time()
        return any(
            st.ip in self.local and (now - st.last_seen) <= LOCAL_N1MM_RECENT
            for st in list(self.stations.values())
        )

    def _peer_rows(self, now):
        """Footer notes for PingPong PCs whose N1MM traffic we do not get."""
        station_ips = set(self.stations)
        rows = []
        for peer in list(self.peers.values()):
            if (now - peer.last_seen) > PEER_TIMEOUT:
                continue
            if peer.ips & station_ips:
                continue
            if peer.local_n1mm:
                # Its N1MM runs, but the broadcast does not reach this PC:
                # usually a wrong Broadcast Address in that PC's N1MM.
                rows.append("{}: N1MM ne pride sem".format(peer.host))
            else:
                rows.append("{}: ni N1MM".format(peer.host))
        return rows

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
        self.options_link = tk.Label(
            top, text="Options ▾", font=("Segoe UI", 8), cursor="hand2"
        )
        self.options_link.pack(side=tk.RIGHT, padx=(0, 6))
        self.options_link.bind("<Button-1>", self._show_options)
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
        self.canvas.bind("<Button-1>", self._silence_call)
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
        # Run finished background work as separate events, so a dialog it
        # opens never blocks this loop (the lamp keeps working meanwhile).
        while self._ui_calls:
            self.root.after(0, self._ui_calls.pop(0))
        now = time.time()
        owners = self._color_owners(now)
        active = None
        latest = -1.0
        for station in list(self.stations.values()):
            if (
                station.tx
                and (now - station.last_seen) <= self.stale
                and self._is_tracked(station)
                and station.last_seen > latest
            ):
                latest = station.last_seen
                active = station

        calls = self._incoming_calls(now) if active is None else []
        test = None
        if active is None and not calls and self.var_test.get():
            test = self._local_station()
        self._show_my_call(owners, now)

        if not calls:
            self._shown_call = None
        if calls:
            self._show_call(calls[-1], owners, now)
        elif test is not None:
            self._show_test(test, owners, now)
        elif active is not None:
            color = self._color_for(active, owners, now) or "red"
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
            if self.alert_on:
                header = "F12 klic VKLOP ({} MHz) – F12 = izklop  ·  ".format(
                    self._my_band() or "?"
                ) + header
            note = self._my_color_note(owners)
            if note:
                header = note + "  ·  " + header
            if self._status:
                header = self._status + "  ·  " + header
            parts = [
                "{}: {}".format(st.label, st.count)
                for st in list(self.stations.values())
            ]
            parts.extend(self._peer_rows(now))
            rows = "  |  ".join(parts) if parts else "ni podatkov"
            self.info.configure(text=header + rows)

        self.root.after(120, self._update)

    def _local_station(self):
        """This PC's own N1MM station (most recently heard), or None."""
        mine = [st for st in list(self.stations.values()) if st.ip in self.local]
        if not mine:
            return None
        return max(mine, key=lambda st: st.last_seen)

    def _dim(self, color, amount=0.35):
        """color blended towards the idle grey (amount = share of color)."""
        try:
            r, g, b = (v // 256 for v in self.root.winfo_rgb(color))
            ir, ig, ib = (v // 256 for v in self.root.winfo_rgb(COLOR_IDLE))
        except tk.TclError:
            return COLOR_IDLE
        mix = lambda c, i: int(i + (c - i) * amount)
        return "#{:02x}{:02x}{:02x}".format(mix(r, ir), mix(g, ig), mix(b, ib))

    def _test_toggled(self):
        self.test_on = bool(self.var_test.get())
        self._test_write(
            "=== test mode {} · PingPong v{} · local IPs {} ===".format(
                "ON" if self.test_on else "OFF",
                __version__,
                ", ".join(sorted(self.local)) or "?",
            )
        )

    def _test_write(self, line):
        try:
            with open(self.test_log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def _test_log(self, src, data):
        """Test mode: one line per packet from this PC's N1MM."""
        raw = data.decode("utf-8", errors="replace")
        tag = re.search(r"<(?![?!])([A-Za-z]\w*)", raw)

        def field(name):
            m = re.search(r"<{0}>(.*?)</{0}>".format(name), raw, re.S)
            return m.group(1).strip() if m else "-"

        stamp = time.strftime("%H:%M:%S") + ".{:03d}".format(int(time.time() * 1000) % 1000)
        kind = tag.group(1) if tag else "?"
        if kind == "RadioInfo":
            self._test_write(
                "{}  {}  RadioInfo  key='{}'  tx={}  freq={}  mode={}".format(
                    stamp, src, field("FunctionKeyCaption"),
                    field("IsTransmitting"), field("Freq"), field("Mode"),
                )
            )
        else:
            self._test_write("{}  {}  {}  ({} bytes)".format(stamp, src, kind, len(data)))

    def _show_test(self, station, owners, now):
        """Test mode: show this PC's last button even without transmitting.

        Bright for TEST_FLASH seconds after the caption changes (or while
        N1MM reports transmitting), dimmed otherwise.
        """
        color = self._color_for(station, owners, now) or "red"
        fresh = (now - station.msg_time) <= TEST_FLASH
        tx = station.tx and (now - station.last_seen) <= self.stale
        bg = color if (fresh or tx) else self._dim(color)
        shown = station.msg or "—"
        if self.canvas.cget("bg") != bg:
            self.canvas.configure(bg=bg)
        if self.canvas.itemcget(self.text_id, "text") != shown:
            self.canvas.itemconfigure(self.text_id, text=shown)
            self.canvas.itemconfigure(self.text_id, font=self._font_for(shown))
        self.info.configure(
            text="TEST  ·  {}  ·  {}  ·  {}".format(
                station.label,
                "oddaja" if tx else "ne oddaja",
                group_label(station.key) or "ni pasu/načina",
            )
        )

    # ---------------------------------------------------------- F12 call

    def _start_keys(self):
        if self.key_watcher is not None:
            return True
        watcher = keys.KeyWatcher(self._f12_pressed)
        if not watcher.start():
            messagebox.showerror(
                "PingPong",
                "F12 call is not available:\n{}".format(watcher.error),
                parent=self.root,
            )
            return False
        self.key_watcher = watcher
        return True

    def _keys_toggled(self):
        on = bool(self.var_keys.get())
        if on and not self._start_keys():
            self.var_keys.set(False)
            on = False
        if not on:
            if self.key_watcher is not None:
                self.key_watcher.stop()
                self.key_watcher = None
            if self.alert_on:
                self.alert_on = False
                self.alert_time = time.time()
                self.hello_now.set()
        self.settings.set("f12_call", on)

    def _f12_pressed(self):
        """Keyboard thread: F12 in N1MM toggles this PC's call."""
        self.alert_on = not self.alert_on
        self.alert_time = time.time()
        self.hello_now.set()
        if self.test_on:
            self._test_write(
                "{}  F12 key  call {}".format(
                    time.strftime("%H:%M:%S"), "ON" if self.alert_on else "OFF"
                )
            )

    def _my_band(self):
        mine = self._local_station()
        return mine.key[0] if mine is not None and mine.key else ""

    def _incoming_calls(self, now):
        """Peers calling (F12) on this PC's band while this PC is in S&P."""
        mine = self._local_station()
        if mine is None or not mine.key or mine.running is True:
            return []
        band = mine.key[0]
        calls = [
            p for p in self._fresh_peers(now)
            if p.alert
            and p.alert_band == band
            and not (p.ips & self.local)  # never "call" ourselves
            and (p.id, p.alert_time) not in self.silenced
        ]
        return sorted(calls, key=lambda p: p.alert_time)

    def _peer_station(self, peer):
        for st in list(self.stations.values()):
            if st.ip in peer.ips:
                return st
        return None

    def _silence_call(self, event=None):
        """Click on a blinking lamp: stop blinking for this call here."""
        peer = self._shown_call
        if peer is not None:
            self.silenced.add((peer.id, peer.alert_time))
            self._shown_call = None

    def _show_call(self, peer, owners, now):
        self._shown_call = peer
        station = self._peer_station(peer)
        if peer.color and owners.get(peer.color.lower()) is peer:
            color = peer.color
        elif station is not None:
            color = self._color_for(station, owners, now) or "red"
        else:
            color = "red"
        name = station.label if station is not None else peer.host
        lit = int(now / BLINK_HALF) % 2 == 0
        bg = color if lit else COLOR_IDLE
        shown = "F12 · {}".format(name)
        if self.canvas.cget("bg") != bg:
            self.canvas.configure(bg=bg)
        if self.canvas.itemcget(self.text_id, "text") != shown:
            self.canvas.itemconfigure(self.text_id, text=shown)
            self.canvas.itemconfigure(self.text_id, font=self._font_for(shown))
        self.info.configure(
            text="{} kliče (F12)  ·  {} MHz  ·  klik = utišaj".format(
                peer.host, peer.alert_band
            )
        )

    def _show_my_call(self, owners, now):
        """Own feedback while my call is on: thick border in my color."""
        if self.alert_on:
            mine = self._local_station()
            color = (self._color_for(mine, owners, now) if mine else "") or "red"
            want = (color, "4")
        else:
            want = ("#888888", "1")
        if (
            self.canvas.cget("highlightbackground") != want[0]
            or str(self.canvas.cget("highlightthickness")) != want[1]
        ):
            self.canvas.configure(
                highlightbackground=want[0], highlightthickness=int(want[1])
            )

    def on_close(self):
        if self.key_watcher is not None:
            self.key_watcher.stop()
        self.stop.set()
        self.root.destroy()


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


_instance_mutex = None


def claim_single_instance(wait):
    """Only one lamp per PC. Waits up to `wait` s for an old one to exit
    (after an update the old lamp closes a moment after starting us)."""
    global _instance_mutex
    if sys.platform != "win32":
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    deadline = time.time() + wait
    while True:
        handle = kernel32.CreateMutexW(None, False, "Local\\PingPongLamp")
        if handle and ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
            _instance_mutex = handle  # held until this process exits
            return True
        if handle:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
        if time.time() >= deadline:
            return False
        time.sleep(0.25)


def main():
    parser = argparse.ArgumentParser(description="N1MM UDP 12060 display")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--stale", type=float, default=DEFAULT_STALE)
    parser.add_argument("--config", default=os.path.join(app_dir(), "lamps.cfg"))
    parser.add_argument(
        "--discovery-port",
        type=int,
        default=DEFAULT_DISCOVERY_PORT,
        help="UDP port for PingPong-to-PingPong discovery (0 = off)",
    )
    parser.add_argument(
        "--no-discovery", action="store_true", help="disable active discovery"
    )
    args = parser.parse_args()

    after_update = os.environ.pop("PINGPONG_AFTER_UPDATE", "") == "1"
    if not claim_single_instance(15.0 if after_update else 3.0):
        hidden = tk.Tk()
        hidden.withdraw()
        messagebox.showinfo(
            "PingPong",
            "PingPong is already running on this PC.\n\n"
            "Only one lamp per PC is allowed - use the window that is open.",
            parent=hidden,
        )
        hidden.destroy()
        return

    root = tk.Tk()
    discovery_port = 0 if args.no_discovery else args.discovery_port
    DisplayApp(root, args.config, args.port, args.stale, discovery_port)
    root.mainloop()


if __name__ == "__main__":
    main()