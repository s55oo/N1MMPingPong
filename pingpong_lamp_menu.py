"""PingPong lamp - the Options menu and its dialogs.

OptionsMixin (used by DisplayApp in n1mm_lamps.py): the Options ▾ menu,
My name / My color, the master's F12 block times and ping-pong settings,
the Windows Firewall rule and opening PingPong Overview on the master.
"""

import os
import sys
import threading
import time
import tkinter as tk
from tkinter import colorchooser, messagebox, simpledialog

import pingpong_phone as phone
import pingpong_update as upd
from pingpong_common import *  # noqa: F401,F403
from pingpong_net import *  # noqa: F401,F403
from pingpong_version import __version__


class OptionsMixin:
    def _show_options(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_checkbutton(
            label="Compact mode",
            accelerator="Shift+F",
            variable=self.var_compact,
            command=lambda: self.root.after(
                50, lambda: self._set_compact(bool(self.var_compact.get()))
            ),
        )
        for value, text in (("normal", "Lamp size: normal"), ("large", "Lamp size: large"),
                            ("xlarge", "Lamp size: extra large")):
            menu.add_radiobutton(label=text, variable=self.var_size, value=value,
                                 command=self._size_changed)
        menu.add_checkbutton(
            label="Night mode (dim lamp colors)",
            variable=self.var_night,
            command=self._night_changed,
        )
        menu.add_separator()
        menu.add_command(
            label="My name…  ({})".format(self.my_alias or "PC name"),
            command=self._choose_alias,
        )
        menu.add_command(
            label="My color…  ({})".format(self.my_color or "automatic"),
            command=self._choose_color,
        )
        if self._i_am_master:
            menu.add_command(
                label="F12 block time, S&P call…  ({:g} s, blocks S&P)".format(self.block_limit),
                command=lambda: self._choose_block_time(run=False),
            )
            menu.add_command(
                label="F12 block time, Run call…  ({:g} s, blocks all)".format(
                    self.block_limit_run),
                command=lambda: self._choose_block_time(run=True),
            )
        else:
            master = self.peer_name(self._master_peer) if self._master_peer else "?"
            menu.add_command(
                label="F12 block time: S&P call {:g} s, Run call {:g} s  (set on master {})".format(
                    self.block_limit, self.block_limit_run, master,
                ),
                state=tk.DISABLED,
            )
        menu.add_checkbutton(
            label="F12 call – blink stations on my band (Shift+F2/F4 = urgent)",
            variable=self.var_keys,
            command=self._keys_toggled,
        )
        menu.add_checkbutton(
            label="Block my F-keys while another station calls (F12)",
            variable=self.var_block,
            command=self._keys_toggled,
        )
        menu.add_checkbutton(
            label="Sound on F12 call",
            variable=self.var_sound,
            command=lambda: self.settings.set("call_sound", self.var_sound.get()),
        )
        menu.add_checkbutton(
            label="Share my N1MM with other lamps (all networks)",
            variable=self.var_relay,
            command=self._relay_toggled,
        )
        menu.add_separator()
        if self._i_am_master:
            var = tk.BooleanVar(value=self.pp_enabled)
            menu.add_checkbutton(
                label="Ping-pong lock (RUN first, inband stations take turns) – for all PCs",
                variable=var, command=lambda: self._set_pingpong(bool(var.get())),
            )
            menu.add_command(
                label="Ping-pong release time…  ({})".format(self._pp_limit_text()),
                command=self._choose_pp_limit,
            )
        else:
            master = self.peer_name(self._master_peer) if self._master_peer else "?"
            menu.add_command(
                label="Ping-pong lock: {}, release time {}  (set on master {})".format(
                    "ON" if self.pp_enabled else "OFF – everybody may transmit",
                    self._pp_limit_text(), master),
                state=tk.DISABLED,
            )
        now_role = self._my_role_now()
        menu.add_command(
            label="Ping-pong role: {}  (from N1MM: Run = RUN, S&P = INB)".format(
                now_role if now_role != "off" else "unknown"),
            state=tk.DISABLED,
        )
        menu.add_separator()
        menu.add_command(
            label="Phone lamp QR code…  ({})".format(
                phone.address_text(self._phone_addrs, self.phone_port) or "no address yet")
            if self.phone_port and not self._phone_error
            else "Phone lamp QR code  ({})".format(self._phone_error or "off"),
            command=self._show_phone_qr,
            state=tk.NORMAL if self.phone_port and not self._phone_error else tk.DISABLED,
        )
        if self._i_am_master:
            menu.add_command(
                label="Open PingPong Overview…",
                command=self._open_overview,
                state=tk.DISABLED if self._busy else tk.NORMAL,
            )
        else:
            master = self.peer_name(self._master_peer) if self._master_peer else "?"
            menu.add_command(
                label="Open PingPong Overview  (only on master {})".format(master),
                state=tk.DISABLED,
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
            label="Windows Firewall: PingPong allowed ✔  (add the rule again…)"
            if self._fw_ok else "Allow PingPong in Windows Firewall…",
            state=tk.NORMAL if sys.platform == "win32" else tk.DISABLED,
            command=self._allow_firewall,
        )
        menu.add_command(
            label="Network password…  ({})".format("ON ✔" if AUTH["key"] else "off"),
            command=self._choose_network_password,
        )
        menu.add_checkbutton(
            label="Diagnostics (log to file)",
            variable=self.var_test,
            command=self._test_toggled,
        )
        menu.add_separator()
        menu.add_command(
            label="PingPong  UDP {}  v{}{}".format(
                self.port, __version__, "" if frozen else "  (source - updates only in exe)"
            ),
            state=tk.DISABLED,
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

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
            text = self.peer_name(owner) if taken else ("✓ " + color if mine else color)
            tk.Button(
                grid,
                text=text,
                width=12,
                bg=color,
                activebackground=color,
                fg=self.text_on(color),
                activeforeground=self.text_on(color),
                disabledforeground=self.text_on(color),
                relief=tk.SUNKEN if mine else tk.RAISED,
                state=tk.DISABLED if taken else tk.NORMAL,
                command=lambda c=color: pick(c),
            ).grid(row=i // 5, column=i % 5, padx=2, pady=2)
        if self.my_color and self.my_color not in COLOR_PALETTE:
            tk.Label(
                dlg,
                text="✓ my color  {}".format(self.my_color),
                bg=self.my_color,
                fg=self.text_on(self.my_color),
                font=("Segoe UI", 9, "bold"),
                padx=8,
                pady=3,
            ).pack(pady=(6, 0))

        def more():
            try:
                _, chosen = colorchooser.askcolor(
                    color=self.my_color or "#ff0000",
                    title="PingPong - my color",
                    parent=dlg,
                )
            except tk.TclError:
                chosen = None
            if not chosen:
                return
            chosen = chosen.lower()
            clash = self._too_similar(chosen, self._color_owners(time.time()))
            if clash:
                messagebox.showwarning(
                    "PingPong",
                    "This color is too similar to the color of {}.\n"
                    "Please choose a different one.".format(clash),
                    parent=dlg,
                )
                return
            pick(chosen)

        buttons = tk.Frame(dlg)
        buttons.pack(pady=8)
        tk.Button(
            buttons, text="More colors…", width=12, command=more
        ).pack(side=tk.LEFT, padx=4)
        tk.Button(
            buttons, text="Automatic", width=10, command=lambda: pick("")
        ).pack(side=tk.LEFT, padx=4)
        tk.Button(buttons, text="Close", width=10, command=closed).pack(
            side=tk.LEFT, padx=4
        )

    # ----- joining a protected PingPong network

    def _password_warnings(self, now):
        """Red line texts: a request for the password, or what is ignored."""
        needed, who = password_needed(now)
        if needed:
            if not getattr(self, "_pw_asked", False) and now - self.started < 60:
                self._pw_asked = True  # once, right after the start - never mid-contest
                self.root.after(500, lambda: self._ask_network_password(auto=True))
            if AUTH["key"]:
                return ["🔒 {} uses a different network password – click here to enter it".format(who)]
            return ["{} ({})".format(PASSWORD_NEEDED_TEXT, who)]
        return auth_problems(now, self.local)

    def _password_click(self):
        if password_needed(time.time())[0]:
            self._ask_network_password(auto=False)

    def _ask_network_password(self, auto=False):
        """Ask for the password of the PingPong network heard here and check
        it at once against a message just received from it."""
        if getattr(self, "_pw_dialog_open", False):
            return
        needed, who = password_needed(time.time())
        if not needed:
            return
        self._pw_dialog_open = True
        try:
            prompt = ("This PingPong network uses a password (heard from {}).\n"
                      "Enter it to connect – the same as on the master.\n\n"
                      "Cancel = later: click the red line under the lamp.").format(who)
            while True:
                text = simpledialog.askstring("PingPong - network password", prompt,
                                              show="*", parent=self.root)
                if not text or not text.strip():
                    return
                ok = password_matches(text.strip())
                if ok is False:
                    prompt = ("Wrong password – it doesn't match the messages from {}.\n"
                              "Try again (Cancel = later).").format(who)
                    continue
                break
            key = derive_key(text.strip())
            set_network_key(key)
            self.settings.set_text("network", "auth_key", key.hex())
            AUTH_SEEN.clear()
            self._key_event("network password entered{}".format("" if ok else " (not checked)"))
            self.hello_now.set()
            if not auto or ok is None:
                messagebox.showinfo("PingPong", "Connected ✔" if ok else
                                    "Password saved – it couldn't be checked yet (no recent "
                                    "message); a red line will tell if it's wrong.",
                                    parent=self.root)
        finally:
            self._pw_dialog_open = False

    def _choose_network_password(self):
        """Same password on every PC (and in the Overview): the lamps then
        sign their messages and ignore any without the right signature."""
        text = simpledialog.askstring(
            "PingPong - network password",
            "Network password – the SAME on every PingPong PC and in the Overview.\n"
            "With it, lamps ignore messages from anyone without it.\n"
            "PC clocks must agree within 5 minutes.\n\n"
            "Empty = off (currently: {}).".format("ON" if AUTH["key"] else "off"),
            show="*", parent=self.root)
        if text is None:
            return
        text = text.strip()
        if text:
            again = simpledialog.askstring("PingPong - network password", "Type it again:",
                                           show="*", parent=self.root)
            if again is None:
                return
            if again.strip() != text:
                messagebox.showwarning("PingPong", "The two passwords differ – nothing changed.",
                                       parent=self.root)
                return
        elif not AUTH["key"]:
            return
        key = derive_key(text)
        set_network_key(key)
        self.settings.set_text("network", "auth_key", key.hex())
        AUTH_SEEN.clear()
        self._key_event("network password {}".format("set" if key else "off"))
        self.hello_now.set()
        messagebox.showinfo(
            "PingPong",
            "Network password {}.\n\nThe other lamps notice it and ask for it themselves "
            "(red line under the lamp – click it). The Overview on this PC uses it too."
            .format("set" if key else "off") if key else "Network password off.",
            parent=self.root)

    def _firewall_rules(self):
        return [FIREWALL_RULE] + ([FIREWALL_PHONE_RULE] if self.phone_port else [])

    def _check_firewall(self):
        """In the background: are this lamp's firewall rules there?"""
        if sys.platform != "win32":
            return

        def work():
            found = [firewall_rule_exists(name) for name in self._firewall_rules()]
            self._fw_ok = None if None in found else all(found)

        threading.Thread(target=work, daemon=True).start()

    def _allow_firewall(self):
        """Add an inbound firewall rule for the lamp's UDP ports (asks UAC)."""
        ports = ",".join(
            str(p) for p in (self.port, self._share["port"]) if p
        )
        tcp = " and TCP {} (phone lamp)".format(self.phone_port) if self.phone_port else ""
        if not messagebox.askyesno(
            "PingPong - Windows Firewall",
            "Allow incoming UDP {} (N1MM data and the other lamps){} on private "
            "and domain networks?\n\nWindows asks for administrator rights. "
            "The rule is called \"{}\"; an older rule with that name is "
            "replaced.".format(ports, tcp, FIREWALL_RULE),
            parent=self.root,
        ):
            return
        rule = (
            'netsh advfirewall firewall delete rule name="{0}" >nul 2>&1 & '
            'netsh advfirewall firewall add rule name="{0}" dir=in action=allow '
            "protocol=UDP localport={1} profile=private,domain"
        ).format(FIREWALL_RULE, ports)
        if self.phone_port:
            rule += (
                ' & netsh advfirewall firewall delete rule name="{0}" >nul 2>&1 & '
                'netsh advfirewall firewall add rule name="{0}" dir=in action=allow '
                "protocol=TCP localport={1} profile=private,domain"
            ).format(FIREWALL_PHONE_RULE, self.phone_port)
        try:
            import ctypes

            result = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "cmd.exe", '/c ' + rule, None, 0
            )
        except Exception as exc:
            result, rule = 0, repr(exc)
        if result > 32:
            self.root.after(3000, self._check_firewall)
            messagebox.showinfo(
                "PingPong - Windows Firewall",
                "Firewall rule \"{}\" added (UDP {}){}.".format(
                    FIREWALL_RULE, ports,
                    " and \"{}\" (TCP {})".format(FIREWALL_PHONE_RULE, self.phone_port)
                    if self.phone_port else ""),
                parent=self.root,
            )
        else:
            messagebox.showwarning(
                "PingPong - Windows Firewall",
                "The rule was not added (no administrator rights given).",
                parent=self.root,
            )

    def _pp_limit_text(self):
        return "{:g} s".format(self.pp_limit) if self.pp_limit > 0 else "off – only RUN frees"

    def _choose_pp_limit(self):
        """Master only: after how many seconds without RUN the slot frees itself."""
        if not self._i_am_master:
            return
        value = simpledialog.askinteger(
            "PingPong - ping-pong release time",
            "After how many seconds without a RUN transmission does the\n"
            "ping-pong slot free itself? {}-{} s.\n"
            "0 = never by time - only RUN transmitting frees it.\n"
            "Used by all lamps.".format(*PP_RANGE),
            initialvalue=int(self.pp_limit),
            minvalue=PP_RANGE[0],
            maxvalue=PP_RANGE[1],
            parent=self.root,
        )
        if value is None:
            return
        self.pp_limit = float(value)
        self.settings.set_text("network", "pp_limit", "{:g}".format(value))
        self._key_event("ping-pong release time set to {}".format(self._pp_limit_text()))
        self.hello_now.set()  # tell all lamps right away

    def _choose_block_time(self, run=False):
        """Master only: how long an F12 call blocks the other PCs.

        run=False: call from an S&P station (blocks S&P stations);
        run=True: call from a Run station (blocks every station).
        """
        if not self._i_am_master:
            return
        current = self.block_limit_run if run else self.block_limit
        value = simpledialog.askinteger(
            "PingPong - F12 block time ({})".format("Run call" if run else "S&P call"),
            "How many seconds an F12 call from a station in {} lasts\n"
            "(blinking and F-key block on {}), {}-{} s.\n"
            "Used by all lamps.".format(
                "Run" if run else "S&P",
                "ALL other stations on the band" if run else "the S&P stations on the band",
                *BLOCK_RANGE
            ),
            initialvalue=int(current),
            minvalue=BLOCK_RANGE[0],
            maxvalue=BLOCK_RANGE[1],
            parent=self.root,
        )
        if value is None:
            return
        if run:
            self.block_limit_run = float(value)
            self.settings.set_text("network", "block_time_run", "{:g}".format(value))
        else:
            self.block_limit = float(value)
            self.settings.set_text("network", "block_time", "{:g}".format(value))
        self._key_event("F12 block time ({}) set to {:g} s".format(
            "Run" if run else "S&P", value))
        self.hello_now.set()  # tell all lamps right away

    def _choose_alias(self):
        current = self.my_alias
        text = simpledialog.askstring(
            "PingPong - my name",
            "Short name for this PC on every lamp (e.g. RUN1).\n"
            "Max {} characters; empty = use the N1MM computer name.".format(MAX_ALIAS),
            initialvalue=current,
            parent=self.root,
        )
        if text is None:
            return
        text = " ".join(text.split())[:MAX_ALIAS]
        if text:
            for peer in self._fresh_peers(time.time()):
                if peer.alias.lower() == text.lower() and not getattr(peer, "same_pc", False):
                    messagebox.showwarning(
                        "PingPong",
                        "The name {} is already used by {}.\n"
                        "Please choose a different one.".format(text, peer.host),
                        parent=self.root,
                    )
                    return
        self.my_alias = text
        self.settings.set_text("station", "alias", text)
        self.hello_now.set()  # tell the other lamps right away

    def _size_changed(self):
        self.lamp_size = self.var_size.get()
        self.scale = LAMP_SIZES.get(self.lamp_size, 1.0)
        self.settings.set_text("window", "size", self.lamp_size)
        self._apply_size()

    def _night_changed(self):
        self.night = bool(self.var_night.get())
        self.settings.set_text("window", "night", "yes" if self.night else "no")
        self.canvas.configure(bg=COLOR_IDLE)  # repainted on the next tick

    def _test_toggled(self):
        self.test_on = bool(self.var_test.get())
        self._test_write(
            "=== diagnostics {} · PingPong v{} · local IPs {} ===".format(
                "ON" if self.test_on else "OFF",
                __version__,
                ", ".join(sorted(self.local)) or "?",
            )
        )

    def _keys_toggled(self):
        call, block = bool(self.var_keys.get()), bool(self.var_block.get())
        # The keyboard watcher always runs: the ping-pong lock may need it
        # whenever N1MM is in S&P (role INB).
        if not self._start_keys():
            self.var_keys.set(False)
            self.var_block.set(False)
            call = block = False
        if not call and self.alert_on:
            self.alert_on = False
            self.alert_time = time.time()
            self.hello_now.set()
        self.f12_on, self.block_on = call, block
        if not block:
            self._block_active = False
        self.settings.set("f12_call", call)
        self.settings.set("block_keys", block)

    def _set_pingpong(self, on):
        """Master only: ping-pong lock on/off for every lamp."""
        if not self._i_am_master:
            return
        self.pp_enabled = on
        self.settings.set_text("network", "pingpong", "yes" if on else "no")
        self._key_event("ping-pong lock {} (for all PCs)".format("ON" if on else "OFF"))
        self.hello_now.set()  # tell all lamps right away

    def _relay_toggled(self):
        self.relay_on = bool(self.var_relay.get())
        self.settings.set("share_n1mm", self.relay_on)

    def _open_overview(self):
        """Start PingPong Overview on this (master) PC. Its exe sits next
        to the lamp's; if it is missing, offer to download it."""
        if not self._i_am_master or self._busy:
            return
        if instance_running(OVERVIEW_MUTEX):
            messagebox.showinfo(
                "PingPong", "PingPong Overview is already running on this PC.",
                parent=self.root)
            return
        if not upd.is_frozen():  # from source: run the .py next to us
            script = os.path.join(app_dir(), "pingpong_overview.py")
            try:
                import subprocess

                subprocess.Popen([sys.executable, script], cwd=app_dir())
                self._key_event("Overview started")
            except OSError as exc:
                messagebox.showerror("PingPong", "Could not start the Overview:\n{}".format(exc),
                                     parent=self.root)
            return
        target = os.path.join(os.path.dirname(upd.exe_path()), OVERVIEW_EXE)
        if os.path.isfile(target):
            self._start_overview(target)
            return
        self._busy = True
        self._status = "looking for PingPong Overview…"
        self._run_bg(lambda: upd.fetch_releases(OVERVIEW_EXE),
                     lambda rels, err: self._offer_overview(target, rels, err))

    def _offer_overview(self, target, releases, error):
        self._busy = False
        self._status = ""
        if error is not None or not releases:
            messagebox.showerror(
                "PingPong",
                "{} is not next to the lamp and could not be downloaded:\n{}".format(
                    OVERVIEW_EXE, error or "no release carries it"),
                parent=self.root)
            return
        rel = releases[0]
        if not messagebox.askyesno(
            "PingPong",
            "{} is not in\n{}\n\nDownload PingPong Overview v{} ({:.1f} MB) from GitHub "
            "and start it?".format(OVERVIEW_EXE, os.path.dirname(target), rel["version"],
                                   (rel["size"] or 0) / 1e6),
            parent=self.root,
        ):
            return
        self._busy = True
        self._status = "downloading PingPong Overview v{}…".format(rel["version"])

        def work():
            part = upd.download(rel, target)
            os.replace(part, target)
            return target

        self._run_bg(work, self._overview_downloaded)

    def _overview_downloaded(self, target, error):
        self._busy = False
        self._status = ""
        if error is not None:
            messagebox.showerror("PingPong", "Download failed:\n{}".format(error),
                                 parent=self.root)
            return
        self._start_overview(target)

    def _start_overview(self, target):
        try:
            upd.relaunch(target, [], after_update=False)
            self._key_event("Overview started")
        except OSError as exc:
            messagebox.showerror("PingPong", "Could not start the Overview:\n{}".format(exc),
                                 parent=self.root)
