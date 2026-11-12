"""PingPong - the lamp's rules, without any window.

RulesMixin holds everything the lamp decides from what it hears:
stations and other lamps, the master, colors, F12 calls (S&P / Run),
urgent calls (Shift+F2/F4), the ping-pong slot lock, which keys to
block, notes and commands from PingPong Overview. DisplayApp in
n1mm_lamps.py adds the window, menus and updates on top of it.

Shared state is guarded by self.lock (see DisplayApp.__init__): the UDP
listener, the discovery thread, the keyboard hook and the window all
touch the station and lamp lists.
"""

import socket
import threading
import time

import pingpong_keys as keys
import pingpong_update as upd
from pingpong_common import *  # noqa: F401,F403
from pingpong_common import __version__, _float
from pingpong_net import *  # noqa: F401,F403


class RulesMixin:
    def _my_role_now(self):
        """This PC's ping-pong role right now, always from its N1MM:
        Run = RUN, S&P = INB, not known (yet) = off."""
        mine = self._local_station()
        if mine is None:
            return "off"
        return {True: "RUN", False: "INB"}.get(mine.running, "off")

    def _pong(self, src, command):
        port = command.get("_port")
        if not port:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(seal(make_pong(command.get("seq", 0), self.node_id, socket.gethostname())),
                        (src, port))
        except OSError:
            pass
        finally:
            sock.close()

    @locked
    def on_command(self, src, command):
        """Discovery thread: a command from PingPong Overview.

        Accepted only from the master PC (the Overview runs there), never
        twice (it arrives on every network), only if meant for this lamp.
        "pingpong" (lock on/off) is applied by the master lamp, which then
        passes the setting on to every lamp.
        """
        seen = self.__dict__.setdefault("_commands_seen", [])
        if command["id"] in seen:
            return
        seen.append(command["id"])
        del seen[:-50]
        if command.get("to") not in ("*", self.node_id):
            return
        master = self._master_peer
        from_master = (
            (self._i_am_master and src in self.local)
            or (master is not None and src in master.ips)
        )
        if not from_master:
            self._key_event("command '{}' from {} ignored - not the master PC".format(
                command["cmd"], src))
            return
        if command["cmd"] == "ping":
            # Answer at once from this (network) thread: the Overview
            # measures the round-trip time of the lamps' own protocol.
            self._pong(src, command)
            return
        self._key_event("command '{}' from the master PC".format(command["cmd"]))
        if command["cmd"] == "update":
            self._ui_calls.append(self._remote_update)
        elif command["cmd"] == "test":  # "Test all lamps" in the Overview
            self._ui_calls.append(self._lamp_test)
        elif command["cmd"] == "restart":  # emergency, from the Overview
            self._ui_calls.append(self._remote_restart)
        elif command["cmd"] == "stop":  # emergency, from the Overview
            self._ui_calls.append(self._remote_stop)
        elif command["cmd"] == "setup":  # "Restore setup…" in the Overview
            pcs, master = command.get("pcs"), command.get("master")
            self._ui_calls.append(lambda: self._apply_setup(pcs, master))
        elif command["cmd"] == "pingpong" and command.get("value") in ("1", "0"):
            if self._i_am_master:
                on = command["value"] == "1"
                self._ui_calls.append(lambda: self._set_pingpong(on))

    def _apply_setup(self, pcs, master):
        """Restore a saved station setup: this PC's name and color (found by
        its Windows computer name); on the master also the F12 times and the
        ping-pong lock and release time."""
        changed = []
        entry = (pcs or {}).get(socket.gethostname().lower()) if isinstance(pcs, dict) else None
        if isinstance(entry, dict):
            alias = " ".join(str(entry.get("alias", self.my_alias) or "").split())[:MAX_ALIAS]
            if alias != self.my_alias:
                self.my_alias = alias
                self.settings.set_text("station", "alias", alias)
                changed.append("name " + (alias or "(PC name)"))
            color = str(entry.get("color", self.my_color) or "")
            if color and color not in COLOR_PALETTE:
                color = self.my_color
            if color != self.my_color:
                self.my_color = color
                self.my_color_time = time.time() if color else 0.0
                self.settings.set_text("station", "color", color)
                self.settings.set_text("station", "color_time", "{:.3f}".format(self.my_color_time))
                changed.append("color " + (color or "automatic"))
        if self._i_am_master and isinstance(master, dict):
            if master.get("block_time"):
                self.block_limit = clamp_block(_float(str(master["block_time"])))
                self.settings.set_text("network", "block_time", "{:g}".format(self.block_limit))
            if master.get("block_time_run"):
                self.block_limit_run = clamp_block(_float(str(master["block_time_run"])), BLOCK_LIMIT_RUN)
                self.settings.set_text("network", "block_time_run", "{:g}".format(self.block_limit_run))
            if master.get("pingpong") in ("1", "0"):
                self.pp_enabled = master["pingpong"] == "1"
                self.settings.set_text("network", "pingpong", "yes" if self.pp_enabled else "no")
            if master.get("pp_limit", "") != "":
                self.pp_limit = clamp_pp(master["pp_limit"])
                self.settings.set_text("network", "pp_limit", "{:g}".format(self.pp_limit))
            changed.append("master settings")
        self._key_event("setup restored: {}".format(", ".join(changed) or "nothing for this PC"))
        self.hello_now.set()

    @locked
    def on_relay(self, src, node_id, data):
        """Discovery thread: another lamp passed on its own N1MM's packet."""
        if node_id == self.node_id:
            return  # our own relay came back
        peer = self.peers.get(node_id)
        if peer is not None and getattr(peer, "same_pc", False):
            return
        if src in self.local:
            return
        ip = self._canon_ip(src)
        station = self.stations.get(ip)
        if station is not None and time.time() - station.direct_seen < DIRECT_RECENT:
            return  # N1MM's own broadcast reaches us: use that
        self.on_packet(ip, data, relayed=True)

    def _canon_ip(self, src):
        """One station per PC: map any address of a PingPong PC to the
        address its station is already known under."""
        if src in self.local:
            return src
        for peer in self._fresh_peers(time.time()):
            if src in peer.ips:
                for ip in sorted(peer.ips - self.local):
                    if ip in self.stations:
                        return ip
        return src

    @locked
    def on_networks(self, nets):
        self.local_nets = set(ip for ip, _ in nets)
        self.local = self.local_nets | self.self_seen | LOOPBACK

    @locked
    def on_hello(self, src, info):
        if info["id"] == self.node_id:
            # Our own broadcast came back: src is certainly one of our IPs.
            if src not in self.local:
                self.self_seen.add(src)
                self.local = self.local_nets | self.self_seen | LOOPBACK
            return
        peer = self.peers.get(info["id"])
        if peer is None:
            peer = Peer(info["id"])
            self.peers[info["id"]] = peer
        peer.host = info["host"] or src
        peer.alias = (info.get("alias") or "")[:MAX_ALIAS]
        peer.role = info.get("role", "") if info.get("role") in SENT_ROLES else ""
        nm = info.get("n1mm_master", "")
        peer.n1mm_master = True if nm == "1" else (False if nm == "0" else None)
        peer.started = info.get("started", 0.0) or 0.0
        peer.colors = info.get("colors", {}) or {}
        peer.block_time = info.get("block_time", 0.0) or 0.0
        peer.block_time_run = info.get("block_time_run", 0.0) or 0.0
        peer.pingpong = info.get("pingpong", "")
        peer.pp_limit = info.get("pp_limit", "")
        peer.alert_run = info.get("alert_run", False)
        peer.urgent = info.get("urgent", False)
        peer.urgent_band = info.get("urgent_band", "")
        peer.urgent_mode = info.get("urgent_mode", "")
        peer.urgent_time = info.get("urgent_time", 0.0)
        peer.urgent_priority = info.get("urgent_priority", False)
        peer.version = info["version"]
        peer.ips = set(info["ips"]) | {src}
        # Same PC only if the hello came from one of our own addresses or
        # carries our PC name. Comparing all addresses is wrong: virtual
        # adapters (VirtualBox 192.168.56.1, VPNs) repeat on every PC.
        peer.same_pc = src in self.local or (
            (info["host"] or "").lower() == socket.gethostname().lower()
        )
        peer.local_n1mm = info["local_n1mm"]
        peer.color = info.get("color", "")
        peer.color_time = info.get("color_time", 0.0)
        peer.alert = info.get("alert", False)
        peer.alert_band = info.get("alert_band", "")
        peer.alert_time = info.get("alert_time", 0.0)
        status = info.get("status", "")
        if status != getattr(peer, "status", None):
            peer.status = status
            if self.test_on:
                self._test_write(
                    "{}  peer {} ({} v{}): {}".format(
                        time.strftime("%H:%M:%S"), peer.host, src,
                        peer.version or "?", status or "no status (older than v1.18)",
                    )
                )
        peer.last_seen = time.time()
        self._priority_heard(peer, time.time())

    def _status_text(self):
        """Short state report sent to the other lamps (for diagnosis)."""
        mine = self._local_station()
        if mine is None:
            n1mm = "N1MM: not heard"
        else:
            n1mm = "N1MM {} from {}, {}".format(
                group_label(mine.key) or "no band/mode",
                mine.ip,
                {True: "Run", False: "S&P", None: "Run/S&P unknown"}[mine.running],
            )
        calls = [p.host for p in self._incoming_calls(time.time())]
        return "; ".join([
            n1mm,
            "F12 call option " + ("on" if self.f12_on else "off"),
            "call " + ("ON" if self.alert_on else "off"),
            "block option " + ("on" if self.block_on else "off"),
            "keyboard watcher " + ("running" if self.key_watcher is not None else "off"),
            "calls seen: " + (", ".join(calls) or "none"),
            "F-keys " + ("BLOCKED" if self._block_active else "free"),
            "sees: " + (", ".join(
                "{} {}".format(st.label, group_label(st.key) or "?")
                for st in list(self.stations.values())
                if st.ip not in self.local and time.time() - st.last_seen < 30
            ) or "no other station"),
            "share N1MM " + ("on" if self.relay_on else "off"),
            "N1MM master here: {}".format(
                {True: "yes", False: "no", None: "unknown (no N1MM settings file)"}[self.n1mm_master]
            ),
            "PingPong master: " + ("this PC" if self._i_am_master else (
                self.peer_name(self._master_peer) if self._master_peer else "?"
            )),
            "ping-pong role " + self._my_role_now()
            + (" (LOCKED)" if self._pp_locked else ""),
        ])

    @locked
    def _hello_state(self):
        return {
            "local_n1mm": self._local_n1mm_active(),
            "color": self.my_color,
            "color_time": self.my_color_time,
            "alert": self.alert_on,
            "alert_band": self._my_band(),
            "alert_time": self.alert_time,
            "status": self._status_text(),
            "alias": self.my_alias,
            "role": self._my_role_now() if self._my_role_now() != "off" else "",
            "n1mm_master": {True: "1", False: "0"}.get(self.n1mm_master, ""),
            "started": self.started,
            "colors": format_colors(self.color_map) if self._i_am_master else "",
            "block_time": self.block_limit if self._i_am_master else 0.0,
            "alert_run": self.alert_on and self.alert_run,
            "block_time_run": self.block_limit_run if self._i_am_master else 0.0,
            "pingpong": ("1" if self.pp_enabled else "0") if self._i_am_master else "",
            "pp_limit": "{:g}".format(self.pp_limit) if self._i_am_master else "",
            "problems": "|".join(self._problems(time.time())),
            "urgent": self.urgent_on,
            "urgent_band": self.urgent_key[0],
            "urgent_mode": self.urgent_key[1],
            "urgent_time": self.urgent_time,
            "urgent_priority": self.urgent_on and self.urgent_priority,
        }

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

    def _check_n1mm_master(self, now):
        """Re-read IsNetworkMaster from this PC's N1MM settings (cheap: only
        when the file changed, at most every 2 s)."""
        if now - self._ini_checked < 2.0:
            return
        self._ini_checked = now
        try:
            mtime = os.path.getmtime(self.n1mm_ini)
        except OSError:
            if self.n1mm_master is not None:
                self.n1mm_master = None
                self.hello_now.set()
            return
        if mtime != self._ini_mtime:
            self._ini_mtime = mtime
            value = read_n1mm_master(self.n1mm_ini)
            if value != self.n1mm_master:
                self.n1mm_master = value
                self._key_event("N1MM network master on this PC: {}".format(value))
                self.hello_now.set()

    def _pick_master(self, now):
        """'me' or a Peer: the PC whose N1MM is network master; if none
        says so, the lamp that started first (ties: PC name)."""
        me = ("me", self.n1mm_master, self.started, socket.gethostname().lower())
        cands = [me] + [
            (p, p.n1mm_master, p.started or now, (p.host or "").lower())
            for p in self._fresh_peers(now)
            if not getattr(p, "same_pc", False)
        ]
        masters = [c for c in cands if c[1] is True]
        pool = masters or cands
        return min(pool, key=lambda c: (c[2], c[3]))[0]

    def color_key(self, station, now):
        """'pc:<windows name>' for a PC running a lamp, else 'n1mm:<name>'."""
        if station.ip in self.local:
            return "pc:" + socket.gethostname().lower()
        for peer in self._fresh_peers(now):
            if station.ip in peer.ips and not getattr(peer, "same_pc", False):
                return "pc:" + (peer.host or "").lower()
        return "n1mm:" + (station.label or station.ip).lower()

    def _assign_colors(self, owners, now):
        """Master: keep every color already given, give new stations the
        next free palette color (never one a PC has chosen itself)."""
        keys = {"pc:" + socket.gethostname().lower()}
        for peer in self._fresh_peers(now):
            if not getattr(peer, "same_pc", False):
                keys.add("pc:" + (peer.host or "").lower())
        for st in list(self.stations.values()):
            if now - st.last_seen <= STATION_FORGET:
                keys.add(self.color_key(st, now))
        # Keep colors of stations still here (sticky); drop names that no
        # longer exist, e.g. a station first seen before its lamp was heard.
        result = {
            k: v for k, v in self.color_map.items()
            if k in keys and v.lower() not in owners
        }
        used = set(owners) | {v.lower() for v in result.values()}
        for key in sorted(keys - set(result)):
            free = [c for c in COLOR_PALETTE if c.lower() not in used]
            color = free[0] if free else COLOR_PALETTE[len(result) % len(COLOR_PALETTE)]
            result[key] = color
            used.add(color.lower())
        return result

    def _port_warning(self):
        """Text when another program listens on N1MM's port, else ""."""
        names = self._port_users.get(self.port) or []
        if not names:
            return ""
        return "UDP {} also used by {} – N1MM data may be missing here " \
            "(see README: Another program on the same port)".format(self.port, ", ".join(names))

    def _master_warning(self, now):
        """Text when more than one PC acts or claims to be master, else "".

        Only the master sends <BlockTime>, so two lamps sending it (or one
        besides this master) means lamps that don't hear each other. Two
        N1MMs saying IsNetworkMaster=True is a N1MM setup problem.
        """
        me = self.my_alias or socket.gethostname()
        peers = [
            p for p in self._fresh_peers(now) if not getattr(p, "same_pc", False)
        ]
        acting = ([me] if self._i_am_master else []) + [
            self.peer_name(p) for p in peers if p.block_time
        ]
        if len(acting) > 1:
            return "TWO MASTERS: {} – the lamps don't hear each other (firewall?) " \
                "or run different versions".format(", ".join(sorted(acting)))
        n1mm = ([me] if self.n1mm_master is True else []) + [
            self.peer_name(p) for p in peers if p.n1mm_master is True
        ]
        if len(n1mm) > 1:
            return "N1MM master on {} – check N1MM's Network Status".format(
                " and ".join(sorted(n1mm)))
        return ""

    def _local_n1mm_active(self):
        now = time.time()
        return any(
            st.ip in self.local and (now - st.last_seen) <= LOCAL_N1MM_RECENT
            for st in list(self.stations.values())
        )

    def _version_rows(self, now):
        """Footer notes for lamps running another PingPong version."""
        mine = upd.parse_version(__version__)
        rows = []
        for peer in self._fresh_peers(now):
            if getattr(peer, "same_pc", False) or not peer.version:
                continue
            theirs = upd.parse_version(peer.version)
            if theirs and theirs < mine:
                rows.append("{}: v{} – update it".format(self.peer_name(peer), peer.version))
            elif theirs > mine:
                rows.append("{}: v{} – update this PC".format(self.peer_name(peer), peer.version))
        return rows

    def _no_lamp_rows(self, now):
        """Footer notes for N1MM PCs heard directly but without a lamp.

        If other lamps are heard, that PC simply runs no PingPong. If no
        lamp at all is heard, this PC's firewall probably drops port 46070.
        """
        if not self._share["port"] or now - self.started < NO_LAMP_AFTER:
            return []
        peers = self._fresh_peers(now)
        lamp_ips = set()
        for peer in peers:
            lamp_ips |= peer.ips
        rows = []
        for st in list(self.stations.values()):
            if (
                st.ip in self.local
                or st.ip in lamp_ips
                or now - st.direct_seen > NO_LAMP_AFTER
            ):
                continue
            if any(not getattr(p, "same_pc", False) for p in peers):
                rows.append("{}: no PingPong lamp there".format(st.label))
            elif getattr(self, "_fw_ok", None):
                # Our rule is there: the other PC's firewall or the network.
                rows.append("{}: no PingPong heard – check that PC's lamp and "
                            "firewall".format(st.label))
            else:
                rows.append(
                    "{}: no PingPong heard – firewall? Options ▾ → Network → Allow "
                    "PingPong in Windows Firewall".format(st.label)
                )
        return rows

    def peer_name(self, peer):
        """Name to show for a PingPong PC: its alias, else its PC name."""
        return peer.alias or peer.host

    def station_name(self, st):
        """Name to show for an N1MM station: the alias its PC announced
        (or this PC's own alias), else N1MM's StationName."""
        if st.ip in self.local:
            return self.my_alias or st.label
        now = time.time()
        for peer in self._fresh_peers(now):
            if st.ip in peer.ips and peer.alias:
                return peer.alias
        return st.label

    def _urgent_pressed(self, fkey="F4"):
        """Keyboard thread: Shift+F2 / Shift+F4 in N1MM start an urgent call.

        It lasts while N1MM transmits the F2/F4 message N1MM fires at the same
        time; it ends when that transmission ends (see _urgent_check).
        """
        if not self.f12_on:
            return
        mine = self._local_station()
        if mine is None or not mine.key:
            self._key_event("Shift+{} ignored - this PC's band/mode unknown".format(fkey))
            return
        self.urgent_on = True
        self.urgent_fkey = fkey
        self.urgent_key = (mine.key[0], mine.key[1])
        self._urgent_tx_seen = False
        self.urgent_time = time.time()
        self.hello_now.set()
        if self.test_on:
            self._test_write("{}  Shift+{}  urgent call {}".format(
                time.strftime("%H:%M:%S"), fkey,
                "ON ({} {})".format(*self.urgent_key) if self.urgent_on else "OFF",
            ))

    def _priority_pressed(self, fkey="F4"):
        """Keyboard thread: Ctrl+F2 / Ctrl+F4 - priority call. Stations on my
        band and mode that are transmitting get Esc (their message stops) and
        their send keys are held; then this lamp sends F2/F4 in N1MM (the
        Ctrl+F4 itself never reaches N1MM). Flashes like Shift+F2/F4."""
        if not self.f12_on:
            return
        mine = self._local_station()
        if mine is None or not mine.key:
            self._key_event("Ctrl+{} ignored - this PC's band/mode unknown".format(fkey))
            return
        self.urgent_on = True
        self.urgent_priority = True
        self.urgent_fkey = fkey
        self.urgent_key = (mine.key[0], mine.key[1])
        self._urgent_tx_seen = False
        self.urgent_time = time.time()
        self.hello_now.set()  # the others stop at once
        self._key_event("Ctrl+{} priority call ({} {})".format(fkey, *self.urgent_key))
        threading.Thread(target=self._send_priority_key, args=(fkey, self.urgent_time),
                         daemon=True).start()

    def _send_priority_key(self, fkey, started):
        """After PRIORITY_DELAY (and once Ctrl is let go): F2/F4 into N1MM."""
        vk = {"F2": keys.VK_F2, "F4": keys.VK_F4}[fkey]
        deadline = started + 1.5
        time.sleep(PRIORITY_DELAY)
        while keys.ctrl_down() and time.time() < deadline:
            time.sleep(0.05)
        if not keys.n1mm_active():
            self._key_event("priority call: N1MM is not the active window - {} not sent".format(fkey))
            return
        keys.press_key(vk, release_ctrl=keys.ctrl_down())
        self._key_event("priority call: {} sent to N1MM".format(fkey))

    def _priority_heard(self, peer, now):
        """A priority call from another PC on my band and mode: if I am
        transmitting, Esc stops it (once per call); my send keys are held."""
        if not getattr(peer, "urgent_priority", False) or not peer.urgent \
                or getattr(peer, "same_pc", False):
            return
        mine = self._local_station()
        if mine is None or not mine.key or (peer.urgent_band, peer.urgent_mode) != tuple(mine.key[:2]):
            return
        done = self.__dict__.setdefault("_priority_done", set())
        call = (peer.id, peer.urgent_time)
        if call in done:
            return
        done.add(call)
        self._priority_block = True
        if mine.tx:
            if keys.n1mm_active():
                keys.press_key(keys.VK_ESCAPE)
                self._key_event("priority call from {}: Esc - my transmission stopped".format(
                    self.peer_name(peer)))
            else:
                self._key_event("priority call from {}: N1MM not active - Esc not sent".format(
                    self.peer_name(peer)))
        else:
            self._key_event("priority call from {}: keys held".format(self.peer_name(peer)))

    def _priority_active(self, now):
        """Another PC's priority call on my band and mode is still on."""
        mine = self._local_station()
        if mine is None or not mine.key:
            return False
        return any(
            getattr(p, "urgent_priority", False) and p.urgent
            and (p.urgent_band, p.urgent_mode) == tuple(mine.key[:2])
            and not getattr(p, "same_pc", False)
            and now - self._call_seen.get(("urgent", p.id, p.urgent_time), now) < PRIORITY_HOLD
            for p in self._fresh_peers(now)
        )

    def _urgent_check(self, now):
        """End my urgent call when my F2/F4 transmission ends."""
        if not self.urgent_on:
            return
        mine = self._local_station()
        tx = mine is not None and mine.tx
        if tx:
            self._urgent_tx_seen = True
            return
        if self._urgent_tx_seen:
            why = "transmission ended"
        elif now - self.urgent_time > URGENT_WAIT:
            why = "no transmission started ({} has no message?)".format(self.urgent_fkey)
        else:
            return
        self.urgent_on = False
        self.urgent_priority = False
        self.urgent_time = now
        self._key_event("urgent call ended: " + why)
        self.hello_now.set()  # receivers stop flashing right away

    def _urgent_seen(self, peer, now):
        """Remember when this urgent call was first seen here (local clock)."""
        self._call_seen.setdefault(("urgent", peer.id, peer.urgent_time), now)
        return True

    def _incoming_urgent(self, now):
        """Urgent calls (Shift+F2/F4) from other PCs on this PC's band and
        mode - Run or S&P, every station is called."""
        mine = self._local_station()
        if mine is None or not mine.key:
            return []
        band, mode = mine.key[0], mine.key[1]
        calls = [
            p for p in self._fresh_peers(now)
            if getattr(p, "urgent", False)
            and p.urgent_band == band
            and p.urgent_mode == mode
            and not getattr(p, "same_pc", False)
            and ("urgent", p.id, p.urgent_time) not in self.silenced
            and self._urgent_seen(p, now)
        ]
        return sorted(calls, key=lambda p: p.urgent_time)

    def _my_limit(self):
        return self.block_limit_run if self.alert_run else self.block_limit

    def _call_limit(self, peer):
        return self.block_limit_run if getattr(peer, "alert_run", False) else self.block_limit

    def _local_station(self):
        """This PC's own N1MM station (most recently heard), or None."""
        mine = [st for st in list(self.stations.values()) if st.ip in self.local]
        if not mine:
            return None
        return max(mine, key=lambda st: st.last_seen)

    def role_of(self, st):
        """Ping-pong role of an N1MM station: "RUN", "INB" or ""."""
        if st.ip in self.local:
            role = self._my_role_now()
            return role if role != "off" else ""
        for peer in self._fresh_peers(time.time()):
            if st.ip in peer.ips and not getattr(peer, "same_pc", False):
                return peer.role
        return ""

    def _pingpong_tx(self, st):
        """UDP thread: a station started transmitting.

        RUN transmitting opens a new period (slot free). The first INB that
        transmits after that holds the slot until RUN transmits again.
        """
        if not st.key:
            return
        band = st.key[0]
        role = self.role_of(st)
        now = time.time()
        if role == "RUN":
            if self._slots.pop(band, None) is not None:
                self._key_event("ping-pong {} MHz: RUN transmits - slot free".format(band))
        elif role == "INB":
            slot = self._slots.get(band)
            if slot is None or pp_expired(slot[1], now, self.pp_limit):
                self._slots[band] = [st.ip, now]
                self._key_event(
                    "ping-pong {} MHz: slot taken by {}".format(band, self.station_name(st))
                )
            elif slot[0] == st.ip:
                slot[1] = now  # holder keeps transmitting in its slot

    def _pingpong_end(self, st):
        """UDP thread: a station stopped transmitting. When RUN's
        transmission on this PC's band ends, the inband stations may go."""
        if not st.key or self.role_of(st) != "RUN":
            return
        if self._my_band() == st.key[0]:
            self._go_time = time.time()
            self._go_band = st.key[0]

    def _go_active(self, now):
        """Show GO on this lamp? (INB, lock on, RUN just finished, slot free)"""
        if not self.pp_enabled or self._pp_locked or self._my_role_now() != "INB":
            return False
        band = self._go_band
        if now - self._go_time > GO_TIME or band != self._my_band():
            return False
        if self._slots.get(band) is not None:
            return False  # an inband station already took the slot
        run = self._run_on_band(band, now)
        return run is not None and not run.tx

    def _n1mm_hint(self, now):
        """This PC's N1MM sends nothing to the lamp's port: say how to fix it."""
        if now - self.started < 30:
            return ""
        mine = self._local_station()
        if mine is not None and now - mine.last_seen < 30:
            return ""
        return ("no data from this PC's N1MM on UDP {0} – N1MM Broadcast Data → "
                "Radio must include 127.0.0.1:{0}".format(self.port))

    def _pingpong_note(self, now):
        """Why the ping-pong lock does nothing for this INB, or ""."""
        if not self.pp_enabled or self._my_role_now() != "INB":
            return ""
        band = self._my_band()
        if band and self._run_on_band(band, now) is None:
            return "no RUN on {} MHz – lock off".format(band)
        return ""

    def _problems(self, now):
        """What is wrong on this PC - sent to the Overview (Check column)."""
        out = []
        port = self._port_warning()
        if port:
            out.append(port.split(" – ")[0])  # "UDP 12062 also used by X"
        hint = self._n1mm_hint(now)
        if hint:
            out.append("no N1MM data on UDP {}".format(self.port))
        if keys.supported() and self.key_watcher is None and now - self.started > 10:
            out.append("keyboard watcher not running")
        if ERROR_COUNT[0]:
            out.append("{} error{} in pingpong_error.log".format(
                ERROR_COUNT[0], "" if ERROR_COUNT[0] == 1 else "s"))
        note = self._pingpong_note(now)
        if note:
            out.append(note.replace(" – lock off", ""))
        return out

    def _run_on_band(self, band, now):
        for st in list(self.stations.values()):
            if (
                st.key and st.key[0] == band
                and now - st.last_seen <= STATION_FORGET
                and self.role_of(st) == "RUN"
            ):
                return st
        return None

    def _pingpong_state(self, now):
        """(locked, holder station) for this PC as an INB."""
        if not self.pp_enabled or self._my_role_now() != "INB":
            return False, None
        mine = self._local_station()
        if mine is None or not mine.key:
            return False, None
        band = mine.key[0]
        slot = self._slots.get(band)
        if slot is None or pp_expired(slot[1], now, self.pp_limit):
            return False, None
        if slot[0] in self.local:
            return False, None  # this PC holds the slot
        if self._run_on_band(band, now) is None:
            return False, None  # no RUN known on this band: no lock
        holder = self.stations.get(slot[0])
        self._pp_left = (  # 0 = no time release: no countdown
            max(1, int(self.pp_limit - (now - slot[1]) + 0.999)) if self.pp_limit > 0 else 0)
        return True, holder

    def _keys_to_block(self):
        """Keyboard thread: which keys to swallow right now."""
        if getattr(self, "_priority_block", False):
            return keys.PINGPONG_KEYS  # another PC's priority call (Ctrl+F2/F4)
        if self._pp_locked:
            return keys.PINGPONG_KEYS
        if self._block_active:
            return True
        return None

    def _key_blocked(self, name):
        """Keyboard thread: a send key was swallowed during a call."""
        self._blocked_key = (name, time.time())
        if self.test_on:
            self._test_write(
                "{}  {} key  BLOCKED (call active)".format(time.strftime("%H:%M:%S"), name)
            )

    def _band_call(self, now):
        """Another PC's F12 call still running on this PC's band, or None.

        Only for S&P (the stations a call is aimed at), silenced or not:
        an S&P station cannot start a call while one runs on its band.
        A Run station is never stopped.
        """
        mine = self._local_station()
        if mine is None or not mine.key or mine.running is True:
            return None
        band = mine.key[0]
        busy = [
            p for p in self._fresh_peers(now)
            if p.alert
            and p.alert_band == band
            and not getattr(p, "same_pc", False)
            and self._block_left(p, now) > 0
        ]
        return min(busy, key=lambda p: p.alert_time) if busy else None

    def _f12_pressed(self):
        """Keyboard thread: F12 in N1MM toggles this PC's call."""
        if not self.f12_on:
            return
        busy = self._band_busy
        if not self.alert_on and busy is not None:
            # Another station's call runs on this band: F12 is not a call.
            self._f12_refused = time.time()
            if self.test_on:
                self._test_write(
                    "{}  F12 key  ignored - {} is calling on this band".format(
                        time.strftime("%H:%M:%S"), self.peer_name(busy)
                    )
                )
            return
        self.alert_on = not self.alert_on
        if self.alert_on:
            mine = self._local_station()
            # A Run station's call blocks every station on the band.
            self.alert_run = mine is not None and mine.running is True
        self.alert_time = time.time()
        self.hello_now.set()
        if self.test_on:
            self._test_write(
                "{}  F12 key  call {}".format(
                    time.strftime("%H:%M:%S"),
                    ("ON (Run call - all stations)" if self.alert_run else "ON (S&P call)")
                    if self.alert_on else "OFF",
                )
            )

    def _my_band(self):
        mine = self._local_station()
        return mine.key[0] if mine is not None and mine.key else ""

    def _incoming_calls(self, now):
        """Peers calling (F12) on this PC's band.

        A call from an S&P station reaches only S&P stations; a call from a
        Run station reaches every station on the band (Run and S&P).
        """
        mine = self._local_station()
        if mine is None or not mine.key:
            return []
        band = mine.key[0]
        in_run = mine.running is True
        calls = [
            p for p in self._fresh_peers(now)
            if p.alert
            and p.alert_band == band
            and (getattr(p, "alert_run", False) or not in_run)
            and not getattr(p, "same_pc", False)  # never "call" ourselves
            and (p.id, p.alert_time) not in self.silenced
            and self._block_left(p, now) > 0  # call time over: stop blinking
        ]
        return sorted(calls, key=lambda p: p.alert_time)

    def _block_left(self, peer, now):
        """Seconds of F-key blocking left for this call (local clock)."""
        key = (peer.id, peer.alert_time)
        start = self._call_seen.setdefault(key, now)
        if len(self._call_seen) > 50:  # forget old calls
            for old in sorted(self._call_seen, key=self._call_seen.get)[:-20]:
                self._call_seen.pop(old, None)
        return self._call_limit(peer) - (now - start)

    def _peer_station(self, peer):
        for st in list(self.stations.values()):
            if st.ip in peer.ips:
                return st
        return None

    def _my_call_left(self, now):
        """Whole seconds my own F12 call still runs."""
        return max(0, int(self._my_limit() - (now - self.alert_time) + 0.999))
