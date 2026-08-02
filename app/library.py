#!/usr/bin/env python3
"""The library of IR protocols that is ALREADY on disk, and how to use it to
add a new device without touching any account.

## The problem it solves

Logitech's public catalog (`app/catalog.py`, read-only: search ->
`GetGlobalDevices` -> `GetGlobalLanguageCommands`) brings, for each command,
its name and its symbolic `KeyCode` -- for example
`"G:Magnavox 13 Bit:()(0x07FF)():3"`. That is: **the protocol's name and the
payload**, but NOT the protocol definition (carrier, and the mark/space
timing of each bit). Without that definition `config_work/synth_ir.py` cannot
synthesize the waveform and `config_work/add_device.py` skips every command
with "protocol missing from the JSON".

The protocol definition shows up in `resources.ProtocolList`. A protocol
captured once serves any other device that uses the same family, forever,
without ever asking for it again.

## WHERE THE LIBRARY LIVES -- and the bug that moved it there

It used to live in "the configs already downloaded in `account_export/output/`".
That was the bug: the library was a side effect of having devices. The user
deleted his devices (with the delete button, which works, on purpose) and
the library went with them -- `available_protocols()` returned 0 and
`vocabulary()` 0 words, so `materialize()` failed on EVERY catalog package
and no device could be downloaded at all any more.

The library now lives in `protocol_library/` (see `app/library_store.py`), its
own permanent directory, indexed by nothing that the Control screen can
delete. The configs on disk are still read -- they are still the freshest
source -- but they are no longer the only one, and every protocol they bring
is copied into the store the first time it is seen, so deleting that device
afterwards no longer takes its protocol away.

## What this module does

1. `available_protocols()` -- the permanent store PLUS the `ProtocolList` of
   every config on disk, in a single dictionary keyed by name.
2. `materialize()` -- takes a catalog package (0.2.0) and builds a
   `resources` with the SAME shape that `commands.load_hub_config()` and
   `add_device.py` already read, using the catalog's commands and the
   library's protocols. If a protocol is missing it says so by name and
   writes NOTHING.
3. `vocabulary()` + `vocabulary_block()` -- the glyph vocabulary that
   `add_device.py` needs, frozen inside the file that gets written.

## Why the vocabulary goes INSIDE the file (measured, not assumed)

`add_device.py:2935` does `glyphs.extender(blob, glyphs.vocabulario(a.config))`:
the glyph table is learned **only from the words the configuration file
brings**. The blob does not store ASCII but a glyph index per character, and
that table is learned by cross-referencing plain-text words against the text
already drawn in the factory blob. A real Logitech config brings hundreds of
words (5 devices, `FunctionList`, `ActivityList`); a single-device catalog
package brings a few dozen, and that is not enough.

Measured in this session, with the catalog's Philips and no vocabulary:

    the label 'Vol Up' cannot be written: the glyphs 'V' are missing

That's why `write()` also leaves `vocabulario_heredado_de_catalogo` at the
top of the JSON -- the SAME key and shape already written by
`app/ir_manual.py:_snapshot()` --: a list of `{"Label": word}` that
`glyphs.vocabulario()` picks up just like any other name (it walks the whole
JSON looking for the keys
`Name`/`Manufacturer`/`Model`/`Label`/`CommandTypeId`) and that
`add_device.py` does not use for anything else: it is not a device, not a
command, not a protocol. It stays FROZEN in the file, so regenerating
tomorrow gives exactly the same result even if what's on disk changes.

CHECK (run, not assumed): the Philips materialized this way -- public
catalog commands + `Magnavox 13 Bit` from the library + this vocabulary --
generates a blob with md5 `eb9c39b072f12c53cd906291990edb56`, **identical
byte for byte** to step 1 of the regression anchor, which is what is grabbed
and verified on the device today. In other words: for that device, this path
(no account touched, no Hub at all) gives the same result as the
transactional sign-up-and-remove that was taken out of the flow.

## The one thing the catalog can't reproduce: 5 labels out of 63

`add_device.py:537 hub_labels()` pulls the pretty label of each
button from `resources.FunctionList`, joining `Commands[].FunctionId-`
against the same `DeviceId-`'s `FunctionMaps`. The catalog package **brings
no `Label` at all** (measured: 0 occurrences of the key in the two packages
on disk), so the commands fall back to `split_camel(name)`, which is the
same path already used by commands without a `FunctionId-` in a real config.

Isolation check (run): the catalog's LG materialized this way gives md5
`23e6275f5b45f93baa2b3363bbada01e`, and the LG's REAL config with
`FunctionList.FunctionMaps` emptied by hand gives the **same md5**. In other
words: the only difference between the two paths is the label of 5 buttons
out of 63 (`-`, `ChannelPrev`, `InputNext`, `OK`, `SmartMenu`), and none of
them is in the IR waveform. No "label library" is invented: `FunctionId`
values repeat across manufacturers with different meanings (measured: 86 is
`AV`, `Input`, and `InputNext` depending on the device), so guessing them
would write the wrong word on a button.

NOTE on dict keys: the string keys returned by `materialize()` and
`required_protocols()` (`ok`, `missing`, `requeridos`, `fabricante`, `modelo`,
`name`, `commands`, `error`, `resources`, `vocabulario`, `protocolos`,
`protocol_origins`) are kept in Spanish ON PURPOSE -- `app/api.py` forwards
some of these straight into the JSON response the JS UI reads by name. Only
the Python-side names (functions, classes, local variables) are translated.

## THE FOUR FUNCTIONS A CALLER NEEDS TO ANSWER "can I download this?"

    have_protocol(name)      -> bool
    protocol_status(names)   -> {"ok", "tengo", "faltan", "detalle"}
    inspect_package(package) -> the SAME verdict `materialize()` would give,
                                writing nothing. Publishes every key
                                `materialize()` and `diagnose()` publish
                                (`ok`, `aplicable`, `missing`,
                                `missing_protocol`, `missing_category`, `reason`,
                                `requeridos`, `name`, `fabricante`,
                                `modelo`, `commands`, `error`), so a caller
                                cannot pick the wrong one by reaching for
                                the name it already knows from the other.
    library_report()         -> what the library holds and where each
                                definition came from.

`inspect_package()` exists so a screen can say "this one you CAN download,
that one is missing `Sony 8 Bit`" BEFORE the user presses anything -- and so
that nothing ever again reports a failed materialization as a save.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_BRIDGE = ROOT / "account_export" / "output"
CONFIG_WORK = ROOT / "config_work"
if str(CONFIG_WORK) not in sys.path:
    sys.path.insert(0, str(CONFIG_WORK))
# Own directory too: `app/api.py` imports this module flat (`__import__(
# "protocol_library")`), but `main.py --selftest` and the packaged .app do not all
# start from the same cwd, and `biblioteca_almacen` sits right here.
_AQUI = Path(__file__).resolve().parent
if str(_AQUI) not in sys.path:
    sys.path.insert(0, str(_AQUI))

import glyphs  # noqa: E402  -- read-only: learns/reads the table, writes nothing

import library_store as almacen  # noqa: E402  -- the permanent store

#: Historical name of the configuration file inside every folder of
#: `account_export/output/`. Kept as-is because `account_export` (which is outside
#: the app's scope) writes it this way, and because changing it would break
#: the folders that are already on disk. NEW folders written by the app do
#: use `device-*`.
CONFIG_NAME = "hub-config-with-device.json"

#: `"G:Magnavox 13 Bit:()(0x07FF)():3"` -> `Magnavox 13 Bit`. Same shape as
#: `commands.KEYCODE`, but here only the protocol's name is needed.
PROTOCOL_RE = re.compile(r"^G:([^:]+):")

#: The fixed return label EVERY new device needs. Copy of
#: `config_work/add_device.py:ETIQUETA_VOLVER` -- it is the string that
#: `add_device.py:3122` refuses to finish without, and the one this module
#: checks for before saying a device is applicable. Kept as a literal
#: because importing `add_device.py` pulls in its whole argparse/main
#: machinery; `check_contract.py` checks the two stay equal.
ETIQUETA_VOLVER = "Devices"

#: The anchor this project claims is grabbed on the remote today
#: (`ESTADO.md`), and the factory fallback. `blob_referencia()` picks
#: between them with the SAME precedence as `Api._remote_blob()`'s static
#: half -- see that function for why the order is this one.
ANCLA_BIN = ROOT / "output" / "config_empaquetada.bin"
ANCLA_MD5 = "976bc70edd15b40f56cb49aa5113594f"
BLOB_FABRICA = ROOT / "backups" / "config_raw.bin"

#: Switch for `disk_configs()`'s auto-repair. On by default -- that IS the
#: "make it agnostic" requirement: nobody should have to know which of the
#: three writers made a folder. Off is for tests that want to observe the
#: raw state of disk.
AUTOREPARAR = True


# --------------------------------------------------------------------------
# 1. what's on disk
# --------------------------------------------------------------------------


def _raw_disk_configs() -> list[Path]:
    """The pure glob, WITHOUT the auto-repair that `disk_configs()` runs.

    Exists so `available_protocols()` / `vocabulary()` / `normalize_folder()`
    can walk what's on disk without re-entering the repair (which itself
    needs the library). `disk_configs()` is the entry point everything
    OUTSIDE this module should use.
    """
    if not OUTPUT_BRIDGE.exists():
        return []
    return sorted(OUTPUT_BRIDGE.glob("*/" + CONFIG_NAME))


def disk_configs() -> list[Path]:
    """All configuration files already downloaded, no matter what the folder
    is called (the old ones are `hub-config-*`, the new ones
    `device-*`). Filtered by the FILE, not by the folder prefix: a
    folder without the file is useless either way.

    ALSO RUNS `normalize_folder()` on each one. That is the "make it
    agnostic" half: whatever wrote the folder -- `write()` here,
    `ir_manual.importar()`, `aprender_ir`, or an old `account_export` export --
    by the time anybody LISTS it, its `manifest.json` already declares the
    protocols the device really uses, and any protocol definition the file
    was missing has already been pulled in from the library. Idempotent and
    cheap (it only reads `manifest.json` and only writes when something
    actually changes), and it can never make listing fail: any exception is
    swallowed per folder, because a folder that can't be repaired still has
    to show up -- with its reason -- instead of disappearing.
    """
    rutas = _raw_disk_configs()
    if AUTOREPARAR:
        lib = None
        for jsn in rutas:
            try:
                if not _needs_normalizing(jsn.parent):
                    continue
                if lib is None:
                    lib = available_protocols()
                normalize_folder(jsn.parent, lib=lib)
            except Exception:  # noqa: BLE001
                continue
    return rutas


def _read_json(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None
    return d if isinstance(d, dict) else None


def origin_of(folder: Path) -> str:
    """`catalogo` | `manual` | `capturado`. From `manifest.json` if present;
    otherwise from the folder's name (old folders don't carry the field)."""
    manifest = _read_json(folder / "manifest.json") or {}
    o = manifest.get("origin")
    if o in ("catalogo", "manual", "capturado"):
        return str(o)
    return "manual" if "manual" in folder.name else "capturado"


@dataclass(frozen=True, slots=True)
class Protocol:
    name: str
    definition: dict
    origin: str  # name of the folder it came from


#: `sembrar()` is tried at most once per process. Without this, a run where
#: the sources genuinely have nothing to give would re-scan ~200 JSON files
#: on every single call, and `available_protocols()` is called on every
#: repaint of the Control screen.
_SEMBRADO_INTENTADO = False


def available_protocols() -> dict[str, Protocol]:
    """Every IR protocol the library has, keyed by name.

    TWO sources, in this order, and the first to bring a name wins:

      1. the configs on disk (`account_export/output/`) -- freshest, and what
         this function used to be limited to;
      2. the permanent store (`protocol_library/`) -- what stays when there are no
         devices on disk at all, which is exactly the state that broke the
         catalog download.

    Order matters and is deliberate: a device on disk keeps deciding for
    itself, so nothing that already resolved starts resolving differently.
    The store only ever ADDS names that disk does not have.

    It also does the reverse trip: every protocol a disk config brings is
    handed to the store the first time it is seen (`aprender_de_config`).
    That is what makes deleting a device stop being destructive -- the
    protocol it taught outlives it. Writing to the store can never make
    listing fail, so it is wrapped: a store that cannot be written (read-only
    install, full disk) degrades to exactly the old behaviour.
    """
    global _SEMBRADO_INTENTADO
    lib: dict[str, Protocol] = {}
    for path in _raw_disk_configs():
        d = _read_json(path)
        if not d:
            continue
        for p in protocols_in_config(d):
            if p.get("Name") and p["Name"] not in lib:
                lib[p["Name"]] = Protocol(p["Name"], p, path.parent.name)
        try:
            almacen.aprender_de_config(
                d,
                source=str(path),
                label=path.parent.name,
                category=origin_of(path.parent),
            )
        except Exception:  # noqa: BLE001
            pass

    try:
        if not almacen.protocolos() and not _SEMBRADO_INTENTADO:
            _SEMBRADO_INTENTADO = True
            almacen.sembrar()
        for name, entrada in almacen.protocolos().items():
            if name in lib:
                continue
            origin = entrada.get("origin") or {}
            lib[name] = Protocol(
                name,
                entrada["definicion"],
                "biblioteca (%s)" % (origin.get("label") or "almacen"),
            )
    except Exception:  # noqa: BLE001
        pass
    return lib


def have_protocol(name: str, lib: dict[str, Protocol] | None = None) -> bool:
    """Do I have the TIMINGS of this protocol? The one-line question a caller
    asks before promising a user that a device can be downloaded."""
    if not name:
        return False
    return name in (lib if lib is not None else available_protocols())


def protocol_status(names, lib: dict[str, Protocol] | None = None) -> dict:
    """`{"ok", "tengo", "missing", "detail", "origenes"}` for a bunch of
    protocol names at once.

    `missing` carries the EXACT names that are missing -- never a count, never
    a boolean. A missing protocol cannot be invented and the only useful
    thing to tell a user is what to go get.
    """
    if lib is None:
        lib = available_protocols()
    pedidos = [n for n in (names or [])]
    tengo = sorted({n for n in pedidos if n and n in lib})
    missing = sorted({n for n in pedidos if not n or n not in lib})
    return {
        "ok": not missing,
        "tengo": tengo,
        "missing": missing,
        "detail": {n: (bool(n) and n in lib) for n in pedidos},
        "origenes": {n: lib[n].origin for n in tengo},
    }


def library_report() -> dict:
    """What the library holds right now and where each definition came from.

    Keys: `total`, `protocolos` (list of `{nombre, origen, fuente, clase,
    agregado_en, en_disco}`), `palabras`, `ruta`, `en_disco`, `en_almacen`.
    """
    lib = available_protocols()
    est = almacen.state()
    from_store = {p["name"]: p for p in est["protocolos"]}
    rows = []
    for name in sorted(lib):
        stored = from_store.get(name, {})
        rows.append(
            {
                "name": name,
                "origin": lib[name].origin,
                "source": stored.get("source") or lib[name].origin,
                "category": stored.get("category") or "disco",
                "agregado_en": stored.get("agregado_en") or "",
                "en_almacen": name in from_store,
            }
        )
    return {
        "total": len(lib),
        "protocolos": rows,
        "palabras": len(vocabulary()),
        "path": est["path"],
        "en_disco": len(lib)
        - sum(1 for f in rows if f["origin"].startswith("biblioteca (")),
        "en_almacen": est["total"],
    }


def seed_library(*, forzar: bool = False) -> dict:
    """Re-runs the seeding by hand. `sembrar()`'s report, unchanged."""
    return almacen.sembrar(forzar=forzar)


