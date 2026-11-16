"""Overview's central watcher: dedicated N1MM input, UI and advisory summaries.

Lamps never ingest the QSO ledger. N1MM Contacts, Application Info and Radio
must be sent directly from every participating PC to master-IP:12063.
"""

import hashlib
import ipaddress
import json
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import pingpong_bandwatch as bw
import pingpong_net as net
from pingpong_bandwire import MAX_STATE


WATCH_PORT = 12063


class BandWatcherMixin:
    def _watch_init(self):
        self.watch_lock = threading.RLock()
        self.watch_error = ''
        self.watch_storage_error = False
        try:
            self.watch_log = bw.WatchLog(os.path.join(self._watch_dir(), 'bandwatch.sqlite'))
        except (OSError, sqlite3.DatabaseError, ValueError) as exc:
            self.watch_log = bw.WatchLog(':memory:')
            with self.watch_log.db:
                self.watch_log.put('enabled', True)
            self.watch_storage_error = True
            self.watch_error = 'Watcher storage unavailable — restore ledger/permissions and restart: ' + str(exc)
        self.watch_boot = uuid.uuid4().hex
        self.watch_started = time.time()
        self.watch_seq = 0
        self.watch_sent = 0
        self.watch_seen = {}
        self.watch_detected = ''
        self.watch_view = {}
        self.watch_next_ui = 0
        self.watch_announced = set()

    @staticmethod
    def _watch_dir():
        from pingpong_overview_common import app_dir
        return app_dir()

    def _watch_authoritative(self, now):
        # Fail closed, including multiple masters and local interface errors.
        with self.lock:
            masters = self._masters(now)
            if len(masters) != 1:
                return False
            try:
                local = {ip for ip, _ in net.local_networks()} | {'127.0.0.1'}
            except Exception:
                return False
            return bool(set(masters[0].hello.get('ips', [])) & local)

    def _watch_listen(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(('0.0.0.0', WATCH_PORT))
            sock.settimeout(.5)
            while not self.stop.is_set():
                try:
                    data, addr = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                if self._watch_authoritative(time.time()):
                    try:
                        with self.watch_lock:
                            self._watch_packet(addr[0], data, time.time())
                    except Exception as exc:
                        self.watch_error = 'Watcher input error — reconciliation required: ' + str(exc)
        except OSError as exc:
            self.watch_error = 'Watcher UDP 12063: ' + str(exc)
        finally:
            sock.close()

    def _watch_packet(self, ip, data, now):
        log = self.watch_log
        if not log.get('enabled', False):
            return
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            return
        fields = bw.contact_packet(data, root=root) or {e.tag.lower(): (e.text or '').strip() for e in root}
        source = fields.get('stationname', '').upper()
        if not source:
            candidates = {item['source'].upper() for item in log.get('mappings', [])
                          if item.get('ip') == ip}
            if len(candidates) == 1:
                source = next(iter(candidates))
                fields['stationname'] = source
        if not any(item['source'].upper() == source and item.get('ip') == ip
                   for item in log.get('mappings', [])):
            if root.tag.lower() in ('contactinfo', 'contactreplace', 'contactdelete') and any(
                    item.get('ip') == ip for item in log.get('mappings', [])):
                log.health = 'ambiguous station identity — reconciliation required'
            return  # N1MM UDP itself is unsigned: explicit source-IP allow list
        kind = root.tag.lower()
        if kind not in ('appinfo', 'radioinfo', 'contactinfo', 'contactreplace', 'contactdelete'):
            return
        self.watch_seen[source] = now
        if fields.get('mycall') and fields['mycall'].upper() != log.entry:
            log.suspend('entry changed — configure and reconcile the correct session')
            return
        if kind == 'appinfo':
            self.watch_detected = bw.contest_name(fields.get('contestname'))
            if log.profile and self.watch_detected != log.profile.contest:
                log.suspend('contest changed — configure and reconcile the correct session')
            return
        if kind in ('contactinfo', 'contactreplace', 'contactdelete'):
            if fields.get('contestname'):
                self.watch_detected = bw.contest_name(fields['contestname'])
            fields['kind'] = kind
            log.ingest(fields, now)
        elif kind == 'radioinfo':
            from pingpong_common import EchoFilter, group_of, clean_caption
            nr, active = fields.get('radionr', '1'), fields.get('activeradionr')
            if nr not in ('1', '2'):
                return
            txfreq = fields.get('txfreq', '')
            freq = txfreq if re.fullmatch(r"0*[1-9][0-9]*", txfreq) else fields.get('freq')
            group = group_of(freq, fields.get('mode', '').upper())
            if not hasattr(self, 'watch_echo'):
                self.watch_echo = EchoFilter()
            tx = fields.get('istransmitting', '').lower() == 'true' and (not active or active == nr)
            tx, _ = self.watch_echo.filter((source, nr), tx, group,
                            clean_caption(fields.get('functionkeycaption', '')), now)
            if tx and group:
                log.observe_tx(source, dict(fields, radionr=nr), group[0], now)

    def _watch_tick(self, now):
        if not hasattr(self, 'watch_log'):
            return None
        authority = self._watch_authoritative(now)
        with self.watch_lock:
            log = self.watch_log
            enabled = log.get('enabled', False)
            if enabled:
                if log.profile and time.gmtime(now).tm_year != log.profile.year:
                    log.suspend('unsupported current rule year — select a verified profile')
                expected = {item['source'].upper() for item in log.get('mappings', [])}
                missing = sorted(name for name in expected if now - self.watch_seen.get(name, 0) > 35)
                if missing:
                    log.health = 'feed missing: {} — reconciliation required'.format(', '.join(missing))
                if not expected:
                    log.health = 'source configuration incomplete'
            view = log.snapshot(now)
            view['enabled'] = enabled
            view['authority'] = authority
            if not authority:
                view['health'] = 'Band watcher unavailable — Overview is not on the unique master PC'
            if self.watch_error:
                view['health'] = self.watch_error
            self.watch_view = view
        if hasattr(self, 'watch_status') and now >= self.watch_next_ui:
            self.watch_next_ui = now + 1
            self._watch_render(view, now)
        if authority and log.get('enabled', None) is not None and now - self.watch_sent >= 3:
            self.watch_sent = now
            self.watch_seq += 1
            with self.lock:
                lamps = list(self.lamps.values())
            for lamp in lamps:
                source = (lamp.n1mm_name or lamp.hello.get('host', '')).upper()
                with self.watch_lock:
                    ips = set(lamp.hello.get('ips', []))
                    signals = {m['signal'] for m in log.get('mappings', [])
                               if (m['source'].upper() == source or m.get('ip') in ips)
                               and m['from'] <= now}
                wire = self._watch_wire(view, signals, now)
                self._send_command(net.make_command('bandwatch', to=lamp.id, state=wire))
        return view

    def _watch_wire(self, view, signals, now):
        active = sorted((f for f in view.get('findings', []) if f['active']), key=lambda f: (f['at'], f['id']))
        alerts = [f['id'] for f in active if not f['acknowledged']][-5:]
        text = 'Band watcher: ' + view['health']
        relevant = [s for s in view.get('signals', []) if not signals or s['signal'] in signals]
        for s in relevant:
            if s['limit'] is None:
                detail = 'conservative hold {:02d}:{:02d}'.format(int(s['operational_remaining']) // 60, int(s['operational_remaining']) % 60)
            else:
                detail = '{}/{} changes; resets {} UTC'.format(s['changes'], s['limit'],
                                     time.strftime('%H:%M', time.gmtime(s['resets'])))
            text += ' · {} {} MHz: {}'.format(s['signal'], s['band'] or '?', detail)
        if active:
            logged = [f for f in active if f['kind'] == 'logged-qso' and not f['acknowledged']]
            finding = (logged or active)[-1]
            text = ('YOUR SIGNAL · ' if any(f['signal'] in signals for f in active) else '') + '{} · {} · {} UTC · {} → {} MHz · {} · '.format(
                'LOGGED BREACH' if finding['kind'] == 'logged-qso' else 'POSSIBLE EARLY TX',
                finding['signal'], time.strftime('%m-%d %H:%M', time.gmtime(finding['at'])),
                finding['previous'], finding['band'], finding['detail']) + text
        wire = dict(v=1, boot=self.watch_boot, started=self.watch_started, seq=self.watch_seq,
                    issued=now, session=hashlib.sha256((view.get('session') or 'unconfigured').encode()).hexdigest(),
                    enabled=view['enabled'], text=text[:600],
                    alerts=[hashlib.sha256(a.encode()).hexdigest()[:24] for a in alerts],
                    urgent=any(f['signal'] in signals and not f['acknowledged'] for f in active),
                    breach=any(f['kind'] == 'logged-qso' and not f['acknowledged'] for f in active))
        while len(json.dumps(wire).encode()) > MAX_STATE:
            wire['text'] = wire['text'][:-50]
        return wire

    def _watch_build(self):
        shell = tk.Frame(self.book)
        self.book.add(shell, text='Band watcher')
        canvas = tk.Canvas(shell, highlightthickness=0)
        vertical = ttk.Scrollbar(shell, orient='vertical', command=canvas.yview)
        horizontal = ttk.Scrollbar(shell, orient='horizontal', command=canvas.xview)
        canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        vertical.pack(side='right', fill='y')
        horizontal.pack(side='bottom', fill='x')
        canvas.pack(side='left', fill='both', expand=True)
        frame = tk.Frame(canvas, padx=6, pady=6)
        window = canvas.create_window(0, 0, window=frame, anchor='nw')
        frame.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window,
                    width=max(e.width, frame.winfo_reqwidth())))
        self.watch_status = tk.Label(frame, anchor='w', justify='left', wraplength=850)
        self.watch_status.pack(fill='x')
        self.watch_fields = {}
        form = tk.Frame(frame)
        form.pack(fill='x', pady=5)
        log = self.watch_log
        values = dict(contest=log.profile.contest if log.profile else bw.FAMILIES[0],
                      year=str(log.profile.year if log.profile else bw.YEAR),
                      category=log.profile.category if log.profile else 'MS',
                      entry=log.entry, session=log.get('session', ''))
        for col, (name, label, choices) in enumerate((
                ('contest', 'Contest', bw.FAMILIES), ('year', 'Rule year', ('2026',)),
                ('category', 'Category', ('MS', 'M2')), ('entry', 'Entry callsign', None),
                ('session', 'Contest session name', None))):
            tk.Label(form, text=label).grid(row=0, column=col, sticky='w')
            var = tk.StringVar(value=values[name])
            self.watch_fields[name] = var
            widget = (ttk.Combobox(form, textvariable=var, values=choices, state='readonly', width=17)
                      if choices else tk.Entry(form, textvariable=var, width=20))
            widget.grid(row=1, column=col, padx=3, sticky='ew')
        tk.Label(frame, text='Assignments apply to new QSOs from the effective UTC time. '
                 'Several computers may share one signal. Run/S&P is not the contest signal.',
                 anchor='w', wraplength=850).pack(fill='x')
        self.watch_map_rows = list(log.get('mappings', []))
        self.watch_maps = ttk.Treeview(frame, columns=('source', 'ip', 'radio', 'raw', 'signal', 'from'),
                                      show='headings', height=3)
        for cid, label in zip(self.watch_maps['columns'],
                             ('N1MM station', 'Sender IP', 'Radio (optional)', 'N1MM role value (optional)', 'Signal', 'Effective UTC')):
            self.watch_maps.heading(cid, text=label)
            self.watch_maps.column(cid, width=135)
        self.watch_maps.pack(fill='x')
        editor = tk.Frame(frame)
        editor.pack(fill='x')
        self.watch_map_fields = {}
        for col, name in enumerate(('source', 'ip', 'radio', 'raw', 'signal', 'from')):
            var = tk.StringVar(value=time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime()) if name == 'from' else '')
            self.watch_map_fields[name] = var
            if name == 'signal':
                widget = ttk.Combobox(editor, textvariable=var, values=('ENTRY', 'RUN', 'MULT', 'TX1', 'TX2'), width=9)
            else:
                widget = tk.Entry(editor, textvariable=var, width=17)
            widget.grid(row=0, column=col, padx=2)
        buttons = tk.Frame(frame)
        buttons.pack(fill='x', pady=5)
        for label, command in (('Add assignment', self._watch_add_mapping),
                               ('Remove selected assignment', self._watch_remove_mapping),
                               ('Save and enable', self._watch_configure),
                               ('Disable', self._watch_disable),
                               ('Import complete Cabrillo…', self._watch_import),
                               ('Acknowledge alerts', self._watch_ack),
                               ('Export audit…', self._watch_export),
                               ('Allow watcher firewall…', self._watch_firewall)):
            tk.Button(buttons, text=label, command=command).pack(side='left', padx=2)
        self.watch_summary = tk.Label(frame, anchor='w', justify='left', wraplength=850)
        self.watch_summary.pack(fill='x')
        self.watch_history = ttk.Treeview(frame, columns=('time', 'signal', 'band', 'detail', 'state'),
                                        show='headings', height=5)
        for cid in self.watch_history['columns']:
            self.watch_history.heading(cid, text=cid.title())
            self.watch_history.column(cid, width=150 if cid != 'detail' else 350)
        self.watch_history.pack(fill='both', expand=True)
        correction = tk.Frame(frame)
        correction.pack(fill='x')
        tk.Label(correction, text='Correct logged QSO signal:').pack(side='left')
        self.watch_qso_id = ttk.Combobox(correction, width=60, state='readonly')
        self.watch_qso_id.pack(side='left')
        self.watch_correct_signal = ttk.Combobox(correction, values=('ENTRY', 'RUN', 'MULT', 'TX1', 'TX2'), width=8)
        self.watch_correct_signal.pack(side='left')
        tk.Button(correction, text='Apply correction', command=self._watch_correct).pack(side='left')
        self._watch_render_maps()

    def _watch_guard(self):
        if getattr(self, 'watch_storage_error', False):
            messagebox.showerror('Band watcher', self.watch_error, parent=self.root)
            return False
        if not self._watch_authoritative(time.time()):
            messagebox.showerror('Band watcher', 'Run Overview on the unique master PC.', parent=self.root)
            return False
        return True

    def _watch_firewall(self):
        if sys.platform != 'win32':
            messagebox.showinfo('Band watcher', 'Allow inbound UDP 12063 in the master PC firewall.', parent=self.root)
            return
        import ctypes
        rule = ('netsh advfirewall firewall add rule name="PingPong band watcher" '
                'dir=in action=allow protocol=UDP localport=12063 profile=private,domain')
        result = ctypes.windll.shell32.ShellExecuteW(None, 'runas', 'cmd.exe', '/c ' + rule, None, 0)
        if result <= 32:
            messagebox.showerror('Band watcher', 'Firewall request was not started.', parent=self.root)
        else:
            self.root.after(2500, lambda: messagebox.showinfo('Band watcher',
                'UDP 12063 firewall rule found.' if net.firewall_rule_exists('PingPong band watcher')
                else 'Rule not confirmed yet. Check Windows Firewall before relying on the feed.', parent=self.root))

    def _watch_render_maps(self):
        self.watch_maps.delete(*self.watch_maps.get_children())
        for i, item in enumerate(self.watch_map_rows):
            self.watch_maps.insert('', 'end', iid=str(i), values=(item['source'], item.get('ip', ''),
                item.get('radio', ''), item.get('raw', ''), item['signal'],
                time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(item['from']))))

    def _watch_add_mapping(self):
        try:
            fields = {k: v.get().strip() for k, v in self.watch_map_fields.items()}
            ipaddress.IPv4Address(fields['ip'])
            fields['from'] = bw.utc_stamp(fields['from'])
            if fields['radio'] not in ('', '1', '2'):
                raise ValueError('Radio must be blank, 1 or 2')
            if not fields['source'] or fields['signal'] not in ('ENTRY', 'RUN', 'MULT', 'TX1', 'TX2'):
                raise ValueError('Select a station and logical signal')
            self.watch_map_rows.append(fields)
            self._watch_render_maps()
        except ValueError as exc:
            messagebox.showerror('Band watcher', str(exc), parent=self.root)

    def _watch_remove_mapping(self):
        selected = self.watch_maps.selection()
        if selected:
            del self.watch_map_rows[int(selected[0])]
            self._watch_render_maps()

    def _watch_configure(self):
        if not self._watch_guard():
            return
        try:
            f = {k: v.get().strip() for k, v in self.watch_fields.items()}
            profile = bw.Profile(f['contest'], int(f['year']), f['category'])
            with self.watch_lock:
                self.watch_log.configure(profile, f['entry'], self.watch_map_rows, f['session'])
                with self.watch_log.db:
                    self.watch_log.put('enabled', True)
                self.watch_seen.clear()
        except (ValueError, TypeError) as exc:
            messagebox.showerror('Band watcher', str(exc), parent=self.root)

    def _watch_disable(self):
        if self._watch_guard():
            with self.watch_lock, self.watch_log.db:
                self.watch_log.put('enabled', False)

    def _watch_import(self):
        if not self._watch_guard():
            return
        path = filedialog.askopenfilename(parent=self.root, title='Complete N1MM Cabrillo export',
                                          filetypes=[('Cabrillo log', '*.log *.cbr *.txt'), ('All files', '*')])
        if not path:
            return
        try:
            with open(path, encoding='utf-8-sig') as stream:
                text = stream.read()
            with self.watch_lock:
                self.watch_log.reconcile(text, time.time(), captured_at=os.path.getmtime(path))
        except (OSError, ValueError) as exc:
            messagebox.showerror('Band watcher', str(exc), parent=self.root)

    def _watch_export(self):
        path = filedialog.asksaveasfilename(parent=self.root, title='Export watcher evidence',
                                          defaultextension='.json', filetypes=[('JSON audit', '*.json')])
        if not path:
            return
        try:
            with self.watch_lock:
                audit = [dict(sequence=n, at=at, kind=kind, evidence=json.loads(body))
                         for n, at, kind, body in self.watch_log.db.execute('SELECT seq,at,kind,body FROM audit ORDER BY seq')]
                result = dict(status=self.watch_view, audit=audit,
                              qsos=[bw.asdict(q) for q in self.watch_log.qsos()])
            with open(path, 'w', encoding='utf-8') as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
        except OSError as exc:
            messagebox.showerror('Band watcher', str(exc), parent=self.root)

    def _watch_ack(self):
        if self._watch_guard():
            with self.watch_lock:
                self.watch_log.acknowledge()

    def _watch_correct(self):
        if not self._watch_guard():
            return
        try:
            identity = self.watch_qso_id.get().split(' | ', 1)[0]
            with self.watch_lock:
                self.watch_log.correct_signal(identity, self.watch_correct_signal.get(), time.time())
        except (ValueError, StopIteration) as exc:
            messagebox.showerror('Band watcher', str(exc) or 'Select a logged QSO', parent=self.root)

    def _watch_render(self, view, now):
        active = [f for f in view['findings'] if view['enabled'] and f['active'] and not f['acknowledged']]
        announced = {(view.get('session'), f['id']) for f in active}
        if announced - self.watch_announced:
            self.root.bell()
        self.watch_announced.update(announced)
        self.watch_status.configure(fg='#b00020' if active else '#444444')
        self.watch_status.configure(text=(str(len(active)) + ' unacknowledged findings · ' if active else '')
            + ('ENABLED · ' if view['enabled'] else 'DISABLED · ')
            + view['health']
            + '\nProfile: {} · {} · {} · {} · session {}'.format(
                view.get('entry', '?'), view.get('contest', 'unconfigured'),
                view.get('year', '?'), view.get('category', '?'), view.get('session', '?'))
            + '\nN1MM input: UDP 12063 · Detected contest: '
            + (self.watch_detected or 'not received') + '\n' + view.get('coverage', ''))
        self.watch_summary.configure(text='\n'.join('{} · {} MHz · {}'.format(s['signal'], s['band'] or '?',
            '{} seconds remaining'.format(int(s['operational_remaining'])) if s['limit'] is None
            else '{}/{} changes this UTC hour'.format(s['changes'], s['limit'])) for s in view['signals']))
        self.watch_history.delete(*self.watch_history.get_children())
        for f in sorted(view['findings'], key=lambda f: f['at'], reverse=True)[:500]:
            self.watch_history.insert('', 'end', values=(time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(f['at'])),
                f['signal'], '{} → {}'.format(f['previous'], f['band']), f['kind'] + ': ' + f['detail'],
                ('historical / suspended' if f.get('suppressed') else 'active' if f['active'] else 'resolved') + ('; acknowledged' if f['acknowledged'] else '')))
        with self.watch_lock:
            self.watch_qso_id['values'] = ['{} | {} {} MHz {}'.format(q.id, q.call, q.band, q.signal or 'UNASSIGNED')
                                          for q in sorted(self.watch_log.qsos(), key=lambda q: q.at, reverse=True)[:500]]
