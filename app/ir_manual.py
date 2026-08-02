#!/usr/bin/env python3
"""Upload an `.ir` by hand for a device that is NOT in Logitech's catalog.

Reimplements NOTHING: not the waveform synthesis, not the blob format, not
the `.ir` parser.

* **The `.ir` parser is the only one in the whole project**:
  `config_work/read_ir.py`. This module imports it and uses its pieces
  (`_bloques`, `_bloque_raw`, `_bloque_parsed`, `ALIAS_PROTOCOLOS`,
  `PROTOCOLOS_EMBEBIDOS`, `IrParseError`). There used to be TWO parsers (one
  here and another there) that had to be kept in sync; `read_ir.py`'s was
  chosen because its round trip is verified BYTE FOR BYTE against real
  waveforms from the factory blob and its `parsed` formula (protocol/
  address/command) is verified against the Hub's 260 real `KeyCode` values.
* **What gets built for the pipeline is built HERE**
  (`build_resources()`), not in `leer_ir.build_resources()` -- that
  version flattens every command into a fixed segment with
  `TotalLength = sum(atoms)`, which leaves the gap between frames at ZERO
  and glues one frame's tail to the next one's head (two marks in a row = a
  single long carrier = the device sees a different keypress). See "The
  framing" below. `read_ir.py` was not touched (project rule:
  `config_work/` only accepts NEW files).

## What it understands from the `.ir`

Flipper Zero / public IRDB blocks, separated by `#`:

* `type: raw` -- the whole waveform in microseconds (`data:`, always starts
  on a mark) + `frequency:`. Always supported: does not depend on any
  formula.
* `type: parsed` -- `protocol:` + `address:` + `command:`. Supported ONLY
  for protocols whose packing formula is verified against real Hub commands
  (`leer_ir.ALIAS_PROTOCOLOS`: NEC -> "Toshiba 32 Bit", SIRC/SIRC12 -> "Sony
  12 Bit", SIRC15 -> "Sony 15 Bit"). Any other one (`NECext`, `SIRC20`,
  `Samsung32`, `RC5`, ...) is REPORTED as unsupported with the reason --
  never guessing a timing.

A block that can't be imported is never silently dropped: it shows up in
`analyze()` with `soportado: false` and the exact reason.

## The framing (what really decides whether the device obeys)

`sintir.sintetizar()` emits: a lead-in, then N frames, each followed by
`gap = max(TotalLength - frame_length, 0)`. If the gap is 0 and the frame
ENDS ON A MARK, its final mark ends up glued to the next frame's opening
mark: the transmitter sends ONE continuous carrier. The factory blob has
ZERO adjacent-mark pairs. Because of that:

* `parsed` -> the protocol built from the measured timings in
  `leer_ir.PROTOCOLOS_TIMINGS` (SIRC and NEC; see that table for the public
  sources and for what is deliberately NOT reproduced from anybody's
  protocol database) gets emitted, and the `KeyCode` carries the
  already-computed `value`:
  `G:Toshiba 32 Bit:()(0x40BF12ED)():3`. This reuses the ENTIRE machinery
  already validated 234/234 (`KeyCode.Start` + `KeyCode.Repeat`,
  `TotalLength`, gap), including NEC's short "still holding" frame.
* `raw` -> fixed segment (`Atoms` with no `Payload`, the same technique the
  blob itself uses for `Toshiba 32 Bit KeyCodeRepeat`), but **normalized to
  always end in a space**, same as that factory segment (its `Atoms` end in
  a 96,077 us space and its `TotalLength` is 0):
    - if the capture ends on a MARK (the common case: the Flipper does not
      record the trailing silence, which is why `data:` usually has an ODD
      count of numbers), a space of `TARGET_GAP_US` is appended;
    - if it ends on a space and that space is already >= `MIN_GAP_US`,
      it is respected AS IS -- nothing gets added to it (the previous
      version added 40 ms on top and doubled the gap);
    - if it ends on a space shorter than `MIN_GAP_US`, it is stretched
      to `TARGET_GAP_US`.
  `TotalLength` = sum of the already-normalized atoms, i.e. gap 0 with the
  silence traveling inside the segment, just like in the factory.

[ASSUMED, not verified against a real device] `TARGET_GAP_US` = 40 ms:
it's the gap measured in the factory blob for Toshiba (40,222 us) and
bigger than Sony's (25,200 us). If the target device expects a different
period, it may not respond: test ONE command before trusting the rest.

## The button labels (the defect that broke the most common case)

`add_device.py` draws each button's label from the config's `Label`
(`hub_labels`, joining `Commands[].FunctionId-` against
`FunctionList.FunctionMaps[].FunctionGroups[].Functions[]` of the same
`DeviceId-`), and falls back to `split_camel(Name)` if there is no
`Label`. The standard names in a Flipper `.ir` (`Vol_up`, `Ch_next`,
`Fast_Forward`, `Vol+`) carry `_` and `+`, which **do not exist in the
device's glyph set**: the old importer wrote them raw into
`Commands[].Name`, the device saved "ok", and `add_device.py` would blow up
much later with "label 'Vol_up' does not fit in an 81 px cell (measures
None)".

Now this module:

1. **normalizes** the name into a drawable label (`SUBSTITUTIONS`: `_` ->
   space, `+` -> " Plus", etc.) and emits it as `Label` in its own
   `FunctionList`, so the label that gets validated is EXACTLY the one that
   is going to be drawn (it does not depend on `split_camel`);
2. runs the SAME three checks `add_device.py` runs on every label: that
   some font can DRAW it (`fonts.choose_detail`), that the glyph table
   can WRITE it (`glyphs.extender`/`codificar`), and that it FITS in the
   81 px cell (`fonts.width`, with the same abbreviation attempt via
   `QUALIFIERS` that `add_device.abbreviate_if_needed` does);
3. a command whose label doesn't pass is left as NOT IMPORTABLE, with the
   reason and the missing letters -- shown in the UI before importing,
   never reaching disk.

## The device's name: TWO checks, not one

`fonts.choose_detail()` (strokes: which letters exist in some font) is
NOT enough: `add_device.py` also has to be able to WRITE every letter with
the learned glyph table (`glyphs.py`), which stores indices, not ASCII.
With the small vocabulary of a manual `.ir`, not even the fixed label
`Devices` that every new device needs gets learned; that's why
`_extra_vocabulary()` inherits the plain-text words from the REAL configs
already downloaded from the catalog. With that inherited vocabulary active,
"Acme" and "Mute" DO pass (measured); without it, they don't. "Qwerty"
still doesn't pass (the hardware has no `Q`).
"""

