#!/usr/bin/env python3
"""Serves the app's REAL UI (`app/ui/`) over HTTP, with the
`window.pywebview.api` bridge wired to the SAME `Api` class from `app/api.py`.

What it's for: taking screenshots of the Control screen without depending on
the graphical session being unlocked (`screencapture` returns black with the
screen locked). The HTML, CSS and JS are the real files -- there is no
second mockup -- and the responses come out of the real Python, not a mock.
The only thing that changes compared to the pywebview window is the
window's frame.

Does NOT touch the device: it exposes the same `Api` methods, and the ones
that talk over USB are still explicit subprocesses that this tool never calls.

Usage:
    app/.venv/bin/python app/tests/serve_ui.py [port]
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import _runtime  # noqa: E402
from api import Api  # noqa: E402

UI = ROOT / "ui"
API = Api()

#: the shim that replaces the pywebview bridge. Injected BEFORE app.js.
SHIM = """
<script>
window.pywebview = { api: new Proxy({}, { get: (_t, method) => (...args) =>
  fetch("/api/" + String(method), {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(args)
  }).then(r => r.json())
})};
window.addEventListener("load", () => {
  setTimeout(() => window.dispatchEvent(new Event("pywebviewready")), 0);
});
/* Optional script for screenshots: ?ir=control&borrar=2 opens the tab and
   clicks "Delete" on the requested k1. It's the SAME click a person would
   make -- it fires on the real DOM button, the function is not called as a
   shortcut. */
(function () {
  const q = new URLSearchParams(location.search);
  const ir = q.get("ir");
  if (!ir) return;
  const waitFor = (sel, ms) => new Promise((res) => {
    const t0 = Date.now();
    (function loop() {
      const e = document.querySelector(sel);
      if (e) return res(e);
      if (Date.now() - t0 > (ms || 15000)) return res(null);
      setTimeout(loop, 120);
    })();
  });
  window.addEventListener("load", async () => {
    const nav = await waitFor('button.nav[data-p="' + ir + '"]');
    if (nav) nav.click();
    const k1 = q.get("borrar");
    if (k1 === null) return;
    const b = await waitFor('[data-borrar="' + k1 + '"]');
    if (b) b.click();
    const yes = q.get("si");
    if (yes === null) return;
    const ok = await waitFor("#btn-borrar-si");
    if (ok) ok.click();
  });
})();
</script>
"""

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep stdout quiet
        pass

    def _reply(self, code, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        rel_path = self.path.split("?")[0].lstrip("/") or "index.html"
        f = (UI / rel_path).resolve()
        if not str(f).startswith(str(UI)) or not f.exists():
            return self._reply(404, b"no", "text/plain")
        data = f.read_bytes()
        if f.suffix == ".html":
            data = data.replace(b"</head>", SHIM.encode() + b"</head>")
        return self._reply(
            200, data, CONTENT_TYPES.get(f.suffix, "application/octet-stream")
        )

    def do_POST(self):
        if not self.path.startswith("/api/"):
            return self._reply(404, b"no", "text/plain")
        method = self.path[len("/api/") :]
        n = int(self.headers.get("Content-Length") or 0)
        args = json.loads(self.rfile.read(n) or b"[]")
        fn = getattr(API, method, None)
        if fn is None or method.startswith("_"):
            return self._reply(
                200,
                json.dumps(
                    {"ok": False, "error": "no such method %s" % method}
                ).encode(),
                "application/json",
            )
        try:
            r = fn(*args)
        except Exception as exc:  # noqa: BLE001
            r = {"ok": False, "error": _runtime.reason(exc)}
        return self._reply(200, json.dumps(r, default=str).encode(), "application/json")


def serve(port: int = 8777):
    srv = HTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
    serve(p)
    print("real UI at http://127.0.0.1:%d/index.html  (Ctrl-C to quit)" % p)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
