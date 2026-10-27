# PingPong – N1MM Logger+ UDP 12060 Lamps

> **Version:** 1.34 · Made by **S55OO** with AI assistance.
> **Download:** [latest release](https://github.com/s55oo/N1MMPingPong/releases/latest) – `n1mm_lamps.exe`, no Python needed.

PingPong shows which computer/station on the network is currently
**transmitting**. It listens to the N1MM Logger+ external UDP broadcast
(XML) on port **12060** and lights a small always-on-top lamp in that
station's color, with the caption of the function key that started the
transmission.

On top of that the lamps talk to each other, so operators can:

- give every PC its **own name and color**, the same on all lamps;
- send an **F12 call** that makes the S&P stations on the same band blink,
  and optionally **block their F-keys** while the call is on;
- **update** all PCs from GitHub, and switch back to an older version.

Two tools are provided:

| File | What it is |
|---|---|
| `n1mm_lamps.exe` / `n1mm_lamps.py` | **Lamp** – small window that lights up in the color of the station on transmit |
| `PingPong.bat` / `n1mm_watch.py` | **Watcher** – detailed list of all N1MM packets (debug/analysis, needs Python) |

---

## Screenshot

![PingPong lamp](PingPong.png)

*(Screenshot of an earlier version; the current lamp also has the
**Options ▾** menu and shows the sending PC's name in the top-left corner.)*

---

## 1. N1MM Logger+ setup

1. On each N1MM Logger+ PC: **File → Settings → Configurer →
   External Broadcast**, enable **RadioInfo** (required) and any other
   reports you want (ContactInfo, Spot, AppInfo, dynamicresults …).
2. **Broadcast Address**: your subnet broadcast (e.g. `192.168.178.255`)
   so every PC sees every station. Use the address of the network you
   run on – **LAN or VPN** – and the same setting on every PC.
3. **Broadcast Port**: `12060` (default, do not change).
4. **"Broadcast Data" must be enabled on every N1MM PC** (e.g. Run2 too),
   otherwise that PC sends nothing.

What N1MM reports – and therefore what PingPong can show:

- `IsTransmitting` is **True only for transmissions N1MM starts itself**
  (function keys, Enter/Insert messages …). Keying with the microphone
  PTT, a paddle or a footswitch is **not** reported.
- `FunctionKeyCaption` is the label of the key that **started that
  transmission**. A key that sends nothing – an empty message, a single
  space, or only a command such as `{WIPE}` – is never reported.
- Phone mode is reported as `SSB` (or `USB`/`LSB`). `Freq` is in 10 Hz
  units: `Freq=701500` means 7015.00 kHz.

---

## 2. Lamp (n1mm_lamps.exe)

### Behavior

- The lamp lights up **exactly while a station transmits**: on at
  `IsTransmitting=True`, off immediately at `False`. It shows the key
  caption with the `Fx` prefix removed (`F1 CQ` → `CQ`).
- Text color adapts to the lamp color: black on light colors, white on
  dark ones.
- The lamp's top-right corner shows the **frequency of the transmitting
  station in kHz** (e.g. `14025.00 kHz`; also the caller's frequency
  during an F12 call).
- The lamp's top-left corner shows **who is sending on this band right
  now** (e.g. `OO840G3`), or `F12: <PC>` during an F12 call; empty when
  idle.
- The line under the lamp lists every station with band, mode and
  Run/S&P, this PC first, e.g.
  `14 CW Run (me) · OO840G3 14 CW S&P · OO840G5 7 SSB Run`.
  While someone transmits: `OO840G3 transmitting · 14 CW S&P`.
  Stations not heard for a minute drop off.
- The window is always on top. **Only one lamp per PC** can run; starting
  a second one just says PingPong is already running.
- **Options ▾** (top-right) holds all settings; the **help icon** next to
  it opens this page.
- **Right-click the lamp** also opens the Options menu.

### Compact mode (Shift+F)

Click the lamp and press **Shift+F**: the title bar, the top row and the
gray margins disappear – only the lamp is left, same size and place. The
status line moves onto the lamp's bottom edge (the sender's name and the
frequency are on the lamp anyway), so no information is lost.

- **Drag the lamp** with the mouse to move it.
- **Right-click** for the Options menu; a plain click still silences an
  F12 call.
- **Shift+F** again brings back the normal window.

### Band + mode group

A lamp shows **only transmissions on the same band and mode as its own
PC's N1MM**, so separate radios never clash:

- Bands: **1.8, 3.5, 7, 14, 21, 28** MHz
- Modes: **CW, RTTY, SSB** (`SSB`, `USB` and `LSB` all count as SSB)

Example: PC1, PC2, PC3 on 14 MHz CW share one lamp picture; PC4 on 7 MHz
CW is never shown on their lamps, and vice versa. The group follows this
PC automatically when it changes band or mode. Stations outside these
bands/modes are never shown.

### My name (Options ▾ → My name…)

Give this PC a short name, e.g. `RUN1` or `RUN2` (max 12 characters).

- The name is sent to all lamps, like the color: **every lamp shows
  `RUN1` instead of the N1MM computer name** – in the lamp corner, the
  line under the lamp and for F12 calls.
- A name already used by another PC is refused.
- Empty = use the N1MM computer name again. Saved per PC in
  `pingpong.ini`.

### My color (Options ▾ → My color…)

Each PC chooses the color of **its own station on every lamp**.

- Click one of the 10 palette colors, or **More colors…** for the Windows
  color picker.
- Colors already chosen by other PCs are greyed out and show that PC's
  name; a custom color **too similar** to another PC's color is refused.
- The choice is sent to all lamps and overrides `lamps.cfg`.
- If two PCs still end up with the same color (one was offline when the
  other chose), the PC that chose it **first** keeps it; the other uses
  an automatic color and shows `color <color> is taken (<PC>)`.
- **Automatic** removes the choice. Stations without a chosen color get
  palette colors in order of appearance (red, orange, light blue, light
  green, purple, cyan, gold, pink, brown, lime green), never a color
  another PC has chosen.

### F12 call (Options ▾ → F12 call – blink S&P stations on my band)

Warn the other operators on your band without transmitting.

- Turn the option on on **every PC that may call** (saved per PC).
- Press **F12 while N1MM is the active window** → your call is **on**:
  your lamp gets a wide **blinking border** in your color and the line
  under it shows `F12 call ON (14 MHz) – F12 = off`.
- Every other PingPong PC that is **on the same band** and in **S&P**
  (N1MM `IsRunning = False`) **blinks** in your color with
  `F12 · <your station>`, framed by a wide border in your color.
  PCs in **Run** or on another band are not affected.
- Press **F12 again** → the call is off everywhere. It also ends when
  your lamp closes; on a receiving PC it ends when that PC changes band
  or goes to Run.
- On a blinking lamp, **click the lamp** to silence that call there
  (`click = silence`). A new F12 call blinks again.
- The calling PC may itself be in Run or S&P.
- N1MM still gets F12 (PingPong only watches it). Tip: give F12 a message
  that sends nothing, e.g. `F12 NULL, ` (a single space), so N1MM ignores
  it. F12 in other programs is ignored by PingPong.
- Real transmissions on the band still take priority on the lamp.

### Block my F-keys (Options ▾ → Block my F-keys while another station calls (F12))

Turn this on on the PCs that should be **stopped** during a call (saved
per PC, off by default).

- While this PC's lamp blinks for an F12 call, N1MM's function keys
  **F1–F11** are swallowed. The lamp shows `BLOCKED · F1`, the line under
  it `F-keys blocked 35 s more`, and lamp and border **swap colors** back
  and forth.
- **Esc**, Enter, letters, F12 and Alt combinations always work.
- The block ends when the caller presses F12 again, when you click the
  lamp, change band or go to Run – and **by itself after 40 s** if the
  caller forgets (the lamp keeps blinking: `block expired`).
- Only while N1MM is the active window. It does **not** stop mouse clicks
  on N1MM's buttons, microphone PTT, paddle or footswitch – it guards
  against accidental key presses, it is not a hardware interlock.

### Updates and switching back (Options ▾)

| Item | What it does |
|---|---|
| **Check for updates** | Looks for a newer release on GitHub and offers to install it. |
| **Switch version…** | Install any released version, newer or older. |
| **Revert to previous version (vX)** | Back to the version you had before the last switch, no download. Use it if a new version has a bug. |
| **Check for updates at startup** | On by default; checks ~3 s after the lamp starts. |
| **Install updates automatically** | Off by default (you are asked). When on, a newer release found at startup installs without asking. |

The new exe is downloaded next to the current one and checked against the
release's SHA-256 checksum. The running exe is renamed to
`n1mm_lamps.v<old version>.exe` (the backup for **Revert**; only one is
kept), the new one takes its place and the lamp restarts. `lamps.cfg` and
`pingpong.ini` are kept.

- Updates work in the **exe** only, not when running `n1mm_lamps.py`.
- Updates are checked only at startup – the lamp never restarts while you
  operate.
- Versions **before 1.6 have no Options menu.** To come back from one,
  double-click the kept backup (e.g. `n1mm_lamps.v1.30.exe`) or download
  the latest release.
- Without internet access the check is silently skipped.

### Diagnostics (Options ▾ → Diagnostics (log to file))

Writes what the lamp sees to `pingpong_test.log` next to the exe – use it
when something doesn't work as expected. It changes nothing on the lamp.

- every packet from this PC's N1MM (time, key caption, transmit state,
  frequency, mode);
