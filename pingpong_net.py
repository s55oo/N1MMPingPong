"""PingPong - networking: lamp-to-lamp messages and the UDP loops.

- PingPongHello (every 3 s), relay of each lamp's own N1MM RadioInfo
  (PPR1) and commands from PingPong Overview (PPC1) on UDP 46070;
- the N1MM listener (UDP 12062, N1MM's Broadcast Data → Radio line must include it);
- this PC's network adapters and broadcast addresses (Windows API);
- which other programs listen on N1MM's port.
"""

import os
import socket
import struct
import sys
import time
import xml.etree.ElementTree as ET

from pingpong_common import *  # noqa: F401,F403
from pingpong_common import __version__, _float, guarded, log_error

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

def _windows_adapters():
    """[(ip, adapter description)] of every IPv4 address (Windows), e.g.
    ("10.147.17.3", "ZeroTier Virtual Port")."""
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

    class MIB_IFROW(ctypes.Structure):
        _fields_ = [
            ("wszName", ctypes.c_wchar * 256),
            ("dwIndex", wintypes.DWORD),
            ("dwType", wintypes.DWORD),
            ("dwMtu", wintypes.DWORD),
            ("dwSpeed", wintypes.DWORD),
            ("dwPhysAddrLen", wintypes.DWORD),
            ("bPhysAddr", ctypes.c_ubyte * 8),
            ("dwAdminStatus", wintypes.DWORD),
            ("dwOperStatus", wintypes.DWORD),
            ("dwLastChange", wintypes.DWORD),
            ("dwInOctets", wintypes.DWORD),
            ("dwInUcastPkts", wintypes.DWORD),
            ("dwInNUcastPkts", wintypes.DWORD),
            ("dwInDiscards", wintypes.DWORD),
            ("dwInErrors", wintypes.DWORD),
            ("dwInUnknownProtos", wintypes.DWORD),
            ("dwOutOctets", wintypes.DWORD),
            ("dwOutUcastPkts", wintypes.DWORD),
            ("dwOutNUcastPkts", wintypes.DWORD),
            ("dwOutDiscards", wintypes.DWORD),
            ("dwOutErrors", wintypes.DWORD),
            ("dwOutQLen", wintypes.DWORD),
            ("dwDescrLen", wintypes.DWORD),
            ("bDescr", ctypes.c_ubyte * 256),
        ]

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
        if row.wType & (0x0008 | 0x0040):  # disconnected / deleted
            continue
        ip = socket.inet_ntoa(struct.pack("<I", row.dwAddr))
        if not _usable_ip(ip):
            continue
        descr = ""
        ifrow = MIB_IFROW()
        ifrow.dwIndex = row.dwIndex
        if iphlpapi.GetIfEntry(ctypes.byref(ifrow)) == 0:
            n = min(ifrow.dwDescrLen, 256)
            descr = bytes(ifrow.bDescr[:n]).split(b"\0")[0].decode("latin-1", "replace")
        result.append((ip, descr))
    return result


def firewall_rule_exists(name):
    """Is there a Windows Firewall rule with this name? True / False;
    None when it can't be checked (not Windows, netsh failed)."""
    if sys.platform != "win32":
        return None
    import subprocess

    try:
        done = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", "name={}".format(name)],
            capture_output=True, timeout=15,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except Exception:
        return None
    return done.returncode == 0


def phone_addresses():
    """{"lan": ip, "zt": ip} - where a phone can reach this PC (the
    ZeroTier one only when this PC is on ZeroTier)."""
    from pingpong_phone import pick_addresses

    adapters = []
    if sys.platform == "win32":
        try:
            adapters = _windows_adapters()
        except Exception:
            adapters = []
    if not adapters:
        adapters = [(ip, "") for ip in local_interfaces()]
    return pick_addresses(adapters, _route_ip())


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
        guarded("N1MM packet", on_packet)(addr[0], data)
    try:
        sock.close()
    except OSError:
        pass