from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
OUTPUT_BRIDGE = RAIZ / "account_export" / "output"
CONFIG_WORK = RAIZ / "config_work"
if str(CONFIG_WORK) not in sys.path:
    sys.path.insert(0, str(CONFIG_WORK))

import fonts  # noqa: E402  -- read-only, same as app/generate.py
import glyphs  # noqa: E402  -- read-only: learns/reads the table, writes nothing
import read_ir  # noqa: E402  -- THE project's .ir parser (not modified)
import synth_ir  # noqa: E402  -- only for MAX_PALABRA (nothing gets synthesized here)

Atomo = read_ir.Atomo
IrParseError = read_ir.IrParseError

FRECUENCIA_DEFECTO = 38_000
MAX_PALABRA = synth_ir.MAX_PALABRA  # 0x7FFF: a longer mark would get clipped
MAX_ATOMOS = 8192  # sanity bound: a real capture measures in the tens, not thousands

#: gap between frames when the `.ir` doesn't carry one. See "The framing" above.
TARGET_GAP_US = 40_000
#: below this, the capture's trailing silence is not treated as a gap
#: between frames (it gets stretched to the target instead).
MIN_GAP_US = 20_000

#: `add_device.py:ATTR_ETIQUETA` -- the font attribute for the 6 labels of
#: the command grid. The literal is repeated (`add_device.py` is not
#: imported as a module: it's one of the gates only ever invoked by
#: subprocess), same as `app/generate.py` already does.
ATTR_ETIQUETA = 0x09
#: `add_device.py:CELL_WIDTH` and `add_device.py:QUALIFIERS`, same reason.
CELL_WIDTH = 81
QUALIFIERS = (
    ("Direction ", ""),
    ("Input ", ""),
    ("Channel ", "Ch "),
    ("Volume ", "Vol "),
)

#: `add_device.py:ETIQUETA_VOLVER`: the fixed label EVERY new device needs
#: to be able to write, no matter its name.
ROTULO_VOLVER = "Devices"

#: real bit widths of each field per `parsed` protocol. If the `.ir` brings
#: a bigger `address`/`command`, the formula would TRUNCATE it silently (two
#: different commands would give the same waveform): rejected instead of
#: truncated.
BITS_PARSED = {
    "NEC": (8, 8),
    "SIRC": (5, 7),
    "SIRC12": (5, 7),
    "SIRC15": (8, 7),
}

#: normalization of command names into drawable labels. The device's glyph
#: set has no `_` or `+` (nor `Q`, `X`, `Z`), and Flipper's standard names
#: use them all the time. Conservative replacements, in order; whatever is
#: left undrawable does NOT get imported (the letter is named).
SUBSTITUTIONS = (
    ("_", " "),
    ("+", " Plus"),
    ("&", " and "),
    ("#", " Num "),
    ("*", " Star "),
    ("=", " "),
    ("|", " "),
    ("~", " "),
    ("(", " "),
    (")", " "),
    ("[", " "),
    ("]", " "),
)


