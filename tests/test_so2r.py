"""SO2R regression: another radio's RX packet cannot end the active TX."""

import time
import unittest
from unittest.mock import patch

from helpers import MY_IP, m, make_lamp, peer, station
from test_overview import make_overview, hello
from test_phone import phone_lamp

REMOTE = '10.0.10.101'


def radio(nr=1, tx=False, freq='1409478', run=True, caption='', active=1, focus=1,
          txfreq=None):
    fields = dict(StationName='S53M1', RadioNr=nr, IsTransmitting=str(tx),
                  Freq=freq, Mode='RTTY-R', IsRunning=str(run),
                  FunctionKeyCaption=caption, ActiveRadioNr=active, FocusRadioNr=focus,
                  TXFreq=txfreq)
    return ('<RadioInfo>' + ''.join('<{0}>{1}</{0}>'.format(k, v)
                                    for k, v in fields.items() if v is not None)
            + '</RadioInfo>').encode()


class RadioSelectionTest(unittest.TestCase):
    def test_arrival_order_does_not_select_idle_radio(self):
        for order in ((1, 2), (2, 1)):
            rs = m.RadioSet()
            for nr in order:
                rs.update(radio(nr, freq='1409478' if nr == 1 else '2108037',
                                run=nr == 1), 100)
            self.assertEqual(rs.select(100).radio_nr, 1)
            rs.update(radio(2, freq='2108037', run=False, focus=2), 101)
            self.assertEqual(rs.select(101).radio_nr, 2)
            self.assertFalse(rs.select(101).running)

    def test_transmission_takes_precedence_over_keyboard_focus(self):
        rs = m.RadioSet()
        rs.update(radio(tx=True, caption='F1 CQ', focus=2), 100)
        rs.update(radio(2, freq='2108037', run=False, focus=2), 101)
        self.assertEqual((rs.select(101).radio_nr, rs.select(101).msg), (1, 'CQ'))
        rs.update(radio(tx=False, focus=2), 102)
        self.assertEqual(rs.select(102).radio_nr, 2)

    def test_idle_selection_falls_back_to_tx_focus_then_lowest_number(self):
        rs = m.RadioSet()
        rs.update(radio(2, active=2, focus=None), 100)
        rs.update(radio(1, active=2, focus=None), 100)
        self.assertEqual(rs.select(100).radio_nr, 2)
        rs.active_nr = None
        self.assertEqual(rs.select(100).radio_nr, 1)

    def test_long_tx_and_expiry_use_own_radio_heartbeat(self):
        rs = m.RadioSet()
        for stamp in (100, 110, 120):
            rs.update(radio(tx=True, caption='F1 CQ'), stamp)
            self.assertTrue(rs.select(stamp + 9.9).tx)
        rs.update(radio(2, freq='2108037', run=False), 134)
        self.assertEqual(rs.select(134).radio_nr, 1)
        self.assertEqual(rs.select(135.1).radio_nr, 2)
        self.assertFalse(rs.select(135.1).tx)
        self.assertIsNone(rs.select(150))

    def test_legacy_missing_radio_fields_and_invalid_radio(self):
        rs = m.RadioSet()
        self.assertIsNotNone(rs.update(radio(nr=None, active=None, focus=None), 100))
        self.assertEqual(rs.select(100).radio_nr, 1)
        for nr in ('garbage', '0', '3', ''):
            self.assertIsNone(rs.update(radio(nr, tx=True), 101))
        self.assertFalse(rs.select(101).tx)
        rs.update(radio(active='bad', focus='bad'), 102)
        self.assertEqual(rs.select(102).radio_nr, 1)

    def test_invalid_tx_cannot_end_or_refresh_transmission(self):
        rs = m.RadioSet()
        rs.update(radio(tx=True), 100)
        for bad in ('<IsTransmitting>bad</IsTransmitting>', ''):
            data = radio().decode().replace('<IsTransmitting>False</IsTransmitting>', bad).encode()
            self.assertIsNone(rs.update(data, 110))
            self.assertTrue(rs.select(110).tx)
        self.assertIsNone(rs.select(116))

    def test_tx_frequency_split_and_fallback(self):
        rs = m.RadioSet()
        r, _, _ = rs.update(radio(tx=True, txfreq='2108037'), 100)
        self.assertEqual((r.rx_freq, r.freq, r.key), ('1409478', '2108037', ('21', 'RTTY')))
        _, _, ended = rs.update(radio(tx=False, txfreq='2108037'), 101)
        self.assertEqual(ended.key, ('21', 'RTTY'))
        self.assertEqual(rs.select(101).key, ('14', 'RTTY'))
        for value in (None, '', 'bad', '0', '²'):
            rs.update(radio(tx=True, txfreq=value), 102)
            self.assertEqual(rs.select(102).key, ('14', 'RTTY'))

    def test_handoff_cannot_resurrect_previous_radio_after_lost_rx(self):
        rs = m.RadioSet()
        rs.update(radio(tx=True, caption='F1 CQ'), 100)
        rs.update(radio(2, tx=True, freq='2108037', active=2, focus=2), 101)
        self.assertEqual(rs.select(101).radio_nr, 2)
        rs.update(radio(2, tx=False, freq='2108037', active=2, focus=2), 102)
        self.assertFalse(rs.select(102).tx)
        self.assertFalse(rs.radios[1].tx)

    def test_explicit_shorter_timeout_is_respected(self):
        rs = m.RadioSet()
        rs.update(radio(tx=True), 100, stale=3)
        self.assertIsNotNone(rs.select(102.9, stale=3))
        self.assertIsNone(rs.select(103.1, stale=3))

    def test_caption_preserved_during_tx_but_not_reused_for_next_tx(self):
        rs = m.RadioSet()
        rs.update(radio(tx=True, caption='F1 CQ'), 100)
        rs.update(radio(tx=True), 101)
        self.assertEqual(rs.select(101).msg, 'CQ')
        rs.update(radio(), 102)
        rs.update(radio(tx=True), 103)
        self.assertEqual(rs.select(103).msg, '')


