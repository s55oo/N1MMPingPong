"""Test sender for the BAND VIOLATION alert (no N1MM_timer needed).

Run on the master PC, next to a running lamp:

    python tools/send_band_alert.py            # violation for 12 s, then "ok"
    python tools/send_band_alert.py --station PC2 --seconds 20
    python tools/send_band_alert.py --stop     # violation, then go silent (-> offline)

It sends the N1MM_timer interface-v1 message to UDP 127.0.0.1:46071 every 2 s,
like N1MM_timer does. Every lamp and the Overview should show a red
"BAND VIOLATION: ..." footer, then clear it (or show "Band watcher offline").
"""

import argparse
import json
import socket
import time
import uuid


def message(instance, seq, status, station):
    violations = []
    if status == "violation":
        violations = [
            {"id": "TEN_MIN:test-1", "kind": "TEN_MIN", "signal": "RUN", "station": station,
             "from_band": "20", "to_band": "15",
             "first_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 90)),
             "call": "K1AR", "qsos": 3, "text": "TEST: RUN 20->15 m - 3 QSOs"},
            {"id": "HOURLY:test-2", "kind": "HOURLY", "signal": "MULT", "station": station,
             "from_band": "40", "to_band": "80", "qsos": 1, "text": "TEST: MULT 9th band change"},
        ]
    return {
        "type": "n1mm_timer.alert", "v": 1, "instance": instance, "seq": seq,
        "sent_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ttl_s": 10, "status": status, "profile": "CQWW-CW-MS", "violations": violations,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=46071)
    ap.add_argument("--station", default="", help="N1MM StationName that 'logged' it (YOUR STATION)")
    ap.add_argument("--seconds", type=float, default=12.0, help="how long the violation is shown")
    ap.add_argument("--stop", action="store_true", help="go silent instead of sending 'ok'")
    args = ap.parse_args()
    station = args.station or socket.gethostname().upper()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    instance, seq = uuid.uuid4().hex[:8], 0

    def send(status):
        nonlocal seq
        seq += 1
        data = json.dumps(message(instance, seq, status, station)).encode("utf-8")
        sock.sendto(data, ("127.0.0.1", args.port))
        print("{}  seq {}  {}".format(time.strftime("%H:%M:%S"), seq, status))

    end = time.time() + args.seconds
    while time.time() < end:
        send("violation")
        time.sleep(2)
    if args.stop:
        print("silent now: after 10 s the screens show 'Band watcher offline'")
        return
    for _ in range(3):
        send("ok")
        time.sleep(2)


if __name__ == "__main__":
    main()
