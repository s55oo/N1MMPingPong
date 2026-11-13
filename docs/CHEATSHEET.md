# PingPong – operator cheat sheet

One page for every operating position. **Printable PDF:**
[PingPong-cheat-sheet.pdf](PingPong-cheat-sheet.pdf) (A4). Full manual:
[README](../README.md). For the captain (master PC): the
[captain's quick chart](PingPong-captain-chart.pdf).

## 1. What the lamp shows

| Lamp | Means | You |
|---|---|---|
| dark, `—` | Nobody on your band and mode is transmitting. | Operate normally. |
| **station color** + message, e.g. `CQ` | A station on your band and mode is transmitting: its color, its message, its name (top left) and frequency (top right). | Inband: don't transmit over it. |
| grey, white **`GO`** | RUN has just finished (3 s): the slot is free for the inband stations. | Now is the moment. The first inband to transmit gets the slot. |
| `WAIT · INB1 · 32 s` | Another inband holds the slot. Your **F1–F11, Enter, Insert** are blocked. | Wait for RUN to transmit. The slot frees itself after the release time. |
| `BLOCKED · F1` | You pressed a blocked key, so nothing was sent. | Wait, then press it again. |
| blinking border, `F12 · RUN1 · 25 s` | F12 call: the border blinks in the caller's color ("hold off, please"). Your F-keys may be blocked meanwhile. | Stop transmitting. A left click on the lamp stops the blinking. |
| fast flash, `RUN1 · QRL?` | Urgent call (Shift+F2/F4) from a station on your band and mode. It flashes (color ↔ white) for as long as the message is sent. With `PRIORITY` in the corner (Ctrl+F2/F4), your own transmission was stopped and your keys are held until it ends. | Stop and listen, at once. |
| purple ↔ white, `INB2 · INB` | Lamp test from the master PC (3 s, `TEST` in the corner). | Nothing to do: it shows your own name. |

## 2. Your keys

| Key | What it does |
|---|---|
| **F12** in S&P | The S&P stations on your band blink in your color and hold off for the call time (default 30 s). F12 again ends the call. |
| **F12** in Run | **All** stations on your band hold off for a few seconds (default 5 s). |
| **Shift+F2 / Shift+F4** | N1MM sends F2/F4 as usual. At the same time, every lamp on your band and mode flashes your name and the message while it is sent. |
| **Ctrl+F2 / Ctrl+F4** | **Priority:** stations on your band and mode that are transmitting are stopped (Esc), and their keys are held. Then your F2/F4 is sent, with the same flashing. |
| Run / S&P | Your role follows N1MM, so there is nothing to set: Run = **RUN**, S&P = **INB**. |
| Mouse | Left click on the lamp stops the blinking. Right click opens Options. **Shift+F** switches to the compact lamp. |

`F12 busy · RUN2` means a call is already running on your band. Wait until it ends.

## 3. Taking turns (ping-pong)

**RUN transmits → `GO` → the first INB takes the slot → the other INBs `WAIT` → RUN transmits again**

- The slot also frees itself after the **release time**. It is set on the master; the default is 40 s, and 0 means only RUN frees it.
- The master can switch the lock **off**. Then everybody may transmit, with no priority.
- `no RUN on 7 MHz – lock off` means there is no RUN on your band, so nobody is locked.
- Only transmissions that **N1MM starts** count. Paddle, microphone PTT and footswitch are neither seen nor blocked.

**Phone lamp:** Options ▾ → **Phone lamp QR code…**, then scan the code with the phone. The phone shows the same lamp; this needs the firewall rule.

## 4. Messages under the lamp – what to do

| Message | Do |
|---|---|
| `no data from this PC's N1MM on UDP 12062` | In N1MM: Config → Configure Ports… → **Broadcast Data**. Tick **Radio**, set its line to `127.0.0.1:12060 127.0.0.1:12062`, then click OK. |
| red `⚠ UDP 12062 also used by …` | Another program on this PC is taking N1MM's data. Tell the captain. |
| `no PingPong heard – firewall?` | Options ▾ → Network → **Allow PingPong in Windows Firewall…** (it asks for administrator rights). |
| `⚠ TWO MASTERS`, or different versions | Tell the captain (Overview: **Update all lamps…**). |
| `🔒 this PingPong network has a password – click here…` | Click the line and type the network password (ask the captain). It is checked at once. |
| Lamp gone or closed | Start `n1mm_lamps.exe` again; only one lamp runs per PC. The Overview raises an alarm when an operating lamp is silent for 30 s. |

*PingPong prevents mistakes, but it is not a hardware interlock. N1MM's own interlock still applies.*