def protocol_definitions() -> dict[str, dict]:
    """`{name: definition}` -- exactly the shape
    `config_work/synth_ir.py:cargar_protocolos()` returns, but taken from the
    permanent library instead of from ONE device's config file.

    `sintir.sintetizar()` only ever needed the timings; asking a particular
    saved device for them is the same bug this module exists to close, one
    level up. `check_learn.py` and `check_ir_manual.py` both used to
    read them out of `account_export/output/hub-config-tv-a/`, and
    both died with FileNotFoundError the moment that device was deleted.
    """
    return {n: p.definition for n, p in available_protocols().items()}


# --------------------------------------------------------------------------
# 1b. reading a config already on disk: what it HAS and what it NEEDS
# --------------------------------------------------------------------------
#
# These two are the whole reason a device downloaded twice used to come out
# different. The catalog package brings each command's `KeyCode` (protocol
# NAME + payload) but never `ProtocolList` (the timings); a real export
# brings both. Asking a file "which protocols do your commands name?" and
# "which definitions do you carry?" separately is what lets any folder, from
# any of the three writers, be judged by the same rule.


def protocols_in_config(config: dict) -> list[dict]:
    """The protocol DEFINITIONS a config file carries (`resources.
    ProtocolList.Protocols`). Empty list if it has none -- that is a normal
    state for a catalog package, not an error."""
    if not isinstance(config, dict):
        return []
    protos = ((config.get("resources") or {}).get("ProtocolList") or {}).get(
        "Protocols"
    )
    return [p for p in (protos or []) if isinstance(p, dict)]


