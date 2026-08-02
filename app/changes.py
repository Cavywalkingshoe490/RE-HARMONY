#!/usr/bin/env python3
"""La lista de cambios pendientes de la sesion, y el boton de Sync.

Hoy cada pantalla aplica por su cuenta: `Api.remote_apply()` agrega UN
dispositivo y ya corre la compuerta; `Api.remote_delete()` borra UNO y
corre la compuerta; `Api.keys_apply()` reasigna teclas (esa si, ya en
lote) y corre la compuerta; `Api.activity_prepare()` edita UNA actividad.
Cuatro botones "Aplicar" distintos, cuatro compuertas corridas por
separado -- y por eso el usuario tiene que decidir, pantalla por pantalla,
si escribe ahora o espera.

Este modulo junta esas cuatro cosas en UNA lista (`SesionCambios`) que vive
en memoria durante la sesion (no se persiste a disco: es "lo que voy
haciendo mientras tengo la app abierta", no un borrador que sobreviva un
reinicio -- si hiciera falta sobrevivirlo, el lugar es
`Api._datos/cambios_pendientes.json`, pero eso no lo pidio el brief). Y
`apply_all()` es el Sync: los aplica EN ORDEN, encadenando blob -> blob
-- exactamente el mismo patron que `Api.keys_apply()` YA usa para mezclar
reasignaciones de pantalla y fisicas en una sola pasada -- generalizado a
las cuatro clases de cambio.

NINGUNA funcion de este archivo escribe el dispositivo. Todas dejan un
`.bin`/`.EZHex` en `salida_dir` y devuelven el resultado de la compuerta
(`generar.preview_gate`), igual que `remote_apply`/`remote_delete`/
`activity_prepare`/`keys_apply` hacen hoy. Escribir de verdad sigue siendo
un paso aparte, detras de las dos llaves que se verifican en Python
(`ack=="GRABAR"` -- la confirmacion explicita del boton rojo -- y la
compuerta verde) -- eso vive en `Api.sync_apply_start()`, no aca.
(`RE_HARMONY_SOLO_LECTURA=1` apaga la escritura del todo; ninguna variable
de entorno hace falta para habilitarla.)

No reimplementa nada de `config_work/`: cada tipo de cambio delega en la
MISMA funcion que su pantalla individual ya usa hoy (`generar.generate`,
`generar.delete_device`, `generar.edit_activity`, `keys_map.apply`,
`keys_physical.apply_physical`) -- no hay una segunda copia del modelo que
pueda divergir.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_WORK = Path(__file__).resolve().parent.parent / "config_work"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(CONFIG_WORK))
import _runtime  # noqa: E402

# SOFT, like `api.py`'s: `changes.py` has to keep working if the library
# isn't importable. It is used for exactly one thing here -- deciding, AT
# THE MOMENT OF QUEUEING, whether the chosen device file can actually be
# turned into IR waveforms -- so without it the queue just goes back to
# being as permissive as it was.
try:
    import library  # noqa: E402
except Exception:  # noqa: BLE001
    library = None  # type: ignore[assignment]

# THE FACTORY TEMPLATE. Soft too, and also for exactly ONE reason: that
# a just-added device comes out of Sync with its rubber keys already
# bound, without the user having to ask. If it does not import, adding
# a device works exactly as before (with the page empty) and `sync_preparar`
# says so -- nothing is promised that is not going to happen.
try:
    import key_template  # noqa: E402
except Exception:  # noqa: BLE001
    key_template = None  # type: ignore[assignment]

#: Whitelist of change types -- the UI cannot invent a fifth one.
#: Same spirit as `generar.ACTIVITY_ACTIONS`.
TIPOS = frozenset(
    {"add_device", "remove_device", "edit_activity", "reassign_key"}
)


# ==========================================================================
# THE DICT-KEY CONTRACT, declared ONCE and verifiable
# ==========================================================================
# THE TRAP of this project, already paid for four times: a dict's keys
# are a contract -- between Python and JS, and between Python modules too --
# and until today that contract only lived inside the body of each
# `_paso_*()`, written as `p["config_json"]`. Whoever queued had no
# way of knowing which keys were needed, so a change that was missing
# one got into the list without complaint and blew up three layers
# later, inside the gate's path, where the error read as
# "the check did not pass".
#
# Now the keys each step reads are declared HERE, next to the
# `TIPOS` already declaring the allowed types, and `SesionCambios.add()`
# demands them AT QUEUE TIME -- the only moment when the error can still
# be explained in terms of what the user has just done.
#
# Rule for anyone adding a new `_paso_*()`: if the body of the step reads
# `p["algo"]`, `algo` goes in `required`. `app/check_contract.py`
# verifies it by reading the source code, so forgetting fails the check,
# no al usuario.
REQUISITOS: dict[str, dict[str, tuple[str, ...]]] = {
    # `config_json`: absolute path of the hub-config that carries the device.
    #   Named the SAME in `Api.catalog_local()`, in `Api.changes_add()`,
    #   in `Api.remote_generate()` and here. One single name, four places.
    # `name`: the label drawn ON THE REMOTE. It is not the name of the
    #   device in the hub-config (that one is `device`): they are two
    #   different things and that is why they have two different names.
    "add_device": {
        "required": ("config_json", "name"),
        "optional": ("device", "pages"),
    },
    "remove_device": {"required": ("k1",), "optional": ()},
    "edit_activity": {
        "required": ("ordinal", "accion"),
        "optional": ("argumento",),
    },
    # `reassign_key` has a CONDITIONAL block: what else is needed
    # depends on the `subtipo`. Those keys do not go here (not optional: they
    # are mandatory in their branch) but in `REQUISITOS_TECLA`, and
    # `parametros_faltantes()` is the only place that knows the rule.
    "reassign_key": {
        "required": ("codigo", "k1", "k2"),
        "optional": ("subtipo",),
    },
}

#: The ones added according to the `subtipo` of `reassign_key` -- an exact
#: copy of what `_step_keys()` reads BY INDEX in each branch. Mandatory in
#: their branch, absent in the other.
REQUISITOS_TECLA = {
    "fisica": ("contexto",),
    "screen": ("screen", "slot"),
    # the site that was missing: the device's own page (the header of the
    # trailer of its `table[6]`), which is where the factory binds each
    # device's rubber keys. Without `slot`: there is one header per
    # screen and it holds for its N pages.
    "device": ("screen",),
}


def parametros_faltantes(kind: str, parametros: dict | None) -> list[str]:
    """The mandatory keys that are NOT in `parametros`, in order.

    Empty = the corresponding step can read everything it needs. It does
    not validate the VALUES (each tool does that with its own criteria):
    it validates that the key contract is met.

    A `None` value or an empty string counts as absent: `name=""` is not
    a name, and letting it through is exactly how the label
    "Add '(unnamed)'" was reached.
    """
    req = REQUISITOS.get(kind)
    if req is None:
        return []
    p = parametros or {}
    required = list(req["required"])
    if kind == "reassign_key":
        sub = (p.get("subtipo") or "screen").strip()
        required += list(REQUISITOS_TECLA.get(sub, REQUISITOS_TECLA["screen"]))
    missing = []
    for k in required:
        v = p.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            missing.append(k)
    return missing


# ==========================================================================
# THE THREE CLASSES OF FAILURE -- which today the screen showed all alike
# ==========================================================================
# Deciding them is Python's job, not the JS's: the UI only has to paint
# what arrives. Like the mandatory texts in `api.py`, this cannot be
# softened by touching the HTML alone.
#
#   "compuerta"   -> `nothing_moved()` said NO. It is THE protection working:
#                    a byte moved that nobody declared. The right text is
#                    "the check did not pass: nothing is written".
#   "herramienta" -> a `config_work/` tool aborted on a check of its
#                    OWN (a glyph that no font draws, a k2 out of
#                    range, section [5] full). It is protection too,
#                    but of something else, and the reason can be explained.
#   "aplicacion"  -> the app broke (KeyError, TypeError, any Python
#                    exception). It is NOT protection, and saying that it
#                    is teaches distrust of the app just when it is working
#                    fine. The text has to say that the problem is the
#                    program's, not the remote's.
CATEGORY_GATE = "gate"
CLASE_HERRAMIENTA = "herramienta"
CATEGORY_APP = "aplicacion"

TEXTO_CLASE = {
    CATEGORY_GATE: (
        "The check did not pass: nothing will be written. Something in the "
        "file moved that nobody asked to move, so the write is refused."
    ),
    CLASE_HERRAMIENTA: (
        "This change can't be applied to your remote as it is. Nothing was "
        "written and nothing was left half-done."
    ),
    CATEGORY_APP: (
        "This is a bug in the app, not a problem with your remote. Nothing "
        "was written and your remote was never touched."
    ),
}


def _fail(category: str, reason: str, **extra) -> dict:
    """The only constructor of a failed step. That there is exactly one is
    what makes it impossible for a new path to forget to say which class
    its failure is and end up showing as if it were the gate."""
    d = {"ok": False, "category": category, "reason": reason, "stderr": reason}
    d.update(extra)
    return d


# ==========================================================================
# THE OTHER HALF OF THE CONTRACT: what goes out to the UI gets serialized
# ==========================================================================
def json_seguro(obj, _path_prefix: str = "") -> tuple[Any, list[str]]:
    """Returns `(copia serializable con json.dumps, [rutas convertidas])`.

    It exists because `config_work/` is pure Python and uses `set` with
    every right to (`fonts.choose_detail()["missing"]` is a `set`, and it
    is right that it should be), but EVERYTHING that crosses to the UI goes
    through `json.dumps` -- and a `set` in there does not give an ugly
    piece of data: it kills the whole call with "Object of type set is not
    JSON serializable", which is what the user ended up reading as if it
    were a security warning.

    It is NOT a cover-up: the list of converted paths comes back together
    with the data, `apply_all()` attaches it as `_convertido`, and the
    check in `app/check_contract.py` demands that it be EMPTY. That is:
    the app never falls over because of this, and whoever left the `set`
    finds out all the same.
    """
    convertidos: list[str] = []

    def _r(o, path):
        if isinstance(o, (str, int, float, bool)) or o is None:
            return o
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                kk = k if isinstance(k, str) else str(k)
                if kk is not k:
                    convertidos.append(
                        "%s.%s (clave %s)" % (path, kk, type(k).__name__)
                    )
                out[kk] = _r(v, "%s.%s" % (path, kk))
            return out
        if isinstance(o, (set, frozenset)):
            convertidos.append("%s (%s)" % (path, type(o).__name__))
            try:
                return [_r(x, path + "[]") for x in sorted(o)]
            except TypeError:  # elementos no comparables entre si
                return [_r(x, path + "[]") for x in o]
        if isinstance(o, (list, tuple)):
            return [_r(x, "%s[%d]" % (path, i)) for i, x in enumerate(o)]
        if isinstance(o, (bytes, bytearray)):
            convertidos.append("%s (%s)" % (path, type(o).__name__))
            return "<%d bytes>" % len(o)
        if isinstance(o, Path):
            convertidos.append("%s (Path)" % path)
            return str(o)
        convertidos.append("%s (%s)" % (path, type(o).__name__))
        return str(o)

    return _r(obj, _path_prefix or "$"), convertidos


# Same regex as `app/api.py::RE_REPUNTA` -- built out of what
# `delete_device.py`/`edit_activity.py` imprimen ("--repunta 0x...", ver sus
# own `print`s), not reinvented twice by accident: it is the same
# formato, definido aca de nuevo porque `changes.py` no importa `api.py`
# (evita el import circular: `api.py` va a importar `changes.py`).
RE_REPUNTA = re.compile(r"--repunta\s+(0x[0-9a-fA-F]+)")


def _why_not_possible(kind: str, parametros: dict | None) -> str | None:
    """`None` = se puede encolar. Un texto = el motivo EXACTO por el que no,
    ya redactado para mostrar.

    Un solo tipo se revisa hoy (`add_device`): es el unico cuyo
    insumo es un archivo de dispositivo que puede estar incompleto. Los
    otros tres operan sobre el blob que ya esta en el mando.

    NUNCA rechaza por una duda: si `library.py` no importa, si el
    diagnostico revienta, o si lo que falta no es un protocolo, deja pasar.
    Un falso rechazo aca bloquea a alguien que no hizo nada mal, que es peor
    que el problema que este chequeo resuelve.
    """
    if kind != "add_device" or library is None:
        return None
    p = parametros or {}
    path = p.get("config_json")
    if not path:
        return None
    try:
        d = library.diagnose(
            Path(path),
            device_name=p.get("device") or None,
            check_glyphs=False,  # depends on the blob; not this layer's business
        )
    except Exception:  # noqa: BLE001
        return None
    if d.get("aplicable") or d.get("missing_category") != "protocolo":
        return None
    return "this device can't be added yet: %s" % d.get("reason")


@dataclass
class Change:
    id: str
    kind: str
    label: str  # human ONE-line description, already computed when adding
    parametros: dict[str, Any]
    creado_en: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "parametros": self.parametros,
            "creado_en": self.creado_en,
        }


class SesionCambios:
    """In-memory accumulator. One instance hangs off `Api`
    (`self._changes`), not off a file: it empties itself if the app is
    closed, on purpose -- it is the list of "what I am going to apply IN
    THIS SESSION"."""

    def __init__(self) -> None:
        self._items: list[Change] = []

    def add(self, kind: str, label: str, parametros: dict) -> Change:
        if kind not in TIPOS:
            raise ValueError(
                "change type not allowed: %r (valid ones: %s)"
                % (kind, ", ".join(sorted(TIPOS)))
            )
        # THE CHECK THAT WAS MISSING. A change missing a key that its
        # `_paso_*()` is going to read does NOT enter the list: it is rejected
        # here, where the error can still be explained in terms of what the user
        # has just done ("the device file still has to be chosen"), instead
        # of blowing up in `apply_all()` with a `KeyError` that the Sync
        # screen showed as if it were the gate protecting them.
        missing = parametros_faltantes(kind, parametros)
        if missing:
            raise ValueError(
                "missing data for %s: %s (arrived: %s)"
                % (
                    kind,
                    ", ".join(missing),
                    ", ".join(sorted((parametros or {}).keys())) or "none",
                )
            )
        # THE SECOND CHECK THAT WAS MISSING, sibling of the one above: the keys
        # are all there, but the chosen FILE cannot be turned into IR
        # waveforms because nobody has the timing definition of its
        # protocol. Until today that got into the list without complaint and
        # recien se veia en Sync, donde `add_device.py` salta cada comando
        # with "protocol missing from the JSON" -- the user would add a
        # television and was not allowed to sync it, without a single word of
        # why.
        #
        # It only rejects on PROTOCOL, which is a fact of the file and does
        # not depend on which blob it is compared against. The other rejection
        # (los glifos) SI depende del blob de referencia, que `changes.py`
        # does not know; it is explained when it happens, in `_reason_generate()`.
        not_applicable_reason = _why_not_possible(kind, parametros)
        if not_applicable_reason:
            raise ValueError(not_applicable_reason)
        c = Change(
            id=uuid.uuid4().hex[:10],
            kind=kind,
            label=label,
            parametros=dict(parametros or {}),
        )
        self._items.append(c)
        return c

    def remove(self, id_: str) -> bool:
        before = len(self._items)
        self._items = [c for c in self._items if c.id != id_]
        return len(self._items) < before

    def vaciar(self) -> None:
        self._items = []

    def listar(self) -> list[Change]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)


