"""Test helpers: lamp and overview objects without a window.

The tests build DisplayApp / Overview with __new__ and set only the state
the code under test reads, so they run anywhere (also on GitHub's build
runner) without a screen. Tk variables are replaced by a tiny stand-in.
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import n1mm_lamps as m  # noqa: E402


class Var:
    """Stand-in for tk.BooleanVar / StringVar."""

    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Event:
    def __init__(self):
        self.count = 0

    def set(self):
        self.count += 1

    def is_set(self):
        return self.count > 0

    def clear(self):
        self.count = 0


class Root:
    """Stand-in for the Tk root: records after() calls."""

    def __init__(self):
        self.scheduled = []

    def after(self, ms, func=None):
        self.scheduled.append((ms, func))

    def winfo_rgb(self, color):
        table = {"red": (65535, 0, 0), "gold": (65535, 55255, 0), "white": (65535, 65535, 65535)}
        return table.get(color, (0, 0, 65535))


MY_IP = "10.0.0.1"


def make_lamp(**over):
    """A DisplayApp with the state __init__ would give it (no window)."""
    app = m.DisplayApp.__new__(m.DisplayApp)
    now = time.time()
    state = dict(
        root=Root(), port=12060, stale=5.0,
        local={MY_IP, "127.0.0.1"}, local_nets={MY_IP}, self_seen=set(),
        stations={}, peers={}, node_id="ME0001", hints={}, used_colors=set(), tracked=None,
        var_sound=Var(True), var_keys=Var(True), var_block=Var(True), var_relay=Var(True),
        var_role=Var("off"), var_auto=Var(False), var_check=Var(True), var_test=Var(False),
        _beeped=set(), f12_on=True, block_on=True, _block_active=False, _blocked_key=("", 0.0),
        _call_seen={}, _had_calls=False, _band_busy=None, _master_warn="", _last_ui_error=0.0,
        _port_users={}, _f12_refused=0.0, key_watcher=None,
        alert_on=False, alert_run=False, alert_time=0.0,
        urgent_on=False, urgent_fkey="F4", urgent_time=0.0, urgent_key=("", ""),
        _urgent_tx_seen=False, _shown_urgent=None, silenced=set(), _shown_call=None,
        hello_now=Event(), _share={"targets": [], "port": 46070}, relay_on=True,
        test_on=False, my_alias="", started=now - 3600, n1mm_master=None,
        color_map={}, block_limit=30.0, block_limit_run=5.0,
        _i_am_master=False, _master_peer=None, my_role="off",
        _slots={}, _pp_locked=False, _pp_holder=None, _pp_left=0,
        my_color="", my_color_time=0.0, _ui_calls=[], _busy=False, _status="",
        lock=threading.RLock(),
    )
    state.update(over)
    app.__dict__.update(state)
    app.events = []
    app._key_event = app.events.append
    return app


def station(app, ip, name, band="14", mode="CW", running=None, tx=False, freq=None):
    st = m.Station(ip, name, "")
    st.key = (band, mode) if band else None
    st.freq = freq or {"14": "1402500", "7": "701500", "21": "2102500"}.get(band, "")
    st.mode = mode
    st.running = running
    st.tx = tx
    st.last_seen = time.time()
    st.direct_seen = time.time()
    app.stations[ip] = st
    return st


def peer(app, node_id, ips, alias="", host=None, **attrs):
    p = m.Peer(node_id)
    p.ips = set(ips)
    p.alias = alias
    p.host = host or alias or node_id
    p.last_seen = time.time()
    p.same_pc = False
    p.version = m.__version__
    for k, v in attrs.items():
        setattr(p, k, v)
    app.peers[node_id] = p
    return p