# --------------------------------------------------------------------------
# .ir parsing -- entirely delegated to config_work/read_ir.py
# --------------------------------------------------------------------------


def _range_ok(name: str, protocolo: str, direccion: int, command: int) -> None:
    """Rejects a `parsed` whose `address`/`command` does not fit in the
    protocol's real bit width. Without this, `NEC address: 12 34 00 00`
    would give EXACTLY the same waveform as `12 00 00 00` (the formula's
    `& 0xFF`) -- silent truncation, exactly what this project avoids. The
    typical case is a `NECext` mislabeled as `NEC`."""
    bits_a, bits_c = BITS_PARSED[protocolo]
    if direccion >> bits_a:
        raise IrParseError(
            "%r: 'address' %#x does not fit in %s's %d bits (it would "
            "truncate to %#x and two different commands would give the "
            "same waveform). If this is really NECext or another extended "
            "protocol, upload it as 'type: raw'."
            % (name, direccion, protocolo, bits_a, direccion & ((1 << bits_a) - 1))
        )
    if command >> bits_c:
        raise IrParseError(
            "%r: 'command' %#x does not fit in %s's %d bits (it would "
            "truncate to %#x). If this is really another protocol, upload "
            "it as 'type: raw'."
            % (name, command, protocolo, bits_c, command & ((1 << bits_c) - 1))
        )


def _marks_ok(name: str, atomos: list) -> None:
    """Rejects a mark longer than a blob word can hold. `sintir.a_palabras`
    CLAMPS marks (it only splits spaces): letting it through would mean
    emitting 32,767 us where the file asked for 60,000, with no warning at
    all. This module is the boundary with untrusted input, so it rejects."""
    for i, a in enumerate(atomos):
        if a.marca and a.us > MAX_PALABRA:
            raise IrParseError(
                "%r: mark #%d measures %d us and the blob cannot store more "
                "than %d us in one word (it would get clipped in silence). "
                "Check the capture: a real IR mark lasts milliseconds, not "
                "tens of thousands of microseconds." % (name, i, a.us, MAX_PALABRA)
            )
    if len(atomos) > MAX_ATOMOS:
        raise IrParseError(
            "%r: %d atoms (limit %d): that does not look like a single "
            "button's capture" % (name, len(atomos), MAX_ATOMOS)
        )
    if len(atomos) < 2:
        raise IrParseError("%r: at least 2 durations are needed" % name)


def _command_from_block(bloque: dict) -> read_ir.ComandoIR:
    """A block already split by `leer_ir._bloques` -> `ComandoIR`, with this
    boundary's extra rejections (field range, oversized marks). Raises
    `IrParseError` with the exact reason."""
    name = bloque.get("name") or "(unnamed)"
    kind = (bloque.get("type") or "").strip().lower()
    if kind == "raw":
        cmd = read_ir._bloque_raw(name, bloque)
        _marks_ok(name, cmd.atomos)
        return cmd
    if kind == "parsed":
        proto_id = (bloque.get("protocol") or "").strip()
        # `_bloque_parsed` already validates presence of protocol/address/
        # command and rejects protocols without a verified formula.
        cmd = read_ir._bloque_parsed(name, bloque, None)
        if proto_id in BITS_PARSED:
            _range_ok(name, proto_id, cmd.direccion, cmd.command)
        _marks_ok(name, cmd.atomos)
        return cmd
    raise IrParseError(
        "%r: 'type' has to be 'raw' or 'parsed', got %r" % (name, bloque.get("type"))
    )


