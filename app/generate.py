#!/usr/bin/env python3
"""Generates the blob for a new device (`config_work/add_device.py`, by
subprocess) and exposes a PURE preview of the write gate
(`grabar.nothing_moved()`) before touching anything.

Two public functions:

- `generate(...)`: validates the labels (name + manual pages, if any) with
  `fonts.choose_detail()` -- if a glyph is missing or the palette changes,
  it aborts BEFORE running anything and says so with `ok=False` and a clear
  message (see "Before calling, validate the labels with fuentes.choose()"
  in the plan). If they pass, it builds `add_device.py`'s `argv` and runs
  it as a **subprocess**: it's ~5,300 lines verified against the device, its
  `main()` takes no parameters (it parses `sys.argv` internally), and
  reimplementing it would create a second copy that could drift --
  exactly what the project's brief forbids ("do not reimplement the gates:
  add_device.py and read_config.py and write.py are invoked as a subprocess").
  Captures stdout+stderr WHOLE -- they are the checks `add_device.py`
  prints (its own, not a summary of ours) -- so the UI can show them as-is.

- `preview_gate(ref, fresh, repuntes)`: imports `grabar` ONLY for
  `nothing_moved()`. Never calls `grabar.cargar()`, never `grabar.main()`, no
  libconcord primitive at all: `ctypes.CDLL` lives inside `cargar()`, which
  this function does not touch -- that's why the import is safe and does not
  open USB (measured: `import grabar` does not touch the device). It's the
  SAME function `write.py` uses to decide whether to write for real, so the
  preview can never drift from the real gate -- it just runs earlier, so the
  UI can show the result right after generating, without waiting for a real
  write subprocess.

This module NEVER imports `config_work/add_device.py` as a module (it's one
of the three gates the brief requires to be invoked by subprocess) and NEVER
calls `write.py` to actually write -- that's `app/remote.py`'s job.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import _runtime

ROOT = Path(__file__).resolve().parent.parent
CONFIG_WORK = ROOT / "config_work"
DISPOSITIVO_PY = CONFIG_WORK / "add_device.py"
BORRAR_PY = CONFIG_WORK / "delete_device.py"
LISTAR_PY = CONFIG_WORK / "list_devices.py"
PANTALLA_ACTIVIDADES_PY = CONFIG_WORK / "screen_activities.py"
EDITAR_ACTIVIDAD_PY = CONFIG_WORK / "edit_activity.py"

sys.path.insert(0, str(CONFIG_WORK))
import fonts  # noqa: E402  -- read-only; does not import grabar/dispositivo/libconcord
import write  # noqa: E402  -- imported ONLY for `nothing_moved` (see docstring above)

#: the attribute `add_device.py` uses for the row name (ATTR_FILA) and the
#: 6 labels of the command grid (ATTR_ETIQUETA) -- both equal 0x09 in
#: `add_device.py`. Repeated here (not imported) so this module never
#: imports `add_device.py`.
ATTR_ETIQUETA = 0x09


def _page_labels(pages: list[str]) -> list[str]:
    """The labels declared by hand in `--page CMD=LBL,CMD=LBL,...`.

    If no page is given, `add_device.py` distributes ALL of the device's
    commands on its own (`PAGINAS_POR_DEFECTO`) and validates its own labels
    internally -- that is not duplicated here on purpose (see the module's
    docstring: `add_device.py` is not reimplemented). This function only
    covers the labels the USER types by hand.
    """
    labels = []
    for page in pages or []:
        for pair in page.split(","):
            if "=" in pair:
                labels.append(pair.split("=", 1)[1])
    return labels


def _serializable_detail(r: dict) -> dict:
    """`fonts.choose_detail()`, converted to types `json.dumps` accepts.

    THIS IS THE BOUNDARY. `config_work/fonts.py` is plain Python and uses
    `set` with every right to do so: `choose_detail()` documents
    `'missing': set(str)` and that is how it should be, it is the set of
    characters no font draws. But `generate.py` is the app's layer, and
    everything that leaves here ends up crossing to the UI through
    `json.dumps` -- with no `default=`, under pywebview.

    A `set` in there did not give back an ugly piece of data: it killed the
    WHOLE call with "Object of type set is not JSON serializable", and that
    text is the one the user ended up reading underneath "the check did not
    pass", as if it were the verification protecting him. It is converted
    here, once, at the only place that dict passes through on its way to the
    screen, and not with a loose `sorted()` at each of the five places that
    consume it (`api.py::remote_validate_label` already had its own, and
    that is why THAT path did not break while this one did).
    """
    d = dict(r)
    missing = d.get("missing")
    if isinstance(missing, (set, frozenset)):
        d["missing"] = sorted(missing)
    validos = d.get("valid_attributes")
    if isinstance(validos, (set, frozenset)):
        d["valid_attributes"] = sorted(validos)
    paleta = d.get("paleta")
    if isinstance(paleta, (set, frozenset)):
        d["paleta"] = sorted(paleta)
    elif isinstance(paleta, tuple):
        d["paleta"] = list(paleta)
    return d


def validate_labels(labels: list[str], blob, contexto: int = ATTR_ETIQUETA) -> dict:
    """Runs `fonts.choose_detail()` on each label, against the INPUT blob
    (before `add_device.py` adds anything). Never raises: always returns a
    dict with `ok`.

        {'ok': bool,
         'detalle': [(label, elegir_detalle()-dict), ...],
         'faltantes': [(label, warning), ...]}

    `faltantes` gathers both "no font draws it whole" and "PALETTE CHANGES"
    (`elegir()` in strict mode rejects those too) -- both are forms of "this
    does not look like what was asked for".

    Everything in the returned dict is JSON-serializable: this is the border
    between `config_work/` (plain Python, sets allowed) and the app (which
    only ever emits what `json.dumps` accepts). See `_serializable_detail`.
    """
    b = blob if isinstance(blob, (bytes, bytearray)) else Path(blob).read_bytes()
    detail, faltantes = [], []
    for etq in labels:
        r = fonts.choose_detail(etq, b, contexto=contexto)
        detail.append((etq, _serializable_detail(r)))
        no_font = r["atributo"] is None
        palette_changed = bool(r["warning"]) and r["warning"].startswith("PALETTE CHANGES")
        if no_font or palette_changed:
            faltantes.append((etq, r["warning"]))
    return {"ok": not faltantes, "detail": detail, "faltantes": faltantes}


def generate(
    blob,
    config_json,
    *,
    index: int,
    name: str,
    salida,
    device: str | None = None,
    pages: list[str] | None = None,
    sin_indicador: bool = False,
    ezhex: str | None = None,
    plantilla: str | None = None,
    timeout: float = 180.0,
) -> dict:
    """Validates labels and, if they pass, runs `add_device.py` as a
    subprocess.

    Builds exactly:

        python3 add_device.py <blob> <config_json>
            [--dispositivo <dispositivo>] --indice <indice> --nombre <nombre>
            --salida <salida> [--pagina ... ]* [--sin-indicador]
            [--ezhex <ezhex> --plantilla <plantilla>]

    Never raises on bad user input (label without a font, missing
    blob/config, missing `add_device.py`, timeout) -- always returns a dict
    with `ok`, so the UI can show it without its own try/except.

        {'ok': bool, 'etapa': 'etiquetas'|'subproceso',
         'argv': [...], 'comando': "...", 'returncode': int|None,
         'stdout': str, 'stderr': str, 'output': str|None,
         'etiquetas': validate_labels()-dict}

    `stdout` is `add_device.py`'s ENTIRE output, unabridged: it carries all
    the checks (a, b, c... the section [5] gate, the page distribution,
    etc.) that the tool already runs internally.
    """
    empty = {
        "ok": False,
        "etapa": "labels",
        "argv": [],
        "command": "",
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "output": None,
        "labels": {"ok": False, "detail": [], "faltantes": []},
    }

    blob_path = Path(blob)
    if not blob_path.exists():
        out = dict(empty)
        out["stderr"] = f"the input blob does not exist: {blob_path}"
        out["labels"] = {
            "ok": False,
            "detail": [],
            "faltantes": [("(blob)", out["stderr"])],
        }
        return out

    config_path = Path(config_json)
    if not config_path.exists():
        out = dict(empty)
        out["stderr"] = f"the configuration file does not exist: {config_path}"
        out["labels"] = {
            "ok": False,
            "detail": [],
            "faltantes": [("(config)", out["stderr"])],
        }
        return out

    labels = [name, *_page_labels(pages or [])]
    validation = validate_labels(labels, blob_path)
    if not validation["ok"]:
        summary = "; ".join(f"{e!r}: {a}" for e, a in validation["faltantes"])
        out = dict(empty)
        out["stderr"] = (
            "label(s) that no font draws whole (or that change palette) -- "
            f"aborting BEFORE running add_device.py: {summary}"
        )
        out["labels"] = validation
        return out

    if not DISPOSITIVO_PY.exists():
        out = dict(empty)
        out["etapa"] = "subproceso"
        out["stderr"] = f"{DISPOSITIVO_PY} does not exist"
        out["labels"] = validation
        return out

    argv = [
        *_runtime.interprete(),
        str(DISPOSITIVO_PY),
        str(blob_path),
        str(config_path),
    ]
    if device:
        argv += ["--device", device]
    argv += ["--index", str(index), "--name", name, "--salida", str(salida)]
    for pag in pages or []:
        argv += ["--page", pag]
    if sin_indicador:
        argv.append("--sin-indicador")
    if ezhex:
        argv += ["--ezhex", str(ezhex)]
        if plantilla:
            argv += ["--plantilla", str(plantilla)]

    command = shlex.join(argv)
    try:
        proc = subprocess.run(
            argv,
            cwd=CONFIG_WORK,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        return {
            "ok": False,
            "etapa": "subproceso",
            "argv": argv,
            "command": command,
            "returncode": None,
            "stdout": stdout
            if isinstance(stdout, str)
            else stdout.decode(errors="replace"),
            "stderr": (
                stderr if isinstance(stderr, str) else stderr.decode(errors="replace")
            )
            + f"\n\ntimed out after {timeout}s",
            "output": None,
            "labels": validation,
        }
    except OSError as exc:
        return {
            "ok": False,
            "etapa": "subproceso",
            "argv": argv,
            "command": command,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "output": None,
            "labels": validation,
        }

    ok = proc.returncode == 0
    output_path = str(salida) if ok and Path(salida).exists() else None
    return {
        "ok": ok,
        "etapa": "subproceso",
        "argv": argv,
        "command": command,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "output": output_path,
        "labels": validation,
    }


def _run(argv: list[str], timeout: float) -> dict:
    """Runs a `config_work/` script as a SUBPROCESS and returns everything.

    Same contract as `generate()`: never raises, always returns a dict with
    `ok`, and `stdout` comes back WHOLE (it's the checks the tool prints, not
    a summary of ours).
    """
    command = shlex.join(argv)
    try:
        proc = subprocess.run(
            argv,
            cwd=CONFIG_WORK,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:

        def _txt(x):
            return x if isinstance(x, str) else (x or b"").decode(errors="replace")

        return {
            "ok": False,
            "argv": argv,
            "command": command,
            "returncode": None,
            "stdout": _txt(exc.stdout),
            "stderr": _txt(exc.stderr) + f"\n\ntimed out after {timeout}s",
        }
    except OSError as exc:
        return {
            "ok": False,
            "argv": argv,
            "command": command,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
        }
    return {
        "ok": proc.returncode == 0,
        "argv": argv,
        "command": command,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def list_devices(blob, salida_json, timeout: float = 120.0) -> dict:
    """Runs `config_work/list_devices.py` (subprocess) on `blob`.

    READ ONLY. Writes nothing except the requested output JSON. By
    subprocess and not by import, for the same reason as `add_device.py`:
    not having a second copy of the model that could drift.
    """
    if not BORRAR_PY.exists() or not LISTAR_PY.exists():
        return {
            "ok": False,
            "argv": [],
            "command": "",
            "returncode": None,
            "stdout": "",
            "stderr": f"missing {LISTAR_PY}",
        }
    argv = [
        *_runtime.interprete(),
        str(LISTAR_PY),
        str(Path(blob)),
        "--json",
        str(Path(salida_json)),
    ]
    return _run(argv, timeout)


def activities(blob, salida_json, timeout: float = 180.0) -> dict:
    """Runs `config_work/screen_activities.py` (subprocess) on `blob`.

    READ ONLY. Same pattern as `list_devices()` for the device screen: the app
    does not reimplement the attribution, it asks for it.
    """
    if not PANTALLA_ACTIVIDADES_PY.exists():
        return {
            "ok": False,
            "argv": [],
            "command": "",
            "returncode": None,
            "stdout": "",
            "stderr": f"missing {PANTALLA_ACTIVIDADES_PY}",
        }
    argv = [
        *_runtime.interprete(),
        str(PANTALLA_ACTIVIDADES_PY),
        str(Path(blob)),
        "--json",
        str(Path(salida_json)),
    ]
    return _run(argv, timeout)


#: The activity-editing actions the app can request, mapped to
#: `edit_activity.py`'s flag. This is a WHITE LIST on purpose: the UI
#: cannot invent a flag and the API does not build the command line with
#: whatever comes from the JS. `--add-set` is not here: adding a new
#: toggle to an activity requires the property to already have transitions
#: in `[14]`, and today that's only true for the three factory ones, so the
#: UI offers change/remove and not add.
ACTIVITY_ACTIONS = {
    "remove_set": "--remove-set",
    "add_set": "--add-set",
    "change_value": "--change-value",
    "renombrar": "--renombrar",
    "erase": "--erase",
}


def edit_activity(
    blob,
    *,
    ordinal: int,
    accion: str,
    argumento: str | None,
    salida,
    ezhex: str | None = None,
    plantilla: str | None = None,
    timeout: float = 600.0,
) -> dict:
    """Runs `config_work/edit_activity.py` (subprocess): edits ONE
    activity.

    Does NOT write the device -- leaves a `.bin` (and optionally an
    `.EZHex`) on disk. Writing stays a separate, explicit step, after the
    gate says yes, same as `generate()` and `delete_device()`.

    `edit_activity.py` runs its own checks (a)..(g) internally, including
    the NEGATIVE of (b)/(e) -- it injects an invalid `k1` into an object that
    is only reachable through section `[14]` and requires the checks to
    catch it. If any of them fails, it exits with a nonzero code and that
    arrives here as `ok=False` with the whole stdout.
    """
    if not EDITAR_ACTIVIDAD_PY.exists():
        return {
            "ok": False,
            "argv": [],
            "command": "",
            "returncode": None,
            "stdout": "",
            "stderr": f"missing {EDITAR_ACTIVIDAD_PY}",
        }
    flag = ACTIVITY_ACTIONS.get(accion)
    if flag is None:
        return {
            "ok": False,
            "argv": [],
            "command": "",
            "returncode": None,
            "stdout": "",
            "stderr": f"activity action not allowed: {accion!r}",
        }
    blob_path = Path(blob)
    if not blob_path.exists():
        return {
            "ok": False,
            "argv": [],
            "command": "",
            "returncode": None,
            "stdout": "",
            "stderr": f"the input blob does not exist: {blob_path}",
        }
    argv = [
        *_runtime.interprete(),
        str(EDITAR_ACTIVIDAD_PY),
        str(blob_path),
        "--index",
        str(int(ordinal)),
        flag,
    ]
    if flag != "--erase":
        if argumento is None:
            return {
                "ok": False,
                "argv": [],
                "command": "",
                "returncode": None,
                "stdout": "",
                "stderr": f"action {accion} needs an argument",
            }
        argv.append(str(argumento))
    argv += ["--salida", str(Path(salida))]
    if ezhex:
        argv += ["--ezhex", str(Path(ezhex))]
        if plantilla:
            argv += ["--plantilla", str(Path(plantilla))]
    result = _run(argv, timeout)
    if result["ok"] and not Path(salida).exists():
        result["ok"] = False
        result["stderr"] = (
            result.get("stderr") or ""
        ) + "\n\n%s was not generated" % salida
    return result


def delete_device(
    blob,
    *,
    index: int,
    salida,
    ezhex: str | None = None,
    plantilla: str | None = None,
    timeout: float = 600.0,
) -> dict:
    """Runs `config_work/delete_device.py` (subprocess): removes ONE device.

    Does NOT write the device -- leaves a `.bin`/`.EZHex` on disk, same as
    `generate()`. Writing stays a separate, explicit step
    (`api.remote_record` / `remote_register_manual_recording`), after the
    gate says yes.

    `delete_device.py` runs its own checks (a, a2, b, c, d) internally AND ALSO the
    real gate (`grabar.nothing_moved`, check (e)) with its negative; if any
    of them fails, it exits with a nonzero code and that arrives here as
    `ok=False` with the whole stdout.
    """
    if not BORRAR_PY.exists():
        return {
            "ok": False,
            "argv": [],
            "command": "",
            "returncode": None,
            "stdout": "",
            "stderr": f"missing {BORRAR_PY}",
        }
    blob_path = Path(blob)
    if not blob_path.exists():
        return {
            "ok": False,
            "argv": [],
            "command": "",
            "returncode": None,
            "stdout": "",
            "stderr": f"the input blob does not exist: {blob_path}",
        }
    argv = [
        *_runtime.interprete(),
        str(BORRAR_PY),
        str(blob_path),
        "--index",
        str(int(index)),
        "--salida",
        str(Path(salida)),
    ]
    if ezhex:
        argv += ["--ezhex", str(Path(ezhex))]
        if plantilla:
            argv += ["--plantilla", str(Path(plantilla))]
    result = _run(argv, timeout)
    if result["ok"] and not Path(salida).exists():
        result["ok"] = False
        result["stderr"] = (
            result["stderr"] or ""
        ) + "\nborrar.py did not leave the output file"
    return result


def preview_gate(ref, fresh, repuntes=()) -> dict:
    """PURE preview of `grabar.nothing_moved()` -- NEVER touches USB.

    Imports `grabar` only for this function: never calls `grabar.cargar()`
    (which is the one that does `ctypes.CDLL(LIB)`), never `grabar.main()`,
    no libconcord primitive. `ref`/`fresh` are paths or `bytes`; `repuntes`
    is an iterable of integer offsets -- one for each `--repoint` that would
    be declared on a real write (each one covers 3 bytes, same as in
    `write.py:main`).

        {'ok': bool, 'diferencias': int, 'sin_declarar': [offsets...],
         'repuntes': [sorted offsets]}

    `ok=False` with a non-empty `sin_declarar` is EXACTLY what makes
    `write.py` refuse to write without `--igual-grabo`. Removing a repoint
    from the list has to give `ok=False` -- that's this function's negative
    check (see `check_load_bearing.py`).
    """

    def _bytes(x):
        return x if isinstance(x, (bytes, bytearray)) else Path(x).read_bytes()

    reference_bytes = _bytes(ref)
    new_bytes = _bytes(fresh)
    repuntes = list(repuntes)
    extra = {p + k for p in repuntes for k in range(3)}
    ok, diff = write.nothing_moved(reference_bytes, new_bytes, extra)
    undeclared = sorted(set(diff) - write.ALLOWED - extra)
    return {
        "ok": ok,
        "diferencias": len(diff),
        "sin_declarar": undeclared,
        "repuntes": sorted(repuntes),
    }