# ==========================================================================
# aplicar_todos(): the blob -> blob chaining, one step per change (with the
# exception of the keys, which go grouped -- see the module docstring).
# ==========================================================================


def _read_repoints(stdout: str) -> list[int]:
    return sorted({int(x, 16) for x in RE_REPUNTA.findall(stdout or "")})


def _step_add_device(
    generate, device_module, blob_path: Path, c: Change, salida: Path
) -> dict:
    p = c.parametros
    # `config_json` and `name` are read by index, with no rescue `.get()`: if
    # they are missing, `SesionCambios.add()` already rejected the change
    # (`REQUISITOS["add_device"]`) y `apply_all()` lo revalida
    # before it got here. A `.get()` with a default would only cover up that
    # contrato se rompio y volveria a correr `add_device.py` con datos que
    # nadie eligio.
    config_json = Path(p["config_json"])
    name = p["name"].strip()
    device = p.get("device") or None
    try:
        index = len(device_module.read_section5(blob_path.read_bytes()))
    except ValueError as exc:
        # `read_section5()` raises ValueError when section [5] of the blob
        # does not add up. That is bad data, not the app broken: the tool
        # refused to guess, which is the right thing.
        return _fail(
            CLASE_HERRAMIENTA,
            "the device list ([5]) of the current configuration doesn't parse, "
            "so there is no way to know where the new device would go: %s" % exc,
        )
    except Exception as exc:  # noqa: BLE001
        # Any OTHER exception (TypeError, AttributeError, whatever) is the
        # app broken. Labeling it "herramienta" would be selling the user
        # a protection that nobody exercised -- the whole defect of this bug.
        return _fail(
            CATEGORY_APP,
            _runtime.reason(exc),
            traza=traceback.format_exc(),
        )
    res = generate.generate(
        blob_path,
        config_json,
        index=index,
        name=name,
        salida=salida,
        device=device,
    )
    res["repoints_int"] = _read_repoints(res.get("stdout", ""))
    res["left_out"] = _read_left_out(res.get("stdout", ""))
    # The `k1` it got. It comes out of here because this is the only place
    # that computes it, and the template step needs it: without it there is
    # no way to know which device the keys have to be bound to.
    res["index"] = index
    res["config_json"] = str(config_json)
    res["name_on_remote"] = name
    # WHICH of the file's devices. Without this the template has to guess
    # by the prefix of the menu name, and with a name like "Tele" it does
    # not match: measured, 32 keys were promised and 0 were bound.
    res["device_in_json"] = device
    if not res.get("ok"):
        # `generate()` never raises: it aborts with `ok=False` on a check of
        # its OWN (a label that no font draws, the file that does not exist,
        # existe, `add_device.py` que devolvio != 0). Eso es la clase
        # never the gate -- the gate has not run yet.
        res.setdefault("category", CLASE_HERRAMIENTA)
        res.setdefault("reason", _reason_generate(res, name, config_json, blob_path))
    return res


