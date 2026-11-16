# Status – shared radio (RUN2 / RUN3), 14–16 Nov 2026

**Update – v1.115 (16 Nov): the echo filter has its own 3 s limit.** Found in a code review, not seen on the
station. The filter decided "the other PC is still transmitting" with the display timeout `--stale` (15 s since
v1.101 / PR #2, which is right for the display: N1MM repeats each radio every 10 s). If the other PC's "TX off"
packet was lost (UDP, e.g. over ZeroTier), this PC's TX packets without caption were ignored as echoes until
N1MM's next status packet – up to 10 s. Now the other PC counts as transmitting for the echo decision only for
`ECHO_BUSY` = 3 s after its last packet; `--stale` is unchanged. Basis for 3 s: in all station logs of
14–15 Nov, the 55 echoes that came while the other PC transmitted arrived at most 1.6 s after that PC's last
packet (median 0.15 s). **Trade-off:** an echo that comes more than 3 s into a long message of the other PC,
with no packet from it in between, is now shown (red, no message). Not seen in the logs, but possible.

**Versions since v1.108** (all only N1MM `RadioInfo`, nothing from vRadio):

| Version | What | Station |
|---|---|---|
| v1.109 | Diagnostics, second layer: `PP` lines (N1MM's values → PingPong's verdict) and `PP  LAMP shows` | used for the logs below |
| v1.110 | Overview: Diagnostics ON / OFF on all lamps | not reported |
| v1.111 | Echo only while the other PC transmits, or 0.3 s after its end (was 1.5 s). From the log of 12:27–12:28: 3 of 8 ignored packets were not echoes | log of 13:36–13:39: 12 starts, all with the right message; 3 echoes ignored, all real |
| v1.112 | Date and time in the Diagnostics header | – |
| v1.113 | Hover comment on the help icon | not reported |
| v1.114 | Collect logs from the Overview, log rotation (5 MB), DIAG mark, contest checklist | **not yet run on the station** |
| v1.115 | Echo filter: own 3 s limit instead of `--stale` | **not yet run on the station** |

**Known open points (code review, 16 Nov):**

- The echo filter acts for **every** pair of PCs on the same band and mode, not only for PCs that share a radio.
  With two radios, a TX packet without caption from one PC is hidden while the other transmits. In the logs a
  PC with its own TX always sent the caption (RUN3: 0 of 439 without), so it may never happen.
- ~~Collect logs has no retransmit; a log with a lost piece is dropped without a message.~~ Solved in v1.116
  (missing pieces are asked for again; every lamp gets a line in the event log). Not yet run on the station.
- Still only short tests (longest 3 minutes) and only RUN2's log. A 15-minute test on both PCs is worth doing.

**Update – v1.108 (15 Nov): echo filter, based on a real capture.** The log of RUN2 (v1.101 session, 20:22–21:40)
showed what N1MM really sends: each PC reports its own TX with the key's caption, but RUN2's N1MM also reports
TX **without caption** right after RUN3's captioned TX (an echo of the shared radio) – 116 of its 270 TX packets.
v1.108 ignores such packets (and keeps the caption of a message that goes on). Replayed on that log: RUN2's
transmissions shown without a message 99 → 6; RUN3 unchanged. It uses only N1MM's `RadioInfo`, nothing from vRadio.
**Confirmed on the station (15 Nov, 12:05, v1.108 on both PCs):** the operator saw no empty messages and RUN2 showed
the correct messages; in the log of that run all 3 captionless TX packets of RUN2 came 0.06–1.2 s after RUN3's TX
(echoes) and none after RUN2's own message. Short test (40 s, 31 TX packets, RUN2's side only) – a longer, busier one
is still worth doing before the contest. The history below (v1.102–v1.107) stays as it was.

