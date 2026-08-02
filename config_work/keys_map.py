#!/usr/bin/env python3
"""The Harmony One's key map, by the path the FIRMWARE actually follows.

## Why this module exists when `keys.py` and `assign_key.py` already do

The two walk a different model and **one of the two is stale**:

* `reubicar.chain(b)` walks the LIVE section `[9]` (the one the master
  index at `0x0C + 4*9` points to).
* the firmware reaches a screen's key register through
  `table[6][ordinal] -> trailer -> slot -> keyreg ptr24`, an **absolute**
  pointer.

`reubicar.relocate()` moves `[9]` to the end and fixes the master index,
but **does not touch `table[6]`'s pointers** -- and since it doesn't erase
the old bytes, those pointers keep resolving, to the OLD copy. Measured:

    backups/config_raw.bin          live [9] 0x291e7..0x29caf
                                    206/206 of tabla[6]'s keyregs fall inside
    output/config_empaquetada.bin (WHAT'S GRABBED TODAY)
                                    live [9] 0x1481ae..0x148ecd
                                    14/226 keyregs inside; the other 212
                                    point at 0x291eb..0x29ca6 -- the old
                                    copy, byte for byte identical to the
                                    factory [9]

The 14 that DO point at the live copy are exactly the ones
`add_device` repointed when adding the Philips and the LG
(screens 74/90/141 slot 1 -- the three menu replicas -- and screen 157's
11 slots).

**Operational consequence, and this file's whole reason to exist**:
editing a page inside an already-relocated blob's live `[9]` is INERT for
212 of the 226 screens. It isn't dangerous: it's worse, it's invisible.
`assign_key.py` edits the live `[9]`; it works for the factory blob and for
new screens, not for reassigning a factory-menu key in the blob that's
grabbed today.

Here a key's identity is `(table[6] screen, slot, code)` and the byte
that gets overwritten is the `u16` INSIDE the record the slot points to,
at its absolute address -- the one the firmware reads. It's declared as
`--repoint`, which is exactly what it exists for: "changing an existing
pointer's target doesn't move any data, but it still falls under this net".

## Which keys can be mapped, and which can't

Across the 156 factory screens, section [9] uses EIGHT codes and no
others (measured: 625 entries):

    0xB0 x75  0xB1 x153  0xB2 x134  0xB3 x70  0xB4 x62  0xB5 x59
    0xAB x55  0xAC x17

These are the TOUCHSCREEN zones (`codigo = tag | 0x80` from section
[19]'s 33 templates): six cells in two columns by three rows, plus the
two footer softkeys. The other 47 codes from the 55-button inventory
(`0x67`) -- numeric keypad, volume, channel, transport, Power -- **hang
off no entry at all**: the firmware resolves them some other way, one
this pass didn't tie to data. The UI has to show them as NOT editable and
say why, instead of pretending they can be mapped.

## The labels

Each key's name comes from the screen's own drawing: the slot's program
is disassembled, the `TXT`/`TXTIN` ops are taken, and each text is
assigned to the section [19] zone it falls into, with the touch
calibration measured in the firmware (`x_px = (raw-765)*176/3283`,
`y_px = 220-(raw-656)*220/3896`; Y is INVERTED).

CHECK: that gives 79 `(k1, k2) -> label` pairs in the factory blob, **79
of 79 unambiguous** (no combination appears with two different names on
two screens). NEGATIVE CHECK: the `k2` ordinal does NOT match the
`hub-config-*.json`'s order for the factory devices (1 hit against 75
misses) -- that's why factory names come from the drawn label and NEVER
from the JSON. For the devices this project added (Philips k1=3, LG
k1=4) it's the opposite: the blob was GENERATED from that JSON, so there
`k2` is the index in `resources.DeviceList[...].Commands` by
construction -- and the check is that the counts close (32 and 63, the
same section [5] declares).

Writes nothing to the device. Does not import `write.py` to write: only
for `nothing_moved`, which is a pure function.

NOTE ON NAMING: `read()`, `apply()`, `checks()`, and `SCREEN_CODES`
keep their exact Spanish names and dict-key shapes -- `app/api.py`
imports this module DIRECTLY (not by subprocess: `teclas_mapa =
_blando("teclas_mapa")`) and calls `teclas_mapa.leer(...)`,
`keys_map.SCREEN_CODES`, and `getattr(teclas_mapa,
"aplicar")(...)` as live Python, then forwards `leer()`'s return dict
straight through to the UI as `modelo=...`. `apply()`'s `changes` input
(`{'screen','slot','codigo','k1','k2'}` per change) and its `detail`
output, and `checks()`'s `{'name','ok','detail'}` per-check dicts,
are read by key on the app side too (`c2["name"]`, `c["ok"]`). Every
dict key anywhere in this file's data model was therefore left in
Spanish, out of caution, even the ones not individually confirmed as
read externally. `_dev_id_de`, `_rehacer_checksum`, and
`_device_names` also keep their names: `keys_physical.py` and
`keys_photo.py` (`import keys_map as TM`) call them directly.
Everything else -- comments, docstrings, local variables, printed and
raised text -- was translated freely.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

import command_records
import configcheck
import add_device as D
import glyphs
import write
import list_devices
import relocate

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: the eight codes section [9] actually uses (measured, not assumed)
SCREEN_CODES = (0xAB, 0xAC, 0xB0, 0xB1, 0xB2, 0xB3, 0xB4, 0xB5)

#: k1 0..2 are the three THIS project's remote shipped with (TV / DVR /
#: Home); from 3 up, this project added them from a `hub-config-*.json`. Was
#: a private copy of the same literal `listar.K1_DE_FABRICA` defines
#: (drift risk: the two constants could silently diverge) -- now imports it
#: instead. NOT a format constant: see `listar.K1_DE_FABRICA`'s docstring
#: for what it would take to make this per-connected-remote instead of a
#: baked-in `3`.
K1_DE_FABRICA = list_devices.K1_DE_FABRICA

#: Hub DeviceList: the vocabulary that makes the glyph table readable. NOT a
#: path baked in here -- it is per-user data that this repo does not ship.
#: `glyphs.devicelist_path()` resolves it (env var, then `<repo>/hub/`, then
#: an account-bridge export) and every reader of this constant already
#: guards with `.exists()`, so a clone with no export degrades to the base
#: glyph table with a message instead of crashing.
HUB_VOCAB = glyphs.devicelist_path()


def x_px(crudo: int) -> float:
    """Touch panel calibration measured in the firmware."""
    return (crudo - 765) * 176.0 / 3283.0


def y_px(crudo: int) -> float:
    """Same, with Y INVERTED (this panel's classic trap)."""
    return 220.0 - (crudo - 656) * 220.0 / 3896.0


def set_t6(b: bytes) -> int:
    """`table[6]` through the master index, not the factory constant: in
    an already-relocated blob the constant points anywhere."""
    t6 = D.u24(b, D.MAESTRO_T6) - D.BASE
    if not 0 <= t6 < len(b) - 3:
        raise ValueError(
            "the master index %#04x doesn't give a valid tabla[6]" % D.MAESTRO_T6
        )
    D.T6 = t6
    return t6


def _decoder(b: bytes, hub):
    """`hub` can be one path or several: the more vocabulary, the more
    glyphs get resolved and the more labels come out complete."""
    rutas = [hub] if isinstance(hub, (str, pathlib.Path)) else list(hub or [])
    vocab: set[str] = set()
    for p in [HUB_VOCAB] + rutas:
        if p and pathlib.Path(p).exists():
            try:
                vocab |= glyphs.vocabulario(str(p))
            except Exception:  # noqa: BLE001 -- one missing vocabulary isn't fatal
                pass
    table, _ = glyphs.extender(b, vocab)

    def dec(ptr: int) -> str | None:
        """The text, or None if it doesn't decode COMPLETELY. Never
        returns a string with '?': a half-decoded label in a mapping UI is
        worse than none."""
        try:
            fin = b.index(b"\x00", ptr)
        except ValueError:
            return None
        s = "".join(table.get(c, "?") for c in b[ptr:fin])
        return s if s and "?" not in s and len(s) <= 20 else None

    def dec_inline(cod: bytes) -> str | None:
        s = "".join(table.get(c, "?") for c in cod)
        return s if s and "?" not in s and len(s) <= 20 else None

    return dec, dec_inline


def _resolve_object(b, dest, ident):
    """`object A` -> `(cmd_id, dev_id, target_page)`.

    Always returns ALL THREE. Collapsing command and transition into a
    single `type` loses data: in the factory blob 261 of section [9]'s
    625 entries carry `{objB,0x7F}` **and** `{page,0x7E}` on the same
    object.
    """
    cmd = dev = pag = None
    for v, t in relocate._slots(b, dest, ident):
        if t == 0x7E:
            pag = v
        elif t == 0x7D:
            cmd = v
        elif t == 0x7C:
            dev = v
        elif t == 0x7F:
            for v2, t2 in relocate._slots(b, dest, v):
                if t2 == 0x7D:
                    cmd = v2
                elif t2 == 0x7C:
                    dev = v2
    return cmd, dev, pag


def _section5_names(b: bytes) -> list[dict]:
    """`[{k1, commands}]` -- how many commands section [5] declares per
    device. This is the HARD range: a `k2` outside it hangs the remote."""
    devs = D.read_section5(b)
    out = []
    for i, d in enumerate(devs):
        n = d.get("n") or d.get("cuantos") or d.get("N")
        if n is None:
            for k in ("commands", "entradas", "count"):
                if k in d:
                    n = d[k]
                    break
        out.append({"k1": i, "commands": int(n) if n is not None else 0})
    return out


def _json_catalog(hub) -> dict[str, list[list[str]]]:
    """`{device name: [list of command names, ...]}`.

    Accepts one path or SEVERAL: `account_export/output/` holds captures from
    different moments and no single one brings both the Philips and the LG
    at once. All of them are merged and the ambiguity is resolved later,
    with section [5]'s count -- not by picking "the newest one", which
    proves nothing.
    """
    rutas = [hub] if isinstance(hub, (str, pathlib.Path)) else list(hub or [])
    out: dict[str, list[list[str]]] = {}
    for r in rutas:
        if not r or not pathlib.Path(r).exists():
            continue
        try:
            _protos, devs = command_records.load_hub_config(str(r))
        except Exception:  # noqa: BLE001
            continue
        for d in devs:
            nom = command_records.device_name(d)
            lst = [c.get("Name") for c in (d.get("Commands") or [])]
            out.setdefault(nom, [])
            if lst not in out[nom]:
                out[nom].append(lst)
    return out


def _list_from_json(
    cat: dict[str, list[list[str]]], name: str | None, cuantos: int
) -> list[str]:
    """The list of command names for an added device.

    The remote's menu shows the TRIMMED name ("Philips", "LG"); the JSON
    carries the full one ("Philips TV", "LG TV"). It's matched by
    prefix and then the deciding CHECK applies: the list has to have
    exactly as many commands as the section [5] sub-table declares for
    that `k1`. If it doesn't match, the order can't be the same by
    construction and nothing gets named -- a shifted name is worse than
    none, because it sends the wrong command to be grabbed.

    If several candidate lists of the same length are left, they have to
    be IDENTICAL; if not, empty is returned.
    """
    if not name:
        return []
    cands = []
    for k, listas in cat.items():
        if k == name or k.lower().startswith(name.lower()):
            cands += [x for x in listas if len(x) == cuantos]
    if not cands:
        return []
    if all(x == cands[0] for x in cands):
        return cands[0]
    return []


def read(blob: bytes, hub=None) -> dict:
    """The full model, ready to serialize to JSON and paint.

    Walks `table[6]` (the firmware's path), not the live section [9].
    """
    b = blob
    set_t6(b)
    sec = relocate.sections(b)
    dest = relocate.table(b, sec[relocate.OBJECT_TABLE][0])
    a9, z9 = sec[9]
    s19 = D.read_section19(b)
    zones_px = {
        k: [
            {
                "codigo": t | 0x80,
                "x": round(x_px(x0), 1),
                "x2": round(x_px(x0 + w), 1),
                "y": round(y_px(y0 + h), 1),
                "y2": round(y_px(y0), 1),
            }
            for (t, x0, w, y0, h) in z
        ]
        for k, z in s19.items()
    }
    dec, dec_inline = _decoder(b, hub)
    n_pant = D.u16(b, D.T6)

    screens = []
    rotulo_de = collections.defaultdict(collections.Counter)
    for ordinal in range(n_pant):
        t = D.u24(b, D.T6 + 3 + 3 * ordinal) - D.BASE
        tr = D.read_trailer(b, t, max_n=200)
        if tr is None:
            screens.append({"ordinal": ordinal, "error": "unreadable trailer"})
            continue
        slots = []
        for si, sp in enumerate(tr["slots"]):
            s = D.read_slot(b, sp - D.BASE)
            if s is None:
                continue
            keyreg = s["keyreg"] - D.BASE
            reg = D.read_key_register(b, keyreg) or []
            textos = []
            for _o, op, args in D.disassemble(b, s["prog"] - D.BASE):
                if op == "TXT":
                    txt = dec(args[2] - D.BASE)
                    if txt:
                        textos.append((args[0], args[1], txt))
                elif op == "TXTIN":
                    txt = dec_inline(args[2])
                    if txt:
                        textos.append((args[0], args[1], txt))
            zones = zones_px.get(s["K"], [])
            keys = []
            for idx, (cod, ident, category) in enumerate(reg):
                cmd, dev, pag = _resolve_object(b, dest, ident)
                z = next((q for q in zones if q["codigo"] == cod), None)
                label = None
                if z is not None:
                    for tx, ty, txt in textos:
                        if (
                            z["x"] - 6 <= tx <= z["x2"] + 2
                            and z["y"] - 4 <= ty <= z["y2"] + 4
                        ):
                            label = txt
                            break
                if cmd is not None and label:
                    rotulo_de[(cmd >> 8, cmd & 0xFF)][label] += 1
                keys.append(
                    {
                        "codigo": cod,
                        "category": category,
                        "objeto": ident,
                        # ABSOLUTE offset of the u16 that has to be
                        # overwritten to reassign: the one the firmware reads
                        "campo": keyreg + 2 + 4 * idx,
                        "cmd_id": cmd,
                        "dev_id": dev,
                        "k1": None if cmd is None else cmd >> 8,
                        "k2": None if cmd is None else cmd & 0xFF,
                        "target_page": pag,
                        "label": label,
                        "zone": z,
                        "editable": cod in SCREEN_CODES and cmd is not None,
                    }
                )
            slots.append(
                {
                    "slot": si,
                    "K": s["K"],
                    "keyreg": keyreg,
                    "keyreg_en_seccion9_viva": a9 <= keyreg < z9,
                    "keys": keys,
                }
            )
        k1s = collections.Counter(
            t["k1"] for s in slots for t in s["keys"] if t["k1"] is not None
        )
        screens.append(
            {
                "ordinal": ordinal,
                "slots": slots,
                "k1": k1s.most_common(1)[0][0] if k1s else None,
                "with_command": sum(
                    1 for s in slots for t in s["keys"] if t["cmd_id"] is not None
                ),
            }
        )

    # command names: drawn label (factory) or the JSON (added devices)
    cat = _json_catalog(hub)
    s5 = _section5_names(b)
    nombres_dev = _device_names(b, hub, len(s5))
    devices = []
    for d in s5:
        k1 = d["k1"]
        nom = nombres_dev.get(k1)
        # The JSON is ONLY valid for the devices this project added.
        # Measured NEGATIVE CHECK: for the three factory ones,
        # `JSON[k2]` matches the drawn label 1 time against 75 -- i.e.
        # the catalog's order is NOT the section [5] sub-table's order.
        # For the added ones it's the opposite, and by construction:
        # `add_device.py` emitted the sub-table by walking that same
        # `Commands` list in order.
        de_fabrica = k1 < K1_DE_FABRICA
        lista_json = [] if de_fabrica else _list_from_json(cat, nom, d["commands"])
        cmds = []
        for k2 in range(d["commands"]):
            label_counts = rotulo_de.get((k1, k2))
            if label_counts:
                label, origin = label_counts.most_common(1)[0][0], "on-screen label"
            elif k2 < len(lista_json) and lista_json[k2]:
                label, origin = lista_json[k2], "device catalog"
            else:
                label, origin = None, "no name known"
            cmds.append({"k2": k2, "name": label, "origin": origin})
        devices.append(
            {
                "k1": k1,
                "name": nom or "device %d" % k1,
                "commands": cmds,
                "cuantos": d["commands"],
            }
        )

    keyregs = [s["keyreg"] for p in screens for s in p.get("slots", [])]
    return {
        "screens": screens,
        "devices": devices,
        "seccion9_viva": [a9, z9],
        "keyregs_en_seccion9_viva": sum(1 for k in keyregs if a9 <= k < z9),
        "keyregs_totales": len(keyregs),
        "screen_codes": list(SCREEN_CODES),
        "rotulos_univocos": sum(1 for c in rotulo_de.values() if len(c) == 1),
        "rotulos_totales": len(rotulo_de),
    }


def _device_names(b: bytes, hub, cuantos: int) -> dict[int, str]:
    """`{k1: name}` -- reuses `list_devices.py`, which already resolves the menu
    and decodes names with the Hub's vocabulary. If it can't, it falls
    back to `None` and the UI shows the number: it never invents a name."""
    try:
        import list_devices

        list_devices.set_t6(b)
        dest11 = relocate.table(b, relocate.sections(b)[11][0])
        decode, _warning = list_devices.make_decoder(b, str(HUB_VOCAB))
        zones19 = D.read_section19(b)
        out: dict[int, str] = {}
        for row in list_devices.menu_rows(b, 74, decode, dest11, zones19) or []:
            if row.get("k1") is not None and row.get("name"):
                out[row["k1"]] = row["name"]
        if out:
            return out
    except Exception:  # noqa: BLE001 -- without a name the UI shows the number
        pass
    return {}


# ==========================================================================
# WRITE -- the usual path: it gets built, and the gate decides
# ==========================================================================
def apply(b: bytes, changes: list[dict]) -> tuple[bytes, list[int], list[dict]]:
    """Reassigns N keys at once, through the firmware's path.

    `changes`: `[{screen, slot, codigo, k1, k2}, ...]`.

    Returns `(new blob, repoints, detail)`. `repoints` are the offsets
    that have to be declared to `write.py` with `--repoint` -- one per
    key, the repointed `u16`. NO check is turned off: exactly which bytes
    get touched is enumerated, which is precisely what `--repoint` exists
    to require.

    Raises `ValueError` (without building anything) if a change doesn't add up.
    """
    if not changes:
        raise ValueError("there is no change to apply")
    set_t6(b)
    sec = relocate.sections(b)
    a9, z9 = sec[9]
    a10, z10 = sec[10]
    dest = relocate.table(b, sec[relocate.OBJECT_TABLE][0])

    s10 = bytearray(b[a10:z10])
    extra: list[int] = []
    parches: list[tuple[int, int]] = []  # (absolute offset, new id_a)
    detail: list[dict] = []
    vistos: set[tuple[int, int, int]] = set()

    for c in changes:
        screen = int(c["screen"])
        slot = int(c["slot"])
        codigo = int(c["codigo"])
        k1 = int(c["k1"])
        k2 = int(c["k2"])
        clave = (screen, slot, codigo)
        if clave in vistos:
            raise ValueError(
                "key (screen %d, slot %d, %#04x) appears twice in the same "
                "batch" % clave
            )
        vistos.add(clave)

        if codigo not in SCREEN_CODES:
            raise ValueError(
                "code %#04x is not a screen zone (%s): that key hangs off "
                "no entry and can't be mapped this way"
                % (codigo, ", ".join("%#04x" % x for x in SCREEN_CODES))
            )

        campo, id_a_old = _field_of(b, screen, slot, codigo)

        cmd_id = (k1 << 8) | k2
        reg, reason = D.resolve_section5(b, cmd_id)
        if reg is None:
            raise ValueError(
                "(device %d, command %d) -> %#06x is NOT reachable through "
                "section [5]: %s -- grabbing this would hang the remote"
                % (k1, k2, cmd_id, reason)
            )
        dev_id = c.get("dev_id")
        dev_id = int(dev_id) if dev_id is not None else _dev_id_de(b, dest, k1)

        id_base = len(dest) + len(extra)
        extra.append(len(s10))
        s10 += command_records.command_object(cmd_id, dev_id)
        id_b = id_base
        extra.append(len(s10))
        s10 += _clone_to(b, dest, id_a_old, id_b)
        id_a = id_base + 1

        parches.append((campo, id_a))
        detail.append(
            {
                "screen": screen,
                "slot": slot,
                "codigo": codigo,
                "campo": campo,
                "old_object": id_a_old,
                "new_object": id_a,
                "cmd_id": cmd_id,
                "dev_id": dev_id,
                "k1": k1,
                "k2": k2,
            }
        )

    out = bytearray(relocate.relocate(b, {10: bytes(s10)}, objetos_extra=extra))
    new9 = relocate.sections(bytes(out))[9][0]
    repuntes: list[int] = []
    for campo, id_a in parches:
        out[campo : campo + 2] = id_a.to_bytes(2, "little")
        repuntes.append(campo)
        # if that field fell inside the old [9], the relocated copy has to
        # say the same thing: otherwise `cadena()` and the firmware would disagree
        if a9 <= campo < z9:
            espejo = new9 + (campo - a9)
            out[espejo : espejo + 2] = id_a.to_bytes(2, "little")
    _rehacer_checksum(out)
    return bytes(out), sorted(repuntes), detail


def _rehacer_checksum(out: bytearray) -> None:
    """The closer's XOR-16, recalculated AFTER overwriting the fields.

    `reubicar()` computes it at the end of its own build; since bytes get
    overwritten here afterward, without this `configcheck` FAILS on
    "XOR-16 checksum". That it did fail was the check that this check
    isn't decorative: this module's first real run aborted exactly there.
    """
    close = int.from_bytes(out[4:7], "little") - relocate.BASE
    lo, hi = 0x21, 0x43
    for k in range(0, close - 2, 2):
        lo ^= out[k]
        hi ^= out[k + 1]
    out[close - 2] = lo
    out[close - 1] = hi


def _field_of(b: bytes, screen: int, slot: int, codigo: int) -> tuple[int, int]:
    """`(absolute offset of the u16, id of today's object A)`."""
    n = D.u16(b, D.T6)
    if not 0 <= screen < n:
        raise ValueError("screen %d doesn't exist (there are %d)" % (screen, n))
    tr = D.read_trailer(b, D.u24(b, D.T6 + 3 + 3 * screen) - D.BASE, max_n=200)
    if tr is None:
        raise ValueError("screen %d's trailer doesn't parse" % screen)
    if not 0 <= slot < tr["N"]:
        raise ValueError(
            "screen %d has no slot %d (it has %d)" % (screen, slot, tr["N"])
        )
    s = D.read_slot(b, tr["slots"][slot] - D.BASE)
    if s is None:
        raise ValueError("slot %d of screen %d doesn't parse" % (slot, screen))
    keyreg = s["keyreg"] - D.BASE
    reg = D.read_key_register(b, keyreg) or []
    for idx, (cod, ident, category) in enumerate(reg):
        if cod == codigo:
            # THE CLASS DECIDES WHAT THE `id` MEANS. Only 0x7F makes the row
            # jump to an object; on the LG page's slot 0, for instance, 0xAB
            # is `{2085, 0x72}` -- the factory's "Activities" foot action,
            # where the id is not an object to repoint. Repointing it left a
            # row that reads a command object as a 0x72 action: it passed
            # every check of the day and did nothing. Caught by
            # `teclas_alcance.checks` (h); refused here, at the source.
            if category != 0x7F:
                raise ValueError(
                    "screen %d slot %d key %#04x: class %#04x is not 0x7F, so "
                    "its id is not a jump to a command object -- reassigning "
                    "it would write a row the firmware does not read as a "
                    "command" % (screen, slot, codigo, category)
                )
            return keyreg + 2 + 4 * idx, ident
    raise ValueError(
        "screen %d slot %d doesn't declare key %#04x (it declares %s)"
        % (screen, slot, codigo, ", ".join("%#04x" % e[0] for e in reg))
    )


def _clone_to(b: bytes, dest: list[int], id_a_old: int, id_b: int) -> bytes:
    """The old A object COPIED, with its single `0x7F` slot repointed.

    Re-emitting the canonical shape `02|{0x0FCA,0x75}|{idB,0x7F}` instead
    of cloning would erase the `{page, 0x7E}` of keys carrying a command
    **and** a transition -- 261 of the factory's 625 entries have it, and
    losing it leaves whole pages with no path in.
    """
    rs = relocate._slots(b, dest, id_a_old)
    if not rs:
        raise ValueError("object %d could not be read" % id_a_old)
    cuantas = sum(1 for _v, t in rs if t == 0x7F)
    if cuantas != 1:
        raise ValueError(
            "object %d has %d 0x7F slots: no way to know which one to repoint"
            % (id_a_old, cuantas)
        )
    nuevas = [(id_b if t == 0x7F else v, t) for v, t in rs]
    return bytes([len(nuevas)]) + b"".join(relocate.slot(v, t) for v, t in nuevas)


def _dev_id_de(b: bytes, dest: list[int], k1: int) -> int:
    """The `dev_id` that device's commands already use, DERIVED from the
    config (not assumed). If there's no precedent it falls back to the
    convention measured on the three factory ones, `(k1<<8)|0x01` -- and
    that gets flagged [ASSUMED]."""
    vistos = set()
    for i in range(len(dest)):
        rs = relocate._slots(b, dest, i)
        cmd = dev = None
        for v, t in rs:
            if t == 0x7D:
                cmd = v
            elif t == 0x7C:
                dev = v
        if cmd is not None and dev is not None and (cmd >> 8) == k1:
            vistos.add(dev)
    if len(vistos) == 1:
        return next(iter(vistos))
    if len(vistos) > 1:
        raise ValueError("ambiguous dev_id for device %d: %s" % (k1, vistos))
    return (k1 << 8) | 0x01


def checks(b: bytes, out: bytes, detail: list[dict], repuntes) -> list[dict]:
    """The mandatory checks. None of them write anything.

    (a) the requested keys resolve to the new command THROUGH THE
        FIRMWARE'S PATH, and no other key on any screen changed;
    (b) each new `cmd_id` is reachable through section [5];
    (c) `grabar.nothing_moved` positive (with the declared repoints) and
        negative (a copy corrupted on purpose, and also WITHOUT declaring
        the repoints: if it passed without declaring them, declaring them
        would prove nothing);
    (d) `configcheck.revisar` all green;
    (e) navigation stays the same (reachable and referenced pages).
    """
    ch: list[dict] = []
    esperado = {(d["screen"], d["slot"], d["codigo"]): d["cmd_id"] for d in detail}

    before = _flat_keys(b)
    after = _flat_keys(out)
    llegaron = [k for k, v in esperado.items() if after.get(k, (None,))[0] == v]
    otras = [
        k
        for k in before
        if k not in esperado and before[k] != after.get(k) and k in after
    ]
    ch.append(
        {
            "name": "(a) keys resolve through tabla[6]",
            "ok": len(llegaron) == len(esperado)
            and not otras
            and len(before) == len(after),
            "detail": "%d/%d requested keys resolve to the new command; %d "
            "keys changed unrequested; %d keys before and %d after"
            % (len(llegaron), len(esperado), len(otras), len(before), len(after)),
        }
    )

    malos = [d for d in detail if D.resolve_section5(out, d["cmd_id"])[0] is None]
    ch.append(
        {
            "name": "(b) reachable through section [5]",
            "ok": not malos,
            "detail": "the %d new commands resolve through the firmware's "
            "exact arithmetic" % len(detail)
            if not malos
            else "do NOT resolve: %s" % [hex(d["cmd_id"]) for d in malos],
        }
    )

    rep = set()
    for r in repuntes:
        rep |= {r, r + 1, r + 2}
    ok_pos, dif_pos = write.nothing_moved(b, out, rep)
    ok_sin, _dif_sin = write.nothing_moved(b, out)
    corrompido = bytearray(out)
    corrompido[0x100] ^= 0xFF
    ok_neg, dif_neg = write.nothing_moved(b, bytes(corrompido), rep)
    ch.append(
        {
            "name": "(c) nothing moved (+/-)",
            "ok": ok_pos and not ok_neg and not ok_sin and 0x100 in dif_neg,
            "detail": "positive with the %d declared repoints: %s (%d "
            "different bytes); WITHOUT declaring them: %s (has to say NO, "
            "otherwise not declaring them would prove nothing); with a "
            "byte corrupted on purpose: %s"
            % (
                len(repuntes),
                "YES" if ok_pos else "NO",
                len(dif_pos),
                "YES" if ok_sin else "NO",
                "YES" if ok_neg else "NO",
            ),
        }
    )

    pruebas = configcheck.revisar(out)
    ch.append(
        {
            "name": "(d) configcheck",
            "ok": all(p[1] for p in pruebas),
            "detail": "; ".join(
                "%s:%s" % (n, "OK" if ok else "FAIL") for n, ok, _ in pruebas
            ),
        }
    )

    alc_a, alc_d = relocate.reachable_pages(b), relocate.reachable_pages(out)
    ref_a, ref_d = (
        relocate.page_references(b),
        relocate.page_references(out),
    )
    ch.append(
        {
            "name": "(e) navigation intact",
            "ok": alc_a == alc_d and ref_a == ref_d,
            "detail": "reachable %d->%d (lost: %s); referenced %d->%d "
            "(lost: %s)"
            % (
                len(alc_a),
                len(alc_d),
                sorted(alc_a - alc_d) or "none",
                len(ref_a),
                len(ref_d),
                sorted(ref_a - ref_d) or "none",
            ),
        }
    )
    return ch


def _flat_keys(b: bytes) -> dict:
    """`{(screen, slot, code): (cmd_id, target_page)}` through tabla[6].

    Returns BOTH things: a comparison that only looked at the command
    would be blind to exactly the damage this module exists to avoid.
    """
    set_t6(b)
    sec = relocate.sections(b)
    dest = relocate.table(b, sec[relocate.OBJECT_TABLE][0])
    out = {}
    for ordinal in range(D.u16(b, D.T6)):
        tr = D.read_trailer(b, D.u24(b, D.T6 + 3 + 3 * ordinal) - D.BASE, max_n=200)
        if tr is None:
            continue
        for si, sp in enumerate(tr["slots"]):
            s = D.read_slot(b, sp - D.BASE)
            if s is None:
                continue
            for cod, ident, _cl in D.read_key_register(b, s["keyreg"] - D.BASE) or []:
                cmd, _dev, pag = _resolve_object(b, dest, ident)
                out[(ordinal, si, cod)] = (cmd, pag)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("--hub", default=None, help="hub-config-*.json for the names")
    ap.add_argument("--out", help="dump the model to JSON")
    ap.add_argument(
        "--asignar",
        action="append",
        default=[],
        metavar="PANT,SLOT,COD,K1,K2",
        help="one change; repeatable. Only writes if ALL checks come back OK",
    )
    ap.add_argument("--salida", help="new blob (only if the checks pass)")
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    if b[:4] != b"GSPM":
        print("the blob doesn't start with GSPM", file=sys.stderr)
        return 1

    m = read(b, a.hub)
    print(
        "screens: %d | keyregs: %d (%d inside the live [9] %#x..%#x, %d "
        "pointing at the old copy)"
        % (
            len(m["screens"]),
            m["keyregs_totales"],
            m["keyregs_en_seccion9_viva"],
            m["seccion9_viva"][0],
            m["seccion9_viva"][1],
            m["keyregs_totales"] - m["keyregs_en_seccion9_viva"],
        )
    )
    print(
        "labels (k1,k2)->name: %d, unambiguous %d"
        % (m["rotulos_totales"], m["rotulos_univocos"])
    )
    for d in m["devices"]:
        con = sum(1 for c in d["commands"] if c["name"])
        print(
            "  k1=%d  %-22s %3d commands (%d named)"
            % (d["k1"], d["name"], d["cuantos"], con)
        )
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(m, indent=1))
        print("model -> %s" % a.out)

    # Stale keyregs are not a curiosity, they are the blob being HALF DEAD:
    # every one of them makes the firmware read a screen's key bindings from
    # the copy a relocation left behind, so editing the live [9] there is
    # silently inert. Reporting that and exiting 0 is how it stayed invisible;
    # it now sets the exit code. `reubicar.relocate(..., reparar_referencias=
    # True)` is what closes it.
    colgados = m["keyregs_totales"] - m["keyregs_en_seccion9_viva"]
    if colgados:
        print(
            "\nWARNING: %d of %d keyregs resolve into a DEAD copy of section "
            "[9]. Editing those screens' keys in the live section has no "
            "effect. Regenerate with reubicar.relocate(..., "
            "reparar_referencias=True) -- note that changes the blob's md5, "
            "so the anchor has to be moved deliberately."
            % (colgados, m["keyregs_totales"]),
            file=sys.stderr,
        )

    if not a.asignar:
        return 2 if colgados else 0

    changes = []
    for spec in a.asignar:
        p, s, c, k1, k2 = (int(x, 0) for x in spec.split(","))
        changes.append({"screen": p, "slot": s, "codigo": c, "k1": k1, "k2": k2})
    try:
        out, repuntes, detail = apply(b, changes)
    except ValueError as e:
        print("NOT APPLIED: %s" % e, file=sys.stderr)
        return 1
    print(
        "\n%d change(s); repoints to declare: %s"
        % (len(detail), [hex(r) for r in repuntes])
    )
    ch = checks(b, out, detail, repuntes)
    width = max(len(c["name"]) for c in ch)
    for c in ch:
        print(
            "  %-*s %-6s %s"
            % (width, c["name"], "OK" if c["ok"] else "FAIL", c["detail"])
        )
    todo = all(c["ok"] for c in ch)
    print("\nblob: %d B -> %d B (+%d)" % (len(b), len(out), len(out) - len(b)))
    print("VERDICT: %s" % ("fit to grab" if todo else "ABORTED"))
    if not todo:
        return 1
    if a.salida:
        pathlib.Path(a.salida).write_bytes(out)
        print("written %s" % a.salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