#: Lo que `config_work/add_device.py:3122` imprime cuando la tabla de
#: it learned from the chosen file is not enough for the fixed label
#: 'Devices'. It is a `SystemExit` with that exact text; untranslated, the
#: screen showed the phrase as it came, which tells nobody anything nor
#: suggests what to do.
RE_ROTULO_VOLVER = re.compile(r"return label '([^']+)' can't be written")

#: Los comandos que `add_device.py` DEJO AFUERA porque el mando no tiene los
#: glifos de su etiqueta. No es un fallo -- el dispositivo se agrega igual --
#: pero el usuario tiene que enterarse en Sync de que ese boton no va a estar,
#: y la unica forma de saberlo es la salida de la herramienta. Se parsea aca y
#: no se re-deduce: quien mide si una etiqueta se puede dibujar es el builder,
#: con las fuentes del blob delante.
RE_LEFT_OUT = re.compile(
    r"^\s+'([^']+)' \(label '([^']+)'\) needs (.+?) -- not in", re.M
)


def _read_left_out(stdout: str) -> list[dict]:
    return [
        {"command": cmd, "label": etq, "missing": missing}
        for cmd, etq, missing in RE_LEFT_OUT.findall(stdout or "")
    ]


def _reason_back_label(
    rotulo: str, config_json: Path | None, blob_path: Path | None
) -> str:
    """El motivo REAL detras de "return label 'Devices' can't be written",
    con la palabra culpable si se puede calcular.

    MEDIDO: el mando no guarda ASCII sino un indice de glifo por caracter, y
    `add_device.py` aprende esa tabla por eliminacion cruzando las palabras
    del archivo elegido contra el texto ya dibujado en el blob de
    referencia. Una palabra del archivo compatible con la misma cadena cruda
    que la verdadera vuelve la lectura ambigua y el glifo se DESCARTA. En
    un `hub-config-manual-*` contra el ancla, el unico responsable es la
    cadena 'Vol dn' -- una etiqueta que la propia app deriva del nombre de
    comando `Vol_dn`, no algo que haya tipeado nadie --: sacandola, la tabla
    vuelve a tener sus 61 glifos.

    Por eso tambien esto no se puede decidir al encolar: depende del blob de
    referencia, que cambia cada vez que se graba el mando. Un dispositivo
    importado hace dos dias podia estar bien cuando se importo y no estarlo
    hoy sin que nadie lo tocara.
    """
    base = (
        "this device file can't be added as it is: the remote learns which "
        "letter each glyph is by cross-referencing the words in the file "
        "against the text already on your remote, and with this file's "
        "words the fixed %r label that every device needs can't be written." % rotulo
    )
    if library is None or config_json is None or blob_path is None:
        return base
    try:
        blob = Path(blob_path).read_bytes()
        g = library.glyph_gate(Path(config_json), blob)
        culpables = g.get("palabras_conflictivas") or []
    except Exception:  # noqa: BLE001
        return base
    if not culpables:
        return base
    return base + (
        " What makes it ambiguous is the button label %s. Repairing that "
        "device in Catalog renames it and makes it usable again."
        % ", ".join(repr(w) for w in culpables)
    )