def protocols_required_by_config(
    config: dict, device_name: str | None = None
) -> dict[str, int]:
    """{protocol name: how many commands name it}, read from the `KeyCode`
    of every command in `resources.DeviceList.DevicesWithFeatures`.

    `device_name` narrows it to ONE device (`"<Manufacturer> <Model>"`, the
    same string `add_device.py --device` matches): a real 5-device
    export needs `Sony 15 Bit` for a device nobody is adding, and demanding
    it would reject a file that is perfectly fine for the device the user
    actually picked.

    The `""` key gathers commands whose `KeyCode` isn't recognized -- same
    convention as `required_protocols()`, which reads the OTHER shape (a
    catalog package). Two shapes, one meaning, and neither reimplements the
    other's regex.
    """
    need: dict[str, int] = {}
    if not isinstance(config, dict):
        return need
    entries = ((config.get("resources") or {}).get("DeviceList") or {}).get(
        "DevicesWithFeatures"
    )
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        dev = entry.get("Device")
        if not isinstance(dev, dict):
            continue
        if device_name is not None:
            own_name = _text(
                dev.get("Name"),
                "%s %s" % (dev.get("Manufacturer") or "", dev.get("Model") or ""),
            )
            armado = _text(
                "%s %s" % (dev.get("Manufacturer") or "", dev.get("Model") or "")
            )
            if device_name not in (own_name, armado):
                continue
        for c in entry.get("Commands") or []:
            if not isinstance(c, dict):
                continue
            m = PROTOCOL_RE.match(c.get("KeyCode") or "")
            name = m.group(1) if m else ""
            need[name] = need.get(name, 0) + 1
    return need


def device_names_in_config(config: dict) -> list[str]:
    """Every name a device inside this file answers to, in file order.

    Both spellings per device: its own `Name` and the `"<Manufacturer>
    <Model>"` that `manifest.requested_device` and `add_device.py
    --dispositivo` build. Having this separate from
    `protocols_required_by_config()` is what lets a caller tell "this file
    has no protocols" apart from "this file doesn't have that device" --
    two very different answers that an empty dict cannot distinguish, and
    the second one must never be answered with the protocols of a device
    nobody asked for.
    """
    salida: list[str] = []
    if not isinstance(config, dict):
        return salida
    entries = ((config.get("resources") or {}).get("DeviceList") or {}).get(
        "DevicesWithFeatures"
    )
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        dev = entry.get("Device")
        if not isinstance(dev, dict):
            continue
        for n in (
            _text(dev.get("Name")),
            _text("%s %s" % (dev.get("Manufacturer") or "", dev.get("Model") or "")),
        ):
            if n and n not in salida:
                salida.append(n)
    return salida


_VOCAB_CACHE: tuple[tuple, set[str]] | None = None


def vocabulary() -> set[str]:
    """The plain-text words that let `glyphs.extender()` pin the glyph table,
    from every REAL config on disk (not from the manually imported ones:
    their names were invented by the user and are not drawn in the factory
    blob, so they're of no use) UNION the permanent store's frozen list.

    THE STORE IS NOT AN OPTIMIZATION HERE, IT IS THE OTHER HALF OF THE BUG.
    This used to read only from disk. With the user's devices deleted it
    returned 0 words, and `vocabulary_block()` -- which `write()` freezes
    inside every new device -- would have written an empty list. The device
    would then have been saved and immediately judged non-applicable, because
    `add_device.py` cannot write the fixed `Devices` label without a glyph
    table, and the table is learned ONLY from these words. Measured, with
    `account_export/output/` as it is today: disk gives 0 words, the store gives
    316, and 316 words give a 61-glyph table against the anchor blob with
    every letter of `Devices` in it.

    Cached against (path, mtime, size) of each file AND of the store's
    vocabulary file: the UI asks for it on every keystroke while a name is
    being typed.
    """
    global _VOCAB_CACHE
    paths = [r for r in _raw_disk_configs() if origin_of(r.parent) != "manual"]
    signature = tuple(
        (str(r), r.stat().st_mtime_ns, r.stat().st_size) for r in paths
    ) + (_firma_almacen(),)
    if _VOCAB_CACHE is not None and _VOCAB_CACHE[0] == signature:
        return _VOCAB_CACHE[1]
    from_disk: set[str] = set()
    for r in paths:
        try:
            from_disk |= glyphs.vocabulario(str(r))
        except Exception:  # noqa: BLE001
            continue
    # The same round trip `available_protocols()` does: whatever a real
    # config on disk teaches is kept, so deleting it later costs nothing.
    try:
        if not almacen.palabras():
            almacen.sembrar()
        guardadas = almacen.palabras()
        nuevas = from_disk - guardadas
        if nuevas and _does_not_ruin_the_table(guardadas | nuevas):
            almacen.guardar_palabras(nuevas, sources_used=[str(r) for r in paths])
        vocab = from_disk | almacen.palabras()
    except Exception:  # noqa: BLE001
        vocab = from_disk
    # The signature is rebuilt AFTER those writes. Caching the one computed
    # before them would make the very next call miss (the store's mtime just
    # changed, by us) and redo the whole ~0.2 s walk for nothing.
    signature = signature[:-1] + (_firma_almacen(),)
    _VOCAB_CACHE = (signature, vocab)
    return vocab


