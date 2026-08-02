#!/usr/bin/env python3
"""Access to the Harmony One: identify (read, subprocess to read_config.py) and build
the grabado (write) command line.

`write.py` is NEVER executed from this module -- it writes flash.
`identify()` is the ONLY place in this file that does `subprocess.run`,
and it always points to `read_config.py` (read-only, calls no write or erase
primitive). `build_record_line()` never imports `subprocess` for
`write.py`: it returns data (`RecordLine`), it does not run anything. This
makes it impossible, by construction, for a future bug in `api.py` to end up
running `write.py` against the device without whoever does it noticing
(they would have to add their own `subprocess.run` by hand, not reuse
anything from here).

This is rule 2 of the layering written out in `app/__init__.py`: nothing
under `app/` writes to flash. Running the write has to be an explicit act by
whoever holds the terminal (or the red button in the UI, which goes through
`api.py`'s two-key confirmation), never a side effect of calling something
here.

NOTE on dict keys: `_stage()`'s returned keys (`etapa`, `ok`, `datos`,
`detail`) are kept in Spanish ON PURPOSE -- `app/api.py`'s
`_identity_summary()` reads them by these exact names, and some flow
through to the JS UI. Only the Python-side names (functions, classes,
parameters, local variables) are translated.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import _runtime

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_WORK = REPO_ROOT / "config_work"
LEER_PY = CONFIG_WORK / "read_config.py"
GRABAR_PY = CONFIG_WORK / "write.py"

# Regexes built straight from read_config.py's actual `print` statements, not assumed.
RE_IDENTITY = re.compile(
    r"remote: arch (?P<arch>\d+), skin (?P<skin>\d+), "
    r"firmware (?P<fw_mayor>\d+)\.(?P<fw_menor>\d+)"
)
RE_CONFIG_NO_DUMP = re.compile(
    r"config used (?P<usada>\d+) B of (?P<total>\d+)\s+"
    r"dump supported: (?P<soportado>yes|NO)"
)
RE_READ = re.compile(r"read (?P<bytes>\d+) B -> (?P<archivo>.+)")
RE_VERDICT = re.compile(r"VERDICT: (?P<veredicto>.+)")


def _stage(name: str, ok: bool, data: dict | None = None, detail: str = "") -> dict:
    return {"etapa": name, "ok": ok, "datos": data or {}, "detail": detail}


def parse_identification(stdout: str, stderr: str, returncode: int) -> dict:
    """Splits `read_config.py`'s output into named stages.

    Does not assume the device supports dumping: the Harmony One reports
    `is_config_dump_supported() == NO`, so `read_config.py` prints "config used ...
    dump supported: NO" and returns before writing `--salida` to disk.
    This function understands both output shapes (with and without a dump).
    """
    stages: list[dict] = []

    m = RE_IDENTITY.search(stdout)
    if m:
        stages.append(
            _stage(
                "identidad",
                True,
                {
                    "arch": int(m["arch"]),
                    "skin": int(m["skin"]),
                    "fw_mayor": int(m["fw_mayor"]),
                    "fw_menor": int(m["fw_menor"]),
                },
            )
        )
    else:
        stages.append(_stage("identidad", False, detail=stderr.strip() or "no match"))

    m = RE_CONFIG_NO_DUMP.search(stdout)
    if m:
        stages.append(
            _stage(
                "config",
                True,
                {
                    "usada": int(m["usada"]),
                    "total": int(m["total"]),
                    "dump_supported": m["soportado"] == "yes",
                },
            )
        )
    else:
        m2 = RE_READ.search(stdout)
        if m2:
            stages.append(
                _stage(
                    "config",
                    True,
                    {"bytes_leidos": int(m2["bytes"]), "dump_supported": True},
                )
            )

    m = RE_VERDICT.search(stdout)
    if m:
        stages.append(
            _stage(
                "veredicto",
                "DOES NOT MATCH" not in m["veredicto"],
                {"text": m["veredicto"].strip()},
            )
        )

    return {
        "ok": returncode == 0,
        "returncode": returncode,
        "etapas": stages,
        "stdout": stdout,
        "stderr": stderr,
    }


def identify(timeout: float = 30.0) -> dict:
    """Identifies the connected remote by running `read_config.py` in a subprocess.

    Read-only: `read_config.py` calls no write or erase primitive (see its
    docstring). The `--salida` file goes to a temp directory that gets
    removed on its own when the `with` block exits; for the Harmony One it
    never actually gets written (dump not supported), but if it's ever used
    with another model that does dump, it leaves no trash behind.
    """
    with tempfile.TemporaryDirectory(prefix="harmony_leer_") as tmp:
        output = Path(tmp) / "leido.bin"
        try:
            r = subprocess.run(
                [*_runtime.interprete(), str(LEER_PY), "--salida", str(output)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(CONFIG_WORK),
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            return {
                "ok": False,
                "returncode": None,
                "etapas": [],
                "stdout": e.stdout or "",
                "stderr": f"timed out after {timeout}s",
            }
        except FileNotFoundError as e:
            return {
                "ok": False,
                "returncode": None,
                "etapas": [],
                "stdout": "",
                "stderr": str(e),
            }
        return parse_identification(r.stdout, r.stderr, r.returncode)


@dataclass
class RecordLine:
    argv: list[str]
    command: str  # argv joined with shlex.join, for display in the UI
    writes_flash: bool  # False only if --verificar-solo is in argv
    warning: str
    extra_warnings: list[str]  # e.g. files that don't exist, --igual-grabo


def build_record_line(
    ezhex: str,
    reference: str | None = None,
    repoints: list | None = None,
    same_recording: bool = False,
    verify_only: bool = False,
) -> RecordLine:
    """Builds the argv for `write.py`. Does NOT run it -- no `subprocess`
    call for `write.py` anywhere in this function or this module. The user
    decides to run it, by hand, in their own terminal.
    """
    argv = [*_runtime.interprete(), str(GRABAR_PY), str(ezhex)]

    extra_warnings: list[str] = []
    if not ezhex or not Path(ezhex).exists():
        extra_warnings.append(f"the EZHex file does not exist: {ezhex!r}")

    if reference:
        argv += ["--referencia", str(reference)]
        if not Path(reference).exists():
            extra_warnings.append(f"the reference file does not exist: {reference!r}")

    for off in repoints or []:
        value = off if isinstance(off, int) else int(str(off), 0)
        argv += ["--repoint", hex(value)]

    if same_recording:
        argv.append("--igual-grabo")
        extra_warnings.append(
            "--igual-grabo turns off the 'nothing moved' validation: it's "
            "the dangerous option"
        )
    if verify_only:
        argv.append("--verify-only")

    if not verify_only and not reference and not same_recording:
        extra_warnings.append(
            "without --referencia, write.py is going to reject the run by design"
        )

    warning = (
        "This WRITES FLASH on the remote."
        if not verify_only
        else "With --verificar-solo it does not write; it only identifies and "
        "validates the file."
    )

    return RecordLine(
        argv=argv,
        command=shlex.join(argv),
        writes_flash=not verify_only,
        warning=warning,
        extra_warnings=extra_warnings,
    )