def make_hello(
    node_id, host, ips, local_n1mm, color="", color_time=0.0,
    alert=False, alert_band="", alert_time=0.0, status="", alias="", role="",
    n1mm_master="", started=0.0, colors="", block_time=0.0,
    alert_run=False, block_time_run=0.0, pingpong="", problems="", pp_limit="",
    urgent=False, urgent_band="", urgent_mode="", urgent_time=0.0, urgent_priority=False,
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
        ("Status", status or ""),
        ("Alias", alias or ""),
        ("Role", role or ""),
        ("N1MMMaster", n1mm_master or ""),
        ("Started", "{:.3f}".format(started or 0.0)),
        ("Colors", colors or ""),
        ("BlockTime", "{:g}".format(block_time) if block_time else ""),
        ("AlertRun", "1" if alert_run else "0"),
        ("BlockTimeRun", "{:g}".format(block_time_run) if block_time_run else ""),
        ("PingPong", pingpong or ""),  # master only: ping-pong lock "1" on / "0" off
        ("Problems", problems or ""),
        ("PPLimit", pp_limit or ""),  # master only: ping-pong release time, s (0 = RUN only)  # what is wrong on this PC, "|"-separated
        ("Urgent", "1" if urgent else "0"),
        ("UrgentBand", urgent_band or ""),
        ("UrgentMode", urgent_mode or ""),
        ("UrgentTime", "{:.3f}".format(urgent_time or 0.0)),
        ("UrgentPriority", "1" if urgent_priority else "0"),
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
        "status": get("Status"),
        "alias": get("Alias"),
        "role": get("Role"),
        "n1mm_master": get("N1MMMaster"),
        "started": _float(get("Started")),
        "colors": parse_colors(get("Colors")),
        "block_time": _float(get("BlockTime")),
        "alert_run": get("AlertRun") == "1",
        "block_time_run": _float(get("BlockTimeRun")),
        "pingpong": get("PingPong"),
        "pp_limit": get("PPLimit"),
        "problems": [p for p in get("Problems").split("|") if p],
        "urgent": get("Urgent") == "1",
        "urgent_band": get("UrgentBand"),
        "urgent_mode": get("UrgentMode"),
        "urgent_time": _float(get("UrgentTime")),
        "urgent_priority": get("UrgentPriority") == "1",
    }

def format_colors(mapping):
    """{'pc:oo840g4': 'red', ...} -> 'pc:oo840g4=red;...' for the hello."""
    return ";".join("{}={}".format(k, v) for k, v in sorted(mapping.items()))

def parse_colors(text):
    result = {}
    for part in (text or "").split(";"):
        key, sep, value = part.partition("=")
        if sep and key.strip() and value.strip():
            result[key.strip()] = value.strip()
    return result

def _sender_socket(ip):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((ip, 0))
    return sock


# ----- network password (optional): signed messages on the lamps' port -----
#
# With a password set on every PC, each message on UDP 46070 (hello, relay,
# command, ping answer) ends in  "\n#PPS1 <unix time> <HMAC-SHA256>"  over the
# time and the message. Messages without a valid signature, or more than
# AUTH_WINDOW seconds old (replay), are dropped. Without a password nothing
# is added, so lamps that have none work exactly as before.

AUTH_MARK = b"\n#PPS1 "
AUTH_WINDOW = 300.0  # s: accepted age of a signed message (PC clocks must agree)
AUTH = {"key": b""}  # derived key; b"" = no password
AUTH_SEEN = {}  # (status, src ip) -> last time; for the warnings on the lamp / Overview
AUTH_SAMPLES = []  # [(time, src, signed message)] we could not check: to test a typed password
AUTH_NAMES = {}  # src ip -> name from a rejected hello (shown in warnings only)
PASSWORD_NEEDED_TEXT = "🔒 this PingPong network has a password – click here to enter it"


def derive_key(password):
    """Key from the password (only this is stored, never the password)."""
    import hashlib

    if not password:
        return b""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"PingPong-N1MM-46070", 20000)


def set_network_key(key):
    AUTH["key"] = key or b""


def _mac(key, ts, payload):
    import hashlib
    import hmac

    return hmac.new(key, ts + b" " + payload, hashlib.sha256).hexdigest()[:32].encode("ascii")


def seal(payload, now=None, key=None):
    """Add the signature (only when a password is set)."""
    key = AUTH["key"] if key is None else key
    if not key:
        return payload
    ts = str(int(now if now is not None else time.time())).encode("ascii")
    return payload + AUTH_MARK + ts + b" " + _mac(key, ts, payload)


def unseal(data, src="", now=None, key=None):
    """(payload, status). status: "ok" (valid signature), "none" (unsigned),
    "bad" (wrong password), "old" (time outside AUTH_WINDOW), "foreign"
    (signed, but this PC has no password). The caller accepts the payload
    only if accepted(status)."""
    key = AUTH["key"] if key is None else key
    now = time.time() if now is None else now
    i = data.rfind(AUTH_MARK)
    if i < 0:
        status, payload = ("none", data)
    else:
        payload = data[:i]
        parts = data[i + len(AUTH_MARK):].strip().split(b" ")
        if not key:
            status = "foreign"
        elif len(parts) != 2:
            status = "bad"
        else:
            import hmac

            ts, mac = parts
            if not hmac.compare_digest(mac, _mac(key, ts, payload)):
                status = "bad"
            else:
                try:
                    age = abs(now - int(ts))
                except ValueError:
                    age = AUTH_WINDOW + 1
                status = "ok" if age <= AUTH_WINDOW else "old"
    if src and status not in ("ok", "none"):
        AUTH_SEEN[(status, src)] = now
    elif src and status == "none" and key:
        AUTH_SEEN[("none", src)] = now
    elif src and status == "ok":
        AUTH_SEEN[("ok", src)] = now  # to tell "password removed" from "one stray PC"
    if src and status not in ("ok",) and not (status == "none" and not key):
        _remember(src, payload, data if status in ("foreign", "bad") else None, now)
    return payload, status


