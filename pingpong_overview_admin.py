"""PingPong Overview - commands to the lamps, setup, password, ping, self-update.

AdminMixin (used by Overview in pingpong_overview.py). Everything that
sends something to the lamps (only from the master PC), plus saving and
restoring the station setup, the network password, the 5-minute ping
and the Overview's own update.
"""

import configparser
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

import n1mm_lamps as pp
import pingpong_update as upd
from pingpong_overview_common import *  # noqa: F401,F403


class AdminMixin:
    def _release_loop(self):
        self.stop.wait(3.0)
        while not self.stop.is_set():
            try:
                releases = upd.fetch_releases()
                if releases:
                    self.newest = releases[0]["version"]
                if upd.is_frozen():
                    mine = upd.fetch_releases(OVERVIEW_ASSET)
                    self.self_update = upd.newer_release(mine, pp.__version__)
            except Exception:
                pass  # no internet: compare the lamps with each other only
            self.stop.wait(RELEASE_CHECK)

    def _offer_self_update(self):
        """UI thread: once per start, offer a newer Overview."""
        rel = self.self_update
        if rel is None or self._asked_update:
            return
        self._asked_update = True
        if messagebox.askyesno(
            TITLE + " - update",
            "PingPong Overview v{} is available (you have v{}).\n\n"
            "Update now? The Overview restarts in a few seconds;\n"
            "its statistics and window settings are kept.".format(rel["version"], pp.__version__),
            parent=self.root,
        ):
            self._update_self(rel)

    def _update_self(self, rel=None):
        rel = rel or self.self_update
        if rel is None:
            return
        target = upd.exe_path()
        self.update_button.configure(text="Downloading v{}…".format(rel["version"]), state=tk.DISABLED)

        def work():
            try:
                new_file = upd.download(rel, target)
                upd.swap_in(target, pp.__version__, new_file)
                self.root.after(0, lambda: self._restart(target))
            except Exception as exc:
                msg = str(exc)
                self.root.after(0, lambda: self._update_failed(rel, msg))

        threading.Thread(target=work, daemon=True).start()

    def _update_failed(self, rel, msg):
        self.update_button.configure(text="Update Overview…", state=tk.NORMAL)
        messagebox.showerror(
            TITLE, "Update to v{} failed:\n{}\n\nThis version (v{}) is unchanged.".format(
                rel["version"], msg, pp.__version__), parent=self.root)

    def _restart(self, target):
        self._save_stats()
        try:
            upd.relaunch(target, sys.argv[1:])
        except OSError as exc:
            messagebox.showerror(TITLE, "Could not start the new version:\n{}".format(exc),
                                 parent=self.root)
            return
        self._close()

    def _target_version(self, live):
        versions = [l.hello.get("version") for l in live if l.hello.get("version")]
        if self.newest:
            versions.append(self.newest)
        return max(versions, key=version_key) if versions else ""

    def _ping_loop(self):
        """Every 5 min (and on 'Ping now'): round-trip time to every lamp."""
        self.stop.wait(10.0)
        while not self.stop.is_set():
            try:
                self._ping_round()
            except Exception:
                pp.log_error("ping")
            self._ping_now.wait(PING_EVERY)
            self._ping_now.clear()

    def _ping_round(self):
        import select

        if not self._on_master_pc(time.time()):
            return  # lamps answer only the master PC
        self._ping_seq += 1
        seq = self._ping_seq
        payload = pp.seal(pp.make_command("ping", seq=seq))
        socks = []
        for ip, bcast in pp.local_networks():
            try:
                sock = pp._sender_socket(ip)
            except OSError:
                continue
            for target in (bcast, "255.255.255.255"):
                try:
                    sock.sendto(payload, (target, self.port))
                except OSError:
                    pass
            socks.append(sock)
        sent = time.time()
        replies = {}
        try:
            while socks:
                left = sent + PING_WAIT - time.time()
                if left <= 0:
                    break
                ready, _, _ = select.select(socks, [], [], left)
                for sock in ready:
                    try:
                        data, _addr = sock.recvfrom(4096)
                    except OSError:
                        continue
                    data, status = pp.unseal(data, _addr[0])
                    pong = pp.parse_pong(data) if pp.accepted(status) else None
                    if pong and pong["seq"] == seq and pong["id"] not in replies:
                        replies[pong["id"]] = (time.time() - sent) * 1000.0
        finally:
            for sock in socks:
                sock.close()
        self._ping_results(replies, time.time())

    def _ping_results(self, replies, now):
        with self.lock:
            for lamp in list(self.lamps.values()):
                if now - lamp.hello_seen > GONE_AFTER:
                    continue
                st = self._stat(lamp)
                if lamp.id in replies:
                    ms = replies[lamp.id]
                    lamp.ping_ms, lamp.ping_lost = ms, False
                    st["ping_sum"] = st.get("ping_sum", 0.0) + ms
                    st["ping_n"] = st.get("ping_n", 0) + 1
                    st["ping_max"] = max(st.get("ping_max", 0.0), ms)
                    if ms > PING_SLOW:
                        self._event(lamp.name(), "slow ping", "{:.0f} ms".format(ms))
                elif version_key(lamp.hello.get("version", "")) >= PING_FROM:
                    lamp.ping_ms, lamp.ping_lost = None, True
                    st["ping_lost"] = st.get("ping_lost", 0) + 1
                    self._event(lamp.name(), "no ping reply")

    def _send_command(self, payload):
        payload = pp.seal(payload)
        sent = 0
        try:
            nets = pp.local_networks()
        except Exception:
            nets = []
        for ip, bcast in nets:
            try:
                sock = pp._sender_socket(ip)
            except OSError:
                continue
            try:
                for target in (bcast, "255.255.255.255"):
                    try:
                        sock.sendto(payload, (target, self.port))
                        sent += 1
                    except OSError:
                        pass
            finally:
                sock.close()
        return sent

    def _emergency(self, action):
        """Emergency only: restart / stop every lamp. Asks twice - Yes, then
        the word YES typed and Enter - before anything is sent."""
        title, text = self.EMERGENCY[action]
        if not self._on_master_pc(time.time()):
            messagebox.showwarning(TITLE, "This PC is not the master PC – the lamps take this "
                                   "only from the master PC.", parent=self.root)
            return
        if not messagebox.askyesno(TITLE + " - EMERGENCY", "{} – are you sure?\n\n{}".format(title, text),
                                   icon="warning", default="no", parent=self.root):
            return
        typed = simpledialog.askstring(
            TITLE + " - EMERGENCY",
            "{}: type YES and press Enter to confirm.".format(title), parent=self.root)
        if (typed or "").strip() != "YES":
            messagebox.showinfo(TITLE, "Cancelled – nothing was sent.", parent=self.root)
            return
        self._event("-", title + " (sent)")
        if not self._send_command(pp.make_command(action)):
            messagebox.showerror(TITLE, "Could not send the request (no network).", parent=self.root)
            return
        messagebox.showinfo(TITLE, "{}: sent to all lamps.".format(title), parent=self.root)

    def _load_network_key(self):
        """Own setting, else the lamp's (pingpong.ini next to the exe) - so a
        password set on the master's lamp is used here too, also later."""
        self._key_checked = time.time()
        key = self.cfg.get("network", "auth_key", fallback="") if self.cfg.has_section("network") else ""
        if not key:
            lamp_ini = configparser.ConfigParser()
            try:
                lamp_ini.read(os.path.join(app_dir(), "pingpong.ini"), encoding="utf-8")
                key = lamp_ini.get("network", "auth_key", fallback="")
            except (OSError, configparser.Error):
                key = ""
        try:
            pp.set_network_key(bytes.fromhex(key))
        except ValueError:
            pp.set_network_key(b"")

    def _choose_network_password(self):
        text = simpledialog.askstring(
            TITLE, "Network password – the same as on the lamps (Options ▾ → Network password…).\n"
            "Empty = off (currently: {}).".format("ON" if pp.AUTH["key"] else "off"),
            show="*", parent=self.root)
        if text is None:
            return
        text = text.strip()
        if text:
            again = simpledialog.askstring(TITLE, "Type it again:", show="*", parent=self.root)
            if again is None:
                return
            if again.strip() != text:
                messagebox.showwarning(TITLE, "The two passwords differ – nothing changed.",
                                       parent=self.root)
                return
        key = pp.derive_key(text)
        pp.set_network_key(key)
        if not self.cfg.has_section("network"):
            self.cfg.add_section("network")
        self.cfg.set("network", "auth_key", key.hex())
        self._save()
        self._event("-", "network password", "set" if key else "off")

    def _setup_data(self):
        """The station setup as heard now: every PC's name and color (by
        Windows computer name) and the master's settings."""
        now = time.time()
        with self.lock:
            live = [l for l in self.lamps.values()
                    if now - l.hello_seen <= GONE_AFTER and l.hello.get("host")]
            masters = self._masters(now)
        pcs = {}
        for lamp in sorted(live, key=lambda l: l.hello_seen):
            h = lamp.hello
            pcs[h["host"].lower()] = {"alias": h.get("alias", ""), "color": h.get("color", "")}
        master = {}
        if len(masters) == 1:
            h = masters[0].hello
            master = {
                "name": masters[0].name(),
                "block_time": "{:g}".format(h.get("block_time") or pp.BLOCK_LIMIT),
                "block_time_run": "{:g}".format(h.get("block_time_run") or pp.BLOCK_LIMIT_RUN),
                "pingpong": h.get("pingpong", ""),
                "pp_limit": h.get("pp_limit", ""),
            }
        return {"saved": time.strftime("%Y-%m-%d %H:%M"), "version": pp.__version__,
                "pcs": pcs, "master": master}

    @staticmethod
    def _setup_summary(data):
        lines = ["{:<16} {:<12} {}".format(host, v.get("alias") or "(PC name)",
                                          v.get("color") or "automatic")
                 for host, v in sorted(data.get("pcs", {}).items())]
        m = data.get("master") or {}
        if m:
            lines.append("")
            lines.append("Master settings: F12 S&P {} s, Run {} s, ping-pong lock {}{}".format(
                m.get("block_time", "?"), m.get("block_time_run", "?"),
                {"1": "ON", "0": "OFF"}.get(m.get("pingpong", ""), "?"),
                ", release {} s".format(m["pp_limit"]) if m.get("pp_limit", "") != "" else ""))
        return "\n".join(lines)

    def _save_setup(self):
        data = self._setup_data()
        if not data["pcs"]:
            messagebox.showinfo(TITLE, "No lamps heard – nothing to save.", parent=self.root)
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save station setup", initialdir=app_dir(),
            initialfile="pingpong_setup.json", defaultextension=".json",
            filetypes=[("PingPong setup", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
        except OSError as exc:
            messagebox.showerror(TITLE, "Could not save:\n{}".format(exc), parent=self.root)
            return
        self._event("-", "setup saved", os.path.basename(path))
        messagebox.showinfo(TITLE, "Saved {} PCs to\n{}\n\n{}".format(
            len(data["pcs"]), path, self._setup_summary(data)), parent=self.root)

    def _restore_setup(self):
        if not self._on_master_pc(time.time()):
            messagebox.showwarning(TITLE, "This PC is not the master PC – the lamps take this "
                                   "only from the master PC.", parent=self.root)
            return
        path = filedialog.askopenfilename(
            parent=self.root, title="Restore station setup", initialdir=app_dir(),
            filetypes=[("PingPong setup", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict) or not isinstance(data.get("pcs"), dict):
                raise ValueError("not a PingPong setup file")
        except (OSError, ValueError) as exc:
            messagebox.showerror(TITLE, "Could not read the setup:\n{}".format(exc),
                                 parent=self.root)
            return
        now = time.time()
        with self.lock:
            heard = {(l.hello.get("host") or "").lower() for l in self.lamps.values()
                     if now - l.hello_seen <= GONE_AFTER}
        missing = sorted(h for h in data["pcs"] if h not in heard)
        text = "Restore the setup saved {}?\n\n{}".format(
            data.get("saved", "?"), self._setup_summary(data))
        if missing:
            text += "\n\nNot heard now (they keep their settings): " + ", ".join(missing)
        if not messagebox.askyesno(TITLE, text, parent=self.root):
            return
        command = pp.make_command("setup", pcs=data["pcs"], master=data.get("master") or {})
        if not self._send_command(command):
            messagebox.showerror(TITLE, "Could not send the setup (no network).", parent=self.root)
            return
        self._event("-", "setup restored (sent)", os.path.basename(path))

    def _test_lamps(self):
        """Every lamp flashes its own name and role for 3 s."""
        if not self._on_master_pc(time.time()):
            messagebox.showwarning(TITLE, "This PC is not the master PC – the lamps take this "
                                   "only from the master PC.", parent=self.root)
            return
        self._event("-", "lamp test (sent)")
        if not self._send_command(pp.make_command("test")):
            messagebox.showerror(TITLE, "Could not send the request (no network).", parent=self.root)

    def _toggle_pingpong(self):
        """Ping-pong lock ON <-> OFF: asks the master lamp, which passes the
        setting on to every lamp (the button follows the master's hello)."""
        current = self.snapshot.get("pingpong", "")
        if not self._on_master_pc(time.time()):
            messagebox.showwarning(TITLE, "This PC is not the master PC – the lamps take this "
                                   "only from the master PC. Run the Overview there.",
                                   parent=self.root)
            return
        if current not in ("1", "0"):
            messagebox.showinfo(TITLE, "No master heard yet (or the master is older than "
                                "v1.67) - set it on the master's lamp under Options.",
                                parent=self.root)
            return
        value = "0" if current == "1" else "1"
        self._event("-", "ping-pong lock " + ("ON" if value == "1" else "OFF") + " requested")
        if not self._send_command(pp.make_command("pingpong", value=value)):
            messagebox.showerror(TITLE, "Could not send the request (no network).", parent=self.root)
            return
        self.pp_button.configure(text="Ping-pong lock: …")
        self._pp_sent = time.time()

    def _on_master_pc(self, now):
        """Is the master lamp on this PC? Lamps take commands only from it."""
        try:
            mine = {ip for ip, _ in pp.local_networks()} | {"127.0.0.1"}
        except Exception:
            return True
        masters = self._masters(now)
        return any(m.src in mine or set(m.hello.get("ips") or []) & mine for m in masters)

    def _update_all(self):
        now = time.time()
        with self.lock:
            live = [l for l in self.lamps.values() if now - l.hello_seen <= GONE_AFTER]
            target = self._target_version(live)
            old = sorted(
                (l.name(), l.hello.get("version", "?")) for l in live
                if version_key(l.hello.get("version", "")) < version_key(target)
            )
            manual = [n for n, v in old if version_key(v) < REMOTE_UPDATE_FROM]
            on_master = self._on_master_pc(now)
        if not old:
            # Nothing to do: just say which version everybody runs.
            messagebox.showinfo(
                TITLE + " - update all lamps",
                "All {} lamps run v{}{}.\nNothing to update.".format(
                    len(live), target or "?",
                    " – the newest release" if self.newest and target == self.newest else ""),
                parent=self.root)
            return
        lines = []
        if self.newest:
            lines.append("Newest release on GitHub: v{}.".format(self.newest))
        lines.append("Not on v{}: {}.".format(target, ", ".join("{} (v{})".format(n, v) for n, v in old)))
        if manual:
            lines.append("\nOlder than v1.60, update these by hand once: {}.".format(", ".join(manual)))
        if not on_master:
            lines.append("\n⚠ This PC is not the master PC – the lamps will ignore the request.")
        lines.append("\nAsk every lamp to install the newest release now?\n"
                     "Each lamp downloads it and restarts (a few seconds).")
        if not messagebox.askyesno(TITLE + " - update all lamps", "\n".join(lines), parent=self.root):
            return
        self._event("-", "update all lamps (sent)")
        sent = self._send_command(pp.make_command("update"))
        if not sent:
            messagebox.showerror(TITLE, "Could not send the request (no network).", parent=self.root)