- the state every other PingPong PC reports (band, Run/S&P, F12 call and
  block options, calls it sees, whether its F-keys are blocked);
- F12 presses, blocked keys and keyboard-watcher events.

Always off after a restart, so the log only grows while you diagnose.
Delete the file any time.

### Command line (optional)

```bat
n1mm_lamps.exe [--port 12060] [--stale 5.0] [--config lamps.cfg]
               [--discovery-port 46070] [--no-discovery]
```

- `--port` – N1MM broadcast port (default 12060).
- `--stale` – safety fallback in seconds: if the transmitting station
  sends nothing for this long, the lamp turns off (default 5.0).
- `--config` – path to `lamps.cfg` (default: next to the exe).
- `--discovery-port` – port the lamps use to talk to each other
  (default 46070, `0` = off). Must be the same on all PCs.
- `--no-discovery` – no lamp-to-lamp messages (no colors, F12 call or
  network notes).

### How the lamps find each other

- Every lamp sends a small `PingPongHello` message every 3 s (at once
  after an F12 press) as a broadcast on every network adapter of the PC –
  LAN, Wi-Fi, VPN – on UDP port **46070**. This port is separate from
  N1MM's ports on purpose: N1MM must never receive these messages.
- The lamp reads all of the PC's IPv4 addresses directly from Windows and
  re-checks them every 30 s, so it always recognises its **own** N1MM
  station (also when N1MM sends to `127.0.0.1`).
