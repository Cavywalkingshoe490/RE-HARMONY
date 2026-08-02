#!/usr/bin/env python3
"""LOAD-BEARING CHECK for the ADD path: the contract of dict keys, and that
everything crossing to the UI really survives `json.dumps`.

WHY THIS FILE EXISTS. The add-a-device screen was broken by a single
disagreement over a name: `Api.catalog_local()` published the path under
`json`, and `changes._step_add_device()` read it as `config_json`.
Nothing caught it -- not the type checker (both are `dict`), not the other
controls (they build their parameters by hand, so they always agree with
themselves) -- and the user found out three layers later, in the Sync
screen, in the form of a `KeyError` shown as "the check did not pass".

That was the fourth time the same class of bug landed in this project
("tramas" renamed on one side only, `d.get("validos")` pointing at a dead
key, the snapshot's `ok` colliding with the envelope's `ok`, and now this).
So this control does not check that ONE bug is gone: it checks the two
invariants whose violation produces that whole class.

  A. **Every key a step reads is declared.** Read out of the SOURCE of
     `app/changes.py` with `ast`: every `p["x"]` inside a `_paso_*()` has to
     appear in `changes.REQUISITOS[kind]`. Add a step that reads a new key
     without declaring it and this fails -- before a user does.
  B. **Every producer of a declared key uses the declared name.** The
     concrete pair that broke: `catalog_local()` has to publish
     `config_json`, and its legacy `json` alias -- which `app/ui/app.js`
     still reads and this session may not touch -- has to hold the SAME
     value, so the two names cannot drift while both exist.
  C. **Nothing reaches the UI that `json.dumps` refuses.** With the real
     `json.dumps`, no `default=` -- which is exactly what failed
     ("Object of type set is not JSON serializable"): the headless HTTP
     bridge of `main.py` passes `default=str` and so it hid the defect,
     while pywebview does not.
  D. **The three classes of failure are told apart.** The gate rejecting,
     a tool aborting on a check of its own, and the app crashing must NOT
     come out looking the same. An app crash reported as "the check
     protected you" teaches the user to distrust the app when it works and
     to ignore the message when it really protects.
  E. **End to end**: queue a device, `sync_preparar()`, and get `ready=True`
     with the gate green.

WHICH DEVICE. Any of them: what is under test is the contract between the
keys, not what appliance it is. It takes the FIRST one in the local catalog,
and if the catalog is empty -- which is the state of a freshly cloned repo --
it builds one on the spot from the example `.ir` this repo ships
(`app/tests/ejemplo_tv.ir`), and deletes it again on the way out. If it cannot
even do that, it SKIPS with the reason and `rc=2` instead of going red: a
check that fails because the machine is missing an input teaches whoever
reads it to ignore it.

NEVER touches USB: it only calls the read-only side of `Api`, and the two
generators it exercises (`generar.generate` / `preview_gate`) run
`add_device.py` by subprocess and `grabar.nothing_moved()` in-process.
Nothing here writes to the remote. The only thing it can write to disk is
that throwaway device folder, and only when there is no other one.

Usage:
    app/.venv/bin/python app/check_contract.py
    # rc 0 = PASSED, 1 = FAILED, 2 = SKIPPED (and it says what is missing)
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(ROOT / "config_work"))

# Read-only for the whole run: this control never has a reason to write.
os.environ.setdefault("RE_HARMONY_SOLO_LECTURA", "1")
os.environ.setdefault("RE_HARMONY_HEADLESS", "1")

import changes  # noqa: E402
import generate  # noqa: E402

#: The example `.ir` this repo ships. It is what BUILDS the throwaway
#: test device when the local catalog is empty, which is how every clone
#: arrives: this way the check depends on nobody's appliance.
EXAMPLE_IR = APP / "tests" / "ejemplo_tv.ir"
TEST_MAKER = "Acme"
TEST_MODEL = "Control Contrato"
#: The label that would be drawn on the remote. Short and with no odd
#: letters: what is under test here is the key contract, not typography.
TEST_LABEL = "Test"
#: How the folder name `ir_manual.import_device()` writes begins.
#: It is checked before deleting anything. On purpose it does NOT carry
#: the maker or the model above: a guard that repeats those two strings
#: falls out of sync the day somebody changes them, and then stops
#: recognising the folder it just created itself, and deletes nothing.
TEST_PREFIX = "hub-config-manual-"

MISSING_REFERENCE = (
    "there is no reference configuration to generate against. "
    "Corre `first_run.py` con el mando enchufado: deja tu linea base "
    "en backups/config_raw.bin"
)

FAILURES: list[str] = []
SKIPS: list[str] = []


def fail(msg: str) -> None:
    FAILURES.append(msg)
    print("  FALLA:", msg)


def skip(msg: str) -> None:
    """Ni PASO ni FALLO: no se pudo correr, y se dice por que.

    Es lo mismo que hacen los otros controles publicados (`rc=2`, que
    `check.py` lee como SALTEADO). Un control que se pone rojo porque a
    la maquina le falta un insumo enseña a ignorarlo, y despues nadie lo
    mira el dia que se pone rojo de verdad."""
    SKIPS.append(msg)
    print("  SALTEADO:", msg)


def strict_dumps(obj, where: str) -> bool:
    """`json.dumps` FOR REAL: no `default=`, which is how pywebview
    calls it. With `default=str` this passed every time, and that is why
    the defect survived every headless test."""
    try:
        json.dumps(obj)
        return True
    except TypeError as exc:
        fail("%s no es JSON-serializable: %s" % (where, exc))
        return False


# ==========================================================================
# A. Every key a step READS is declared in REQUISITOS
# ==========================================================================
#: `_paso_*` -> the change type whose requirements it governs.
STEP_TO_KIND = {
    "_step_add_device": "add_device",
    "_step_delete_device": "remove_device",
    "_step_edit_activity": "edit_activity",
    "_step_keys": "reassign_key",
}


def keys_read(fn: ast.FunctionDef) -> tuple[set[str], set[str]]:
    """`({keys by index}, {keys by .get()})` that the body of `fn`
    reads out of a parameter dict.

    It only looks at subscripts/`.get()` on the names that in these steps
    ARE the parameter dict (`p`, or `c.parametros` straight). It does not
    claim to be a general analyser: it claims to be exact for the shape
    these four steps already have, and to break loudly if anybody changes
    that shape."""
    duras: set[str] = set()
    blandas: set[str] = set()

    def is_params(nodo) -> bool:
        if isinstance(nodo, ast.Name) and nodo.id == "p":
            return True
        return isinstance(nodo, ast.Attribute) and nodo.attr == "parametros"

    for n in ast.walk(fn):
        if (
            isinstance(n, ast.Subscript)
            and is_params(n.value)
            and isinstance(n.slice, ast.Constant)
            and isinstance(n.slice.value, str)
        ):
            duras.add(n.slice.value)
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "get"
            and is_params(n.func.value)
            and n.args
            and isinstance(n.args[0], ast.Constant)
            and isinstance(n.args[0].value, str)
        ):
            blandas.add(n.args[0].value)
    return duras, blandas


def check_a() -> None:
    print("\nA. the keys each step reads are declared in REQUISITOS")
    arbol = ast.parse((APP / "changes.py").read_text(encoding="utf-8"))
    vistos = set()
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.FunctionDef) or nodo.name not in STEP_TO_KIND:
            continue
        kind = STEP_TO_KIND[nodo.name]
        vistos.add(nodo.name)
        req = changes.REQUISITOS[kind]
        # Conditional = mandatory in ITS branch (`REQUISITOS_TECLA`), not
        # optional. `parametros_faltantes()` demands them per `subtipo`.
        condicionales: set[str] = set()
        if kind == "reassign_key":
            for extra in changes.REQUISITOS_TECLA.values():
                condicionales |= set(extra)
        declaradas = set(req["required"]) | set(req["optional"]) | condicionales
        duras, blandas = keys_read(nodo)
        sin_declarar = (duras | blandas) - declaradas
        if sin_declarar:
            fail(
                "%s reads keys that REQUISITOS[%r] does not declare: %s"
                % (nodo.name, kind, ", ".join(sorted(sin_declarar)))
            )
        # A key read by index (`p["x"]`) is MANDATORY by
        # definition: if it is missing, the step raises KeyError. Declaring it
        # optional would be a lie, and that is exactly how the bug happened.
        opcionales_duras = duras & (set(req["optional"]) - condicionales)
        if opcionales_duras:
            fail(
                "%s reads %s by index but REQUISITOS[%r] declares them optional"
                % (nodo.name, ", ".join(sorted(opcionales_duras)), kind)
            )
        print("   %-28s lee %s -- OK" % (nodo.name, sorted(duras | blandas)))
    faltan_pasos = set(STEP_TO_KIND) - vistos
    if faltan_pasos:
        fail(
            "no se encontraron en changes.py: %s (¿se renombraron? este "
            "check would stay green without looking at anything)" % ", ".join(sorted(faltan_pasos))
        )

    # NEGATIVE: a change missing a mandatory key does NOT get on the list.
    s = changes.SesionCambios()
    try:
        s.add("add_device", "Add 'x'", {"name": "x"})
        fail("NEGATIVE FAILED: an add was queued WITHOUT config_json")
    except ValueError as exc:
        if "config_json" not in str(exc):
            fail("the rejection does not name the missing key: %s" % exc)
        else:
            print("   sin config_json -> rechazado al encolar -- OK")
    try:
        s.add("add_device", "Add", {"config_json": "/tmp/x.json"})
        fail("NEGATIVE FAILED: an add was queued WITHOUT a name")
    except ValueError:
        print("   sin nombre       -> rechazado al encolar -- OK")
    try:
        s.add(
            "add_device", "Add", {"config_json": "/tmp/x.json", "name": "  "}
        )
        fail("NEGATIVE FAILED: an add with a blank name was queued")
    except ValueError:
        print("   nombre en blanco -> rechazado al encolar -- OK")
    # NEGATIVE: a physical key with no `contexto` (which `_step_keys` reads
    # indice en esa rama) tampoco entra.
    try:
        s.add(
            "reassign_key", "k", {"subtipo": "fisica", "codigo": 1, "k1": 1, "k2": 2}
        )
        fail("NEGATIVE FAILED: a physical key was queued WITHOUT contexto")
    except ValueError:
        print("   tecla fisica sin contexto -> rechazada -- OK")


# ==========================================================================
# B/C/D/E. Against the real `Api`
# ==========================================================================
def catalog_label(i: dict) -> str:
    return ("%s %s" % (i.get("fabricante") or "", i.get("modelo") or "")).strip()


def usable_items(a) -> tuple[list[dict], dict]:
    """The items whose `config_json` exists, plus the whole response.

    Filtering is by the FILE and not by the folder on purpose: a folder
    missing its `.json` is no good for testing anything, and letting it in
    would make the check fail over broken data on this machine instead of
    over a broken contract in the program."""
    cat = a.catalog_local()
    if not cat.get("ok"):
        return [], cat
    usables = [
        i
        for i in cat.get("items") or []
        if i.get("config_json") and Path(i["config_json"]).is_file()
    ]
    return usables, cat


def build_test_device(blob: Path) -> tuple[Path | None, str]:
    """Arma un dispositivo de prueba con el `.ir` de ejemplo del repo.

    Es lo que hace que este control pueda correr en un clon recien bajado:
    el catalogo local se llena bajando aparatos de una cuenta o importando
    `.ir`, y un clon no tiene ni lo uno ni lo otro. En vez de exigir el
    aparato de alguien, se fabrica uno -- el mismo camino que ya ejercita
    `app/check_ir_manual.py` -- y se borra al terminar.

    `(folder, "")` si salio; `(None, reason)` si no, y entonces el motivo
    es para SALTEAR, no para fallar: significa que a esta maquina le falta
    un insumo, no que el programa este mal."""
    if not EXAMPLE_IR.is_file():
        return None, (
            "the local catalog is empty and the example .ir is not here either "
            "(%s) to build a test device with" % EXAMPLE_IR
        )
    try:
        import ir_manual  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return (
            None,
            "the local catalog is empty and I could not import ir_manual: %s" % exc,
        )
    try:
        r = ir_manual.import_device(
            EXAMPLE_IR,
            TEST_MAKER,
            TEST_MODEL,
            TEST_LABEL,
            blob=blob.read_bytes(),
        )
    except Exception as exc:  # noqa: BLE001
        return None, (
            "the local catalog is empty and the example .ir blew up while "
            "importarse: %s: %s" % (type(exc).__name__, exc)
        )
    if not r.get("ok"):
        return None, (
            "the local catalog is empty and the example .ir could not be "
            "importar: %s" % r.get("error")
        )
    target = Path(r["target"])
    print(
        "   local catalog empty -> test device built from %s: "
        "%s (%d comandos)" % (EXAMPLE_IR.name, target.name, r.get("commands") or 0)
    )
    return target, ""


def check_bcde() -> None:
    import api  # after setting SOLO_LECTURA

    a = api.Api()

    # THE REFERENCE. With no valid configuration to generate against there
    # is nothing to check. Its absence is NOT a bug in the program: it is an
    # input this machine does not have, and so it is skipped.
    ref = a._remote_blob()
    blob = Path(ref.get("blob") or "")
    if not blob.is_file():
        skip(
            "%s (referencia ofrecida: %s)"
            % (MISSING_REFERENCE, ref.get("reference_warning") or ref.get("blob") or "-")
        )
        return

    # THE TEST DEVICE. The FIRST one in the local catalog -- any of them
    # will do, what is under test is the key contract -- and if there is
    # none, one is built from the example `.ir`.
    usables, cat = usable_items(a)
    if not cat.get("ok"):
        fail("catalog_local() no dio ok: %s" % cat.get("error"))
        return
    fabricado = None
    if not usables:
        fabricado, reason = build_test_device(blob)
        if fabricado is None:
            skip(reason)
            return
        usables, cat = usable_items(a)
        if not usables:
            skip(
                "the test device was built (%s) and catalog_local() "
                "no lo lista" % fabricado.name
            )
            return
    try:
        bcde(a, cat, usables[0], blob)
    finally:
        # EXACTLY the folder `import_device()` just returned is deleted. The
        # prefix is the second lock: so that a bug somewhere else cannot turn
        # this into an `rmtree` over an appliance of the user's. If it does
        # not recognise the folder it deletes NOTHING and says so: a test
        # folder left quietly in the catalog is junk that later turns up on
        # the Catalog screen with nobody knowing where it came from.
        if fabricado is not None:
            if fabricado.name.startswith(TEST_PREFIX):
                shutil.rmtree(fabricado, ignore_errors=True)
                print("\n   (carpeta de prueba borrada: %s)" % fabricado.name)
            else:
                fail(
                    "I did not delete the test folder %s: it does not start with %r and "
                    "la reconozco" % (fabricado, TEST_PREFIX)
                )


def bcde(a, cat: dict, test: dict, blob: Path) -> None:
    print("\nB. producer and consumer use THE SAME key name")
    strict_dumps(cat, "catalog_local()")
    required = set(changes.REQUISITOS["add_device"]["required"])
    for it in cat["items"]:
        if "config_json" not in it:
            fail(
                "catalog_local() does not publish 'config_json' (the key "
                "cambios.REQUISITOS declara: %s)" % sorted(required)
            )
            break
        if it.get("json") is not None and it["json"] != it["config_json"]:
            fail(
                "catalog_local()'s 'json' alias no longer holds the same value as "
                "'config_json' in %s: the two names have drifted apart" % it.get("dir")
            )
            break
    else:
        print(
            "   catalog_local() publica config_json (+ alias json identico) "
            "en %d items -- OK" % len(cat["items"])
        )

    print(
        "   dispositivo de prueba: %s (%s)"
        % (catalog_label(test) or "sin fabricante/modelo", test["dir"])
    )

    print("\nB2. queueing the catalog item AS IS no longer breaks in Sync")
    # This is the exact case of the bug: handing `changes_add()` the item
    # `catalog_local()` returns. It used to get on the list labelled
    # "Add '(unnamed)'" y reventaba con KeyError en `sync_preparar()`.
    r = a.changes_add("add_device", dict(test))
    strict_dumps(r, "cambios_agregar(item del catalogo)")
    if r.get("ok"):
        etq = (r.get("change") or {}).get("label") or ""
        if "(unnamed)" in etq:
            fail("volvio la etiqueta 'Add \"(unnamed)\"': %r" % etq)
        else:
            print("   aceptado, etiqueta %r -- OK" % etq)
    else:
        # Rejecting it is correct too (the item carries no `name`), but the
        # rejection has to be explicit and name what is missing.
        if "name" not in (r.get("error") or "").lower():
            fail("the rejection does not say the name is missing: %r" % r.get("error"))
        else:
            print("   rechazado al encolar: %r -- OK" % r["error"])
    a.changes_clear()

    print("\nC. json.dumps FOR REAL on the labels' error path")
    # El `set` que mataba la llamada: `fonts.choose_detail()["missing"]`.
    # A label is asked for with characters no font on the remote draws,
    # which is the path that set travelled on its way out to the UI.
    v = generate.validate_labels(["Ñ¿☃"], blob)
    strict_dumps(v, "generar.validate_labels(etiqueta imposible)")
    for _etq, det in v.get("detail") or []:
        if isinstance(det.get("missing"), (set, frozenset)):
            fail("validate_labels() still returns 'faltan' as a set")
            break
    else:
        print("   validate_labels() -> 'faltan' es lista -- OK")
    if v.get("ok"):
        fail("a label no font draws came back ok=True")

    print("\nD. the three classes of failure are told apart")
    # D1. tool: the add aborts on a check of the tool itself (the label
    # cannot be drawn). It is NOT the gate.
    s = changes.SesionCambios()
    s.add(
        "add_device",
        "Add imposible",
        {"config_json": test["config_json"], "name": "Ñ¿☃"},
    )
    import add_device as dispositivo_mod

    with tempfile.TemporaryDirectory(prefix="control_contrato_") as tmp:
        r1 = changes.apply_all(
            s.listar(),
            blob,
            Path(tmp),
            generate=generate,
            device_module=dispositivo_mod,
        )
    strict_dumps(r1, "aplicar_todos() con etiqueta imposible")
    if r1.get("ok"):
        fail("an add with an undrawable label came back ok=True")
    elif r1.get("category") != changes.CLASE_HERRAMIENTA:
        fail(
            "a tool that aborted on ITS OWN check came out as class %r "
            "(it had to be %r)" % (r1.get("category"), changes.CLASE_HERRAMIENTA)
        )
    elif not (r1.get("error") or "").strip():
        fail("the tool failure carries no reason in plain language")
    else:
        print(
            "   herramienta: clase=%r  motivo=%r -- OK"
            % (r1["category"], r1["error"][:60])
        )
    if r1.get("_convertido"):
        fail(
            "something on its way out to the UI was not JSON-serializable and "
            "convertirlo: %s" % r1["_convertido"]
        )

    # D2. application: a Python exception must NOT come out as the gate.
    class _Broken:
        def read_section5(self, _b):
            raise TypeError("boom sintetico")

    s2 = changes.SesionCambios()
    s2.add(
        "add_device",
        "Add %s" % TEST_LABEL,
        {"config_json": test["config_json"], "name": TEST_LABEL},
    )
    with tempfile.TemporaryDirectory(prefix="control_contrato_") as tmp:
        r2 = changes.apply_all(
            s2.listar(),
            blob,
            Path(tmp),
            generate=generate,
            device_module=_Broken(),
        )
    strict_dumps(r2, "aplicar_todos() with a Python exception")
    if r2.get("ok"):
        fail("a step that raised TypeError came back ok=True")
    elif r2.get("category") != changes.CATEGORY_APP:
        # And "it did not come out as the gate" is not enough: an app bug
        # labelled "tool" also sells the user a protection nobody ever
        # exercised. It has to say the problem belongs to the program.
        fail(
            "A PYTHON EXCEPTION CAME OUT LABELLED %r INSTEAD OF %r: it is "
            "exactly the defect this check exists to prevent"
            % (r2.get("category"), changes.CATEGORY_APP)
        )
    else:
        passed = (r2.get("steps") or [{}])[-1]
        if not (passed.get("technical_detail") or {}).get("traza"):
            fail("an 'aplicacion' class failure carries no traceback for 'See more'")
        print("   excepcion de Python: clase=%r (+ traza) -- OK" % r2.get("category"))

    # D3. y el `KeyError: 'config_json'` original, si alguien arma el Cambio
    # a mano y esquiva `SesionCambios.add()`.
    crudo = changes.Change(
        id="test", kind="add_device", label="Add", parametros=dict(test)
    )
    with tempfile.TemporaryDirectory(prefix="control_contrato_") as tmp:
        r3 = changes.apply_all(
            [crudo], blob, Path(tmp), generate=generate, device_module=dispositivo_mod
        )
    strict_dumps(r3, "aplicar_todos() with a hand-built Cambio")
    if r3.get("ok"):
        fail("a change with no 'nombre' got as far as generating something")
    elif r3.get("category") == changes.CATEGORY_GATE:
        fail("a broken contract came out labelled as the gate")
    elif "name" not in (r3.get("error") or ""):
        fail("the error does not name the missing key: %r" % r3.get("error"))
    else:
        print("   contrato roto: clase=%r  %r -- OK" % (r3["category"], r3["error"][:70]))

    print(
        "\nE. the add end to end: queue %s -> sync_preparar()"
        % (catalog_label(test) or test["dir"])
    )
    a.changes_clear()
    # Which of the devices in the file, resolved the way the Control
    # screen resolves it: by maker+model against what the file carries.
    # (`name` is something ELSE: the label drawn on the remote.)
    inside = a.remote_devices_from_json(test["config_json"])
    objetivo = next(
        (
            i["name"]
            for i in (inside.get("items") or [])
            if i.get("fabricante") == test.get("fabricante")
            and i.get("modelo") == test.get("modelo")
        ),
        None,
    )
    r = a.changes_add(
        "add_device",
        {
            "config_json": test["config_json"],
            "name": TEST_LABEL,
            "device": objetivo,
        },
    )
    if not r.get("ok"):
        fail(
            "could not queue %s: %s"
            % (catalog_label(test) or test["dir"], r.get("error"))
        )
        return
    print("   encolado: %r" % (r["change"]["label"],))
    prep = a.sync_preparar()
    strict_dumps(prep, "sync_preparar()")
    if not prep.get("ok"):
        fail(
            "sync_preparar() fallo: clase=%r  %s"
            % (prep.get("category"), prep.get("error"))
        )
        print(
            "      detalle:", json.dumps(prep.get("technical_detail"), indent=1)[:1500]
        )
    else:
        comp = prep.get("gate") or {}
        print(
            "   listo=%s  compuerta.ok=%s  sin_declarar=%s  repuntes=%s"
            % (
                prep.get("ready"),
                comp.get("ok"),
                comp.get("sin_declarar"),
                prep.get("repuntes"),
            )
        )
        print("   linea: %r" % prep.get("linea"))
        if not prep.get("ready"):
            fail("sync_preparar() no llego a listo=True")
        if not comp.get("ok"):
            fail(
                "the gate did not come back green: sin_declarar=%s" % comp.get("sin_declarar")
            )
        if prep.get("category") is not None:
            fail("a green Sync came out with clase=%r" % prep.get("category"))
    a.changes_clear()


def main() -> int:
    print("CONTRACT CHECK -- the add path (no USB, nothing written)")
    check_a()
    check_bcde()
    print()
    if FAILURES:
        print("CONTROL: FAILED")
        for f in FAILURES:
            print("  -", f)
        return 1
    # Order matters: if something FAILED that wins, even if something was
    # also left unrun. Green only when it all ran and it all came out right.
    if SKIPS:
        print("CONTROL: SKIPPED")
        for s in SKIPS:
            print("  -", s)
        return 2
    print("CONTROL: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
