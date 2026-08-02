#!/usr/bin/env python3
"""Entry point of the RE-HARMONY desktop app.

Opens a pywebview window that loads `app/ui/index.html` and exposes `Api`
(`app/api.py`) as the JS<->Python bridge via `js_api`. Does not touch the
device at startup: every operation against the remote goes through the
explicit subprocesses of `app/remote.py` (identify via `read_config.py`; build,
without running, the `write.py` command line).

Usage:
    app/.venv/bin/python app/main.py

Or, once installed (see `pyproject.toml`), via the console script:
    RE-HARMONY
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _run_frozen_script(argv: list[str]) -> int:
    """Re-exec this frozen binary as a plain Python interpreter for one
    script, instead of opening the window.

    Only relevant inside a PyInstaller bundle. There, `sys.executable` is
    this same compiled app, not a real `python3` -- so every subprocess call
    in `app/remote.py` / `app/generate.py` / `app/learn_ir.py` that used
    to run `[sys.executable, "config_work/whatever.py", ...]` now runs
    `[sys.executable, "--frozen-run-script", "config_work/whatever.py", ...]`
    (see `app/_runtime.interprete()`). This function is what makes that
    second form actually run the script: it mimics `python script.py args...`
    via `runpy`, including `sys.argv` and the script's own directory on
    `sys.path`, and turns the script's `SystemExit` into this process's exit
    code -- so callers that check `returncode` see the same thing they would
    from a real interpreter.
    """
    import runpy

    if not argv:
        print("--frozen-run-script needs a script path", file=sys.stderr)
        return 2

    script = Path(argv[0]).resolve()
    sys.argv = [str(script), *argv[1:]]
    script_dir = str(script.parent)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    return 0


if len(sys.argv) > 1 and sys.argv[1] == "--frozen-run-script":
    # Handled before importing webview/api: none of this needs a window,
    # and staying out of pywebview's way keeps this path fast and free of
    # any GUI side effects.
    raise SystemExit(_run_frozen_script(sys.argv[2:]))

UI_DIR = Path(__file__).resolve().parent / "ui"
INDEX_HTML = UI_DIR / "index.html"

#: RE_HARMONY_HEADLESS=1 -- run only the HTTP bridge to `Api`, no window,
#: ever. Checked before importing `webview` (see below) so this path has
#: zero pywebview/AppKit/pyobjc footprint: nothing here CAN pop up a window,
#: there's no code path left that creates one. This is the mode every
#: offline check should run the app in -- the `check_*.py` scripts in `app/`,
#: or anything else that has to exercise the API without a display.
HEADLESS = os.environ.get("RE_HARMONY_HEADLESS") == "1"

#: RE_HARMONY_NO_FOCUS=1 -- open the window but make it strictly view-only:
#: no Dock icon, no Cmd-Tab entry, and it can never become the key window,
#: so it never receives a keystroke. This is NOT the default, on purpose:
#: `focus=False` is what pywebview's `WindowHost.canBecomeKeyWindow` returns
#: (`webview/platforms/cocoa.py:76-77`), so with it the 11 text inputs of
#: `app/ui/index.html` -- starting with the login e-mail and password --
#: cannot be typed into at all. See `_quiet_launch()`.
NO_FOCUS = os.environ.get("RE_HARMONY_NO_FOCUS") == "1"

if HEADLESS:
    from api import Api  # noqa: E402
else:
    import webview  # noqa: E402

    from api import Api  # noqa: E402


def _run_headless(port: int) -> int:
    """Serve the real UI (`app/ui/`) and the real `Api` (`app/api.py`) over
    plain HTTP, with no `pywebview` import anywhere on this path.

    This is what `RE_HARMONY_HEADLESS=1` runs. The bridge the page expects
    at `window.pywebview.api.<method>(...)` becomes `POST /api/<method>`
    with a JSON array of arguments as the body, dispatched to one live
    `Api()` instance; static files (`index.html`, `app.css`, `app.js`, ...)
    are served as-is from `UI_DIR`. Same approach as
    `app/tests/serve_ui.py` (built to screenshot the app without an
    unlocked session), minus its screenshot-only click-simulation shim --
    this is the production "run and verify the app without a window" path,
    meant to be left running (e.g. `curl` against it, or a smoke check in a
    script) rather than started once and torn down.

    Does not touch the device: `Api()` construction is exactly what
    `app/tests/serve_ui.py` already does at import time with no window,
    and every method that talks over USB still shells out to
    `config_work/*.py` as an explicit subprocess -- this handler doesn't
    change that, it only forwards the call.
    """
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import _runtime

    api = Api()

    content_types = {
        ".html": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:  # keep stdout quiet
            pass

        def _reply(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            rel_path = self.path.split("?")[0].lstrip("/") or "index.html"
            target = (UI_DIR / rel_path).resolve()
            if not str(target).startswith(str(UI_DIR)) or not target.exists():
                return self._reply(404, b"not found", "text/plain")
            data = target.read_bytes()
            return self._reply(
                200, data, content_types.get(target.suffix, "application/octet-stream")
            )

        def do_POST(self) -> None:
            if not self.path.startswith("/api/"):
                return self._reply(404, b"not found", "text/plain")
            method_name = self.path[len("/api/") :]
            length = int(self.headers.get("Content-Length") or 0)
            args = json.loads(self.rfile.read(length) or b"[]")
            fn = getattr(api, method_name, None)
            if fn is None or method_name.startswith("_"):
                body = json.dumps(
                    {"ok": False, "error": "no such method %s" % method_name}
                ).encode()
                return self._reply(200, body, "application/json")
            try:
                result = fn(*args)
            except Exception as exc:  # noqa: BLE001 -- report to the caller, never crash the server
                result = {"ok": False, "error": _runtime.reason(exc)}
            return self._reply(
                200, json.dumps(result, default=str).encode(), "application/json"
            )

    # THREAD PER CALL, like the real window. pywebview dispatches every
    # `window.pywebview.api.<m>()` on its own thread (`webview/util.py`
    # `js_bridge_call` -> `Thread(target=_call)`), so a slow call there never
    # blocks the next one. A plain `HTTPServer` is single-threaded and does
    # the opposite: it queues, and a device poll that takes a minute makes
    # every later call look hung. That is a lie about the app -- it made
    # `catalog_delete` take 81s here and 0s in the window. `Api` is already
    # built for concurrent callers (`self._lock_aparato` serializes the one
    # thing that must be serialized, USB access), so this only removes the
    # harness's own bottleneck.
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(
        f"RE-HARMONY headless: http://127.0.0.1:{port}/index.html  "
        "(no window; Ctrl-C to stop)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


class _NoAutoActivate:
    """Proxy over pywebview's shared `NSApplication` that swallows exactly
    one call: `activateIgnoringOtherApps_`.

    That is the call that steals focus. `webview/platforms/cocoa.py` fires it
    unconditionally at line 744 (`first_show()`, when the window opens) and
    at line 751 (`show()`), and it is what yanks the front-most app away from
    whatever the user was typing in. Everything else `BrowserView.app.<...>`
    does -- `run()`, `isRunning()`, `setMainMenu_()`, `setDelegate_()`,
    `stop_()`, `abortModal()`, `mainMenu()`, `setServicesMenu_()`,
    `keyWindow()`, `setApplicationIconImage_()`, the 15 uses in that file --
    is forwarded untouched to the real object by `__getattr__`.

    Why a Python proxy and not a patch to the flag: `BrowserView.app` is a
    plain *Python* class attribute, so it can be swapped without touching
    pywebview on disk and without pyobjc subclassing. And, unlike
    `focus=False`, this keeps `canBecomeKeyWindow` returning True, so the
    window still accepts the keyboard the moment the user clicks it.
    """

    def __init__(self, app: object) -> None:
        object.__setattr__(self, "_app", app)

    def activateIgnoringOtherApps_(self, _flag: object) -> None:  # noqa: N802 -- ObjC selector name
        """Swallowed on purpose: this is the focus theft."""
        return None

    def __getattr__(self, name: str) -> object:
        return getattr(object.__getattribute__(self, "_app"), name)


def _install_no_auto_activate(cocoa: object) -> bool:
    """Wrap `cocoa.BrowserView.app` in `_NoAutoActivate`. Returns True if the
    swap happened.

    Takes the module as an argument (instead of importing it) so it can be
    exercised offline against a stub -- see `_selftest()` -- with no AppKit,
    no window and no Dock icon involved.
    """
    browser_view = getattr(cocoa, "BrowserView", None)
    app = getattr(browser_view, "app", None)
    if app is None or isinstance(app, _NoAutoActivate):
        return False
    browser_view.app = _NoAutoActivate(app)
    return True


def _quiet_launch() -> None:
    """Keep the window from stealing focus when it opens on macOS, WITHOUT
    making it unusable.

    Default (this is what runs on every normal launch): install
    `_NoAutoActivate`. pywebview's Cocoa backend sets up `NSApplication` in
    the class body of `BrowserView`, which runs the first time
    `webview.platforms.cocoa` is imported -- normally deep inside
    `webview.start()`, out of our control. Importing that same module here
    forces the one-time class body to run under us; Python caches the import
    in `sys.modules`, so pywebview's later `import webview.platforms.cocoa`
    is a no-op re-import and the object we swapped in is the one it uses.
    The window still opens and `makeKeyAndOrderFront_` still runs, but the
    process no longer pulls itself in front of the app the user is working
    in, and -- because `focus` stays True -- typing works normally once the
    user does click the window.

    `RE_HARMONY_NO_FOCUS=1` on top of that: activation policy -> Accessory
    (no Dock icon, no Cmd-Tab entry) and `focus=False` in `create_window`
    (see `main()`), which makes `WindowHost.canBecomeKeyWindow` return False
    forever -- a strictly view-only window that will not accept a single
    keystroke. Useful to look at the UI while working on something else;
    useless for logging in.

    NOT verified by opening a window: this project's rule is that no window
    is ever opened to check anything by eye. What IS verified, offline, is
    the proxy's own behaviour (`_selftest()`) and the pywebview lines it
    relies on (`cocoa.py:76-77`, `:744`, `:751`, `:597`), read from the
    installed copy. If a future pywebview changes those internals this
    degrades silently (the `except` below) instead of blocking startup;
    `RE_HARMONY_HEADLESS=1` is the fallback guaranteed by construction never
    to open anything.
    """
    if sys.platform != "darwin":
        return
    try:
        import webview.platforms.cocoa as cocoa  # (see docstring: runs the class body under us)

        _install_no_auto_activate(cocoa)

        if NO_FOCUS:
            import AppKit

            AppKit.NSApplication.sharedApplication().setActivationPolicy_(
                AppKit.NSApplicationActivationPolicyAccessory
            )
    except Exception:  # noqa: BLE001 -- best effort only, must never block startup
        pass


def _selftest() -> int:
    """`python app/main.py --selftest`: check the no-focus plumbing without
    AppKit, without pywebview and without opening anything."""

    class _FakeApp:
        def __init__(self) -> None:
            self.activated = 0
            self.ran = 0

        def activateIgnoringOtherApps_(self, _flag):  # noqa: N802
            self.activated += 1

        def run(self):
            self.ran += 1

        def isRunning(self):  # noqa: N802
            return False

        def setMainMenu_(self, _menu):  # noqa: N802
            return "menu-set"

    class _FakeBrowserView:
        app = _FakeApp()

    class _FakeCocoa:
        BrowserView = _FakeBrowserView

    real = _FakeBrowserView.app
    fails = 0

    def check(label: str, cond: bool) -> None:
        nonlocal fails
        print(f"  {'OK  ' if cond else 'FAIL'}  {label}")
        if not cond:
            fails += 1

    check("swap reported as done", _install_no_auto_activate(_FakeCocoa) is True)
    check(
        "BrowserView.app is now the proxy",
        isinstance(_FakeBrowserView.app, _NoAutoActivate),
    )
    _FakeBrowserView.app.activateIgnoringOtherApps_(True)
    _FakeBrowserView.app.activateIgnoringOtherApps_(True)
    check("activateIgnoringOtherApps_ swallowed (0 calls through)", real.activated == 0)
    _FakeBrowserView.app.run()
    check("run() forwarded", real.ran == 1)
    check("isRunning() forwarded", _FakeBrowserView.app.isRunning() is False)
    check(
        "setMainMenu_() forwarded",
        _FakeBrowserView.app.setMainMenu_(None) == "menu-set",
    )
    check("idempotent (no double wrap)", _install_no_auto_activate(_FakeCocoa) is False)
    check(
        "HEADLESS never imports webview",
        "webview" not in sys.modules if HEADLESS else True,
    )
    print("SELFTEST: %s" % ("PASSED" if fails == 0 else f"{fails} FAILED"))
    return 0 if fails == 0 else 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        return _selftest()

    if not INDEX_HTML.exists():
        print(f"missing {INDEX_HTML}", file=sys.stderr)
        return 1

    if HEADLESS:
        port = int(os.environ.get("RE_HARMONY_HEADLESS_PORT", "8777"))
        return _run_headless(port)

    _quiet_launch()

    api = Api()
    window = webview.create_window(
        "RE-HARMONY",
        str(INDEX_HTML),
        js_api=api,
        width=1080,
        height=780,
        min_size=(880, 620),
        # `focus` is what `WindowHost.canBecomeKeyWindow` returns
        # (`webview/platforms/cocoa.py:76-77`): False means the window can
        # NEVER become the key window, i.e. it never receives a keystroke and
        # the login fields cannot be filled in. So it is only used when the
        # user explicitly asks for a view-only window with
        # RE_HARMONY_NO_FOCUS=1. Not stealing focus at launch is handled by
        # `_quiet_launch()` instead, which does not cost the keyboard.
        focus=not NO_FOCUS,
    )
    # `_set_window` starts with an underscore on purpose: pywebview does not
    # expose to the JS side any method that starts with `_`, so the window
    # itself stays out of the page's reach.
    api._set_window(window)
    webview.start(debug=os.environ.get("RE_HARMONY_DEBUG") == "1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