def _reason_generate(
    res: dict,
    name: str,
    config_json: Path | None = None,
    blob_path: Path | None = None,
) -> str:
    """Plain language (well, plain English: it is screen text) for the
    `ok=False` of `generar.generate()`. It comes out of what the tool
    already said -- no diagnosis is invented."""
    etq = res.get("labels") or {}
    faltantes = etq.get("faltantes") or []
    if faltantes:
        cuales = ", ".join(
            "%r" % (f[0] if isinstance(f, (list, tuple)) else f) for f in faltantes
        )
        return (
            "the remote's built-in fonts can't draw %s the way it was typed, "
            "so the name would come out wrong on the screen. Pick a different "
            "name (plain letters and digits are always safe)." % cuales
        )
    m = RE_ROTULO_VOLVER.search(res.get("stderr") or "")
    if m:
        return _reason_back_label(m.group(1), config_json, blob_path)
    if res.get("etapa") == "subproceso" and res.get("returncode") not in (0, None):
        return (
            "the tool that builds the new device (add_device.py) refused to "
            "finish for %r. The full output is below." % name
        )
    return (res.get("stderr") or "").strip() or (
        "the new device could not be built and the tool did not say why."
    )


def _step_delete_device(generate, blob_path: Path, c: Change, salida: Path) -> dict:
    k1 = int(c.parametros["k1"])
    res = generate.delete_device(blob_path, index=k1, salida=salida)
    res["repoints_int"] = _read_repoints(res.get("stdout", ""))
    if not res.get("ok"):
        res.setdefault("category", CLASE_HERRAMIENTA)
        res.setdefault(
            "reason",
            (res.get("stderr") or "").strip()
            or "the tool that removes a device (delete_device.py) refused to finish.",
        )
    return res


def _step_edit_activity(generate, blob_path: Path, c: Change, salida: Path) -> dict:
    p = c.parametros
    res = generate.edit_activity(
        blob_path,
        ordinal=int(p["ordinal"]),
        accion=p["accion"],
        argumento=p.get("argumento"),
        salida=salida,
    )
    res["repoints_int"] = _read_repoints(res.get("stdout", ""))
    if not res.get("ok"):
        res.setdefault("category", CLASE_HERRAMIENTA)
        res.setdefault(
            "reason",
            (res.get("stderr") or "").strip()
            or "the tool that edits an activity (edit_activity.py) refused "
            "to finish.",
        )
    return res


