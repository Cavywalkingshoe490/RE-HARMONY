#!/usr/bin/env python3
"""The ONLY class exposed to the JS (`window.pywebview.api`).

Everything the interface can do goes through here. This layer **reimplements
nothing**: it delegates to the sibling modules that are already written and
verified (`session.py`, `catalog.py`, `generate.py`, `remote.py`,
`history.py`), which in turn invoke `config_work/add_device.py`,
`config_work/read_config.py`, and `config_work/write.py` **by subprocess**. The
layering rule this file exists to enforce is written out in
`app/__init__.py`: one direction only (`app/` imports `config_work/`, never
the other way round), nothing in `app/` writes to flash, and the gate is
CALLED rather than copied.

HARD RULES, in code and not in a comment:

  * **No write is offered unless the gate passed.** `remote_record()` runs
    the gate again on the Python side before touching anything: it does not
    trust the JS. And the UI simply does not draw the button (it does not
    exist in the DOM) while `remote_gate()` hasn't returned `passed=True`.
  * **Writing needs an EXPLICIT confirmation, in Python.** Two keys, both
    checked here and not in the JS: `ack == "GRABAR"` (the red button the
    user consciously clicks) and the gate in green. The env var that used to
    be a third key (`RE_HARMONY_PERMITIR_GRABADO=1`) is gone as a
    requirement -- it was a belt on top of a belt from when nothing was
    verified yet, and it left the user stuck at "verified and ready, now
    restart the app with an environment variable". What is left is the
    OPPOSITE switch, for whoever wants the app nailed shut:
    `RE_HARMONY_SOLO_LECTURA=1`. Writing by hand in a terminal is still
    offered (the command is always built) and "I already grabbed it by hand"
    still lands in the history.
  * **The gate is not reimplemented**: `grabar.nothing_moved` and
    `grabar.ALLOWED` are called (via `generar.preview_gate`),
    the same objects `write.py` itself uses. `import grabar` does not touch
    USB (`LIB` is a string; `ctypes.CDLL` lives inside `cargar()`), so
    previewing the gate does not need the remote plugged in.
  * **Byte-for-byte verification IS possible now.** `read_config.py` says
    `is_config_dump_supported() = NO` -- that is the *Logitech* dump path --
    but `config_work/read_flash_baseline.py` reads the raw flash out of the
    remote anyway, and that dump gives the SAME sha256 as the `.bin` this
    app generates (measured: `config_empaquetada.bin` ->
    `0ba5745918d58fc08a1ba4bd3ebff6cc3e36d008c102dda0769e704690a8adae`). So
    `sync_verificar_grabado()` closes the loop for real; the old "size is
    the only thing that can be compared" is no longer true and is not said
    anywhere. `TEXTO_CIERRE_DE_LAZO` still lives here, in Python, and the UI
    asks for it through `status()`: the text cannot be changed by touching
    only the HTML.
  * **Nothing this app does creates, deletes, or modifies anything in the
    Logitech account.** `catalog_save()` only does
    `SearchGlobalDevices`/`GetGlobalDevices`/`GetGlobalLanguageCommands`
    against the public catalog (a normal, read-only account login) and
    builds the device with the protocols already on disk (`library.py`);
    `catalog_ir_import()` doesn't even open the network. Nothing in
    `app/` reads `remoteId`, `skinId`, `hubSecret`, or
    `~/.harmony_api_token.json`.

Soft imports: if `keyring` -- the one third-party import the account path
still has -- is missing, the app **still starts**; `status()["absent"]`
reports it and the UI disables Account and Catalog with the reason. The
Control screen -- the critical path -- does not depend on it.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import _runtime

# THE ONLY WAY AN EXCEPTION IS ALLOWED TO REACH A SCREEN. Every `except` in
# this file words its failure through here, so no Python class name is ever
# pasted in front of a message that already explains what is missing (see
# `_runtime.reason`). It is bound this early on purpose: `_soft_import()`
# runs below, before the rest of the helpers exist, and it needs it too.
_motivo = _runtime.reason

APP = Path(__file__).resolve().parent
RAIZ = APP.parent
CONFIG_WORK = RAIZ / "config_work"
SALIDA = RAIZ / "output"
BACKUPS = RAIZ / "backups"
OUTPUT_BRIDGE = RAIZ / "account_export" / "output"
LEER_PY = CONFIG_WORK / "read_config.py"
GRABAR_PY = CONFIG_WORK / "write.py"

for _path in (str(APP), str(CONFIG_WORK)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Regression anchor: the blob that is grabbed and verified on the device TODAY.
ANCLA_BIN = SALIDA / "config_empaquetada.bin"
ANCLA_MD5 = "976bc70edd15b40f56cb49aa5113594f"
ANCLA_PASO1_MD5 = "eb9c39b072f12c53cd906291990edb56"
ANCLA_REPUNTES = [0x20, 0x24]  # MASTER_S5 and MASTER_T6

SERVICIO_LLAVERO = "harmony-app"

# Writing is ON by default and gated by an EXPLICIT confirmation (`ack ==
# "GRABAR"`, checked in Python) plus the gate in green. Requiring an env var
# on top of that was pure friction: the user did everything right, the app
# said "checked and ready", and then asked them to restart with a variable an
# end user has no reason to know. The remaining switch goes the other way,
# for whoever wants the app locked read-only:
#
#     RE_HARMONY_SOLO_LECTURA=1
#
# `RE_HARMONY_PERMITIR_GRABADO=0` is honoured too, so anyone who had it
# pinned to 0 keeps exactly the behaviour they were counting on.
SOLO_LECTURA = (
    os.environ.get("RE_HARMONY_SOLO_LECTURA") == "1"
    or os.environ.get("RE_HARMONY_PERMITIR_GRABADO") == "0"
)
PERMITIR_GRABADO = not SOLO_LECTURA

# The texts the UI has an OBLIGATION to show. They live in Python, not in the
# HTML, so they cannot be softened by touching only the template.
#
# CORRECTED: the raw flash dump (`config_work/read_flash_baseline.py`, what
# `estado_mando.refrescar()` runs) gives the same sha256 as the generated
# `.bin`. Byte-for-byte verification is real; it is a read and it takes about
# 136 s, so it is offered as an optional step, never forced.
TEXTO_CIERRE_DE_LAZO = (
    "Byte-for-byte verification is available: the remote's flash is read "
    "back and its sha256 compared against the file that was written. It is "
    "a read (it changes nothing) and it takes about 136 s."
)
# The quick check that `remote_loop_closure()` does on its own: only SIZE.
# Kept separate on purpose so nobody reads a size match as a full check.
TEXTO_SOLO_TAMANO = (
    "This compared SIZE only -- the bytes the remote says it received "
    "against the size of the file that was sent."
)
# FIRST USE, and the dead end it replaces. A fresh clone has no
# `backups/config_raw.bin`: that file is a raw dump of somebody's own remote
# and it is never published. Until this text existed, the three read-only
# screens answered a clone with the PATH of the file that is missing --
# which asks the user to produce, by hand, the one thing the app exists to
# produce. Reading the remote IS the app (`remote_real_state()` already
# does it, read-only, and keeps what it read), so that is what gets offered.
# It lives in Python, like the other obligations, so it cannot be softened
# by editing only the template.
TEXTO_PRIMER_USO = (
    "Nothing has been read off your Harmony One yet, so there is nothing to "
    "show. Plug it in with the USB cable and tap 'Read my remote': the app "
    "reads what is written in its memory and works from that. It ONLY "
    "READS -- nothing is written to your remote and nothing on it changes."
)
TEXTO_TECLAS_NO_EDITABLES = (
    "Of the 55 buttons the remote declares, 44 can be changed: 36 rubber "
    "keys (numbers, volume, channel, the d-pad, transport) plus the 8 "
    "touchscreen zones. The rubber keys are not tied loose: they are tied "
    "PER ACTIVITY -- the remote decides which command to send by looking at "
    "which activity you're in. "
    "That's why an activity is chosen above. The remaining 11 do not hang "
    "off any command in any context: they are marked not editable with the "
    "measured reason, never hidden and never left clickable with no effect."
)
# WHERE a rubber key gets bound, which is the thing grabada #7 got wrong:
# the change was written, verified and inert, because it was written in the
# one place the remote was not looking. The screen has to say this BEFORE the
# change is queued, not after the sync.
TEXTO_TECLAS_SITIO = (
    "A rubber key doesn't do one single thing: the remote decides what to "
    "send by looking at where you are when you press it. There are two "
    "places to bind it, and they are not interchangeable. "
    "ON THE DEVICE'S OWN PAGE (Devices -> that device): it works whenever "
    "you are on that device's page, with no activity running. That's how "
    "the three devices this remote came with are wired, and it's the one to "
    "pick if you just added a device and want to drive it. "
    "IN AN ACTIVITY: it works only while that activity is running, and only "
    "on screens that don't already claim the key -- a device page claims it "
    "first."
)
# Only ever shown when somebody DELIBERATELY locked the app read-only. In the
# normal case it is never reached, so it must not read like an instruction
# the user is expected to follow.
TEXTO_GRABADO_APAGADO = (
    "Read-only mode is on (RE_HARMONY_SOLO_LECTURA=1): the app will not "
    "write. It builds the command for you to run in a terminal."
)

# --------------------------------------------------------------------------
# The Sync screen: ONE line per state. Not paragraphs, and never the same
# thing said twice. The screen has exactly three moments, and each one shows
# one line and one button:
#
#     preparing    linea_sync_preparando(n)   [Check and prepare]
#     checked      LINEA_SYNC_VERIFICADO      [Write to my remote]  (red)
#     writing      LINEA_SYNC_ESCRIBIENDO     progress bar
#
# The list of pending changes stays (short and useful). Everything else that
# used to be printed around it is gone.
# --------------------------------------------------------------------------
LINEA_SYNC_SIN_CAMBIOS = "There are no changes waiting."
LINEA_SYNC_VERIFICADO = "Checked. Nothing moved that you didn't ask for."
LINEA_SYNC_NO_PASO = "The check did not pass: nothing will be written."
LINEA_SYNC_ESCRIBIENDO = "Writing to your remote. Don't unplug it."

#: La linea del boton **Connect** mientras corre. Dice lo que de verdad
#: pasa -- se busca el mando y se lee su memoria entera -- y avisa que
#: tarda, porque tarda: ~80 transacciones USB de 16 KiB. Vive en Python
#: junto a las otras lineas obligatorias, no en la plantilla.
#:
#: El boton se llama Connect y no "Refresh" por lo mismo: "refresh" no
#: nombra ninguna de las dos cosas que hace.
TEXTO_CONECTANDO = (
    "Connecting to your Harmony One and reading its memory. This takes "
    "a couple of minutes -- don't unplug it."
)

# THE THREE LINES THAT UNTIL TODAY WERE ONE. `LINEA_SYNC_NO_PASO` was shown
# both when the gate rejected (real protection) and when the
# app broke on its own (a KeyError). That teaches you to distrust the app just
# when it is working fine, and to ignore the warning when it really does
# protect. The CLASS is decided by Python (`changes.CLASE_*`), not by the JS.
LINEA_POR_CLASE = {
    # the gate said NO: a byte moved that nobody declared
    "gate": LINEA_SYNC_NO_PASO,
    # a config_work/ tool aborted on a check of ITS OWN
    "herramienta": "This change can't be applied: nothing will be written.",
    # the app broke. It is NOT protection and cannot be called protection.
    "aplicacion": "This is a bug in the app, not a problem with your remote.",
}

#: The parameter names, said the way somebody who does not program would say them.
#: Used when `changes.parametros_faltantes()` rejects something being queued.
FALTA_HUMANA = {
    "config_json": "the device file (pick one in Catalog first)",
    "name": "the name to show on the remote",
    "device": "which device inside that file",
    "k1": "which device to remove",
    "ordinal": "which activity",
    "accion": "what to do to that activity",
    "codigo": "which key",
    "k2": "which command to put on that key",
    "contexto": "which context that key belongs to",
    "screen": "which screen",
    "slot": "which slot on that screen",
}


def sync_preparing_line(n: int) -> str:
    """`n` pending changes -> the single line of the 'preparing' state."""
    if not n:
        return LINEA_SYNC_SIN_CAMBIOS
    return "%d change%s ready to apply." % (n, "" if n == 1 else "s")


# --------------------------------------------------------------------------
# soft imports
# --------------------------------------------------------------------------
FALTA: dict[str, str] = {}


def _soft_import(name: str):
    """Imports `name` or records why it couldn't. Never raises."""
    try:
        return __import__(name)
    except Exception as exc:  # noqa: BLE001
        FALTA[name] = _motivo(exc)
        return None


remote = _soft_import("remote")  # identify (read_config.py) + build the write command line
generate = _soft_import("generate")  # add_device.py by subprocess + pure gate
history = _soft_import("history")  # SQLite history
fonts = _soft_import("fonts")  # glyphs: which labels the hardware draws
command_records = _soft_import(
    "command_records"
)  # read the device list of an already-captured config
dispositivo_mod = _soft_import(
    "add_device"
)  # ONLY read_section5(): how many are there already
session = _soft_import("session")  # login + keychain (needs keyring)
catalog = _soft_import("catalog")  # Logitech's public catalog, read-only
library = _soft_import("library")  # IR protocols already captured, on disk
ir_manual = _soft_import("ir_manual")  # import a .ir (Flipper/IRDB) by hand
api_learn = _soft_import("api_learn")  # Learn screen: mixin of this class
keys_map = _soft_import("keys_map")  # Keys screen: model + reassignment
keys_physical = _soft_import("keys_physical")  # rubber keys: [10]'s contexts
keys_photo = _soft_import("keys_photo")  # remote photo + code<->command join
keys_reach = _soft_import("keys_reach")  # what the firmware REACHES
# THE FACTORY TEMPLATE. The ONE place the app names the planner: it decides
# which command goes on which rubber key of a device's own page. See
# `app/key_template.py` -- nothing else in the app calls into it.
key_template = _soft_import("key_template")
remote_status = _soft_import(
    "remote_status"
)  # the 3 real states -- truth from the remote
changes = _soft_import("changes")  # pending-changes session model + Sync's chain
summary = _soft_import("summary")  # plain-language summary of pending changes
progress = _soft_import(
    "progress"
)  # write.py's stdout, parsed live, for the progress bar

# The Learn screen lives in its own file and hooks in as a base class
# (pywebview exposes the OBJECT's methods, so a mixin stays just as
# available to the JS). If `api_learn.py` doesn't import, the app starts
# anyway without that screen: `status()["absent"]` says so and nothing else
# notices.
_MixinAprender = (
    getattr(api_learn, "ApiAprender", object) if api_learn else object
)

# `read_config.py` prints this line only in the no-dump branch (Harmony One).
RE_DECLARA = re.compile(r"the remote declares:\s*(\d+)\s*B")
RE_VEREDICTO = re.compile(r"VERDICT:\s*(.+)")
RE_REPUNTA = re.compile(r"--repunta\s+(0x[0-9a-fA-F]+)")


def _ok(**kw) -> dict:
    d: dict = {"ok": True}
    d.update(kw)
    return d


def _err(msg, **kw) -> dict:
    d: dict = {"ok": False, "error": str(msg)}
    d.update(kw)
    return d


def _err_de(exc: BaseException, prefijo: str = "", **kw) -> dict:
    """The answer to a button when an `except` caught something.

    Same shape as `_err`, plus ONE extra key when it applies:
    `falla_interna: true`. That key is the difference between "this is off
    and here is why" and "the app broke". Both come back as `ok:false` and
    both used to be indistinguishable to the screen and to anything
    measuring the app -- a `KeyError` worded politely reads exactly like a
    feature that is deliberately switched off, which is the shape of a
    green that means nothing. The flag is a fact, not prose: it survives
    rewording and translation.
    """
    text = _motivo(exc)
    d = _err("%s: %s" % (prefijo, text) if prefijo else text, **kw)
    if _runtime.falla_interna(exc):
        d["falla_interna"] = True
    return d


def _md5(path: Path) -> str:
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _hex(n: int) -> str:
    return "%#08x" % n


def _failed_step_detail(r: dict) -> dict | None:
    """What goes BEHIND "See more" when a Sync step fails: the stderr
    and the traceback of the ONE step that failed, not the whole envelope.

    Making it a function and not an inline `r["steps"][-1]` is deliberate:
    it is the only thing the UI has to paint as technical text, and that
    way there is ONE place where what counts as "technical detail" gets
    decided."""
    for p in reversed(r.get("steps") or []):
        if not p.get("ok"):
            d = p.get("technical_detail") or {}
            return {
                "label": p.get("label"),
                "kind": p.get("kind"),
                "category": p.get("category"),
                "reason": p.get("reason"),
                "stderr": (d.get("stderr") or "")[-4000:],
                "stdout": (d.get("stdout") or "")[-4000:],
                "traza": d.get("traza"),
                "command": d.get("command"),
            }
    if r.get("error"):
        return {"stderr": str(r["error"]), "traza": r.get("traza")}
    return None


def _int(x) -> int:
    """Accepts 32, '32', '0x20', or ' 0x20 ' -- whatever might come from the JS."""
    if isinstance(x, bool):
        raise ValueError("a repoint cannot be a boolean")
    if isinstance(x, int):
        return x
    return int(str(x).strip(), 0)


def _repoints(value) -> list[int]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        partes = [p for p in re.split(r"[,\s]+", value.strip()) if p]
    else:
        partes = list(value)
    return sorted({_int(p) for p in partes})


def _path(x) -> Path:
    """Always absolute. Subprocesses run with cwd=config_work, so a relative
    path from the UI side would mean something else over there."""
    return Path(str(x)).expanduser().resolve()


