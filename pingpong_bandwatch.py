"""Central, advisory band-change rules. No GUI, radio control or network I/O.

Profiles are explicitly pinned to the verified 2026 CQ rules. A logical
signal is RUN/MULT or TX1/TX2, never the N1MM Run/S&P button or physical rig.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import time
import xml.etree.ElementTree as ET


YEAR = 2026
FAMILIES = ('CQ-WW-CW', 'CQ-WW-SSB', 'CQ-WW-RTTY',
            'CQ-WPX-CW', 'CQ-WPX-SSB', 'CQ-WPX-RTTY')
ALIASES = {name.replace('-', ''): name for name in FAMILIES}
ALIASES.update({'CQWW': 'CQ-WW', 'CQWPXCW': 'CQ-WPX-CW',
                'CQWPXSSB': 'CQ-WPX-SSB', 'CQWPXRTTY': 'CQ-WPX-RTTY'})
BANDS = {'1.8', '3.5', '7', '14', '21', '28'}


def contest_name(text):
    value = (text or '').strip().upper()
    return ALIASES.get(value.replace('-', ''), value)


def utc_stamp(value):
    return int(datetime.strptime(value.strip(), '%Y-%m-%d %H:%M:%S')
               .replace(tzinfo=timezone.utc).timestamp())


@dataclass(frozen=True)
class Profile:
    contest: str
    year: int
    category: str

    def __post_init__(self):
        if self.contest not in FAMILIES or self.year != YEAR or self.category not in ('MS', 'M2'):
            raise ValueError('Unsupported contest/year/category; select a verified 2026 profile')

    @property
    def timed(self):
        return self.category == 'MS' and self.contest in ('CQ-WW-CW', 'CQ-WW-SSB')

    @property
    def limit(self):
        return 10 if self.category == 'MS' and 'WPX' in self.contest else 8

    @property
    def signals(self):
        if self.category == 'M2':
            return ('TX1', 'TX2')
        return ('ENTRY',) if 'WPX' in self.contest else ('RUN', 'MULT')


@dataclass(frozen=True)
class QSO:
    id: str
    at: int
    band: str
    call: str
    signal: str
    source: str = ''
    radio: str = ''
    raw_signal: str = ''
    order: int = 0

    def __post_init__(self):
        if self.band not in BANDS or not self.id or not self.call or self.at < 0:
            raise ValueError('Invalid QSO identity, time, band or call')


def evaluate(profile, qsos, now):
    """Rebuild from QSO history. Crossing a UTC hour retains previous band.

    Timed checking uses Cabrillo clock minutes. Each first QSO on a new
    band starts its period, including a QSO that is itself a violation.
    """
    states, findings = {}, []
    for q in sorted((q for q in qsos if q.at <= now), key=lambda q: (q.at, q.order, q.id)):
        group = 'ENTRY' if profile.signals == ('ENTRY',) else q.signal
        if group not in profile.signals:
            continue
        minute, hour = q.at // 60 * 60, q.at // 3600 * 3600
        state = states.get(group)
        if state is None:
            states[group] = dict(signal=group, band=q.band, anchor=minute,
                                 anchor_exact=q.at + (59 if q.id.startswith('export:') else 0),
                                 hour=hour, changes=0, last=q.at)
            continue
        if state['hour'] != hour:
            state.update(hour=hour, changes=0)
        if q.band != state['band']:
            previous = state['band']
            state['changes'] += 1
            bad = minute < state['anchor'] + 600 if profile.timed else state['changes'] > profile.limit
            if bad:
                findings.append(dict(id=q.id + ':band', kind='logged-qso', at=q.at,
                                     signal=group, previous=previous, band=q.band,
                                     call=q.call, qso=q.id,
                                     detail=('change before {:02d}:{:02d} UTC'.format(
                                         (state['anchor'] + 600) // 3600 % 24,
                                         (state['anchor'] + 600) // 60 % 60)
                                             if profile.timed else 'change {}/{}'.format(
                                                 state['changes'], profile.limit))))
            state.update(band=q.band, anchor=minute,
                         anchor_exact=q.at + (59 if q.id.startswith('export:') else 0))
        state['last'] = q.at
    for group in profile.signals:
        state = states.setdefault(group, dict(signal=group, band='', anchor=0, anchor_exact=0,
                                              hour=now // 3600 * 3600, changes=0, last=0))
        if state['hour'] != now // 3600 * 3600:
            state.update(hour=now // 3600 * 3600, changes=0)
        state['remaining'] = (max(0, state['anchor'] + 600 - now)
                              if profile.timed and state['band'] else 0)
        state['operational_remaining'] = (max(0, state['anchor_exact'] + 600 - now)
                                          if profile.timed and state['band'] else 0)
        state['limit'] = None if profile.timed else profile.limit
        state['left'] = None if profile.timed else max(0, profile.limit - state['changes'])
        state['resets'] = now // 3600 * 3600 + 3600
    return list(states.values()), findings


def contact_packet(data, root=None):
    """Parse documented N1MM contact events; preserve role metadata as raw.

    run1run2 is documented for Multi-Two, but its encoding and MS meaning
    are not assumed. Assignment must be confirmed in watcher configuration.
    """
    if root is None:
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return None
    kind = root.tag.lower()
    if kind not in ('contactinfo', 'contactreplace', 'contactdelete', 'appinfo'):
        return None
    fields = {e.tag.lower(): (e.text or '').strip() for e in root}
    fields['kind'] = kind
    return fields


def cabrillo(text, profile, entry):
    """Read a complete CQ-format export, without touching N1MM's database.

    Cabrillo transmitter numbers are specified by CQ: MS 0=RUN, 1=MULT;
    M2 0=TX1, 1=TX2. Reject missing/ambiguous assignment instead of guessing.
    """
    headers, rows = {}, []
    for line in text.splitlines():
        name, sep, value = line.partition(':')
        if not sep:
            continue
        name, value = name.strip().upper(), value.strip()
        if name == 'QSO':
            rows.append(value.split())
        elif name != 'X-QSO':
            headers[name] = value.upper()
    if 'END-OF-LOG' not in headers or 'START-OF-LOG' not in headers:
        raise ValueError('A complete Cabrillo export with START/END-OF-LOG is required')
    if headers.get('CALLSIGN') != entry.upper() or contest_name(headers.get('CONTEST')) != profile.contest:
        raise ValueError('Export callsign/contest differs from selected entry')
    expected = 'ONE' if profile.category == 'MS' else 'TWO'
    if headers.get('CATEGORY-OPERATOR') != 'MULTI-OP' or headers.get('CATEGORY-TRANSMITTER') != expected:
        raise ValueError('Export category differs from selected profile')
    result = []
    occurrences = {}
    for number, row in enumerate(rows):
        count = 12 if profile.contest == 'CQ-WW-RTTY' else 10
        if len(row) not in (count, count + 1):
            raise ValueError('Unsupported CQ Cabrillo QSO layout at row {}'.format(number + 1))
        freq, mode, date, clock, own = row[:5]
        call = row[8] if count == 12 else row[7]
        if own.upper() != entry.upper():
            raise ValueError('QSO entry callsign mismatch')
        at = utc_stamp('{} {}:{}:00'.format(date, clock[:2], clock[2:]))
        if datetime.fromtimestamp(at, timezone.utc).year != profile.year:
            raise ValueError('QSO year differs from rule profile')
        allowed_mode = {'CW': 'CW', 'SSB': 'PH', 'RTTY': 'RY'}[profile.contest.rsplit('-', 1)[1]]
        if mode != allowed_mode:
            raise ValueError('QSO mode differs from selected contest')
        khz = float(freq)
        band = next((band for lo, hi, band in ((1800, 2000, '1.8'), (3500, 4000, '3.5'),
                    (7000, 7300, '7'), (14000, 14350, '14'), (21000, 21450, '21'),
                    (28000, 29700, '28')) if lo <= khz < hi), None)
        if band is None or band == '1.8' and profile.contest.endswith('RTTY'):
            raise ValueError('Unsupported contest band')
        signal = 'ENTRY'
        if profile.signals != ('ENTRY',):
            tx = row[count] if len(row) == count + 1 else ''
            if tx not in ('0', '1'):
                raise ValueError('Cabrillo transmitter identifier 0/1 is required')
            signal = profile.signals[int(tx)]
        fingerprint = '{}:{}:{}:{}:{}'.format(at, band, call.upper(), signal, mode)
        occurrence = occurrences.get(fingerprint, 0)
        occurrences[fingerprint] = occurrence + 1
        identity = hashlib.sha256((fingerprint + ':' + str(occurrence)).encode()).hexdigest()
        result.append(QSO('export:' + identity, at, band, call.upper(), signal, source='@cabrillo', order=number))
    return result


class WatchLog:
    """Durable central log with edit history, immutable assignment snapshots.

    All methods are called under the owning Overview/service lock. UDP has
    no replay/ack: recovery is explicitly incomplete until a full export is
    reconciled, and even then live delivery remains unverified.
    """

    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS qsos (id TEXT PRIMARY KEY, body TEXT);
            CREATE TABLE IF NOT EXISTS aliases (rawid TEXT PRIMARY KEY, canonical TEXT);
            CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY, at REAL, kind TEXT, body TEXT);
            CREATE TABLE IF NOT EXISTS findings (id TEXT PRIMARY KEY, body TEXT, active INTEGER,
                                                  acknowledged INTEGER DEFAULT 0);
        ''')
        self._qsos = {q.id: q for q in (QSO(**json.loads(row[0]))
                      for row in self.db.execute('SELECT body FROM qsos'))}
        self._aliases = dict(self.db.execute('SELECT rawid,canonical FROM aliases'))
        self._evaluation = None
        self.profile = None
        self.entry = ''
        self.pending = self.get('pending', {})
        self.suspended = self.get('suspended', False)
        self.health = 'history incomplete — import full log'
        config = self.get('profile')
        if config:
            self.profile = Profile(**config)
            self.entry = self.get('entry', '')
            self.health = 'reconciliation required after restart'

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM config WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO config VALUES (?,?)', (key, json.dumps(value)))

    def audit(self, kind, body, now):
        self.db.execute('INSERT INTO audit(at,kind,body) VALUES (?,?,?)',
                        (now, kind, json.dumps(body)))

    def configure(self, profile, entry, mappings, session):
        if not entry.strip() or not session.strip():
            raise ValueError('Entry callsign and contest session are required')
        for mapping in mappings:
            if mapping.get('signal') not in profile.signals or not mapping.get('source'):
                raise ValueError('Every source mapping requires a valid logical signal')
            if not isinstance(mapping.get('from'), (int, float)):
                raise ValueError('Assignment effective UTC time is required')
        old = self.get('session')
        with self.db:
            if old != session:
                # Preserve the previous complete history in the audit before isolation.
                self.audit('session-archive', dict(session=old, profile=self.get('profile'),
                           entry=self.entry, qsos=[asdict(q) for q in self.qsos()],
                           findings=self.findings()), time.time())
                self.db.execute('DELETE FROM qsos')
                self._qsos.clear()
                self._aliases.clear()
                self.db.execute('DELETE FROM aliases')
                self._evaluation = None
                self.db.execute('DELETE FROM findings')
                self.pending.clear()
                self.put('snapshot_at', None)
                self.put('tombstones', [])
                self.put('pending', {})
            elif self.profile and (self.profile != profile or self.entry != entry.upper()):
                raise ValueError('Changing entry/profile requires a new contest session')
            self.profile, self.entry = profile, entry.upper()
            self.put('profile', asdict(profile))
            self.put('entry', self.entry)
            self.put('session', session)
            self.put('mappings', mappings)
            self.suspended = False
            self.put('suspended', False)
            self.audit('configure', dict(profile=asdict(profile), entry=self.entry,
                                        mappings=mappings, session=session), time.time())
        self.health = 'history incomplete — reconcile selected session'

    def suspend(self, reason):
        self.suspended = True
        self.health = reason
        with self.db:
            self.put('suspended', True)

    def assign(self, fields, at):
        if self.profile.signals == ('ENTRY',):
            return 'ENTRY'
        # A mapping may select a physical radio and/or a confirmed raw role.
        choices = [item for item in self.get('mappings', [])
                   if item['source'].upper() == fields.get('stationname', '').upper()
                   and item['from'] <= at
                   and (not item.get('radio') or str(item['radio']) == fields.get('radionr'))
                   and (item.get('raw') in (None, '') or str(item['raw']) == fields.get('run1run2'))]
        if not choices:
            return ''
        latest = max(item['from'] for item in choices)
        signals = {item['signal'] for item in choices if item['from'] == latest}
        return next(iter(signals)) if len(signals) == 1 else ''

    def qsos(self):
        return list(self._qsos.values())

    def _save_qso(self, q):
        self._qsos[q.id] = q
        self._evaluation = None
        self.db.execute('INSERT OR REPLACE INTO qsos VALUES (?,?)', (q.id, json.dumps(asdict(q))))

    def _bind_alias(self, rawid, canonical):
        if rawid and rawid != canonical:
            self._aliases[rawid] = canonical
            self.db.execute('INSERT OR REPLACE INTO aliases VALUES (?,?)', (rawid, canonical))

    def ingest(self, fields, now):
        if not self.profile:
            return False
        deletion = (fields.get('kind') == 'contactdelete'
                    or fields.get('isclaimedqso', '1').lower() in ('0', 'false'))
        if ((fields.get('contestname') or not deletion)
                and contest_name(fields.get('contestname')) != self.profile.contest
                or (fields.get('mycall') or not deletion)
                and fields.get('mycall', '').upper() != self.entry):
            self.suspend('contest/entry mismatch — select and reconcile the correct session')
            return False
        if self.suspended:
            return False
        try:
            at = utc_stamp(fields['timestamp'])
            if datetime.fromtimestamp(at, timezone.utc).year != self.profile.year:
                self.suspend('unsupported QSO year/session — select a verified profile')
                return False
            identity = fields.get('id', '')
            if not identity and not deletion:
                raise ValueError('QSO ID missing')
            rawid = self.get('session') + ':' + identity if identity else ''
            key = self._aliases.get(rawid, rawid)
            if key in self.get('tombstones', []) and fields['kind'] == 'contactinfo':
                return False
            old = self._qsos.get(key)
            if old is None:
                lookup_at = utc_stamp(fields.get('oldtimestamp') or fields['timestamp'])
                lookup_call = (fields.get('oldcall') or fields.get('call', '')).upper()
                matches = [q for q in self.qsos()
                           if (q.at == lookup_at or q.source == '@cabrillo' and q.at // 60 == lookup_at // 60)
                           and q.call == lookup_call
                           and (not identity or q.source == '@cabrillo')
                           and (fields['kind'] != 'contactinfo' or deletion
                                or q.band == fields.get('band', '').replace(',', '.'))]
                if len(matches) > 1:
                    raise ValueError('Ambiguous export/live identity')
                if matches:
                    old, key = matches[0], matches[0].id
                elif fields['kind'] == 'contactreplace' or deletion:
                    raise ValueError('Unknown edit/delete identity; reconcile full log')
            if deletion:
                recent = self.get('replacements', {}).get(key, 0)
                if 0 <= now - recent < 2:
                    self.health = 'out-of-order edit/delete — reconciliation required'
                    return False
                self.pending[key] = now + 2
                with self.db:
                    self._bind_alias(rawid, key)
                    self.put('pending', self.pending)
                    self.audit('contactdelete-pending', dict(id=key), now)
                return True
            signal = old.signal if old else self.assign(fields, at)
            q = QSO(key, at, fields['band'].replace(',', '.'), fields['call'].upper(),
                    signal, fields.get('stationname', ''), fields.get('radionr', ''),
                    fields.get('run1run2', ''), order=old.order if old else 0)
        except (KeyError, ValueError):
            self.health = 'invalid, ambiguous or incomplete contact — reconciliation required'
            return False
        if old and old.source != '@cabrillo' and fields['kind'] == 'contactinfo' and old != q:
            self.health = 'conflicting contact replay — reconciliation required'
            return False
        self.pending.pop(key, None)
        with self.db:
            self._bind_alias(rawid, key)
            self.put('pending', self.pending)
            if fields['kind'] == 'contactreplace':
                recent = self.get('replacements', {})
                recent[key] = now
                self.put('replacements', {k: v for k, v in recent.items() if now - v < 60})
            if old != q:
                self._save_qso(q)
                self.audit(fields['kind'], asdict(q), now)
        if not signal:
            self.health = 'unassigned logical signal — reconciliation required'
        return True

    def flush(self, now):
        removed = [key for key, deadline in self.pending.items() if now >= deadline]
        with self.db:
            for key in removed:
                self.db.execute('DELETE FROM qsos WHERE id=?', (key,))
                self._qsos.pop(key, None)
                self._evaluation = None
                self.audit('contactdelete', dict(id=key), now)
                del self.pending[key]
            if removed:
                self.put('pending', self.pending)
                self.put('tombstones', list(set(self.get('tombstones', []) + removed)))

    def reconcile(self, text, now, captured_at=None):
        if captured_at is not None:
            if captured_at > now + 2 or self.db.execute(
                    "SELECT 1 FROM audit WHERE at>? AND kind IN "
                    "('contactinfo','contactreplace','contactdelete','contactdelete-pending','signal-correction') LIMIT 1",
                    (captured_at,)).fetchone():
                raise ValueError('Log changed after export. Pause station logging, export again, and import before resuming.')
        if not self.profile:
            raise ValueError('Configure the watcher first')
        rows = cabrillo(text, self.profile, self.entry)
        with self.db:
            previous = self.qsos()
            old_aliases = dict(self._aliases)
            self.audit('before-reconcile', dict(qsos=[asdict(q) for q in previous], aliases=old_aliases), now)
            self.db.execute('DELETE FROM aliases')
            self._aliases.clear()
            by_contact = {}
            reverse_aliases = {}
            for rawid, canonical in old_aliases.items():
                reverse_aliases.setdefault(canonical, []).append(rawid)
            for q in rows:
                by_contact.setdefault((q.at // 60, q.call, q.band), []).append(q)
            for old in previous:
                matches = by_contact.get((old.at // 60, old.call, old.band), [])
                if len(matches) == 1:
                    for rawid in [old.id] + reverse_aliases.get(old.id, []):
                        self._bind_alias(rawid, matches[0].id)
            self.db.execute('DELETE FROM qsos')
            self._qsos.clear()
            self._evaluation = None
            for q in rows:
                self._save_qso(q)
            self.pending.clear()
            self.put('pending', {})
            self.put('tombstones', [])
            self.put('snapshot_at', now)
            self.audit('reconcile', dict(rows=len(rows), at=now), now)
        self.health = 'snapshot reconciled; live UDP delivery unverified'

    def correct_signal(self, identity, signal, now):
        if not self.profile or signal not in self.profile.signals:
            raise ValueError('Invalid logical signal')
        q = next(q for q in self.qsos() if q.id == identity)
        with self.db:
            self._save_qso(QSO(**dict(asdict(q), signal=signal)))
            self.audit('signal-correction', dict(id=identity, signal=signal), now)

    def findings(self):
        return [dict(json.loads(body), active=bool(active), acknowledged=bool(ack))
                for body, active, ack in self.db.execute('SELECT body,active,acknowledged FROM findings')]

    def acknowledge(self):
        with self.db:
            self.db.execute('UPDATE findings SET acknowledged=1')
            self.audit('acknowledge', {'findings': [f['id'] for f in self.findings() if f['active']]}, time.time())

    def snapshot(self, now):
        self.flush(now)
        if not self.profile:
            return dict(health='unsupported/unconfigured rules', signals=[], findings=[])
        fresh = (self._evaluation is None or self._evaluation[0] != now // 60
                 or now >= self._evaluation[3])
        if fresh:
            signals, findings = evaluate(self.profile, self.qsos(), now)
            next_qso = min((q.at for q in self.qsos() if q.at > now), default=float('inf'))
            self._evaluation = (now // 60, signals, findings, next_qso)
        else:
            _, signals, findings, _ = self._evaluation
        signals = [dict(s, remaining=max(0, s['anchor'] + 600 - now)
                        if self.profile.timed and s['band'] else 0,
                        operational_remaining=max(0, s['anchor_exact'] + 600 - now)
                        if self.profile.timed and s['band'] else 0) for s in signals]
        if not self.pending and fresh:
            with self.db:
                old_states = {identity: (json.loads(body), active, ack) for identity, body, active, ack
                              in self.db.execute('SELECT id,body,active,acknowledged FROM findings')}
                self.db.execute('UPDATE findings SET active=0 WHERE json_extract(body,\'$.kind\')=\'logged-qso\'')
                for finding in findings:
                    previous = old_states.get(finding['id'])
                    if previous and previous[0] != finding:
                        self.audit('finding-revision', dict(previous=previous[0], revised=finding), now)
                    ack = previous[2] if previous and previous[1] and previous[0] == finding else 0
                    self.db.execute('INSERT INTO findings(id,body,active,acknowledged) VALUES (?,?,1,?) '
                                    'ON CONFLICT(id) DO UPDATE SET body=excluded.body,active=1,acknowledged=excluded.acknowledged',
                                    (finding['id'], json.dumps(finding), ack))
        return dict(health=('contest/session suspended — configure and reconcile' if self.suspended else
                           'log edit pending' if self.pending else self.health),
                    suspended=self.suspended,
                    contest=self.profile.contest, year=self.profile.year,
                    category=self.profile.category, entry=self.entry,
                    session=self.get('session'), snapshot_at=self.get('snapshot_at'),
                    signals=[] if self.suspended else signals,
                    findings=[dict(f, active=False, suppressed=True) if self.suspended else f
                              for f in self.findings()],
                    coverage='Band-change timing/counts only; multiplier eligibility and CQ solicitation unchecked')

    def observe_tx(self, source, fields, band, now):
        """Observed early TX only. Tuning and focus do not call this method."""
        if not self.profile or not self.profile.timed or self.pending or self.suspended:
            return
        signal = self.assign(dict(fields, stationname=source), now)
        signals = self.snapshot(now)['signals']
        state = next((s for s in signals if s['signal'] == signal), None)
        if not state or not state['band'] or band == state['band'] or not state['operational_remaining']:
            return
        identity = 'tx:{}:{}:{}'.format(signal, state['anchor'], band)
        finding = dict(id=identity, kind='observed-tx', at=now, signal=signal,
                       previous=state['band'], band=band,
                       detail='N1MM TX during conservative band hold; confirm against log-minute timing; manual PTT not covered')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO findings(id,body,active) VALUES (?,?,1)',
                            (identity, json.dumps(finding)))
