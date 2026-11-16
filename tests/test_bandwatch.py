"""Synthetic CQ fixtures; no claim of a live N1MM hardware capture."""

import os
import tempfile
import unittest

from pingpong_bandwatch import (Profile, QSO, WatchLog, FAMILIES, evaluate,
                               utc_stamp, cabrillo, contact_packet)

BASE = utc_stamp('2026-11-28 12:00:00')


def q(n, minute, band, signal='TX1'):
    return QSO(str(n), BASE + minute * 60, band, 'K{}ABC'.format(n), signal)


def export(contest='CQ-WW-CW', tx='TWO', rows=None):
    rows = rows or ['14000 CW 2026-11-28 1200 S53M 599 15 K1ABC 599 05 0']
    return '\n'.join(['START-OF-LOG: 3.0', 'CALLSIGN: S53M', 'CONTEST: ' + contest,
                     'CATEGORY-OPERATOR: MULTI-OP', 'CATEGORY-TRANSMITTER: ' + tx]
                    + ['QSO: ' + row for row in rows] + ['END-OF-LOG:'])


class BandRuleTest(unittest.TestCase):
    def test_profiles_are_explicit(self):
        for contest in FAMILIES:
            for category in ('MS', 'M2'):
                p = Profile(contest, 2026, category)
                self.assertEqual(p.timed, category == 'MS' and contest in ('CQ-WW-CW', 'CQ-WW-SSB'))
                self.assertEqual(p.limit, 10 if category == 'MS' and 'WPX' in contest else 8)
        for args in (('UNKNOWN', 2026, 'MS'), ('CQ-WW-CW', 2027, 'MS'), ('CQ-WW-CW', 2026, 'SO')):
            with self.assertRaises(ValueError):
                Profile(*args)

    def test_limit_is_not_a_breach_but_next_change_is(self):
        for contest in FAMILIES:
            for category in ('MS', 'M2'):
                p = Profile(contest, 2026, category)
                if p.timed:
                    continue
                group = p.signals[0]
                rows = [q(n, n, '14' if n % 2 == 0 else '7', group) for n in range(p.limit + 1)]
                states, findings = evaluate(p, rows, BASE + 1800)
                self.assertEqual(findings, [])
                self.assertEqual(states[0]['left'], 0)
                rows.append(q(p.limit + 1, p.limit + 1, '7', group))
                self.assertEqual(len(evaluate(p, rows, BASE + 1800)[1]), 1)

    def test_hour_reset_keeps_previous_band(self):
        p = Profile('CQ-WW-CW', 2026, 'M2')
        rows = [q(1, 59, '14'), q(2, 60, '7'), q(3, 61, '14')]
        states, _ = evaluate(p, rows, BASE + 3660)
        self.assertEqual(states[0]['changes'], 2)
        self.assertEqual(evaluate(p, rows, BASE + 86400)[0][0]['changes'], 0)

    def test_independent_signals_and_sorted_events(self):
        p = Profile('CQ-WW-RTTY', 2026, 'MS')
        rows = [q(1, 0, '14', 'RUN'), q(2, 1, '7', 'MULT'), q(3, 2, '21', 'RUN')]
        states, _ = evaluate(p, list(reversed(rows)), BASE + 180)
        self.assertEqual({s['signal']: s['changes'] for s in states}, {'RUN': 1, 'MULT': 0})

    def test_timer_uses_cabrillo_minutes_and_not_each_qso(self):
        p = Profile('CQ-WW-CW', 2026, 'MS')
        rows = [QSO('1', BASE + 59, '14', 'K1A', 'RUN'), q(2, 5, '14', 'RUN')]
        self.assertEqual(evaluate(p, rows, BASE + 599)[0][0]['remaining'], 1)
        self.assertEqual(evaluate(p, rows + [q(3, 10, '7', 'RUN')], BASE + 600)[1], [])
        self.assertEqual(len(evaluate(p, rows + [q(3, 9, '7', 'RUN')], BASE + 600)[1]), 1)

    def test_wpx_ms_groups_all_sources(self):
        p = Profile('CQ-WPX-CW', 2026, 'MS')
        rows = [q(1, 0, '14', 'PC1'), q(2, 1, '7', 'PC2')]
        self.assertEqual(evaluate(p, rows, BASE + 120)[0][0]['changes'], 1)

    def test_cabrillo_validates_entry_category_mode_and_year(self):
        p = Profile('CQ-WW-CW', 2026, 'M2')
        self.assertEqual(cabrillo(export(), p, 'S53M')[0].signal, 'TX1')
        for text in (export().replace('S53M', 'S50A'), export(tx='ONE'),
                     export().replace('2026-11', '2025-11'),
                     export().replace(' CW ', ' PH '), export().replace('END-OF-LOG:', '')):
            with self.assertRaises(ValueError):
                cabrillo(text, p, 'S53M')

    def test_bootstrap_all_profiles_including_ww_rtty_two_exchange_fields(self):
        # Layout verified against official CQ Cabrillo examples; dates/calls synthetic.
        for name in FAMILIES:
            mode = {'CW': 'CW', 'SSB': 'PH', 'RTTY': 'RY'}[name.rsplit('-', 1)[1]]
            row = ('14080 RY 2026-09-26 1200 S53M 599 15 DX K1ABC 599 05 MA 0'
                   if name == 'CQ-WW-RTTY' else
                   '14000 {} 2026-11-28 1200 S53M 599 15 K1ABC 599 05 0'.format(mode))
            for category, tx in (('MS', 'ONE'), ('M2', 'TWO')):
                p = Profile(name, 2026, category)
                parsed = cabrillo(export(name, tx, [row]), p, 'S53M')
                self.assertEqual((parsed[0].call, parsed[0].signal), ('K1ABC', p.signals[0]))

    def test_conservative_tx_hold_is_distinct_from_log_minute_check(self):
        p = Profile('CQ-WW-CW', 2026, 'MS')
        states, findings = evaluate(p, [QSO('1', BASE + 59, '14', 'K1A', 'RUN')], BASE + 600)
        self.assertEqual(states[0]['remaining'], 0)
        self.assertEqual(states[0]['operational_remaining'], 59)
        self.assertEqual(findings, [])

    def test_packet_keeps_physical_and_logical_fields_separate(self):
        f = contact_packet(b'<contactinfo><RadioNr>2</RadioNr><run1run2>1</run1run2>'
                           b'<IsRunQSO>True</IsRunQSO></contactinfo>')
        self.assertEqual((f['radionr'], f['run1run2'], f['isrunqso']), ('2', '1', 'True'))


class WatchLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'watch.sqlite')
        self.log = WatchLog(self.path)
        self.profile = Profile('CQ-WW-CW', 2026, 'M2')
        self.mappings = [dict(source='RUN-PC', radio='1', signal='TX1', **{'from': BASE}),
                         dict(source='SECOND-PC', radio='2', signal='TX1', **{'from': BASE})]
        self.log.configure(self.profile, 'S53M', self.mappings, '2026-cqww-test')

    def tearDown(self):
        self.log.db.close()
        self.tmp.cleanup()

    def fields(self, identity='A', clock='12:00:00', band='14', source='RUN-PC', kind='contactinfo'):
        return dict(id=identity, timestamp='2026-11-28 ' + clock, contestname='CQWWCW',
                    mycall='S53M', call='K1ABC', band=band, stationname=source,
                    radionr='1', kind=kind)

    def test_duplicates_and_forwarded_copies(self):
        f = self.fields()
        self.log.ingest(f, BASE)
        self.log.ingest(dict(f, IsOriginal=False), BASE + 1)
        self.assertEqual(len(self.log.qsos()), 1)
        self.assertEqual(self.log.qsos()[0].signal, 'TX1')

    def test_mapping_changes_do_not_rewrite_past_assignments(self):
        self.log.ingest(self.fields(), BASE)
        new = self.mappings + [dict(source='RUN-PC', signal='TX2', **{'from': BASE + 60})]
        self.log.configure(self.profile, 'S53M', new, '2026-cqww-test')
        self.log.ingest(self.fields('B', '12:02:00'), BASE + 120)
        self.assertEqual([q.signal for q in self.log.qsos()], ['TX1', 'TX2'])
        self.log.correct_signal('2026-cqww-test:A', 'TX2', BASE + 180)
        self.assertEqual(self.log.qsos()[0].signal, 'TX2')

    def test_delete_replace_is_debounced(self):
        f = self.fields()
        self.log.ingest(f, BASE)
        self.log.ingest(dict(f, kind='contactdelete'), BASE + 1)
        self.assertEqual(self.log.snapshot(BASE + 1)['health'], 'log edit pending')
        self.log.ingest(dict(f, kind='contactreplace', band='7'), BASE + 1.5)
        self.assertEqual(self.log.snapshot(BASE + 4)['signals'][0]['band'], '7')
        self.assertEqual(len(self.log.qsos()), 1)
        self.log.ingest(dict(f, kind='contactdelete'), BASE + 5)
        self.log.flush(BASE + 8)
        self.assertEqual(self.log.qsos(), [])

    def test_restart_requires_reconciliation_and_preserves_qsos(self):
        self.log.ingest(self.fields(), BASE)
        self.log.db.close()
        self.log = WatchLog(self.path)
        self.assertEqual(len(self.log.qsos()), 1)
        self.assertIn('reconciliation required', self.log.health)

    def test_full_export_then_live_guid_binding(self):
        self.log.reconcile(export(), BASE + 1)
        self.assertIn('delivery unverified', self.log.health)
        self.log.ingest(self.fields(), BASE + 2)
        self.assertEqual(len(self.log.qsos()), 1)
        self.assertTrue(self.log.qsos()[0].id.startswith('export:'))
        self.assertEqual(self.log._aliases['2026-cqww-test:A'], self.log.qsos()[0].id)

    def test_session_change_archives_and_isolates(self):
        self.log.ingest(self.fields(), BASE)
        self.log.configure(self.profile, 'S53M', self.mappings, 'new-entry')
        self.assertEqual(self.log.qsos(), [])
        self.assertIsNone(self.log.get('snapshot_at'))
        self.assertTrue(self.log.db.execute("SELECT 1 FROM audit WHERE kind='session-archive'").fetchone())

    def test_findings_remain_as_resolved_after_edit_and_ack(self):
        for n in range(10):
            self.log.ingest(self.fields(str(n), '12:{:02d}:00'.format(n), '14' if n % 2 == 0 else '7'), BASE + n * 60)
        snap = self.log.snapshot(BASE + 600)
        self.assertEqual(len(snap['findings']), 1)
        self.log.acknowledge()
        f = self.fields('9', '12:09:00', '14', kind='contactreplace')
        self.log.ingest(f, BASE + 601)
        findings = self.log.snapshot(BASE + 602)['findings']
        self.assertFalse(findings[0]['active'])
        self.assertTrue(findings[0]['acknowledged'])

    def test_wrong_contest_and_missing_identity_do_not_contaminate(self):
        self.assertFalse(self.log.ingest(dict(self.fields(), contestname='ARRLDX'), BASE))
        self.assertFalse(self.log.ingest(dict(self.fields(), id=''), BASE))
        self.assertEqual(self.log.qsos(), [])

    def test_observed_early_tx_is_separate_from_logged_findings(self):
        p = Profile('CQ-WW-CW', 2026, 'MS')
        mapping = [dict(source='RUN-PC', signal='RUN', **{'from': BASE})]
        self.log.configure(p, 'S53M', mapping, 'ms-session')
        self.log.ingest(self.fields(), BASE)
        self.log.observe_tx('RUN-PC', {'radionr': '1'}, '7', BASE + 120)
        self.log.observe_tx('RUN-PC', {'radionr': '1'}, '7', BASE + 121)
        findings = self.log.snapshot(BASE + 121)['findings']
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['kind'], 'observed-tx')

    def test_pending_delete_survives_restart(self):
        f = self.fields()
        self.log.ingest(f, BASE)
        self.log.ingest(dict(f, kind='contactdelete'), BASE + 1)
        self.log.db.close()
        self.log = WatchLog(self.path)
        self.log.flush(BASE + 4)
        self.assertEqual(self.log.qsos(), [])
        self.assertFalse(self.log.ingest(f, BASE + 5))

    def test_documented_delete_without_guid_matches_unique_qso(self):
        self.log.ingest(self.fields(), BASE)
        delete = self.fields(kind='contactdelete')
        delete.pop('id')
        self.assertTrue(self.log.ingest(delete, BASE + 1))
        self.log.flush(BASE + 4)
        self.assertEqual(self.log.qsos(), [])

    def test_delayed_original_cannot_overwrite_replacement(self):
        original = self.fields()
        self.log.ingest(original, BASE)
        self.log.ingest(dict(original, kind='contactreplace', band='7'), BASE + 1)
        self.assertFalse(self.log.ingest(original, BASE + 2))
        self.assertEqual(self.log.qsos()[0].band, '7')
        self.assertIn('reconciliation required', self.log.health)

    def test_reversed_delete_replace_is_conservative(self):
        original = self.fields()
        self.log.ingest(original, BASE)
        self.log.ingest(dict(original, kind='contactreplace', band='7'), BASE + 1)
        self.assertFalse(self.log.ingest(dict(original, kind='contactdelete'), BASE + 1.1))
        self.log.flush(BASE + 5)
        self.assertEqual(self.log.qsos()[0].band, '7')
        self.assertIn('reconciliation required', self.log.health)

    def test_assignment_correction_recalculates_downstream_changes(self):
        for n in range(10):
            self.log.ingest(self.fields(str(n), '12:{:02d}:00'.format(n), '14' if n % 2 == 0 else '7'), BASE + n * 60)
        self.assertEqual(len(self.log.snapshot(BASE + 600)['findings']), 1)
        self.log.correct_signal('2026-cqww-test:5', 'TX2', BASE + 601)
        view = self.log.snapshot(BASE + 602)
        self.assertFalse(any(f['active'] for f in view['findings']))
        self.assertEqual(view['signals'][0]['changes'], 7)

    def test_reconciliation_rejects_export_older_than_received_updates(self):
        self.log.ingest(self.fields(), BASE + 10)
        with self.assertRaises(ValueError):
            self.log.reconcile(export(), BASE + 20, captured_at=BASE + 5)
        self.assertEqual(len(self.log.qsos()), 1)

    def test_contest_switch_suspends_old_warnings_without_erasing_history(self):
        for n in range(10):
            self.log.ingest(self.fields(str(n), '12:{:02d}:00'.format(n), '14' if n % 2 == 0 else '7'), BASE + n * 60)
        self.assertTrue(self.log.snapshot(BASE + 600)['findings'][0]['active'])
        self.log.ingest(dict(self.fields(), contestname='CQWPXCW'), BASE + 601)
        view = self.log.snapshot(BASE + 602)
        self.assertTrue(view['suspended'])
        self.assertEqual(view['signals'], [])
        self.assertFalse(any(f['active'] for f in view['findings']))
        self.assertTrue(self.log.findings()[0]['active'])  # retained evidence
        self.log.db.close()
        self.log = WatchLog(self.path)
        self.assertTrue(self.log.snapshot(BASE + 603)['suspended'])

    def test_edit_of_imported_qso_keeps_one_canonical_identity(self):
        self.log.reconcile(export(), BASE + 1)
        canonical = self.log.qsos()[0].id
        self.log.ingest(self.fields(kind='contactdelete'), BASE + 2)
        self.log.ingest(self.fields(kind='contactreplace', band='7'), BASE + 2.5)
        self.log.flush(BASE + 5)
        self.assertEqual([(q.id, q.band) for q in self.log.qsos()], [(canonical, '7')])
        self.assertEqual(self.log._aliases['2026-cqww-test:A'], canonical)
        self.log.db.close()
        self.log = WatchLog(self.path)
        self.assertEqual(self.log._aliases['2026-cqww-test:A'], canonical)

    def test_imported_qso_delete_without_guid_uses_unique_minute_match(self):
        self.log.reconcile(export(), BASE + 1)
        f = self.fields(clock='12:00:37', kind='contactdelete')
        f.pop('id')
        self.assertTrue(self.log.ingest(f, BASE + 40))
        self.log.flush(BASE + 43)
        self.assertEqual(self.log.qsos(), [])

    def test_cabrillo_identity_is_stable_when_earlier_row_is_inserted(self):
        first = '14000 CW 2026-11-28 1200 S53M 599 15 K1ABC 599 05 0'
        earlier = '7000 CW 2026-11-28 1159 S53M 599 15 K2ABC 599 05 0'
        a = cabrillo(export(rows=[first]), self.profile, 'S53M')
        b = cabrillo(export(rows=[earlier, first]), self.profile, 'S53M')
        self.assertEqual(a[0].id, b[1].id)

    def test_future_qso_becomes_visible_without_waiting_for_next_minute(self):
        self.log.ingest(self.fields(clock='12:00:01'), BASE)
        self.assertEqual(self.log.snapshot(BASE)['signals'][0]['band'], '')
        self.assertEqual(self.log.snapshot(BASE + 1)['signals'][0]['band'], '14')

    def test_reactivated_or_revised_breach_requires_new_acknowledgment(self):
        for n in range(10):
            self.log.ingest(self.fields(str(n), '12:{:02d}:00'.format(n), '14' if n % 2 == 0 else '7'), BASE + n * 60)
        self.log.snapshot(BASE + 600)
        self.log.acknowledge()
        self.log.ingest(self.fields('9', '12:09:00', '14', kind='contactreplace'), BASE + 601)
        self.log.snapshot(BASE + 602)
        self.log.ingest(self.fields('9', '12:09:00', '7', kind='contactreplace'), BASE + 603)
        finding = self.log.snapshot(BASE + 604)['findings'][0]
        self.assertTrue(finding['active'])
        self.assertFalse(finding['acknowledged'])

    def test_reconciliation_retains_guid_aliases_for_later_edits(self):
        self.log.ingest(self.fields(), BASE)
        self.log.reconcile(export(), BASE + 1)
        self.assertTrue(self.log.ingest(self.fields(kind='contactreplace', band='7'), BASE + 2))
        self.assertEqual(len(self.log.qsos()), 1)
        self.assertEqual(self.log.qsos()[0].band, '7')

    def test_multiple_pcs_share_one_logical_signal_counter(self):
        self.log.ingest(self.fields(), BASE)
        second = dict(self.fields('B', '12:01:00', '7', source='SECOND-PC'), radionr='2')
        self.log.ingest(second, BASE + 60)
        self.assertEqual([q.signal for q in self.log.qsos()], ['TX1', 'TX1'])
        self.assertEqual(self.log.snapshot(BASE + 61)['signals'][0]['changes'], 1)

    def test_raw_role_mapping_is_explicit_and_not_run_sp_or_radio_number(self):
        mapping = [dict(source='RUN-PC', raw='2', signal='TX1', **{'from': BASE})]
        self.log.configure(self.profile, 'S53M', mapping, '2026-cqww-test')
        f = dict(self.fields(), radionr='2', run1run2='2', isrunqso='False')
        self.log.ingest(f, BASE)
        self.assertEqual(self.log.qsos()[0].signal, 'TX1')
        self.log.ingest(dict(f, id='B', run1run2='1'), BASE + 1)
        self.assertEqual(self.log.qsos()[1].signal, '')
        self.assertIn('unassigned', self.log.health)