def _does_not_ruin_the_table(candidata: set[str]) -> bool:
    """Would this word list still let the fixed `Devices` label be written?

    THE ONE DANGER OF MAKING THE VOCABULARY PERMANENT. The glyph table is
    learned by elimination, so a single word compatible with the same raw
    string as a real one makes a letter ambiguous and the glyph is DISCARDED
    -- measured on a manually imported TV (`hub-config-manual-*`), where a
    single string
    `'Vol dn'` took the table from 61 glyphs to 60 and killed the `D` of
    `Devices`. On disk that was recoverable: `repair()` does leave-one-out
    over the words the DEVICE adds and renames the guilty label. A poisoned
    word inside the frozen block is NOT recoverable that way -- `repair()`
    never proposes removing from the frozen block, because removing from it
    can only make things worse.

    So nothing enters the permanent list without being measured first. If
    there is no reference blob to measure against, nothing is written: this
    call's words still work in memory, they simply are not made permanent on
    a judgement that could not be made.
    """
    ref = blob_referencia()
    if ref is None:
        return False
    try:
        table, _ = glyphs.extender(ref.read_bytes(), candidata)
    except Exception:  # noqa: BLE001
        return False
    inv = {v: k for k, v in table.items()}
    return all(c in inv for c in ETIQUETA_VOLVER)


def _firma_almacen() -> tuple:
    try:
        st = almacen.VOCABULARY_PATH.stat()
        return ("<almacen>", st.st_mtime_ns, st.st_size)
    except OSError:
        return ("<almacen>", 0, 0)


def vocabulary_block(words: set[str] | None = None) -> list[dict]:
    """The `vocabulario_heredado_de_catalogo` that gets frozen inside the
    file.

    Same key and same shape already written by `app/ir_manual.py:_snapshot()`
    (`[{"Label": word}, ...]`, at the top of the JSON): both paths that write
    a new device leave the file looking the same. It is not a device datum --
    neither `commands.load_hub_config()` nor `add_device.py` reads it -- it IS AND
    ONLY IS fuel for `glyphs.vocabulario()`, which walks the entire JSON
    looking for `Name`/`Label`/`Manufacturer`/`Model` keys.
    """
    p = sorted(words if words is not None else vocabulary())
    return [{"Label": w} for w in p]


# --------------------------------------------------------------------------
# 2. materialize a catalog package (0.2.0) against the library
# --------------------------------------------------------------------------


def _text(*candidates) -> str:
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return ""


def required_protocols(catalog_commands: list) -> dict[str, int]:
    """{protocol name: how many commands use it}. The `""` key gathers the
    commands whose `KeyCode` is not recognized."""
    need: dict[str, int] = {}
    for c in catalog_commands:
        if not isinstance(c, dict):
            continue
        m = PROTOCOL_RE.match(c.get("KeyCode") or "")
        name = m.group(1) if m else ""
        need[name] = need.get(name, 0) + 1
    return need


def _rechazo(error: str, **extra) -> dict:
    """A refusal from `materialize()` / `inspect_package()`, carrying EVERY
    key a caller might reach for.

    Why the redundancy is on purpose: `materialize()` published `ok`/`error`,
    `diagnose()` publishes `aplicable`/`reason`/`missing_category`/
    `missing_protocol`, and `Api.catalog_local()` forwards the second set to
    the JS. A caller that reads the name it happens to know from the other
    function used to get `None` and read it as "fine" -- that is precisely
    the shape of the bug that reported a failed materialization as a save.
    Here every one of those names exists and says the same thing.
    """
    d = {
        "ok": False,
        "aplicable": False,
        "error": error,
        "reason": error,
        "missing_category": "file",
        "missing_protocol": None,
        "missing": [],
        "requeridos": {},
        "protocolos": [],
    }
    d.update(extra)
    return d


def materialize(package: dict, lib: dict[str, Protocol] | None = None) -> dict:
    """Catalog package (0.2.0) -> `resources` shaped like a config.

    Returns `{"ok": bool, ...}`. With `ok=False` there is nothing to write:
    `missing` carries the protocols the library does not have, by name, and
    `missing_protocol` the first of them. `inspect_package()` gives the same
    verdict without building anything.
    """
    if lib is None:
        lib = available_protocols()
    resources_in = package.get("resources") if isinstance(package, dict) else None
    if not isinstance(resources_in, dict):
        return _rechazo("the package has no 'resources'")
    cmds = resources_in.get("global_language_commands")
    if not isinstance(cmds, list) or not cmds:
        return _rechazo("the package brings no commands")

    sel = (
        resources_in.get("selected_match")
        if isinstance(resources_in.get("selected_match"), dict)
        else {}
    )
    gd = (
        resources_in.get("global_device")
        if isinstance(resources_in.get("global_device"), dict)
        else {}
    )
    q = package.get("query") if isinstance(package.get("query"), dict) else {}

    # `selected_match` overrides `query`: the user types "LG TV" and the
    # catalog returns the real model "LG TV". The name built here is
    # the one `add_device.py --device` will have to match.
    manufacturer = _text(
        sel.get("Manufacturer"),
        q.get("manufacturer"),
        gd.get("PrimaryManufacturerAlias"),
    )
    model = _text(sel.get("DeviceModel"), q.get("model"), gd.get("PrimaryModelAlias"))
    if not manufacturer or not model:
        return _rechazo("the package has no manufacturer/model")
    name = f"{manufacturer} {model}"

    need = required_protocols(cmds)
    missing = sorted(n for n in need if not n or n not in lib)
    if missing:
        return _rechazo(
            _missing_from_catalog(missing),
            missing_category="protocolo",
            missing_protocol=next((f for f in missing if f), None),
            missing=missing,
            requeridos=need,
            protocolos=sorted(n for n in need if n and n in lib),
            fabricante=manufacturer,
            modelo=model,
            name=name,
            commands=len(cmds),
        )

    used = sorted(need)
    protocols = [lib[n].definition for n in used]
    device = {
        "Device": {
            "Manufacturer": manufacturer,
            "Model": model,
            "Name": name,
            "Id-": gd.get("Id") or gd.get("GlobalDeviceVersionId-") or 0,
            "DeviceType": gd.get("DeviceType"),
            "GlobalDeviceVersionId-": gd.get("GlobalDeviceVersionId-"),
        },
        "Commands": cmds,
        "DeviceFeatures": [],
    }
    resources = {
        "ProtocolList": {"Protocols": protocols},
        "DeviceList": {"DevicesWithFeatures": [device]},
        # Empty on purpose: the catalog brings no `Label` (see the module's
        # docstring). `add_device.py` falls back on its own to
        # `split_camel(name)`.
        "FunctionList": {"FunctionMaps": []},
    }
    return {
        "ok": True,
        "aplicable": True,
        "missing_category": None,
        "reason": None,
        "missing_protocol": None,
        "error": None,
        "resources": resources,
        "vocabulario": vocabulary_block(),
        "fabricante": manufacturer,
        "modelo": model,
        "name": name,
        "commands": len(cmds),
        "protocolos": used,
        "protocol_origins": {n: lib[n].origin for n in used},
        "requeridos": need,
        "missing": [],
    }