# ==========================================================================
# THE FACTORY TEMPLATE, applied on its own to what was just added
# ==========================================================================
# THE REQUEST, verbatim: "it would be good if each device, when I set it
# up, self-assigned the buttons that go with it, so when you enter each
# dispositivo todo funciona".
#
# WHY IT GOES HERE AND NOT AT QUEUE TIME: when the user adds a
# device, that device DOES NOT EXIST YET in the blob -- it has no `k1`
# and no page of its own, and the page is exactly where the writing goes.
# despues de que `add_device.py` corrio hay algo que atar. Asi que el plan
# plan is computed on the blob that adding the device left, in the same
# chain, and the resulting keys come in by the SAME path as a hand-set key
# (`keys_physical.apply_device` + `teclas_alcance.checks`): no
# second way of writing keys that could be laxer exists.
def _plantilla_de_altas(
    blob_bytes: bytes,
    altas: list[dict],
    ya_encoladas: set[tuple[int, int]],
    *,
    keys_map=None,
    keys_physical=None,
    device_module=None,
) -> tuple[list[Change], list[dict]]:
    """`(changes sinteticos, informe por device)`.

    `altas`: `[{'k1', 'config_json', 'name', 'change_id'}]` -- one per
    `add_device` that went fine in this chain.
    `ya_encoladas`: `(screen, codigo)` that the user already set by hand
    in Sync. Those are NOT queued again: `apply_device()` rejects
    the whole batch if a key appears twice, and besides, what the user
    chose wins.
    """
    autos: list[Change] = []
    informe: list[dict] = []
    for alta in altas:
        k1 = alta.get("k1")
        name = alta.get("name") or ("device %s" % k1)
        if key_template is None:
            informe.append(
                {
                    "k1": k1,
                    "name": name,
                    "ok": False,
                    "n_ligadas": 0,
                    "error": "key_template.py did not import, so the new "
                    "device's keys were not bound. Nothing else changed.",
                }
            )
            continue
        # THE NAMES come out of the SAME file the device was added
        # with, and from the EXACT device that was chosen inside it. There
        # is nowhere else to get them from: the blob stores IR waveforms, not
        # nombres de comando.
        nombres, err_nombres = (None, None)
        if alta.get("config_json"):
            nombres, err_nombres = key_template.nombres_de_json(
                alta["config_json"], alta.get("device")
            )
            if err_nombres:
                nombres = None
        try:
            plan = key_template.device_plan(
                blob_bytes,
                k1,
                hub=[alta["config_json"]] if alta.get("config_json") else None,
                nombres=nombres,
                keys_map=keys_map,
                keys_physical=keys_physical,
                device_module=device_module,
            )
        except Exception as exc:  # noqa: BLE001
            informe.append(
                {
                    "k1": k1,
                    "name": name,
                    "ok": False,
                    "n_ligadas": 0,
                    "error": _runtime.reason(exc),
                }
            )
            continue
        if not plan.get("ok") or not plan.get("plan_posible"):
            informe.append(
                {
                    "k1": k1,
                    "name": plan.get("name") or name,
                    "ok": False,
                    "n_ligadas": 0,
                    "error": err_nombres
                    or plan.get("error")
                    or plan.get("no_plan_reason"),
                }
            )
            continue
        puestos = 0
        for c in plan.get("changes") or []:
            if (c["screen"], c["codigo"]) in ya_encoladas:
                continue
            row = next((f for f in plan["rows"] if f["codigo"] == c["codigo"]), {})
            autos.append(
                Change(
                    id="auto%d_%02x" % (int(k1), int(c["codigo"])),
                    kind="reassign_key",
                    label="Key %s -> %s (%s, automatic)"
                    % (
                        row.get("key") or "%#04x" % c["codigo"],
                        row.get("command") or "command %d" % c["k2"],
                        plan["name"],
                    ),
                    parametros={
                        "subtipo": "device",
                        "screen": c["screen"],
                        "codigo": c["codigo"],
                        "k1": c["k1"],
                        "k2": c["k2"],
                    },
                )
            )
            puestos += 1
        informe.append(
            {
                "k1": k1,
                "name": plan["name"],
                "ok": True,
                "screen": plan["screen"],
                "origin": plan.get("origin"),
                "n_ligadas": puestos,
                "n_ya_tuyas": len(plan.get("changes") or []) - puestos,
                "roles_totales": plan.get("roles_totales"),
                "no_command": [
                    {"key": f["key"], "rol": f["rol"]}
                    for f in plan.get("no_command") or []
                ],
                "respetadas": [
                    {"key": f["key"], "rol": f["rol"]}
                    for f in plan.get("respetadas") or []
                ],
                "summary": plan.get("summary"),
            }
        )
    return autos, informe


def _step_keys(
    keys_map, keys_physical, blob_bytes: bytes, key_changes: list[Change]
) -> dict:
    """Groups ALL the pending `reassign_key` (no matter in what order
    they were added relative to the other types) and applies them in ONE
    single pass -- a copy of `Api.keys_apply()`, generalized from there."""
    screen_changes, fisica_c, device_changes, change_ids = [], [], [], []
    for c in key_changes:
        p = c.parametros
        sub = p.get("subtipo") or "screen"
        if sub == "device":
            device_changes.append(
                {
                    "screen": int(p["screen"]),
                    "codigo": int(p["codigo"]),
                    "k1": int(p["k1"]),
                    "k2": int(p["k2"]),
                }
            )
        elif sub == "fisica":
            fisica_c.append(
                {
                    "contexto": int(p["contexto"]),
                    "codigo": int(p["codigo"]),
                    "k1": int(p["k1"]),
                    "k2": int(p["k2"]),
                }
            )
        else:
            screen_changes.append(
                {
                    "screen": int(p["screen"]),
                    "slot": int(p["slot"]),
                    "codigo": int(p["codigo"]),
                    "k1": int(p["k1"]),
                    "k2": int(p["k2"]),
                }
            )
        change_ids.append(c.id)

    fresh = blob_bytes
    repuntes: list[int] = []
    detail: list[dict] = []
    chequeos: list[dict] = []
    # The functions travel by REFERENCE, not by name. With `getattr(modulo,
    # "aplicar_dispositivo")` the name travels as a STRING, and no check sees
    # a string: not the import sweep, not the type checker, not the export
    # rename -- which renames the function and leaves the string alone.
    # Measured on the published repo: `keys_physical` exports
    # `apply_device` and this file asked it for `apply_device`, so
    # all three reassignment branches died with `AttributeError` halfway
    # through Sync. With the direct reference, if the name changes the error
    # shows up at import time, not when the user presses Sync.
    for label, lote, apply, controlar in (
        ("screen", screen_changes, keys_map.apply, keys_map.checks),
        (
            "fisica",
            fisica_c,
            keys_physical.apply_physical,
            keys_physical.checks,
        ),
        (
            "device",
            device_changes,
            keys_physical.apply_device,
            keys_physical.device_checks,
        ),
    ):
        if not lote:
            continue
        previo = fresh
        try:
            fresh, rep, det = apply(previo, lote)
        except Exception as exc:  # noqa: BLE001
            # `keys_map.apply()` raises `ValueError` for ITS OWN checks
            # (a k2 out of range, a slot that does not exist) -- that is the
            # tool protecting, not the app broken. Any other
            # exception really is the app.
            return _fail(
                CLASE_HERRAMIENTA if isinstance(exc, ValueError) else CATEGORY_APP,
                "the %s key block was refused: %s" % (label, exc),
                changes=change_ids,
                traza=traceback.format_exc(),
            )
        try:
            ch = controlar(previo, fresh, det, rep)
        except Exception as exc:  # noqa: BLE001
            return _fail(
                CATEGORY_APP,
                "the checks for the %s key block could not be run: %s"
                % (label, exc),
                changes=change_ids,
                traza=traceback.format_exc(),
            )
        for c2 in ch:
            c2["name"] = "[%s] %s" % (label, c2["name"])
        chequeos += ch
        repuntes += list(rep)
        detail += [dict(d, kind=label) for d in det]

    # THE CHECK THAT WAS MISSING, on the final blob and all three sites at once:
    # walk the pointers the firmware walks, starting from the master index,
    # and read what is there at the end of the walk. Every other check
    # looks at the record that was EDITED; write #7 proved that a record
    # can end up edited, verified, and not be the one the remote reaches.
    if detail:
        try:
            import keys_reach

            chequeos += keys_reach.checks(blob_bytes, fresh, detail)
        except Exception as exc:  # noqa: BLE001
            return _fail(
                CATEGORY_APP,
                "the reachability check could not be run: %s" % exc,
                changes=change_ids,
                traza=traceback.format_exc(),
            )

    if not all(c["ok"] for c in chequeos):
        return _fail(
            CLASE_HERRAMIENTA,
            "the key block's own checks did not all come back green, so the "
            "reassignment was not applied.",
            checks=chequeos,
            changes=change_ids,
        )
    return {
        "ok": True,
        # `blob_bytes` goes out under a key that `apply_all()` STRIPS before
        # putting this into `steps[].technical_detail`. It lived as plain `blob_bytes`
        # and traveled whole to the UI: ~100 KB of `bytes` that
        # `json.dumps` does not serialize -- the same defect as the `set`, with
        # another type. See `_sin_carga()`.
        "blob_bytes": fresh,
        "repoints_int": sorted(set(repuntes)),
        "detail": detail,
        "checks": chequeos,
        "changes": change_ids,
    }


