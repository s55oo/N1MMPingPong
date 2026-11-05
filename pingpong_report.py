"""After-contest report: one HTML page from the Overview's statistics and
event log (Activity tab → Contest report…).

build_report() is pure (no window, no files) so it can be tested; the
Overview reads the data, writes the page next to its exe and opens it.
"""

import csv
import html
import io
import time

FKEYS = ["F{}".format(i) for i in range(1, 13)]

# Event log lines that are counted per station in the summary table.
COUNTED = {
    "ALARM lamp not heard": "alarms",
    "lamp not heard": "lost",
    "lamp restarted": "restarts",
    "no ping reply": "ping_fail",
}
# Frequent lines left out of the "Notable events" list (they are counted).
ROUTINE = {"ping-pong WAIT", "ping-pong free", "F12 call", "urgent call", "slow ping",
           "no ping reply", "lamp heard again", "lamp on"}
MAX_EVENTS = 300


def hms(seconds):
    seconds = int(seconds or 0)
    return "{}:{:02d}:{:02d}".format(seconds // 3600, seconds // 60 % 60, seconds % 60)


def read_events(path, since):
    """Event log rows (dicts) from `since` (epoch seconds) on; [] if none."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            text = fh.read()
    except OSError:
        return []
    rows = []
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        try:
            when = time.mktime(time.strptime(row["date"] + " " + row["time"], "%Y-%m-%d %H:%M:%S"))
        except (KeyError, TypeError, ValueError):
            continue
        if when >= int(since):  # the log has whole seconds
            row["when"] = when
            for k in ("station", "details"):  # undo the spreadsheet guard for display
                v = row.get(k) or ""
                if v.startswith("'") and v[1:2] in ("=", "+", "-", "@"):
                    row[k] = v[1:]
            rows.append(row)
    return rows


def _e(text):
    return html.escape(str(text))


def _heat_color(minutes):
    """0 min white … 60 min dark red (TX minutes in one hour)."""
    if minutes <= 0:
        return ""
    level = min(1.0, minutes / 30.0)
    light = int(95 - 55 * level)
    return "background:hsl(4,70%,{}%);color:{}".format(light, "#fff" if light < 60 else "#000")


def _station_tables(add, stations, counted, total_tx, total_n, hours):
    """Stations, Function keys and TX-per-hour tables."""
    add("<h2>Stations</h2><div class='wrap'><table><tr>")
    for h in ("Station", "TX time", "Share", "Transmissions", "Busiest hour", "Waits (WAIT)",
              "Waited", "F12 calls", "Urgent", "Ping avg", "Ping max", "Ping lost",
              "Not heard", "Alarms", "Restarts"):
        add("<th>{}</th>".format(h))
    add("</tr>")
    for s in stations:
        name = s.get("name", "?")
        tx = s.get("tx_time", 0.0)
        share = tx / total_tx if total_tx else 0.0
        n = sum(s.get("keys", {}).values()) + s.get("other", 0)
        hrs = s.get("hours") or {}
        busiest = max(hrs.items(), key=lambda kv: kv[1]) if hrs else None
        pn = s.get("ping_n", 0)
        c = counted.get(name, {})
        cells = [
            _e(name), hms(tx),
            "<span class='bar' style='width:{}px'></span> {:.0f}%".format(int(60 * share), 100 * share),
            n,
            "{} ({:.0f} min)".format(_e(busiest[0][-2:] + ":00"), busiest[1] / 60.0) if busiest else "",
            s.get("locked", 0) or "", hms(s.get("locked_time", 0.0)) if s.get("locked_time") else "",
            s.get("f12", 0) or "", s.get("urgent", 0) or "",
            "{:.1f} ms".format(s["ping_sum"] / pn) if pn else "",
            "{:.1f} ms".format(s.get("ping_max", 0)) if pn else "",
            s.get("ping_lost", 0) or "",
            c.get("lost", "") or "", c.get("alarms", "") or "", c.get("restarts", "") or "",
        ]
        add("<tr>")
        for i, v in enumerate(cells):
            warn = i in (11, 13) and v not in ("", 0)
            add("<td{}>{}</td>".format(" class='warn'" if warn else "", v))
        add("</tr>")
    add("</table></div>")

    # ---- function keys
    add("<h2>Function keys</h2><div class='wrap'><table><tr><th>Station</th>")
    add("".join("<th>{}</th>".format(k) for k in FKEYS) + "<th>Other</th><th>Total</th></tr>")
    tot = dict.fromkeys(FKEYS + ["other"], 0)
    for s in stations:
        keys = s.get("keys", {})
        add("<tr><td>{}</td>".format(_e(s.get("name", "?"))))
        for k in FKEYS:
            tot[k] += keys.get(k, 0)
            add("<td>{}</td>".format(keys.get(k, 0) or ""))
        tot["other"] += s.get("other", 0)
        add("<td>{}</td><td>{}</td></tr>".format(s.get("other", 0) or "",
                                                 sum(keys.values()) + s.get("other", 0)))
    if len(stations) > 1:
        add("<tr class='all'><td>All</td>" + "".join(
            "<td>{}</td>".format(tot[k] or "") for k in FKEYS)
            + "<td>{}</td><td>{}</td></tr>".format(tot["other"] or "", total_n))
    add("</table></div>")

    # ---- TX minutes per hour
    if hours:
        add("<h2>TX minutes per hour</h2><div class='wrap'><table class='heat'><tr><th>Station</th>")
        last_day = ""
        for h in hours:
            day, hour = h[:10], h[-2:]
            label = hour if day == last_day else "{}<br>{}".format(day[5:], hour)
            last_day = day
            add("<th>{}</th>".format(label))
        add("</tr>")
        for s in stations:
            hrs = s.get("hours") or {}
            add("<tr><td>{}</td>".format(_e(s.get("name", "?"))))
            for h in hours:
                mins = hrs.get(h, 0.0) / 60.0
                add("<td style='{}'>{}</td>".format(
                    _heat_color(mins), "{:.0f}".format(mins) if mins >= 0.5 else ("·" if mins else "")))
            add("</tr>")
        add("</table></div><p class='note'>Hours in local time; darker = more transmitting.</p>")


def build_report(stats, since, now, events, title="PingPong contest report"):
    """HTML page. stats: the Overview's per-station statistics (as saved in
    pingpong_stats.json); events: rows from read_events()."""
    stations = sorted(stats.values(), key=lambda s: s.get("name", "").lower())
    counted = {}
    for ev in events:
        key = COUNTED.get(ev.get("event", ""))
        if key:
            per = counted.setdefault(ev.get("station", ""), {})
            per[key] = per.get(key, 0) + 1
    total_tx = sum(s.get("tx_time", 0.0) for s in stations) or 0.0
    total_n = sum(sum(s.get("keys", {}).values()) + s.get("other", 0) for s in stations)
    hours = sorted({h for s in stations for h in (s.get("hours") or {})})

    out = []
    add = out.append
    add("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    add("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    add("<title>{}</title>".format(_e(title)))
    add("""<style>
  body { font: 14px/1.45 "Segoe UI", system-ui, sans-serif; color:#1d2330; margin:24px; max-width:1100px; }
  h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:16px; margin:28px 0 8px; }
  .meta { color:#555; margin-bottom:12px; }
  .kpis { display:flex; gap:12px; flex-wrap:wrap; margin:12px 0; }
  .kpi { border:1px solid #dde1e7; border-radius:8px; padding:8px 14px; min-width:120px; }
  .kpi b { display:block; font-size:20px; }
  .kpi span { color:#555; font-size:12px; }
  .wrap { overflow-x:auto; }
  table { border-collapse:collapse; }
  th, td { border:1px solid #dde1e7; padding:4px 8px; text-align:right; white-space:nowrap; }
  th { background:#f3f5f8; font-size:12px; }
  td:first-child, th:first-child { text-align:left; font-weight:600; }
  tr.all td { font-weight:700; background:#f8f9fb; }
  .bar { display:inline-block; height:10px; background:#3b82f6; border-radius:2px; vertical-align:middle; }
  td.warn { color:#b00020; font-weight:700; }
  .heat td { min-width:34px; text-align:center; font-size:12px; }
  .ev td, .ev th { text-align:left; } .ev td:first-child { font-weight:400; color:#555; }
  .note { color:#555; font-size:12px; }
  @media print { body { margin:10mm; } h2 { break-after:avoid; } }
</style></head><body>""")
    add("<h1>{}</h1>".format(_e(title)))
    add("<div class='meta'>From {} to {} · {} stations · made by PingPong Overview</div>".format(
        _e(time.strftime("%Y-%m-%d %H:%M", time.localtime(since))),
        _e(time.strftime("%Y-%m-%d %H:%M", time.localtime(now))), len(stations)))

    alarms = sum(c.get("alarms", 0) for c in counted.values())
    kpis = [
        (hms(total_tx), "total TX time"),
        (total_n, "transmissions started"),
        (sum(s.get("locked", 0) for s in stations), "ping-pong waits"),
        (sum(s.get("f12", 0) for s in stations), "F12 calls"),
        (sum(s.get("urgent", 0) for s in stations), "urgent calls"),
        (alarms, "\"not heard\" alarms"),
    ]
    add("<div class='kpis'>" + "".join(
        "<div class='kpi'><b>{}</b><span>{}</span></div>".format(_e(v), _e(t)) for v, t in kpis)
        + "</div>")

    if not stations:
        add("<p>No statistics yet.</p>")
    else:
        _station_tables(add, stations, counted, total_tx, total_n, hours)

    # ---- notable events
    notable = [ev for ev in events if ev.get("event") not in ROUTINE]
    add("<h2>Notable events</h2>")
    if not notable:
        add("<p class='note'>None – no alarms, lost lamps or commands in this period.</p>")
    else:
        add("<div class='wrap'><table class='ev'><tr><th>Time</th><th>Station</th>"
            "<th>Event</th><th>Details</th></tr>")
        for ev in notable[-MAX_EVENTS:]:
            add("<tr><td>{} {}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                _e(ev.get("date", "")[5:]), _e(ev.get("time", "")), _e(ev.get("station", "")),
                _e(ev.get("event", "")), _e(ev.get("details", ""))))
        add("</table></div>")
        if len(notable) > MAX_EVENTS:
            add("<p class='note'>Last {} of {} shown – all of them are in "
                "pingpong_events.csv.</p>".format(MAX_EVENTS, len(notable)))
    add("</body></html>")
    return "".join(out)
