"""PingPong Overview - the web page (TCP 8046) and its firewall rule.

WebMixin (used by Overview in pingpong_overview.py): serves the same
live table to any browser on the network, read only.
"""

import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tkinter import messagebox

import n1mm_lamps as pp
from pingpong_overview_common import *  # noqa: F401,F403


class WebMixin:
    def _serve_web(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/data.json"):
                    body = json.dumps(app.snapshot).encode("utf-8")
                    ctype = "application/json; charset=utf-8"
                elif self.path in ("/", "/index.html"):
                    body = PAGE.encode("utf-8")
                    ctype = "text/html; charset=utf-8"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass  # quiet

        try:
            server = ThreadingHTTPServer(("0.0.0.0", self.web_port), Handler)
        except OSError as exc:
            self.web_error = "web page off - port {} busy: {}".format(self.web_port, exc)
            return
        server.daemon_threads = True
        server.timeout = 0.5
        while not self.stop.is_set():
            server.handle_request()
        server.server_close()

    def web_urls(self):
        ips = []
        try:
            ips = [ip for ip, _ in pp.local_networks()]
        except Exception:
            pass
        names = [socket.gethostname()] + ips
        return ["http://{}:{}".format(n, self.web_port) for n in names]

    def _check_firewall(self):
        """Look (in the background) for our inbound rule for the web page;
        when it is there, hide the firewall button and show the address in
        the status line."""
        if sys.platform != "win32" or not self.web_port:
            return

        def work():
            found = pp.firewall_rule_exists(FIREWALL_RULE)
            self.root.after(0, lambda: self._firewall_checked(found))

        threading.Thread(target=work, daemon=True).start()

    def _firewall_checked(self, found):
        self.fw_open = found  # Settings ▾ and the bottom line show it

    def _allow_firewall(self):
        """Let other PCs / phones open the web page (asks for admin)."""
        if sys.platform != "win32":
            return
        if not messagebox.askyesno(
            TITLE,
            "Allow incoming TCP {} (the overview web page) on private and "
            "domain networks?\n\nWindows asks for administrator rights.".format(self.web_port),
            parent=self.root,
        ):
            return
        rule = (
            'netsh advfirewall firewall delete rule name="{0}" >nul 2>&1 & '
            'netsh advfirewall firewall add rule name="{0}" dir=in action=allow '
            "protocol=TCP localport={1} profile=private,domain"
        ).format(FIREWALL_RULE, self.web_port)
        try:
            import ctypes

            result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", "/c " + rule, None, 0)
        except Exception:
            result = 0
        if result > 32:
            messagebox.showinfo(TITLE, "Firewall rule added (TCP {}).".format(self.web_port), parent=self.root)
            self.root.after(2000, self._check_firewall)  # hides the button
        else:
            messagebox.showwarning(TITLE, "The rule was not added (no administrator rights given).",
                                   parent=self.root)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PingPong Overview</title>
<style>
  :root { --bg:#f6f7f9; --card:#fff; --text:#1d2330; --muted:#6b7280; --line:#e3e6eb; --warn:#c00000; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#14171c; --card:#1c2027; --text:#e8eaee; --muted:#9aa1ad; --line:#2c323c; --warn:#ff6b6b; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:14px/1.4 "Segoe UI", system-ui, -apple-system, sans-serif; }
  header { padding:12px 16px 8px; }
  h1 { font-size:18px; margin:0 0 4px; }
  #head { font-weight:600; }
  #head.warn { color:var(--warn); }
  #head.ok { color:#1f8a3b; }
  #head.alarm { color:#fff; background:#c00000; padding:2px 6px; border-radius:4px;
                animation: alarm 1s steps(1) infinite; }
  @keyframes alarm { 50% { background:#ff7070; } }
  #status { color:var(--muted); font-size:12px; }
  #status.lost { color:var(--warn); font-weight:600; }
  .wrap { overflow-x:auto; margin:0 16px; background:var(--card);
          border:1px solid var(--line); border-radius:8px; }
  table { border-collapse:collapse; width:100%; min-width:1000px; }
  th, td { padding:6px 8px; text-align:left; white-space:nowrap; border-bottom:1px solid var(--line); }
  th { font-size:12px; color:var(--muted); font-weight:600; position:sticky; top:0; background:var(--card); }
  td.c { text-align:center; } td.r { text-align:right; }
  th:first-child, td:first-child { position:sticky; left:0; background:inherit; font-weight:600; }
  th:first-child { z-index:1; } tr { background:var(--card); }
  tr.urgent td { animation: flash .3s steps(1) infinite; }
  @keyframes flash { 50% { background:#ffffff; color:#ff3030; } }
  #notes { padding:10px 16px 16px; color:var(--muted); font-size:13px; }
  #notes div.w { color:var(--warn); }
  .empty { padding:16px; color:var(--muted); }
  h2 { font-size:16px; margin:8px 16px 8px; }
  #since { font-weight:400; color:var(--muted); font-size:13px; }
  table.stats { min-width:760px; }
  table.stats td, table.stats th { text-align:center; }
  table.stats td:first-child, table.stats th:first-child { text-align:left; }
  table.stats tr.all td { font-weight:700; }
  table.heat td { min-width:44px; }
  .foot { color:var(--muted); font-size:12px; margin:8px 16px 24px; }
</style>
</head>
<body>
<header>
  <h1 id="title">PingPong Overview</h1>
  <div id="head">connecting…</div>
  <div id="status"></div>
</header>
<div class="wrap"><table><thead><tr id="cols"></tr></thead><tbody id="rows"></tbody></table>
<div class="empty" id="empty" hidden>No PingPong lamps heard yet.</div></div>
<div id="notes"></div>
<h2>Function keys <span id="since"></span></h2>
<div class="wrap"><table class="stats"><thead><tr id="scols"></tr></thead><tbody id="srows"></tbody></table></div>
<p class="foot">Transmissions started by each function key, per station. Reset and CSV export in the Overview window.</p>
<h2>Activity</h2>
<div class="wrap"><table class="stats"><thead><tr id="acols"></tr></thead><tbody id="arows"></tbody></table></div>
<h2>TX minutes per hour</h2>
<div class="wrap"><table class="stats heat"><thead><tr id="hcols"></tr></thead><tbody id="hrows"></tbody></table></div>
<p class="foot">Ping-pong waiting, F12 and urgent calls, and transmit time per station.</p>
<script>
const CENTER = new Set(["band","mode","run","role","ver"]), RIGHT = new Set(["freq","seen"]);
let cols = [];
function cell(id, text) {
  const td = document.createElement("td");
  td.textContent = text;
  if (CENTER.has(id)) td.className = "c"; else if (RIGHT.has(id)) td.className = "r";
  return td;
}
function render(d) {
  document.getElementById("title").textContent = d.title;
  document.title = d.title;
  const head = document.getElementById("head");
  head.textContent = d.head;
  head.className = (d.alarm && d.alarm.length) ? "alarm" :
    ((d.warn || d.ready === "bad") ? "warn" : (d.ready === "ok" ? "ok" : ""));
  if (cols.length !== d.columns.length) {
    cols = d.columns;
    const tr = document.getElementById("cols"); tr.innerHTML = "";
    for (const [, name] of cols) { const th = document.createElement("th"); th.textContent = name; tr.appendChild(th); }
  }
  const body = document.getElementById("rows"); body.innerHTML = "";
  for (const r of d.rows) {
    const tr = document.createElement("tr");
    if (r.tag) tr.className = r.tag;
    if (r.bg) tr.style.background = r.bg;
    if (r.fg) tr.style.color = r.fg;
    for (const [id] of cols) tr.appendChild(cell(id, r.values[id] || ""));
    body.appendChild(tr);
  }
  document.getElementById("empty").hidden = d.rows.length > 0;
  const notes = document.getElementById("notes"); notes.innerHTML = "";
  for (const n of d.notes) {
    const div = document.createElement("div"); div.textContent = n;
    if (n.startsWith("\\u26a0")) div.className = "w";
    notes.appendChild(div);
  }
  if (d.stats) {
    document.getElementById("since").textContent = "since " + d.stats.since;
    const sc = document.getElementById("scols"); sc.innerHTML = "";
    for (const name of d.stats.columns) { const th = document.createElement("th"); th.textContent = name; sc.appendChild(th); }
    const sb = document.getElementById("srows"); sb.innerHTML = "";
    for (const row of d.stats.rows) {
      const tr = document.createElement("tr"); if (row[0] === "All") tr.className = "all";
      for (const v of row) { const td = document.createElement("td"); td.textContent = v; tr.appendChild(td); }
      sb.appendChild(tr);
    }
  }
  if (d.activity) {
    const a = d.activity;
    const ac = document.getElementById("acols"); ac.innerHTML = "";
    for (const name of a.columns) { const th = document.createElement("th"); th.textContent = name; ac.appendChild(th); }
    const ab = document.getElementById("arows"); ab.innerHTML = "";
    for (const row of a.rows) {
      const tr = document.createElement("tr"); if (row[0] === "All") tr.className = "all";
      for (const v of row) { const td = document.createElement("td"); td.textContent = v; tr.appendChild(td); }
      ab.appendChild(tr);
    }
    const hc = document.getElementById("hcols"); hc.innerHTML = "<th>Station</th>";
    for (const h of a.hours) { const th = document.createElement("th"); th.textContent = h; hc.appendChild(th); }
    const hb = document.getElementById("hrows"); hb.innerHTML = "";
    for (const [name, mins] of a.heat) {
      const tr = document.createElement("tr");
      const n = document.createElement("td"); n.textContent = name; tr.appendChild(n);
      for (const v of mins) {
        const td = document.createElement("td");
        const share = Math.min(v / 30, 1);
        if (v) { td.textContent = v + "'"; td.style.background = "rgba(31,111,235," + (0.15 + 0.75 * share) + ")";
                 if (share > 0.5) td.style.color = "#fff"; }
        tr.appendChild(td);
      }
      hb.appendChild(tr);
    }
  }
  const st = document.getElementById("status");
  st.textContent = "updated " + d.time; st.className = "";
}
async function tick() {
  try {
    const res = await fetch("data.json", {cache: "no-store"});
    render(await res.json());
  } catch (e) {
    const st = document.getElementById("status");
    st.textContent = "connection to the overview program lost - retrying…"; st.className = "lost";
  }
  setTimeout(tick, 1000);
}
tick();
</script>
</body>
</html>
"""