def _sin_carga(res: dict) -> dict:
    """The copy of a step result that CAN cross to the UI: without the
    whole blob inside. What the screen needs to know about the blob is its
    size, not its 100 KB."""
    limpio = dict(res)
    b = limpio.pop("blob_bytes", None)
    if b is not None:
        limpio["blob_tamano"] = len(b)
    return limpio


def _apply_all_raw(
    changes: list[Change],
    blob_inicial: Path,
    salida_dir: Path,
    *,
    plantilla: Path | None = None,
    prefijo: str = "sync",
    generate=None,
    device_module=None,
    keys_map=None,
    keys_physical=None,
) -> dict:
    """Applies ALL the `changes` on top of `blob_inicial`, chained, and
    returns ONE combined result. It does not write to the device.

    The modules (`generar`, `device_module`, `keys_map`,
    `keys_physical`) are passed as parameters instead of being imported up
    here: they are the SAME objects `Api` already has loaded (via
    `_soft_import`), so if one of them did not import in `Api` (a missing
    optional dependency) that same absence is respected here, instead of
    this module trying its own import and covering the problem up.

    Order of application: `add_device` / `remove_device` /
    `edit_activity`, IN THE ORDER they sit in `changes` (each one starts
    from the `.bin` the previous one left); the `reassign_key`, no
    matter where they are interleaved, are grouped and applied together AT
    THE END of that chain (same reason as `keys_apply`: reassigning one key
    at a time would recompute `reubicar.sections()` once per key instead of
    once per batch).

    It stops at the FIRST step that fails (fail-fast: nothing is written
    anywhere until the final gate, so there is no "half applied" state to
    clean up -- what there is are temporary files in `salida_dir` that can
    be deleted without guilt).

        {'ok': bool,
         'fallo_en': id of the change that failed | None,
         'pasos': [{'cambio_id', 'tipo', 'ok', 'detalle_tecnico'}, ...],
         'blob_final': str | None,
         'repuntes_int': [...],
         'compuerta': generar.preview_gate()-dict | None}

    KNOWN LIMITATION, left unresolved on purpose for lack of a measured use
    case: mixing `add_device` and `remove_device` in the SAME
    batch was not tested -- `remove_device` references a `k1` that has
    to stay valid after the previous steps of the same batch. If the UI
    allows both things at once, it is better to resolve the `k1` of the
    deletion BEFORE building the change list (against the real blob), not
    at apply time.
    """
    if generate is None:
        return {
            "ok": False,
            "category": CATEGORY_APP,
            "fallo_en": None,
            "steps": [],
            "error": "generate.py no importa",
        }

    blob_inicial = Path(blob_inicial)
    if not blob_inicial.exists():
        return {
            "ok": False,
            "category": CATEGORY_APP,
            "fallo_en": None,
            "steps": [],
            "error": "the initial blob does not exist: %s" % blob_inicial,
        }

    # THE DICT-KEY CONTRACT, re-validated over the WHOLE list BEFORE touching
    # a single blob. `SesionCambios.add()` already demanded it at queue
    # time; this covers whoever builds the list without going through there
    # (a script, a test, a `Change` put together by hand) and, above all, it
    # makes the error come out as "a piece of data is missing", not a `KeyError`
    # halfway down the chain, which is what the Sync screen showed as if it were
    # compuerta.
    for c in changes:
        missing = parametros_faltantes(c.kind, c.parametros)
        if missing:
            return {
                "ok": False,
                "category": CATEGORY_APP,
                "fallo_en": c.id,
                "steps": [],
                "blob_final": None,
                "repoints_int": [],
                "gate": None,
                "error": (
                    "the queued change %r (%s) is missing the data its step "
                    "reads: %s. Nothing was built."
                    % (c.label, c.kind, ", ".join(missing))
                ),
            }

    salida_dir = Path(salida_dir)
    salida_dir.mkdir(parents=True, exist_ok=True)
    marca = time.strftime("%Y%m%d_%H%M%S")

    resto = [c for c in changes if c.kind != "reassign_key"]
    keys = [c for c in changes if c.kind == "reassign_key"]

    blob_actual = blob_inicial
    steps: list[dict] = []
    total_repoints: list[int] = []
    #: the `add_device` that went fine, with the `k1` each one got.
    #: It is collected here because `_step_add_device()` is the only
    #: one that computes it, and the template needs it afterwards.
    altas: list[dict] = []

    for c in resto:
        salida = salida_dir / f"{prefijo}_{marca}_{c.id}.bin"
        # Each step runs inside its own try. A Python exception is NOT the
        # gate rejecting: it is the app broken, and it comes out labeled as
        # such (`CATEGORY_APP` + the traceback), so that the screen can
        # say "this is an error in the application, not a problem with your
        # remote" instead of claiming a protection it never exercised.
        try:
            if c.kind == "add_device":
                if device_module is None:
                    res = _fail(
                        CATEGORY_APP,
                        "add_device.py did not import, so no device can be added.",
                    )
                else:
                    res = _step_add_device(
                        generate, device_module, blob_actual, c, salida
                    )
            elif c.kind == "remove_device":
                res = _step_delete_device(generate, blob_actual, c, salida)
            elif c.kind == "edit_activity":
                res = _step_edit_activity(generate, blob_actual, c, salida)
            else:  # pragma: no cover -- TIPOS already filtered it at agregar()
                res = _fail(
                    CATEGORY_APP, "tipo de cambio desconocido: %r" % c.kind
                )
        except Exception as exc:  # noqa: BLE001
            res = _fail(
                CATEGORY_APP,
                _runtime.reason(exc),
                traza=traceback.format_exc(),
            )

        steps.append(
            {
                "change_id": c.id,
                "kind": c.kind,
                "label": c.label,
                "ok": bool(res.get("ok")),
                "category": res.get("category"),
                "reason": res.get("reason"),
                "left_out": res.get("left_out") or [],
                "technical_detail": _sin_carga(res),
            }
        )
        if not res.get("ok"):
            return {
                "ok": False,
                "category": res.get("category") or CATEGORY_APP,
                "error": res.get("reason") or res.get("stderr") or "",
                "fallo_en": c.id,
                "steps": steps,
                "blob_final": None,
                "repoints_int": total_repoints,
                "gate": None,
            }
        total_repoints += res.get("repoints_int") or []
        blob_actual = Path(salida)
        if c.kind == "add_device" and res.get("index") is not None:
            altas.append(
                {
                    "change_id": c.id,
                    "k1": res["index"],
                    "config_json": res.get("config_json"),
                    "device": res.get("device_in_json"),
                    "name": res.get("name_on_remote"),
                }
            )

    blob_bytes = blob_actual.read_bytes()

    # THE TEMPLATE: what was just added comes out with its keys bound.
    # The synthetic changes go BEFORE the user's in the same list: if
    # the user already touched that key by hand, theirs is the one that
    # stays (it is filtered by `(screen, codigo)`, and besides
    # `apply_device()` rejects the batch if a key comes twice).
    plantilla_informe: list[dict] = []
    if altas:
        ya = set()
        for c in keys:
            p = c.parametros or {}
            if (p.get("subtipo") or "screen") == "device":
                try:
                    ya.add((int(p["screen"]), int(p["codigo"])))
                except Exception:  # noqa: BLE001
                    pass
        try:
            autos, plantilla_informe = _plantilla_de_altas(
                blob_bytes,
                altas,
                ya,
                keys_map=keys_map,
                keys_physical=keys_physical,
                device_module=device_module,
            )
        except Exception as exc:  # noqa: BLE001
            # The template failing CANNOT knock down the add: the device
            # is added all the same, with the page as it was, and it is said so.
            autos = []
            plantilla_informe = [
                {
                    "k1": a.get("k1"),
                    "name": a.get("name"),
                    "ok": False,
                    "n_ligadas": 0,
                    "error": _runtime.reason(exc),
                }
                for a in altas
            ]
        keys = autos + keys

    if keys:
        try:
            if keys_map is None or keys_physical is None:
                res = _fail(
                    CATEGORY_APP,
                    "keys_map.py/keys_physical.py did not import, so no key "
                    "can be reassigned.",
                )
            else:
                res = _step_keys(keys_map, keys_physical, blob_bytes, keys)
        except Exception as exc:  # noqa: BLE001
            res = _fail(
                CATEGORY_APP,
                _runtime.reason(exc),
                traza=traceback.format_exc(),
            )
        steps.append(
            {
                "change_id": None,
                "kind": "reasignar_tecla (lote de %d)" % len(keys),
                "label": "%d tecla(s) reasignada(s)" % len(keys),
                "ok": bool(res.get("ok")),
                "category": res.get("category"),
                "reason": res.get("reason"),
                # WITHOUT the ~100 KB blob inside: `technical_detail` crosses to
                # the UI through `json.dumps` and `bytes` does not serialize.
                "technical_detail": _sin_carga(res),
            }
        )
        if not res.get("ok"):
            return {
                "ok": False,
                "category": res.get("category") or CATEGORY_APP,
                "error": res.get("reason") or res.get("stderr") or "",
                "fallo_en": (res.get("changes") or [None])[0],
                "steps": steps,
                "blob_final": None,
                "repoints_int": total_repoints,
                "gate": None,
                "key_template": plantilla_informe,
            }
        blob_bytes = res["blob_bytes"]
        total_repoints += res.get("repoints_int") or []
        salida = salida_dir / f"{prefijo}_{marca}_teclas.bin"
        salida.write_bytes(blob_bytes)
        blob_actual = salida

    total_repoints = sorted(set(total_repoints))
    blob_final = salida_dir / f"{prefijo}_{marca}_final.bin"
    blob_final.write_bytes(blob_bytes)

    ezhex_final = None
    if plantilla is not None and Path(plantilla).exists():
        ezhex_final = salida_dir / f"{prefijo}_{marca}_final.EZHex"
        arm = subprocess.run(  # noqa: S603 -- ezhex.py: no USB, no network
            [
                *_runtime.interprete(),
                "ezhex.py",
                "armar",
                str(plantilla),
                str(blob_final),
                str(ezhex_final),
            ],
            cwd=str(CONFIG_WORK),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if arm.returncode != 0 or not ezhex_final.exists():
            return {
                "ok": False,
                "category": CLASE_HERRAMIENTA,
                "fallo_en": None,
                "steps": steps,
                "blob_final": str(blob_final),
                "repoints_int": total_repoints,
                "gate": None,
                "key_template": plantilla_informe,
                "error": "the final .EZHex could not be assembled: %s"
                % (arm.stderr or arm.stdout),
            }

    gate = generate.preview_gate(
        blob_inicial.read_bytes(), blob_final.read_bytes(), total_repoints
    )

    return {
        # `ok` on THIS envelope = "the steps ran". Whether the GATE came back
        # green is `gate.ok`, and they are two different questions: that is
        # why `category` comes out as CLASE_COMPUERTA when the only thing that failed
        # was the gate. (It is the same `ok` collision that already bit once with
        # the snapshot's `ok` against the envelope's `ok`.)
        "ok": True,
        "category": None if (gate or {}).get("ok") else CATEGORY_GATE,
        "fallo_en": None,
        "steps": steps,
        "blob_final": str(blob_final),
        "ezhex_final": str(ezhex_final) if ezhex_final else None,
        "repoints_int": total_repoints,
        "gate": gate,
        # WHAT GOT BOUND BY ITSELF, and what did not: one entry per device added
        # in this run. The Sync screen shows it BEFORE writing, so that
        # nobody has to find out by pressing which key is missing.
        "key_template": plantilla_informe,
    }


def apply_all(*args, **kw) -> dict:
    """`_apply_all_raw()` + THE TWO EDGE GUARANTEES.

    Everything this function returns crosses to the UI through `json.dumps`
    (in pywebview, with no `default=`), so the two ways of breaking that
    border are closed HERE, in a single place, and not at every `return` of
    the hundreds of lines above:

      1. **No exception escapes.** If something blows up anyway, it comes
         back as a normal envelope with `category="aplicacion"` and the
         traceback inside. The screen can say "this is an error in the
         application, not a problem with your remote" -- which is the truth
         -- instead of pinning on the user a gate that never ran.
      2. **Nothing goes out that `json.dumps` cannot serialize.** `set`,
         `bytes`, `Path`: they get converted, and whatever had to be
         converted comes back listed in `_convertido` so the check can see
         it. A loose `set` killed the whole call with "Object of type set
         is not JSON serializable" -- the message the user read as if it
         were a security warning.
    """
    try:
        r = _apply_all_raw(*args, **kw)
    except Exception as exc:  # noqa: BLE001
        r = {
            "ok": False,
            "category": CATEGORY_APP,
            "fallo_en": None,
            "steps": [],
            "blob_final": None,
            "repoints_int": [],
            "gate": None,
            "error": _runtime.reason(exc),
            "traza": traceback.format_exc(),
        }
    seguro, convertidos = json_seguro(r)
    if convertidos:
        seguro["_convertido"] = convertidos
    return seguro


if __name__ == "__main__":
    # END-TO-END console check, offline: it chains TWO device
    # additions (Philips + LG, the same two that
    # `app/check_load_bearing.py` uses as a regression anchor) through
    # `SesionCambios` + `apply_all()`, and demands the SAME final md5 that
    # that anchor already declares verified on the device -- if this gives the
    # same md5 as `check_load_bearing.py`, it means that chaining through
    # `apply_all()` is EQUIVALENT to chaining by hand the way
    # `Api.remote_apply()` called twice does. It touches USB at no
    # momento (`generar.generate` corre `add_device.py` por subprocess,
    # which does not import libconcord).
    import hashlib
    import tempfile

    ROOT = Path(__file__).resolve().parent.parent
    BACKUPS = ROOT / "backups"
    OUTPUT = ROOT / "account_export" / "output"
    EXPECTED_MD5 = "976bc70edd15b40f56cb49aa5113594f"

    sys.path.insert(0, str(CONFIG_WORK))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import add_device as dispositivo_mod  # noqa: E402
    import generate  # noqa: E402

    fallas = []
    with tempfile.TemporaryDirectory(prefix="cambios_selftest_") as tmp:
        tmp = Path(tmp)
        sesion = SesionCambios()
        sesion.add(
            "add_device",
            "Agregar 'Philips'",
            {
                "config_json": str(
                    OUTPUT
                    / "hub-config-tv-a"
                    / "hub-config-with-device.json"
                ),
                "name": "Philips",
            },
        )
        sesion.add(
            "add_device",
            "Agregar 'LG'",
            {
                "config_json": str(
                    OUTPUT / "hub-config-tv-b" / "hub-config-with-device.json"
                ),
                "name": "LG",
                "device": "LG TV",
            },
        )
        print("changes in the session:")
        for c in sesion.listar():
            print("  -", c.label, c.parametros)

        r = apply_all(
            sesion.listar(),
            BACKUPS / "config_raw.bin",
            tmp,
            plantilla=BACKUPS / "one_20260724_210614_a.EZHex",
            generate=generate,
            device_module=dispositivo_mod,
        )
        print("\nok=%s  fallo_en=%s" % (r.get("ok"), r.get("fallo_en")))
        # What `apply_all()` returns crosses to the UI through `json.dumps`
        # WITHOUT `default=` (that is how pywebview calls it). It is tested with
        # the real `json.dumps`, which is what was failing: a `set` inside did
        # not give ugly data, it killed the whole call with "Object of type set
        # is not JSON serializable".
        try:
            json.dumps(r)
        except TypeError as exc:
            fallas.append("aplicar_todos() returned something not serializable: %s" % exc)
        if r.get("_convertido"):
            fallas.append(
                "types that are not JSON had to be converted in the output: %s "
                "(the fix goes at the source, not at the edge)" % r["_convertido"]
            )
        for p in r.get("steps", []):
            print("  paso %s (%s): ok=%s" % (p["change_id"], p["kind"], p["ok"]))
        if not r.get("ok"):
            print("error:", r.get("error"))
            for p in r.get("steps", []):
                if not p["ok"]:
                    dt = p["technical_detail"]
                    print(
                        "  stderr of the step that failed:", (dt.get("stderr") or "")[-2000:]
                    )
            fallas.append("aplicar_todos() no dio ok=True")
        else:
            md5 = hashlib.md5(Path(r["blob_final"]).read_bytes()).hexdigest()
            print("md5 final:     ", md5)
            print("esperado (ancla):", EXPECTED_MD5)
            if md5 != EXPECTED_MD5:
                fallas.append(
                    "md5 does NOT match the anchor: chaining through aplicar_todos() "
                    "did not reproduce the same thing as control_apply() called twice"
                )
            print("compuerta:", r["gate"])
            if not r["gate"]["ok"]:
                fallas.append("the final gate did not give paso=True")

        # NEGATIVE: taking a change out of the list has to really remove it.
        before = len(sesion)
        alguno = sesion.listar()[0]
        ok_quitar = sesion.remove(alguno.id)
        if not ok_quitar or len(sesion) != before - 1:
            fallas.append("quitar() did not take the change out of the list")
        if sesion.remove("no-such-id-exists"):
            fallas.append(
                "NEGATIVE FAILED: quitar() with a nonexistent id returned True"
            )

        # NEGATIVE: a type outside the whitelist has to be rejected.
        try:
            sesion.add("wipe_the_whole_remote", "no", {})
            fallas.append("NEGATIVE FAILED: a type that is not allowed did not raise ValueError")
        except ValueError:
            pass

    print()
    if fallas:
        print("SELFTEST: FAILED")
        for f in fallas:
            print("  -", f)
        raise SystemExit(1)
    print("SELFTEST: PASSED (offline, sin USB, md5 identico al ancla verificada).")