class LampSO2RTest(unittest.TestCase):
    def setUp(self):
        self.app = make_lamp(stale=m.DEFAULT_STALE)
        station(self.app, MY_IP, 'LOCAL', mode='RTTY', running=False)
        # Deliberately stale PC role: the RadioInfo role must win.
        self.peer = peer(self.app, 'REMOTE', [REMOTE], alias='RUN1', role='INB')

    def packet(self, data, stamp, relayed=True):
        with patch('time.time', return_value=stamp):
            self.app.on_packet(REMOTE, data, relayed=relayed)

    def test_observed_sequence_keeps_lamp_until_own_rx(self):
        # 19:23:13.240 TX, .260 other radio RX, 19:23:18.570 original RX.
        # Radio numbers annotate the supplied summary (which omitted them).
        start = time.time()
        self.app._slots['14'] = ['other', start]
        self.packet(radio(tx=True, caption='F5 His Call+F2 Run Exch'), start)
        self.packet(radio(2, freq='2108037', run=False), start + .020)
        st = self.app.stations[REMOTE]
        self.assertTrue(st.tx)
        self.assertEqual((st.key, st.msg), (('14', 'RTTY'), 'His Call+F2 Run Exch'))
        self.app.tracked = ('14', 'RTTY')
        self.assertTrue(self.app._is_tracked(st))
        self.assertEqual(self.app.role_of(st), 'RUN')
        self.assertNotIn('14', self.app._slots)
        self.assertEqual(self.app._go_time, 0)
        self.packet(radio(tx=False), start + 5.330)
        self.assertFalse(st.tx)
        self.assertAlmostEqual(self.app._go_time, start + 5.330)

    def test_per_radio_run_on_band_and_slot_holder(self):
        start = time.time()
        self.packet(radio(), start)
        self.packet(radio(2, freq='2108037', run=False, focus=2), start)
        self.assertEqual(self.app.stations[REMOTE].key, ('21', 'RTTY'))
        self.assertIsNotNone(self.app._run_on_band('14', start))
        self.assertIsNone(self.app._run_on_band('21', start))
        self.packet(radio(2, tx=True, freq='2108037', run=False, active=2, focus=2), start)
        self.assertEqual(self.app._slots['21'][0], REMOTE)

    def test_radio_handoff_and_local_role_selection(self):
        self.app.stations.clear()
        with patch('n1mm_lamps.send_relay'):
            self.app.on_packet('127.0.0.1', radio(tx=True, caption='F1 CQ'))
            self.app.on_packet(MY_IP, radio(2, freq='2108037', run=False, focus=2))
            self.assertEqual(len(self.app.stations), 1)
            self.assertEqual(self.app._my_role_now(), 'RUN')
            self.app.on_packet(MY_IP, radio(tx=False, focus=2))
            self.assertEqual(self.app._my_role_now(), 'INB')
            self.assertEqual(self.app._my_band(), '21')
            self.app.on_packet(MY_IP, radio(2, tx=True, freq='2108037', run=False,
                                             active=2, focus=2, caption='F2 Exch'))
            self.assertEqual(self.app._local_station().msg, 'Exch')

    def test_direct_preference_is_per_radio(self):
        start = time.time()
        self.packet(radio(tx=True, caption='F1 CQ'), start, relayed=False)
        with patch('time.time', return_value=start + .1):
            self.app.on_relay(REMOTE, 'REMOTE', radio(2, freq='2108037', run=False))
            self.app.on_relay(REMOTE, 'REMOTE', radio(tx=False))
        st = self.app.stations[REMOTE]
        self.assertEqual(set(st.radios.radios), {1, 2})
        self.assertTrue(st.tx)  # duplicate relay RX for directly heard R1 ignored

    def test_local_urgent_follows_transmitting_radio(self):
        self.app.stations.clear()
        with patch('n1mm_lamps.send_relay'):
            self.app.on_packet(MY_IP, radio(tx=True, caption='F4 Call'))
            self.app.urgent_on = True
            self.app.urgent_time = time.time()
            self.app.on_packet(MY_IP, radio(2, freq='2108037', run=False, focus=2))
            self.app._urgent_check(time.time())
            self.assertTrue(self.app.urgent_on)
            self.assertTrue(self.app._urgent_tx_seen)
            self.app.on_packet(MY_IP, radio(tx=False, focus=2))
            self.app._urgent_check(time.time())
            self.assertFalse(self.app.urgent_on)

    def test_urgent_ends_on_original_radio_during_tx_handoff(self):
        self.app.stations.clear()
        with patch('n1mm_lamps.send_relay'):
            self.app.on_packet(MY_IP, radio(tx=True, caption='F4 Call'))
            self.app._urgent_pressed('F4')
            self.app._urgent_check(time.time())
            self.assertTrue(self.app._urgent_tx_seen)
            self.app.on_packet(MY_IP, radio(2, tx=True, freq='2108037',
                                             active=2, focus=2))
            self.app._urgent_check(time.time())
        self.assertFalse(self.app.urgent_on)
        self.assertTrue(self.app._local_station().tx)

    def test_f12_band_and_role_not_changed_by_idle_radio_packet(self):
        self.app.stations.clear()
        with patch('n1mm_lamps.send_relay'):
            self.app.on_packet(MY_IP, radio(tx=True))
            self.app.on_packet(MY_IP, radio(2, freq='2108037', run=False, focus=2))
        self.app._f12_pressed()
        self.assertTrue(self.app.alert_run)
        self.assertEqual(self.app._hello_state()['alert_band'], '14')
        caller = peer(self.app, 'CALLER', ['10.0.0.8'], alert=True,
                      alert_band='21', alert_run=True, alert_time=time.time())
        self.assertNotIn(caller, self.app._incoming_calls(time.time()))
        caller.alert_band = '14'
        self.assertIn(caller, self.app._incoming_calls(time.time()))

    def test_priority_stops_actual_tx_despite_other_keyboard_focus(self):
        self.app.stations.clear()
        with patch('n1mm_lamps.send_relay'):
            self.app.on_packet(MY_IP, radio(tx=True))
            self.app.on_packet(MY_IP, radio(2, freq='2108037', run=False, focus=2))
        caller = peer(self.app, 'CALLER', ['10.0.0.8'], urgent=True,
                      urgent_priority=True, urgent_band='14', urgent_mode='RTTY',
                      urgent_time=time.time())
        with patch('pingpong_rules.keys.n1mm_active', return_value=True), \
                patch('pingpong_rules.keys.press_key') as press:
            self.app._priority_heard(caller, time.time())
            self.app._priority_heard(caller, time.time())
            press.assert_called_once_with(m.keys.VK_ESCAPE)
        self.assertTrue(self.app._priority_active(time.time()))

    def test_urgent_caption_matches_radio_mode_on_same_band(self):
        start = time.time()
        self.packet(radio(caption='F4 CW call').replace(b'RTTY-R', b'CW'), start)
        self.packet(radio(2, tx=True, caption='F1 RTTY CQ'), start)
        self.peer.urgent = True
        self.peer.urgent_band, self.peer.urgent_mode = '14', 'CW'
        self.assertEqual(self.app._peer_station(self.peer).msg, 'CW call')

    def test_radio_numbers_in_direct_and_relay_diagnostics(self):
        self.app.test_on = True
        lines = []
        self.app._test_write = lines.append
        self.app.on_packet(REMOTE, radio(2, active=1, focus=2), relayed=True)
        self.assertIn('radio=2  active=1  focus=2', lines[0])
        lines.clear()
        with patch('n1mm_lamps.send_relay'):
            self.app.on_packet(MY_IP, radio())
        self.assertIn('radio=1  active=1  focus=1', lines[0])

    def test_color_identity_and_radio_expiry(self):
        start = time.time()
        self.packet(radio(tx=True), start)
        st = self.app.stations[REMOTE]
        color, label = st.color, st.label
        self.packet(radio(2, freq='2108037', run=False), start + 14)
        with patch('time.time', return_value=start + 16):
            self.app._refresh_radios(start + 16)
        self.assertFalse(st.tx)
        self.assertEqual((st.color, st.label), (color, label))
        self.assertEqual(len(self.app.stations), 2)