def _missing_from_catalog(missing: list[str]) -> str:
    """The refusal a user can act on: WHICH protocol is missing and how it
    arrives. Never a count, never "something went wrong"."""
    return (
        "the library does not have the timing definition of %s. The catalog "
        "brings the protocol's name and each command's payload, but not its "
        "timings: they arrive by importing an .ir file of any device in that "
        "family once (Catalog tab), and from then on they stay for every "
        "other device that uses it."
        % ", ".join(repr(f) if f else "(KeyCode not recognized)" for f in missing)
    )


def inspect_package(package: dict, lib: dict[str, Protocol] | None = None) -> dict:
    """`materialize()`'s verdict WITHOUT building anything -- the question
    "could I download this device right now?".

    Same keys as `materialize()`, minus `resources` / `vocabulario`, plus
    `protocolos_disponibles`. On success it does NOT build the `resources`
    (that is `materialize()`'s job) but it does answer `ok=True`,
    `aplicable=True`, and lists in `protocolos` the ones it would use and in
    `protocol_origins` where each definition would come from.

    Meant to be called on a whole shelf of catalog packages to show which are
    downloadable before the user picks one.
    """
    if lib is None:
        lib = available_protocols()
    mat = materialize(package, lib=lib)
    mat.pop("resources", None)
    mat.pop("vocabulario", None)
    mat["protocolos_disponibles"] = sorted(lib)
    return mat


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "x"


