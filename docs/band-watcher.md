# Central band-change watcher

The optional **Band watcher** tab in `pingpong_overview.exe` evaluates the
station log centrally. Lamps receive advisory summaries, never the complete
QSO ledger. Existing interlocks, F12, priority and SO2R behavior remain
separate. The watcher does not send radio commands, edit N1MM, block keys or
stop a transmission.

## Supported profiles

Rules were checked on 2026-11-16. Only these explicit **2026** profiles and
Multi-Single (`MS`) / Multi-Two (`M2`) categories are supported:

| Contest | MS | M2 |
|---|---|---|
| CQ WW CW / SSB | Independent RUN and MULT ten-minute holds | Eight changes per logical signal per UTC clock hour |
| CQ WW RTTY | Eight changes per UTC clock hour independently for RUN and MULT | Eight per logical signal per UTC clock hour |
| CQ WPX CW / SSB / RTTY | Ten changes per UTC clock hour for the entry | Eight per logical transmitter per UTC clock hour |

A return trip counts twice. An hour reset retains the previous band. The
first QSO on a new band determines the change's hour; mere tuning does not
increment a counter. Reaching the limit is not itself a breach.

For WW CW/SSB MS, retrospective log checking uses the FAQ's Cabrillo clock
minutes: first QSO at 14:00 permits a logged change at 14:10. The operational
countdown is deliberately more conservative: it holds ten minutes from the
QSO's actual seconds when available. A Cabrillo-only QSO has no seconds, so
it uses the end of that minute for this operational countdown. These are
separate calculations. An observed N1MM transmission during this conservative
hold is a **possible early-TX warning**, not a proven logged breach.

Sources:

- [CQ WW rules](https://www.cqww.com/rules/) and [FAQ](https://www.cqww.com/rules/faq.htm)
- [CQ WPX rules](https://cqwpx.com/rules/) and [FAQ](https://cqwpx.com/rules_faq.htm)
- [CQ WW RTTY rules](https://cqwwrtty.com/rules.htm)
- [CQ WPX RTTY rules](https://cqwpxrtty.com/rules.htm)
- [N1MM external UDP messages](https://n1mmwp.hamdocs.com/appendices/external-udp-broadcasts/)
- [WW RTTY Cabrillo layout](https://cqwwrtty.com/cabrillo.htm), including both exchange fields
- [WPX RTTY Cabrillo layout](https://cqwpxrtty.com/cabrillo.htm)

## Set up on the master PC

1. Update Overview and every lamp. Run one Overview on the PC whose lamp
   is the unique PingPong master. Other Overview instances may view their
   local state, but cannot publish trusted watcher status. The existing
   Windows single-instance guard prevents two local Overview processes.
2. In **Band watcher**, select contest, year, category, entry callsign and a
   unique contest session name, such as `S53M-CQWW-CW-2026`. Changing entry or
   profile requires a new session. Old sessions are archived in the audit;
   QSOs and findings are isolated from the new session.
3. Add assignments for every participating N1MM station and its **sender
   IPv4 address**. Enter its N1MM StationName, not a PingPong nickname. All
   sources must be listed, even in WPX MS where the signal is `ENTRY`.
   Use `RUN`/`MULT` for WW MS, `TX1`/`TX2` for M2, and `ENTRY` for WPX MS.
4. Optionally select physical radio number or a verified N1MM role value
   to distinguish multiple logical signals on one PC. Give every assignment
   an effective **UTC** date/time. Several PCs can share a signal; new
   assignments take effect at their configured time. Conflicting equally
   recent assignments are unresolved, rather than guessed.
5. Select **Save and enable**. Use **Allow watcher firewall…** on Windows
   to request inbound UDP 12063 for private/domain networks, and confirm
   the rule. No firewall change happens merely by opening Overview.

`IsRunning` and `IsRunQSO` are Run/S&P operating indicators, not contest
RUN/MULT assignments. `RadioNr` is a physical radio, not automatically a
Cabrillo signal. N1MM documents `run1run2` for Multi-Two, but the watcher does
not guess its numeric encoding or assume an MS interpretation. Before using
a role-value assignment, compare actual packets with that station's N1MM
role setting and exported Cabrillo transmitter column. A per-PC/radio
assignment is available when that metadata is absent or insufficient.

Assignments are captured on ingestion. Editing the current mapping does not
rewrite already logged signal identities. Use the QSO signal correction
control for recent contacts, or correct the logical assignment in N1MM and
reconcile a new complete export to update earlier history. Recalculation
includes dependent subsequent band changes. N1MM's database is never written.

## Configure N1MM data

On **each participating N1MM PC**, add `MASTER-IP:12063` as a destination for
**Application Info, Contacts and Radio** in Configurer → Broadcast Data.
Preserve all existing destinations, including the local lamp's
`127.0.0.1:12062`. For example, the Radio line might become:

```
127.0.0.1:12062 10.0.10.104:12063
```

This watcher uses each PC's own broadcasts; it does not depend on a single
PC forwarding the whole network log with All Computers. Keep existing
All Computers settings as needed for other software. Forwarded duplicates
are deduplicated by QSO identity when accepted; source-IP validation rejects
unexpected senders. N1MM input is unsigned UDP, so the IP/station allow list
is not cryptographic authentication. Keep it on the station's trusted network.
Watcher summaries use PingPong's existing network-password authentication and
master authorization. Configure the same password throughout the network.

N1MM emits RadioInfo periodically (normally every ten seconds), which also
provides source liveness. A configured source not heard for 35 seconds marks
history as requiring reconciliation. Reconnection does not silently restore
completeness. All station computer clocks must be synchronized; watcher
summaries that are stale or materially future-dated are rejected.

## Bootstrap, recovery and completeness

A UDP stream cannot replay earlier QSOs or prove that every contact packet
was delivered. The watcher therefore starts with **history incomplete**.

- Pause logging across the station briefly, export a **complete current
  Cabrillo 3 log** from the synchronized N1MM log, and import it with
  **Import complete Cabrillo…** before resuming. Do this before the contest
  where possible, and after restart or any known gap. Use the original
  export file, not an old copy with a newly changed modification time.
- The importer checks entry, contest, category, year, mode and band, and
  requires START/END-OF-LOG. WW RTTY's two exchange fields are supported.
  Multi-signal profiles require the Cabrillo transmitter column: 0/1 means
  RUN/MULT for WW MS and TX1/TX2 for M2. WPX MS uses one entry-wide counter.
- An export older than already received contact mutations is rejected, to
  avoid overwriting live updates. Pause logging and export again. The
  importer cannot prove that a manually supplied file contains every QSO;
  the captain is responsible for supplying the full synchronized log.
- After import the state reads **snapshot reconciled; live UDP delivery
  unverified**. This is deliberately not an unconditional “all clear.” A
  heartbeat can establish that a source is alive, but cannot prove that a
  separate QSO datagram was delivered. Reconcile periodically and whenever
  the feed, network, role assignments or logging state is uncertain.
- The central `bandwatch.sqlite` file stores the QSO projection, assignments,
  aliases, pending deletes, acknowledgment state and audit history. Restart
  preserves evidence but requires a new reconciliation. Back up this file
  while Overview is stopped. A storage error does not prevent the existing
  Overview features from starting; watcher storage must be repaired before
  enabling or configuring it.

Cabrillo has no N1MM GUIDs and only minute timestamps. Unique imported
contacts can be bound to later live GUIDs; aliases persist across restart.
Ambiguous duplicate contacts, unknown edits, conflicting replays and missing
identity are reported as requiring reconciliation instead of guessed.
Edits use the documented delete/replace sequence with a two-second delete
hold. Deletes may be matched by unique timestamp/call when no GUID is sent.
Reversed edit/delete delivery is treated conservatively and requests
reconciliation. Unknown/changed contest or entry suspends monitoring and
suppresses old-session warnings until the captain configures and reconciles
the correct session. A stale packet cannot switch the profile automatically.

## Warnings and red flashing

Every updated lamp receives station-wide findings, even on an unrelated band.
**An active, unacknowledged logged-QSO breach makes every client flash red
and dark**, preserving its transmitting station/caption. Affected operators
also get a `YOUR SIGNAL` indication and a sound notification. The local
lamp sound preference controls the sound, not the red flash.

The captain's **Acknowledge alerts** stops the station-wide flashing without
deleting evidence. A correction that resolves the breach also stops its
flash. Reintroduced or revised breaches require a new acknowledgment.
**Silence current band watcher alert** on a lamp silences its sound only;
it does not acknowledge the station-wide finding. Possible early-TX warnings
remain visually distinct and do not claim a logged breach or trigger the
logged-breach red flash.

Timers/counters describe the current restriction; timestamped findings are
historical evidence. Findings stay inspectable after the hour resets or
edits resolve them. **Export audit…** saves the ledger and event/revision
history as JSON. The Overview web page includes watcher summaries/status;
phone lamp views include the warning and the desktop lamp's flashing colors.

Only the master Overview publishes status. Messages include version,
publication-instance/session identity, sequence and issue time. Lamps reject
out-of-order, duplicate, stale and unauthorized state. After twelve seconds
without fresh trusted status, a previously enabled lamp shows **Band watcher
unavailable** and stops treating the old status as current. Master changes
invalidate the previous master's state. Older lamps ignore the new command
and retain their existing behavior; they cannot display watcher alerts.

## Coverage and validation limits

This is a band-change timing/counting advisory, not complete contest-rule
certification. It does not establish multiplier eligibility, detect prohibited
CQ solicitation, verify simultaneous RF signals, or monitor manual PTT/paddle
TX that N1MM does not report. Those checks remain outside its coverage.
N1MM-originated early-TX observations are distinct from logged-QSO findings.
No automatic radio control or enforcement is introduced.

Tests use explicitly synthetic N1MM packets and CQ-layout exports. A native
Tk build/render smoke check verifies the watcher tab can be constructed;
it does not substitute for a Windows executable or live multi-PC/radio test.
