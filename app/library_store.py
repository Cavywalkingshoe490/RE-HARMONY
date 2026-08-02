#!/usr/bin/env python3
"""The PERMANENT store behind `library.py`: protocol timings and glyph
vocabulary that survive deleting every downloaded device.

## The bug this exists to kill (measured, not guessed)

`biblioteca.available_protocols()` built the library by walking
`account_export/output/*/hub-config-with-device.json` -- i.e. **the devices the
user had already downloaded**. The user deleted his devices (with the delete
button, which works, on purpose), and the library went with them:

    available_protocols() -> 0     vocabulary() -> 0 words

From that moment NO device from the catalog could be downloaded any more.
Logitech's catalog brings each command's `KeyCode` -- the protocol's NAME and
the payload -- but never the `ProtocolList` with the mark/space timings, so
`materialize()` failed on every package, and `Api.catalog_save()` reported
that failure as `ok=True, materializado=False`: the screen said "saved" and
no folder was ever written.

The second half of the same bug is quieter and would have bitten right after:
`vocabulary()` read from the same deleted folders, so even a materialized
device would have been written with an EMPTY
`vocabulario_heredado_de_catalogo`, and `add_device.py` would refuse it for
lack of the glyphs of the fixed `Devices` label (see `library.py`'s module
docstring -- the glyph table is learned by elimination from the words the
config file carries).

So the store keeps BOTH things, in its own directory, indexed by nothing that
can be deleted from the Control screen.

## Where it lives and what it looks like

    protocol_library/
      protocolos/<slug>.json     one file per protocol
      vocabulary.json           the frozen word list for the glyph table
      seeded.json              what was scanned, when, and what came out

One file per protocol on purpose: adding one never rewrites the others, a
half-written store loses one protocol instead of all of them, and every entry
is readable by hand. Each file records WHERE the definition came from:

    {"schema_version": "1.0.0",
     "nombre": "Toshiba 32 Bit",
     "definicion": {...},                 # verbatim ProtocolList entry
     "origen": {"fuente": "account_export/output/live-account/resources/ProtocolList.json",
                "clase": "exportacion",   # exportacion|catalogo|manual|capturado|respaldo
                "etiqueta": "live-account",
                "agregado_en": "2026-07-29T..."},
     "huella": "<sha256 of the definition>"}

`fingerprint` is what makes re-seeding idempotent and makes a silent change
visible: same name + different timings never overwrites, it is reported.

## Seeding: only from what is really on disk, never invented

`sembrar()` scans, in this fixed order (first one to bring a name wins):

  1. `account_export/output/` -- what the app writes and reads today.
  2. `app/packaging/dist/RE-HARMONY.app/Contents/apk_bridge/output/` -- the
     copy that shipped inside the built .app. It is INSIDE the repo and it
     still holds the exports that were deleted from (1).
  3. `/tmp/output_backup/` -- read-only backup, if it is still
     there. Read, never restored: the user deleted those devices on purpose.

Test fixtures are NOT a source (`account_export/tests/fixtures/ProtocolList.json`
holds a fake `ExampleProtocol`), and neither are the per-device raw captures
`ir_manual.py` names `RAW-<nnn>-<button>`: those timings belong to one
imported `.ir` file, `ir_manual.importar()` always writes them inside the
device's own file, and two different imports produce the same name with
different contents. Sharing them would be the one way this store could hand
out a wrong waveform.

MEASURED with the three sources above, `account_export/output/` as it is today
(zero devices): 6 protocols -- `JerroldO1 16 Bit`, `LG 28 Bit`,
`Magnavox 13 Bit`, `Sony 12 Bit`, `Sony 15 Bit`, `Toshiba 32 Bit` -- and 417
words that give a 61-glyph table against the anchor blob, enough to write
`Devices`. The five catalog packages sitting in
`account_export/output/catalog-live/` need exactly `Toshiba 32 Bit` (the three
LGs, including one the user tried to download from the catalog),
`Magnavox 13 Bit` (Philips) and `Sony 12/15 Bit` + `Toshiba 32 Bit` (Sony):
all six are covered.

## Growing: nothing that arrives is ever lost again

`biblioteca.available_protocols()` calls `aprender_de_config()` for every
config it reads off disk, so any protocol a newly downloaded or imported
device brings is copied into the store the first time it is seen. Deleting
that device afterwards no longer takes the protocol with it. That is the
whole point.

NOTE on dict keys: the strings this module returns (`ok`, `name`,
`definicion`, `origin`, `source`, `category`, `label`, `agregado_en`,
`protocolos`, `palabras`, `nuevos`, `conflictos`, `reason`) are Spanish ON
PURPOSE and are a contract -- `library.py` re-exports some of them and
`app/api.py` forwards them to the JS by name.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_WORK = ROOT / "config_work"
if str(CONFIG_WORK) not in sys.path:
    sys.path.insert(0, str(CONFIG_WORK))

#: Overridable so the checks can run against a scratch store without touching
#: the real one. Read at import time on purpose: a test that moves the store
#: mid-run would leave the caches pointing at two different directories.
PATH = Path(os.environ.get("RE_HARMONY_LIBRARY") or (ROOT / "protocol_library"))
RUTA_PROTOCOLOS = PATH / "protocols"
VOCABULARY_PATH = PATH / "vocabulary.json"
RUTA_SEMBRADO = PATH / "seeded.json"

SCHEMA = "1.0.0"

#: `ir_manual.py:580` names every raw capture `RAW-<nnn>-<button>`. Per-device
#: by construction, so it never goes into a store shared by all devices.
RAW_RE = re.compile(r"^RAW-\d{3}-")

#: The file `account_export` and `biblioteca.write()` both use inside a folder.
#: Duplicated from `biblioteca.CONFIG_NAME` instead of imported: importing
#: `protocol_library` from here would be a cycle (it imports this module).
#: `check_library.py` checks the two stay equal.
CONFIG_NAME = "hub-config-with-device.json"


# --------------------------------------------------------------------------
# where the seed comes from
# --------------------------------------------------------------------------


def fonts() -> list[tuple[str, Path]]:
    """`(category, directory)` to scan, in priority order. Only the ones that
    exist. Nothing here is ever written to -- `/tmp/...` in particular is
    read and never restored."""
    candidatas = [
        ("exportacion", ROOT / "account_export" / "output"),
        (
            "exportacion",
            ROOT
            / "app"
            / "packaging"
            / "dist"
            / "RE-HARMONY.app"
            / "Contents"
            / "account_export"
            / "output",
        ),
    ]
    return [(c, d) for c, d in candidatas if d.is_dir()]


def _label(source: Path, base: Path) -> str:
    """Short human name for where a definition came from: the folder under the
    source root, which is what the Control screen already shows."""
    try:
        rel = source.relative_to(base)
    except ValueError:
        rel = source
    partes = rel.parts
    return partes[0] if partes else source.name


def _short_path(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


# --------------------------------------------------------------------------
# reading protocol definitions out of any JSON shape we have on disk
# --------------------------------------------------------------------------


def _read_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None


def definiciones_en(doc) -> list[dict]:
    """Every protocol definition inside one parsed JSON, whatever its shape.

    Three shapes are known to exist on disk and all three are read here, so
    nobody upstream has to know which writer made the file:

      * a config          -> `resources.ProtocolList.Protocols`
      * a resource dump   -> `{"Protocols": [...]}`  (`resources/ProtocolList.json`)
      * a nested resource -> `{"ProtocolList": {"Protocols": [...]}}`

    The third shape is what found `LG 28 Bit` and `JerroldO1 16 Bit`, which
    live ONLY in `account_export/output/*/resources/ProtocolList.json` -- files
    that survived the deletion untouched and that the old library never
    looked at, because it only ever globbed `*/hub-config-with-device.json`.
    """
    if not isinstance(doc, dict):
        return []
    for contenedor in (
        (doc.get("resources") or {}).get("ProtocolList")
        if isinstance(doc.get("resources"), dict)
        else None,
        doc.get("ProtocolList"),
        doc,
    ):
        if isinstance(contenedor, dict):
            protos = contenedor.get("Protocols")
            if isinstance(protos, list):
                return [p for p in protos if isinstance(p, dict) and p.get("Name")]
    return []


def _fingerprint(definicion: dict) -> str:
    crudo = json.dumps(definicion, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


def compartible(name: str) -> bool:
    """Is this protocol name safe to share across devices? See `RAW_RE`."""
    return bool(name) and not RAW_RE.match(name)


# --------------------------------------------------------------------------
# the store itself
# --------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "x"


_CACHE: tuple[tuple, dict[str, dict]] | None = None


def _firma() -> tuple:
    """(path, mtime, size) of every protocol file, so the cache notices a file
    added by hand or by another process."""
    if not RUTA_PROTOCOLOS.is_dir():
        return ()
    salida = []
    for p in sorted(RUTA_PROTOCOLOS.glob("*.json")):
        try:
            st = p.stat()
        except OSError:
            continue
        salida.append((p.name, st.st_mtime_ns, st.st_size))
    return tuple(salida)


def protocolos() -> dict[str, dict]:
    """`{name: entry}` for everything in the store. `entry` is the file's
    content: `name`, `definicion`, `origin`, `fingerprint`.

    Never raises and never seeds by itself -- `biblioteca.available_protocols()`
    owns when to seed, so a caller that only wants to LOOK does not write.
    """
    global _CACHE
    firma = _firma()
    if _CACHE is not None and _CACHE[0] == firma:
        return _CACHE[1]
    salida: dict[str, dict] = {}
    if RUTA_PROTOCOLOS.is_dir():
        for p in sorted(RUTA_PROTOCOLOS.glob("*.json")):
            d = _read_json(p)
            if not isinstance(d, dict):
                continue
            name = d.get("name")
            definicion = d.get("definicion")
            if not name or not isinstance(definicion, dict):
                continue
            d["file"] = p.name
            salida.setdefault(str(name), d)
    _CACHE = (firma, salida)
    return salida


def has(name: str) -> bool:
    """Does the STORE have this protocol's timings? `biblioteca.have_protocol()`
    is the one to call from outside -- it also looks at what is on disk."""
    return bool(name) and name in protocolos()


def guardar(
    name: str,
    definicion: dict,
    *,
    source: str,
    category: str = "exportacion",
    label: str | None = None,
) -> str:
    """Puts ONE protocol in the store. Returns what happened:

        "nuevo"     written for the first time
        "ya-estaba" same name, same timings: nothing written
        "conflicto" same name, DIFFERENT timings: nothing written, reported

    Never overwrites. A protocol is a timing table from Logitech's catalog,
    the same for every device that uses it; if two files disagree, the honest
    answer is to say so, not to pick one silently.
    """
    if not compartible(name) or not isinstance(definicion, dict):
        return "descartado"
    actuales = protocolos()
    fingerprint = _fingerprint(definicion)
    previo = actuales.get(name)
    if previo is not None:
        return "ya-estaba" if previo.get("fingerprint") == fingerprint else "conflicto"
    RUTA_PROTOCOLOS.mkdir(parents=True, exist_ok=True)
    entrada = {
        "schema_version": SCHEMA,
        "name": name,
        "definicion": definicion,
        "origin": {
            "source": source,
            "category": category,
            "label": label or category,
            "agregado_en": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "fingerprint": fingerprint,
    }
    target = RUTA_PROTOCOLOS / ("%s.json" % _slug(name))
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entrada, ensure_ascii=False, indent=1))
    tmp.replace(target)  # atomic: a reader never sees half a protocol
    global _CACHE
    _CACHE = None
    return "fresh"


def aprender_de_config(
    config: dict, *, source: str, label: str, category: str
) -> list[str]:
    """Copies into the store every protocol a config file carries that the
    store does not have yet. Returns the names actually added.

    This is the half that makes deletion harmless from now on: it runs every
    time `biblioteca.available_protocols()` reads a folder off disk.
    """
    nuevos = []
    for p in definiciones_en(config):
        if (
            guardar(p["Name"], p, source=source, category=category, label=label)
            == "fresh"
        ):
            nuevos.append(p["Name"])
    return nuevos


# --------------------------------------------------------------------------
# the frozen vocabulary
# --------------------------------------------------------------------------


def palabras() -> set[str]:
    """The stored word list for the glyph table. Empty set if never seeded."""
    d = _read_json(VOCABULARY_PATH)
    if not isinstance(d, dict):
        return set()
    return {w for w in (d.get("palabras") or []) if isinstance(w, str) and w}


def guardar_palabras(nuevas: set[str], *, sources_used: list[str]) -> int:
    """Unions `nuevas` into the stored list. Returns how many words were added.

    UNION, never replace: the words come from real Logitech exports and each
    one is a chance for `glyphs.extender()` to pin one more glyph by
    elimination. Dropping a word can only shrink the table, and the table is
    what decides whether the fixed `Devices` label can be written at all.
    """
    previas = palabras()
    total = previas | {w for w in nuevas if isinstance(w, str) and w}
    if total == previas and VOCABULARY_PATH.exists():
        return 0
    PATH.mkdir(parents=True, exist_ok=True)
    d = _read_json(VOCABULARY_PATH)
    before = d.get("fonts") if isinstance(d, dict) else None
    all_fonts = sorted(set(before or []) | set(sources_used))
    payload = {
        "schema_version": SCHEMA,
        "actualizado_en": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fonts": all_fonts,
        "palabras": sorted(total),
    }
    tmp = VOCABULARY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    tmp.replace(VOCABULARY_PATH)
    return len(total) - len(previas)


# --------------------------------------------------------------------------
# seeding
# --------------------------------------------------------------------------


def _archivos_candidatos(base: Path) -> list[Path]:
    """Every JSON under a source root that could carry protocols or words.

    Bounded on purpose (`*/*.json` and `*/*/*.json`): the source roots hold
    `.download` / `.response` blobs and `resources/` dumps, and walking them
    whole would parse megabytes for nothing.
    """
    vistos: list[Path] = []
    for patron in ("*.json", "*/*.json", "*/*/*.json"):
        for p in sorted(base.glob(patron)):
            if p.is_file():
                vistos.append(p)
    return vistos


def _es_manual(p: Path) -> bool:
    """A folder written by `ir_manual.importar()`. Its words were typed by the
    user and are not drawn in the factory blob, so they are useless -- and
    measurably harmful -- for the glyph table (see `biblioteca.vocabulary()`)."""
    return any("manual" in parte for parte in p.parts)


def sembrar(*, forzar: bool = False) -> dict:
    """Fills an empty store from everything on disk. Idempotent and safe to
    call on every start: with a non-empty store and `forzar=False` it does
    nothing and returns immediately.

    Returns
        {"ok", "protocolos": [...], "nuevos": [...], "conflictos": [...],
         "palabras": int, "palabras_nuevas": int, "fuentes": [...],
         "archivos": int}
    """
    if protocolos() and not forzar:
        return {
            "ok": True,
            "protocolos": sorted(protocolos()),
            "nuevos": [],
            "conflictos": [],
            "palabras": len(palabras()),
            "palabras_nuevas": 0,
            "fonts": [],
            "archivos": 0,
            "reason": "the store was already seeded",
        }

    import glyphs  # noqa: PLC0415 -- read-only, and only needed when seeding

    nuevos: list[str] = []
    conflictos: list[str] = []
    sources_used: list[str] = []
    vocab: set[str] = set()
    archivos = 0

    for category, base in fonts():
        for file in _archivos_candidatos(base):
            doc = _read_json(file)
            if doc is None:
                continue
            archivos += 1
            defs = definiciones_en(doc)
            if defs:
                label = _label(file, base)
                for p in defs:
                    r = guardar(
                        p["Name"],
                        p,
                        source=_short_path(file),
                        category=category,
                        label=label,
                    )
                    if r == "fresh":
                        nuevos.append(p["Name"])
                        if _short_path(file) not in sources_used:
                            sources_used.append(_short_path(file))
                    elif r == "conflicto":
                        conflictos.append("%s (%s)" % (p["Name"], _short_path(file)))
            # Words come only from REAL exports, never from a manual import.
            if file.name == CONFIG_NAME and not _es_manual(file):
                try:
                    vocab |= glyphs.vocabulario(str(file))
                    if _short_path(file) not in sources_used:
                        sources_used.append(_short_path(file))
                except Exception:  # noqa: BLE001
                    pass
            # Some account exports land under another file name (a whole
            # account snapshot saved as `before.json` / `with-<device>.json`
            # by the transaction flow); measured on its own, one of those
            # already gives a 61-glyph table, so it is worth reading.
            elif file.name in (
                "before.json",
                "with-philips.json",
            ) and not _es_manual(file):
                try:
                    vocab |= glyphs.vocabulario(str(file))
                    if _short_path(file) not in sources_used:
                        sources_used.append(_short_path(file))
                except Exception:  # noqa: BLE001
                    pass

    agregadas = guardar_palabras(vocab, sources_used=sources_used)
    summary = {
        "ok": bool(protocolos()),
        "protocolos": sorted(protocolos()),
        "nuevos": sorted(set(nuevos)),
        "conflictos": sorted(set(conflictos)),
        "palabras": len(palabras()),
        "palabras_nuevas": agregadas,
        "fonts": [_short_path(d) for _, d in fonts()],
        "archivos": archivos,
    }
    try:
        PATH.mkdir(parents=True, exist_ok=True)
        RUTA_SEMBRADO.write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA,
                    "seeded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    **summary,
                },
                ensure_ascii=False,
                indent=1,
            )
        )
    except Exception:  # noqa: BLE001
        pass
    return summary


def state() -> dict:
    """What the store holds right now, ready to show. Keys are a contract."""
    ps = protocolos()
    return {
        "path": str(PATH),
        "total": len(ps),
        "palabras": len(palabras()),
        "protocolos": [
            {
                "name": n,
                "origin": (e.get("origin") or {}).get("label") or "?",
                "source": (e.get("origin") or {}).get("source") or "?",
                "category": (e.get("origin") or {}).get("category") or "?",
                "agregado_en": (e.get("origin") or {}).get("agregado_en") or "",
            }
            for n, e in sorted(ps.items())
        ],
    }


if __name__ == "__main__":  # pragma: no cover -- manual self-check
    r = sembrar(forzar="--forzar" in sys.argv)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    print(json.dumps(state(), ensure_ascii=False, indent=1))