def _remember(src, payload, signed, now):
    """Keep a name for the warnings and a sample to check a typed password."""
    import re

    m = re.search(rb"<Alias>([^<]{1,40})</Alias>", payload) or \
        re.search(rb"<Host>([^<]{1,40})</Host>", payload)
    if m:
        AUTH_NAMES[src] = m.group(1).decode("utf-8", "replace")
    if signed is not None:
        AUTH_SAMPLES.append((now, src, signed))
        del AUTH_SAMPLES[:-20]


def password_matches(password, now=None, within=600.0):
    """Does this password sign the protected messages heard lately?
    None = no recent signed message to check against."""
    now = time.time() if now is None else now
    recent = [data for when, _src, data in AUTH_SAMPLES if now - when <= within]
    if not recent:
        return None
    key = derive_key(password)
    return any(unseal(data, key=key, now=now)[1] in ("ok", "old") for data in recent)


def password_removed(now, local=(), within=30.0):
    """(True, who) when this PC has a password but the others have dropped
    it: unsigned messages arrive and no signed one from another PC."""
    if not AUTH["key"]:
        return False, ""
    recent = {(st, src) for (st, src), when in AUTH_SEEN.items()
              if now - when <= within and src not in local}
    if any(st == "ok" for st, _ in recent):
        return False, ""  # someone still signs: not removed
    srcs = sorted({src for st, src in recent if st == "none"})
    if not srcs:
        return False, ""
    return True, ", ".join(sorted({AUTH_NAMES.get(s, s) for s in srcs}))


def password_needed(now, within=30.0):
    """(True, who) when lamps nearby use a password this PC doesn't have
    (none set, or a different one); who = the name heard from them."""
    status = "bad" if AUTH["key"] else "foreign"
    srcs = [src for (st, src), when in AUTH_SEEN.items() if st == status and now - when <= within]
    if not srcs:
        return False, ""
    return True, ", ".join(sorted({AUTH_NAMES.get(s, s) for s in srcs}))


def accepted(status):
    """With a password only valid signatures count; without one, anything
    (a signed message from a protected group is then read but unchecked)."""
    if AUTH["key"]:
        return status == "ok"
    return status in ("none", "foreign")


def auth_problems(now, local=(), within=30.0, where="Options ▾ → Network password…"):
    """Short texts about rejected messages in the last `within` seconds."""
    out = []
    labels = {
        "bad": "different network password",
        "none": "no network password",
        "old": "wrong time (check the PC clock)",
    }
    for (status, src), when in sorted(AUTH_SEEN.items()):
        if now - when > within or src in local:
            continue
        if status == "foreign":
            continue
        if status == "none" and password_removed(now, local, within)[0]:
            continue  # shown as "password removed" instead
        if status in labels and AUTH["key"]:
            name = AUTH_NAMES.get(src)
            out.append("{}: {} – ignored".format(
                "{} ({})".format(name, src) if name else src, labels[status]))
    if not AUTH["key"] and any(st == "foreign" and now - w <= within
                               for (st, _), w in AUTH_SEEN.items()):
        out.append("other lamps use a network password – set it: " + where)
    return out

PONG_MAGIC = b"PPP1"  # lamp -> Overview: answer to a ping (round-trip time)


def make_pong(seq, node_id, host):
    import json

    return PONG_MAGIC + json.dumps({"seq": seq, "id": node_id, "host": host}).encode("utf-8")


def parse_pong(data):
    """dict of a pong message, or None."""
    if not data.startswith(PONG_MAGIC):
        return None
    import json

    try:
        body = json.loads(data[len(PONG_MAGIC):].decode("utf-8"))
    except ValueError:
        return None
    return body if isinstance(body, dict) and "seq" in body and body.get("id") else None


RELAY_MAGIC = b"PPR1"  # lamp-to-lamp copy of this PC's N1MM RadioInfo

DIRECT_RECENT = 15.0  # s: prefer N1MM's own broadcast over a relayed copy

def make_relay(node_id, data):
    return RELAY_MAGIC + node_id.encode("ascii") + b"\n" + data

COMMAND_MAGIC = b"PPC1"  # command from PingPong Overview on the master PC