class OverviewSO2RTest(unittest.TestCase):
    def setUp(self):
        self.o = make_overview()
        hello(self.o, 'R', 'SO2R-PC', 'RUN1', role='INB')

    def packet(self, data, stamp):
        with patch('time.time', return_value=stamp):
            self.o._packet(REMOTE, m.make_relay('R', data))

    def test_interleaved_packets_keep_caption_and_count_one_tx(self):
        start = time.time()
        self.packet(radio(tx=True, caption='F5 His Call+F2 Run Exch'), start)
        self.packet(radio(2, freq='2108037', run=False), start + .020)
        lamp = self.o.lamps['R']
        self.assertTrue(lamp.tx)
        self.assertEqual(lamp.caption, 'His Call+F2 Run Exch')
        self.assertTrue(lamp.running)
        self.assertEqual(lamp.key(), ('14', 'RTTY'))
        self.assertNotIn('14', self.o.slots)
        self.assertEqual(self.o._stat(lamp)['keys'], {'F5': 1})
        self.packet(radio(), start + 5.330)
        self.assertFalse(lamp.tx)
        self.assertAlmostEqual(self.o._stat(lamp)['tx_time'], 5.330, places=3)

    def test_expiry_not_extended_by_other_radio_or_hello(self):
        start = time.time()
        self.packet(radio(tx=True, caption='F1 CQ'), start)
        self.packet(radio(2, freq='2108037', run=False), start + 14)
        lamp = self.o.lamps['R']
        with patch('time.time', return_value=start + 14.5):
            hello(self.o, 'R', 'SO2R-PC', 'RUN1', role='RUN')
        with patch('time.time', return_value=start + 16):
            self.o._row(lamp, start + 16, [])
        self.assertFalse(lamp.tx)
        self.assertAlmostEqual(self.o._stat(lamp)['tx_time'], m.DEFAULT_STALE)
        self.o._sync_radio(lamp, start + 17)
        self.assertAlmostEqual(self.o._stat(lamp)['tx_time'], m.DEFAULT_STALE)

    def test_tx_handoff_counts_both_radios_without_resurrecting_old_tx(self):
        start = time.time()
        self.packet(radio(tx=True, caption='F1 CQ'), start)
        self.packet(radio(2, tx=True, freq='2108037', caption='F2 Exch',
                          run=False, active=2, focus=2), start + 2)
        self.packet(radio(2, tx=False, freq='2108037', run=False,
                          active=2, focus=2), start + 5)
        lamp = self.o.lamps['R']
        self.assertFalse(lamp.tx)
        self.assertEqual(self.o._stat(lamp)['keys'], {'F1': 1, 'F2': 1})
        self.assertAlmostEqual(self.o._stat(lamp)['tx_time'], 5)
        self.assertEqual(self.o.slots['21'][0], 'R')

    def test_ten_second_refresh_keeps_long_transmission_in_row(self):
        start = time.time()
        for offset in (0, 10, 20):
            self.packet(radio(tx=True, caption='F1 CQ'), start + offset)
            with patch('time.time', return_value=start + offset + 9):
                self.assertTrue(self.o._row(self.o.lamps['R'], start + offset + 9, [])['values']['tx'])
        self.assertEqual(self.o._stat(self.o.lamps['R'])['keys'], {'F1': 1})


class PhoneSO2RTest(unittest.TestCase):
    def test_both_radio_bands_visible_under_same_pc_identity(self):
        app = phone_lamp()
        app.stale = m.DEFAULT_STALE
        app.on_packet(REMOTE, radio(tx=True, caption='F1 CQ'), relayed=True)
        app.on_packet(REMOTE, radio(2, freq='2108037', run=False), relayed=True)
        bands = app._phone_snapshot(time.time())['bands']
        self.assertEqual([b['band'] for b in bands], ['14 MHz RTTY', '21 MHz RTTY'])
        self.assertEqual(bands[0]['msg'], 'CQ')
        self.assertTrue(bands[0]['who'])
        self.assertEqual(bands[1]['who'], '')