**Update – v1.107 (14 Nov, 23:50): rolled back.** v1.107 has exactly the code of v1.101 (PR #1 and
PR #2 kept); the experiments of v1.102–v1.106 are removed from the code but stay in git history
(commits below) and on their release pages. The text below describes how it came to that.

**Short version:** v1.101 is the last version that is known to work (PR #2, SO2R radio state,
merged and released). Versions v1.102 to v1.106 tried to fix a problem with **two PCs
sharing one radio**. None of them has been confirmed on the real station, and the last
reports were "not ok". This file says what was tried, what is known, what is not, and how to
go back to v1.101.

## The station and the problem

| | |
|---|---|
| RUN2 | 192.168.0.240 – vRadio **host** (ZeroTier 10.147.17.116 in the vRadio example) |
| RUN3 | 192.168.0.76 – vRadio **client** |
| Radio | one Yaesu FTDX101D, shared by both PCs through N1MM_vRadio2 (`pcs = 2`) |
| Band | both on 3.5 MHz (80 m), CW |

Symptoms, in the order they were reported:

1. The lamp showed **s50c-e8460p / RUN2 as transmitting** while the other PC keyed, and the
   view switched between the two. (Also before v1.101 – so it is **not** caused by the SO2R change.)
2. After v1.102: "much better", but sometimes **RUN2 red without a message**.
3. After v1.104 / v1.105 / v1.106: **"messages don't go from RUN2 to RUN3"**; "not ok".

## What is known (evidence)

- N1MM's `IsTransmitting` on a **shared radio** is not reliable: both N1MMs see the radio
  transmit and both report TX on it. (Confirmed by the vRadio author's document
  `PINGPONG_SHARE.md` in N1MM_vRadio2.)
- The CAT log from vRadio (20:23–20:26) shows the VFOs swapped between the radio side and the
  N1MM side (A = 3.5277 / B = 3.5231 on the radio; reversed towards N1MM) and a `TX -> RX`
  flicker about every second. It contains **no PingPong data**.
- vRadio 1.0.9+ with `pcs = 2` sends a `vRadioShare` packet to UDP 46070 (every second and at
  each TX change): `Id, Host, Version, Ips, Radio, HostVfo, ClientVfo, Clients, Tx, TxFrom, Seq`.
  `Tx` = none / A / B; `TxFrom` = a client IP, `local`, `dj`, or empty.
- The Broadcast Data → Radio line is the same on both PCs (`127.0.0.1:12060 127.0.0.1:12062`).
- Lamp-to-lamp delivery code works (real UDP test of the discovery loop: relay and vRadio
  packets arrive). It is not a code regression in the network layer.

## What is NOT known

- **Nobody has seen a real capture** from RUN2 or RUN3 during the problem. No
  `pingpong_test.log` / Watcher output was received. The log file on `oo840g4` (the PC linked
  to the session) is from 4 Nov and has nothing to do with this.
- Whether RUN2 and RUN3 hear each other at all (the status line under the lamp was never
  reported).
- Which `Mode` text N1MM sends on each PC (CW vs `CW-R`).
- Whether vRadio's `Ips` / `Clients` match the addresses PingPong knows for each PC.

## What was tried

| Version | Commit | What | Result on the station |
|---|---|---|---|
| **v1.100** | `612cd95` | PR #1: `RTTY-R` counts as RTTY; USB/LSB/SSB case-insensitive | fine |
| **v1.101** | `a0d49d8` | **PR #2: SO2R radios tracked separately, `--stale` 15 s. LAST KNOWN GOOD** | fine |
| v1.102 | `f7882b3` | `SharedTx`: on the **same frequency** the first PC to report TX owns it, others ignored while it transmits + 1.75 s | "much better" |
| v1.103 | `95c4b55` | caption may come in any packet; caption memory 1.5 s; takeover if the echo came first | still "RUN2 red without message" |
| v1.104 | `e2350d8` | `vRadioShare` packet decides who transmits (pairing by IP, `Tx`/`TxFrom`); PCs not paired are unchanged | **"messages don't go from RUN2 to RUN3"** |
| v1.105 | `f94b5a5` | Diagnostics log lines for `vRadioShare` + verdict per PC | no log received |
| v1.106 | `2f512e5` | `CW-R/CW-U/CW-L/CWR` count as CW; `FSK/FSK-R` as RTTY | "not ok" |

Honest assessment: v1.102–v1.106 were **reasoned from the symptoms and the vRadio document,
not from captures**. They pass 236 unit tests, but unit tests only prove the rules do what I
meant, not that the station sends what I assumed. The most likely suspects for the failure
after v1.104 are the IP pairing with `vRadioShare` (a paired PC whose address does not match
is judged "does not transmit") and the mode strings.

## Back to v1.101 (done in v1.107)

All code of v1.102–v1.106 sits in a few commits, so it can be removed cleanly. Pick one:

**A. New release with the code of v1.101 (recommended – the updater only offers higher numbers):**

```
git checkout main && git pull
git checkout a0d49d8 -- .                 # files exactly as in v1.101
# keep this file and the release notes:
git checkout main -- docs/STATUS-shared-radio.md releases/
# set pingpong_version.py to the next number, e.g. 1.107; write releases/v1.107.md:
#   "Same code as v1.101 (rollback of the shared-radio experiments v1.102-v1.106)"
git add -A && git commit -m "v1.107: rollback to the code of v1.101" && git push
```

CI then builds, tests, scans and publishes v1.107; **Update all lamps…** installs it everywhere.

**B. Without a new release:** on each PC keep the old exe. Each lamp keeps a backup of its
previous version (`n1mm_lamps.vX.exe`, **Options ▾ → Updates → Revert**); the Overview has
the same. The release pages of v1.101 (and v1.102–v1.106) still have their exes on GitHub.

**What you lose by going back:** `SharedTx` (same-frequency rule), the `vRadioShare` support
and its diagnostics, and the CW-R / FSK aliases. What stays: PR #1 and PR #2.
The CW-R alias is small and harmless – it can be kept (`MODE_ALIASES` in `pingpong_common.py`)
if you want it.

**Only v1.100/1.101 behaviour on a shared radio:** the lamp may again show the PC that only
*echoes* the radio's TX (symptom 1). Workaround until a proper fix: none in PingPong.

## What is needed for a proper fix

One real capture, taken while the problem happens, from **RUN3** (or RUN2):

1. **Options ▾ → Diagnostics (log to file)** on, then key F2 on RUN2, wait 2 s, F2 on RUN3,
   switch Diagnostics off. The log is `pingpong_test.log` **next to the exe of that lamp**.
2. Or run the Watcher (`PingPong.bat`) and copy ~10 lines.
3. Also: what the status line under the lamp says on RUN3 (does it list RUN2, or a warning
   such as `no PingPong heard – firewall?` / `⚠ TWO MASTERS` / 🔒), and whether the problem
   began only after vRadio was switched to `pcs = 2`.

From that: the `Mode`/`Freq` each PC sends, whether the TX flag and the caption arrive, whether
the packets of the other PC arrive at all, and what vRadio's pairing says for each PC.
The vRadio side can also fix it at the source: report TX only to the PC that keys (or set
`pingpong_port = 0` to switch its PingPong packet off and test with v1.103 behaviour).

## Housekeeping still open

- GitHub releases are many: keep the last three. The session cannot delete them (HTTP 403):
  `"1.96","1.97","1.98","1.99" | % { gh release delete "v$_" --repo s55oo/N1MMPingPong --cleanup-tag -y }`
- The release notes of v1.90 have a stray `n` around a code fence (fix by hand on GitHub).
- The PingPong folder on `oo840g4` still has old exes (`n1mm_lamps.exe` ≈ v1.97,
  `pingpong_overview.exe` ≈ v1.93); the sources there were synced to v1.106.
- The link from the Claude app to the PC was unstable on 14 Nov (dropped twice); computer
  control of Explorer is click-only, so a lamp cannot be started from the session.
- Session: https://claude.ai/code/session_0111qV5FWpUdDw1LzrU84vTe
