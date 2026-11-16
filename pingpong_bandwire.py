"""Bounded advisory watcher status; existing PingPong command authentication
and master-PC authorization must succeed BEFORE calling accept().
"""

import json
import math

VERSION = 1
TTL = 12.0
MAX_STATE = 1800


class WatchClient:
    def __init__(self, enabled=False):
        self.enabled = enabled
        self.authority = None
        self.state = None
        self.received = 0
        self.epochs = {}  # high-water marks survive master changes in this process
        self.seen_alerts = set()
        self.ring = False

    def accept(self, state, authority, now):
        try:
            if not authority or not isinstance(state, dict) or len(json.dumps(state).encode()) > MAX_STATE:
                return False
            if state['v'] != VERSION or not isinstance(state['enabled'], bool):
                return False
            started, issued, seq = state['started'], state['issued'], state['seq']
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                   for v in (started, issued, seq)):
                return False
            if issued < started or issued > now + 2 or now - issued > TTL or seq < 0 or int(seq) != seq:
                return False
            if not all(isinstance(state[k], str) and len(state[k]) <= 600
                       for k in ('boot', 'session', 'text')):
                return False
            ids = state['alerts']
            if not isinstance(ids, list) or len(ids) > 5 or not all(isinstance(i, str) and len(i) < 180 for i in ids):
                return False
            high = self.epochs.get(authority)
            if high:
                old_started, old_boot, old_seq = high
                if started < old_started or started == old_started and state['boot'] != old_boot:
                    return False
                if state['boot'] == old_boot and (started != old_started or seq <= old_seq):
                    return False
            if (not isinstance(state['urgent'], bool)
                    or not isinstance(state.get('breach', False), bool)):
                return False
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        self.epochs[authority] = (started, state['boot'], seq)
        self.enabled = state['enabled']
        self.authority, self.state, self.received = authority, dict(state), now
        alert_keys = {(state['session'], alert) for alert in ids}
        self.ring = self.enabled and state['urgent'] and bool(alert_keys - self.seen_alerts)
        self.seen_alerts.update(alert_keys)
        return True

    def text(self, authority, now):
        if not self.enabled:
            return ''
        if (not self.state or authority != self.authority or now - self.received > TTL
                or now - self.state['issued'] > TTL):
            return 'Band watcher unavailable'
        return self.state['text']

    def flash(self, authority, now):
        return bool(self.enabled and self.state and self.state.get('breach', False)
                    and authority == self.authority and now - self.received <= TTL
                    and now - self.state['issued'] <= TTL)

    def acknowledge(self):
        self.ring = False  # evidence stays visible; each event only rings once