def write(
    mat: dict, *, source_kind: str = "catalogo", source: str | None = None
) -> Path:
    """Writes the folder that the Control screen is going to list. Returns
    the folder. `mat` is what `materialize()` returned with `ok=True`."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = OUTPUT_BRIDGE / (
        "dispositivo-%s-%s-%s-%s"
        % (source_kind, _slug(mat["fabricante"]), _slug(mat["modelo"]), stamp)
    )
    dest.mkdir(parents=True, exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    snapshot = {
        "schema_version": "0.3.0",
        "generated_at": now,
        "source": "catalogo-publico+biblioteca-local",
        "resources": mat["resources"],
        # See `vocabulary_block()`: without this `add_device.py` does not
        # learn enough glyphs to write the buttons' labels.
        "vocabulario_heredado_de_catalogo": mat.get("vocabulario") or [],
    }
    manifest = {
        "schema_version": "0.3.0",
        "origin": source_kind,
        "generated_at": now,
        "requested_device": {
            "manufacturer": mat["fabricante"],
            "model": mat["modelo"],
        },
        "validation": {
            "commands": mat["commands"],
            "referenced_protocols": len(mat["protocolos"]),
        },
        "protocolos": mat["protocolos"],
        "protocol_origins": mat.get("protocol_origins") or {},
        "sin_cuenta": True,  # nothing was created or deleted in any account
        "source": source,
    }
    (dest / CONFIG_NAME).write_text(json.dumps(snapshot, ensure_ascii=False, indent=1))
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1)
    )
    # LEAVE IT ALREADY APPLICABLE, not applicable-looking. `materialize()`
    # already refused to get here without every protocol's timings, so the
    # only thing that could still be wrong is the glyph table -- and that
    # one depends on the reference blob, so it can only be judged now, with
    # the file written. `repair()` writes nothing when there is nothing to
    # fix (measured: the catalog path leaves `FunctionList` empty, so it has
    # no labels to collide with anything), and it never fails: a device that
    # is saved and reported is strictly better than a save that blew up on
    # the way out.
    try:
        repair(dest)
    except Exception:  # noqa: BLE001
        pass
    return dest


# --------------------------------------------------------------------------
# 3. IS THIS SAVED DEVICE APPLICABLE? -- one rule for all three writers
# --------------------------------------------------------------------------
#
# WHY THIS EXISTS. Three different pieces of code write a folder into
# `account_export/output/`: `write()` (catalog + library), `ir_manual.importar()`
# (a `.ir` by hand) and an old `account_export` export (the cloud). Only the
# first one ever wrote `manifest.protocolos`, so `Api.catalog_local()` --
# which reads exactly that key -- listed the other two with `protocolos=[]`
# and the same device downloaded twice looked different. MEASURED before
# touching anything: all seven folders on disk DID carry their
# `ProtocolList`; nothing was missing, the manifest just never said so.
# `normalize_folder()` closes that hole for every writer, past and future,
# and `disk_configs()` runs it, so no caller has to know it exists.
#
# THE SECOND BLOCKER, which the protocol count cannot see. `add_device.py`
# does NOT store ASCII: it stores a glyph index per character, and it learns
# that table at run time with `glifos.extender(blob, glifos.vocabulario(
# config))` -- by elimination, against the strings already drawn in the
# reference blob. A word in the config that is compatible with the same raw
# string as the real one makes the reading ambiguous, and the glyph is
# DISCARDED instead of learned. MEASURED on a manual import
# (`hub-config-manual-*`)
# against the anchor: with its own vocabulary the table comes out with 60
# glyphs and no `D`, so `add_device.py:3122` aborts with "return label
# 'Devices' can't be written". Leave-one-out over its 24 device words pins
# it exactly: the single string `'Vol dn'` (a label THIS APP derives from
# the command name `Vol_dn`, not something the user typed) is the whole
# cause -- drop it and the table is 61 glyphs again.
#
# That is why applicability is computed LIVE and not frozen at save time:
# it depends on the reference blob, and the reference blob changes every
# time something is written to the remote. `ir_manual.importar()` DID check
# this at import time and passed -- against the blob of that moment. The
# item then went stale on disk without anybody touching it.


def blob_referencia() -> Path | None:
    """The blob to judge glyphs against, when the caller has no better one.

    Same precedence as the STATIC half of `Api._remote_blob()`: the anchor
    `output/config_empaquetada.bin` if its md5 still matches the
    declared one, else the factory `backups/config_raw.bin`. It deliberately
    does NOT know about the live read or the grabbing history -- those live
    in `api.py` and the caller that has them should pass `blob_bytes`
    itself. Returns None if neither file is there.
    """
    try:
        if ANCLA_BIN.exists():
            import hashlib

            if hashlib.md5(ANCLA_BIN.read_bytes()).hexdigest() == ANCLA_MD5:
                return ANCLA_BIN
    except Exception:  # noqa: BLE001
        pass
    return BLOB_FABRICA if BLOB_FABRICA.exists() else None


#: (blob length, config path, config mtime_ns) -> the glyph verdict. The
#: table costs ~0.16 s per config, and Control asks for the whole list on
#: every repaint.
_GLIFOS_CACHE: dict[tuple, dict] = {}


def _glyph_table(config_path: Path, blob_bytes: bytes) -> dict:
    table, _ = glyphs.extender(blob_bytes, glyphs.vocabulario(str(config_path)))
    return table


def glyph_gate(
    config_path: Path,
    blob_bytes: bytes,
    *,
    extra_labels: tuple[str, ...] = (),
) -> dict:
    """Can the remote WRITE the fixed return label with the glyph table this
    file is going to produce?

    Runs exactly what `add_device.py` runs (`glyphs.extender` over the
    file's own vocabulary, then `glyphs.codificar`), so a green here and a
    red there cannot disagree. `extra_labels` is for the caller that already
    knows the name the user typed.

    Returns `{"ok", "missing_letters", "palabras_conflictivas", "glyphs"}`.
    `palabras_conflictivas` is only computed when it failed (it costs one
    extra table per candidate word) and is the ACTIONABLE part: those are
    the strings in this file that, removed, would make the missing glyph
    learnable again.
    """
    clave = (
        len(blob_bytes),
        str(config_path),
        config_path.stat().st_mtime_ns,
        extra_labels,
    )
    hit = _GLIFOS_CACHE.get(clave)
    if hit is not None:
        return hit
    requeridas = (ETIQUETA_VOLVER,) + tuple(e for e in extra_labels if e)
    table = _glyph_table(config_path, blob_bytes)
    inv = {v: k for k, v in table.items()}
    missing = sorted({c for etq in requeridas for c in etq if c not in inv})
    r = {
        "ok": not missing,
        "missing_letters": missing,
        "palabras_conflictivas": [],
        "glyphs": len(table),
        "requeridas": list(requeridas),
    }
    if missing:
        r["palabras_conflictivas"] = _palabras_conflictivas(
            config_path, blob_bytes, requeridas
        )
    _GLIFOS_CACHE[clave] = r
    return r


def _palabras_conflictivas(
    config_path: Path, blob_bytes: bytes, requeridas: tuple[str, ...]
) -> list[str]:
    """Leave-one-out over the words this DEVICE adds on top of the frozen
    catalog vocabulary: which ones, removed, make every required letter
    learnable again.

    Only the device's own words are candidates. The frozen
    `vocabulario_heredado_de_catalogo` is the block that MAKES the table
    complete (measured: 314 words -> 61 glyphs on its own); removing from it
    can only make things worse, so it is never proposed.
    """
    d = _read_json(config_path) or {}
    congelado = {
        w.get("Label")
        for w in (d.get("vocabulario_heredado_de_catalogo") or [])
        if isinstance(w, dict) and w.get("Label")
    }
    completo = glyphs.vocabulario(str(config_path))
    candidatas = sorted(completo - congelado)
    if not candidatas or not congelado:
        return []
    culpables = []
    for palabra in candidatas:
        table, _ = glyphs.extender(blob_bytes, completo - {palabra})
        inv = {v: k for k, v in table.items()}
        if all(c in inv for etq in requeridas for c in etq):
            culpables.append(palabra)
    return culpables


def _needs_normalizing(folder: Path) -> bool:
    """Cheap pre-check so `disk_configs()` doesn't re-read every config on
    every repaint: only the manifest is read here.

    Both keys are checked, not just `protocolos`. A manifest that already
    carries `protocolos: []` -- the empty list, which is what a writer
    leaves when it knows the key exists but not how to fill it -- would
    otherwise look normalized forever and keep listing a perfectly good
    device as protocol-less. `protocol_origins` is written by
    `normalize_folder()` and by nobody else, so its presence is the honest
    "this has been through here" mark.
    """
    manifest = _read_json(folder / "manifest.json")
    if manifest is None:
        return False  # no manifest at all: not ours to invent one
    return not isinstance(manifest.get("protocolos"), list) or not isinstance(
        manifest.get("protocol_origins"), dict
    )


def normalize_folder(folder: Path, *, lib: dict[str, Protocol] | None = None) -> dict:
    """Makes ONE saved folder look like `write()` had written it, whoever
    actually did. Idempotent; writes only when something changes.

    Two things, and only these two -- nothing here ever touches a command,
    a label or a name:

      1. `manifest.protocolos` / `manifest.protocol_origins`: the protocols
         this device's commands actually name, whether the definition came
         in the file itself or from the library. THIS is what
         `Api.catalog_local()` publishes as `protocolos`, and what made the
         same device look different depending on which code path saved it.
      2. any protocol DEFINITION the file names but does not carry gets
         copied in from the library, into `resources.ProtocolList`. That is
         the "automatic translation": a `.ir` import or an old export that
         references a protocol it doesn't define stops being a dead item as
         soon as any other device on disk brings that definition.

    Returns `{"ok", "changes": [...], "protocolos": [...], "missing": [...]}`.
    `missing` is never fatal here: a protocol nobody on disk has cannot be
    invented, and `diagnose()` is what reports it in words.
    """
    if lib is None:
        lib = available_protocols()
    config_path = folder / CONFIG_NAME
    manifest_path = folder / "manifest.json"
    config = _read_json(config_path)
    manifest = _read_json(manifest_path)
    if config is None or manifest is None:
        return {"ok": False, "error": "folder without config/manifest", "changes": []}

    pedido = manifest.get("requested_device") or {}
    device_name = _text(
        "%s %s" % (pedido.get("manufacturer") or "", pedido.get("model") or "")
    )
    # THE MISMATCH IS NOT PAPERED OVER. If the manifest asks for a device
    # this file does not contain, the protocols of some OTHER device in it
    # are not an answer -- writing them into the manifest would make a
    # broken folder look ready. `read_local_export()` already refuses this
    # same folder for the same reason; the two have to agree.
    presentes = device_names_in_config(config)
    if device_name and presentes and device_name not in presentes:
        return {
            "ok": False,
            "error": (
                "the manifest asks for %r and the file contains %s"
                % (device_name, ", ".join(repr(p) for p in presentes))
            ),
            "changes": [],
            "protocolos": [],
            "missing": [],
        }
    need = protocols_required_by_config(config, device_name or None)
    has = {p["Name"]: p for p in protocols_in_config(config) if p.get("Name")}

    changes: list[str] = []
    missing: list[str] = []
    agregados: dict[str, str] = {}
    for name in sorted(need):
        if not name:
            missing.append("")
            continue
        if name in has:
            continue
        if name in lib:
            has[name] = lib[name].definition
            agregados[name] = lib[name].origin
        else:
            missing.append(name)

    if agregados:
        # APPEND, never re-sort. The protocols already in the file keep
        # their exact order and their exact bytes: `add_device.py` walks
        # this list, and reordering a file that already worked would be a
        # silent change to something nobody asked to change -- the same
        # rule the write gate enforces on the blob. The new ones go at the
        # end, in a fixed order so two runs give the same file.
        resources = config.setdefault("resources", {})
        plist = resources.setdefault("ProtocolList", {})
        previos = protocols_in_config(config)
        plist["Protocols"] = previos + [
            lib[n].definition for n in sorted(agregados) if n in lib
        ]
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=1))
        changes.append(
            "protocol definitions taken from the library: %s"
            % ", ".join("%s (from %s)" % (n, o) for n, o in sorted(agregados.items()))
        )

    resueltos = sorted(n for n in need if n and n in has)
    origenes = dict(manifest.get("protocol_origins") or {})
    for n in resueltos:
        origenes.setdefault(n, agregados.get(n) or folder.name)
    if manifest.get("protocolos") != resueltos or manifest.get("protocol_origins") != {
        n: origenes[n] for n in resueltos
    }:
        manifest["protocolos"] = resueltos
        manifest["protocol_origins"] = {n: origenes[n] for n in resueltos}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
        changes.append("manifest.protocolos filled in: %s" % ", ".join(resueltos))

    return {
        "ok": True,
        "changes": changes,
        "protocolos": resueltos,
        "missing": sorted(set(missing)),
        "requeridos": need,
    }


def how_to_get_it(missing: list[str]) -> str:
    """THE PUBLIC NAME of `_missing_how_to_get_it()`, and the one to use from
    outside this module.

    `app/api.py` was already calling this function, but by the private
    name -- via `getattr(biblioteca, "como_conseguirlo", None) or getattr(
    biblioteca, "_falta_como_conseguirlo", None)`, written blind by two
    agents in parallel that could not see each other's code. It worked by
    accident: the public name did not exist and it fell through to the
    private one. An underscore is a promise that it can be renamed without
    warning, so that splice would break the day somebody renamed it --
    and it would break SILENTLY, because `getattr` with a `None` default
    does not fail, it simply leaves the screen without the text that
    explains what to do.
    """
    return _missing_how_to_get_it(missing)


def _missing_how_to_get_it(missing: list[str]) -> str:
    with_name = [f for f in missing if f]
    if not with_name:
        return (
            "some commands in this file carry a KeyCode this app doesn't "
            "recognize, so there is no way to tell which protocol they need."
        )
    return (
        "the timing definition of %s is missing. It can't be invented: it "
        "arrives by downloading another device from the catalog that uses "
        "the same protocol, or by importing an .ir file of this one once "
        "(Catalog tab). From then on it stays available for every other "
        "device in the same family." % ", ".join(repr(f) for f in with_name)
    )


def diagnose(
    folder: Path,
    *,
    blob_bytes: bytes | None = None,
    lib: dict[str, Protocol] | None = None,
    device_name: str | None = None,
    label: str | None = None,
    check_glyphs: bool = True,
) -> dict:
    """THE verdict for one saved device: can it be added to the remote right
    now, and if not, exactly what is missing.

    Keys published (they are a contract -- `app/api.py` forwards them to the
    JS by name, and `app/check_contract.py` checks they stay):

        aplicable          bool
        motivo             str|None   -- ready to show, no assembly needed
        falta_protocolo    str|None   -- the FIRST missing one, singular
        faltan_protocolos  list[str]
        protocolos         list[str]  -- the ones it really uses, resolved
        clase_falta        None | "protocolo" | "glifos" | "archivo"
        glifos             dict|None  -- `glyph_gate()`'s answer

    `label` is the name the user typed for the remote's menu, when the
    caller already knows it; it gets checked with the same table as the
    fixed `Devices` label.

    `folder` may be the FOLDER or the config FILE itself. `changes.py`
    holds the file path (that's what the queued change carries as
    `config_json`), Control holds the folder, and neither should have to
    convert -- converting in two places is how the two of them drift.
    """
    if lib is None:
        lib = available_protocols()
    if folder.is_file():
        config_path, folder = folder, folder.parent
    else:
        config_path = folder / CONFIG_NAME
    config = _read_json(config_path)
    if config is None:
        return {
            "aplicable": False,
            "missing_category": "file",
            "reason": "there is no readable device file at %s." % config_path,
            "missing_protocol": None,
            "missing_protocols": [],
            "protocolos": [],
            "glyphs": None,
        }

    if device_name is None:
        manifest = _read_json(folder / "manifest.json") or {}
        pedido = manifest.get("requested_device") or {}
        device_name = (
            _text(
                "%s %s" % (pedido.get("manufacturer") or "", pedido.get("model") or "")
            )
            or None
        )
    # Asked for a device the file doesn't have: that is a broken FOLDER, not
    # a missing protocol, and answering with some other device's protocols
    # would make it look ready. `catalogo.read_local_export()` refuses the
    # same folder for the same reason -- the two must not disagree.
    presentes = device_names_in_config(config)
    if device_name and presentes and device_name not in presentes:
        return {
            "aplicable": False,
            "missing_category": "file",
            "reason": (
                "this folder is inconsistent: it says it holds %r, but the "
                "file inside contains %s. Nothing can be built from it -- "
                "download or import the device again."
                % (device_name, ", ".join(repr(p) for p in presentes))
            ),
            "missing_protocol": None,
            "missing_protocols": [],
            "protocolos": [],
            "glyphs": None,
        }
    need = protocols_required_by_config(config, device_name)
    has = {p["Name"] for p in protocols_in_config(config) if p.get("Name")}
    missing = sorted({n for n in need if n not in has and n not in lib})
    resueltos = sorted({n for n in need if n and (n in has or n in lib)})

    if not need:
        return {
            "aplicable": False,
            "missing_category": "file",
            "reason": (
                "this file carries no command for %s, so there is nothing to "
                "put on the remote."
                % (repr(device_name) if device_name else "any device")
            ),
            "missing_protocol": None,
            "missing_protocols": [],
            "protocolos": [],
            "glyphs": None,
        }

    if missing:
        return {
            "aplicable": False,
            "missing_category": "protocolo",
            "reason": _missing_how_to_get_it(missing),
            "missing_protocol": next((f for f in missing if f), None),
            "missing_protocols": missing,
            "protocolos": resueltos,
            "glyphs": None,
        }

    if not check_glyphs:
        return {
            "aplicable": True,
            "missing_category": None,
            "reason": None,
            "missing_protocol": None,
            "missing_protocols": [],
            "protocolos": resueltos,
            "glyphs": None,
        }

    if blob_bytes is None:
        ref = blob_referencia()
        blob_bytes = ref.read_bytes() if ref else None
    if not blob_bytes:
        return {
            "aplicable": True,
            "missing_category": None,
            "reason": None,
            "missing_protocol": None,
            "missing_protocols": [],
            "protocolos": resueltos,
            "glyphs": None,
        }

    g = glyph_gate(
        config_path, blob_bytes, extra_labels=tuple(x for x in (label,) if x)
    )
    if g["ok"]:
        return {
            "aplicable": True,
            "missing_category": None,
            "reason": None,
            "missing_protocol": None,
            "missing_protocols": [],
            "protocolos": resueltos,
            "glyphs": g,
        }
    return {
        "aplicable": False,
        "missing_category": "glyphs",
        "reason": _reason_glyphs(g),
        "missing_protocol": None,
        "missing_protocols": [],
        "protocolos": resueltos,
        "glyphs": g,
    }


def _reason_glyphs(g: dict) -> str:
    letras = ", ".join(repr(c) for c in g["missing_letters"])
    base = (
        "the remote learns which letter each glyph is by cross-referencing "
        "the words in this file against the text already on the remote, and "
        "with this file's words the letter%s %s cannot be pinned down -- so "
        "the fixed %r label that every device needs cannot be written."
        % ("" if len(g["missing_letters"]) == 1 else "s", letras, ETIQUETA_VOLVER)
    )
    conflictivas = g.get("palabras_conflictivas") or []
    if conflictivas:
        return base + (
            " What makes it ambiguous is %s: renaming that button (Repair "
            "does it) makes the device usable again."
            % ", ".join(repr(w) for w in conflictivas)
        )
    return base + (
        " Downloading any device from the catalog brings hundreds of words "
        "and usually resolves it."
    )


def diagnose_all(
    *, blob_bytes: bytes | None = None, check_glyphs: bool = True
) -> list[dict]:
    """`diagnose()` for every saved folder, in the same order
    `disk_configs()` lists them. Each entry carries `dir` so a caller can
    join it against `Api.catalog_local()`'s items by name."""
    lib = available_protocols()
    if blob_bytes is None and check_glyphs:
        ref = blob_referencia()
        blob_bytes = ref.read_bytes() if ref else None
    salida = []
    for jsn in disk_configs():
        d = diagnose(
            jsn.parent,
            blob_bytes=blob_bytes,
            lib=lib,
            check_glyphs=check_glyphs,
        )
        d["dir"] = jsn.parent.name
        salida.append(d)
    return salida


# --------------------------------------------------------------------------
# 4. REPAIR -- the part that changes device data, and only when asked
# --------------------------------------------------------------------------


def _variantes(palabra: str) -> list[str]:
    """Rewrites of a button label that keep the same words, in increasing
    order of how much they change what the screen shows. The first one that
    both fixes the table AND is itself drawable wins; nothing is invented
    beyond capitalisation and spacing."""
    partes = palabra.split()
    v = [
        " ".join(p[:1].upper() + p[1:] for p in partes),  # 'Vol dn' -> 'Vol Dn'
        "".join(p[:1].upper() + p[1:] for p in partes),  # -> 'VolDn'
        " ".join(partes).upper(),  # -> 'VOL DN'
    ]
    return [x for i, x in enumerate(v) if x and x != palabra and x not in v[:i]]


def _reemplazar_label(config: dict, before: str, after: str) -> int:
    """Rewrites every `Label` exactly equal to `before`. Only `Label`:
    `Name`/`CommandName` are the join keys `hub_labels()` and
    `add_device.py` match on, and renaming those would break the join."""
    n = 0
    pila = [config]
    while pila:
        x = pila.pop()
        if isinstance(x, dict):
            if x.get("Label") == before:
                x["Label"] = after
                n += 1
            pila.extend(x.values())
        elif isinstance(x, list):
            pila.extend(x)
    return n


def repair(
    folder: Path,
    *,
    blob_bytes: bytes | None = None,
    lib: dict[str, Protocol] | None = None,
    touch_labels: bool = True,
) -> dict:
    """Leaves ONE saved folder applicable, or says exactly why it can't.

    Order, from least to most invasive:

      1. `normalize_folder()` -- manifest + protocol definitions from the
         library. Never touches device data.
      2. only if the glyph gate is still red AND `touch_labels`: rewrite
         the button `Label`s that make the table ambiguous, trying
         `_variantes()` in order and keeping the first that turns the gate
         green. Every rewrite is recorded in `manifest.reparaciones` with
         its before/after, so it is auditable and reversible by hand.

    A protocol that is on nobody's disk is NOT repairable and this says so
    with its name -- it cannot be invented.
    """
    if lib is None:
        lib = available_protocols()
    hechos: list[str] = []
    norm = normalize_folder(folder, lib=lib)
    hechos.extend(norm.get("changes") or [])

    if blob_bytes is None:
        ref = blob_referencia()
        blob_bytes = ref.read_bytes() if ref else None

    d = diagnose(folder, blob_bytes=blob_bytes, lib=lib)
    if d["aplicable"] or d["missing_category"] != "glyphs" or not touch_labels:
        return {"ok": d["aplicable"], "changes": hechos, "diagnostico": d}

    config_path = folder / CONFIG_NAME
    manifest_path = folder / "manifest.json"
    conflictivas = (d.get("glyphs") or {}).get("palabras_conflictivas") or []
    reparaciones = []
    for palabra in conflictivas:
        applied = None
        for variante in _variantes(palabra):
            config = _read_json(config_path)
            if config is None:
                break
            if not _reemplazar_label(config, palabra, variante):
                continue  # the word isn't a Label (it's a Name): can't be touched
            original = config_path.read_text()
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=1))
            _GLIFOS_CACHE.clear()
            g = glyph_gate(config_path, blob_bytes)
            table = _glyph_table(config_path, blob_bytes)
            dibujable = glyphs.codificar(variante, table) is not None
            if g["ok"] and dibujable:
                applied = variante
                break
            config_path.write_text(original)  # put it back exactly as it was
            _GLIFOS_CACHE.clear()
        if applied:
            reparaciones.append(
                {
                    "campo": "Label",
                    "before": palabra,
                    "after": applied,
                    "reason": (
                        "%r made the glyph reading ambiguous and the fixed "
                        "%r label could not be written" % (palabra, ETIQUETA_VOLVER)
                    ),
                }
            )
            hechos.append("button label %r -> %r" % (palabra, applied))
            break  # one is enough by construction: the gate is green again

    if reparaciones:
        manifest = _read_json(manifest_path) or {}
        # `setdefault` is NOT enough here: it returns the value already
        # there when the key exists, and on disk it exists with `null` -- a
        # manifest already repaired and then restored from a backup ends up
        # exactly like that. The `.extend()` on that `None` blew up AFTER
        # repairing the file: the repair was done and the function died noting
        # it, i.e. the screen said "couldn't do it" about something that WAS done.
        previas = manifest.get("reparaciones")
        manifest["reparaciones"] = (
            previas if isinstance(previas, list) else []
        ) + reparaciones
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))

    _GLIFOS_CACHE.clear()
    final = diagnose(folder, blob_bytes=blob_bytes, lib=lib)
    return {"ok": final["aplicable"], "changes": hechos, "diagnostico": final}


def repair_all(
    *, blob_bytes: bytes | None = None, touch_labels: bool = True
) -> list[dict]:
    """`repair()` over everything on disk. Returns one entry per folder,
    with `dir` and whether it ended applicable."""
    lib = available_protocols()
    if blob_bytes is None:
        ref = blob_referencia()
        blob_bytes = ref.read_bytes() if ref else None
    salida = []
    for jsn in _raw_disk_configs():
        r = repair(
            jsn.parent,
            blob_bytes=blob_bytes,
            lib=lib,
            touch_labels=touch_labels,
        )
        r["dir"] = jsn.parent.name
        salida.append(r)
    return salida


if __name__ == "__main__":  # pragma: no cover -- manual self-check
    lib = available_protocols()
    print("configs on disk:", [str(p.parent.name) for p in disk_configs()])
    print("protocols:", {n: p.origin for n, p in lib.items()})
    print("vocabulary:", len(vocabulary()), "words")
    print("store:", almacen.PATH)
    ref = blob_referencia()
    print("reference blob:", ref)
    for d in diagnose_all():
        print(
            "  %-52s aplicable=%-5s %s"
            % (d["dir"][:52], d["aplicable"], d["reason"] or "")
        )
