"""Central watcher integration; synthetic N1MM packets and fake network only."""
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from helpers import make_lamp, peer, MY_IP, m
from pingpong_bandservice import BandWatcherMixin
from pingpong_bandwatch import Profile, WatchLog
from test_bandwatch import BASE, export
from test_bandwire import state


class Service(BandWatcherMixin):
    pass


class BandServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = Service()
        self.service.watch_log = WatchLog(os.path.join(self.tmp.name, 'watch.sqlite'))
        self.service.watch_log.configure(Profile('CQ-WW-CW', 2026, 'M2'), 'S53M',
            [dict(source='RUN-PC', ip='10.0.0.5', radio='1', signal='TX1', **{'from': BASE})], 'test')
        with self.service.watch_log.db:
            self.service.watch_log.put('enabled', True)
        self.service.watch_seen = {}
        self.service.watch_detected = ''
        self.service.watch_lock = threading.RLock()
        self.service.lock = threading.Lock()
        self.service.watch_started = BASE
        self.service.watch_boot = 'BOOT'
        self.service.watch_seq = 0
        self.service.watch_sent = 0
        self.service.watch_error = ''
        self.service.watch_view = {}
        self.service.watch_next_ui = 0
        self.service.lamps = {}
        self.service._watch_authoritative = lambda now: True
        self.sent = []
        self.service._send_command = self.sent.append

    def tearDown(self):
        self.service.watch_log.db.close()
        self.tmp.cleanup()

    def packet(self, tag='contactinfo', **fields):
        values = dict(ID='QSO1', contestname='CQWWCW', mycall='S53M',
                      StationName='RUN-PC', timestamp='2026-11-28 12:00:00',
                      band='14', call='K1ABC', RadioNr='1')
        values.update(fields)
        return ('<'+tag+'>' + ''.join('<{0}>{1}</{0}>'.format(k,v) for k,v in values.items()) + '</'+tag+'>').encode()

    def test_source_allowlist_and_duplicates(self):
        s = self.service
        data = self.packet()
        s._watch_packet('10.0.0.6', data, BASE)
        self.assertEqual(s.watch_log.qsos(), [])
        s._watch_packet('10.0.0.5', data, BASE)
        s._watch_packet('10.0.0.5', data, BASE + 1)
        self.assertEqual(len(s.watch_log.qsos()), 1)
        self.assertEqual(s.watch_seen['RUN-PC'], BASE + 1)

    def test_disable_and_contest_detection(self):
        s = self.service
        s._watch_packet('10.0.0.5', self.packet('AppInfo'), BASE)
        self.assertEqual(s.watch_detected, 'CQ-WW-CW')
        with s.watch_log.db:
            s.watch_log.put('enabled', False)
        s._watch_packet('10.0.0.5', self.packet(), BASE)
        self.assertEqual(s.watch_log.qsos(), [])

    def test_tuning_does_not_count_changes_or_tx_findings(self):
        s = self.service
        s._watch_packet('10.0.0.5', self.packet(), BASE)
        s._watch_packet('10.0.0.5', self.packet('RadioInfo', Freq='701000', Mode='CW',
                                              IsTransmitting='False'), BASE + 60)
        view = s._watch_tick(BASE + 60)
        self.assertEqual(view['signals'][0]['changes'], 0)
        self.assertEqual(view['findings'], [])

    def test_feed_loss_marks_incomplete_and_does_not_self_clear(self):
        s = self.service
        s.watch_log.reconcile(export(), BASE)
        view = s._watch_tick(BASE + 40)
        self.assertIn('feed missing', view['health'])
        s._watch_packet('10.0.0.5', self.packet('RadioInfo'), BASE + 41)
        self.assertIn('reconciliation required', s._watch_tick(BASE + 42)['health'])

    def test_wire_is_bounded_and_contains_no_qso_ledger(self):
        s = self.service
        s._watch_packet('10.0.0.5', self.packet(), BASE)
        view = s._watch_tick(BASE)
        wire = s._watch_wire(view, {'TX1'}, BASE)
        self.assertLess(len(json.dumps(wire).encode()), 1800)
        self.assertNotIn('qsos', wire)
        self.assertNotIn('QSO1', wire['text'])
        self.assertIn('TX1', wire['text'])

    def test_publishing_stops_when_authority_is_lost(self):
        s = self.service
        lamp = type('Lamp', (), dict(n1mm_name='RUN-PC', id='L', hello={}))()
        s.lamps = {'L': lamp}
        s._watch_tick(BASE)
        self.assertEqual(len(self.sent), 1)
        command = m.parse_command(self.sent[0])
        self.assertEqual((command['cmd'], command['to']), ('bandwatch', 'L'))
        s._watch_authoritative = lambda now: False
        s._watch_tick(BASE + 4)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('unavailable', s.watch_view['health'])

    def test_lamp_rejects_nonmaster_and_accepts_master_command(self):
        app = make_lamp()
        master = peer(app, 'MASTER', ['10.0.0.5'])
        app._master_peer = master
        command = m.parse_command(m.make_command('bandwatch', state=state()))
        with patch('time.time', return_value=102):
            app.on_command('10.0.0.6', command)
            self.assertFalse(app._band_client().enabled)
            # A new command UUID: unauthorized input cannot consume this one.
            command = m.parse_command(m.make_command('bandwatch', state=state()))
            app.on_command('10.0.0.5', command)
            self.assertIn('TX1', app._band_text(102))
            app._master_peer = peer(app, 'NEW', ['10.0.0.7'])
            self.assertEqual(app._band_text(102), 'Band watcher unavailable')

    def test_unique_master_check_fails_closed(self):
        s = Service()
        s.lock = threading.Lock()
        s._masters = lambda now: []
        self.assertFalse(s._watch_authoritative(BASE))
        master = type('Lamp', (), dict(hello={'ips': [MY_IP]}))()
        s._masters = lambda now: [master]
        with patch('pingpong_bandservice.net.local_networks', return_value=[(MY_IP, '10.0.0.255')]):
            self.assertTrue(s._watch_authoritative(BASE))
        s._masters = lambda now: [master, master]
        self.assertFalse(s._watch_authoritative(BASE))

    def test_logged_breach_flash_is_station_wide_and_acknowledgeable(self):
        s = self.service
        view = dict(enabled=True, session='test', health='live', signals=[], findings=[
            dict(id='Q9', kind='logged-qso', active=True, acknowledged=False, signal='TX1',
                 at=BASE, previous='14', band='7', detail='change 9/8')])
        # Even an unrelated signal must receive the red-flash flag.
        wire = s._watch_wire(view, {'TX2'}, BASE)
        self.assertTrue(wire['breach'])
        self.assertFalse(wire['urgent'])
        view['findings'][0]['acknowledged'] = True
        self.assertFalse(s._watch_wire(view, {'TX2'}, BASE)['breach'])

    def test_desktop_flash_alternates_red_and_dark_without_changing_caption(self):
        app = make_lamp(_info_fg='normal')
        app._i_am_master = True
        app._port_warning = lambda: ''
        app._password_warnings = lambda now: []
        app._sync_compact = lambda: None
        app._phone_tail = lambda: None
        class Info:
            values = {'text': 'CQ', 'fg': 'normal'}
            def cget(self, key):
                return self.values[key]
            def configure(self, **kw):
                self.values.update(kw)
        app.info = Info()
        colors = []
        app._set_bg = colors.append
        app._band_client().accept(state(breach=True), app.node_id, 102)
        for stamp in (102.2, 102.55):
            with patch('time.time', return_value=stamp):
                app._update_tail()
        self.assertEqual(set(colors), {'red', '#202020'})
        self.assertIn('CQ', app.info.values['text'])

    def test_unicode_summary_is_bounded_and_authentication_is_preserved(self):
        from pingpong_net import AUTH, derive_key, set_network_key, seal, unseal, accepted
        previous = AUTH['key']
        view = dict(enabled=True, session='S'*1000, health='Ž'*600, signals=[], findings=[])
        wire = self.service._watch_wire(view, set(), BASE)
        self.assertLessEqual(len(json.dumps(wire).encode()), 1800)
        try:
            set_network_key(derive_key('test-watcher'))
            packet = seal(m.make_command('bandwatch', state=wire))
            body, status = unseal(packet, '10.0.0.5')
            self.assertTrue(accepted(status))
            self.assertEqual(m.parse_command(body)['state'], wire)
            _, status = unseal(packet[:-1] + b'!', '10.0.0.5')
            self.assertFalse(accepted(status))
        finally:
            set_network_key(previous)

    def test_dedicated_udp_listener_ingests_loopback_contact(self):
        import socket
        s = self.service
        maps = s.watch_log.get('mappings')
        maps[0]['ip'] = '127.0.0.1'
        with s.watch_log.db:
            s.watch_log.put('mappings', maps)
        s.stop = threading.Event()
        bound, ingested = threading.Event(), threading.Event()
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        class Receiver:
            def bind(self, addr):
                receiver.bind(addr)
                bound.set()
            def settimeout(self, seconds):
                receiver.settimeout(seconds)
            def recvfrom(self, count):
                return receiver.recvfrom(count)
            def close(self):
                receiver.close()
        original = s._watch_packet
        def consume(*args):
            original(*args)
            ingested.set()
        s._watch_packet = consume
        try:
            with patch('pingpong_bandservice.WATCH_PORT', 0), \
                    patch('pingpong_bandservice.socket.socket', return_value=Receiver()):
                thread = threading.Thread(target=s._watch_listen)
                thread.start()
                self.assertTrue(bound.wait(1))
                sender.sendto(self.packet(), ('127.0.0.1', receiver.getsockname()[1]))
                self.assertTrue(ingested.wait(1))
                s.stop.set()
                thread.join(2)
                self.assertFalse(thread.is_alive())
            self.assertEqual(len(s.watch_log.qsos()), 1)
        finally:
            s.stop.set()
            sender.close()
            receiver.close()

    def test_unconfigured_new_master_does_not_silently_disable_existing_clients(self):
        s = self.service
        with s.watch_log.db:
            s.watch_log.db.execute("DELETE FROM config WHERE key='enabled'")
        lamp = type('Lamp', (), dict(n1mm_name='RUN-PC', id='L', hello={}))()
        s.lamps = {'L': lamp}
        s._watch_tick(BASE)
        self.assertEqual(self.sent, [])