def _parse_commands(path: Path) -> dict:
    """Everything needed, raw atoms included (internal use: `analyze()`
    trims this for the UI, `import_device()` uses it whole).

    The whole file is only rejected if it isn't an `.ir` at all
    (`leer_ir._bloques` requires the `Filetype: IR signals file` header). A
    single block that can't be imported does NOT bring down the whole file:
    it stays with `soportado: false` and its reason."""
    if not path.exists():
        return {"ok": False, "error": f"{path} does not exist"}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="latin-1")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"could not read the file: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"could not read the file: {exc}"}

    try:
        bloques = read_ir._bloques(text)
    except IrParseError as exc:
        return {"ok": False, "error": str(exc)}

    used: dict[str, int] = {}
    commands = []
    for b in bloques:
        raw_name = b.get("name") or "(unnamed)"
        n = used.get(raw_name, 0)
        used[raw_name] = n + 1
        name = raw_name if n == 0 else f"{raw_name} ({n + 1})"

        item = {
            "name": name,
            "kind": (b.get("type") or "unknown").strip().lower(),
            "protocolo": b.get("protocol"),
            "soportado": False,
            "reason": None,
            "frecuencia": None,
            "atomos": None,
            "rotulo": None,
            "avisos": [],
            "_atomos_crudos": None,
            "_proto_hub": None,
            "_value": None,
        }
        try:
            cmd = _command_from_block(dict(b, name=name))
        except IrParseError as exc:
            item["reason"] = str(exc)
            commands.append(item)
            continue

        avisos: list[str] = []
        proto_hub = value = None
        if cmd.kind == "parsed":
            proto_hub, formula = read_ir.ALIAS_PROTOCOLOS[cmd.protocolo]
            value = formula(cmd.direccion, cmd.command)
        else:
            impares = len(cmd.atomos) % 2 == 1
            if impares:
                avisos.append(
                    "the capture ends on a mark (it has no trailing "
                    "silence): a %d ms gap is added between frames"
                    % (TARGET_GAP_US // 1000)
                )
            elif cmd.atomos[-1].us < MIN_GAP_US:
                avisos.append(
                    "the capture's trailing silence (%d us) is shorter than "
                    "%d us: stretched to %d us"
                    % (cmd.atomos[-1].us, MIN_GAP_US, TARGET_GAP_US)
                )

        item.update(
            soportado=True,
            frecuencia=cmd.frecuencia or FRECUENCIA_DEFECTO,
            atomos=len(cmd.atomos),
            avisos=avisos,
            rotulo=label_for(name),
            _atomos_crudos=cmd.atomos,
            _proto_hub=proto_hub,
            _value=value,
        )
        commands.append(item)

    if not commands:
        return {"ok": False, "error": "the file declares no 'name:' block at all"}
    return {"ok": True, "file": str(path), "commands": commands}


def analyze(path: Path, blob: bytes | None = None) -> dict:
    """View for the UI: every command with whether it can be imported, why
    not if it can't, and WHICH LABEL the user is going to see on the remote.
    If `blob` is given, it also runs the full check (draw + write + fit in
    the cell) on every label -- which is what really decides whether
    `add_device.py` is going to be able to generate the blob."""
    r = _parse_commands(path)
    if not r["ok"]:
        return r
    internal_commands = r["commands"]
    if blob is not None:
        _mark_impossible_labels(internal_commands, blob)
    commands = [
        {k: v for k, v in c.items() if not k.startswith("_")} for c in internal_commands
    ]
    soportados = sum(1 for c in commands if c["soportado"])
    summary = {
        "total": len(commands),
        "soportados": soportados,
        "no_soportados": len(commands) - soportados,
        "raw": sum(1 for c in commands if c["kind"] == "raw"),
        "parsed": sum(1 for c in commands if c["kind"] == "parsed"),
    }
    return {
        "ok": True,
        "file": r["file"],
        "commands": commands,
        "summary": summary,
    }


# --------------------------------------------------------------------------
# labels: normalization + the three checks add_device.py runs
# --------------------------------------------------------------------------


def label_for(name: str) -> str:
    """`.ir` name -> label that is going to be DRAWN on the remote.

    Only the documented substitutions (`SUBSTITUTIONS`) + whitespace
    collapsing. Never cuts words or strips vowels (same rule as
    `device.abbreviate_if_needed`). Whatever remains undrawable gets
    rejected later, with the letter named."""
    s = name or ""
    for find, replace in SUBSTITUTIONS:
        s = s.replace(find, replace)
    s = re.sub(r"\s+", " ", s).strip()
    return s or "?"


def _missing_letters(text: str, table: dict) -> list[str]:
    inv = {v: k for k, v in table.items()}
    return sorted({c for c in text if c not in inv})


def _width_or_none(text: str, blob: bytes, table: dict) -> int | None:
    """The width in px of the text with `ATTR_ETIQUETA`, or `None` if that
    font can't draw it whole -- the same arithmetic as
    `device.text_width` (via `fonts.width`). It also requires the
    LEARNED glyph table and `fonts`'s encoding to agree for every letter:
    if they diverge, the width measured here would not be the one
    `add_device.py` actually draws."""
    cod = glyphs.codificar(text, table)
    if cod is None:
        return None
    esperado = bytes(fonts.INV[c] for c in text if c in fonts.INV) + b"\x00"
    if len(esperado) != len(cod) or esperado != cod:
        return None
    try:
        return fonts.width(text, ATTR_ETIQUETA, blob)
    except ValueError:
        return None


def check_label(rotulo: str, blob: bytes, table: dict) -> dict:
    """The THREE checks `add_device.py` runs on a button label:

    1. some font DRAWS it (`fonts.choose_detail`, context ATTR_ETIQUETA)
    2. the glyph table WRITES it (`glyphs.codificar`)
    3. it FITS in the 81 px cell, or fits after a `QUALIFIERS`
       substitution (exactly the same attempt, in the same order, as
       `device.abbreviate_if_needed`)

    Returns `{'ok', 'final', 'reason', 'missing', 'width'}`. `final` is the
    label as it is actually going to be drawn (may come back abbreviated)."""
    fu = fonts.choose_detail(rotulo, blob, contexto=ATTR_ETIQUETA)
    missing_font_chars = sorted(fu.get("missing") or [])
    if fu.get("atributo") is None:
        return {
            "ok": False,
            "final": rotulo,
            "missing": missing_font_chars,
            "width": None,
            "reason": "no font can draw it (%s)"
            % (
                ("missing characters: %s" % ", ".join(map(repr, missing_font_chars)))
                if missing_font_chars
                else (fu.get("warning") or "?")
            ),
        }
    missing_glyphs = _missing_letters(rotulo, table)
    if missing_glyphs:
        return {
            "ok": False,
            "final": rotulo,
            "missing": missing_glyphs,
            "width": None,
            # NOT "with the available vocabulary": the glyph table is the
            # complete one (71 characters, read from the remote's own
            # fonts), so a missing letter is missing from the HARDWARE and
            # no download can add it. Saying "vocabulary" sent people to
            # the Catalog tab to fix something the catalog cannot fix.
            "reason": "the remote has no glyph for these characters (its "
            "fonts carry 71, and Q, X and Z are not among them): %s"
            % ", ".join(map(repr, missing_glyphs)),
        }
    a = _width_or_none(rotulo, blob, table)
    if a is not None and a <= CELL_WIDTH:
        return {"ok": True, "final": rotulo, "missing": [], "width": a, "reason": None}
    for find, replace in QUALIFIERS:
        if find in rotulo:
            corto = re.sub(r"\s+", " ", rotulo.replace(find, replace)).strip()
            a2 = _width_or_none(corto, blob, table)
            if a2 is not None and a2 <= CELL_WIDTH:
                return {
                    "ok": True,
                    "final": corto,
                    "missing": [],
                    "width": a2,
                    "reason": None,
                }
    return {
        "ok": False,
        "final": rotulo,
        "missing": [],
        "width": a,
        "reason": "does not fit in the %d px cell (measures %s) and no "
        "QUALIFIERS abbreviation is enough -- use a shorter name in the .ir"
        % (CELL_WIDTH, a),
    }


def _mark_impossible_labels(commands: list[dict], blob: bytes) -> list[dict]:
    """Marks `soportado=False` (with a reason) on every command whose label
    cannot be drawn/written/fit. Iterates to a fixed point because labels
    are part of the vocabulary that feeds `glyphs.extender()`: removing one
    can (in theory) take material away from learning the rest."""
    for _ in range(4):
        soportados = [c for c in commands if c["soportado"]]
        if not soportados:
            return commands
        table = _table_for(soportados, blob)
        change = False
        for c in soportados:
            rev = check_label(c.get("rotulo") or label_for(c["name"]), blob, table)
            if rev["ok"]:
                c["rotulo"] = rev["final"]
                if rev["final"] != label_for(c["name"]):
                    c.setdefault("avisos", []).append(
                        "the label was abbreviated to %r to fit in the cell"
                        % rev["final"]
                    )
            else:
                c["soportado"] = False
                c["reason"] = "label %r cannot be used: %s" % (
                    c.get("rotulo"),
                    rev["reason"],
                )
                change = True
        if not change:
            return commands
    return commands


def _table_for(supported_commands: list[dict], blob: bytes) -> dict:
    resources = build_resources(supported_commands, "?", "?", "?")
    snap = _snapshot(resources, _extra_vocabulary(), "rotulos")
    return _glyph_table(blob, snap)


# --------------------------------------------------------------------------
# synthetic JSON shaped like hub-config-with-device.json
# --------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "x"


def _normalized_atoms(atomos: list) -> list:
    """A `raw` waveform ready to be a fixed segment: always ends in a
    space, and that space is the gap between frames. See "The framing"."""
    at = [Atomo(a.marca, a.us) for a in atomos]
    if at[-1].marca:
        at.append(Atomo(False, TARGET_GAP_US))
    elif at[-1].us < MIN_GAP_US:
        at[-1] = Atomo(False, TARGET_GAP_US)
    return at


def _raw_protocol(idx: int, cmd_name: str, frecuencia: int, atomos) -> dict:
    at = _normalized_atoms(atomos)
    seg = {
        "Name": "Main",
        "Header": [],
        "Payload": None,
        "Trailer": [],
        "Atoms": [
            {
                "Type": 1 if a.marca else 0,
                "Value": a.us,
                "MinValue": None,
                "MaxValue": None,
            }
            for a in at
        ],
        # gap 0 on purpose: the silence already travels inside `Atoms`, same
        # as in the factory segment `Toshiba 32 Bit KeyCodeRepeat`.
        "TotalLength": sum(a.us for a in at),
    }
    return {
        "Name": f"RAW-{idx:03d}-{_slug(cmd_name)[:24]}",
        "CarrierFrequency": frecuencia,
        "IRSegments": [seg],
        "CodeSegments": [],
        # no `Start`: a raw capture has no frame distinct from "still
        # holding", the same one repeats. Nothing else is emitted: the
        # repeat count is a per-command parameter (today fixed at 3 in
        # `commands.press_wave`), and the bookkeeping fields a vendor
        # protocol-database row carries are neither read by anything here
        # nor this project's to reproduce.
        "KeyCode": {
            "Start": None,
            "Repeat": [{"SegmentName": "Main"}],
            "Finish": None,
        },
    }


def build_resources(
    supported_commands: list[dict],
    fabricante: str,
    modelo: str,
    device_name: str,
) -> dict:
    """`{"ProtocolList": ..., "DeviceList": ..., "FunctionList": ...}` -- the
    same shape `commands.load_hub_config()` / `add_device.py` already know how to
    read.

    * a `parsed` command references the REAL Hub protocol (literal copy),
      with its `value` in the `KeyCode`: the machinery validated 234/234 is
      reused;
    * a `raw` command gets its own single-fixed-frame protocol, already
      normalized to end in a space (see "The framing");
    * `FunctionList` carries each command's `Label`: the label that was
      validated is exactly the one `add_device.py` is going to draw.
    """
    protocolos: list[dict] = []
    by_name: dict[str, dict] = {}
    cmds = []
    funciones = []
    dev_id = (abs(hash((fabricante, modelo, device_name))) % 0x7FFFFFFF) or 1

    for i, c in enumerate(supported_commands):
        rotulo = c.get("rotulo") or label_for(c["name"])
        if c.get("_proto_hub"):
            protocol_name = c["_proto_hub"]
            if protocol_name not in by_name:
                proto = copy.deepcopy(read_ir.PROTOCOLOS_EMBEBIDOS[protocol_name])
                by_name[protocol_name] = proto
                protocolos.append(proto)
            keycode = "G:%s:()(0x%X)():3" % (protocol_name, c["_value"])
        else:
            proto = _raw_protocol(
                i,
                c["name"],
                c["frecuencia"] or FRECUENCIA_DEFECTO,
                c["_atomos_crudos"],
            )
            protocolos.append(proto)
            keycode = "G:%s:()(0x0)():3" % proto["Name"]

        fid = 700000 + i
        cmds.append(
            {
                "Name": c["name"],
                "CommandTypeId": c["name"],
                "KeyCode": keycode,
                "FunctionId-": fid,
                "FunctionGroupId": 1,
                "Id-": 900000 + i,
                "ProtocolId": None,
                "DateTaught": None,
                "IsLearned": True,
                "Parameters": None,
                "Raw": None,
                "TransportType": 1,
            }
        )
        funciones.append(
            {
                "CommandName": c["name"],
                "DeviceId-": dev_id,
                "FunctionId-": fid,
                "Label": rotulo,
                "Name": c["name"],
                "TransportType": 0,
                "__type": "FunctionAction",
            }
        )

    device = {
        "Device": {
            "Manufacturer": fabricante,
            "Model": modelo,
            "Name": device_name,
            "Id-": dev_id,
            "DeviceType": 1,
            "DeviceTypeDisplayName": "Manual",
        },
        "Commands": cmds,
        "DeviceFeatures": [],
    }
    return {
        "ProtocolList": {"Protocols": protocolos},
        "DeviceList": {"DevicesWithFeatures": [device]},
        "FunctionList": {
            "FunctionMaps": [
                {
                    "DeviceId-": dev_id,
                    "UIModeName": "Manual",
                    "FunctionGroups": [{"Name": "Manual", "Functions": funciones}],
                    "__type": "FunctionMap",
                }
            ]
        },
    }


def _extra_vocabulary() -> list[str]:
    """The hundreds of plain-text words (`FunctionList`/command names) that a
    real Logitech config carries. Not an invented filler, and not optional:
    measured, a single `.ir`'s own vocabulary is not enough for
    `glyphs.extender()` to learn even the fixed `Devices` label that
    `add_device.py` ALWAYS needs.

    IT COMES FROM `biblioteca.vocabulary()`, i.e. from the permanent library,
    NOT from the folders on disk. This used to glob
    `account_export/output/hub-config-*/` -- the devices already downloaded --
    and returned an EMPTY list the moment the user deleted them, which broke
    importing a `.ir` for exactly the same reason the catalog download broke:
    a library that was a side effect of having devices. Measured with
    `account_export/output/` as it is today (zero devices): the glob gives 0
    words and `biblioteca.vocabulary()` gives 316.

    The old glob stays as the fallback for the case where `protocol_library` itself
    cannot be imported -- degrading to the previous behaviour is fine;
    pretending there is a vocabulary when there is none is not.
    """
    try:
        import library  # noqa: PLC0415 -- soft: the app runs without it

        vocab = set(library.vocabulary())
        if vocab:
            return sorted(vocab)
    except Exception:  # noqa: BLE001
        pass
    vocab = set()
    if OUTPUT_BRIDGE.exists():
        for jsn in sorted(
            OUTPUT_BRIDGE.glob("hub-config-*/hub-config-with-device.json")
        ):
            if jsn.parent.name.startswith("hub-config-manual-"):
                continue  # do not chain a manual import on top of another
            try:
                vocab |= glyphs.vocabulario(str(jsn))
            except Exception:  # noqa: BLE001
                continue
    return sorted(vocab)


def _snapshot(resources: dict, extra_vocab: list[str], source: str) -> dict:
    """The full JSON that gets written to disk AND the one used for the
    pre-check -- the SAME words, so validation never says "yes" while the
    real `add_device.py` subprocess says "no".

    `vocabulario_heredado_de_catalogo` is not a device datum (neither
    `commands.load_hub_config()` nor `add_device.py` reads it): it IS AND ONLY IS
    fuel for `glyphs.vocabulario()`, which walks the whole JSON looking for
    `Label` keys. The words are real (from configs already downloaded from
    the catalog)."""
    return {
        "schema_version": "0.3.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source,
        "resources": resources,
        "vocabulario_heredado_de_catalogo": [{"Label": w} for w in extra_vocab],
    }


_CACHE_TABLA: dict[tuple, dict] = {}


def _glyph_table(blob: bytes, snapshot: dict) -> dict:
    """The glyph table `add_device.py` is going to have available for THIS
    file: `glyphs.extender()` against `blob` + ALL of the snapshot's
    vocabulary. `glyphs.vocabulario()` only knows how to read from a file,
    so it gets dumped to a temp file (always deleted)."""
    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    try:
        json.dump(snapshot, tmp)
        tmp.close()
        vocab = glyphs.vocabulario(tmp.name)
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    clave = (len(blob), hash(frozenset(vocab)))
    if clave in _CACHE_TABLA:
        return _CACHE_TABLA[clave]
    table, _aprendidos = glyphs.extender(blob, vocab)
    _CACHE_TABLA[clave] = table
    return table


def validate_name(
    device_name: str,
    supported_commands: list[dict],
    fabricante: str,
    modelo: str,
    blob: bytes,
) -> dict:
    """The checks that really decide whether `add_device.py` is going to be
    able to write this device: the name's TWO (draw + write), the fixed
    `Devices` label's, and every button label's THREE. Meant to run LIVE
    while the user types, and again, identically, inside `import_device()` before
    disk gets touched."""
    device_name = (device_name or "").strip()
    if not device_name:
        return {"ok": False, "name": "", "error": "a name is needed"}

    resources = build_resources(
        supported_commands, fabricante or "?", modelo or "?", device_name
    )
    snap = _snapshot(
        resources, _extra_vocabulary(), "manual-ir-upload-previsualizacion"
    )
    return _validate_with_snapshot(device_name, supported_commands, snap, blob)


def _validate_with_snapshot(
    device_name: str, supported_commands: list[dict], snap: dict, blob: bytes
) -> dict:
    fu = fonts.choose_detail(device_name, blob)
    table = _glyph_table(blob, snap)
    missing_glyphs = _missing_letters(device_name, table)
    missing_back_letters = _missing_letters(ROTULO_VOLVER, table)
    font_ok = fu.get("atributo") is not None

    rotulos = []
    rotulos_ok = True
    for c in supported_commands:
        rev = check_label(c.get("rotulo") or label_for(c["name"]), blob, table)
        rotulos.append(
            {
                "name": c["name"],
                "rotulo": rev["final"],
                "ok": rev["ok"],
                "reason": rev["reason"],
                "width": rev["width"],
            }
        )
        rotulos_ok = rotulos_ok and rev["ok"]

    return {
        "ok": font_ok
        and not missing_glyphs
        and not missing_back_letters
        and rotulos_ok
        and bool(supported_commands),
        "name": device_name,
        "font_ok": font_ok,
        "font_missing": sorted(fu.get("missing") or []),
        "font_warning": fu.get("warning"),
        "glyphs_ok": not missing_glyphs,
        "glyphs_missing": missing_glyphs,
        "rotulo_volver_ok": not missing_back_letters,
        "back_label_missing": missing_back_letters,
        "rotulos_ok": rotulos_ok,
        "rotulos": rotulos,
    }


def import_device(
    path: Path,
    fabricante: str,
    modelo: str,
    device_name: str,
    *,
    blob: bytes,
) -> dict:
    """Parses, runs ALL the checks (name + fixed label + every button
    label), and, if they pass, writes `account_export/output/hub-config-manual-.../`
    with the SAME shape as a real export. Never writes if anything cannot be
    drawn/written/fit: EVERYTHING is validated before disk gets touched."""
    fabricante = (fabricante or "").strip()
    modelo = (modelo or "").strip()
    device_name = (device_name or "").strip()
    if not fabricante or not modelo:
        return {"ok": False, "error": "manufacturer and model are needed"}
    if not device_name:
        return {
            "ok": False,
            "error": "the name that will show up in the remote's menu is needed",
        }

    r = _parse_commands(path)
    if not r["ok"]:
        return r
    commands = r["commands"]
    _mark_impossible_labels(commands, blob)
    soportados = [c for c in commands if c["soportado"]]
    publicos = [{k: v for k, v in c.items() if not k.startswith("_")} for c in commands]
    if not soportados:
        return {
            "ok": False,
            "error": "no command in the file could be imported (see the "
            "detail for each one)",
            "commands": publicos,
        }

    resources = build_resources(soportados, fabricante, modelo, device_name)
    snap = _snapshot(resources, _extra_vocabulary(), "manual-ir-upload")
    v = _validate_with_snapshot(device_name, soportados, snap, blob)
    if not v["ok"]:
        motivos = []
        if not v["font_ok"]:
            motivos.append(
                "no font can DRAW: %s"
                % (", ".join(v["font_missing"]) or v.get("font_warning") or "?")
            )
        if not v["glyphs_ok"]:
            motivos.append(
                "the remote has no glyph for these characters (its fonts "
                "carry 71, and Q, X and Z are not among them): %s"
                % ", ".join(v["glyphs_missing"])
            )
        if not v.get("rotulo_volver_ok", True):
            motivos.append(
                "the fixed label 'Devices', which every new device needs, "
                "cannot be written either (characters %s). That is a "
                "reader bug, not something to fix by downloading: those "
                "letters are all in the remote's own fonts"
                % ", ".join(v["back_label_missing"])
            )
        if not v.get("rotulos_ok", True):
            malos = [x for x in v.get("rotulos") or [] if not x["ok"]]
            motivos.append(
                "there are button labels the remote cannot draw: %s"
                % "; ".join("%s (%s)" % (x["rotulo"], x["reason"]) for x in malos)
            )
        return {
            "ok": False,
            "etapa": "label",
            "error": "the device %r cannot be used (%s)"
            % (device_name, "; ".join(motivos)),
            "validacion": v,
            "commands": publicos,
        }

    marca = time.strftime("%Y%m%d_%H%M%S")
    slug_dir = f"hub-config-manual-{_slug(fabricante)}-{_slug(modelo)}-{marca}"
    target = OUTPUT_BRIDGE / slug_dir
    target.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema_version": "0.3.0",
        "origin": "manual",
        "generated_at": snap["generated_at"],
        "requested_device": {"manufacturer": fabricante, "model": modelo},
        "validation": {
            "commands": len(soportados),
            "referenced_protocols": len(
                {p["Name"] for p in resources["ProtocolList"]["Protocols"]}
            ),
            "commands_skipped": len(commands) - len(soportados),
        },
        "ir_source": str(path),
    }

    json_path = target / "hub-config-with-device.json"
    manifest_path = target / "manifest.json"
    json_path.write_text(json.dumps(snap, ensure_ascii=False, indent=1))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))

    # THE SAME FINISH AS THE CATALOG PATH. Until now this writer left a
    # manifest WITHOUT `protocolos`, and `Api.catalog_local()` reads exactly
    # that key -- so an imported device listed as if it used no protocol at
    # all, while the identical device downloaded from the catalog listed its
    # protocol. Same device, two save paths, two different answers, and the
    # user has no way to know there are two paths. `normalize_folder()` is
    # the ONE place that decides what a saved folder has to look like;
    # calling it here means the item is already right when it is written,
    # not only when something lists it later (`biblioteca.disk_configs()`
    # also runs it, for the folders written before this line existed).
    try:
        import library  # noqa: PLC0415 -- soft: the app runs without it

        library.normalize_folder(target)
    except Exception:  # noqa: BLE001
        pass  # the device is written and usable; only the manifest is poorer

    saltados = [c for c in publicos if not c["soportado"]]
    return {
        "ok": True,
        "target": str(target),
        "json": str(json_path),
        "fabricante": fabricante,
        "modelo": modelo,
        "name": device_name,
        "commands": len(soportados),
        "commands_skipped": len(saltados),
        "detail": publicos,
        "rotulos": v.get("rotulos"),
        "label_warning": v.get("font_warning"),
        "gaps_warning": (
            "Not every command in this file carries its own timing for "
            "repeating (what happens while you hold a button down): the "
            "ones that do use it as captured; the ones that don't get a "
            "standard %d ms gap instead, which is a reasonable estimate, "
            "not something measured on your device. Try a single command "
            "on the remote before trusting the rest." % (TARGET_GAP_US // 1000)
        ),
    }