- A PC running PingPong whose N1MM traffic doesn't arrive is listed under
  the lamp:

  | Note | Meaning |
  |---|---|
  | `RUN2: N1MM not reaching here` | N1MM runs on RUN2 but its broadcast doesn't reach this PC – check RUN2's **Broadcast Address** |
  | `RUN2: no N1MM` | PingPong runs on RUN2, but its N1MM isn't broadcasting |

### lamps.cfg (optional)

Not needed – stations are detected automatically. To give stations fixed
labels/colors, put a `lamps.cfg` next to the exe with one line per station
(see `lamps.example.cfg`):

```
IP,label,color
192.168.178.55,PC1,red
192.168.178.56,Run2,orange
```

Lines starting with `#` are skipped. A newly detected station is written
to the file automatically with its palette color. A color chosen with
**My color…** on that PC overrides the file.

### Settings file: pingpong.ini

Written next to the exe when you change something in **Options**; one per
PC. `[update]`: `check_at_startup`, `install_automatically`, `f12_call`,
`block_keys`. `[station]`: `alias` (My name), `color`, `color_time` (when the
color was chosen – the earliest choice wins a conflict).

---

## 3. Troubleshooting

| Problem | What to check |
|---|---|
| Lamp never lights | **Broadcast Data** and **RadioInfo** enabled in N1MM, correct **Broadcast Address**; band/mode in the list above; the lamp only lights for N1MM-started transmissions (not mic PTT/paddle). |
| My own transmissions don't show | The lamp shows only *your band and mode* – check the line under the lamp shows your station. |
| A key doesn't show | That key sends nothing (empty message, `{WIPE}` …) – N1MM doesn't report it. |
| Other PCs don't blink on F12 | All PCs on the same version; receiving PC in **S&P** on the **same band**; F12 pressed while N1MM is active; **Diagnostics** on the calling PC shows what the other PCs report. |
| One PC never receives the other lamps | Windows Firewall on that PC must allow `n1mm_lamps.exe` (or UDP port 46070) on private networks, e.g. as administrator: `netsh advfirewall firewall add rule name="PingPong lamps UDP 46070" dir=in action=allow protocol=UDP localport=46070 profile=private,domain` |
| F12 does nothing at all | If N1MM runs **as administrator**, run PingPong as administrator too. |
| N1MM shows `IndexOutOfRangeException` in `MultiOpManager` | A lamp older than v1.9 is still running somewhere (it used port 12070, N1MM's multi-computer port) – update it. |

---

## 4. Watcher (PingPong.bat / n1mm_watch.py)

Detailed view of all UDP traffic: station table on the left, traffic on the
right, details of the selected packet at the bottom. Needs Python.

```bat
        double-click:  PingPong.bat
   or:  python n1mm_watch.py [--port 12060] [--bind 0.0.0.0] [--log FILE] [--selftest]
```

- **Stop/Listen** – toggles listening.
- **Freeze** – stops adding rows (traffic is still recorded).
- **Autoscroll** – follows the latest packet.
- **Hex/raw** – shows packet details as hex + raw XML.
- **Log to file** – JSON lines into `n1mm_pingpong.log` (next to the
  script; change with `--log`).
- **Clear** – empties the display.
- `--selftest` – checks parsing with sample packets.

---

## 5. Building the EXE

Normally you don't need to: every release is built automatically (see
below). To build locally, with Python installed, double-click
**`build_exe.bat`**, or run:

```bat
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed --noupx --name n1mm_lamps --manifest manifest.xml --icon pingpong.ico n1mm_lamps.py
copy /Y dist\n1mm_lamps.exe n1mm_lamps.exe
```

**Antivirus false positives:** a locally built EXE can be flagged by
Windows Defender or other AV engines – PyInstaller's prebuilt bootloader
is a common heuristic trigger. The released EXE is built by the
[**Build n1mm_lamps.exe**](.github/workflows/build-exe.yml) GitHub Actions
workflow, which compiles the PyInstaller bootloader **from source** on a
clean Windows runner, uses no UPX, and runs a Defender scan on the result.
Prefer the release download if AV flags a local build.

**Releases are automatic.** On every push to `main` the workflow reads
`__version__` from `n1mm_lamps.py`. If release `v<version>` doesn't exist
yet, it creates the tag and the release, with notes from
`releases/v<version>.md`, and attaches the freshly built EXE. To publish a
new version: bump `__version__`, add `releases/vX.Y.md`, push to `main`.
Existing releases are never overwritten.

---

## 6. Files

```
n1mm_lamps.py       – lamp (source)
pingpong_update.py  – self-update / switch-version logic (built into the exe)
pingpong_keys.py    – F12 key watcher and F-key block (built into the exe)
n1mm_watch.py       – traffic watcher (source)
PingPong.bat        – watcher launcher
build_exe.bat       – local one-click EXE build
manifest.xml        – PyInstaller manifest (modern common controls)
pingpong.ico        – app icon (exe, title bar, taskbar)
help.png            – help icon (embedded in the lamp)
PingPong.png        – screenshot
lamps.example.cfg   – example for lamps.cfg
releases/vX.Y.md    – release notes, one file per version
.github/workflows/build-exe.yml – automatic build and release
```

Created next to the exe on each PC (not in the repository):

```
n1mm_lamps.exe      – the lamp (download from Releases)
lamps.cfg           – optional station labels/colors, auto-written
pingpong.ini        – per-PC settings from the Options menu
pingpong_test.log   – Diagnostics log (only while Diagnostics is on)
n1mm_lamps.vX.exe   – backup kept by the updater for "Revert"
```
