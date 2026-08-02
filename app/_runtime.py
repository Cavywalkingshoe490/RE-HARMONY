#!/usr/bin/env python3
"""The two things every screen of this app needs from the runtime: how to
spawn a `config_work/*.py` script as a subprocess, and how to word a failure
for the person who pressed the button (`reason()`, at the bottom).

SPAWNING A SCRIPT, whether running from the dev venv or from inside a
PyInstaller `.app` bundle.

In a normal (non-frozen) run, `sys.executable` is a real Python interpreter,
so `[sys.executable, str(script), ...]` works as-is -- this is what every
call site in this app already did before packaging existed.

Inside a PyInstaller bundle, `sys.executable` is the frozen app binary
itself, *not* a Python interpreter: passing it a `.py` path as an argument
does not run that script. `main.py` handles this by re-execing itself in
"script mode" whenever its first argument is `--frozen-run-script` (see
`_run_frozen_script()` there), so `interprete()` below just adds that flag
when frozen. Every existing call site only has to change
`[sys.executable, str(script), ...]` into `[*interprete(), str(script), ...]`
-- nothing else about how these subprocesses are built, run, or checked
changes, in either mode.
"""

from __future__ import annotations

import sys


def interprete() -> list[str]:
    """Argv prefix that reaches a real Python runtime for a subprocess.

    Non-frozen: `[sys.executable]`, identical to the pre-packaging behavior.
    Frozen: `[sys.executable, "--frozen-run-script"]`, which `main.py`
    intercepts before it ever imports `webview` or opens a window.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--frozen-run-script"]
    return [sys.executable]


#: Exceptions whose `str()` comes back empty: without this table the person
#: would read a line that ends in a colon and nothing. Keyed by class name
#: because `TimeoutError` and `socket.timeout` are the same object in Python
#: 3.10+, and matching by name does not force a `socket` import in here.
_SIN_TEXTO = {
    "TimeoutError": "it took too long and was cut off",
    "ConnectionError": "the connection dropped",
    "ConnectionResetError": "the connection was reset by the other end",
    "ConnectionRefusedError": "the connection was refused",
    "BrokenPipeError": "the other end closed the connection",
    "MemoryError": "this computer ran out of memory",
    "RecursionError": "the operation nested deeper than Python allows",
    "StopIteration": "the data ran out sooner than expected",
    "KeyboardInterrupt": "it was interrupted",
}


#: THE ONES THAT ARE ALWAYS A BUG. Python raises these AT the programmer,
#: never at a situation: their message is written for whoever wrote the
#: line, and nothing the person pressing the button owns can fix them.
#: Nothing in this app raises them on purpose (`app/check_contract.py`
#: raises one synthetic `TypeError` to prove its own catch works, and that
#: is the whole list), so seeing one means the app broke, not that a
#: feature is switched off. `falla_interna()` is what lets a screen -- and
#: any harness measuring the app -- tell those two apart, which prose
#: alone cannot: a polite sentence over a bug still reads like a feature
#: that is politely off.
_ES_UN_BUG = frozenset(
    {
        "AttributeError",
        "IndexError",
        "KeyError",
        "NameError",
        "TypeError",
        "UnboundLocalError",
        "ZeroDivisionError",
    }
)


def falla_interna(exc: BaseException) -> bool:
    """True when `exc` is the app's own fault, not a situation to report."""
    return type(exc).__name__ in _ES_UN_BUG


def reason(exc: BaseException) -> str:
    """The reason, written for whoever pressed the button.

    `type(exc).__name__` is a Python word, not a reason. `SessionError` and
    `ValueError` tell the person nothing about what is missing or what they
    can do, and they were being pasted in front of messages that already
    said both -- "login rejected: SessionError: Logitech's client_id is
    missing, and without it...". The class name only survives when the
    exception carries no sentence of its own, because then it is the only
    thing there is.

    A bug (see `_ES_UN_BUG`) is worded as a bug and says so, because the
    honest answer there is not "this is off, here is what to do" -- there
    is nothing the person can do -- but "the app broke here".

    This is the single place the app turns an exception into user-facing
    prose: `api.py`, `changes.py` and `main.py` all come through here, so
    the wording cannot drift between screens.
    """
    text = str(exc).strip()
    if falla_interna(exc):
        return (
            "the app broke here, and this is a bug in the app -- not "
            "something you did and not something you can install (%s: %s)"
            % (type(exc).__name__, text or "no detail")
        )
    if text:
        return text
    return _SIN_TEXTO.get(type(exc).__name__, type(exc).__name__)


if __name__ == "__main__":  # pragma: no cover -- self-test
    assert reason(ValueError("there is no session file")) == (
        "there is no session file"
    ), "the sentence the exception carries has to survive whole"
    assert not falla_interna(ValueError("x")), "a situation is not a bug"
    assert falla_interna(KeyError("keys")), "a KeyError here is always a bug"
    assert "bug in the app" in reason(KeyError("keys")), (
        "a bug has to read as a bug, not as a feature that is politely off"
    )
    assert reason(TimeoutError()) == "it took too long and was cut off"
    assert reason(RuntimeError()) == "RuntimeError", (
        "with no sentence and no entry in the table, the class name is all "
        "there is -- saying nothing would be worse"
    )
    assert "Error" not in reason(ValueError("the file is not there")), (
        "no Python class name may reach the screen when there is a sentence"
    )
    print("RUNTIME: PASSED")
