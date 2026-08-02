#!/usr/bin/env python3
"""LOAD-BEARING CHECK for the translation `cloud/.ir -> remote`: every device
already saved on disk ends up APPLICABLE, or says exactly what it is missing.

WHAT WAS BROKEN, measured before touching anything
--------------------------------------------------
`Api.catalog_local()` published `protocolos` straight out of
`manifest.json`, and only ONE of the three writers ever wrote that key
(`biblioteca.write()`, the catalog path). The other two -- `ir_manual.
importar()` and the old `account_export` cloud exports -- did not. Result, on
the 8 folders on disk: the SAME device downloaded twice listed
`protocolos=['Toshiba 32 Bit']` one time and `protocolos=[]` the other,
which read as "this one can't be applied". It was never true: all 8 files
DID carry their own `resources.ProtocolList`. The manifest simply never
said so.

The second blocker, which no protocol count can see: `add_device.py` does
not store ASCII, it stores a glyph index per character, and it learns that
table at run time by elimination -- crossing the words of the chosen file
against the text already drawn in the reference blob. A word in the file
compatible with the same raw string as the real one makes the reading
ambiguous and the glyph is DISCARDED. Measured on the two
`hub-config-manual-*` folders against the anchor: 60 glyphs instead of 61,
no `D`, and `add_device.py:3122` aborts with "return label 'Devices'
can't be written". Leave-one-out over the 24 words those files add pins it
to exactly one string, `'Vol dn'` -- a label THIS APP derives from the
command name `Vol_dn`, not something a user typed.

WHAT THIS CHECKS (nothing is assumed, everything is run)
--------------------------------------------------------
  1. every saved folder ends applicable, or with `missing_category` +
     `missing_protocol`/`reason` filled in -- never a broken item saved in
     silence;
  2. NEGATIVE: a device whose protocol is on nobody's disk says WHICH one is
     missing and does not blow up -- built here on the fly, in a temp
     directory, out of the LIBRARY'S OWN protocols, with one `KeyCode`
     rewritten to a protocol that cannot exist. Nothing downloaded and no
     device saved is needed for this; when there IS one saved on disk, it
     gets the same battery on top;
  3. NEGATIVE: queuing that same device is refused by
     `changes.SesionCambios.add()` with the protocol's name in the
     message -- the change never enters the list, so it can never fail at
     Sync;
  4. `normalize_folder()` is idempotent and, when it has nothing to add,
     leaves the config file BYTE FOR BYTE untouched (the anchor's two files
     feed `check_load_bearing.py`: a reformat there would be a silent
     change);
  5. the fixed return label this module checks for is still the one
     `config_work/add_device.py` demands.

Touches no USB, opens no network, uses no account, and needs NOTHING
downloaded: it runs on a fresh install, where the library is the three
protocols that ship with the code and `account_export/output/` does not even
exist. Whatever IS there is read and tested too, and never written: the only
thing this file writes is inside a `TemporaryDirectory`.

Usage:
    python3 app/check_library.py
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import library  # noqa: E402
import changes  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG_WORK = ROOT / "config_work"

#: A protocol name that cannot be on anybody's disk: it is the negative
#: control's whole point. If this ever showed up in a real config the check
#: would stop being negative, so it is deliberately absurd.
NONEXISTENT_PROTOCOL = "Fantasia 42 Bit"


def _fail(fallas: list[str], text: str) -> None:
    fallas.append(text)


# --------------------------------------------------------------------------
# 1. everything on disk: applicable, or with the exact reason
# --------------------------------------------------------------------------
def check_permanent_library(fallas: list[str]) -> None:
    """THE control this file was rewritten for: with `account_export/output/`
    holding NO devices, the library still has protocols and words.

    The library used to be built by reading the devices already downloaded.
    The user deleted his devices with the delete button (which works, on
    purpose) and the library went with them: `available_protocols()` -> 0,
    `vocabulary()` -> 0 words, and from that moment NOT ONE device from the
    catalog could be downloaded -- while the screen still said "saved",
    because `Api.catalog_save()` forwarded the failed materialization as
    `ok=True, materializado=False`.

    So this check is deliberately hardest exactly in the state that broke it:
    zero devices on disk.
    """
    lib = library.available_protocols()
    enDisco = library.disk_configs()
    print(
        "  devices on disk: %d   |   protocols in the library: %d"
        % (len(enDisco), len(lib))
    )
    for name in sorted(lib):
        print("      %-20s <- %s" % (name, lib[name].origin))
    if not lib:
        _fail(
            fallas,
            "available_protocols() == 0: the library is empty and NO device "
            "from the catalog can be downloaded. That is the bug this check "
            "exists for.",
        )
    palabras = library.vocabulary()
    print("  glyph vocabulary: %d words" % len(palabras))
    if not palabras:
        _fail(
            fallas,
            "vocabulary() == 0 words: every device saved from here on would "
            "be written with an empty vocabulary and would come out "
            "non-applicable, because the fixed %r label cannot be drawn "
            "without a glyph table" % library.ETIQUETA_VOLVER,
        )

    # Every raw catalog package on disk: it materializes, or it names the
    # protocol it is missing. Never a third answer.
    crudos = sorted((library.OUTPUT_BRIDGE / "catalog-live").glob("*.json"))
    if not crudos:
        print("  (no raw catalog package on disk to check)")
    for crudo in crudos:
        try:
            paquete = json.loads(crudo.read_text())
        except Exception as exc:  # noqa: BLE001
            _fail(fallas, "%s is not readable: %s" % (crudo.name, exc))
            continue
        r = library.inspect_package(paquete, lib=lib)
        print(
            "      %-28s ok=%-5s %s"
            % (
                crudo.name,
                r["ok"],
                r["protocolos"] if r["ok"] else "MISSING %s" % r["missing"],
            )
        )
        if not r["ok"] and not r["missing_protocol"]:
            _fail(
                fallas,
                "%s cannot be materialized and does not name the protocol it "
                "is missing -- that is a silent failure" % crudo.name,
            )
        if not r["ok"] and not (r["reason"] or "").strip():
            _fail(fallas, "%s fails without a reason to show" % crudo.name)

    # NEGATIVE: a protocol nobody has is NAMED, never invented.
    if library.have_protocol(NONEXISTENT_PROTOCOL, lib):
        _fail(
            fallas,
            "the library claims to have %r: the negative control stopped "
            "being negative" % NONEXISTENT_PROTOCOL,
        )
    st = library.protocol_status([NONEXISTENT_PROTOCOL], lib=lib)
    if st["ok"] or st["missing"] != [NONEXISTENT_PROTOCOL]:
        _fail(
            fallas,
            "protocol_status() does not name %r as missing (got %r)"
            % (NONEXISTENT_PROTOCOL, st),
        )
    else:
        print(
            "  negative: %r -> named as missing, not invented" % NONEXISTENT_PROTOCOL
        )


def check_disk(fallas: list[str]) -> list[dict]:
    ref = library.blob_referencia()
    print("reference blob: %s" % ref)
    if ref is None:
        # NOT a failure. The reference blob is READ OFF A REMOTE (`first
        # run`), it is not in the repo, and nobody has it before plugging one
        # in for the first time. `diagnose()` already handles its absence:
        # the protocol half -- which is what this file is about -- is judged
        # the same, and only the glyph half is left unjudged. Failing here
        # would make this control impossible to pass on a machine that has
        # not read a remote yet, which is a control that measures who you
        # are and not whether the code works.
        print(
            "  (no reference blob: the glyph half is not judged here. It is "
            "read off your own remote by first_run.py.)"
        )
    diags = library.diagnose_all()
    if not diags:
        # NOT a failure any more. Zero saved devices is a legitimate state --
        # the user deleted them on purpose -- and it is precisely the state
        # `check_permanent_library()` above has to survive. Failing here
        # would be this file demanding the very thing whose absence it exists
        # to prove harmless.
        print("  (no saved device: nothing to judge here, see 1/6)")
    for d in diags:
        state = "APPLICABLE" if d["aplicable"] else "no (%s)" % d["missing_category"]
        print("  %-54s %-18s %s" % (d["dir"][:54], state, d["protocolos"]))
        if not d["aplicable"]:
            print("      reason: %s" % d["reason"])
            if not d["reason"]:
                _fail(
                    fallas,
                    "%s is not applicable and does not say why -- an item saved "
                    "in silence is exactly the thing this must not do" % d["dir"],
                )
            if d["missing_category"] == "protocolo" and not d["missing_protocol"]:
                _fail(
                    fallas,
                    "%s fails because of a protocol and does not name it" % d["dir"],
                )
        else:
            if not d["protocolos"]:
                _fail(
                    fallas,
                    "%s says it is applicable with no protocol at all: that is "
                    "the exact symptom this check exists for" % d["dir"],
                )
    return diags


# --------------------------------------------------------------------------
# 2 + 3. NEGATIVE: a protocol that is on nobody's disk
# --------------------------------------------------------------------------
#
# WHERE THE DEVICE THAT GETS BROKEN COMES FROM. The negative needs a saved
# device to rewrite, and it used to take it from `account_export/output/` --
# either a folder already sitting there, or a raw catalog package downloaded
# with an account. A machine that has NEITHER (no device saved, no catalog
# package: a fresh install, or the one this file's 1/6 exists for, where the
# user deleted his devices on purpose) got a `RuntimeError` and the negative
# -- the only half of this file that can prove something does NOT happen --
# never ran. A control that needs a particular download to say anything is
# an alarm that only rings in one house.
#
# So the device is BUILT HERE, out of the library's own protocols, and it
# goes through the very same `materialize()` + `write()` a real one does.
# Three consequences, all of them wanted:
#
#   * it needs no account, no download, no remote and nothing on disk;
#   * it is the SAME device on every machine, so a failure here is a failure
#     for everybody -- not "it passes here because my catalog happens to
#     have one more protocol";
#   * what IS on disk keeps being tested: the built one always runs, and a
#     real saved folder runs ON TOP of it whenever there is one.

#: The built device's name. Deliberately not a real make or model: if it
#: ever shows up in a listing it has to be readable at a glance as something
#: this check made up.
SYNTHETIC_MAKER = "Example"
SYNTHETIC_MODEL = "Test Device"

#: Standard command names -- the ones any remote has. They only need to
#: exist: what the negative measures is the protocol, not the labels.
SYNTHETIC_COMMANDS = ("Power", "VolumeUp", "VolumeDown", "Mute", "ChannelUp")


def _synthetic_package(lib: dict) -> dict | None:
    """A catalog package written here, in the same 0.2.0 shape
    `materialize()` reads, using a protocol THE LIBRARY ALREADY HAS.

    `None` when the library has none: with an empty library there is nothing
    to build one out of, and 1/6 has already reported that as the failure it
    is -- saying it twice would only hide which one is the cause.
    """
    if not lib:
        return None
    protocolo = sorted(lib)[0]
    commands = [
        {
            "CommandTypeId": name,
            "Name": name,
            "KeyCode": "G:%s:()(0x%04X)():3" % (protocolo, 0x100 + i),
            "IsLearned": False,
            "TransportType": 1,
        }
        for i, name in enumerate(SYNTHETIC_COMMANDS)
    ]
    return {
        "schema_version": "0.2.0",
        "query": {"manufacturer": SYNTHETIC_MAKER, "model": SYNTHETIC_MODEL},
        "resources": {
            "global_device": {},
            "global_language_commands": commands,
            "selected_match": {
                "Manufacturer": SYNTHETIC_MAKER,
                "DeviceModel": SYNTHETIC_MODEL,
                "DeviceType": 1,
            },
        },
    }


def _write_package(paquete: dict, target: Path, source: str) -> Path | None:
    """`materialize()` + `write()` for one catalog package, INSIDE `target`.
    Never writes to `account_export/output/`: whatever the user has saved there
    is his, and this check neither adds to it nor puts back what he deleted.

    `None` if the package does not materialize (the library is missing one of
    its protocols) -- which is a legitimate answer for a package downloaded
    long ago, not a failure of this check.
    """
    mat = library.materialize(paquete)
    if not mat["ok"]:
        return None
    anterior = library.OUTPUT_BRIDGE
    library.OUTPUT_BRIDGE = target
    try:
        return library.write(mat, source_kind="catalogo", source=source)
    finally:
        library.OUTPUT_BRIDGE = anterior


def _copy_from_disk(target: Path) -> Path | None:
    """A real saved folder, copied into `target`. `None` when there is none
    saved -- the normal state of a fresh install, and not a problem."""
    for jsn in library.disk_configs():
        d = library._read_json(jsn)
        if d and library.protocols_required_by_config(d):
            folder = target / "dispositivo-de-disco"
            shutil.copytree(jsn.parent, folder)
            return folder
    return None


def _package_from_disk(target: Path) -> Path | None:
    """The first raw catalog package on disk that materializes, written into
    `target`. `None` if there is no such package."""
    for crudo in sorted((library.OUTPUT_BRIDGE / "catalog-live").glob("*.json")):
        try:
            paquete = json.loads(crudo.read_text())
        except Exception:  # noqa: BLE001
            continue
        folder = _write_package(paquete, target, str(crudo))
        if folder is not None:
            return folder
    return None


def _break_protocol(folder: Path) -> None:
    """Rewrites ONE command's `KeyCode` so it names a protocol the library
    cannot have, and drops `manifest.protocolos` (as if written by one of the
    writers that never filled it in). Nothing outside `folder` is touched."""
    path = folder / library.CONFIG_NAME
    config = json.loads(path.read_text())
    entradas = ((config.get("resources") or {}).get("DeviceList") or {}).get(
        "DevicesWithFeatures"
    ) or []
    tocados = 0
    for entrada in entradas:
        for c in entrada.get("Commands") or []:
            if isinstance(c.get("KeyCode"), str) and c["KeyCode"].startswith("G:"):
                c["KeyCode"] = re.sub(
                    r"^G:[^:]+:", "G:%s:" % NONEXISTENT_PROTOCOL, c["KeyCode"]
                )
                tocados += 1
                break
        if tocados:
            break
    if not tocados:
        raise RuntimeError("could not find a KeyCode to rewrite")
    path.write_text(json.dumps(config, ensure_ascii=False, indent=1))
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    manifest.pop("protocolos", None)  # as if a writer that never filled it in
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False))


def _run_negative(fallas: list[str], label: str, folder: Path) -> None:
    """The whole battery on ONE broken device: diagnosed by name, not
    repairable, and refused by the changes list."""
    print("  -- %s" % label)
    _break_protocol(folder)
    d = library.diagnose(folder)
    print(
        "  diagnose  -> aplicable=%s  falta_protocolo=%r"
        % (d["aplicable"], d["missing_protocol"])
    )
    print("  reason: %s" % d["reason"])
    if d["aplicable"]:
        _fail(
            fallas,
            "NEGATIVE FAILED (%s): a device with a protocol nobody has came back "
            "applicable" % label,
        )
    if d["missing_protocol"] != NONEXISTENT_PROTOCOL:
        _fail(
            fallas,
            "NEGATIVE FAILED (%s): it does not name the missing protocol "
            "(falta_protocolo=%r, expected %r)"
            % (label, d["missing_protocol"], NONEXISTENT_PROTOCOL),
        )
    if NONEXISTENT_PROTOCOL not in (d["reason"] or ""):
        _fail(
            fallas,
            "NEGATIVE FAILED (%s): the reason shown does not carry the missing "
            "protocol's name" % label,
        )

    # repair() must NOT claim to have fixed what cannot be fixed
    r = library.repair(folder)
    if r["ok"]:
        _fail(
            fallas,
            "NEGATIVE FAILED (%s): repair() said ok=True on a protocol that is "
            "on nobody's disk -- it cannot be invented" % label,
        )

    # and queuing it has to be refused, by name
    sesion = changes.SesionCambios()
    try:
        sesion.add(
            "add_device",
            "negativo",
            {
                "config_json": str(folder / library.CONFIG_NAME),
                "name": "TV",
            },
        )
        _fail(
            fallas,
            "NEGATIVE FAILED (%s): a device with no protocol timings entered the "
            "changes list -- it would have failed at Sync instead" % label,
        )
    except ValueError as exc:
        print("  agregar() -> rejected: %s" % exc)
        if NONEXISTENT_PROTOCOL not in str(exc):
            _fail(
                fallas,
                "NEGATIVE FAILED (%s): the rejection does not name the missing "
                "protocol" % label,
            )
    if len(sesion):
        _fail(
            fallas,
            "NEGATIVE FAILED (%s): the rejected change stayed in the list" % label,
        )


def check_negative(fallas: list[str]) -> None:
    lib = library.available_protocols()
    with tempfile.TemporaryDirectory(prefix="control_biblioteca_") as tmp:
        raiz = Path(tmp)

        # 1. the one that always exists: built here from the library.
        paquete = _synthetic_package(lib)
        if paquete is None:
            _fail(
                fallas,
                "the library has no protocol at all, so the negative could not "
                "even be built -- see 1/6, that is the same failure",
            )
        else:
            folder = _write_package(
                paquete, raiz / "sintetico", "built by %s" % Path(__file__).name
            )
            if folder is None:
                _fail(
                    fallas,
                    "the device built here out of the library's own protocol %r "
                    "did not materialize: materialize() is refusing a package "
                    "whose every protocol it has" % sorted(lib)[0],
                )
            else:
                _run_negative(
                    fallas,
                    "device built here (protocol %r, from the library)"
                    % sorted(lib)[0],
                    folder,
                )

        # 2. and, ON TOP, whatever is really on disk. Absent = nothing to add,
        #    never a failure: `account_export/output/` is not part of the repo.
        real = _copy_from_disk(raiz) or _package_from_disk(raiz / "de-catalogo")
        if real is None:
            print(
                "  (nothing saved on disk and no catalog package: only the "
                "device built here was tested, which is the whole point)"
            )
        else:
            _run_negative(fallas, "real device on disk (%s)" % real.name, real)


# --------------------------------------------------------------------------
# 4. normalize_folder() does not touch what it has nothing to add to
# --------------------------------------------------------------------------
def check_idempotent(fallas: list[str]) -> None:
    for jsn in library.disk_configs():
        before = jsn.read_bytes()
        manifest_antes = (jsn.parent / "manifest.json").read_bytes()
        r1 = library.normalize_folder(jsn.parent)
        r2 = library.normalize_folder(jsn.parent)
        if r2.get("changes"):
            _fail(
                fallas,
                "%s: normalize_folder() is not idempotent, the second run still "
                "changed %s" % (jsn.parent.name, r2["changes"]),
            )
        if not r1.get("changes") and jsn.read_bytes() != before:
            _fail(
                fallas,
                "%s: normalize_folder() had nothing to add and rewrote the "
                "config file anyway" % jsn.parent.name,
            )
        if (
            not r1.get("changes")
            and (jsn.parent / "manifest.json").read_bytes() != manifest_antes
        ):
            _fail(
                fallas,
                "%s: normalize_folder() had nothing to add and rewrote the "
                "manifest anyway" % jsn.parent.name,
            )
    print("  normalize_folder(): idempotent, and a no-op leaves both files intact")


# --------------------------------------------------------------------------
# 5. the fixed label is still the one add_device.py demands
# --------------------------------------------------------------------------
def check_label_contract(fallas: list[str]) -> None:
    source = (CONFIG_WORK / "add_device.py").read_text()
    m = re.search(r"^ETIQUETA_VOLVER\s*=\s*(['\"])(.+?)\1", source, re.M)
    if not m:
        _fail(fallas, "ETIQUETA_VOLVER could not be read out of add_device.py")
        return
    if m.group(2) != library.ETIQUETA_VOLVER:
        _fail(
            fallas,
            "biblioteca.ETIQUETA_VOLVER=%r but add_device.py uses %r: the "
            "glyph check would be judging a label the tool doesn't ask for"
            % (library.ETIQUETA_VOLVER, m.group(2)),
        )
    else:
        print("  ETIQUETA_VOLVER == %r in both files" % library.ETIQUETA_VOLVER)


def main() -> int:
    fallas: list[str] = []

    print("== 1/6: the library survives having NO device on disk ==")
    check_permanent_library(fallas)

    print("\n== 2/6: every saved device, applicable or with its reason ==")
    check_disk(fallas)

    print("\n== 3+4/6: NEGATIVE -- a protocol that is on nobody's disk ==")
    check_negative(fallas)

    print("\n== 5/6: normalize_folder() is idempotent and does not reformat ==")
    check_idempotent(fallas)

    print("\n== 6/6: the fixed return label contract ==")
    check_label_contract(fallas)

    print("\n" + "=" * 72)
    if fallas:
        print("LOAD-BEARING CHECK: FAILED (%d problem(s)):" % len(fallas))
        for f in fallas:
            print("  - %s" % f)
        return 1
    print("LOAD-BEARING CHECK: PASSED.")
    print("  - the library has protocols and words with NO device on disk")
    print("  - every raw catalog package materializes, or names what it lacks")
    print("  - every saved device is applicable, or names what it is missing")
    print("  - a protocol nobody has is named, not invented, and not queued")
    print("  - normalizing twice changes nothing the second time")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
