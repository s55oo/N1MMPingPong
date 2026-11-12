"""PingPong lamp - the phone lamp (web page on TCP 8047) and its QR code.

PhoneMixin (used by DisplayApp in n1mm_lamps.py): the addresses shown
left of Options, the state served to the phone, and the QR code window.
The web server and the page are in pingpong_phone.py.
"""

import socket
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox

import pingpong_phone as phone
import pingpong_qr as qr
from pingpong_common import *  # noqa: F401,F403
from pingpong_common import log_error  # noqa: F401
from pingpong_net import *  # noqa: F401,F403
from pingpong_version import __version__


class PhoneMixin:
    def _phone_seen(self):
        self._phone_req = time.time()  # called from the web server thread

    def _phone_address_loop(self):
        while not self.stop.is_set():
            try:
                self._phone_addrs = phone_addresses()
            except Exception:
                log_error("phone addresses")
            self.stop.wait(60.0)

    def _phone_tail(self):
        """Lamp header text; the phone page's state while someone watches."""
        label = getattr(self, "phone_label", None)
        if label is not None:
            if self._phone_error:
                text = "phone lamp: " + self._phone_error
            elif self.phone_port:
                text = phone.address_text(self._phone_addrs, self.phone_port)
            else:
                text = ""
            if label.cget("text") != text:
                label.configure(text=text, fg="#c00000" if self._phone_error else "#555555")
        if self.phone_port and time.time() - self._phone_req < 10.0:
            self._phone_state = self._phone_snapshot(time.time())

    def _phone_urls(self):
        """[(label, url)] of the phone lamp: LAN, plus ZeroTier if present."""
        addrs = self._phone_addrs or {}
        return [
            (label, "http://{}:{}/".format(addrs[key], self.phone_port))
            for key, label in (("lan", "LAN"), ("zt", "ZeroTier")) if addrs.get(key)
        ]

    def _show_phone_qr(self):
        """A window with a QR code per address: scan it with the phone."""
        urls = self._phone_urls()
        if not urls:
            messagebox.showinfo("PingPong", "No network address found yet – try again in a minute.",
                                parent=self.root)
            return
        old = getattr(self, "_qr_window", None)
        if old is not None:
            try:
                old.destroy()
            except tk.TclError:
                pass
        win = tk.Toplevel(self.root, bg="white")
        self._qr_window = win
        win.title("PingPong – phone lamp")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        tk.Label(win, text="Scan with the phone's camera to open this lamp",
                 bg="white", font=("Segoe UI", 10, "bold")).pack(padx=12, pady=(10, 4))
        row = tk.Frame(win, bg="white")
        row.pack(padx=8)
        for label, url in urls:
            cell = tk.Frame(row, bg="white")
            cell.pack(side=tk.LEFT, padx=8, pady=4)
            matrix = qr.qr_matrix(url)
            box, quiet = 6, 4
            side = (len(matrix) + 2 * quiet) * box
            canvas = tk.Canvas(cell, width=side, height=side, bg="white", highlightthickness=0)
            canvas.pack()
            for r, line in enumerate(matrix):
                for c, dark in enumerate(line):
                    if dark:
                        x, y = (c + quiet) * box, (r + quiet) * box
                        canvas.create_rectangle(x, y, x + box, y + box, fill="black", width=0)
            tk.Label(cell, text="{}\n{}".format(label, url[7:].rstrip("/")), bg="white",
                     font=("Segoe UI", 9)).pack()
        tk.Label(win, text="The phone must be on the same network (or ZeroTier).\n"
                           "Other devices need: Options ▾ → Network → Allow PingPong in Windows Firewall…",
                 bg="white", fg="#555555", font=("Segoe UI", 8), justify=tk.CENTER).pack(pady=(2, 4))
        tk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 10))
        win.bind("<Escape>", lambda e: win.destroy())

    def _open_phone_page(self):
        addrs = self._phone_addrs or {}
        ip = addrs.get("lan") or addrs.get("zt") or "127.0.0.1"
        if self.phone_port and not self._phone_error:
            webbrowser.open("http://{}:{}/".format(ip, self.phone_port))

    def _hex(self, color):
        """Any Tk color as #rrggbb for the web page ("" if unknown)."""
        if not color:
            return ""
        if color not in self._hexcache:
            try:
                r, g, b = self.root.winfo_rgb(color)
                self._hexcache[color] = "#{:02x}{:02x}{:02x}".format(r >> 8, g >> 8, b >> 8)
            except Exception:
                self._hexcache[color] = ""
        return self._hexcache[color]

    def _phone_snapshot(self, now):
        """What the phone page shows: the lamp as it is now, plus one tile
        per band with who transmits there (UI thread, lock held)."""
        border = self.canvas.cget("highlightbackground")
        lamp = {
            "bg": self._hex(self.canvas.cget("bg")),
            "fg": self._hex(self.canvas.itemcget(self.text_id, "fill")),
            "text": self.canvas.itemcget(self.text_id, "text"),
            "who": self._who_text,
            "freq": self._freq_text,
            "border": "" if border == self._window_bg else self._hex(border),
            "info": self.info.cget("text")[:300],
        }
        owners = self._color_owners(now)
        groups = {}
        for st in list(self.stations.values()):
            if st.key is None or now - st.last_seen > 60:
                continue
            groups.setdefault(st.key, []).append(st)
        bands = []
        for key in sorted(groups, key=lambda k: (band_sort_key(k[0]), k[1])):
            sts = groups[key]
            tx = [st for st in sts if st.tx and now - st.last_seen <= self.stale]
            active = max(tx, key=lambda st: st.last_seen) if tx else None
            bands.append({
                "band": "{} MHz {}".format(*key),
                "mine": key == self.tracked,
                "who": self.station_name(active) if active else "",
                "msg": (active.msg or "") if active else "",
                "color": self._hex(self._color_for(active, owners, now) or "red") if active else "",
                "stations": ", ".join(sorted(
                    "{} {}".format(self.station_name(st), self.role_label(st)).strip()
                    for st in sts)),
            })
        return {
            "name": self.my_alias or socket.gethostname(),
            "version": __version__,
            "time": time.strftime("%H:%M:%S"),
            "lamp": lamp,
            "bands": bands,
        }
