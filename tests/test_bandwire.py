"""Synthetic watcher wire state: authority is supplied AFTER master validation."""
import unittest
from pingpong_bandwire import WatchClient


def state(**kw):
    s = dict(v=1, enabled=True, started=100, issued=101, boot='BOOT', seq=1,
             session='CQWW2026', text='TX1 8/8 changes', alerts=[], urgent=False)
    s.update(kw)
    return s


class WatchWireTest(unittest.TestCase):
    def test_fresh_status_and_expiry(self):
        c = WatchClient()
        self.assertTrue(c.accept(state(), 'MASTER', 102))
        self.assertEqual(c.text('MASTER', 103), 'TX1 8/8 changes')
        self.assertEqual(c.text('MASTER', 114), 'Band watcher unavailable')
        self.assertEqual(c.text('OTHER', 103), 'Band watcher unavailable')

    def test_replay_sequence_epoch_and_master_changes(self):
        c = WatchClient()
        self.assertTrue(c.accept(state(), 'M', 102))
        self.assertFalse(c.accept(state(), 'M', 102))
        self.assertTrue(c.accept(state(seq=2), 'M', 102))
        self.assertFalse(c.accept(state(boot='different'), 'M', 102))
        self.assertTrue(c.accept(state(boot='new', started=102, issued=102), 'M', 102))
        self.assertFalse(c.accept(state(seq=99), 'M', 102))
        self.assertTrue(c.accept(state(), 'M2', 102))
        self.assertEqual(c.text('M', 103), 'Band watcher unavailable')

    def test_stale_future_invalid_and_oversized(self):
        for s in (state(issued=80), state(issued=200), state(v=2), state(text='x'*1900),
                  state(seq=float('nan')), state(seq=True), state(started=float('inf')),
                  state(alerts=['x'*200]), state(enabled='yes')):
            self.assertFalse(WatchClient().accept(s, 'M', 102))
        self.assertFalse(WatchClient().accept(state(), None, 102))

    def test_alert_dedup_ack_keeps_evidence(self):
        c = WatchClient()
        self.assertTrue(c.accept(state(alerts=['QSO:9'], urgent=True, text='BREACH 9/8'), 'M', 102))
        self.assertTrue(c.ring)
        c.acknowledge()
        self.assertEqual(c.text('M', 102), 'BREACH 9/8')
        self.assertTrue(c.accept(state(seq=2, alerts=['QSO:9'], urgent=True), 'M', 102))
        self.assertFalse(c.ring)
        self.assertTrue(c.accept(state(seq=3, alerts=['QSO:10'], urgent=True), 'M', 102))
        self.assertTrue(c.ring)

    def test_persisted_enabled_state_starts_unavailable(self):
        self.assertEqual(WatchClient(enabled=True).text('M', 102), 'Band watcher unavailable')
        c = WatchClient(enabled=True)
        self.assertTrue(c.accept(state(enabled=False), 'M', 102))
        self.assertEqual(c.text('M', 102), '')

    def test_breach_flashes_every_client_until_central_ack_or_expiry(self):
        for urgent in (False, True):  # unaffected and affected operators
            c = WatchClient()
            self.assertTrue(c.accept(state(breach=True, urgent=urgent, alerts=['Q9']), 'M', 102))
            self.assertTrue(c.flash('M', 102))
            c.acknowledge()  # local silence is not a captain's acknowledgment
            self.assertTrue(c.flash('M', 102))
            self.assertFalse(c.flash('OTHER', 102))
            self.assertFalse(c.flash('M', 114))
            self.assertTrue(c.accept(state(seq=2, breach=False, alerts=[]), 'M', 103))
            self.assertFalse(c.flash('M', 103))

    def test_possible_tx_warning_does_not_claim_a_logged_breach(self):
        c = WatchClient()
        self.assertTrue(c.accept(state(urgent=True, alerts=['TX1']), 'M', 102))
        self.assertFalse(c.flash('M', 102))