def make_command(cmd, to="*", **args):
    """Overview -> lamps: e.g. make_command("update")."""
    import json
    import uuid

    body = {"id": uuid.uuid4().hex, "cmd": cmd, "to": to}
    body.update(args)
    return COMMAND_MAGIC + json.dumps(body).encode("utf-8")

def parse_command(data):
    """dict of a command message, or None."""
    if not data.startswith(COMMAND_MAGIC):
        return None
    import json

    try:
        body = json.loads(data[len(COMMAND_MAGIC):].decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(body, dict) or not body.get("id") or not body.get("cmd"):
        return None
    return body

def parse_relay(data):
    """(node_id, N1MM packet) of a relay message, or None."""
    if not data.startswith(RELAY_MAGIC):
        return None
    head, sep, payload = data[len(RELAY_MAGIC):].partition(b"\n")
    if not sep or not head:
        return None
    return head.decode("ascii", errors="replace"), payload

def send_relay(share, payload):
    """Broadcast a relay message on every network (any thread)."""
    for sock, bcast in list(share.get("targets", ())):
        try:
            sock.sendto(seal(payload), (bcast, share["port"]))
        except OSError:
            pass

def discovery_loop(
    port, node_id, get_state, on_networks, on_hello, stop, hello_now=None,
    share=None, on_relay=None, on_command=None,
):
    """Active discovery between PCs running PingPong.

    Every HELLO_INTERVAL seconds a small PingPongHello packet is broadcast
    on every active adapter (LAN, Wi-Fi, VPN) on the discovery port, which
    is separate from N1MM's ports so N1MM and other loggers never see it.
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
        try:
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
                if share is not None:
                    share["port"] = port
                    share["targets"] = [
                        (senders[ip], bcast) for ip, bcast in nets if ip in senders
                    ]
                next_refresh = now + NETWORK_REFRESH
            if hello_now is not None and hello_now.is_set():
                hello_now.clear()  # e.g. F12 pressed: announce right away
                next_hello = 0.0
            if now >= next_hello:
                payload = seal(make_hello(
                    node_id, host, [ip for ip, _ in nets], **get_state()
                ))
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
        except Exception:
            # Never let one error stop all lamp-to-lamp messages.
            log_error("discovery send")
            next_hello = time.time() + HELLO_INTERVAL
            stop.wait(1.0)
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
        data, status = unseal(data, addr[0])
        if not accepted(status):
            continue
        command = parse_command(data)
        if command is not None:
            if on_command is not None:
                command["_port"] = addr[1]  # where to answer a ping
                guarded("command", on_command)(addr[0], command)
            continue
        relay = parse_relay(data)
        if relay is not None:
            if on_relay is not None:
                guarded("relay", on_relay)(addr[0], relay[0], relay[1])
            continue
        info = parse_hello(data)
        if info:
            guarded("hello", on_hello)(addr[0], info)
    for sock in list(senders.values()) + ([recv] if recv else []):
        try:
            sock.close()
        except OSError:
            pass

PORT_CHECK = 30.0  # s: how often to look for other programs on N1MM's port

def port_users(port):
    """Names of other programs (not this one) listening on UDP port.

    Windows only (GetExtendedUdpTable); [] elsewhere or on any error.
    Two programs on N1MM's port means Windows gives each packet sent to
    127.0.0.1 to only one of them - the lamp then misses N1MM data.
    """
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    iphlp = ctypes.WinDLL("iphlpapi")
    size = wintypes.DWORD(0)
    AF_INET, UDP_TABLE_OWNER_PID = 2, 1
    iphlp.GetExtendedUdpTable(None, ctypes.byref(size), False, AF_INET, UDP_TABLE_OWNER_PID, 0)
    buf = ctypes.create_string_buffer(size.value + 1024)
    size = wintypes.DWORD(len(buf))
    if iphlp.GetExtendedUdpTable(buf, ctypes.byref(size), False, AF_INET, UDP_TABLE_OWNER_PID, 0):
        return []
    count = struct.unpack_from("<I", buf, 0)[0]
    me = os.getpid()
    pids = set()
    for i in range(count):
        _addr, rport, pid = struct.unpack_from("<III", buf, 4 + 12 * i)
        if socket.ntohs(rport & 0xFFFF) == port and pid not in (me, 0):
            pids.add(pid)
    names = []
    for pid in sorted(pids):
        names.append(_process_name(pid) or "PID {}".format(pid))
    # PyInstaller onefile: our own launcher process has the same exe name
    # but owns no socket, so it never shows up here.
    return sorted(set(names))

def _process_name(pid):
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
            return ""
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return ""

def port_watch_loop(port, out, stop):
    """Background: out[port] = other programs on the port, every 30 s."""
    stop.wait(3.0)
    while not stop.is_set():
        try:
            out[port] = port_users(port)
        except Exception:
            out[port] = []
        stop.wait(PORT_CHECK)
