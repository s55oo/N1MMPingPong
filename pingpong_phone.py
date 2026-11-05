"""Phone lamp: this PC's lamp as a web page, for a phone or tablet.

Every lamp serves it on TCP 8047 (--phone-port, 0 = off):

- GET /       the page (one file, no internet needed);
- GET /state  JSON: the big lamp (same color and text as on the PC) and
              one tile per band showing who transmits there.

Read only: the page shows, it cannot change anything.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PHONE_PORT = 8047


def start_server(port, get_state, seen):
    """Start the web server in a thread. Returns (server, "") or
    (None, error text). get_state() -> dict; seen() is called on every
    request (the lamp only builds the state while someone watches)."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen()
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                body, ctype = PAGE.encode("utf-8"), "text/html; charset=utf-8"
            elif path == "/state":
                body = json.dumps(get_state() or {}).encode("utf-8")
                ctype = "application/json"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # no console output
            pass

    try:
        server = ThreadingHTTPServer(("", port), Handler)
    except OSError as exc:
        return None, "TCP {} busy ({})".format(port, exc.strerror or exc)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, ""


def pick_addresses(adapters, route_ip=""):
    """From [(ip, adapter description)] pick the LAN address (the one with
    the default route, else the first non-ZeroTier one) and the ZeroTier
    address. Returns {"lan": ip or "", "zt": ip or ""}."""
    zt = [ip for ip, descr in adapters if "zerotier" in (descr or "").lower()]
    lan = [ip for ip, descr in adapters if ip not in zt]
    best = route_ip if route_ip in lan else (lan[0] if lan else "")
    return {"lan": best, "zt": zt[0] if zt else ""}


def address_text(addrs, port):
    """'192.168.1.20:8047 · 10.147.17.3:8047' for the lamp header: the LAN
    address, plus the ZeroTier one when this PC is on ZeroTier."""
    parts = ["{}:{}".format(addrs[k], port) for k in ("lan", "zt") if addrs.get(k)]
    return "  ·  ".join(parts)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#111">
<title>PingPong lamp</title>
<style>
  * { box-sizing: border-box; }
  html, body { margin:0; height:100%; background:#111; color:#eee;
    font-family:"Segoe UI", system-ui, -apple-system, Roboto, sans-serif; }
  body { display:flex; flex-direction:column; padding:10px; gap:10px; }
  #top { display:flex; justify-content:space-between; font-size:13px; color:#aaa; gap:8px; }
  #top b { color:#eee; }
  #lamp { flex:1 1 55%; min-height:180px; border-radius:14px; position:relative;
    display:flex; align-items:center; justify-content:center; text-align:center;
    border:14px solid transparent; transition:background .1s; overflow:hidden; }
  #text { font-weight:700; font-size:min(18vw, 14vh); line-height:1.05; padding:0 8px;
    word-break:break-word; }
  #who, #freq { position:absolute; top:8px; font-weight:700; font-size:16px; }
  #who { left:12px; } #freq { right:12px; }
  #info { font-size:13px; color:#bbb; max-height:4.2em; overflow:hidden; }
  #bands { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:8px; }
  .band { border-radius:10px; padding:8px 10px; background:#2a2a2a; min-height:64px; }
  .band.mine { outline:2px solid #fff; }
  .band .k { font-weight:700; font-size:18px; }
  .band .w { font-weight:700; font-size:15px; }
  .band .s { font-size:12px; opacity:.85; }
  #lost { display:none; position:fixed; inset:auto 10px 10px 10px; background:#c00000;
    color:#fff; padding:8px 12px; border-radius:8px; font-weight:600; text-align:center; }
  body.lost #lost { display:block; } body.lost #lamp { filter:grayscale(1) brightness(.5); }
</style>
</head>
<body>
<div id="top"><span><b id="name">PingPong</b> <span id="ver"></span></span><span id="clock"></span></div>
<div id="lamp"><span id="who"></span><span id="freq"></span><span id="text">—</span></div>
<div id="info"></div>
<div id="bands"></div>
<div id="lost">connection to the lamp lost – retrying…</div>
<script>
function textOn(hex) {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex || "");
  if (!m) return "#fff";
  const [r, g, b] = [1, 2, 3].map(i => parseInt(m[i], 16));
  return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? "#000" : "#fff";
}
let failures = 0;
async function tick() {
  try {
    const res = await fetch("state", {cache: "no-store"});
    const d = await res.json();
    failures = 0; document.body.classList.remove("lost");
    document.getElementById("name").textContent = d.name || "PingPong";
    document.getElementById("ver").textContent = d.version ? "v" + d.version : "";
    document.getElementById("clock").textContent = d.time || "";
    document.title = (d.name || "PingPong") + " lamp";
    const L = d.lamp || {};
    const lamp = document.getElementById("lamp");
    lamp.style.background = L.bg || "#3a3a3a";
    lamp.style.color = L.fg || textOn(L.bg);  // same text color as on the PC (GO: black)
    lamp.style.borderColor = L.border || "transparent";
    document.getElementById("text").textContent = L.text || "—";
    document.getElementById("who").textContent = L.who || "";
    document.getElementById("freq").textContent = L.freq || "";
    document.getElementById("info").textContent = L.info || "";
    const box = document.getElementById("bands"); box.innerHTML = "";
    for (const b of (d.bands || [])) {
      const el = document.createElement("div");
      el.className = "band" + (b.mine ? " mine" : "");
      if (b.color) { el.style.background = b.color; el.style.color = textOn(b.color); }
      const k = document.createElement("div"); k.className = "k"; k.textContent = b.band;
      const w = document.createElement("div"); w.className = "w";
      w.textContent = b.who ? (b.who + (b.msg ? " · " + b.msg : "")) : "";
      const s = document.createElement("div"); s.className = "s"; s.textContent = b.stations || "";
      el.append(k, w, s); box.appendChild(el);
    }
  } catch (e) {
    if (++failures >= 3) document.body.classList.add("lost");
  }
  setTimeout(tick, 300);
}
tick();
</script>
</body>
</html>
"""
