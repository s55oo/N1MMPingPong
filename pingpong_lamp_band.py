"""Lamp side of the BAND VIOLATION alert from N1MM_timer (see pingpong_bandalert.py).

- The lamp on the PC running N1MM_timer receives the alert on 127.0.0.1:46071
  (loopback only) and, if it is the master, passes it on to all PingPong PCs
  over the signed lamp network (UDP 46070).
- Every lamp accepts it from the network only from the master PC, shows it in
  a footer (red: BAND VIOLATION, grey: Band watcher offline) and beeps once per
  new violation if the lamp's sound is on.
- Advisory only: nothing here touches the ping-pong lock, F-keys, radios or N1MM.
"""

import os
import socket
import threading
import time
import tkinter as tk

import pingpong_bandalert as band
import pingpong_contest as contest
import pingpong_contest_log as contest_log
from pingpong_common import UI_BG, UI_DIM, UI_TEXT, log_error
from pingpong_net import BAND_MAGIC, loopback_loop, send_relay

BAND_RED = "#c62828"
BAND_GREY = "#5f6368"


class BandAlertMixin:
    """Mixed into the lamp. Needs: settings, stop, local, _i_am_master,
    _master_peer, _share, stations, var_sound, frame, root, _key_event."""

    def _band_init(self):
        self._contest_radio_seen = None
        self._contest_selection = None
        self._contest_folder = self.settings.get_text("contest", "database_folder", "")
        self._contest_msg = None
        self._contest_master = None
        self._contest_retired = {}
        self._contest_expires = 0.0
        self._contest_label = None
        self._activity_label = None
        self._contest_text = ""
        self.var_last_qso = tk.BooleanVar(value=self.settings.get("show_last_qso", False))
        self.band = band.BandAlert()
        self._band_master = None  # the master the alert came from
        self._band_view = None  # (kind, text) shown now
        self._band_label = None
        try:
            port = int(self.settings.get_text("band_alert", "port", str(band.BAND_PORT)))
        except ValueError:
            port = band.BAND_PORT
        self.band_port = port if 0 < port < 65536 else 0  # 0 = off
        threading.Thread(target=contest_log.publish_loop, args=(self,), daemon=True).start()
        if self.band_port:
            threading.Thread(target=self._band_listen, daemon=True).start()

    def _band_listen(self):
        if not loopback_loop(self.band_port, self.on_band_local, self.stop):
            self._key_event("band alert: port {} is in use - not listening".format(self.band_port))

    # --- receiving -----------------------------------------------------------------

    def on_band_local(self, src, data):
        """Loopback thread: a datagram from N1MM_timer on this PC."""
        if src not in band.LOOPBACK:
            return  # the socket only listens on 127.0.0.1; checked again anyway
        if data.startswith(contest.MAGIC):
            if (self._i_am_master and getattr(self, "_contest_selection", None) is None
                    and self._contest_take(data, "me")):
                send_relay(self._share, data)
            return
        msg, why = band.validate(data, time.time())
        if msg is None:
            self._key_event("band alert from N1MM_timer refused: " + why)
            return
        if not self._i_am_master:
            self._key_event("band alert from N1MM_timer ignored - this PC is not the master")
            return
        send_relay(self._share, BAND_MAGIC + bytes(data))
        self._band_take(msg, "me")

    def on_band(self, src, data):
        """Discovery thread: an alert passed on by the master's lamp."""
        master = self._master_peer
        if self._i_am_master and src in self.local:
            source = "me"  # our own relay came back
        elif master is not None and src in master.ips:
            source = master.id
        else:
            self._key_event("band alert from {} ignored - not the master PC".format(src))
            return
        if data.startswith(contest.MAGIC):
            self._contest_take(data, source)
            return
        msg, why = band.validate(data, time.time())
        if msg is None:
            self._key_event("band alert refused: " + why)
            return
        self._band_take(msg, source)

    def _publish_contest(self, data):
        if self._contest_take(data, "me"):
            send_relay(self._share, data)

    def _contest_take(self, data, source):
        now = time.time()
        msg = contest.decode(data, now)
        if msg is None:
            return False
        with self.lock:
            old = getattr(self, "_contest_msg", None)
            mono = time.monotonic()
            retired = {k: expires for k, expires in getattr(self, "_contest_retired", {}).items()
                       if expires > mono}
            if (source, msg["instance"]) in retired:
                return False
            if old and self._contest_master == source:
                if msg["instance"] == old["instance"] and msg["seq"] <= old["seq"]:
                    return False
                if msg["instance"] != old["instance"]:
                    if msg["sent"] < old["sent"] <= now + 2:
                        return False
                    retired[(source, old["instance"])] = mono + 302
            self._contest_retired = retired
            self._contest_expires = mono + min(msg["ttl"], msg["sent"] + msg["ttl"] - now)
            self._contest_msg, self._contest_master = msg, source
        return True

    def _contest_update(self, master, now):
        with self.lock:
            if getattr(self, "_contest_master", None) != master:
                self._contest_expires = 0.0
            msg = getattr(self, "_contest_msg", None)
            stale = time.monotonic() >= getattr(self, "_contest_expires", 0.0)
        text = ""
        if msg:
            pc = os.environ.get("COMPUTERNAME") or socket.gethostname()
            text = contest.display(msg, now, {pc}, self.var_last_qso.get(), stale)
        elif getattr(self, "var_last_qso", None) is not None and self.var_last_qso.get():
            text = "Waiting for Application Info · open or reselect the contest on the master"
        if text == getattr(self, "_contest_text", ""):
            return
        self._contest_text = text
        lines = text.splitlines()
        activity = lines.pop() if lines and lines[-1].startswith("Last QSO · ") else ""
        self._contest_label = self._contest_row(getattr(self, "_contest_label", None),
                                                "\n".join(lines), 9, UI_TEXT,
                                                before=getattr(self, "_activity_label", None))
        self._activity_label = self._contest_row(getattr(self, "_activity_label", None),
                                                 activity, 8, UI_DIM)

    def _contest_row(self, label, text, size, color, before=None):
        if not text:
            if label is not None:
                label.pack_forget()
            return label
        if label is None:
            label = tk.Label(self.frame, font=("Segoe UI", size), justify=tk.LEFT,
                             bg=UI_BG, fg=color)
        label.configure(text=text)
        if not label.winfo_manager():
            position = {"before": before} if before is not None and before.winfo_manager() else {}
            label.pack(fill=tk.X, pady=(2, 0), **position)
        return label

    def _band_take(self, msg, source):
        with self.lock:
            if source != self._band_master:
                self.band.reset()  # a new master: the old state is not valid
                self._band_master = source
            if self.band.offer(msg, time.time()) and self.test_on:
                self._key_event("band alert: {} seq {} {}".format(
                    msg["status"], msg["seq"], "; ".join(v["text"] for v in msg["violations"])))

    # --- display (UI thread, from the display loop) --------------------------------

    def _own_station_names(self):
        return {st.label for ip, st in self.stations.items()
                if ip in self.local and getattr(st, "label", "")}

    def _band_update(self, now):
        """Never let the alert footer break the lamp's display loop."""
        try:
            self._band_update_body(now)
        except Exception:
            if now - getattr(self, "_band_error_at", 0.0) > 60:
                self._band_error_at = now
                log_error("band alert display")

    def _band_update_body(self, now):
        master = "me" if self._i_am_master else getattr(self._master_peer, "id", None)
        try:
            self._contest_update(master, now)
        except Exception:
            if now - getattr(self, "_contest_error_at", 0.0) > 60:
                self._contest_error_at = now
                log_error("contest status display")
        if self._band_master is not None and master != self._band_master:
            self.band.reset()  # the master changed: forget the old alert
            self._band_master = None
        kind, text = self.band.footer(now, self._own_station_names())
        fresh = self.band.new_ids(now)
        if fresh and self.var_sound.get():
            self._band_sound()
        view = (kind, text)
        if view == self._band_view:
            return
        self._band_view = view
        label = self._band_footer()
        if not kind:
            label.pack_forget()
            return
        label.configure(text=text, bg=BAND_RED if kind == "violation" else BAND_GREY)
        if not label.winfo_ismapped():
            label.pack(fill=tk.X, pady=(2, 0))

    def _band_footer(self):
        if self._band_label is None:
            self._band_label = tk.Label(
                self.frame, text="", font=("Segoe UI", 9, "bold"), fg="white",
                bg=BAND_RED, wraplength=340, justify=tk.CENTER, padx=4, pady=2,
            )
        return self._band_label

    def _band_repack(self):
        """After the layout changed (compact mode): show the footer at the bottom again."""
        for row in (getattr(self, "_contest_label", None), getattr(self, "_activity_label", None)):
            if row is not None:
                row.pack_forget()
        self._contest_text = ""
        label = getattr(self, "_band_label", None)
        if label is not None:
            label.pack_forget()
        self._band_view = None

    def _band_sound(self):
        def beep():
            try:
                import winsound

                for freq in (880, 660, 880):
                    winsound.Beep(freq, 180)
            except Exception:
                pass  # no sound device / not Windows

        threading.Thread(target=beep, daemon=True).start()