class Api(_MixinAprender):
    """Public methods = the JS surface. Everything returns a serializable dict.

    The ones starting with `_` are not exposed by pywebview (the bridge
    skips them).

    Inherits the `aprender_*` methods (Learn screen) from
    `api_aprender.ApiAprender`. If that module doesn't import, the base is
    `object` and the app starts without that screen instead of not starting
    at all.
    """

    def __init__(self) -> None:
        self._window = None
        self._lock_aparato = threading.Lock()  # only one USB access at a time
        self._last_generation: dict | None = None
        self._last_gate: dict | None = None
        self._busqueda: object | None = None  # SearchResult from catalogo.search
        self._keys_cache: dict | None = None  # key map (expensive to build)
        self._keys_cache_key: tuple | None = None  # (path, mtime) of the blob
        self._datos = self._data_directory()
        # -- estado real + cambios pendientes + Sync (see remote_status.py /
        #    changes.py / summary.py / progress.py for the design) --------
        self._changes = changes.SesionCambios() if changes else None
        self._verdad_actual: dict | None = (
            None  # last estado_mando.refrescar() w/ estado=CONECTADO_VERDAD
        )
        # `(monotonic, answer)` of the last `remote_real_state()`, WHATEVER
        # it answered -- including "not plugged in". `_verdad_actual` only
        # keeps the good one, and that is why it could not stop the three
        # cards of the Control screen from each firing their own full USB
        # read when there was nothing on disk yet. See
        # `_estado_real_cacheado()`.
        self._ultimo_estado_real: tuple[float, dict] | None = None
        self._trabajos_grabado: dict[
            str, object
        ] = {}  # job_id -> progreso.TrabajoGrabado
        # -- Connect (la lectura con barra) ------------------------------
        # `_trabajo_lectura` es LA lectura en vuelo, si hay una. No es lo
        # mismo que el diccionario: el diccionario guarda todas para que un
        # polling atrasado siga encontrando la suya; este puntero es el que
        # hace que apretar Connect (o volver a la solapa) mientras corre se
        # ENGANCHE a la que ya esta en vez de disparar la segunda. El lock
        # es solo para ese "mirar y decidir", que si no es atomico deja
        # pasar dos lecturas cuando dos clicks caen juntos.
        self._trabajos_lectura: dict[str, object] = {}  # job_id -> TrabajoLectura
        self._trabajo_lectura: object | None = None
        self._lock_lectura = threading.Lock()
        # The `.bin` of the last write, for `sync_verificar_grabado()`:
        # it is against THAT file (not the `.EZHex`) that the raw dump of the
        # flash has to give the same sha256.
        self._last_written_blob: str | None = None

    # ==================================================================
    # infrastructure (not exposed to the JS: starts with _)
    # ==================================================================
    def _set_window(self, window) -> None:
        self._window = window

    @staticmethod
    def _data_directory() -> Path:
        if history is not None:
            try:
                return history.data_directory()
            except Exception:  # noqa: BLE001
                pass
        if platform.system() == "Darwin":
            base = Path.home() / "Library" / "Application Support" / "HarmonyOne"
        elif platform.system() == "Windows":
            base = Path(os.environ.get("APPDATA", str(Path.home()))) / "HarmonyOne"
        else:
            base = (
                Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
                / "HarmonyOne"
            )
        base.mkdir(parents=True, exist_ok=True)
        (base / "ezhex").mkdir(exist_ok=True)
        return base

    def _connect(self):
        """Connection to the history's SQLite, with the two columns the app
        adds on top of `history.py`'s schema.

        `registro.record()` saves `referencia_sha256` (a hash), but
        `write.py --referencia` needs a PATH. Added here, additively and
        idempotently, instead of touching `history.py` (which belongs to a
        different front): `history.py`'s `CREATE TABLE IF NOT EXISTS`
        coexists fine with a later `ALTER TABLE ADD COLUMN`.
        """
        if history is None:
            raise RuntimeError(
                "history.py does not import: %s" % FALTA.get("history")
            )
        conn = history.connect()
        cols = {f["name"] for f in conn.execute("PRAGMA table_info(grabadas)")}
        for col in (
            "referencia_path",
            "ezhex_origen",
            "compuerta_salida",
            "etiqueta_dispositivo",
            "comandos_dispositivo",
        ):
            if col not in cols:
                kind = "INTEGER" if col == "comandos_dispositivo" else "TEXT"
                conn.execute(f"ALTER TABLE grabadas ADD COLUMN {col} {kind}")
        conn.commit()
        return conn

    def _run_read(self, comparar: str | None = None, timeout: float = 60.0) -> dict:
        """`read_config.py` by subprocess. READ ONLY.

        `read_config.py` uses `read_config_from_remote` and no write or erase
        primitive at all. `aparato.identify()` does not accept
        `--comparar`, so the call lives here, but the parsing is delegated
        to `aparato.parse_identification()` so `read_config.py`'s output regexes
        have a single owner.
        """
        if remote is None:
            return {
                "ok": False,
                "returncode": None,
                "etapas": [],
                "stdout": "",
                "stderr": "remote.py does not import: %s" % FALTA.get("remote"),
            }
        with tempfile.TemporaryDirectory(prefix="harmony_leer_") as tmp:
            argv = [
                *_runtime.interprete(),
                str(LEER_PY),
                "--salida",
                str(Path(tmp) / "l.bin"),
            ]
            if comparar:
                argv += ["--comparar", str(_path(comparar))]
            try:
                r = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=str(CONFIG_WORK),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return {
                    "ok": False,
                    "returncode": None,
                    "etapas": [],
                    "stdout": "",
                    "stderr": f"timed out after {timeout}s",
                }
            except OSError as exc:
                return {
                    "ok": False,
                    "returncode": None,
                    "etapas": [],
                    "stdout": "",
                    "stderr": str(exc),
                }
            return remote.parse_identification(r.stdout, r.stderr, r.returncode)

    @staticmethod
    def _identity_summary(res: dict) -> dict:
        """Flattens `aparato.parse_identification`'s stages for the UI."""
        d: dict = {
            "arch": None,
            "skin": None,
            "firmware": None,
            "used": None,
            "totales": None,
            "dump_supported": None,
            "bytes_leidos": None,
            "veredicto": None,
        }
        for etapa in res.get("etapas") or []:
            datos = etapa.get("datos") or {}
            if etapa["etapa"] == "identidad" and etapa["ok"]:
                d["arch"] = datos.get("arch")
                d["skin"] = datos.get("skin")
                d["firmware"] = "%s.%s" % (
                    datos.get("fw_mayor"),
                    datos.get("fw_menor"),
                )
            elif etapa["etapa"] == "config":
                d["used"] = datos.get("usada")
                d["totales"] = datos.get("total")
                d["dump_supported"] = datos.get("dump_supported")
                d["bytes_leidos"] = datos.get("bytes_leidos")
            elif etapa["etapa"] == "veredicto":
                d["veredicto"] = (datos.get("text") or "").strip()
        return d

    # ==================================================================
    # simple CONTROL -- what the backend derives ON ITS OWN, without asking
    # the user anything. See "THE PROBLEM" in the brief: the reference, the
    # repoints, the index, and the listing of what's there today are not
    # decisions for the user to make.
    # ==================================================================
    @staticmethod
    def _config_from_dump(crudo: Path) -> bytes | None:
        """La CONFIG que hay adentro de un volcado crudo de flash, o `None`.

        EXISTE PARA QUE HAYA UN SOLO RECORTE. `read_flash_baseline.py` baja la
        ventana entera (3.932.160 B): la config es el prefijo hasta el cierre
        `PTYY` inclusive (1.418.476 B hoy) y el resto es relleno. Entregar el
        volcado entero como si fuera una config **no da un error claro**: se
        pasa 196.608 B del limite del formato GSPM (`0x390000` = 3.735.552) y
        `add_device.py` se planta sin poder explicar por que.

        Eso es exactamente lo que le pasaba al usuario: tocaba Refrescar en
        Control y a partir de ahi el Sync fallaba con "the tool that builds
        the new device refused to finish". El recorte estaba escrito, pero
        SOLO en el camino persistido -- y el camino en vivo, que tiene MAS
        prioridad, entregaba el crudo. Dos caminos para la misma pregunta y
        uno solo arreglado; por eso ahora hay uno.
        """
        try:
            if not crudo.is_file():
                return None
            raw = crudo.read_bytes()
        except OSError:
            return None
        i = raw.find(b"PTYY")
        if i < 0:
            return None
        cfg = raw[: i + 4]
        # That it is a config, not a run of bytes that happens to contain
        # "PTYY": the size declared at +4 has to match the cut.
        try:
            declarado = int.from_bytes(raw[4:7], "little") - 0x040000
        except Exception:  # noqa: BLE001
            return None
        return cfg if declarado == len(cfg) - 4 else None

    def _recorte_en_vivo(self) -> Path | None:
        """The file with the config trimmed out of the dump that was read THIS
        session (`self._verdad_actual`), or `None` if that dump does not
        give a valid config.

        It is written next to the dump and `medida.bin` is not reused: that
        one is the persisted path, with its own freshness guard, and
        overwriting it from here would mix two things that were decided
        separately.
        """
        crudo = self._verdad_actual and self._verdad_actual.get("blob")
        if not crudo:
            return None
        cfg = self._config_from_dump(Path(crudo))
        if cfg is None:
            return None
        target = self._datos / "referencia" / "en_vivo.bin"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.read_bytes() != cfg:
                target.write_bytes(cfg)
        except OSError:
            return None
        return target

    def _referencia_medida(self) -> dict | None:
        """The config the remote has RIGHT NOW, read from its flash, or `None`.

        Returns `None` -- not an approximation -- the moment anything does
        not add up: no dump at all, one that does not validate, or one that
        is OLDER than the last grabbed entry. "I could not measure" is a
        useful answer; "I measured something similar" is not, and that is
        the one that broke this the first time.

        The dump is the whole flash window (3.932.160 B); the config is the
        prefix up to and including the `PTYY` closer. Trimming there is the
        same criterion `configcheck.close()` uses.
        """
        crudo = self._datos / "verdad_actual.bin"
        cfg = self._config_from_dump(crudo)
        if cfg is None:
            return None

        # FRESHNESS: the dump has to be NEWER than the last grabbed entry.
        leido_en = crudo.stat().st_mtime
        last_write = 0.0
        if history is not None:
            try:
                conn = self._connect()
                try:
                    f = conn.execute("SELECT MAX(fecha) AS f FROM grabadas").fetchone()
                finally:
                    conn.close()
                if f is not None and f["f"]:
                    import datetime as _dt

                    last_write = _dt.datetime.fromisoformat(str(f["f"])).timestamp()
            except Exception:  # noqa: BLE001
                # If the last grabbed entry cannot be dated, the dump is NOT
                # assumed to be good: it falls back to the usual path.
                return None
        if leido_en < last_write:
            return None

        target = self._datos / "referencia" / "medida.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != cfg:
            target.write_bytes(cfg)
        return {
            "blob": str(target),
            # NO "just now". Este es el camino PERSISTIDO: se llega aca
            # cuando NO hay una lectura viva de esta sesion (esa la sirve
            # `_recorte_en_vivo()`, una rama antes, y esa si dice "just
            # now"). O sea que se llega justo cuando "just now" es falso --
            # y se veia: con el mando desenchufado, debajo del titulo "This
            # is not your control's state" decia "Source: read from your
            # remote's own memory just now". Dos frases contradictorias en
            # la misma tarjeta. Ahora fecha la lectura en vez de fingir que
            # es de recien.
            "origin": (
                "read from your remote's own memory on %s (not now -- that "
                "reading was saved)"
                % time.strftime("%Y-%m-%d %H:%M", time.localtime(leido_en))
            ),
            "write_id": None,
            "name": None,
            "confianza": "measured",
            "bytes": len(cfg),
        }

    def _current_reference(self) -> dict:
        """The `.bin` used as the base to add the next device: the LAST
        grabbed entry the user confirmed booted fine, or
        `backups/config_raw.bin` if none has been confirmed yet. The user
        does not need to know the concept of "reference" exists -- this is
        what resolves it on its own.
        """
        default = {
            "blob": str(BACKUPS / "config_raw.bin"),
            "origin": "factory default -- no grabbed entry has been confirmed yet",
            "write_id": None,
            "name": None,
        }

        # THE DEVICE FIRST, THE PAPERWORK AFTER. What the remote has can be
        # READ -- `estado_mando.refrescar()` pulls down the raw flash and
        # validates it -- and a read beats any bookkeeping, because the
        # bookkeeping has already been wrong: the app had been comparing against
        # the 1.379.186 B anchor while the remote had 1.418.476 B, and its gate
        # said "nothing moved that you did not ask for" while measuring against a
        # baseline that was not the device's. Writing like that reverted two
        # sessions of work, with the gate green.
        #
        # The FRESHNESS condition is what makes this safe: only a dump read
        # AFTER the last grabbed entry is any good. An earlier one describes
        # a remote that no longer exists, which is exactly the problem being
        # fixed here, only with a different date.
        measured = self._referencia_medida()
        if measured is not None:
            return measured

        if history is None:
            return default
        try:
            conn = self._connect()
        except Exception:  # noqa: BLE001
            return default
        try:
            row = conn.execute(
                "SELECT id, ezhex_path, etiqueta_dispositivo FROM grabadas "
                "WHERE verificado_por_usuario=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return default
        ez = Path(row["ezhex_path"] or "")
        if not ez.is_file():
            return default
        cache = self._datos / "referencia"
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / f"{row['id']:06d}.bin"
        if not target.exists():
            try:
                import ezhex

                _cab, binario = ezhex.split(ez.read_bytes())
                target.write_bytes(binario)
            except Exception:  # noqa: BLE001
                return default
        return {
            "blob": str(target),
            "origin": "the last grabbed entry confirmed good (#%d)" % row["id"],
            "write_id": row["id"],
            "name": row["etiqueta_dispositivo"],
        }

    def _last_devices_snapshot(self) -> dict | None:
        """The full device list from the LAST full capture found in
        `account_export/output/` (files already on disk, from before).

        This is NOT a live read of the device -- the Harmony One does not
        allow dumping its config: it is reading the most recent file
        already on disk. It carries the names as the user set them (TV,
        Home, DVR, ...), something the blob's section [5] does not
        have (it only counts how many there are).
        """
        if command_records is None or not OUTPUT_BRIDGE.exists():
            return None
        mejor: tuple[str, Path] | None = None
        rutas = (
            library.disk_configs()
            if library is not None
            else sorted(OUTPUT_BRIDGE.glob("*/hub-config-with-device.json"))
        )
        for jsn in rutas:
            d = jsn.parent
            man = d / "manifest.json"
            if not man.exists():
                continue
            # Only full captures describe what the remote has: a device
            # added from the catalog or from a .ir brings just one, and
            # treating it as a portrait of the remote would be misleading.
            if library is not None and library.origin_of(d) != "capturado":
                continue
            try:
                manifest = json.loads(man.read_text())
            except Exception:  # noqa: BLE001
                continue
            when = manifest.get("generated_at") or ""
            if mejor is None or when > mejor[0]:
                mejor = (when, jsn)
        if mejor is None:
            return None
        try:
            _protos, devs = command_records.load_hub_config(str(mejor[1]))
        except Exception:  # noqa: BLE001
            return None
        nombres = [command_records.device_name(dv) for dv in devs]
        return {
            "nombres": nombres,
            "count": len(nombres),
            "downloaded_at": mejor[0],
            "source": str(mejor[1]),
        }

    def _device_commands(self, config_json: str, device: str | None) -> int | None:
        if command_records is None:
            return None
        try:
            _protos, devs = command_records.load_hub_config(str(_path(config_json)))
        except Exception:  # noqa: BLE001
            return None
        for dv in devs:
            if device is None or command_records.device_name(dv) == device:
                return len(dv.get("Commands") or [])
        return None

    def remote_status(self) -> dict:
        """Everything needed for the top line of the Control tab: whether
        the remote is plugged in and, if it is, what it has today -- in
        plain human language, not offsets. It is the only call the simple
        UI needs to paint itself.
        """
        if remote is None:
            return _err("remote.py does not import: %s" % FALTA.get("remote"))
        ident = self.remote_identify()
        snap = self._last_devices_snapshot()
        if not ident.get("ok"):
            # HEADS UP: "couldn't ask" is NOT "not plugged in". The real
            # case is the device lock held by another operation
            # (identifying, closing the loop, a write in flight): if this
            # collapsed into "not connected", the app would tell someone
            # who already has it plugged in to plug it in, and the real
            # reason would be lost.
            return _ok(
                conectado=False,
                ocupado=True,
                reason=ident.get("error"),
                snapshot=snap,
            )
        return _ok(
            conectado=bool(ident.get("conectado")),
            ocupado=False,
            arch=ident.get("arch"),
            skin=ident.get("skin"),
            firmware=ident.get("firmware"),
            used=ident.get("used"),
            totales=ident.get("totales"),
            veredicto=ident.get("veredicto"),
            snapshot=snap,
        )

    def remote_real_state(self) -> dict:
        """LA verdad, en las tres situaciones reales -- ver `remote_status.py`.

        Distinto de `remote_status()` (arriba): ese metodo mezcla "esta
        enchufado" con `_last_devices_snapshot()`, un archivo CACHEADO de
        una descarga del catalogo, SIN mirar si hay mando conectado -- por
        eso la pantalla podia decir "tu control tiene N dispositivos" con
        el cable afuera (el sintoma reportado en el brief). Este metodo
        SIEMPRE devuelve una de tres situaciones, nunca las mezcla, y NUNCA
        usa un archivo cacheado como si fuera el estado del aparato:

            estado == 'desconectado'            -> `last_local_config`
                (si hay) es la ultima config que ESTE PROYECTO escribio;
                nunca se presenta como el estado del control.
            estado == 'conectado_verdad'        -> se leyo el flash de
                verdad; `n_devices`/`n_screens`/`blob` son reales.
            estado == 'conectado_sin_verdad'     -> el mando respondio pero
                lo que devolvio no valida; `reason` dice por que, medido.

        Es lo que **Refrescar** en la pantalla Control tiene que llamar
        para que el boton haga algo real (el brief: "si toco Refresh no
        hace nada"). Cada llamada vuelve a leer el flash del control desde
        cero (read-only, ~80 transacciones USB de 16 KiB) -- por eso esto
        no se dispara solo en cada repintado, solo cuando lo pide el
        usuario (o al entrar a Control por primera vez).

        Efecto colateral, DELIBERADO: si el resultado es
        `conectado_verdad`, deja ese blob como la referencia que el resto
        de la app usa (`_remote_blob()`, mas arriba) hasta la proxima
        llamada a este metodo -- es literalmente "que la verdad salga del
        control" para Catalogo/Actividades/Teclas tambien, no solo para
        esta pantalla. Y actualiza `mandos` (que remoto fisico es este),
        asi el historial multi-remoto se mantiene al dia con cada refresh,
        no solo con cada grabado.
        """
        if remote_status is None:
            return _err(
                "remote_status.py does not import: %s" % FALTA.get("remote_status")
            )
        if not self._lock_aparato.acquire(blocking=False):
            return _err("there is already an operation in progress with the device")
        try:
            return self._leer_estado_real()
        finally:
            self._lock_aparato.release()

    def _ultima_config_local(self) -> dict | None:
        """La ultima config que ESTE PROYECTO escribio y el usuario
        confirmo. Se le pasa a `estado_mando.refrescar()` para el caso
        DESCONECTADO, donde se muestra rotulada como historial local --
        nunca como el estado del aparato.
        """
        if history is None:
            return None
        try:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id, fecha, etiqueta_dispositivo, "
                    "comandos_dispositivo FROM grabadas "
                    "WHERE verificado_por_usuario=1 ORDER BY id DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                return None
            return {
                "write_id": row["id"],
                "fecha": row["fecha"],
                "name": row["etiqueta_dispositivo"],
                "commands": row["comandos_dispositivo"],
            }
        except Exception:  # noqa: BLE001
            return None

    def _leer_estado_real(self, on_evento=None) -> dict:
        """El cuerpo de `remote_real_state()`, **sin tomar el lock del
        aparato** -- lo toma quien llama.

        Existe partido en dos porque ahora hay DOS llamadores que necesitan
        exactamente esta secuencia y no pueden compartir el `acquire`:
        `remote_real_state()` (sincrono, toma y suelta el lock alrededor)
        y `control_conectar_iniciar()` (toma el lock en el hilo que atiende
        a la UI y lo suelta en el hilo de la lectura, para que la llamada
        vuelva enseguida con un `trabajo_id` y la barra pueda arrancar).
        Duplicar el cuerpo era garantizar que uno de los dos se quedara sin
        el efecto colateral que importa: dejar `_verdad_actual` puesto.

        `on_evento` viaja tal cual a `estado_mando.refrescar()`: es lo que
        va reportando bytes leidos / bytes totales mientras la lectura pasa.
        """
        r = remote_status.refrescar(
            self._datos,
            last_local_config=self._ultima_config_local(),
            on_evento=on_evento,
        )

        if r["state"] == remote_status.CONECTADO_VERDAD:
            self._verdad_actual = r
            if history is not None:
                identidad = r.get("identidad") or {}
                try:
                    history.identify_or_create_remote(
                        identidad,
                        serial=identidad.get("serial"),
                        baseline_sha256=r.get("sha256"),
                        n_dispositivos_actual=r.get("n_devices"),
                        n_pantallas_actual=r.get("n_screens"),
                        baseline_es_de_fabrica=r.get("parece_de_fabrica"),
                    )
                except Exception:  # noqa: BLE001
                    pass  # the real state is already computed; logging the remote is a bonus
        else:
            self._verdad_actual = None
        respuesta = _ok(**r)
        # Every answer is remembered, not only the good one: what
        # `_blob_de_referencia()` needs to know, when there is nothing on
        # disk, is whether the device was ALREADY asked a moment ago --
        # and "it is not plugged in" is an answer.
        self._ultimo_estado_real = (time.monotonic(), respuesta)
        return respuesta

    # ------------------------------------------------------------------
    # CONNECT: la misma lectura, pero con barra y sin perderse al cambiar
    # de solapa
    # ------------------------------------------------------------------
    #
    # Tres cosas que antes no estaban, y las tres por el mismo motivo: la
    # lectura real dura ~2 min (~80 transacciones USB de 16 KiB) y la
    # pantalla la trataba como si fuera instantanea.
    #
    #   `control_conectar_iniciar()`   arranca la lectura en un hilo y
    #                                  devuelve un `trabajo_id` enseguida.
    #   `control_conectar_progreso()`  polling: bytes medidos, etapa, fin.
    #   `control_presencia()`          "¿sigue enchufado?" barato (solo
    #                                  identifica, NO lee el flash).
    #   `control_estado_recordado()`   lo ultimo MEDIDO, sin tocar el USB.
    #
    # Es la misma forma que ya tenia el grabado (`sync_apply_start` /
    # `sync_progreso` / `progreso.TrabajoGrabado`), no una segunda
    # invencion: `TrabajoLectura` hereda de `TrabajoGrabado`.

    def control_conectar_iniciar(self) -> dict:
        """EL boton **Connect**. Arranca la lectura del flash EN UN HILO
        APARTE y devuelve `trabajo_id` para seguirla con
        `control_conectar_progreso()`.

        SOLO LECTURA. El camino entero es `read_flash_baseline.py`, que
        llama `get_identity()` y `read_flash_at()` y ninguna primitiva de
        escritura. No hay `ack` que pedir porque no hay nada que
        confirmar: esto no puede modificar el mando.

        **UNA SOLA LECTURA A LA VEZ, y sin decir que no.** Si ya hay una
        en curso devuelve EL MISMO `trabajo_id` con `ya_en_curso=True`, en
        vez de arrancar otra o de fallar. Ese es el caso real del pedido:
        el usuario aprieta Connect, se va a otra solapa mientras corre y
        vuelve -- la pantalla se re-engancha a la lectura que ya estaba,
        no dispara la segunda ni la tercera. El lock del aparato lo toma
        ESTE metodo y lo suelta el hilo, asi que ninguna otra operacion
        USB puede colarse en el medio.
        """
        if remote_status is None:
            return _err(
                "remote_status.py does not import: %s" % FALTA.get("remote_status")
            )
        if progress is None:
            return _err("progress.py does not import: %s" % FALTA.get("progress"))
        with self._lock_lectura:
            en_curso = self._trabajo_lectura
            if en_curso is not None and not en_curso.terminado:
                return _ok(
                    trabajo_id=en_curso.id,
                    ya_en_curso=True,
                    linea="Already reading your remote -- following that read.",
                )
            if not self._lock_aparato.acquire(blocking=False):
                return _err("there is already an operation in progress with the device")
            trabajo = progress.TrabajoLectura()
            self._trabajo_lectura = trabajo
            self._trabajos_lectura[trabajo.id] = trabajo
            # Las lecturas viejas se tiran: cada una guarda ~250 eventos y
            # una sesion larga puede tocar Connect muchas veces. Se dejan
            # unas cuantas para que un polling atrasado siga encontrando la
            # suya (y conteste "no read with that id" solo si de verdad es
            # antiquisima), no una sola.
            if len(self._trabajos_lectura) > 8:
                for old in list(self._trabajos_lectura)[:-8]:
                    if old != trabajo.id:
                        self._trabajos_lectura.pop(old, None)

        def _run() -> None:
            """El hilo de la lectura. Orden, igual que el del grabado:

            1. leer (con `on_evento` alimentando la barra),
            2. dejar la RESPUESTA en `trabajo.state` -- antes de
               `marcar_fin`, porque la UI pinta en cuanto ve `terminado`
               y sin esto no tendria que pintar,
            3. soltar el lock y `marcar_fin` en un `finally`, para que la
               pantalla salga de "connecting" SI O SI.
            """
            state: dict | None = None
            error: str | None = None
            try:
                state = self._leer_estado_real(on_evento=trabajo.add)
            except Exception as exc:  # noqa: BLE001
                error = _motivo(exc)
            finally:
                trabajo.state = state
                try:
                    self._lock_aparato.release()
                except Exception:  # noqa: BLE001
                    pass
                ok = bool(state and state.get("ok"))
                trabajo.marcar_fin(
                    ok=ok,
                    returncode=0 if ok else -1,
                    error=error or (None if ok else (state or {}).get("error")),
                )

        # Si el hilo no arranca, EL LOCK DEL APARATO SE SUELTA ACA. Sin
        # esto, un fallo al crear el hilo dejaba `_lock_aparato` tomado
        # para siempre: ninguna lectura, ningun grabado, ninguna sonda --
        # el aparato inalcanzable hasta reiniciar la app, y sin nada en
        # pantalla que dijera por que. El unico que suelta ese lock es el
        # `finally` de `_run()`, y si `_run()` nunca corre, nadie.
        try:
            threading.Thread(target=_run, daemon=True, name="conectar").start()
        except Exception as exc:  # noqa: BLE001
            with self._lock_lectura:
                self._trabajo_lectura = None
            self._trabajos_lectura.pop(trabajo.id, None)
            try:
                self._lock_aparato.release()
            except Exception:  # noqa: BLE001
                pass
            return _err(_motivo(exc))
        return _ok(
            trabajo_id=trabajo.id,
            ya_en_curso=False,
            linea=TEXTO_CONECTANDO,
        )

    def control_conectar_progreso(self, trabajo_id: str, start: int = 0) -> dict:
        """Polling de `control_conectar_iniciar()`: eventos nuevos desde
        `start`, el porcentaje, los BYTES medidos y, cuando termino, el
        estado completo. Ver `progreso.TrabajoLectura.snapshot()`.
        """
        trabajo = self._trabajos_lectura.get(trabajo_id)
        if trabajo is None:
            return _err("no read with that id (did the app restart?)")
        snap = trabajo.snapshot(start)
        # Mismo cuidado que en `sync_progreso()`: un `ok`/`error` dentro del
        # snapshot PISA el sobre de `_ok()` y la pantalla lee "el puente se
        # cayo" cuando en realidad la lectura sigue. Fue el BUG 1 del
        # grabado; aca se corta en los dos lados igual.
        snap.pop("ok", None)
        snap.pop("error", None)
        return _ok(**snap)

    def control_presencia(self) -> dict:
        """¿El mando SIGUE enchufado? Medido, barato, sin leer el flash.

        Es lo que hace que el estado conectado pueda sobrevivir al cambio
        de solapa sin ser una mentira: al volver a Control la pantalla
        muestra al instante lo ultimo MEDIDO (`control_estado_recordado()`)
        y llama a esto para volver a medir la presencia. Si el cable ya no
        esta, el estado se cae solo; si esta, se queda, sin repetir los
        ~2 min de la lectura entera.

        `ocupado=True` (el lock del aparato tomado por una lectura o un
        grabado en vuelo) NO es "desconectado" y se distingue: quien llama
        tiene que dejar el estado como estaba, no bajarlo.
        """
        if remote_status is None:
            return _err(
                "remote_status.py does not import: %s" % FALTA.get("remote_status")
            )
        if not self._lock_aparato.acquire(blocking=False):
            return _ok(
                presente=None,
                ocupado=True,
                reason="there is already an operation in progress with the device",
            )
        try:
            r = remote_status.presencia()
        except Exception as exc:  # noqa: BLE001
            return _err(_motivo(exc))
        finally:
            self._lock_aparato.release()
        # Si el mando NO esta, lo que se leyo antes deja de ser el estado
        # del aparato: `_verdad_actual` se limpia ACA, en Python, para que
        # ninguna pantalla siga sirviendo el blob de un mando que no esta.
        if not r.get("presente"):
            self._verdad_actual = None
        return _ok(ocupado=False, **r)

    def control_estado_recordado(self) -> dict:
        """Lo ULTIMO que se midio, sin tocar el USB ni un byte.

        NO es una fuente de verdad nueva: es la MISMA respuesta que dio
        `remote_real_state()` la ultima vez, con su edad en segundos al
        lado para que la pantalla pueda decir cuando fue. Existe para que
        cambiar de solapa y volver no borre lo que ya se sabe (y no cueste
        otra lectura de 2 min).

        `present=False` cuando todavia no se midio nada en esta sesion: ahi la
        pantalla ofrece **Connect** y no inventa nada.
        """
        previo = self._ultimo_estado_real
        if previo is None:
            return _ok(hay=False, state=None, edad_segundos=None)
        edad = round(time.monotonic() - previo[0], 1)
        # Copia, y con su `ok` INTACTO. Va ANIDADO bajo `state`, no
        # esparcido en el sobre, asi que no puede pisar nada -- y la
        # pantalla lo lee: `pintarEstadoReal()` decide por `r.ok` si esta
        # mirando una respuesta o un error. Sacarlo "por las dudas" hizo
        # exactamente eso: volver a Control repintaba "the read did not
        # finish" sobre un mando conectado y bajaba `CONECTADO_MANDO`,
        # apagando Catalogo/Actividades/Teclas. Medido en la UI real.
        return _ok(hay=True, state=dict(previo[1]), edad_segundos=edad)

    def remote_apply(
        self, config_json: str, name: str, device: str | None = None
    ) -> dict:
        """THE single button on the Control tab.

        Derives EVERYTHING that used to have to be filled in by hand -- the
        reference (`_current_reference`), the index (blob's section [5]),
        the output paths, and the `--repoint` values (from
        `add_device.py`'s stdout) -- and runs `generate()` + the gate. Does
        NOT write the device: that stays a separate, explicit step
        (`remote_record` / `remote_register_manual_recording`), precisely
        because the gate has to decide whether that next step exists at all.
        """
        name = (name or "").strip()
        if not name:
            return _err("the name to show in the remote's menu is missing")
        config_json = (config_json or "").strip()
        if not config_json:
            return _err("a device to add needs to be chosen")

        # CLOSED TRAP: `config_work/add_device.py` has
        # `--device default="Philips TV"` (left over from when
        # the script only served one TV). If a `device=None` were let
        # through from here, a JSON that doesn't bring that Philips fails
        # with "'Philips TV' not found; available: [...]", a message
        # that means nothing to the user. `config_work/` is not touched: the
        # name gets resolved HERE and is always passed explicitly.
        if not device:
            elegibles = self.remote_devices_from_json(config_json)
            items = elegibles.get("items") or []
            if len(items) == 1:
                device = items[0]["name"]
            elif items:
                return _err(
                    "That file brings %d devices and it's not clear which "
                    "one to add. Pick it from the list above." % len(items)
                )
            else:
                return _err(
                    elegibles.get("error") or "that file brings no device at all"
                )

        ref = self._current_reference()
        blob = Path(ref["blob"])
        if not blob.exists():
            return _err(
                "no reference blob was found (%s)" % ref.get("origin"),
                referencia=ref,
            )

        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "device"
        marca = time.strftime("%Y%m%d_%H%M%S")
        salida = SALIDA / f"{slug}_{marca}.bin"
        ezhex_salida = SALIDA / f"{slug}_{marca}.EZHex"
        plantilla = BACKUPS / "one_20260724_210614_a.EZHex"

        gen = self.remote_generate(
            {
                "blob": str(blob),
                "config_json": config_json,
                "device": device or None,
                "name": name,
                "index": "auto",
                "output": str(salida),
                "ezhex": str(ezhex_salida),
                "plantilla": str(plantilla),
            }
        )
        if not gen.get("ok"):
            return _err(
                "Could not generate the new device.",
                etapa="generar",
                referencia=ref,
                technical_detail={"generar": gen},
            )

        new_file = gen.get("ezhex") or gen.get("output")
        comp = self.remote_gate(
            new_file, str(blob), gen.get("repoints_int") or []
        )
        if not comp.get("ok"):
            return _err(
                "The gate could not be run.",
                etapa="gate",
                referencia=ref,
                technical_detail={"generar": gen, "gate": comp},
            )

        cmd = None
        if comp.get("passed"):
            cmd = self.remote_record_command(
                new_file, str(blob), comp.get("repoints_int") or []
            )

        return _ok(
            ready=bool(comp.get("passed")),
            name=name,
            command_records=self._device_commands(config_json, device),
            file=new_file,
            referencia=ref,
            generate=gen,
            gate=comp,
            command=cmd if (cmd and cmd.get("ok")) else None,
        )

    # ==================================================================
    # CONTROL -- what it has today, and removing one
    # ==================================================================
    def _remote_blob(self) -> dict:
        """LA referencia, con una guarda en la unica salida.

        Envuelve a `_remote_blob_raw()`, que es quien elige entre los cinco
        origenes. La guarda esta ACA y no en cada `return` por una razon
        concreta: el bug que la motivo fue tener el recorte del volcado
        escrito en un camino y no en el otro. Arreglar los dos caminos no
        impide que aparezca un tercero -- validar en la salida, si.

        Que se exige: que el archivo exista, que entre en el limite del
        formato (`0x390000`) y que cierre con `PTYY`. Un blob que no cumple
        eso no produce un error entendible mas adelante: hace que
        `add_device.py` se plante con un mensaje que no dice nada, que es
        justo lo que el usuario vio ("no hace sync").

        Si la guarda salta NO se sigue con un blob invalido: se cae al ancla,
        y se deja dicho en `reference_warning` -- una referencia que no valida
        es un problema para mostrar, no para tragar.
        """
        ref = self._remote_blob_raw()
        try:
            p = Path(ref.get("blob") or "")
            n = p.stat().st_size
            trailer = p.read_bytes()[-4:] if n >= 4 else b""
        except OSError:
            n, trailer = -1, b""
        if 0 < n < 0x390000 and trailer == b"PTYY":
            return ref
        bad = "%s (%s B)" % (ref.get("blob"), n if n >= 0 else "could not be read")
        if ANCLA_BIN.exists() and _md5(ANCLA_BIN) == ANCLA_MD5:
            return {
                "blob": str(ANCLA_BIN),
                "origin": (
                    "the file this project has confirmed matches what's "
                    "currently written to your control"
                ),
                "write_id": None,
                "name": None,
                "confianza": "ancla",
                "reference_warning": (
                    "the reference that was going to be used is not a valid "
                    "configuration, so the known-good one is being used "
                    "instead: %s" % bad
                ),
            }
        return dict(ref, reference_warning="this reference does not validate: %s" % bad)

    def _remote_blob_raw(self) -> dict:
        """The blob that BEST represents what the remote has today.

        `_current_reference()` falls back to `config_raw.bin` (factory, 3
        devices) as long as no grabbed entry has been confirmed in the
        history. For the ADD path that's conservative and fine. For LISTING
        and REMOVING it's not enough: it would show 3 devices on a remote
        that has 5, and -- worse -- the gate would compare against a blob
        that isn't the one actually written, in which case `nothing_moved`
        would mean nothing.

        Precedence, declared and in this order:

          1. the last grabbed entry the user confirmed as good (history);
          2. if there is none, the ANCHOR -- `output/config_empaquetada.bin`
             -- but ONLY if its md5 still matches the declared one. It's the
             blob this project claims is grabbed and running today (ESTADO.md);
          3. if the anchor is missing or doesn't match, `config_raw.bin` (factory).

        Never guesses: returns `origin` in plain language so the UI can say it.

        ADDED precedence 0, ABOVE the three below: if `remote_real_state()`
        was called THIS SESSION and came back `CONECTADO_VERDAD` (the remote
        answered AND its raw flash validated), that freshly-read blob wins
        over everything -- it is not a cached file, it is what the remote
        just said it has. `self._verdad_actual` is only ever set by
        `remote_real_state()`, and only ever cleared implicitly by the app
        restarting: a state read once this session stays authoritative for
        the rest of it (re-reading the whole flash on every screen paint
        would be slow and wasn't asked for; re-reading it for real is what
        the Refrescar button on the Control screen is for).
        """
        if self._verdad_actual and self._verdad_actual.get("blob"):
            # TRIM IT. What `estado_mando.refrescar()` leaves in `blob` is the
            # WHOLE FLASH WINDOW, not a config: 3.932.160 B, of which
            # 2.513.684 are padding. Passing it like that blows straight through the
            # limite del formato (0x390000) y `add_device.py` se planta --
            # which is the "it does not sync" the user reported after hitting
            # Refrescar. The trim is the same one as in `_config_from_dump()`:
            # a single one, for both paths.
            recortado = self._recorte_en_vivo()
            if recortado is not None:
                return {
                    "blob": str(recortado),
                    "origin": (
                        "read straight from your remote's flash just now, in "
                        "this session (not a cached file) -- see Refrescar on "
                        "Control"
                    ),
                    "write_id": None,
                    "name": None,
                    "confianza": "verdad_en_vivo",
                }
            # If this session's dump does not give a valid config it is NOT used
            # anyway: it keeps going down the precedence list. Handing over
            # something that does not validate is what caused this.
        ref = self._current_reference()
        # WATCH THE ORDER OF THESE TWO QUESTIONS. It used to be decided by
        # `write_id`, and a reference MEASURED off the flash has no grabada_id
        # -- it did not come from the history, it came from the device -- so it fell
        # through the if and the anchor won: the real read of the remote was
        # silently thrown away in favour of a file from two sessions ago. It asks
        # for `confianza`, which is what really tells where it came from.
        if ref.get("confianza") == "measured":
            return ref
        if ref.get("write_id") is not None:
            return dict(ref, confianza="historial")
        if ANCLA_BIN.exists() and _md5(ANCLA_BIN) == ANCLA_MD5:
            return {
                "blob": str(ANCLA_BIN),
                "origin": (
                    "the file this project has confirmed matches what's "
                    "currently written to your control"
                ),
                "write_id": None,
                "name": None,
                "confianza": "ancla",
            }
        return dict(ref, confianza="fabrica")

    # ==================================================================
    # FIRST USE: there is no configuration on disk yet, and reading the
    # remote is what produces one
    # ==================================================================
    def _estado_real_cacheado(self, *, frescura: float = 45.0) -> dict:
        """`remote_real_state()`, without asking the device the same
        question three times for one screen.

        Control paints three cards (devices, activities, keys) and each one
        resolves its own reference. If each resolution fired its own full
        USB read, opening the screen once would read the flash three times
        -- about 80 transactions each. Within `frescura` seconds the answer
        already measured is reused; after that it asks again. Refresh always
        asks again, because it calls `remote_real_state()` directly and
        that method never looks at this cache.
        """
        previo = self._ultimo_estado_real
        if previo is not None and (time.monotonic() - previo[0]) < frescura:
            return previo[1]
        return self.remote_real_state()

    def _blob_de_referencia(self, blob: str | None = None) -> tuple:
        """`(path, ref, error)` -- the blob every read-only screen works off.

        WHY THIS EXISTS. In a fresh clone `backups/config_raw.bin` is not
        there: it is a raw dump of somebody's own remote and it is never
        published. `_referencia_actual()` names it as the last fallback, so
        the three screens that read it answered with

            the reference blob does not exist: .../backups/config_raw.bin

        which is a dead end wearing the clothes of an error message. Reading
        the remote is THE function of this app, the capability is already
        here (`remote_real_state()` reads the flash for real and keeps
        what it read), and the screen was pointing at a file instead of at
        it.

        So when there is no configuration to read, this asks the remote --
        the same read Refresh does, READ ONLY, not a single write primitive
        -- and if the remote answers, that is the reference. If it does not,
        the answer says what to plug in and what will happen, and carries
        `primer_uso=True` so the screen can offer the button that does it.
        Never a path to a file that does not exist.

        `blob` given by the caller is a different matter: somebody named a
        specific file, and reading the remote would not produce THAT file,
        so it is not offered.
        """
        ref = self._remote_blob()
        p = _path(blob) if blob else Path(ref["blob"])
        if p.exists():
            return p, ref, None
        if blob:
            return None, ref, _err("that file does not exist: %s" % p, referencia=ref)

        if remote_status is None:
            return (
                None,
                ref,
                _err(
                    "There is no configuration read from your remote yet, and "
                    "the part of the app that reads it did not load: %s"
                    % FALTA.get("remote_status"),
                    primer_uso=True,
                    puede_leer=False,
                ),
            )

        # THE READ. Read-only: `read_flash_baseline.py` calls `get_identity`
        # and `read_flash_at` and nothing else -- there is no write or erase
        # primitive anywhere in that path.
        state = self._estado_real_cacheado()
        if state.get("ok") and state.get("state") == remote_status.CONECTADO_VERDAD:
            ref = self._remote_blob()  # the live read wins the precedence now
            p = Path(ref["blob"])
            if p.exists():
                return p, ref, None
            # It answered and validated, and the trim still did not give a
            # config. That is measured, and it is said, not smoothed over.
            return (
                None,
                ref,
                _err(
                    "Your remote answered and its memory read back, but the "
                    "configuration inside it could not be cut out of the dump.",
                    primer_uso=True,
                    puede_leer=True,
                    state=state.get("state"),
                ),
            )

        return None, ref, self._error_de_primer_uso(state)

    @staticmethod
    def _error_de_primer_uso(state: dict) -> dict:
        """The answer when there is nothing to read yet AND the remote did
        not give one. Four causes, four different texts, none of them a
        file path.

        The libconcord one is not reworded here: `grabar.TEXT_WITHOUT_LIBCONCORD`
        already says what it is, why it is not shipped and how to build it,
        and it is the text every other screen shows for the same cause. It
        is read with `getattr` because that constant is not present in every
        build of `write.py`; `LIB`, which is the FACT being measured, is.
        """
        reason = str(state.get("reason") or state.get("error") or "")

        # The read did not even get as far as a state. Today the one way
        # that happens is the device lock being held by another operation,
        # and that already has its own sentence -- which must not be
        # replaced by "plug it in", because it IS plugged in and something
        # else is using it. Same distinction `remote_status()` makes
        # between "busy" and "not connected".
        if not state.get("ok") and not state.get("state"):
            return _err(
                state.get("error") or "the remote could not be asked",
                primer_uso=True,
                puede_leer=True,
            )

        sin_libconcord = False
        texto_libconcord = ""
        try:
            import write  # noqa: PLC0415 -- `LIB` is a path; loading is in cargar()

            sin_libconcord = getattr(write, "LIB", "?") is None
            texto_libconcord = getattr(write, "TEXT_WITHOUT_LIBCONCORD", "")
        except Exception:  # noqa: BLE001
            pass  # if it does not even import, that is not "libconcord is missing"
        if sin_libconcord or (texto_libconcord and texto_libconcord[:40] in reason):
            return _err(
                texto_libconcord or reason,
                primer_uso=True,
                puede_leer=False,
                state=state.get("state"),
            )

        if state.get("state") == remote_status.CONECTADO_SIN_VERDAD:
            return _err(
                state.get("mensaje")
                or "Your remote answered, but its configuration did not read back.",
                primer_uso=True,
                puede_leer=True,
                state=state.get("state"),
                reason=reason or None,
            )

        return _err(
            TEXTO_PRIMER_USO,
            primer_uso=True,
            puede_leer=True,
            state=state.get("state") or remote_status.DESCONECTADO,
            reason=reason or None,
        )

    # ==================================================================
    # ACTIVITIES -- which device each one touches, editing them, deleting them
    # ==================================================================
    def activities_list(self, blob: str | None = None) -> dict:
        """The remote's activities, with WHICH DEVICE EACH ONE TOUCHES.

        Runs `config_work/screen_activities.py` by subprocess (same rule
        as everywhere else: the model is not reimplemented here).

        The attribution is BY DATA, not by naming everything: it resolves
        `seccion[10][ordinal] -> ENTER hook -> SETs (tag>=0x80) ->
        register [14][property] -> transition -> {cmd_id,0x7D} -> k1`. That
        chain's check is documented in `config_work/activities.py`'s
        docstring: contingency 5/0/0/7 over the 12 registers with
        transitions (one-tail Fisher 1/792), `index == id` mapping 9/9
        against 5/9 for the best competitor, and cross-checking against the
        Hub's INDEPENDENT oracle (the decoded IR waveform), which gives 5/5.

        `determinado=False` on an activity does NOT mean "it uses no
        device": it means the data chain does not explain it, and the UI is
        obligated to say so. Today that happens to `Apagar todo` (All Off).
        """
        if generate is None:
            return _err("generate.py does not import: %s" % FALTA.get("generate"))
        blob_p, ref, absent = self._blob_de_referencia(blob)
        if absent is not None:
            return absent
        target = self._datos / "actividades.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        res = generate.activities(blob_p, target)
        if not res.get("ok"):
            return _err(
                res.get("stderr")
                or "screen_activities.py aborted (%s)" % res.get("returncode"),
                referencia=ref,
                technical_detail={"activities": res},
            )
        try:
            datos = json.loads(target.read_text())
        except Exception as exc:  # noqa: BLE001
            return _err("could not read the activities report: %s" % exc)
        return _ok(
            activities=datos.get("activities") or [],
            devices=datos.get("devices") or [],
            palancas=datos.get("levers_by_device") or {},
            menu=datos.get("menu"),
            create=datos.get("create") or {},
            gold_check=datos.get("gold_check") or {},
            oraculo_ir=datos.get("oraculo_ir"),
            referencia=ref,
            blob=str(blob_p),
            technical_detail={"activities": res},
        )

    def activity_prepare(
        self,
        ordinal: int,
        accion: str,
        argumento: str | None = None,
        blob: str | None = None,
    ) -> dict:
        """Prepares the blob with ONE activity edited. Does NOT write the device.

        Exact mirror of `remote_delete`: derives the reference on its own,
        runs `edit_activity.py` by subprocess, then the gate. Returns the
        same shape (`ready`, `file`, `referencia`, `gate`, `command`)
        so the UI can reuse the same write path, the same "did it boot
        fine?" question, and the same history -- with no second
        implementation that could be more lax.

        `accion` comes from the white list `generar.ACTIVITY_ACTIONS`: the
        UI cannot invent a flag.
        """
        if generate is None:
            return _err("generate.py does not import: %s" % FALTA.get("generate"))
        try:
            ordinal = int(ordinal)
        except Exception:  # noqa: BLE001
            return _err("invalid ordinal: %r" % (ordinal,))
        if accion not in generate.ACTIVITY_ACTIONS:
            return _err("action not allowed: %r" % (accion,))

        listado = self.activities_list(blob)
        if not listado.get("ok"):
            return listado
        act = next(
            (a for a in listado["activities"] if a.get("ordinal") == ordinal), None
        )
        if act is None:
            return _err("the remote has no activity %d" % ordinal)
        if accion in ("renombrar", "erase") and not act.get("en_menu"):
            # The same reason the user already saw in the list, recomputed
            # here: the UI cannot enable on its own what Python is denying.
            return _err(
                "“%s” has no row in the menu (it hangs off a "
                "physical key): there is no row to rename or remove."
                % act.get("name"),
                actividad=act,
            )

        ref = listado["referencia"]
        blob_p = Path(listado["blob"])
        marca = time.strftime("%Y%m%d_%H%M%S")
        salida = SALIDA / f"actividad_{ordinal}_{accion}_{marca}.bin"
        ezhex_salida = SALIDA / f"actividad_{ordinal}_{accion}_{marca}.EZHex"
        plantilla = BACKUPS / "one_20260724_210614_a.EZHex"

        res = generate.edit_activity(
            blob_p,
            ordinal=ordinal,
            accion=accion,
            argumento=argumento,
            salida=salida,
            ezhex=str(ezhex_salida),
            plantilla=str(plantilla),
        )
        repuntes = sorted(
            {int(x, 16) for x in RE_REPUNTA.findall(res.get("stdout", ""))}
        )
        det = {
            "herramienta": "edit_activity.py",
            "command": res.get("command"),
            "returncode": res.get("returncode"),
            "stdout": res.get("stdout", ""),
            "stderr": res.get("stderr", ""),
            "blob": str(blob_p),
            "output": str(salida) if salida.exists() else None,
            "ezhex": str(ezhex_salida) if ezhex_salida.exists() else None,
            "ordinal": ordinal,
            "accion": accion,
            "argumento": argumento,
            "repuntes": [_hex(p) for p in repuntes],
            "repoints_int": repuntes,
        }
        if not res.get("ok"):
            return _err(
                (res.get("stderr") or "").strip().splitlines()[-1]
                if (res.get("stderr") or "").strip()
                else "Could not prepare the remote with that change.",
                etapa="editar",
                referencia=ref,
                actividad=act,
                technical_detail={"editar": det},
            )
        if salida.exists():
            det["md5"] = _md5(salida)
            det["tamano"] = salida.stat().st_size

        new_file = det.get("ezhex") or det.get("output")
        comp = self.remote_gate(new_file, str(blob_p), repuntes)
        if not comp.get("ok"):
            return _err(
                "The gate could not be run.",
                etapa="gate",
                referencia=ref,
                actividad=act,
                technical_detail={"editar": det, "gate": comp},
            )
        cmd = None
        if comp.get("passed"):
            cmd = self.remote_record_command(
                new_file, str(blob_p), comp.get("repoints_int") or []
            )
        return _ok(
            ready=bool(comp.get("passed")),
            accion=accion,
            ordinal=ordinal,
            name=act.get("name"),
            argumento=argumento,
            file=new_file,
            referencia=ref,
            generate=det,
            gate=comp,
            command=cmd if (cmd and cmd.get("ok")) else None,
        )

    def remote_list_devices(self, blob: str | None = None) -> dict:
        """ALL the devices the remote has today, read from the blob.

        Runs `config_work/list_devices.py` by subprocess (same rule as the rest of
        `config_work/`: the model is not reimplemented here). Each device
        comes with its name, how many commands it has, and whether it came
        from the factory or was added by the user.

        `borrable`, `reason`, and `se_pierde` are NOT decided here: they come
        from `list_devices.py`, which derives them from the same model
        `delete_device.py` uses. There is ONE limit left, and it does not
        distinguish factory from added:

          * the LAST one left on menu page 1. Removing it would leave the
            Devices menu with no row at all there, a state that does not
            exist at the factory.

        Everything else can be removed: factory ones (k1 0..2) just like the
        added ones, and going from N>1 back to N=1 (which restores the
        0xAE/0xAF strips) is already handled by repointing to the factory
        header that stays byte for byte in place. `delete_device.py`'s checks
        (a..h) always run before writing anything; if any of them fails for
        a specific device, `delete_device.py` aborts with its own message and
        `remote_delete` returns it as is -- there is no second, more
        lenient opinion here.

        `se_pierde` is also the list of ENGLISH sentences the UI shows in
        the confirmation: which device goes away, how many commands, and
        what happens to the activities that use it. Computed with
        `activities.py` (the transitive closure of section [14], the
        activity engine) and it is the SAME function `delete_device.py` later
        prints, so the two screens can never say different things.

        It also reports `huerfanos_seccion5`: the indices section [5]
        declares that the menu no longer reaches. That is exactly what a
        removal leaves behind, and it is the CORRECT state -- see
        `list_devices.py`'s docstring.
        """
        if generate is None:
            return _err("generate.py does not import: %s" % FALTA.get("generate"))
        blob_p, ref, absent = self._blob_de_referencia(blob)
        if absent is not None:
            return absent
        target = self._datos / "listado.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        res = generate.list_devices(blob_p, target)
        if not res.get("ok"):
            return _err(
                res.get("stderr") or "list_devices.py aborted (%s)" % res.get("returncode"),
                referencia=ref,
                technical_detail={"listar": res},
            )
        try:
            datos = json.loads(target.read_text())
        except Exception as exc:  # noqa: BLE001
            return _err("could not read the listing: %s" % exc)

        items = datos.get("devices") or []
        agregados = [d for d in items if not d.get("de_fabrica")]
        # `borrable`, `reason`, and `se_pierde` come ALREADY resolved from
        # `list_devices.py`, which derives them from the same model `delete_device.py`
        # uses. NOTHING gets recomputed here: a second opinion in Python was
        # exactly the bug that flatly refused the factory ones when
        # `delete_device.py` already knew how to remove them.
        for d in items:
            d.setdefault("borrable", True)
            d.setdefault("reason", None)
            d.setdefault("se_pierde", [])
        return _ok(
            devices=items,
            cuantos=len(items),
            agregados=len(agregados),
            de_fabrica=len(items) - len(agregados),
            activities=datos.get("activities") or {},
            huerfanos=datos.get("huerfanos_seccion5") or [],
            declarados_seccion5=datos.get("declarados_seccion5"),
            glyph_warning=datos.get("glyph_warning"),
            referencia=ref,
            blob=str(blob_p),
            technical_detail={"listar": res},
        )

    def remote_delete(self, k1: int) -> dict:
        """Prepares the blob WITHOUT device `k1`. Does NOT write the device.

        Exact mirror of `remote_apply`: derives the reference on its own,
        runs `delete_device.py` by subprocess, then the gate. Returns the same
        shape as `remote_apply` (`ready`, `file`, `referencia`,
        `gate`, `command`), precisely so the UI reuses the same write
        path, the same "did it boot fine?" question, and the same history --
        with no second implementation that could be more lax.
        """
        if generate is None:
            return _err("generate.py does not import: %s" % FALTA.get("generate"))
        try:
            k1 = int(k1)
        except Exception:  # noqa: BLE001
            return _err("invalid index: %r" % (k1,))

        listado = self.remote_list_devices()
        if not listado.get("ok"):
            return listado
        chosen = next((d for d in listado["devices"] if d.get("k1") == k1), None)
        if chosen is None:
            return _err(
                "the remote has no device with index %d" % k1,
                listado=listado,
            )
        if not chosen.get("borrable"):
            # The same reason the user already saw in the list, recomputed
            # here: the UI cannot enable on its own what Python is denying.
            return _err(
                "that device cannot be removed from the app (%s)"
                % chosen.get("reason"),
                reason=chosen.get("reason"),
                device=chosen,
            )

        ref = listado["referencia"]
        blob = Path(listado["blob"])
        slug = (
            re.sub(r"[^a-z0-9]+", "_", (chosen.get("name") or "").lower()).strip("_")
            or "device"
        )
        marca = time.strftime("%Y%m%d_%H%M%S")
        salida = SALIDA / f"sin_{slug}_{marca}.bin"
        ezhex_salida = SALIDA / f"sin_{slug}_{marca}.EZHex"
        plantilla = BACKUPS / "one_20260724_210614_a.EZHex"

        res = generate.delete_device(
            blob,
            index=k1,
            salida=salida,
            ezhex=str(ezhex_salida),
            plantilla=str(plantilla),
        )
        repuntes = sorted(
            {int(x, 16) for x in RE_REPUNTA.findall(res.get("stdout", ""))}
        )
        bor = {
            "herramienta": "delete_device.py",
            "command": res.get("command"),
            "returncode": res.get("returncode"),
            "stdout": res.get("stdout", ""),
            "stderr": res.get("stderr", ""),
            "blob": str(blob),
            "output": str(salida) if salida.exists() else None,
            "ezhex": str(ezhex_salida) if ezhex_salida.exists() else None,
            "index": k1,
            "name": chosen.get("name"),
            "repuntes": [_hex(p) for p in repuntes],
            "repoints_int": repuntes,
        }
        if not res.get("ok"):
            return _err(
                "Could not prepare the remote without that device.",
                etapa="erase",
                referencia=ref,
                device=chosen,
                technical_detail={"erase": bor},
            )
        if salida.exists():
            bor["md5"] = _md5(salida)
            bor["tamano"] = salida.stat().st_size
            bor["devices"] = self._count_devices(salida)

        new_file = bor.get("ezhex") or bor.get("output")
        comp = self.remote_gate(new_file, str(blob), repuntes)
        if not comp.get("ok"):
            return _err(
                "The gate could not be run.",
                etapa="gate",
                referencia=ref,
                device=chosen,
                technical_detail={"erase": bor, "gate": comp},
            )

        cmd = None
        if comp.get("passed"):
            cmd = self.remote_record_command(
                new_file, str(blob), comp.get("repoints_int") or []
            )

        return _ok(
            ready=bool(comp.get("passed")),
            accion="erase",
            name=chosen.get("name"),
            command_records=chosen.get("commands"),
            k1=k1,
            file=new_file,
            referencia=ref,
            generate=bor,
            gate=comp,
            command=cmd if (cmd and cmd.get("ok")) else None,
        )

    # ==================================================================
    # PENDING CHANGES -- the session's list, the summary, and Sync
    # ==================================================================
    def changes_list(self) -> dict:
        """Everything prepared in this session. Applies nothing."""
        if self._changes is None:
            return _err("changes.py does not import: %s" % FALTA.get("changes"))
        items = [c.to_dict() for c in self._changes.listar()]
        return _ok(items=items, count=len(items))

    def _plantilla_de_json(self, config_json, device=None) -> dict:
        """Which standard keys a device FILE would bind, before it is added.

        The device doesn't exist in the blob yet -- it has no `k1` and no
        page of its own -- so this can't be the real plan. What it can do,
        and what it is for, is answer "how many keys is this going to bring
        wired, and which ones is it not" at the moment of queueing, from the
        very list of command names `add_device.py` is going to walk (the
        order of that list IS the `k2` order). Read only.
        """
        if key_template is None:
            return {
                "ok": False,
                "error": FALTA.get(
                    "key_template", "key_template.py does not import"
                ),
            }
        try:
            # EL MISMO lector que usa el paso de Sync (`changes.py`), para
            # what the screen promises when queueing and what actually gets bound
            # on syncing come out of the same file and the same device.
            nombres, err = key_template.nombres_de_json(
                _path(config_json), device or None
            )
            if err:
                return {"ok": False, "error": err}
            plan = key_template.plan_from_names(nombres)
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc)
        plan["ok"] = True
        plan["device"] = device
        return plan

    @staticmethod
    def _frase_plantilla(plan: dict) -> str:
        """THE one sentence, written once. The Control screen shows it, the
        Sync list repeats it and `_change_label()` falls back to it -- if
        each of them worded it on its own they would drift, and the Sync
        list would end up saying less than the screen the change came from.
        """
        if not plan.get("ok"):
            return ""
        t = " -- and %d of its keys get bound automatically" % plan["n_ligadas"]
        if plan.get("n_missing"):
            t += ", %d can't (%s)" % (
                plan["n_missing"],
                ", ".join("%s: no %s" % (f["key"], f["rol"]) for f in plan["missing"]),
            )
        return t

    def device_template_preview(self, config_json: str, device: str = "") -> dict:
        """The same thing, for the Control screen: what the device about to
        be added is going to bring wired. Read only, touches nothing."""
        plan = self._plantilla_de_json(config_json, device or None)
        if not plan.get("ok"):
            return _err(plan.get("error") or "no plan could be built")
        plan["frase"] = self._frase_plantilla(plan)
        # `plan` brings its own `ok`: it is popped BEFORE the splat. It is the trap
        # that already bit with `_ok(**snapshot)` -- two `ok` keys deciding
        # different things in the same envelope.
        plan.pop("ok", None)
        return _ok(**plan)

    def _change_label(self, kind: str, parametros: dict) -> str:
        """Best effort, READ ONLY, to work out a human description when the
        screen that adds the change did not bring one already computed. It
        reuses the SAME queries each individual screen already uses
        (`remote_devices_from_json`, `remote_list_devices`,
        `activities_list`) -- it never invents a number.

        `reassign_key` is deliberately NOT resolved here: the Keys screen
        already knows the name of the command picked in its own dropdown
        (`command_mapping()`); recomputing it here would mean re-reading
        ~950 slots for every key added to the list, dead expensive for
        something the caller already has at hand. If no explicit `label`
        comes in for this type, a generic one is built out of the raw fields
        -- better that than nothing, never an error.
        """
        if kind == "add_device":
            # `name` and `config_json` are read by index: by the time this
            # runs, `changes_add()` has already rejected the change if any
            # was missing (`changes.parametros_faltantes`). The
            # `or "(unnamed)"` that used to be here described nothing: it turned a
            # missing parameter into a plausible label, the change got onto the
            # list anyway, and the user only found out at Sync --
            # with a KeyError dressed up as the gate.
            name = parametros["name"].strip()
            commands_n = None
            info = self.remote_devices_from_json(parametros["config_json"])
            if info.get("ok"):
                objetivo = parametros.get("device")
                for it in info.get("items") or []:
                    if objetivo is None or it.get("name") == objetivo:
                        commands_n = it.get("commands")
                        break
            # THE KEYS IT IS GOING TO BRING BOUND. It is said right in the label,
            # which is the line the Sync list shows: the user has to be able to
            # read "and it is also going to bind 32 keys for you" BEFORE
            # syncing, not find it out afterwards pressing keys on the remote.
            plan = self._plantilla_de_json(
                parametros["config_json"], parametros.get("device")
            )
            return "Add '%s'%s%s" % (
                name,
                " (%d commands)" % commands_n if commands_n is not None else "",
                self._frase_plantilla(plan),
            )
        if kind == "remove_device":
            k1 = parametros.get("k1")
            listado = self.remote_list_devices()
            if listado.get("ok"):
                d = next(
                    (x for x in listado["devices"] if x.get("k1") == k1), None
                )
                if d:
                    return "Remove '%s' (%d commands)" % (
                        d.get("name"),
                        d.get("commands") or 0,
                    )
            return "Remove device #%s" % k1
        if kind == "edit_activity":
            ordinal = parametros.get("ordinal")
            accion = parametros.get("accion")
            name = None
            listado = self.activities_list()
            if listado.get("ok"):
                a = next(
                    (x for x in listado["activities"] if x.get("ordinal") == ordinal),
                    None,
                )
                name = a.get("name") if a else None
            verbos = {
                "remove_set": "Turn an option off in",
                "add_set": "Turn an option on in",
                "change_value": "Change a value in",
                "renombrar": "Rename",
                "erase": "Delete",
            }
            return "%s '%s'" % (
                verbos.get(accion, accion),
                name or ("activity #%s" % ordinal),
            )
        if kind == "reassign_key":
            try:
                return "Reassign key %#04x" % int(parametros.get("codigo", 0))
            except Exception:  # noqa: BLE001
                return "Reassign a key"
        return str(kind)

    def changes_add(
        self, kind: str, parametros: dict, label: str | None = None
    ) -> dict:
        """Adds ONE change to the session's list. Applies nothing, does not
        touch the device, does not run the gate -- that is
        `sync_preparar()` / `sync_apply_start()`, over the WHOLE list.

        `kind` has to be in `changes.TIPOS`: the UI cannot invent a fifth
        one. `label`, if the calling screen already computed it (e.g.
        Keys, which already knows the name of the chosen command), is used
        as is; if not, it is worked out with `_change_label()`.
        """
        if self._changes is None:
            return _err("changes.py does not import: %s" % FALTA.get("changes"))
        parametros = dict(parametros or {})
        # THE KEY CONTRACT, demanded BEFORE anything else. What
        # each `changes._paso_*()` is going to read is declared in
        # `changes.REQUISITOS`, not hidden inside the body of the step, and it is
        # checked HERE -- the only moment when the error can still be
        # explained in terms of what the user just did.
        #
        # Before, this did not exist and the guard below was
        # `if kind == "add_device" and parametros.get("config_json")`:
        # a change WITHOUT `config_json` never entered the `if`, skipped the whole
        # validation, got onto the list with the label "Add '(unnamed)'" and
        # only blew up at Sync -- with a `KeyError` that the screen
        # presented as if the check had protected the user.
        if changes is not None:
            missing = changes.parametros_faltantes(kind, parametros)
            if missing:
                return _err(
                    "this change is missing %s. Nothing was queued."
                    % " and ".join(FALTA_HUMANA.get(k, "'%s'" % k) for k in missing),
                    missing=missing,
                    category=changes.CATEGORY_APP,
                )
        # THE usual TRAP (see `_path()`, above): the subprocesses of
        # `config_work/` run with `cwd=config_work`, so a relative path
        # exactly as it arrives from the UI would point somewhere else over
        # there. It is normalised to absolute HERE, once, when adding --
        # no en `changes.py` (que no conoce la convencion de paths de esta
        # convention) and not at every later use.
        if kind == "add_device":
            try:
                parametros["config_json"] = str(_path(parametros["config_json"]))
            except Exception as exc:  # noqa: BLE001
                return _err("invalid config_json: %s" % exc)
            # Which of the JSON's devices. The Control screen always
            # sends one (its button is not drawn without `DISPOSITIVO_ELEGIDO`),
            # pero si llegara vacio NO se puede dejar pasar: `add_device.py`
            # tiene `--device` con default "Philips TV", asi que
            # a None does not error out -- it builds ANOTHER device, or fails with a
            # "'Philips TV' not found" que no dice nada del problema
            # problem. It is resolved here, once, at queueing time.
            if not parametros.get("device"):
                info = self.remote_devices_from_json(parametros["config_json"])
                items = info.get("items") or []
                if not info.get("ok") or not items:
                    return _err(
                        "could not read which devices that file contains: %s"
                        % info.get("error", "no items")
                    )
                if len(items) > 1:
                    return _err(
                        "that file contains %d devices: you have to say which one "
                        "(%s)"
                        % (len(items), ", ".join(str(i["name"]) for i in items))
                    )
                parametros["device"] = items[0]["name"]
        if not label:
            try:
                label = self._change_label(kind, parametros)
            except Exception as exc:  # noqa: BLE001
                label = "%s (could not be described: %s)" % (kind, exc)
        try:
            c = self._changes.add(kind, label, parametros)
        except ValueError as exc:
            return _err(str(exc))
        return _ok(change=c.to_dict(), count=len(self._changes))

    def changes_remove(self, id: str) -> dict:
        """Removes ONE change from the list, by id. Does not affect the others."""
        if self._changes is None:
            return _err("changes.py does not import: %s" % FALTA.get("changes"))
        if not self._changes.remove(id):
            return _err("there is no waiting change with that id: %r" % id)
        return _ok(count=len(self._changes))

    def changes_clear(self) -> dict:
        """Discards ALL the session's pending changes."""
        if self._changes is None:
            return _err("changes.py does not import: %s" % FALTA.get("changes"))
        self._changes.vaciar()
        return _ok(count=0)

    def changes_summary(self) -> dict:
        """(c) from the brief: a plain-language description of what is going to
        change, to show BEFORE writing."""
        if summary is None:
            return _err("summary.py does not import: %s" % FALTA.get("summary"))
        if self._changes is None:
            return _err("changes.py does not import: %s" % FALTA.get("changes"))
        return _ok(**summary.summarize_changes(self._changes.listar()))

    def sync_preparar(self) -> dict:
        """Generates the combined blob with ALL the pending changes, chained,
        and runs the gate ONE single time -- without writing the device. For
        the whole list, what `remote_apply`/`remote_delete`/
        `activity_prepare`/`keys_apply` already do each on their own for ONE
        change at a time (see `changes.apply_all()`).
        """
        if changes is None or generate is None:
            return _err(
                "changes.py/generate.py does not import: %s"
                % (FALTA.get("changes") or FALTA.get("generate"))
            )
        if self._changes is None or not len(self._changes):
            return _err(LINEA_SYNC_SIN_CAMBIOS, linea=LINEA_SYNC_SIN_CAMBIOS)
        pending = len(self._changes)
        ref = self._remote_blob()
        blob = Path(ref["blob"])
        if not blob.exists():
            return _err("the reference file does not exist: %s" % blob, referencia=ref)
        plantilla = BACKUPS / "one_20260724_210614_a.EZHex"
        r = changes.apply_all(
            self._changes.listar(),
            blob,
            SALIDA,
            plantilla=plantilla,
            generate=generate,
            device_module=dispositivo_mod,
            keys_map=keys_map,
            keys_physical=keys_physical,
        )
        if not r.get("ok"):
            # THE DISTINCTION THAT WAS MISSING. This used to always return
            # `LINEA_SYNC_NO_PASO` ("the check did not pass"), both when the
            # gate had rejected and when the app had broken on its own --
            # and an app that claims a protection it never exercised teaches you
            # to ignore the warning the day it does protect. `category` is decided by
            # `changes.py`; aca solo se traduce a la linea que corresponde.
            category = r.get("category") or changes.CATEGORY_APP
            return _err(
                r.get("error") or "one of the waiting changes could not be prepared",
                referencia=ref,
                fallo_en=r.get("fallo_en"),
                steps=r.get("steps"),
                key_template=r.get("key_template") or [],
                category=category,
                es_bug=category == changes.CATEGORY_APP,
                linea=LINEA_POR_CLASE.get(category, LINEA_SYNC_NO_PASO),
                # The technical detail goes separately, so the screen
                # can hide it behind "See more" instead of dumping it
                # on somebody who only wanted to add a TV set.
                technical_detail=_failed_step_detail(r),
            )
        gate_passed = bool((r.get("gate") or {}).get("ok"))
        self._last_generation = r
        self._last_gate = r.get("gate") if gate_passed else None
        file = r.get("ezhex_final") or r.get("blob_final")
        # The by-hand write command. It is NO longer the normal case (the default
        # is now that the app can write after the explicit
        # confirmation), but it is still always built: it is useful with
        # `RE_HARMONY_SOLO_LECTURA=1`, y sirve a quien prefiera correrlo a
        # hand just as `remote_apply`/`activity_prepare`/
        # `keys_apply` do for their one loose change. It is only built if the
        # gate came back green: no green, no command to offer (RULE 1, for the
        # text too).
        cmd = None
        if gate_passed and file:
            c = self.remote_record_command(
                file, ref["blob"], r.get("repoints_int") or []
            )
            cmd = c if c.get("ok") else None
        return _ok(
            ready=gate_passed,
            steps=r.get("steps"),
            # WHAT GOT BOUND ON ITS OWN. One entry per device added in
            # this batch: how many keys ended up bound and WHICH ones did not, with
            # the measured reason (the device does not bring that command). It goes
            # apart from `steps` because it is not technical detail: it is what the
            # user has to read before saying yes.
            key_template=r.get("key_template") or [],
            file=file,
            blob_final=r.get("blob_final"),
            referencia=ref,
            repuntes=[_hex(p) for p in r.get("repoints_int") or []],
            repoints_int=r.get("repoints_int") or [],
            gate=r.get("gate"),
            command=cmd,
            # The steps ran; if something said no, it was THE GATE --
            # this one really is the protection working, and here `category` says it
            # explicitly so the screen does not have to deduce it from the
            # ausencia de `ready`.
            category=None if gate_passed else changes.CATEGORY_GATE,
            es_bug=False,
            # ONE status line, already resolved here. The UI shows it as
            # is: it does not have to word anything or chain paragraphs.
            count=pending,
            linea=(
                LINEA_SYNC_VERIFICADO
                if gate_passed
                else (
                    LINEA_SYNC_NO_PASO if file else sync_preparing_line(pending)
                )
            ),
            linea_preparando=sync_preparing_line(pending),
            solo_lectura=SOLO_LECTURA,
        )

    def sync_apply_start(self, ack: str = "") -> dict:
        """EL boton de Sync de verdad: arranca la escritura EN UN HILO
        APARTE y devuelve un `trabajo_id` para seguirla con
        `sync_progreso()` -- en vez de bloquear hasta el final como
        `remote_record()`.

        LAS DOS LLAVES REALES, las dos verificadas ACA en Python y
        ninguna delegada al DOM:

          1. `ack == 'GRABAR'` -- la confirmacion explicita. Es el boton
             rojo de la pantalla, el click consciente. YA SE PROBO que una
             regla que vive solo en el DOM no alcanza, asi que este
             chequeo se queda en Python pase lo que pase.
          2. La compuerta -- recalculada ACA, corriendo `sync_preparar()`
             de nuevo, nunca confiando en lo que la UI mando de vuelta.

        La tercera llave vieja (`RE_HARMONY_PERMITIR_GRABADO=1` al
        arrancar) SE FUE: era un cinturon sobre un cinturon de cuando
        nada estaba verificado, y era el obstaculo principal -- la app
        decia "verificado y listo" y despues pedia reiniciar con una
        variable de entorno. Queda el interruptor inverso,
        `RE_HARMONY_SOLO_LECTURA=1`, para quien quiera la app clavada en
        solo lectura.

        La diferencia con `remote_record()` es SOLO el mecanismo: en vez
        de `subprocess.run` (que no deja ver nada hasta que termina), usa
        `progreso.ejecutar_en_vivo()`, que reporta cada linea de
        `write.py` a medida que sale (ver `progress.py`).
        """
        if not PERMITIR_GRABADO:
            return _err(TEXTO_GRABADO_APAGADO, apagado=True, solo_lectura=True)
        if ack != "GRABAR":
            return _err(
                "the explicit confirmation is missing: nothing was written",
                falta_confirmacion=True,
            )
        if progress is None or remote is None:
            return _err(
                "progress.py/remote.py does not import: %s"
                % (FALTA.get("progress") or FALTA.get("remote"))
            )
        prep = self.sync_preparar()
        if not prep.get("ok") or not prep.get("ready"):
            return _err(
                LINEA_SYNC_NO_PASO,
                preparado=prep,
                linea=LINEA_SYNC_NO_PASO,
            )
        ezhex = prep["file"]
        referencia = prep["referencia"]["blob"]
        repuntes = prep["repoints_int"]
        cmd = self.remote_record_command(ezhex, referencia, repuntes)
        if not cmd.get("ok"):
            return cmd
        if not self._lock_aparato.acquire(blocking=False):
            return _err("there is already an operation in progress with the device")

        trabajo = progress.TrabajoGrabado()
        self._trabajos_grabado[trabajo.id] = trabajo
        applied_changes = (
            [c.to_dict() for c in self._changes.listar()] if self._changes else []
        )

        def _run() -> None:
            """El hilo del grabado. Su contrato, en orden y sin excepciones:

              1. corre `write.py` (`ejecutar_en_vivo` ya no lanza por
                 culpa del proceso: timeout y muerte fea vuelven como
                 `ok=False` + `error`),
              2. ANOTA EN EL HISTORIAL -- pase lo que pase, incluso si el
                 grabado fallo o el hilo exploto. Lo que se anota es que
                 SE ESCRIBIO, y eso ocurrio igual,
              3. recien entonces `marcar_fin`, en un `finally`, para que
                 la pantalla cambie SI O SI.

            El orden 2-antes-de-3 no es estetico: la UI dispara la
            pregunta de arranque (REGLA 2) en cuanto ve `terminado`, y sin
            `write_id` puesto no tendria donde guardar la respuesta. Si
            el usuario cierra la ventana justo ahi, la fila del historial
            ya existe -- que es lo que la ultima vez NO paso.
            """
            res: dict = {}
            ok = False
            returncode: int | None = None
            error: str | None = None
            try:
                res = progress.ejecutar_en_vivo(
                    cmd["argv"], CONFIG_WORK, trabajo.add
                )
                ok = bool(res.get("ok"))
                returncode = res.get("returncode")
                error = res.get("error")
            except Exception as exc:  # noqa: BLE001
                error = _motivo(exc)
            finally:
                try:
                    trabajo.write_id = self._annotate(
                        ezhex,
                        referencia,
                        {
                            "passed": True,
                            "repoints_int": repuntes,
                            "salida_cruda": (res.get("transcript") or "")[-4000:],
                        },
                        returncode if returncode is not None else -1,
                        "aplicado via Sync (%d cambio(s))%s"
                        % (
                            len(applied_changes),
                            "" if ok else " -- NO TERMINO BIEN: %s" % (error or "?"),
                        ),
                        "; ".join(c.get("label") or "" for c in applied_changes)[
                            :200
                        ]
                        or None,
                        None,
                    )
                except Exception as exc:  # noqa: BLE001
                    # No longer swallowed in silence: it travels to the snapshot so
                    # that the screen can say "this was written and it did
                    # not get logged", instead of lying by omission.
                    trabajo.write_entry_error = _motivo(exc)
                # The change queue is emptied ONLY if the write finished
                # fine. If it failed, the changes stay pending: they are the
                # user's work and there is no reason to throw it away.
                if ok and self._changes is not None:
                    try:
                        self._changes.vaciar()
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    self._lock_aparato.release()
                except Exception:  # noqa: BLE001
                    pass
                # LAST, always: the end of the process is the hard signal.
                trabajo.marcar_fin(ok=ok, returncode=returncode, error=error)

        threading.Thread(target=_run, daemon=True).start()
        # What was just written, so it can be verified byte for byte
        # afterwards (`sync_verificar_grabado()`). It is compared against the
        # `.bin`, NOT against the `.EZHex`: the raw dump of the flash is the
        # blob, and it is the `.bin` that gives the same sha256.
        self._last_written_blob = prep.get("blob_final")
        return _ok(
            trabajo_id=trabajo.id,
            changes=applied_changes,
            linea=LINEA_SYNC_ESCRIBIENDO,
            blob_final=prep.get("blob_final"),
            # RULE 2: after writing, ALWAYS ask whether the remote
            # booted fine. It lives here, in Python, not in the DOM.
            pregunta="Now unplug the remote: did it boot fine?",
        )

    def sync_progreso(self, trabajo_id: str, start: int = 0) -> dict:
        """Polling: new events from index `start`, the overall percentage, and
        whether it is done. See `progreso.TrabajoGrabado.snapshot()`.
        """
        trabajo = self._trabajos_grabado.get(trabajo_id)
        if trabajo is None:
            return _err("no job with that id (did the app restart?)")
        snap = trabajo.snapshot(start)
        # `_ok()` builds `{"ok": True}` and then does `update(kw)`: any
        # `ok`/`error` coming in the snapshot OVERWRITES the call's envelope.
        # That was BUG 1 -- `ok: null` while the write was running, the UI read
        # it as "the bridge went down", cut the polling on the first cycle
        # and stayed on "Writing to your remote" forever. It is popped out here
        # too, on top of in `progreso.snapshot()`, because this is the
        # only point where the dict crosses over to the JS.
        snap.pop("ok", None)
        snap.pop("error", None)
        return _ok(**snap)

    def sync_verificar_grabado(self, file: str = "") -> dict:
        """EL CIERRE DE LAZO DE VERDAD, byte a byte. OPCIONAL.

        Durante mucho tiempo esto no se podia: `read_config.py` devuelve
        `is_config_dump_supported() = NO` y lo unico comparable era el
        TAMANO. Ya no es cierto. `config_work/read_flash_baseline.py`
        (lo que corre `estado_mando.refrescar()`) saca el volcado crudo
        del flash, y ese volcado da el MISMO sha256 que el `.bin` que
        genera esta app -- medido sobre
        `output/config_empaquetada.bin` ->
        `0ba5745918d58fc08a1ba4bd3ebff6cc3e36d008c102dda0769e704690a8adae`.

        Es una LECTURA: no escribe, no borra, no toca la config. Tarda
        ~136 s (el timeout es 300 s y no se baja: ver
        `estado_mando.refrescar`). Por eso se ofrece como paso aparte,
        con el tiempo dicho de antemano, y nunca se dispara sola.

        `file`: el `.bin` a comparar. Si no se pasa, el ultimo que
        escribio `sync_apply_start()`.
        """
        if remote_status is None:
            return _err(
                "remote_status.py does not import: %s" % FALTA.get("remote_status")
            )
        objetivo = (file or "").strip() or self._last_written_blob
        if not objetivo:
            return _err(
                "there is no file to compare against: nothing has been written "
                "from this session yet"
            )
        path = _path(objetivo)
        if not path.exists():
            return _err("%s does not exist" % path)
        esperado = _sha256(path)
        if not self._lock_aparato.acquire(blocking=False):
            return _err("there is already an operation in progress with the device")
        try:
            # 300 s of timeout, exactly as `estado_mando` defines it: the
            # real read takes ~136 s and lowering it only produces false
            # "no contesto".
            r = remote_status.refrescar(self._datos)
        finally:
            self._lock_aparato.release()

        if r["state"] != remote_status.CONECTADO_VERDAD:
            return _err(
                r.get("mensaje") or "the remote's flash could not be read back",
                state=r["state"],
                reason=r.get("reason"),
                esperado_sha256=esperado,
                file=str(path),
            )
        # The deliberate side effect of `remote_real_state()` holds
        # here too: if the truth was read, that becomes the reference.
        self._verdad_actual = r
        leido = r.get("sha256")
        coincide = bool(leido) and leido == esperado
        return _ok(
            coincide=coincide,
            byte_a_byte=True,
            file=str(path),
            esperado_sha256=esperado,
            leido_sha256=leido,
            volcado=r.get("blob"),
            identidad=r.get("identidad"),
            measured_at=r.get("measured_at"),
            linea=(
                "Verified byte for byte: what's on the remote is exactly the "
                "file that was written."
                if coincide
                else "The remote's flash does NOT match the file that was "
                "written: the sha256 is different."
            ),
        )

    # ==================================================================
    # startup
    # ==================================================================
    def ping(self) -> dict:
        """CHECK: confirms the JS <-> Python bridge is alive."""
        return _ok(pong=True, hora=time.strftime("%H:%M:%S"))

    def status(self) -> dict:
        """Everything the UI needs to paint itself the first time."""
        return _ok(
            raiz=str(RAIZ),
            datos=str(self._datos),
            python=sys.version.split()[0],
            plataforma=platform.system(),
            absent=FALTA,
            # `permitir_grabado` still exists under the same name (the
            # UI reads it that way) but it is now True by default: it is only turned
            # con `RE_HARMONY_SOLO_LECTURA=1`. La proteccion pasa a ser la
            # confirmacion explicita (`ack == "GRABAR"`) + la compuerta.
            permitir_grabado=PERMITIR_GRABADO,
            solo_lectura=SOLO_LECTURA,
            hay_cuenta=session is not None,
            hay_catalogo=catalog is not None,
            ancla_md5=ANCLA_MD5,
            defaults={
                "blob": str(BACKUPS / "config_raw.bin"),
                "plantilla": str(BACKUPS / "one_20260724_210614_a.EZHex"),
                "salida_dir": str(SALIDA),
                "repuntes": [_hex(p) for p in ANCLA_REPUNTES],
            },
            textos={
                "loop_closure": TEXTO_CIERRE_DE_LAZO,
                "size_only": TEXTO_SOLO_TAMANO,
                "grabado_apagado": TEXTO_GRABADO_APAGADO,
                # ONE line per Sync state. The UI picks one, it does not
                # chain them. `sync_preparando` is built with the count
                # (see `sync_preparing_line()`); `sync_preparar()` already
                # devuelve resuelta en su clave `line`.
                "sync_no_changes": LINEA_SYNC_SIN_CAMBIOS,
                "sync_verified": LINEA_SYNC_VERIFICADO,
                "sync_not_passed": LINEA_SYNC_NO_PASO,
                "sync_escribiendo": LINEA_SYNC_ESCRIBIENDO,
                # La linea del boton Connect mientras la lectura corre.
                "conectando": TEXTO_CONECTANDO,
                # The THREE failure classes, each with its own line. They live in
                # Python (like the rest of the obligatory texts): the
                # screen cannot turn an app bug into "the check
                # protected you" by touching only the HTML.
                # `sync_preparar()` devuelve `category` + `line` ya resueltas;
                # this is so the UI can paint them before calling.
                "kind_gate": LINEA_POR_CLASE["gate"],
                "kind_tool": LINEA_POR_CLASE["herramienta"],
                "kind_app": LINEA_POR_CLASE["aplicacion"],
                "kind_app_detail": (
                    "The app hit an error of its own while preparing the "
                    "changes. Nothing was written and your remote was never "
                    "touched. The technical detail is under 'See more'."
                ),
                # THE SAME CLASS, BUT WHILE QUEUEING. The one above says "while
                # preparing the changes" and the screen closed it with "your
                # changes are still on the Sync list" -- both LIES on the
                # queueing path, where the change was rejected and did NOT
                # get onto the list. One text per moment, none recycled.
                "kind_app_detail_queue": (
                    "The app hit an error of its own while adding this change "
                    "to the Sync list. Nothing was queued and your remote was "
                    "never touched. The technical detail is under 'See more'."
                ),
                "sync_confirmar": (
                    "This writes your remote's memory. It cannot be undone from here."
                ),
                "verificacion_opcional": (
                    "Optional: read the remote back and compare sha256 "
                    "byte for byte (~136 s, changes nothing)."
                ),
            },
        )

    # ==================================================================
    # ACCOUNT
    # ==================================================================
    def account_status(self, read_keychain: bool = False) -> dict:
        """What's saved, without hitting the network and without showing any
        token.

        This is a normal Logitech account login, to read the public
        catalog. This layer reads neither `~/.harmony_api_token.json` nor
        any `remoteId`/`skinId`/`hubSecret`: those were only needed for the
        device sign-up-and-remove flow, which was taken out.

        ## `read_keychain` defaults to FALSE, and that is the whole point

        On macOS, `keyring.get_password()` makes the OS pop up a dialog
        asking for permission to read the keychain. This method used to do it
        unconditionally, and the UI called it on the FIRST PAINT
        (`app.js`, `cargarCuenta()`), with Account as the landing tab. So
        every single launch asked for a credential before the user had
        touched anything -- and it asked TWICE, because `SERVICIO_LLAVERO`
        and `sesion.SERVICE_NAME` are two different keychain items.

        An app that asks for a credential to draw its first screen trains
        people to click Allow without reading. The keychain is now only read
        when the user asks for it in so many words, and every caller that
        genuinely needs it (`account_login`, `account_renew`,
        `account_forget`) already asks on its own behalf, after a click.
        """
        if session is None:
            return _err(
                "the session module does not import (in practice, `keyring` "
                "is not installed -- `cd app && uv sync`)",
                detail=FALTA.get("session"),
            )
        if not read_keychain:
            return _ok(
                email=None,
                hay_password=False,
                keychain_read=False,
                token_lip=session.DEFAULT_TOKEN_FILE.exists(),
            )
        recordado = None
        try:
            import keyring

            recordado = keyring.get_password(SERVICIO_LLAVERO, "__ultimo_email__")
        except Exception as exc:  # noqa: BLE001
            return _err("could not read the keychain: %s" % exc)
        has_password = False
        if recordado:
            try:
                has_password = session.load_password(recordado) is not None
            except Exception:  # noqa: BLE001
                has_password = False
        return _ok(
            email=recordado,
            hay_password=has_password,
            keychain_read=True,
            token_lip=session.DEFAULT_TOKEN_FILE.exists(),
        )

    def account_login(self, email: str, password: str, recordar: bool = True) -> dict:
        """LIP login against accounts.logi.com. The password goes to the OS
        keychain (never to a file in the project) and only if `recordar`."""
        if session is None:
            return _err(
                "the session module does not import", detail=FALTA.get("session")
            )
        email = (email or "").strip()
        if not email or not password:
            return _err("email and password are needed")
        try:
            session.login(email, password, remember=bool(recordar))
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "login rejected")
        try:
            import keyring

            keyring.set_password(SERVICIO_LLAVERO, "__ultimo_email__", email)
        except Exception:  # noqa: BLE001
            pass
        return _ok(email=email, recordada=bool(recordar))

    def account_renew(self, email: str) -> dict:
        """`sesion.ensure_session`'s renewal cascade (signin -> refresh
        -> re-login with the keychain's password).

        The same one call the Catalog buttons make. There is no second
        "just the tokens" entry point any more: one per app, one type."""
        if session is None:
            return _err(
                "the session module does not import", detail=FALTA.get("session")
            )
        try:
            session.ensure_session((email or "").strip())
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc)
        return _ok(email=email)

    def account_forget(self, email: str) -> dict:
        """Deletes the password from the keychain. Does not touch the tokens
        or the account."""
        if session is None:
            return _err(
                "the session module does not import", detail=FALTA.get("session")
            )
        try:
            session.forget_password((email or "").strip())
            import keyring

            try:
                keyring.delete_password(SERVICIO_LLAVERO, "__ultimo_email__")
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            return _err("could not delete from the keychain: %s" % exc)
        return _ok()

    # ==================================================================
    # CATALOG
    # ==================================================================
    def catalog_local(self) -> dict:
        """The devices already ready in `account_export/output/`, with their
        protocols. Does not touch the network or any account.

        Listed by the configuration FILE, not by the folder prefix
        (`biblioteca.disk_configs()`): so old folders and the ones the
        app writes today (`device-*`) both show up the same way.

        The command count comes from `catalogo.read_local_export()`, which
        counts it again from `resources.DeviceList` instead of trusting the
        `manifest.validation.commands` that was written by the same code
        that built the file (that would be a circular check).
        """
        items = []
        rutas = (
            library.disk_configs()
            if library is not None
            else sorted(OUTPUT_BRIDGE.glob("*/hub-config-with-device.json"))
        )
        for jsn in rutas:
            d = jsn.parent
            man = d / "manifest.json"
            if not man.exists():
                continue
            try:
                manifest = json.loads(man.read_text())
            except Exception:  # noqa: BLE001
                continue
            pedido = manifest.get("requested_device") or {}
            origin = (
                library.origin_of(d)
                if library is not None
                else ("manual" if "manual" in d.name else "capturado")
            )
            item = {
                "dir": d.name,
                # THE NAME IS `config_json`, and it is the SAME name in the
                # five places this path goes through:
                # `catalog_local()` (here) -> `changes_add()` ->
                # `changes.REQUISITOS["add_device"]` ->
                # `changes._step_add_device()` ->
                # `remote_generate()`. It came out as `json` from here and
                # here only, and that one name disagreement is the WHOLE bug: whoever
                # queued the item as it came left a change without
                # `config_json`, which blew up with a `KeyError` three layers
                # later -- inside the gate's path, where the
                # screen showed it as "the check did not pass".
                "config_json": str(jsn),
                # ALIAS ON ITS WAY OUT. `app/ui/app.js` still reads `i.json` in
                # four places (Control's <select>, "Use in Control" and
                # the two `find`s that go with it) and this session cannot
                # touch `app/ui/`. It goes away as soon as those four lines move to
                # `i.config_json`; until then the SAME path counts, never
                # one computed separately -- `app/check_contract.py`
                # checks that they are identical so they cannot diverge.
                "json": str(jsn),
                "fabricante": pedido.get("manufacturer"),
                "modelo": pedido.get("model"),
                "downloaded_at": manifest.get("generated_at"),
                "origin": origin,
                "protocolos": manifest.get("protocolos") or [],
                "commands_manifest": (manifest.get("validation") or {}).get("commands"),
                "commands": None,
                "problema": None,
            }
            if catalog is not None:
                try:
                    res = catalog.read_local_export(d)
                    item["commands"] = res.command_count
                except Exception as exc:  # noqa: BLE001
                    item["problema"] = _motivo(exc)
            items.append(item)
        # THE VERDICT, measured, not deduced. Without this the screen fell back to
        # `protocolos.length > 0`, which is a GOOD APPROXIMATION AND A BAD
        # ANSWER: it painted "Ready" over the one device Sync would then
        # reject (the `.ir` with the 'Vol dn' label, which breaks the
        # glyph table and makes 'Devices' impossible to write). The user
        # found out about the problem three screens later, which is exactly
        # what he asked to stop happening.
        #
        # `diagnose_all()` decides it against the REAL reference blob, which is
        # why it is computed HERE on every listing and not frozen at save time:
        # applicability depends on the blob the remote has today, and that changes.
        # An item saved yesterday can become inapplicable without anyone
        # touching it -- that is literally what happened.
        if library is not None and items:
            try:
                by_dir = {d["dir"]: d for d in library.diagnose_all()}
            except Exception as exc:  # noqa: BLE001
                # The verdict failing can NOT empty out the catalog
                # screen: it degrades to "could not be measured", which is different
                # from "it is ready" and looks different.
                #
                # And it degrades LEAVING THE KEYS IN PLACE. It used to return without
                # publishing `aplicable`, and everything that reads them had to
                # guess whether "not there" meant "old version of the
                # backend" or "could not be measured" -- two different things with the
                # same look. `aplicable: None` is the third answer that was
                # missing: not yes, not no, do not know.
                by_dir = {}
                detail = _motivo(exc)
                for it in items:
                    it["problema"] = it["problema"] or detail
                    it.setdefault("aplicable", None)
                    it.setdefault("missing_protocol", None)
                    it.setdefault("not_applicable_reason", detail)
                    it.setdefault("missing_category", None)
                    it.setdefault("reparable", False)
            for it in items:
                d = by_dir.get(it["dir"])
                if d is None:
                    continue
                it["aplicable"] = d.get("aplicable")
                it["missing_protocol"] = d.get("missing_protocol")
                it["not_applicable_reason"] = d.get("reason")
                it["missing_category"] = d.get("missing_category")
                # The RESOLVED protocols, not the ones the manifest declares:
                # it is the same datum measured the same way for the three
                # save paths, which is what made the same
                # dispositivo bajado dos veces se viera distinto.
                if d.get("protocolos"):
                    it["protocolos"] = d["protocolos"]
                # `reparable` does NOT come from `diagnose()` -- it is derived from the
                # class, and it is derived HERE once so the UI does not have
                # to know which classes `repair()` knows how to fix:
                # "glifos" (renames the ambiguous label) and "protocolo"
                # (brings the definition from the library) yes. "archivo" no:
                # there the file itself is missing, and that gets downloaded again.
                it["reparable"] = d.get("missing_category") in ("glyphs", "protocolo")
        return _ok(items=items)

    def catalog_repair(self, dir: str) -> dict:
        """Deja UN dispositivo guardado en condiciones de usarse, o dice por
        que no se puede.

        Existe porque el mensaje de error ya mandaba aca -- decia "Repairing
        that device in Catalog renames it and makes it usable again" cuando
        no habia ningun Repair en ningun lado. Un remedio que se nombra y no
        existe es el mismo callejon sin salida que era no poder borrar:
        el usuario lee que hay una salida, la busca, y no esta.

        Que arregla `biblioteca.repair()`, de menos a mas invasivo: el
        manifest, las definiciones de protocolo que falten (las trae de la
        biblioteca local), y recien al final la etiqueta ambigua que rompe la
        tabla de glifos. NO toca el mando ni la cuenta de Logitech: es una
        carpeta de `account_export/output/`.
        """
        if library is None:
            return _err("the library module isn't available")
        name = (dir or "").strip()
        if not name:
            return _err("no device was given")
        # The SAME path traversal guard as `catalog_delete`: `dir`
        # is a folder NAME, it is resolved inside OUTPUT_BRIDGE and the result is
        # checked to still be inside. `repair()` writes.
        try:
            target = (OUTPUT_BRIDGE / name).resolve()
            target.relative_to(OUTPUT_BRIDGE.resolve())
        except (ValueError, OSError):
            return _err("that device name isn't valid")
        if not target.is_dir():
            return _err("that device isn't saved on this computer anymore")
        try:
            r = library.repair(target)
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc)

        # CAREFUL WITH THESE TWO KEYS, which are named alike and are NOT the
        # same: the `ok` returned by `biblioteca.repair()` means "it ended up
        # applicable", and the `ok` of the `_ok()/_err()` envelope means "the call
        # ran". Mixing them up is the same mistake that once hung the whole Sync
        # (`_ok(**snapshot)` overwriting the snapshot's `ok`), and it has already
        # bitten once here: reading `r["aplicable"]` -- which `repair()` does NOT
        # publish, the verdict lives in `r["diagnostico"]` -- gave `None` and said
        # "it still can't be added" about a repair that had actually
        # worked. That is why the verdict is taken from the diagnosis, which is
        # quien lo midio.
        diag = r.get("diagnostico") or {}
        quedo_aplicable = bool(diag.get("aplicable", r.get("ok")))
        hechos = r.get("changes") or []
        if quedo_aplicable:
            return _ok(
                aplicable=True,
                changes=hechos,
                mensaje=(
                    "%s is ready to add to your remote now. Nothing was "
                    "written to your remote or your Logitech account." % name
                ),
            )
        # Could not be done: say WHAT is missing, not "it failed".
        return _ok(
            aplicable=False,
            changes=hechos,
            mensaje=diag.get("reason")
            or "it still can't be added, reason not measured",
        )

    def catalog_delete(self, dir: str) -> dict:
        """Borra UNA carpeta del catalogo LOCAL (lo que el brief pide:
        "en Catalogo no puedo eliminar dispositivos").

        QUE BORRA Y QUE NO -- la distincion importa y la UI la repite:
        esto borra una carpeta de `account_export/output/`, o sea un
        dispositivo YA DESCARGADO/IMPORTADO que todavia no se le cargo al
        mando. NO toca el control (no hay escritura de flash en ningun
        camino de este metodo), y NO toca la cuenta de Logitech (el
        catalogo publico es de solo lectura: lo que se borra es la copia
        local, se puede volver a bajar). Para sacar un dispositivo que YA
        esta EN el mando el camino es otro y sigue siendo el de siempre:
        `remote_delete()` -> compuerta -> escritura.

        Sobre la consigna "NO modifiques account_export/": se respeta. No se
        toca una sola linea de codigo de ese subsistema; `output/` es el
        directorio de DATOS que la propia app ya crea y llena
        (`catalog_save`, `catalog_ir_import`, `aprender`). Borrar una
        carpeta de exportacion es la operacion inversa de las que ya
        existen, no una modificacion del puente.

        Guarda contra path traversal: `dir` es un NOMBRE de carpeta, se
        resuelve dentro de `OUTPUT_BRIDGE` y se verifica que el resultado
        siga estando adentro antes de borrar nada.
        """
        name = (dir or "").strip()
        if not name:
            return _err("which folder to delete is missing")
        if "/" in name or "\\" in name or name in (".", ".."):
            return _err("invalid folder name: %r" % name)
        base = OUTPUT_BRIDGE.resolve()
        try:
            target = (base / name).resolve()
        except OSError as exc:
            return _err("could not resolve the folder: %s" % exc)
        # The real check: after resolving symlinks, it still has to
        # hang off output/. A `dir` with ".." or a symlink pointing
        # outside dies here, not in the rmtree.
        if target == base or base not in target.parents:
            return _err("that folder is not inside the local catalog")
        if not target.is_dir():
            return _err("no such folder in the local catalog: %s" % name)
        # Something queued for Sync cannot be deleted: the pending change
        # points at a `config_json` that would stop existing and the write
        # would fail halfway through. It is checked in Python, not
        # only in the JS, because of THE usual TRAP.
        # THE BLOCKERS COME BACK WITH THEIR `id`, NOT JUST NARRATED.
        #
        # The message said "Take it off the Sync list first" and stopped there.
        # Combined with a device Sync can NOT apply (the one missing the
        # protocol's timing definition), that is a trap with no way
        # out: it cannot be applied and it cannot be deleted. The screen
        # needs the `id` to be able to offer the way out right where the
        # user is standing, so it goes in `bloqueado_por`.
        #
        # THE KEY CONTRACT: `bloqueado_por` is a LIST of dicts with
        # exactly `id` and `label` -- the two names already used by
        # `Change.to_dict()` and that `changes_remove(id)` receives. `app/ui/app.js`
        # (`borrarLocal()`) reads those two and no others.
        bloqueos: list[dict] = []
        if self._changes is not None:
            for c in self._changes.listar():
                cj = (c.parametros or {}).get("config_json")
                if not cj:
                    continue
                try:
                    if target in Path(cj).resolve().parents:
                        bloqueos.append({"id": c.id, "label": c.label})
                except OSError:
                    continue
        if bloqueos:
            return _err(
                "can't delete it: %s waiting in Sync %s this device (%s). Take it "
                "off the Sync list first."
                % (
                    "a change" if len(bloqueos) == 1 else "%d changes" % len(bloqueos),
                    "uses" if len(bloqueos) == 1 else "use",
                    ", ".join(str(b["label"]) for b in bloqueos),
                ),
                bloqueado_por=bloqueos,
            )
        try:
            shutil.rmtree(target)
        except OSError as exc:
            return _err("could not delete it: %s" % exc)
        return _ok(borrado=name)

    # ==================================================================
    # KEYS -- the map of what each key does, and how to change it
    # ==================================================================
    def _hub_for_names(self) -> list[str]:
        """ALL the `hub-config-*.json` files on disk, whatever there is.

        No single one is chosen as "the most complete": no single capture
        brings both the Philips and the LG at once (tested: the LG's does
        not have the Philips, and using only that one made the Philips's 32
        commands show up as "unnamed command"). They are all passed in and
        `keys_map._list_from_json` decides with a hard check -- the list
        has to measure exactly what section [5] declares for that device,
        and if there are several, they have to be identical.
        """
        return [
            str(p) for p in sorted(OUTPUT_BRIDGE.glob("*/hub-config-with-device.json"))
        ]

    def keys_model(self, refrescar: bool = False) -> dict:
        """The full key map of the blob that represents the remote today.

        The expensive part (disassembling ~950 slots and extending the
        glyph table) is cached by `(path, mtime)`. `refrescar=True` throws
        it away.

        Does NOT touch the device, writes nothing, and imports `write.py`
        only for `nothing_moved`, which is pure.
        """
        if keys_map is None:
            return _err("keys_map.py does not import: %s" % FALTA.get("keys_map"))
        blob, ref, absent = self._blob_de_referencia()
        if absent is not None:
            return absent
        clave = (str(blob), blob.stat().st_mtime_ns)
        if refrescar or getattr(self, "_keys_cache_key", None) != clave:
            datos = blob.read_bytes()
            hub = self._hub_for_names()
            try:
                modelo = keys_map.read(datos, hub)
            except Exception as exc:  # noqa: BLE001
                return _err("could not read the key map: %s" % exc)
            # The photo + the code<->command join. If it fails, the screen
            # keeps working with the LCD zones and says so: no map gets
            # invented.
            foto = None
            foto_error = None
            if keys_photo is None:
                foto_error = FALTA.get("keys_photo", "keys_photo.py does not import")
            else:
                try:
                    foto = keys_photo.modelo(datos, hub)
                except Exception as exc:  # noqa: BLE001
                    foto_error = _motivo(exc)
            # THE SECOND SITE. A rubber key can be bound in two different
            # places and they are not interchangeable, which is exactly what
            # grabada #7 cost: the keyboard CONTEXT (`[10][n]`, in force only
            # while that Activity is running) and the DEVICE'S OWN PAGE (the
            # `table[6]` trailer's header, in force while you are standing on
            # that device in Devices). The screen has to be able to offer the
            # second one, so the model carries it.
            pages = None
            pages_error = None
            if keys_physical is None:
                pages_error = FALTA.get(
                    "keys_physical", "keys_physical.py does not import"
                )
            else:
                try:
                    pages = keys_physical.map_devices(datos, hub)
                except Exception as exc:  # noqa: BLE001
                    pages_error = _motivo(exc)
            # THE FACTORY TEMPLATE, per device, computed off the SAME model
            # and pages that were just built (no second disassembly). This
            # is what lets the screen say, without the user pressing
            # anything, how many keys that device has bound and which ones
            # it can't -- and it is the same plan the "bind them" button
            # queues. One call point: `key_template.device_plan`.
            self._keys_cache_key = clave
            self._keys_cache = modelo
            self._keys_photo = foto
            self._keys_photo_error = foto_error
            self._keys_pages = pages
            self._keys_pages_error = pages_error
            self._keys_template = self._pages_template(
                datos, modelo, pages, hub
            )
        # HOW MANY OF THOSE ARE ALREADY WAITING IN SYNC. Computed OUTSIDE the
        # cache: the cache is keyed by the blob, and the Sync list changes
        # without the blob changing. Without this the card kept offering "bind
        # 31" with all 31 already queued, and the second press queued zero without
        # explaining why.
        plan_with_queue = []
        for p in getattr(self, "_keys_template", None) or []:
            enc = self._keys_pending_on(p.get("screen"))
            plan_with_queue.append(
                dict(
                    p,
                    n_en_sync=sum(
                        1 for c in p.get("changes") or [] if c["codigo"] in enc
                    ),
                )
            )
        return _ok(
            referencia=ref,
            modelo=self._keys_cache,
            foto=getattr(self, "_keys_photo", None),
            foto_error=getattr(self, "_keys_photo_error", None),
            paginas_dispositivo=getattr(self, "_keys_pages", None),
            paginas_error=getattr(self, "_keys_pages_error", None),
            plantilla=plan_with_queue,
            plantilla_error=(
                None
                if key_template is not None
                else FALTA.get(
                    "key_template", "key_template.py does not import"
                )
            ),
            editables=list(keys_map.SCREEN_CODES),
            aviso_no_editables=TEXTO_TECLAS_NO_EDITABLES,
            aviso_sitio=TEXTO_TECLAS_SITIO,
        )

    # ------------------------------------------------------------------
    # THE FACTORY TEMPLATE -- the app's single call point into the planner
    # ------------------------------------------------------------------
    def _pages_template(self, datos, modelo, pages, hub) -> list[dict]:
        """One plan per device that HAS a page of its own. Read only.

        Devices without a reachable page (`device_screen` doesn't
        resolve them) are not in `pages` and therefore not here: there is
        no header to write into, and inventing an entry would promise a
        button that can't do anything.
        """
        if key_template is None or not pages:
            return []
        outside = []
        for pag in pages:
            try:
                outside.append(
                    key_template.device_plan(
                        datos,
                        pag.get("k1"),
                        hub=hub,
                        modelo=modelo,
                        page=pag,
                        device_module=dispositivo_mod,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                outside.append(
                    {
                        "ok": False,
                        "k1": pag.get("k1"),
                        "name": pag.get("name"),
                        "screen": pag.get("screen"),
                        "error": _motivo(exc),
                        "changes": [],
                        "rows": [],
                        "n_to_bind": 0,
                    }
                )
        return outside

    def keys_template_plan(self, k1: int) -> dict:
        """What binding the standard keys on device `k1` would do. Read only.

        `pending` are the keys of that same page that are ALREADY waiting
        in Sync because the user set them by hand: the template does not
        queue those again, and the screen says so.
        """
        if key_template is None:
            return _err(
                "key_template.py does not import: %s"
                % FALTA.get("key_template")
            )
        r = self.keys_model()
        if not r.get("ok"):
            return r
        plan = next(
            (p for p in (r.get("plantilla") or []) if p.get("k1") == int(k1)), None
        )
        if plan is None:
            return _err(
                "device %s doesn't have a page of its own that the firmware "
                "reaches, so there is no place to bind its keys" % k1
            )
        if not plan.get("ok"):
            return _err(plan.get("error") or "no plan could be built", plan=plan)
        if not plan.get("plan_posible"):
            # There is a page and it could be read, but there is nothing to decide
            # with. It is an honest NO, not an error: the screen shows it as text and
            # does not draw the button.
            return _err(
                plan.get("no_plan_reason") or "no plan can be built", plan=plan
            )
        yatuyos = self._keys_pending_on(plan.get("screen"))
        net_changes = [
            c for c in plan.get("changes") or [] if c["codigo"] not in yatuyos
        ]
        return _ok(
            plan=plan,
            k1=int(k1),
            name=plan.get("name"),
            screen=plan.get("screen"),
            changes=net_changes,
            n_a_encolar=len(net_changes),
            n_ya_en_sync=len(plan.get("changes") or []) - len(net_changes),
            summary=plan.get("summary"),
        )

    def _keys_pending_on(self, screen) -> set:
        """The codes of that page already queued in Sync by hand."""
        outside = set()
        if self._changes is None or screen is None:
            return outside
        for c in self._changes.listar():
            p = c.parametros or {}
            if c.kind != "reassign_key":
                continue
            if (p.get("subtipo") or "screen") != "device":
                continue
            try:
                if int(p.get("screen")) == int(screen):
                    outside.add(int(p.get("codigo")))
            except Exception:  # noqa: BLE001
                continue
        return outside

    def keys_template_queue(self, k1: int) -> dict:
        """Queues the standard binding for a device that is ALREADY on the
        remote. Writes nothing: it only adds to Sync's list.

        Same path a hand-made key change takes (`reassign_key`, subtype
        `device`), so there is no second, laxer way in: the gate,
        `teclas_alcance` and `nothing_moved` all still have the last word in
        `sync_preparar()`.
        """
        prev = self.keys_template_plan(k1)
        if not prev.get("ok"):
            return prev
        if self._changes is None:
            return _err("changes.py does not import: %s" % FALTA.get("changes"))
        plan = prev["plan"]
        fallos = []
        puestos = 0
        for c in prev["changes"]:
            row = next((f for f in plan["rows"] if f["codigo"] == c["codigo"]), {})
            label = "Key %s -> %s (%s, on %s's page)" % (
                row.get("key") or key_template.hex2(c["codigo"]),
                row.get("command") or ("command %d" % c["k2"]),
                plan["name"],
                plan["name"],
            )
            r = self.changes_add(
                "reassign_key",
                {
                    "subtipo": "device",
                    "screen": c["screen"],
                    "codigo": c["codigo"],
                    "k1": c["k1"],
                    "k2": c["k2"],
                },
                label,
            )
            if r.get("ok"):
                puestos += 1
            else:
                fallos.append(
                    "%s: %s" % (row.get("key") or c["codigo"], r.get("error"))
                )
        return _ok(
            encolados=puestos,
            fallos=fallos,
            plan=plan,
            summary=plan.get("summary"),
            count=len(self._changes),
        )

    def keys_apply(self, changes: list) -> dict:
        """Generates the blob with the accumulated changes and runs THE GATE.

        Same path as `remote_apply()`: it gets built, validated, and the
        next step (write) exists ONLY if the gate passes. Never writes the
        device.
        """
        if keys_map is None:
            return _err("keys_map.py does not import: %s" % FALTA.get("keys_map"))
        if generate is None:
            return _err("generate.py does not import: %s" % FALTA.get("generate"))
        if not changes:
            return _err("there is no pending change")

        ref = self._remote_blob()
        blob = Path(ref["blob"])
        if not blob.exists():
            return _err("the reference blob does not exist %s" % blob, referencia=ref)
        b = blob.read_bytes()

        # Two kinds of change, two DIFFERENT blob mechanisms. They are not
        # mixed in a single pass: they are chained, and EACH stage runs its
        # own battery of checks. The end-to-end check (that not a single
        # undeclared byte moved between the reference blob and the final
        # one) is done afterward by the real gate, with the union of the
        # repoints.
        screen_changes, fisica_c, device_changes = [], [], []
        for c in changes or []:
            try:
                kind = c.get("kind") or "screen"
                if kind == "device":
                    device_changes.append(
                        {
                            "screen": _int(c["screen"]),
                            "codigo": _int(c["codigo"]),
                            "k1": _int(c["k1"]),
                            "k2": _int(c["k2"]),
                        }
                    )
                elif kind == "fisica":
                    fisica_c.append(
                        {
                            "contexto": _int(c["contexto"]),
                            "codigo": _int(c["codigo"]),
                            "k1": _int(c["k1"]),
                            "k2": _int(c["k2"]),
                        }
                    )
                else:
                    screen_changes.append(
                        {
                            "screen": _int(c["screen"]),
                            "slot": _int(c["slot"]),
                            "codigo": _int(c["codigo"]),
                            "k1": _int(c["k1"]),
                            "k2": _int(c["k2"]),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                return _err("invalid change (%s): %r" % (exc, c))
        if (fisica_c or device_changes) and keys_physical is None:
            return _err(
                "keys_physical.py does not import: %s" % FALTA.get("keys_physical")
            )
        if keys_reach is None:
            return _err(
                "keys_reach.py does not import: %s" % FALTA.get("keys_reach")
            )

        fresh = b
        repuntes: list[int] = []
        detail: list[dict] = []
        chequeos: list[dict] = []
        for label, lote, modulo, apply, controlar in (
            ("screen", screen_changes, keys_map, "aplicar", "checks"),
            ("fisica", fisica_c, keys_physical, "aplicar_fisica", "checks"),
            (
                "device",
                device_changes,
                keys_physical,
                "aplicar_dispositivo",
                "controles_dispositivo",
            ),
        ):
            if not lote:
                continue
            previo = fresh
            try:
                fresh, rep, det = getattr(modulo, apply)(previo, lote)
            except Exception as exc:  # noqa: BLE001
                return _err(str(exc), etapa="generar:" + label, referencia=ref)
            try:
                ch = getattr(modulo, controlar)(previo, fresh, det, rep)
            except Exception as exc:  # noqa: BLE001
                return _err(
                    "the checks failed: %s" % exc, etapa="controles:" + label
                )
            for c2 in ch:
                c2["name"] = "[%s] %s" % (label, c2["name"])
            chequeos += ch
            repuntes += list(rep)
            detail += [dict(d, kind=label) for d in det]
        repuntes = sorted(set(repuntes))
        # THE CHECK THAT WAS MISSING, over the FINAL blob and over all three
        # sites at once: walk the pointers the firmware walks, from the
        # master index, and read what is at the end of the walk. Every other
        # check here looks at the record that was edited; grabada #7 proved
        # that a record can be edited, verified and still not be the one the
        # remote arrives at. See `teclas_alcance.checks`.
        try:
            chequeos += keys_reach.checks(b, fresh, detail)
        except Exception as exc:  # noqa: BLE001
            return _err(
                "the reachability check could not be run: %s" % exc,
                etapa="controles:alcance",
                referencia=ref,
            )
        if not all(c["ok"] for c in chequeos):
            return _err(
                "The new blob's checks did not all come back green: no "
                "file gets written.",
                etapa="checks",
                referencia=ref,
                technical_detail={"checks": chequeos, "changes": detail},
            )

        marca = time.strftime("%Y%m%d_%H%M%S")
        salida = SALIDA / ("teclas_%s.bin" % marca)
        ezhex_salida = SALIDA / ("teclas_%s.EZHex" % marca)
        plantilla = BACKUPS / "one_20260724_210614_a.EZHex"
        SALIDA.mkdir(parents=True, exist_ok=True)
        salida.write_bytes(fresh)
        arm = subprocess.run(  # noqa: S603 -- ezhex.py, no network and no USB
            [
                *_runtime.interprete(),
                "ezhex.py",
                "armar",
                str(plantilla),
                str(salida),
                str(ezhex_salida),
            ],
            cwd=str(CONFIG_WORK),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if arm.returncode != 0 or not ezhex_salida.exists():
            return _err(
                "could not build the .EZHex: %s" % (arm.stderr or arm.stdout),
                etapa="ezhex",
                referencia=ref,
            )

        comp = self.remote_gate(str(ezhex_salida), str(blob), repuntes)
        if not comp.get("ok"):
            return _err(
                "The gate could not be run.",
                etapa="gate",
                referencia=ref,
                technical_detail={"gate": comp, "checks": chequeos},
            )
        cmd = None
        if comp.get("passed"):
            cmd = self.remote_record_command(str(ezhex_salida), str(blob), repuntes)
        return _ok(
            ready=bool(comp.get("passed")),
            # `name` and `commands` are consumed by the SAME write and
            # history path used by "add a device" (`grabarAhora`,
            # `registrarManualControl`): there is no second write
            # implementation that could be more lax.
            name="%d key%s remapped"
            % (
                len(detail),
                "" if len(detail) == 1 else "s",
            ),
            command_records=len(detail),
            file=str(ezhex_salida),
            bin=str(salida),
            md5=_md5(salida),
            tamano=salida.stat().st_size,
            crecio=salida.stat().st_size - len(b),
            changes=detail,
            checks=chequeos,
            repuntes=[_hex(r) for r in repuntes],
            repoints_int=list(repuntes),
            referencia=ref,
            gate=comp,
            command=cmd if (cmd and cmd.get("ok")) else None,
        )

    def command_mapping(self, dir: str) -> dict:
        """Command names of a device already downloaded to
        `account_export/output/<dir>/`.

        Read only, no network: re-reads `hub-config-with-device.json`,
        matching by `requested_device` from the manifest -- the same
        criterion as `catalogo.read_local_export()` (`app/catalog.py`), but
        returning the **names** instead of only the count -- the Mapping
        screen needs to pick a specific command, not just know how many
        there are.

        Used by the Mapping screen to populate the command dropdown when
        assigning a key on the remote's drawing. Writes nothing.
        """
        d = OUTPUT_BRIDGE / dir
        man = d / "manifest.json"
        jsn = d / "hub-config-with-device.json"
        if ".." in dir or "/" in dir or "\\" in dir:
            return _err("invalid directory name: %r" % dir)
        if not (man.exists() and jsn.exists()):
            return _err("%s does not exist" % d)
        try:
            manifest = json.loads(man.read_text())
            pedido = manifest.get("requested_device") or {}
            fabricante = pedido.get("manufacturer")
            modelo = pedido.get("model")
            snapshot = json.loads(jsn.read_text())
            entradas = ((snapshot.get("resources") or {}).get("DeviceList") or {}).get(
                "DevicesWithFeatures"
            ) or []
            candidatos = [
                e
                for e in entradas
                if isinstance(e, dict)
                and (e.get("Device") or {}).get("Manufacturer") == fabricante
                and (e.get("Device") or {}).get("Model") == modelo
            ]
            if len(candidatos) != 1:
                return _err(
                    "expected 1 device %r %r in DeviceList, found %d"
                    % (fabricante, modelo, len(candidatos))
                )
            command_records = candidatos[0].get("Commands") or []
            nombres = sorted(
                {
                    c.get("Name")
                    for c in command_records
                    if isinstance(c, dict) and c.get("Name")
                }
            )
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc)
        return _ok(fabricante=fabricante, modelo=modelo, command_records=nombres)

    def catalog_search(self, email: str, fabricante: str, modelo: str) -> dict:
        """Searches Logitech's global catalog. Read only against the account."""
        if catalog is None or session is None:
            return _err(
                "the catalog is not available: `catalog.py` or `session.py` did not "
                "import (in practice, `keyring` is not installed -- `cd app && uv sync`)",
                detail=FALTA.get("catalog") or FALTA.get("session"),
            )
        try:
            # ONE entry point, ONE type. `ensure_session()` returns the
            # `HarmonySession` the catalog takes; asking for the tokens here
            # and handing those over is what used to blow up inside
            # `signin()` with an AttributeError on the press of this button.
            sesion_abierta = session.ensure_session((email or "").strip())
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "no valid session")
        try:
            res = catalog.search(sesion_abierta, fabricante, modelo)
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "the search failed")
        self._busqueda = res
        items = [
            {
                "index": m.index,
                "fabricante": m.manufacturer,
                "modelo": m.model,
                # `DisplayText`/`SelectedText` come back null in the real
                # catalog: the text is built from Manufacturer + Model.
                "label": m.display_text
                or (" ".join(x for x in (m.manufacturer, m.model) if x)).strip()
                or "(unnamed)",
            }
            for m in res.matches
        ]
        return _ok(items=items, total=len(items))

    def catalog_save(self, email: str, index: int) -> dict:
        """Saves the device chosen from the previous search, READY to use on
        the Control screen.

        READ ONLY against Logitech's public catalog
        (`SearchGlobalDevices`/`GetGlobalDevices`/`GetGlobalLanguageCommands`):
        nothing gets registered or removed on the account. There is no
        confirmation gate because there is nothing irreversible to confirm.

        Two writes, and the second is the one that matters:

          1. the raw catalog package (0.2.0) in `catalog-live/` -- the
             response as is, so it can be audited later;
          2. `biblioteca.materialize()` + `biblioteca.write()`: joins
             those commands with each protocol's timing definition that is
             ALREADY on disk and writes a `device-catalogo-*` folder
             that the Control screen lists and `add_device.py` knows how
             to read.

        The catalog package by itself does NOT bring `ProtocolList` (the
        mark/space timing of each bit): it brings the protocol's name and
        each command's payload. If the library does not have that protocol,
        step 2 cannot happen and **this returns `ok=False`** naming the
        missing protocol -- that's when an `.ir` needs to be imported once
        (`catalog_ir_import`), and from then on that protocol stays
        available for any other device in the same family.

        THIS USED TO RETURN `ok=True, materializado=False` IN THAT CASE, and
        that single key is the whole of the bug the user reported ("it says
        yes and it doesn't show up in the list to send to the device"): the
        screen reads `ok` to decide whether to say "saved", so it said
        "saved" over a device that had been written nowhere. `ok=True` now
        means one thing and only one: the `device-catalogo-*` folder
        exists and the Control screen lists it. The raw package IS still on
        disk either way -- `downloaded=True` says so, and `catalog_pending()` /
        `catalog_resume()` are how it gets finished later.
        """
        if catalog is None or session is None:
            return _err(
                "the catalog is not available: `catalog.py` or `session.py` did not "
                "import (in practice, `keyring` is not installed -- `cd app && uv sync`)",
                detail=FALTA.get("catalog") or FALTA.get("session"),
            )
        if self._busqueda is None:
            return _err("a search needs to run first: there are no results in memory")
        try:
            index = int(index)
        except Exception:  # noqa: BLE001
            return _err("invalid index")
        matches = getattr(self._busqueda, "matches", [])
        if index < 0 or index >= len(matches):
            return _err("index out of range (there are %d results)" % len(matches))
        chosen = matches[index]
        try:
            sesion_abierta = session.ensure_session((email or "").strip())
            paquete = catalog.fetch_device_package(
                sesion_abierta,
                chosen.manufacturer,
                chosen.model,
                self._busqueda,
                index,
            )
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "could not fetch the device")
        label = re.sub(
            r"[^a-z0-9]+", "-", f"{chosen.manufacturer} {chosen.model}".lower()
        ).strip("-")
        target = OUTPUT_BRIDGE / "catalog-live" / f"{label}.json"
        try:
            catalog.save_device_package(paquete, target)
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "could not save")
        if history is not None:
            try:
                history.cache_catalog(
                    [
                        {
                            "manufacturer": chosen.manufacturer,
                            "model": chosen.model,
                            "json_path": str(target),
                        }
                    ]
                )
            except Exception:  # noqa: BLE001
                pass
        # -- and now what makes it usable: each protocol's timings, from
        #    the library already on disk. No network and no account.
        #    ONE call point, shared with `catalog_resume()`.
        return self._materializar_paquete(paquete, target, accion="downloaded")

    # ------------------------------------------------------------------
    # EL UNICO PUNTO DE CONTACTO CON `protocol_library` PARA MATERIALIZAR
    # ------------------------------------------------------------------
    #
    # DOS CAMINOS ENTRAN ACA Y NINGUN OTRO LADO LLAMA A `protocol_library`
    # FOR THIS: `catalog_save()` (just downloaded off the network) and
    # `catalog_resume()` (a package that was already in `catalog-live/`).
    # Splicing in the new library means changing the three lines
    # marked `>>> BIBLIOTECA <<<` below and nothing else.
    #
    # THE CONTRACT IT PUBLISHES, and which key each caller decides by:
    #
    #   ok           -- `True` ONLY if what ended up written is the folder
    #                   `device-catalogo-*` that Control lists. This is
    #                   the underlying change: before, it came out `ok=True`
    #                   with `materializado=False` and the screen said "saved"
    #                   over a device that existed in no list at all.
    #   usable       -- explicit mirror of `ok` for whoever does not want to
    #                   depend on the envelope. `app/ui/app.js:guardar()` paints
    #                   by `r.ok`; `retomar()` too.
    #   materializado-- HISTORICAL ALIAS of `usable`. `app.js` reads it and so
    #                   do the old tests. It is ALWAYS worth the SAME.
    #   bajado       -- the raw package DID end up on disk (`catalog-live/`).
    #                   `app.js` uses it to pick the colour of the banner:
    #                   `downloaded=True` makes it a warning ("downloaded, something
    #                   missing"); without it, an alarm ("could not download").
    #   paquete      -- path of the raw JSON. It is what makes it possible to
    #                   RETOMAR: `catalog_resume(paquete_id)`.
    #   paquete_id   -- the file name without `.json`. It is the key that
    #                   travels to the UI and back; never an absolute path,
    #                   because of the same path traversal guard as
    #                   `catalog_delete`.
    #   destino      -- ONLY when `ok=True`: the device's folder.
    #   faltan       -- protocols the library does not have, by name.
    #   como         -- the real way to get it, in plain language. It comes
    #                   out of the library, it is not worded here.
    def _materializar_paquete(
        self, paquete: dict, package_path: Path, *, accion: str
    ) -> dict:
        """Catalog package on disk -> device folder, or the truth about why
        not. `accion` is `"downloaded"` (came from the network just now) or
        `"retomado"` (was already sitting in `catalog-live/`); it only
        changes the wording."""
        label = package_path.stem
        comun = {
            "downloaded": True,
            "paquete": str(package_path),
            "paquete_id": label,
        }
        # TWO WORDINGS, not one with a verb plugged in: whoever has just
        # downloaded has to be told the download DID go fine (the package is on
        # disk), and whoever is resuming does not need to be told something was
        # downloaded, because he already knows it was.
        downloaded_now = accion == "downloaded"
        verbo = (
            "came down from Logitech's catalog and is saved here"
            if downloaded_now
            else "was already downloaded here"
        )
        rescate = (
            "You do not have to search for it again: it is waiting under "
            "“Downloaded, waiting for a protocol”, and Resume finishes it "
            "the moment that protocol is on disk."
            if downloaded_now
            else "It stays where it is, under “Downloaded, waiting for a "
            "protocol”. Nothing was lost: Resume works the moment that "
            "protocol is on disk."
        )
        if library is None:
            return _err(
                "The device package %s (%s), but it was NOT "
                "saved as a device: library.py does not import (%s), so "
                "the file the Control screen uses could not be built. It will "
                "not show up in any list to send to the remote."
                % (verbo, package_path.name, FALTA.get("library")),
                usable=False,
                materializado=False,
                missing_category="file",
                retomable=True,
                **comun,
            )
        try:
            # >>> BIBLIOTECA <<< (1/3) -- catalog package to `resources`.
            mat = library.materialize(paquete)
        except Exception as exc:  # noqa: BLE001
            return _err(
                "The device package %s (%s), but building "
                "the device failed: %s. It was NOT saved as a device and "
                "will not show up in any list to send to the remote."
                % (verbo, package_path.name, _motivo(exc)),
                usable=False,
                materializado=False,
                retomable=True,
                **comun,
            )
        if not mat.get("ok"):
            missing = mat.get("missing") or []
            # >>> BIBLIOTECA <<< (2/3) -- how to get a missing protocol.
            # `_missing_how_to_get_it` is the name today; if the library
            # publishes it as `how_to_get_it`, this picks that one up
            # without another edit.
            how_to_fn = getattr(library, "how_to_get_it", None) or getattr(
                library, "_missing_how_to_get_it", None
            )
            try:
                how_to = how_to_fn(missing) if (how_to_fn and missing) else ""
            except Exception:  # noqa: BLE001
                how_to = ""
            name = mat.get("name") or label
            explanation = mat.get("error") or "the library is missing its protocol."
            # DO NOT SAY THE REMEDY TWICE. `materialize()` already explains it
            # adentro de su propio `error` ("...they need to be captured once
            # from an .ir file"), and sticking `_missing_how_to_get_it()` on the end
            # gave two paragraphs in a row saying the same thing in other words
            # -- measured in the jsdom render. It is added ONLY if the library's
            # text did not already name the way out. `how_to` travels anyway as
            # a key, for whoever wants to show it separately.
            extra = "" if ".ir" in explanation else (how_to + "\n\n" if how_to else "")
            return _err(
                "%s %s, but it was NOT saved as a device: %s\n\nSo it does "
                "NOT show up in the list to send to the remote, and nothing "
                "was created or removed in your Logitech account (this is "
                "read-only against the public catalog).\n\n%s%s"
                % (name, verbo, explanation, extra, rescate),
                usable=False,
                materializado=False,
                missing=missing,
                missing_category="protocolo",
                retomable=True,
                name=name,
                fabricante=mat.get("fabricante"),
                modelo=mat.get("modelo"),
                command_records=mat.get("commands"),
                como=how_to,
                **comun,
            )
        try:
            # >>> BIBLIOTECA <<< (3/3) -- write the folder Control lists.
            folder = library.write(
                mat, source_kind="catalogo", source=str(package_path)
            )
        except Exception as exc:  # noqa: BLE001
            return _err(
                "The device could be built but writing it failed: %s. It "
                "was NOT saved as a device. The package stays on this "
                "computer and Resume can try again." % _motivo(exc),
                usable=False,
                materializado=False,
                retomable=True,
                **comun,
            )
        return _ok(
            target=str(folder),
            usable=True,
            materializado=True,
            command_records=mat["commands"],
            name=mat["name"],
            fabricante=mat.get("fabricante"),
            modelo=mat.get("modelo"),
            protocolos=mat["protocolos"],
            warning=(
                "Done: %d commands, protocol %s (from what was already on "
                "disk). Your account was not touched: read-only against the "
                "public catalog, nothing was created or removed. It already "
                "shows up in Control. Note: the catalog does not bring each "
                "button's label, so the command name split into words is "
                "used instead." % (mat["commands"], ", ".join(mat["protocolos"]))
            ),
            **comun,
        )

    # ------------------------------------------------------------------
    # CATALOG -- packages downloaded that did NOT become a device
    # ------------------------------------------------------------------
    def catalog_pending(self) -> dict:
        """The raw catalog packages sitting in `catalog-live/` that never
        became a usable device.

        WHY THIS EXISTS. Downloading from the catalog is two things that
        used to be reported as one: the package comes down (network +
        account) and then it has to be joined with the protocol's timings
        (local). The second half can fail with the first half perfectly
        fine -- that is exactly today's case, with the library at 0
        protocols. The package is NOT garbage: it is the expensive half,
        already paid for. This lists it so it can be finished later
        instead of searched for again.

        A package counts as already used when some `device-*` folder
        on disk names it in `manifest.source` -- the key
        `biblioteca.write()` writes. Does not touch the network or the
        account.
        """
        base = OUTPUT_BRIDGE / "catalog-live"
        if not base.exists():
            return _ok(items=[], total=0)
        # Which packages already produced a device folder.
        used: set[str] = set()
        for man in OUTPUT_BRIDGE.glob("*/manifest.json"):
            try:
                source = (json.loads(man.read_text()) or {}).get("source")
            except Exception:  # noqa: BLE001
                continue
            if isinstance(source, str) and source:
                used.add(Path(source).name)
        lib = {}
        if library is not None:
            try:
                # >>> BIBLIOTECA <<< -- what protocols exist today.
                lib = library.available_protocols()
            except Exception:  # noqa: BLE001
                lib = {}
        items = []
        for jsn in sorted(base.glob("*.json")):
            if jsn.name in used:
                continue
            try:
                paquete = json.loads(jsn.read_text())
            except Exception as exc:  # noqa: BLE001
                items.append(
                    {
                        "paquete_id": jsn.stem,
                        "file": str(jsn),
                        "name": jsn.stem,
                        "fabricante": None,
                        "modelo": None,
                        "commands": None,
                        "downloaded_at": None,
                        "ready": False,
                        "missing": [],
                        "reason": "the package can't be read: %s" % _motivo(exc),
                    }
                )
                continue
            q = paquete.get("query") if isinstance(paquete.get("query"), dict) else {}
            item = {
                "paquete_id": jsn.stem,
                "file": str(jsn),
                "fabricante": q.get("manufacturer"),
                "modelo": q.get("model"),
                "name": (
                    " ".join(
                        x
                        for x in (q.get("manufacturer"), q.get("model"))
                        if isinstance(x, str) and x
                    ).strip()
                    or jsn.stem
                ),
                "downloaded_at": paquete.get("generated_at"),
                "commands": None,
                "ready": False,
                "missing": [],
                "reason": None,
            }
            if catalog is not None:
                try:
                    item["commands"] = catalog.command_count(paquete)
                except Exception:  # noqa: BLE001
                    pass
            if library is None:
                item["reason"] = "library.py does not import (%s)" % FALTA.get(
                    "protocol_library"
                )
                items.append(item)
                continue
            # The SAME verdict `catalog_resume()` will reach, measured the
            # same way and not guessed from the file name: a dry
            # `materialize()` against the library as it is right now. Never
            # frozen: a package that is not ready today becomes ready the
            # moment an `.ir` of its family is imported.
            try:
                mat = (
                    library.materialize(paquete, lib)
                    if lib
                    else library.materialize(paquete)
                )
            except Exception as exc:  # noqa: BLE001
                item["reason"] = _motivo(exc)
                items.append(item)
                continue
            if mat.get("ok"):
                item["ready"] = True
                item["name"] = mat.get("name") or item["name"]
                item["fabricante"] = mat.get("fabricante") or item["fabricante"]
                item["modelo"] = mat.get("modelo") or item["modelo"]
                item["commands"] = mat.get("commands", item["commands"])
                item["protocolos"] = mat.get("protocolos") or []
                item["reason"] = None
            else:
                item["missing"] = mat.get("missing") or []
                item["name"] = mat.get("name") or item["name"]
                item["fabricante"] = mat.get("fabricante") or item["fabricante"]
                item["modelo"] = mat.get("modelo") or item["modelo"]
                if mat.get("commands"):
                    item["commands"] = mat["commands"]
                item["reason"] = mat.get("error") or "its protocol is not on disk"
            items.append(item)
        return _ok(items=items, total=len(items))

    def catalog_resume(self, paquete_id: str) -> dict:
        """Finishes a package that was downloaded but never became a device.

        No network and no account: the package is already on disk. Same
        contract as `catalog_save()` (see `_materializar_paquete`), so the
        screen renders both with the same code.
        """
        # THE SAME PATH TRAVERSAL GUARD AS `catalog_delete`: what arrives
        # is a NAME, never a path. It is resolved inside
        # `catalog-live/` and the result is checked to still be in there.
        name = (paquete_id or "").strip()
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return _err("invalid package name")
        base = (OUTPUT_BRIDGE / "catalog-live").resolve()
        path = (
            base / (name if name.endswith(".json") else name + ".json")
        ).resolve()
        if base not in path.parents or not path.is_file():
            return _err("that downloaded package isn't on this computer anymore")
        try:
            paquete = json.loads(path.read_text())
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "the downloaded package can't be read")
        return self._materializar_paquete(paquete, path, accion="retomado")

    # ------------------------------------------------------------------
    # CATALOG -- import a .ir by hand (no account and no network)
    # ------------------------------------------------------------------
    def catalog_ir_analyze(self, path: str) -> dict:
        """What an `.ir` file brings (Flipper Zero / public IRDB format):
        which commands can be imported and, one by one, why not the ones
        that can't. Writes nothing, opens no network, touches no account."""
        if ir_manual is None:
            return _err("ir_manual.py does not import: %s" % FALTA.get("ir_manual"))
        ref = self._current_reference()
        blob = Path(ref["blob"])
        # with the blob on hand, `analyze()` also runs the three label
        # checks on EVERY command (draw / write / fit in the 81 px cell): it
        # is what keeps a device from being imported only to later make
        # `add_device.py` abort at the end of a long run.
        r = ir_manual.analyze(_path(path), blob.read_bytes() if blob.exists() else None)
        return _ok(**r) if r.get("ok") else _err(r.get("error") or "could not read")

    def catalog_ir_validate(
        self, path: str, fabricante: str, modelo: str, name: str
    ) -> dict:
        """The checks that really decide whether the remote can write this
        device: the name's two (`fonts.choose_detail` + the glyph
        table), the fixed `Devices` label's, and every button label's three
        -- with the same vocabulary that is going to be frozen inside the
        file."""
        if ir_manual is None:
            return _err("ir_manual.py does not import: %s" % FALTA.get("ir_manual"))
        ref = self._current_reference()
        blob = Path(ref["blob"])
        if not blob.exists():
            return _err("the reference blob does not exist %s" % blob)
        r = ir_manual._parse_commands(_path(path))
        if not r.get("ok"):
            return _err(r.get("error") or "could not read the .ir")
        ir_manual._mark_impossible_labels(r["commands"], blob.read_bytes())
        soportados = [c for c in r["commands"] if c["soportado"]]
        if not soportados:
            return _err("no command in that file can be imported")
        try:
            v = ir_manual.validate_name(
                name, soportados, fabricante, modelo, blob.read_bytes()
            )
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "validation failed")
        return _ok(**v)

    def catalog_ir_import(
        self, path: str, fabricante: str, modelo: str, name: str
    ) -> dict:
        """Imports the `.ir` and leaves the device ready in Control.

        This is the path for a device the catalog doesn't have, or for a
        protocol the library doesn't know yet. It touches no account and
        needs no internet: the `.ir` is a raw capture (microseconds +
        carrier), which is exactly what the blob needs.
        """
        if ir_manual is None:
            return _err("ir_manual.py does not import: %s" % FALTA.get("ir_manual"))
        ref = self._current_reference()
        blob = Path(ref["blob"])
        if not blob.exists():
            return _err("the reference blob does not exist %s" % blob)
        try:
            r = ir_manual.import_device(
                _path(path), fabricante, modelo, name, blob=blob.read_bytes()
            )
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "the import failed")
        if not r.get("ok"):
            return _err(r.get("error") or "could not import", **r)
        return _ok(**{k: v for k, v in r.items() if k != "ok"})

    # ==================================================================
    # CONTROL -- what's in a configuration file and in a blob
    # ==================================================================
    def remote_devices_from_json(self, config_json: str) -> dict:
        """The devices an already-captured configuration file
        (`hub-config-with-device.json`) brings, with the exact name
        `add_device.py --device` expects."""
        if command_records is None:
            return _err("command_records.py does not import: %s" % FALTA.get("command_records"))
        path = _path(config_json)
        if not path.exists():
            return _err("%s does not exist" % path)
        try:
            _protos, devs = command_records.load_hub_config(str(path))
        except Exception as exc:  # noqa: BLE001
            return _err("could not read the configuration file: %s" % exc)
        items = []
        for i, dv in enumerate(devs):
            d = dv.get("Device") or {}
            items.append(
                {
                    "index_in_json": i,
                    "name": command_records.device_name(dv),
                    "fabricante": d.get("Manufacturer"),
                    "modelo": d.get("Model"),
                    "commands": len(dv.get("Commands") or []),
                }
            )
        return _ok(items=items)

    def remote_next_index(self, blob: str) -> dict:
        """How many devices the blob already has = the index for the next one.

        Comes from the blob's section [5], not from a number typed by hand.
        """
        if dispositivo_mod is None:
            return _err("add_device.py does not import: %s" % FALTA.get("add_device"))
        path = _path(blob)
        if not path.exists():
            return _err("%s does not exist" % path)
        try:
            actuales = len(dispositivo_mod.read_section5(path.read_bytes()))
        except Exception as exc:  # noqa: BLE001
            return _err("could not read section [5]: %s" % exc)
        return _ok(devices=actuales, index=actuales)

    def remote_validate_label(self, label: str, blob: str | None = None) -> dict:
        """Runs BEFORE generating: the hardware draws no Q, X, or Z in any
        of its 18 fonts, so `Xbox` or `Zappiti` are not writable."""
        if fonts is None:
            return _err("fonts.py does not import: %s" % FALTA.get("fonts"))
        path = _path(blob) if blob else (BACKUPS / "config_raw.bin")
        if not path.exists():
            return _err("the reference blob does not exist %s" % path)
        label = (label or "").strip()
        if not label:
            return _err("the name is empty: type the name the remote will draw")
        try:
            d = fonts.choose_detail(label, path.read_bytes())
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "the font check failed")
        return _ok(
            label=label,
            dibujable=d.get("atributo") is not None,
            atributo=d.get("atributo"),
            validos=list(d.get("valid_attributes") or []),
            missing=sorted(d.get("missing") or []),
            warning=d.get("warning"),
            ausentes_conocidas=sorted(getattr(fonts, "MAYUSCULAS_AUSENTES", [])),
        )

    # ==================================================================
    # CONTROL -- the device (READ ONLY)
    # ==================================================================
    def remote_identify(self) -> dict:
        """`read_config.py` without comparing: arch, skin, firmware, and bytes used."""
        if not self._lock_aparato.acquire(blocking=False):
            return _err("there is already an operation in progress with the device")
        try:
            res = self._run_read()
        finally:
            self._lock_aparato.release()
        d = self._identity_summary(res)
        d.update(
            conectado=bool(res.get("ok")),
            returncode=res.get("returncode"),
            stdout=res.get("stdout", ""),
            stderr=res.get("stderr", ""),
        )
        return _ok(**d)

    def remote_loop_closure(self, file: str) -> dict:
        """QUICK check: the size the remote declares vs the file that was
        sent. Seconds, not minutes.

        This one on its own is NOT byte-for-byte (`TEXTO_SOLO_TAMANO`).
        The byte-for-byte check DOES exist now and is a separate,
        deliberate step -- `sync_verificar_grabado()`, ~136 s -- so this
        method points at it instead of claiming it is impossible.
        """
        path = _path(file)
        if not path.exists():
            return _err("%s does not exist" % path, advertencia=TEXTO_SOLO_TAMANO)
        if not self._lock_aparato.acquire(blocking=False):
            return _err("there is already an operation in progress with the device")
        try:
            res = self._run_read(comparar=str(path))
        finally:
            self._lock_aparato.release()
        d = self._identity_summary(res)
        text = (res.get("stdout") or "") + "\n" + (res.get("stderr") or "")
        m = RE_DECLARA.search(text)
        declara = int(m.group(1)) if m else d.get("used")
        m = RE_VEREDICTO.search(text)
        veredicto = m.group(1).strip() if m else d.get("veredicto")
        tam = path.stat().st_size
        d.update(
            file=str(path),
            tamano_archivo=tam,
            declara=declara,
            veredicto=veredicto,
            coincide=(declara is not None and declara == tam),
            conectado=bool(res.get("etapas")),
            returncode=res.get("returncode"),
            stdout=res.get("stdout", ""),
            stderr=res.get("stderr", ""),
            advertencia=TEXTO_SOLO_TAMANO,
            # What CAN be done, said here and not as an excuse:
            # the raw dump of the flash gives the same sha256 as the `.bin`.
            byte_a_byte=False,
            byte_a_byte_disponible=True,
            byte_a_byte_metodo="sync_verificar_grabado",
            byte_a_byte_segundos=136,
            loop_closure=TEXTO_CIERRE_DE_LAZO,
        )
        return _ok(**d)

    # ==================================================================
    # CONTROL -- generate
    # ==================================================================
    def remote_generate(self, params: dict) -> dict:
        """Runs `add_device.py` (subprocess) via `generar.generate()`.

        `params`: blob, config_json, nombre, salida, dispositivo (optional),
        indice (optional: if missing it comes from section [5]), ezhex,
        plantilla.

        Every path is turned ABSOLUTE before it goes down to `generate.py`:
        the subprocess runs with `cwd=config_work`, so a relative one from
        the UI would point somewhere else over there.
        """
        if generate is None:
            return _err("generate.py does not import: %s" % FALTA.get("generate"))
        params = params or {}
        try:
            blob = _path(params.get("blob") or (BACKUPS / "config_raw.bin"))
            config_json = _path(params["config_json"])
            salida = _path(params["output"])
        except KeyError as exc:
            return _err("missing parameter %s" % exc)
        except Exception as exc:  # noqa: BLE001
            return _err("invalid parameters: %s" % exc)
        name = (params.get("name") or "").strip()
        if not name:
            # in user language: nobody types `--name` by hand.
            return _err("The name to show in the remote's menu is missing.")
        if not blob.exists():
            return _err("the base blob does not exist %s" % blob)
        if not config_json.exists():
            return _err(
                "the chosen configuration file does not exist: %s" % config_json
            )

        index = params.get("index")
        if index in (None, "", "auto"):
            r = self.remote_next_index(str(blob))
            if not r["ok"]:
                return r
            index = r["index"]
        try:
            index = int(index)
        except Exception:  # noqa: BLE001
            return _err("invalid index: %r" % (params.get("index"),))

        ezhex_salida = params.get("ezhex")
        plantilla = params.get("plantilla")
        if ezhex_salida:
            ezhex_salida = str(_path(ezhex_salida))
            if not plantilla:
                return _err("writing a .EZHex needs --plantilla")
            plantilla = str(_path(plantilla))
            if not Path(plantilla).exists():
                return _err("the template does not exist %s" % plantilla)

        res = generate.generate(
            blob,
            config_json,
            index=index,
            name=name,
            salida=str(salida),
            device=params.get("device") or None,
            ezhex=ezhex_salida,
            plantilla=plantilla,
            timeout=float(params.get("timeout") or 900.0),
        )

        repuntes = sorted(
            {int(x, 16) for x in RE_REPUNTA.findall(res.get("stdout", ""))}
        )
        salida_dict = {
            "etapa": res.get("etapa"),
            "command": res.get("command"),
            "returncode": res.get("returncode"),
            "stdout": res.get("stdout", ""),
            "stderr": res.get("stderr", ""),
            "blob": str(blob),
            "output": str(salida) if salida.exists() else None,
            "ezhex": ezhex_salida
            if ezhex_salida and Path(ezhex_salida).exists()
            else None,
            "index": index,
            "name": name,
            "repuntes": [_hex(p) for p in repuntes],
            "repoints_int": repuntes,
            "labels_ok": (res.get("labels") or {}).get("ok"),
            "labels_missing": [
                {"label": e, "warning": a}
                for e, a in (res.get("labels") or {}).get("faltantes", [])
            ],
        }
        if res.get("ok") and salida.exists():
            salida_dict["md5"] = _md5(salida)
            salida_dict["tamano"] = salida.stat().st_size
            salida_dict["is_anchor"] = salida_dict["md5"] == ANCLA_MD5
            salida_dict["devices"] = self._count_devices(salida)
        if not res.get("ok"):
            self._last_generation = None
            return _err(
                res.get("stderr")
                or "add_device.py aborted (returncode %s)" % res.get("returncode"),
                **salida_dict,
            )
        self._last_generation = salida_dict
        self._last_gate = None  # generating again invalidates the gate
        return _ok(**salida_dict)

    @staticmethod
    def _count_devices(blob: Path) -> int | None:
        if dispositivo_mod is None:
            return None
        try:
            return len(dispositivo_mod.read_section5(blob.read_bytes()))
        except Exception:  # noqa: BLE001
            return None

    # ==================================================================
    # CONTROL -- the gate (does not need the remote)
    # ==================================================================
    def remote_gate(self, fresh: str, referencia: str, repuntes=None) -> dict:
        """Previews `grabar.nothing_moved()` WITHOUT touching the device.

        `fresh` can be the `.bin` or the `.EZHex` (split with
        `ezhex.split`). Reproduces the two lines `write.py` would print,
        as is.
        """
        if generate is None:
            return _err("generate.py does not import: %s" % FALTA.get("generate"))
        try:
            rep = _repoints(repuntes)
        except Exception as exc:  # noqa: BLE001
            return _err("invalid repoints: %s" % exc)
        new_p = _path(fresh)
        ref_p = _path(referencia)
        if not new_p.exists():
            return _err("the file to write does not exist: %s" % new_p)
        if not ref_p.exists():
            return _err("the reference blob does not exist: %s" % ref_p)
        datos = new_p.read_bytes()
        if new_p.suffix.lower() == ".ezhex":
            try:
                import ezhex

                _cab, datos = ezhex.split(datos)
            except Exception as exc:  # noqa: BLE001
                return _err("could not split the .EZHex: %s" % exc)
        try:
            res = generate.preview_gate(ref_p.read_bytes(), datos, rep)
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "the gate failed")
        line1 = "nothing moved relative to %s: %s (%d bytes differ)" % (
            ref_p,
            "YES" if res["ok"] else "NO",
            res["diferencias"],
        )
        lines = [line1]
        if rep:
            lines.append(
                "declared repoints: %s   bytes outside what was declared: %s"
                % (
                    ", ".join(_hex(p) for p in rep),
                    ", ".join(_hex(x) for x in res["sin_declarar"]) or "none",
                )
            )
        d = {
            "passed": bool(res["ok"]),
            "diferencias": res["diferencias"],
            "sin_declarar": [_hex(x) for x in res["sin_declarar"]],
            "repuntes": [_hex(p) for p in rep],
            "repoints_int": rep,
            "fresh": str(new_p),
            "referencia": str(ref_p),
            "salida_cruda": "\n".join(lines),
        }
        self._last_gate = d if d["passed"] else None
        return _ok(**d)

    # ==================================================================
    # CONTROL -- the write command (data, not execution)
    # ==================================================================
    def remote_record_command(
        self,
        ezhex: str,
        referencia: str | None = None,
        repuntes=None,
        igual_grabo: bool = False,
        verify_only: bool = False,
    ) -> dict:
        """Builds `write.py`'s argv. Does NOT run it: returns text.

        Delegates to `aparato.build_record_line()`, which by construction
        has not a single `subprocess.run` pointing at `write.py`.
        """
        if remote is None:
            return _err("remote.py does not import: %s" % FALTA.get("remote"))
        try:
            rep = _repoints(repuntes)
        except Exception as exc:  # noqa: BLE001
            return _err("invalid repoints: %s" % exc)
        try:
            line = remote.build_record_line(
                str(_path(ezhex)),
                reference=str(_path(referencia)) if referencia else None,
                repoints=rep,
                same_recording=bool(igual_grabo),
                verify_only=bool(verify_only),
            )
        except Exception as exc:  # noqa: BLE001
            return _err("could not build the command: %s" % exc)
        return _ok(
            argv=list(line.argv),
            command=line.command,
            cwd=str(CONFIG_WORK),
            escribe_flash=bool(line.writes_flash),
            advertencia=line.warning,
            avisos=list(line.extra_warnings),
            sera_rechazado=any("is going to reject" in a for a in line.extra_warnings),
        )

    def remote_record(
        self,
        ezhex: str,
        referencia: str,
        repuntes=None,
        ack: str = "",
        name: str = "",
        command_records: int | None = None,
    ) -> dict:
        """WRITES FLASH. Needs BOTH keys turned, both checked in Python:

        1. `ack == "GRABAR"` -- the user's explicit confirmation.
        2. The gate in green, recomputed HERE (the JS is not trusted).

        (`RE_HARMONY_SOLO_LECTURA=1` shuts this off entirely, for whoever
        wants it; it is not needed to allow writing.)

        Also requires `--referencia`: without it `write.py` rejects it
        anyway, and `--igual-grabo` is deliberately not exposed through
        this path.
        """
        if not PERMITIR_GRABADO:
            return _err(TEXTO_GRABADO_APAGADO, apagado=True, solo_lectura=True)
        if ack != "GRABAR":
            return _err(
                "the explicit confirmation is missing: nothing was written",
                falta_confirmacion=True,
            )
        if not referencia:
            return _err("--referencia with a known-good blob is needed")
        comp = self.remote_gate(ezhex, referencia, repuntes)
        if not comp.get("ok") or not comp.get("passed"):
            return _err(
                "the gate did not pass: not writing",
                gate=comp,
            )
        cmd = self.remote_record_command(ezhex, referencia, comp["repoints_int"])
        if not cmd.get("ok"):
            return cmd
        if not self._lock_aparato.acquire(blocking=False):
            return _err("there is already an operation in progress with the device")
        try:
            proc = subprocess.run(
                cmd["argv"],
                cwd=str(CONFIG_WORK),
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
            salida, error, rc = proc.stdout, proc.stderr, proc.returncode
        except Exception as exc:  # noqa: BLE001
            salida, error, rc = "", _motivo(exc), -1
        finally:
            self._lock_aparato.release()
        gid = None
        anotado = None
        try:
            gid = self._annotate(
                ezhex,
                referencia,
                comp,
                rc,
                "grabbed from the app",
                name or None,
                command_records,
            )
        except Exception as exc:  # noqa: BLE001
            anotado = _motivo(exc)
        return _ok(
            write_id=gid,
            no_se_anoto=anotado,
            returncode=rc,
            stdout=salida,
            stderr=error,
            command=cmd["command"],
            pregunta="Now unplug the remote: did it boot fine?",
        )

    def remote_register_manual_recording(
        self,
        ezhex: str,
        referencia: str,
        repuntes=None,
        resultado: int = 0,
        notas: str = "",
        name: str = "",
        command_records: int | None = None,
    ) -> dict:
        """The real path: a human ran the command in their terminal and
        comes back to the app to record it.

        Runs the gate again to save its result alongside the entry -- it
        does not trust the UI or the operator's memory.
        """
        comp = self.remote_gate(ezhex, referencia, repuntes)
        if not comp.get("ok"):
            return comp
        try:
            gid = self._annotate(
                ezhex,
                referencia,
                comp,
                int(resultado),
                notas or "grabbed by hand from the terminal",
                name or None,
                command_records,
            )
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc, "could not record it in the history")
        return _ok(
            write_id=gid,
            gate=comp,
            pregunta="Now unplug the remote: did it boot fine?",
        )

    def _annotate(
        self,
        ezhex,
        referencia,
        comp: dict,
        resultado: int,
        notas: str,
        name: str | None = None,
        commands_n: int | None = None,
    ) -> int:
        """Inserts the grabbed entry into the history (copies the .EZHex to
        data/).

        `name`/`commands_n` are the label shown in the remote's menu and
        how many commands it has -- what later builds the "remote status"
        human sentence (`_last_devices_snapshot` covers the catalog;
        this covers what the app itself applied)."""
        if history is None:
            raise RuntimeError(
                "history.py does not import: %s" % FALTA.get("history")
            )
        ez = _path(ezhex)
        ref = _path(referencia) if referencia else None
        gid = history.record(
            ez,
            reference_sha256=_sha256(ref) if ref and ref.exists() else None,
            repoints=comp.get("repoints_int") or [],
            gate_ok=bool(comp.get("passed")),
            result=resultado,
            notes=notas,
        )
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE grabadas SET referencia_path=?, ezhex_origen=?, "
                "compuerta_salida=?, etiqueta_dispositivo=?, comandos_dispositivo=? "
                "WHERE id=?",
                (
                    str(ref) if ref else None,
                    str(ez),
                    comp.get("salida_cruda"),
                    name,
                    commands_n,
                    gid,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return gid

    # ==================================================================
    # HISTORY
    # ==================================================================
    def history(self) -> dict:
        """What got grabbed, when, with which gate, and whether it booted fine."""
        if history is None:
            return _err("history.py does not import: %s" % FALTA.get("history"))
        conn = self._connect()
        try:
            rows = [
                dict(f) for f in conn.execute("SELECT * FROM grabadas ORDER BY id DESC")
            ]
        finally:
            conn.close()
        items = []
        for f in rows:
            f["repuntes"] = json.loads(f.get("repuntes") or "[]")
            f["repoints_hex"] = [_hex(int(p)) for p in f["repuntes"]]
            f["compuerta_ok"] = (
                None if f.get("compuerta_ok") is None else bool(f["compuerta_ok"])
            )
            f["verificado_por_usuario"] = (
                None
                if f.get("verificado_por_usuario") is None
                else bool(f["verificado_por_usuario"])
            )
            ez = f.get("ezhex_path") or ""
            f["existe_copia"] = bool(ez) and Path(ez).is_file()
            f["file_name"] = Path(ez).name if ez else ""
            items.append(f)
        return _ok(items=items)

    def history_confirm_startup(
        self, write_id: int, arranco: bool, notas: str = ""
    ) -> dict:
        """`resultado: 0` only says libconcord did not fail. This is the
        only proof the remote came out usable -- a human gives it."""
        if history is None:
            return _err("history.py does not import: %s" % FALTA.get("history"))
        try:
            history.mark_verified(
                int(write_id), bool(arranco), notas if notas else None
            )
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc)
        respuesta = _ok(
            guardado=True, write_id=int(write_id), arranco=bool(arranco)
        )
        if not arranco:
            anterior = self._last_good(int(write_id))
            respuesta["ofrecer_rollback"] = anterior
        return respuesta

    def _last_good(self, write_id: int) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM grabadas WHERE id<? AND verificado_por_usuario=1 "
                "ORDER BY id DESC LIMIT 1",
                (write_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        d = dict(row)
        d["repuntes"] = json.loads(d.get("repuntes") or "[]")
        return d

    def history_command_rollback(self, write_id: int) -> dict:
        """The command to go back to entry `write_id`. NEVER runs it."""
        if history is None:
            return _err("history.py does not import: %s" % FALTA.get("history"))
        try:
            datos = history.for_rollback(int(write_id))
        except Exception as exc:  # noqa: BLE001
            return _err_de(exc)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT referencia_path FROM grabadas WHERE id=?", (int(write_id),)
            ).fetchone()
        finally:
            conn.close()
        ref = row["referencia_path"] if row else None
        if not ref or not Path(ref).exists():
            return _err(
                "the --referencia blob for that entry is missing (%s). "
                "`write.py` requires it: pick it by hand on the Control "
                "screen." % (ref or "no path"),
                ezhex=str(datos["ezhex_path"]),
                repuntes=[_hex(int(p)) for p in datos["repuntes"]],
            )
        cmd = self.remote_record_command(
            str(datos["ezhex_path"]), ref, datos["repuntes"]
        )
        if cmd.get("ok"):
            cmd["ezhex"] = str(datos["ezhex_path"])
            cmd["referencia"] = ref
        return cmd

    # ==================================================================
    # REGRESSION ANCHOR
    # ==================================================================
    def anchor_status(self) -> dict:
        """The md5 of the blob grabbed today, measured on disk."""
        if not ANCLA_BIN.exists():
            # NOT "file not found". The anchor is one machine's regression
            # reference -- the blob already burned and verified on a remote,
            # kept to prove that rebuilding it lands on the same bytes. It
            # is deliberately not shipped, so on a fresh clone this button
            # can never light up and the message has to say what to do
            # instead of naming a path nobody can produce.
            return _err(
                "the regression anchor is not on this computer (%s). It is a "
                "blob that was already burned and verified on a remote, kept "
                "to prove that rebuilding it lands on the same bytes; it is "
                "not part of the repo. Nothing else needs it: your own "
                "reference is the one the Control screen reads off YOUR "
                "remote." % ANCLA_BIN,
                file=str(ANCLA_BIN),
                esperado=ANCLA_MD5,
            )
        m = _md5(ANCLA_BIN)
        return _ok(
            file=str(ANCLA_BIN),
            md5=m,
            esperado=ANCLA_MD5,
            coincide=m == ANCLA_MD5,
            tamano=ANCLA_BIN.stat().st_size,
        )

    def anchor_regenerate(self, target: str | None = None) -> dict:
        """Redoes the two-step chain and requires the SAME md5.

        `config_raw.bin` (3 devices) -> Philips (index 3) -> LG (index 4).
        Always writes to a separate directory: never over `output/`.
        """
        if generate is None:
            return _err("generate.py does not import: %s" % FALTA.get("generate"))
        d = (
            _path(target)
            if target
            else Path(tempfile.mkdtemp(prefix="harmony_ancla_"))
        )
        d.mkdir(parents=True, exist_ok=True)
        if d.resolve() == SALIDA.resolve():
            return _err("the anchor is never regenerated over output/")
        plantilla = BACKUPS / "one_20260724_210614_a.EZHex"

        # THE PRECONDITION, SAID OUT LOUD. This replay feeds on three files
        # that were produced on one machine and are not in the repo: two
        # account exports and the packaging template. Without this check the
        # button answered "step 1 failed" and hid the reason inside `steps`
        # -- a sign that says nothing. Whoever presses it deserves to read
        # WHAT is missing and that there is nothing to install to get it.
        philips_json = (
            OUTPUT_BRIDGE
            / "hub-config-tv-a"
            / "hub-config-with-device.json"
        )
        lg_json = (
            OUTPUT_BRIDGE / "hub-config-tv-b" / "hub-config-with-device.json"
        )
        entradas = {
            "the account export of step 1": philips_json,
            "the account export of step 2": lg_json,
            "the packaging template": plantilla,
            "the starting blob": BACKUPS / "config_raw.bin",
        }
        missing = [name for name, path in entradas.items() if not path.is_file()]
        if missing:
            return _err(
                "the anchor can't be regenerated here: %s %s missing. Those "
                "files are not part of the repo -- they came off the account "
                "and the remote of whoever made the anchor, so there is "
                "nothing to install to get them. What you CAN do is the same "
                "comparison on your own remote: read it on the Control "
                "screen, prepare a Sync, and compare that."
                % (", ".join(missing), "is" if len(missing) == 1 else "are"),
                missing=[str(entradas[n]) for n in missing],
            )
        steps = []

        p1 = self.remote_generate(
            {
                "blob": str(BACKUPS / "config_raw.bin"),
                "config_json": str(philips_json),
                "name": "Philips",
                "index": 3,
                "output": str(d / "philips_empaquetado.bin"),
                "ezhex": str(d / "philips_empaquetado.EZHex"),
                "plantilla": str(plantilla),
            }
        )
        steps.append(
            {
                "passed": 1,
                "name": "Philips",
                "ok": bool(p1.get("ok")),
                "md5": p1.get("md5"),
                "esperado": ANCLA_PASO1_MD5,
                "coincide": p1.get("md5") == ANCLA_PASO1_MD5,
                "repuntes": p1.get("repuntes"),
                "error": p1.get("error"),
            }
        )
        if not p1.get("ok"):
            return _err(
                "step 1 of 2 (%s) failed: %s"
                % (steps[-1]["name"], p1.get("error") or "it gave no reason"),
                steps=steps,
                dir=str(d),
                detail=p1,
            )

        p2 = self.remote_generate(
            {
                "blob": str(d / "philips_empaquetado.bin"),
                "config_json": str(lg_json),
                "device": "LG TV",
                "name": "LG",
                "index": 4,
                "output": str(d / "config_empaquetada.bin"),
                "ezhex": str(d / "config_empaquetada.EZHex"),
                "plantilla": str(plantilla),
            }
        )
        steps.append(
            {
                "passed": 2,
                "name": "LG",
                "ok": bool(p2.get("ok")),
                "md5": p2.get("md5"),
                "esperado": ANCLA_MD5,
                "coincide": p2.get("md5") == ANCLA_MD5,
                "repuntes": p2.get("repuntes"),
                "error": p2.get("error"),
            }
        )
        if not p2.get("ok"):
            return _err(
                "step 2 of 2 (%s) failed: %s"
                % (steps[-1]["name"], p2.get("error") or "it gave no reason"),
                steps=steps,
                dir=str(d),
                detail=p2,
            )

        # And the gate for both steps, with its negative.
        c1 = self.remote_gate(
            str(d / "philips_empaquetado.bin"),
            str(BACKUPS / "config_raw.bin"),
            ANCLA_REPUNTES,
        )
        c2 = self.remote_gate(
            str(d / "config_empaquetada.bin"),
            str(d / "philips_empaquetado.bin"),
            ANCLA_REPUNTES,
        )
        neg = self.remote_gate(
            str(d / "philips_empaquetado.bin"),
            str(BACKUPS / "config_raw.bin"),
            [ANCLA_REPUNTES[0]],
        )
        return _ok(
            dir=str(d),
            steps=steps,
            md5=p2.get("md5"),
            esperado=ANCLA_MD5,
            coincide=p2.get("md5") == ANCLA_MD5,
            compuerta_paso1=c1.get("passed"),
            compuerta_paso2=c2.get("passed"),
            compuerta_negativo=neg.get("passed"),
            negativo_correcto=neg.get("passed") is False,
        )

    # ==================================================================
    # window utilities
    # ==================================================================
    def choose_file(self, guardar: bool = False, name: str = "") -> dict:
        """Native file dialog. Returns `{ok, path}`."""
        if self._window is None:
            return _err("there is no window (the app is running without a GUI)")
        try:
            import webview

            if guardar:
                res = self._window.create_file_dialog(
                    webview.SAVE_DIALOG,
                    directory=str(SALIDA),
                    save_filename=name or "nuevo.bin",
                )
            else:
                res = self._window.create_file_dialog(
                    webview.OPEN_DIALOG, directory=str(RAIZ)
                )
        except Exception as exc:  # noqa: BLE001
            return _err("could not open the dialog: %s" % exc)
        if not res:
            return _ok(path=None)
        path = res if isinstance(res, str) else res[0]
        return _ok(path=str(path))

    def open_folder(self, path: str) -> dict:
        """Shows a file or folder in Finder/Explorer."""
        p = _path(path)
        if not p.exists():
            return _err("%s does not exist" % p)
        try:
            if platform.system() == "Darwin":
                subprocess.run(["open", "-R", str(p)], check=False)
            elif platform.system() == "Windows":
                subprocess.run(["explorer", "/select,", str(p)], check=False)
            else:
                subprocess.run(["xdg-open", str(p.parent)], check=False)
        except Exception as exc:  # noqa: BLE001
            return _err(str(exc))
        return _ok(path=str(p))


if __name__ == "__main__":
    # Console check: imports the class, instantiates it, and calls the
    # methods that touch neither the device nor the network. Used to verify
    # the API without a GUI.
    api = Api()
    print(json.dumps(api.ping(), ensure_ascii=False))
    est = api.status()
    print("falta:", est["absent"])
    print("permitir_grabado:", est["permitir_grabado"])
    print("ancla:", json.dumps(api.anchor_status(), ensure_ascii=False))
    print(
        "compuerta ancla:",
        json.dumps(
            {
                k: v
                for k, v in api.remote_gate(
                    str(SALIDA / "config_empaquetada.bin"),
                    str(SALIDA / "philips_empaquetado.bin"),
                    ANCLA_REPUNTES,
                ).items()
                if k != "salida_cruda"
            },
            ensure_ascii=False,
        ),
    )
    print("history:", len(api.history().get("items", [])), "entradas")
