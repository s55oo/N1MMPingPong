"""PingPong lamp - updates from GitHub and remote restart / stop.

UpdateMixin (used by DisplayApp in n1mm_lamps.py): checking for and
installing new releases, switching back, and the Overview's update,
restart and stop commands. The file work itself is in pingpong_update.py.
"""

import os
import sys
import time
import tkinter as tk
from tkinter import messagebox

import pingpong_update as upd
from pingpong_common import *  # noqa: F401,F403
from pingpong_common import log_error  # noqa: F401
from pingpong_net import *  # noqa: F401,F403
from pingpong_version import __version__


class UpdateMixin:
    def _remote_update(self):
        """'Update all lamps' from the Overview: install the newest release
        without asking (the master's operator asked for it)."""
        if not upd.is_frozen():
            self._show_status("update request ignored - running from source")
            return
        if self._busy:
            return
        self._busy = True
        self._status = "update requested by the master – checking…"

        def done(releases, error):
            self._busy = False
            self._status = ""
            if error is not None:
                self._show_status("update check failed: {}".format(error))
                return
            new = upd.newer_release(releases, __version__)
            if new is None:
                self._show_status("already the newest version (v{})".format(__version__))
                return
            self._install(new)

        self._run_bg(upd.fetch_releases, done)

    def _check_updates(self, manual):
        if self._busy:
            return
        self._busy = True
        if manual:
            self._status = "checking for updates…"
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
        self._status = "downloading v{}…".format(release["version"])

        def progress(done, total):
            if total:
                self._status = "downloading v{}… {}%".format(
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
        self._status = "getting versions…"

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

    def _remote_restart(self):
        """Emergency 'Restart all lamps' from the Overview: start this lamp
        again (same exe / script, same options) and close this one."""
        self._key_event("restart requested by the master PC")
        if upd.is_frozen():
            target, args = sys.executable, sys.argv[1:]
        else:
            target, args = sys.executable, [os.path.abspath(sys.argv[0])] + sys.argv[1:]
        try:
            upd.relaunch(target, args)  # the new lamp waits until this one is gone
        except OSError:
            log_error("remote restart")
            return  # could not start a new one: keep running
        self.on_close()

    def _remote_stop(self):
        """Emergency 'Stop all lamps' from the Overview: close this lamp
        (keyboard watcher removed, nothing blocked any more)."""
        self._key_event("stop requested by the master PC")
        self.on_close()
