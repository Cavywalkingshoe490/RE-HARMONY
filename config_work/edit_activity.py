#!/usr/bin/env python3
"""EDIT a Harmony One Activity (TV HD=7, PC=8), without moving a byte.

## THE TASK AND THE HYPOTHESIS -- verdict

An earlier round had concluded "which activity uses which device cannot
be attributed" and that's why `delete_device.py` over-reports what's lost. That
diagnosis is REFUTED: the attribution IS in the data, through Path B
(data), and it matches Path A (name) on 8 of 9 state variables --
measured here, not assumed, see "THE GOLD CHECK" below.

The complete chain (verified instruction by instruction against
`backups/app_0x020000.bin` in an earlier RE round, with four rounds of
adversarial refutation on top of that, and RE-VERIFIED BYTE FOR BYTE in
this file against `output/config_empaquetada.bin` -- the offsets
below are measured, not quoted from memory):

    page 44 "My Activities" row -> {0xFF|ordinal, 0x1F} on a tabla[11]
    object (#2410=TV HD, #2411=PC) -> firmware switches [0x0E22]=ordinal
    and fires the ENTER/LEAVE/REFRESH reason -> section[10][ordinal]
    (KEYBOARD CONTEXT table, 10 fixed entries in master-index slot 10, NOT
    to be confused with "section [10]" = the object store `relocate.py`
    moves; they're the SAME master-index slot but one object lives INSIDE
    the other: that region's first bytes are the little 10-pointer table,
    the rest of the range is the generic store where #2066, #748, etc.
    live) -> the reserved codes 0x01/0x02/0x05 (ENTER/LEAVE/REFRESH) give
    the `idx` of a tabla[11] object -- **the ENTER**.

**ENTER does NOT emit IR.** It's a slot object (same format as ANY object
in the project, `<u8 count><count x {u16 value, u8 tag}>`) that writes a
DESIRED STATE VECTOR: atoms with tag>=0x80 are a direct SET
(`property = tag & 0x7F`, `value = value field`); atoms with tag=0x7F
forward to ANOTHER nested object that in turn carries more SETs. Each SET
fires, in section [14]'s engine (the state machine: `<u16 initial><u16
limit><u16 n_transitions><u8 pad><n x 8B>`, transition=`<u8 flags><u16
from><u16 to><atom 3B>`), the transition that matches `from`(old)
`to`(new), and THAT transition IS the IR command + the delay (the
transition's atom, tag=0x7F -> object with `{cmd_id,0x7D}{dev_id,0x7C}`,
the same "object B" primitive any device button uses).

### THE GOLD CHECK (run in this file, `--check-de-oro`)

The TWO paths have to agree. Path A = the property's name in section `[0]`
(`TV_Power_2` -> the device the MENU calls `TV`; the prefix table is read
out of the blob by `activities.device_prefixes()`, never written here);
Path B = the transitive closure of that property's TRANSITIONS in `[14]`
down to a `{cmd_id,0x7D}`, taking `k1 = cmd_id >> 8`.

Device names in this table are generic stand-ins -- on a real remote they
are whatever that user's Hub called each device.

    property                  k1 (name)     k1 (transitions)    matches
    CurrentLocation_1          --            {}                  OK
    DVR_Power_2                1             {1}                 OK
    CurrentActivityState_0_3   --            {}                  OK
    TV_Power_2                 0             {0}                 OK
    TV_Input_5                 0             {0}                 OK
    ButtonSoundVolume_2        --            {}                  OK
    Home_Input_8                2             {2}                 OK
    Home_Power_2                2             {2}                 OK
    TV_OnlinePower_2            0 (ASSUMED)  {}                  MISMATCH

**8/9 = 88.9 %, UNROUNDED.** The only mismatch (`TV_OnlinePower_2`) has
ZERO transitions declared in `[14]` (measured: `n_transiciones=0`): it's
written as a SIDE EFFECT inside `TV_Power_2`'s `1->0` transition object
(`SET TV_OnlinePower=0`, verified by reading that object), never fires
anything on its own. Path B has nothing to confirm it with: it stays
[ASSUMED] by name alone, and is declared as such -- not rounded up to 9/9.

n=9 is the ENTIRE population of named variables in section [0] (not a
sample): there's no possible sampling bias on this figure. What IS left
underdetermined by `n=2` activities (TV HD/PC) is everything that can't be
measured on the two: there's no third complete activity to compare
against (ordinal 9 "All Off" has no LEAVE and doesn't live in a menu,
it's a one-sided degenerate case).

## WHAT CAN BE EDITED TODAY, without moving a byte

Every object in this blob is referenced by its ID in `table[11]` (`<u16
count><count x ptr24>`, master-index slot 11). Nobody but `tabla[11]`
knows an object's PHYSICAL address -- everyone else names it by ID.
That's why "editing object N" never means moving anything: a new copy is
written to the blob's TAIL and ONLY `table[11][N]`'s entry gets
overwritten (repointed), 3 bytes, in place. The old object stays
alive-but-dead at its original offset -- same criterion as the rest of
the project (`relocate.py`, `delete_device.py`, `fourth_device.py`).

    quitar-set / agregar-set / cambiar-valor  -> repoints
        tabla[11][ENTER] (or of the sub-object that actually carries that
        SET, if nested one level)
    renombrar    -> repoints tabla[6][44]'s ONE slot's `prog` (3 B, in
        place), to a new program with that row's TXT pointing at the new
        text (glyphs from `fonts.encode`, validated with
        `fonts.choose`)
    borrar       -> repoints the same slot's `keyreg` (without that row)
        AND, on top of that, repoints the `[ordinal]` entry of the little
        10-pointer table at the start of the master index's section[10]
        (keyboard context) to an empty context -- the TWO parts the task
        asked for ("remove it from the tabla[6][44] menu AND its [10] context")

## WHAT IS NOT TOUCHED, and why

  * Section [14] (the state machine): its transitions are SHARED by both
    activities and by `All Off` (`#519`/`#565`/`#862`/etc. get reused).
    Editing a SET doesn't touch [14] -- it only changes WHICH transitions
    fire when ENTER is touched, not how each one resolves.
  * Ordinal 9 ("All Off"): has no row in any menu -- it's fired by the
    PHYSICAL key 0xA5 from the global keymap (`seccion[10][1]`).
    `--index 9` with `--erase`/`--renombrar` ABORTS: there's no row to
    cut or rename.
  * Adding an ADDED device (Philips=3, LG=4) to an activity's vector: NO
    PATH EXISTS. Only TV/DVR/Home (k1 0/1/2) have a
    `*_Power`/`*_Input` property with transitions in `[14]`; Philips and
    LG have no state variable declared at all. `--add-set` rejects it
    with the reason, doesn't attempt it.

## CHECKS (all run on their own after every write; abort if they fail)

  (a) `reubicar.chain()` of DEVICE buttons: identical before and after
      (this tool never touches `[9]`, so it has to come out exactly
      equal, not approximately).
  (b) the reachable transitions of ALL remaining activities resolve,
      through `cmd_setup_ir`'s arithmetic (`resolve_section5`, identical
      to `delete_device.py`'s), to a `(k1,k2)` section [5] declares -- no
      activity can be left pointing at a nonexistent device.
  (c) `grabar.nothing_moved`, WITH ITS NEGATIVE (without declaring the
      repoint(s) it has to say NO).
  (d) `configcheck.revisar()` all green.
  (e) round trip: applying the edit and its inverse gives the SAME
      logical model as the starting blob (same SETs, same name, same
      live row) -- not necessarily the same file byte for byte, because
      every step adds a new tail (same criterion as the WHOLE project).

Nothing was grabbed. `grabar.cargar()` was not touched, nor any
`erase_*`/`write_firmware_*`. `account_export/` was not modified.

Usage:
    python3 edit_activity.py blob.bin --control-de-oro
    python3 edit_activity.py blob.bin --indice 7 --listar
    python3 edit_activity.py blob.bin --indice 7 --quitar-set Home_Power \\
        --salida output/sin_home.bin
    python3 edit_activity.py blob.bin --indice 7 --agregar-set Home_Power=1 \\
        --salida output/con_home.bin
    python3 edit_activity.py blob.bin --indice 7 --cambiar-valor TV_Input=2 \\
        --salida output/tv_input2.bin
    python3 edit_activity.py blob.bin --indice 8 --renombrar "Peli" \\
        --salida output/pc_renombrado.bin
    python3 edit_activity.py blob.bin --indice 8 --borrar \\
        --salida output/sin_pc.bin
    python3 edit_activity.py blob.bin --indice 7 --cambiar-valor TV_Input=2 \\
        --ida-y-vuelta
"""

from __future__ import annotations

import argparse
import pathlib

import activities as A
import blob_records as BR
import delete_device
import configcheck
import add_device as D
import fonts
import write
import relocate

BASE = D.BASE

#: the two activity ordinals living in a tabla[6][44] row and therefore
#: renamable/removable. 9 (All Off) is physical, not a menu one.
ORDINALES_CON_FILA = (7, 8)
#: all three, for --listar / --control-de-oro (includes 9, read-only).
TODOS_LOS_ORDINALES = (7, 8, 9)

CODE_ENTER, CODE_EXIT, COD_REFRESCAR = 0x01, 0x02, 0x05
TAG_OBJETO = 0x7F
TAG_CMD = 0x7D
TAG_DEV = 0x7C


# ============================================================ primitives ===
# build/read tabla[11]'s anonymous objects and section[10]'s little table.
# Same format as `device._slots` / `reubicar.slot`, re-exposed
# here without importing private internals twice.


def _closer(b: bytes) -> int:
    """Offset where `<u16 checksum><'PTYY'>` starts (same criterion as
    `reubicar.relocate` / `configcheck.close`: the pointer at +4 points
    at PTYY, the checksum sits 2 B before it)."""
    return D.u24(b, 4) - BASE - 2


def _append_tail(b: bytes, cuerpo: bytes) -> tuple[bytes, int]:
    """Appends `cuerpo` to the blob's tail (where the old closer was) and
    returns `(new_blob, body_offset)`, with the closer and checksum
    already fixed. Repoints nothing: that's the caller's job, since it's
    the one that knows WHICH pointer has to point there."""
    c = _closer(b)
    out = bytearray(b[:c])
    new_off = len(out)
    out += cuerpo
    if len(out) % 2:
        out += b"\x00"
    new_close = len(out) + 2
    out += b"\x00\x00" + b"PTYY"
    out[4:7] = (BASE + new_close).to_bytes(3, "little")
    configcheck.arreglar(out)
    return bytes(out), new_off


def serialize_slots(entradas: list[tuple[int, int]]) -> bytes:
    """`<u8 count><count x {u16 value, u8 tag}>` -- the format of ANY
    anonymous `table[11]` object (ENTER/LEAVE/REFRESH, #748, #827, ...).
    Inverse of `device._slots`."""
    if len(entradas) > 255:
        raise SystemExit("an object cannot have more than 255 slots")
    out = bytearray([len(entradas)])
    for value, tag in entradas:
        out += relocate.slot(value & 0xFFFF, tag)
    return bytes(out)


def repoint_table11(
    b: bytes, dest11: list[int], t11_off: int, idx: int, new_body: bytes
) -> tuple[bytes, int]:
    """Appends `new_body` to the tail and repoints `table[11][idx]`
    to point there. Returns `(new_blob, repoint_offset)` -- the offset is
    declared as `extra`/`--repoint` for `grabar.nothing_moved`. This is
    the ONLY write mechanism in this file for anonymous objects: an
    object is never edited in place, its entry in the global table is
    always repointed instead."""
    fresh, off_cuerpo = _append_tail(b, new_body)
    entry_off = t11_off + 2 + 3 * idx
    out = bytearray(fresh)
    out[entry_off : entry_off + 3] = (BASE + off_cuerpo).to_bytes(3, "little")
    configcheck.arreglar(out)
    return bytes(out), entry_off


def repoint_field(b: bytes, off: int, width: int, new_value: int) -> bytes:
    """Overwrites a `width`-byte field IN PLACE (a pointer or a value) --
    without adding anything to the tail. For the cases where what changes
    is a pointer that ALREADY lives inside a fixed-width structure (a
    7 B slot's `prog`/`keyreg`, or an entry in section[10]'s little
    table): there "add and repoint" isn't needed, repointing alone is
    enough -- the field was already there, the same width, and nothing moves."""
    out = bytearray(b)
    out[off : off + width] = new_value.to_bytes(width, "little")
    configcheck.arreglar(out)
    return bytes(out)


# ================================================================ reading ===


def _dest11(b: bytes) -> tuple[list[int], int]:
    sec = relocate.sections(b)
    t11_off = sec[11][0]
    return relocate.table(b, t11_off), t11_off


def read_properties(b: bytes) -> dict[int, dict]:
    """`{id: {'name','inicial','limite','n_transiciones','off'}}`,
    section `[0]` (names, `blob_records.py`) cross-checked with section
    `[14]` (the state machine, `activities.engine_records` -- its
    'vid'/'valor' fields are MISNAMED there: structurally they're
    `initial_value`/`limit`, not an id; the real id is the record's
    POSITION in the list, measured with the naming gold check below).
    """
    regs = A.engine_records(b)
    nombres = {r.ident: r.name for r in BR.scan(b) if r.type == 1}
    out = {}
    for i, r in enumerate(regs):
        out[i] = {
            "name": nombres.get(i),
            "inicial": r["vid"],
            "limite": r["value"],
            "n_transiciones": r["cuantos"],
            "off": r["off"],
            "entradas": r["entradas"],
        }
    return out


def gold_check_names(b: bytes) -> tuple[int, int, list[tuple]]:
    """The check for section [0] against [14]: a name's numeric suffix
    (`TV_Input_5`) has to be EXACTLY `limit+1`. Returns
    `(hits, total, detail)`."""
    props = read_properties(b)
    total = ok = 0
    detail = []
    for pid, p in props.items():
        if p["name"] is None:
            continue
        total += 1
        try:
            sufijo = int(p["name"].rsplit("_", 1)[-1])
        except ValueError:
            detail.append((p["name"], pid, "no numeric suffix"))
            continue
        acierta = sufijo == p["limite"] + 1
        ok += acierta
        detail.append(
            (p["name"], pid, "OK" if acierta else "FAIL (limite=%d)" % p["limite"])
        )
    return ok, total, detail


def _chase_k1(
    b: bytes, dest11: list[int], idx: int, prof: int = 0, visto=None
) -> set[int]:
    """Transitive closure from object `idx` down to the `k1`s of every
    `{cmd_id,0x7D}` reachable through `0x7F` slots. Same arithmetic as
    `activities.engine_k1`, parameterized by seed so it can be run
    PER PROPERTY (gold check) and PER ACTIVITY (check (b))."""
    if visto is None:
        visto = set()
    if idx in visto or prof > 10 or not (0 <= idx < len(dest11)):
        return set()
    visto.add(idx)
    out: set[int] = set()
    for v, t in D._slots(b, dest11[idx]) or []:
        if t == TAG_CMD:
            out.add(v >> 8)
        elif t == TAG_OBJETO:
            out |= _chase_k1(b, dest11, v, prof + 1, visto)
    return out


def _cmds_of_activity(b: bytes, dest11: list[int], ordinal: int) -> set[int]:
    """ALL the `cmd_id`s an activity might ever emit.

    MEASURED CORRECTION: following only `0x7F` from the hook doesn't
    reach a single command -- confirmed by instrumenting checks (b) and
    (extra), which walked 557 objects and examined ZERO `0x7D` slots.
    It's structural: the IR doesn't hang off the hook, it hangs off each
    property's `[14]` record that the hook SETs, and the SETs are
    `tag >= 0x80`, i.e. the walk was cut short right before it. Here it's
    seeded from BOTH sides: the hook's direct closure PLUS each SET's
    `[14][pid]` record.
    """
    cmds: set[int] = set()
    ganchos = activity_hooks(b, ordinal)
    regs = A.engine_records(b)
    for idx in ganchos.values():
        if idx is None:
            continue
        # (1) what hangs directly off the hook
        pila, visto = [idx], set()
        while pila:
            i = pila.pop()
            if i in visto or not (0 <= i < len(dest11)):
                continue
            visto.add(i)
            for v, t in D._slots(b, dest11[i]) or []:
                if t == TAG_CMD:
                    cmds.add(v)
                elif t == TAG_OBJETO:
                    pila.append(v)
        # (2) what each SET fires, through [14]'s state machine
        for pid, _value in A.object_sets(b, dest11, idx):
            for tr in A.transitions_of(b, pid, regs):
                if tr["tag"] != TAG_OBJETO:
                    continue
                cmds.update(A._cmds_from_object(b, dest11, tr["atomo"]))
    return cmds


#: Path A: which device each property belongs to, by the name's PREFIX.
#: This is the task's WEAK PATH -- used only for the cross-check, never
#: to decide whether a write is valid (Path B, `_chase_k1`, decides that,
#: since it's the one that actually walks data).
#:
#: The prefix -> k1 table is NOT written here: it is derived from the menu
#: of the blob being read (`activities.device_prefixes`), because the
#: device names are the user's, not the format's. On a blob whose names
#: can't be decoded the table comes back empty and path A simply says
#: nothing for every row -- which the cross-check reports as such.
def _k1_by_name(name: str | None, prefijos: dict[str, int]) -> int | None:
    return A.k1_by_name(name, prefijos)


def gold_check(b: bytes, verbose: bool = True) -> tuple[int, int]:
    """Runs the full gold check (name vs. transitions) and prints it.
    Returns `(hits, total)` UNROUNDED."""
    dest11, _t11_off = _dest11(b)
    props = read_properties(b)
    prefijos = A.device_prefixes(b)
    if verbose and not prefijos:
        print(
            "  (the device menu could not be read or its names don't decode: "
            "path A says nothing for every row)"
        )
    ok = total = 0
    for pid, p in sorted(props.items()):
        if p["name"] is None:
            continue
        total += 1
        esperado = _k1_by_name(p["name"], prefijos)
        measured: set[int] = set()
        for _crudo, slot_value, tag in p["entradas"]:
            if tag == TAG_OBJETO:
                measured |= _chase_k1(b, dest11, slot_value)
        coincide = (esperado is None and not measured) or (
            esperado is not None and esperado in measured
        )
        ok += coincide
        if verbose:
            print(
                "  %-24s id=%#04x  path A (name)=%s  path B (transitions)=%s  %s"
                % (
                    p["name"],
                    pid,
                    esperado,
                    sorted(measured),
                    "OK" if coincide else "MISMATCH",
                )
            )
    if verbose:
        print("GOLD CHECK: %d/%d match (unrounded)" % (ok, total))
    return ok, total


def read_context10(
    b: bytes, ordinal: int
) -> tuple[list[tuple[int, int, int]], str, int]:
    """`(entries, format, offset)` of `seccion[10][ordinal]` -- that
    activity's KEYBOARD CONTEXT table. Format reuses `dispositivo.
    read_header`: short `<u8 count><count x {cod,id,cls}>` or long
    `<00><u8 count><count x {flag,cod,id,cls}>`, exactly what the
    ENTER/LEAVE hooks of any screen already use -- verified it's the SAME
    table (10 fixed entries, offset = `reubicar.sections(b)[10][0]`)."""
    sec = relocate.sections(b)
    base10 = sec[10][0]
    n = b[base10]
    if not (0 <= ordinal < n):
        raise SystemExit(
            "section[10] declares %d context(s) (0..%d); ordinal %d out of range"
            % (n, n - 1, ordinal)
        )
    off = D.u24(b, base10 + 1 + 3 * ordinal) - BASE
    cab = D.read_header(b, off)
    if cab is None:
        raise SystemExit(
            "section[10][%d] (offset %#08x) doesn't parse as a header" % (ordinal, off)
        )
    entradas, fmt = cab
    return entradas, fmt, off


def activity_hooks(b: bytes, ordinal: int) -> dict[str, int | None]:
    """`{'ENTER':idx, 'LEAVE':idx, 'REFRESH':idx}` (idx in tabla[11], or
    None if that code isn't declared -- "All Off" has no LEAVE, measured)."""
    entradas, _fmt, _off = read_context10(b, ordinal)
    by_code = {cod: idx for cod, idx, _cls in entradas}
    return {
        "ENTER": by_code.get(CODE_ENTER),
        "LEAVE": by_code.get(CODE_EXIT),
        "REFRESH": by_code.get(COD_REFRESCAR),
    }


def activity_report(b: bytes, ordinal: int) -> dict:
    """Everything there is to know about an activity before editing it:
    its three hooks, decoded, with the property name where known."""
    dest11, _t11_off = _dest11(b)
    props = read_properties(b)
    ganchos = activity_hooks(b, ordinal)
    out: dict = {"ordinal": ordinal, "ganchos": {}}
    for hook_name, idx in ganchos.items():
        if idx is None:
            out["ganchos"][hook_name] = None
            continue
        atomos = []
        for value, tag in D._slots(b, dest11[idx]) or []:
            if tag == 0x7E:
                atomos.append(("PAGE", value, None))
            elif tag == 0x07:
                atomos.append(("OP07", value, None))
            elif tag == TAG_OBJETO:
                sub = []
                for v2, t2 in D._slots(b, dest11[value]) or []:
                    if t2 >= 0x80:
                        pid = t2 & 0x7F
                        sub.append(("SET", v2, props.get(pid, {}).get("name"), pid))
                    else:
                        sub.append(("atomo", v2, t2, None))
                atomos.append(("OBJ", value, sub))
            elif tag >= 0x80:
                pid = tag & 0x7F
                atomos.append(("SET", value, props.get(pid, {}).get("name"), pid))
            else:
                atomos.append(("atomo", value, tag, None))
        out["ganchos"][hook_name] = {"idx": idx, "atomos": atomos}
    return out


def print_report(b: bytes, ordinal: int) -> None:
    inf = activity_report(b, ordinal)
    print("activity %d:" % ordinal)
    for hook_name, g in inf["ganchos"].items():
        if g is None:
            print("  %-8s -- not declared" % hook_name)
            continue
        print("  %-8s obj #%d" % (hook_name, g["idx"]))
        for a in g["atomos"]:
            if a[0] == "SET":
                print("      SET %s = %s" % (a[2] or ("property %#04x" % a[3]), a[1]))
            elif a[0] == "PAGE":
                print("      PAGE(%d)" % a[1])
            elif a[0] == "OP07":
                print("      OP07(%#06x)" % a[1])
            elif a[0] == "OBJ":
                print("      obj #%d:" % a[1])
                for s in a[2]:
                    if s[0] == "SET":
                        print(
                            "          SET %s = %s"
                            % (s[2] or ("property %#04x" % s[3]), s[1])
                        )
                    else:
                        print("          atom (%s, %#04x)" % (s[1], s[2]))
            else:
                print("      atom (%s, %#04x)" % (a[1], a[2]))


# ============================================================== writing ===


def _property_by_name(b: bytes, name: str) -> tuple[int, dict]:
    props = read_properties(b)
    for pid, p in props.items():
        if p["name"] == name:
            return pid, p
    # tolerates the short name without a suffix ("Home_Power" as well as
    # "Home_Power_2"), which is how the task's example asks for it.
    cortos = {
        (p["name"].rsplit("_", 1)[0] if p["name"] else None): (pid, p)
        for pid, p in props.items()
        if p["name"]
    }
    if name in cortos:
        return cortos[name]
    raise SystemExit(
        "property %r doesn't exist in this blob's section [0]/[14]. "
        "Declared properties: %s"
        % (name, sorted(p["name"] for p in props.values() if p["name"]))
    )


def _locate_set(
    b: bytes, dest11: list[int], idx_gancho: int, pid: int
) -> tuple[int, int] | None:
    """Looks for property `pid`'s SET atom inside hook `idx_gancho`
    (top-level) OR in any of its objects nested ONE level (`0x7F`).
    Returns `(idx_of_the_object_that_contains_it, position_within_that_object)`
    or `None` if it isn't there. `idx_of_the_object_that_contains_it` is
    what has to be repointed -- it can be the hook itself or a sub-object
    like #748/#827."""
    tag_buscado = 0x80 | pid
    ranuras = D._slots(b, dest11[idx_gancho]) or []
    for pos, (v, t) in enumerate(ranuras):
        if t == tag_buscado:
            return idx_gancho, pos
    for pos, (v, t) in enumerate(ranuras):
        if t == TAG_OBJETO:
            sub = D._slots(b, dest11[v]) or []
            for pos2, (v2, t2) in enumerate(sub):
                if t2 == tag_buscado:
                    return v, pos2
    return None


def remove_set(
    b: bytes, ordinal: int, property_name: str, also_refresh: bool = True
) -> tuple[bytes, set[int], list[str]]:
    """Removes `property_name`'s SET from ENTER (and from REFRESH too
    if it's there -- REFRESH re-applies entries, it doesn't add new ones,
    measured). If the property isn't declared on that hook, nothing
    happens there (silently, on purpose: "removing something that isn't
    there" isn't an error, the activity just doesn't touch it anymore)."""
    pid, _p = _property_by_name(b, property_name)
    dest11, t11_off = _dest11(b)
    ganchos = activity_hooks(b, ordinal)
    tocados: set[int] = set()
    notas: list[str] = []
    objetivos = ["ENTER"] + (["REFRESH"] if also_refresh else [])
    fresh = b
    for hook_name in objetivos:
        idx = ganchos.get(hook_name)
        if idx is None:
            continue
        dest11, t11_off = _dest11(fresh)
        loc = _locate_set(fresh, dest11, idx, pid)
        if loc is None:
            notas.append(
                "%s had no SET %s: no change there" % (hook_name, property_name)
            )
            continue
        idx_obj, pos = loc
        ranuras = D._slots(fresh, dest11[idx_obj]) or []
        nuevas = [rv for j, rv in enumerate(ranuras) if j != pos]
        if not nuevas:
            raise SystemExit(
                "removing %s from %s would leave object #%d empty (0 "
                "slots): there's no valid way to serialize an empty "
                "object in this format (not even the factory blob has "
                "one) -- aborting instead of inventing one"
                % (property_name, hook_name, idx_obj)
            )
        cuerpo = serialize_slots(nuevas)
        fresh, off_repunte = repoint_table11(fresh, dest11, t11_off, idx_obj, cuerpo)
        tocados.update({off_repunte, off_repunte + 1, off_repunte + 2})
        notas.append(
            "%s: removed SET %s from object #%d (repointed tabla[11][%d])"
            % (hook_name, property_name, idx_obj, idx_obj)
        )
    return fresh, tocados, notas


def _add_set_in_hooks(
    b: bytes,
    ordinal: int,
    property_name: str,
    value: int,
    objetivos: list[str],
) -> tuple[bytes, set[int], list[str]]:
    """The core of `add_set`, parameterized by the EXACT list of
    hooks to touch -- used both by `add_set` (ENTER, +optional
    REFRESH) and by `--ida-y-vuelta`'s self-test (which needs to rebuild
    exactly the hook `remove_set` touched, no more, no less)."""
    pid, prop = _property_by_name(b, property_name)
    if not (0 <= value <= prop["limite"]):
        raise SystemExit(
            "%s only allows 0..%d (declared in [14]); %d was requested"
            % (property_name, prop["limite"], value)
        )
    has_transition_to_value = any(
        int.from_bytes(crudo[3:5], "little") == value
        or int.from_bytes(crudo[3:5], "little") == 0xFFFE
        for crudo, _r, _t in prop["entradas"]
    )
    if prop["n_transiciones"] and not has_transition_to_value:
        raise SystemExit(
            "%s has no transition declared toward value %d in section "
            "[14]: adding this SET would fire no IR command -- rejected "
            "instead of writing something inert" % (property_name, value)
        )
    if prop["n_transiciones"] == 0:
        raise SystemExit(
            "%s has NO transition at all in [14] (0 declared): there's no "
            "IR command it would fire, whatever the value -- rejected"
            % property_name
        )
    ganchos = activity_hooks(b, ordinal)
    tocados: set[int] = set()
    notas: list[str] = []
    fresh = b
    for hook_name in objetivos:
        idx = ganchos.get(hook_name)
        if idx is None:
            notas.append("%s not declared: no change there" % hook_name)
            continue
        dest11, t11_off = _dest11(fresh)
        if _locate_set(fresh, dest11, idx, pid) is not None:
            raise SystemExit(
                "%s already has a SET for %s -- use --cambiar-valor, not "
                "--agregar-set" % (hook_name, property_name)
            )
        ranuras = D._slots(fresh, dest11[idx]) or []
        nuevas = ranuras + [(value, 0x80 | pid)]
        cuerpo = serialize_slots(nuevas)
        fresh, off_repunte = repoint_table11(fresh, dest11, t11_off, idx, cuerpo)
        tocados.update({off_repunte, off_repunte + 1, off_repunte + 2})
        notas.append(
            "%s: added SET %s=%d directly on object #%d (repointed "
            "tabla[11][%d])" % (hook_name, property_name, value, idx, idx)
        )
    return fresh, tocados, notas


def add_set(
    b: bytes,
    ordinal: int,
    property_name: str,
    value: int,
    also_refresh: bool = False,
) -> tuple[bytes, set[int], list[str]]:
    """Adds a new SET, DIRECTLY on the ENTER hook (top-level, not nested
    -- simpler and functionally identical: the firmware's atom drainer
    processes an object's top level the same as a nested level, measured
    on `#2070`, which carries `SET DVR_Power=0` LOOSE alongside
    its two sub-objects `#609`/`#580`). REJECTS if the property has NO
    transition at all in `[14]` for the requested `value` (adding a SET
    that fires nothing would be silently useless) or if it's already set."""
    objetivos = ["ENTER"] + (["REFRESH"] if also_refresh else [])
    return _add_set_in_hooks(b, ordinal, property_name, value, objetivos)


def change_value(
    b: bytes,
    ordinal: int,
    property_name: str,
    new_value: int,
    also_refresh: bool = True,
) -> tuple[bytes, set[int], list[str]]:
    """Changes the VALUE of a SET that's already set (e.g. the TV's
    input). Locates the object that actually carries that atom
    (top-level or nested one level) and rewrites it whole with the new
    value -- the rest of its slots, in the SAME ORDER, untouched."""
    pid, prop = _property_by_name(b, property_name)
    if not (0 <= new_value <= prop["limite"]):
        raise SystemExit(
            "%s only allows 0..%d (declared in [14]); %d was requested"
            % (property_name, prop["limite"], new_value)
        )
    dest11, t11_off = _dest11(b)
    ganchos = activity_hooks(b, ordinal)
    tocados: set[int] = set()
    notas: list[str] = []
    objetivos = ["ENTER"] + (["REFRESH"] if also_refresh else [])
    fresh = b
    for hook_name in objetivos:
        idx = ganchos.get(hook_name)
        if idx is None:
            continue
        dest11, t11_off = _dest11(fresh)
        loc = _locate_set(fresh, dest11, idx, pid)
        if loc is None:
            notas.append(
                "%s had no SET %s: no change there (use --agregar-set if "
                "needed)" % (hook_name, property_name)
            )
            continue
        idx_obj, pos = loc
        ranuras = D._slots(fresh, dest11[idx_obj]) or []
        _old_value, tag = ranuras[pos]
        ranuras[pos] = (new_value, tag)
        cuerpo = serialize_slots(ranuras)
        fresh, off_repunte = repoint_table11(fresh, dest11, t11_off, idx_obj, cuerpo)
        tocados.update({off_repunte, off_repunte + 1, off_repunte + 2})
        notas.append(
            "%s: %s = %d (was %d), object #%d repointed"
            % (hook_name, property_name, new_value, _old_value, idx_obj)
        )
    return fresh, tocados, notas


# --------------------------------------------------------- assembler ---
# inverse of `device.disassemble`, only for the opcodes that
# actually appear in tabla[6][44]'s program (measured: CALL/ATTR/TXTIN/
# TXT/BMP/FIN). SWITCH/JMP/RET/ATOMO/RECT don't appear there; if they
# ever showed up in ANOTHER program, this function rejects them with
# SystemExit instead of guessing their shape -- they aren't verified in
# reverse.


def _assemble_one(op: str, ar: tuple) -> bytes:
    if op == "FIN":
        return b"\x00"
    if op == "RET":
        return b"\x17"
    if op == "CALL":
        return b"\x16" + D.p(ar[0])
    if op == "JMP":
        return b"\x14" + D.p(ar[0])
    if op == "ATTR":
        return bytes([0x10, ar[0]])
    if op == "BMP":
        return bytes([0x02, ar[0], ar[1]]) + D.p(ar[2])
    if op == "TXT":
        return bytes([0x04, ar[0], ar[1]]) + D.p(ar[2])
    if op == "TXTIN":
        return bytes([0x05, ar[0], ar[1]]) + ar[2] + b"\x00"
    if op == "ATOMO":
        return bytes([0x11]) + ar[0].to_bytes(2, "little") + bytes([ar[1]])
    if op == "RECT":
        return bytes([0x01, ar[0], ar[1], ar[2], ar[3]])
    raise SystemExit(
        "edit_activity.py doesn't know how to re-assemble opcode %r: it "
        "doesn't appear in tabla[6][44]'s program and isn't verified in "
        "reverse -- aborting instead of guessing its shape" % op
    )


def assemble_program(ins: list[tuple[int, str, tuple]]) -> bytes:
    out = bytearray()
    for _off, op, ar in ins:
        out += _assemble_one(op, ar)
    return bytes(out)


def _activity_row_in_menu(b: bytes, activity_ordinal: int) -> dict:
    dest11, _ = _dest11(b)
    m = A.activities_menu(b, dest11)
    if m is None:
        raise SystemExit("the activities menu (tabla[6][44]) was not found")
    for f in m["rows"]:
        if f["act"] == activity_ordinal:
            return {**m, "row": f}
    raise SystemExit(
        "activity %d has no row in the menu (ordinal %d): it's already "
        "removed, or it's 'All Off' (physical, no row) -- can't be "
        "renamed/removed" % (activity_ordinal, m["ordinal"])
    )


def _index_of_drawn_name(b: bytes, m: dict, activity_ordinal: int) -> int:
    """The index, within the page's program, of the instruction that
    draws that row's NAME.

    Row k drawn top to bottom uses zone code k -- the same geometric
    invariant `device.read_extra_rows` already requires and
    `activities.activity_names` uses. If the program draws a
    different number of names than there are live rows, it aborts:
    there's no way to locate the row with certainty and guessing would
    delete the wrong one.
    """
    ins = D.disassemble(b, m["prog"])
    plant = D.read_section19(b)
    order = D.template_buttons(plant.get(m["K"], []))
    by_code = {f["codigo"]: f["act"] for f in m["rows"]}
    dibujadas_idx = [
        (ar[1], i)
        for i, (_off, op, ar) in enumerate(ins)
        if op in ("TXT", "TXTIN") and ar[0] == D.TAG_NAME
    ]
    dibujadas_idx.sort()
    presentes = [c for c in order if c in by_code]
    if len(presentes) != len(dibujadas_idx):
        raise SystemExit(
            "the activities page's program draws %d name(s) but the key "
            "register declares %d activity row(s): out of sync, cannot "
            "locate the row with certainty" % (len(dibujadas_idx), len(presentes))
        )
    for cod, (_y, idx_ins) in zip(presentes, dibujadas_idx):
        if by_code[cod] == activity_ordinal:
            return idx_ins
    raise SystemExit("could not locate activity %d's row" % activity_ordinal)


#: Y offset from a row's TOP (where the big icon is drawn) down to the
#: baseline of its name. Factory geometry, the SAME one `dibujo.device_row_ops`
#: emits and `device` re-measures: big icon 164x50 at (0x06, Y), small
#: icon 51x48 at (0x0B, Y+1), name at (0x3F, Y+19). Verified on the
#: activities menu (tabla[6][44]): rows at Y=38/92, names at Y=57/111.
Y_TEXTO_OFFSET = 19
Y_ICONO_CHICO_OFFSET = 1


def _row_icon_indices(ins: list, row_y: int) -> list[int]:
    """Indices, inside a page's program, of the two `BMP`s that draw the
    ICONS of the row whose top is at `row_y`.

    Same factory shape as the name: big icon at (`X_ICONO_GRANDE`, Y),
    small one at (`X_ICONO_CHICO`, Y+1). Returns them sorted. Aborts if
    it finds ONE and not the other -- half a row would be left drawn and
    that is exactly the defect this exists to prevent.
    """
    grande = [
        i
        for i, (_off, op, ar) in enumerate(ins)
        if op == "BMP" and ar[0] == D.X_ICONO_GRANDE and ar[1] == row_y
    ]
    chico = [
        i
        for i, (_off, op, ar) in enumerate(ins)
        if op == "BMP"
        and ar[0] == D.X_ICONO_CHICO
        and ar[1] == row_y + Y_ICONO_CHICO_OFFSET
    ]
    if len(grande) != len(chico):
        raise SystemExit(
            "the row at Y=%d draws %d big icon(s) and %d small one(s): "
            "aborting instead of leaving half a row drawn"
            % (row_y, len(grande), len(chico))
        )
    if len(grande) > 1:
        raise SystemExit(
            "the row at Y=%d draws its icon %d times: out of sync, cannot "
            "remove it with certainty" % (row_y, len(grande))
        )
    return sorted(grande + chico)


def _indices_of_drawn_row(b: bytes, m: dict, activity_ordinal: int) -> list[int]:
    """EVERY instruction index that draws `activity_ordinal`'s row: its
    NAME **and its two ICONS**.

    `--erase` used to remove only the name, and the result was VERIFIED
    defective on the device: "I saw it deleted the activity but the name
    and the icon are still there, and it doesn't work". Check (f)
    (`names drawn == live rows`) did NOT catch it because it only counts
    `TXT`/`TXTIN` with `X == TAG_NAME`; the two `BMP`s of the row were
    outside its net. `check_menu_icons_synced()` (h) closes that.
    """
    ins = D.disassemble(b, m["prog"])
    idx_txt = _index_of_drawn_name(b, m, activity_ordinal)
    text_y = ins[idx_txt][2][1]
    row_y = text_y - Y_TEXTO_OFFSET
    return sorted([idx_txt] + _row_icon_indices(ins, row_y))


def _row_tops(ins: list) -> list[int]:
    """The Y of the top of every row the program DRAWS, derived from the
    names (`X == TAG_NAME`) and snapped to the factory grid
    (`Y_ROW_0 + k*ROW_STEP`). Rows off the grid are reported as -1 so a
    caller notices instead of silently ignoring them."""
    tops = []
    for _off, op, ar in ins:
        if op in ("TXT", "TXTIN") and ar[0] == D.TAG_NAME:
            y = ar[1] - Y_TEXTO_OFFSET
            tops.append(y if (y - D.Y_ROW_0) % D.ROW_STEP == 0 else -1)
    return tops


def rename_activity(
    b: bytes, activity_ordinal: int, new_name: str
) -> tuple[bytes, set[int], list[str]]:
    """Renames `activity_ordinal`'s row in tabla[6][44]. Validates with
    `fonts.choose()` (the hardware can't draw Q/X/Z, see `fonts.py`)
    and writes with `fonts.encode()` -- exactly the pair the task
    asked for. Rewrites the page's WHOLE program (preserves byte for byte
    everything that isn't this row) and repoints IN PLACE the page's one
    slot's `prog` field (3 B, inside a fixed-width structure: no need for
    a two-step add-and-repoint, repointing alone is enough)."""
    m = _activity_row_in_menu(b, activity_ordinal)
    ins = D.disassemble(b, m["prog"])
    dest11, _ = _dest11(b)
    plant = D.read_section19(b)
    order = D.template_buttons(plant.get(m["K"], []))
    by_code = {f["codigo"]: f["act"] for f in m["rows"]}
    dibujadas_idx = []  # (y, index_in_ins) of each TXT/TXTIN with TAG_NOMBRE
    for i, (_off, op, ar) in enumerate(ins):
        if op == "TXT" and ar[0] == D.TAG_NAME:
            dibujadas_idx.append((ar[1], i))
        elif op == "TXTIN" and ar[0] == D.TAG_NAME:
            dibujadas_idx.append((ar[1], i))
    dibujadas_idx.sort()
    presentes = [c for c in order if c in by_code]
    if len(presentes) != len(dibujadas_idx):
        raise SystemExit(
            "the activities page's program draws %d name(s) but the key "
            "register declares %d activity row(s): out of sync, cannot "
            "locate the row with certainty" % (len(dibujadas_idx), len(presentes))
        )
    idx_ins_objetivo = None
    for cod, (_y, idx_ins) in zip(presentes, dibujadas_idx):
        if by_code[cod] == activity_ordinal:
            idx_ins_objetivo = idx_ins
            y_objetivo = ins[idx_ins][2][1]
            break
    if idx_ins_objetivo is None:
        raise SystemExit("could not locate activity %d's row" % activity_ordinal)

    # the ATTR in effect at that point of the program (the one that
    # actually draws that row), not an assumed value: walked from the start.
    attr_vigente = None
    for _off, op, ar in ins[: idx_ins_objetivo + 1]:
        if op == "ATTR":
            attr_vigente = ar[0]
    if attr_vigente is None:
        raise SystemExit("could not determine that row's ATTR in effect")

    atributo = fonts.choose(new_name, b, contexto=attr_vigente)
    if atributo is None:
        detail = fonts.choose_detail(new_name, b, contexto=attr_vigente)
        raise SystemExit(
            "%r cannot be drawn with a font from the context's palette "
            "(ATTR %d): %s -- the hardware has no Q/X/Z in any font, and "
            "some fonts don't even cover every other letter"
            % (new_name, attr_vigente, detail)
        )
    cod = fonts.encode(new_name, atributo, b)
    if cod is None:
        raise SystemExit(
            "%r passed fuentes.choose() but fuentes.encode() could not "
            "encode it with attribute %d -- inconsistency between the two "
            "functions, aborting instead of writing garbage" % (new_name, atributo)
        )

    fresh, off_text = _append_tail(b, cod)
    new_ins = list(ins)
    old_op = new_ins[idx_ins_objetivo][1]
    new_ins[idx_ins_objetivo] = (
        0,
        "TXT",
        (D.TAG_NAME, y_objetivo, D.BASE + off_text - D.BASE),
    )
    # note: TXT takes a FILE OFFSET, not a ptr24 -- D.p() adds the base inside.
    new_ins[idx_ins_objetivo] = (0, "TXT", (D.TAG_NAME, y_objetivo, off_text))
    if atributo != attr_vigente:
        # the chosen attribute is a different palette than the context's:
        # it has to be declared before this row and the previous one
        # RESTORED afterward, so the attribute of what follows in the
        # program doesn't shift.
        new_ins = (
            new_ins[:idx_ins_objetivo]
            + [(0, "ATTR", (atributo,))]
            + [new_ins[idx_ins_objetivo]]
            + [(0, "ATTR", (attr_vigente,))]
            + new_ins[idx_ins_objetivo + 1 :]
        )
    cuerpo_programa = assemble_program(new_ins)
    fresh, off_programa = _append_tail(fresh, cuerpo_programa)

    slot_off = m["slot"]
    out = bytearray(fresh)
    out[slot_off + 4 : slot_off + 7] = (BASE + off_programa).to_bytes(3, "little")
    configcheck.arreglar(out)
    fresh = bytes(out)
    tocados = {slot_off + 4, slot_off + 5, slot_off + 6}
    return (
        fresh,
        tocados,
        [
            "activity %d's row renamed to %r (old drawing: %s, ATTR %d%s)"
            % (
                activity_ordinal,
                new_name,
                old_op,
                atributo,
                ""
                if atributo == attr_vigente
                else " (different from context %d, declared and restored)"
                % attr_vigente,
            )
        ],
    )


def delete_activity(
    b: bytes, activity_ordinal: int
) -> tuple[bytes, set[int], list[str]]:
    """Removes the row from `table[6][44]` (repoints the slot's `keyreg`,
    without that entry) AND neutralizes `seccion[10][activity_ordinal]`
    (repoints that entry of the little 10-pointer table to an empty
    context) -- the TWO parts the task asked for.

    Doesn't delete `#2410`/`#2411` or any object: they stay alive-but-dead,
    the usual criterion.

    ALSO removes the NAME from the drawing program. It used not to, and
    the result was VERIFIED defective: `prog` came out byte for byte
    identical (`0x00f931` before and after), the deleted row was still
    drawn on screen, and `activities.activity_names()` started
    returning `{}` because of the "2 drawn texts / 1 live row" mismatch --
    which degraded a DEVICE deletion's warning from "TV HD and PC" down
    to "activity 7". The `TXT`/`TXTIN` instruction for that row is
    removed and the slot's `prog` field is repointed (3 B, fixed width),
    the same mechanism `rename_activity()` already uses.
    `check_menu_synced()` verifies it.

    ALSO removes the row's TWO ICONS (big 164x50 at (0x06,Y), small
    51x48 at (0x0B,Y+1) -- the factory row shape). It used not to, and
    the result was VERIFIED defective on the device: "I saw it deleted
    the activity but the name and the icon are still there, and it
    doesn't work" -- a ghost row, drawn and untouchable. Check (f) let it
    through because it only counted names; `check_menu_icons_synced()`
    (h) now counts icons too.

    LIMIT LEFT, declared: the K=7 template's geometry -- whether rows are
    positional like the Devices menu or fixed by their Y -- wasn't
    verified, so the remaining rows aren't recompacted: whichever is left
    is drawn where it was (and keeps declaring the touch zone of the
    position it is drawn at, so what's seen stays what's touched)."""
    m = _activity_row_in_menu(b, activity_ordinal)
    kr = D.read_key_register(b, m["keyreg"])
    if kr is None:
        raise SystemExit("the activities page's key register doesn't parse")
    id_cortado = m["row"]["id"]
    nuevas = [(cod, ident, cls) for cod, ident, cls in kr if ident != id_cortado]
    if len(nuevas) == len(kr):
        raise SystemExit(
            "activity %d's row doesn't appear in the keyreg" % activity_ordinal
        )
    cuerpo_kr = D.build_raw_register(nuevas)
    slot_off = m["slot"]
    out = bytearray(b)
    fresh, off_kr = _append_tail(bytes(out), cuerpo_kr)
    out = bytearray(fresh)
    out[slot_off + 1 : slot_off + 4] = (BASE + off_kr).to_bytes(3, "little")
    configcheck.arreglar(out)
    fresh = bytes(out)
    tocados = {slot_off + 1, slot_off + 2, slot_off + 3}

    # ---- also remove the NAME **and the two ICONS** from the program, so
    # no ghost is left. Removing only the name left the row's graphic on
    # screen (measured on the device).
    row_indices = set(_indices_of_drawn_row(b, m, activity_ordinal))
    ins = D.disassemble(b, m["prog"])
    without_row = [x for i, x in enumerate(ins) if i not in row_indices]
    fresh, off_prog = _append_tail(fresh, assemble_program(without_row))
    out = bytearray(fresh)
    out[slot_off + 4 : slot_off + 7] = (BASE + off_prog).to_bytes(3, "little")
    configcheck.arreglar(out)
    fresh = bytes(out)
    tocados |= {slot_off + 4, slot_off + 5, slot_off + 6}

    # neutralize seccion[10][ordinal]: repoint that entry to an empty
    # context. TWO bytes, not one: `<u8 short=0><u8 long=0>`. With a
    # single byte, the LONG form reader (the one All Off uses: `00 05
    # ...`) would read the blob's next byte as the count, which is
    # garbage from the tail.
    sec = relocate.sections(fresh)
    base10 = sec[10][0]
    new2, off_ctx = _append_tail(fresh, b"\x00\x00")
    entry10_off = base10 + 1 + 3 * activity_ordinal
    out2 = bytearray(new2)
    out2[entry10_off : entry10_off + 3] = (BASE + off_ctx).to_bytes(3, "little")
    configcheck.arreglar(out2)
    new2 = bytes(out2)
    tocados |= {entry10_off, entry10_off + 1, entry10_off + 2}

    return (
        new2,
        tocados,
        [
            "activity %d's row removed from tabla[6][44]'s keyreg (id %d)"
            % (activity_ordinal, id_cortado),
            "the row's %d drawing instruction(s) removed from the program "
            "(name + the 2 icons): nothing of it is drawn any more" % len(row_indices),
            "seccion[10][%d] repointed to an empty context (0 rows)"
            % activity_ordinal,
        ],
    )


# =============================================================== checks ==


def check_relocation_chain(referencia: bytes, fresh: bytes) -> bool:
    return relocate.chain(referencia) == relocate.chain(fresh)


def check_activities_not_orphaned(b: bytes) -> tuple[bool, list[str]]:
    """No remaining activity can emit a `k1` outside section [5]. Seeded
    by `_cmds_of_activity`, i.e. hook PLUS `[14]` -- it used to follow
    only `0x7F` from the hook and reached no command at all."""
    dest11, _ = _dest11(b)
    devs5 = D.read_section5(b)
    n_dev = len(devs5)
    problemas = []
    for ordinal in TODOS_LOS_ORDINALES:
        try:
            cmds = _cmds_of_activity(b, dest11, ordinal)
        except SystemExit as e:
            problemas.append("activity %d: %s" % (ordinal, e))
            continue
        outside = sorted({c >> 8 for c in cmds if not 0 <= (c >> 8) < n_dev})
        if outside:
            problemas.append(
                "activity %d: references nonexistent device(s) %s" % (ordinal, outside)
            )
    return not problemas, problemas


def check_commands_resolve(b: bytes) -> tuple[bool, list[str]]:
    """Every `(k1,k2)` the activities can emit resolves through
    `cmd_setup_ir`'s arithmetic -- same notion as `delete_device.py`'s check (a),
    reusing `device.read_section5`.

    Seeded by `_cmds_of_activity`. The WHOLE engine
    (`activities.engine_commands`) is also re-checked, which covers
    property transitions no activity SETs today but that are still alive
    in `[14]`.
    """
    dest11, _ = _dest11(b)
    devs5 = D.read_section5(b)
    problemas = []

    def check(cmds, label):
        for c in sorted(cmds):
            k1, k2 = c >> 8, c & 0xFF
            if not (0 <= k1 < len(devs5) and 0 <= k2 < devs5[k1]["n"]):
                problemas.append(
                    "%s: command (k1=%d,k2=%d) outside section [5]" % (label, k1, k2)
                )

    for ordinal in TODOS_LOS_ORDINALES:
        try:
            check(_cmds_of_activity(b, dest11, ordinal), "activity %d" % ordinal)
        except SystemExit:
            continue
    check(A.engine_commands(b, dest11), "engine [14]")
    return not problemas, problemas


def check_menu_synced(b: bytes) -> tuple[bool, list[str]]:
    """The names DRAWN in the menu have to be as many as the key
    register's LIVE rows.

    Exists because `--erase` used to leave the program intact: the
    deleted row stayed drawn (a ghost row) and, worse,
    `activities.activity_names` would return `{}` because of the
    mismatch -- which degraded a DEVICE deletion's warning to "activity
    7" instead of the name.
    """
    dest11, _ = _dest11(b)
    m = A.activities_menu(b, dest11)
    if m is None:
        return True, []
    ins = D.disassemble(b, m["prog"])
    dibujados = sum(
        1 for _o, op, ar in ins if op in ("TXT", "TXTIN") and ar[0] == D.TAG_NAME
    )
    vivas = len(m["rows"])
    if dibujados != vivas:
        return False, [
            "the activities menu draws %d name(s) and the key register "
            "declares %d row(s): a ghost row would be left" % (dibujados, vivas)
        ]
    return True, []


def check_menu_icons_synced(b: bytes) -> tuple[bool, list[str]]:
    """The ICONS drawn in the menu have to be as many as the live rows,
    and sit on the SAME rows the names do.

    Exists because check (f) only counted names: `--erase` removed the
    `TXT` and left the row's two `BMP`s, and the device showed the
    deleted activity's graphic, drawn and untouchable ("I saw it deleted
    the activity but the name and the icon are still there, and it
    doesn't work"). Counting is not enough -- the icons are matched
    POSITION BY POSITION against the names' rows, so removing the wrong
    row's icon is caught too.
    """
    dest11, _ = _dest11(b)
    m = A.activities_menu(b, dest11)
    if m is None:
        return True, []
    ins = D.disassemble(b, m["prog"])
    name_rows = _row_tops(ins)
    if -1 in name_rows:
        return False, [
            "the activities menu draws a name off the factory row grid "
            "(Y_FILA_0=%d, step %d): %s" % (D.Y_ROW_0, D.ROW_STEP, name_rows)
        ]
    grandes = sorted(
        ar[1]
        for _o, op, ar in ins
        if op == "BMP" and ar[0] == D.X_ICONO_GRANDE and ar[1] >= D.Y_ROW_0
    )
    chicos = sorted(
        ar[1] - Y_ICONO_CHICO_OFFSET
        for _o, op, ar in ins
        if op == "BMP" and ar[0] == D.X_ICONO_CHICO and ar[1] > D.Y_ROW_0
    )
    esperado = sorted(name_rows)
    problemas = []
    if grandes != esperado:
        problemas.append(
            "the activities menu draws big icons on rows %s and names on "
            "rows %s: a ghost row would be left (icon without a name)"
            % (grandes, esperado)
        )
    if chicos != esperado:
        problemas.append(
            "the activities menu draws small icons on rows %s and names on "
            "rows %s: a ghost row would be left (icon without a name)"
            % (chicos, esperado)
        )
    if len(esperado) != len(m["rows"]):
        problemas.append(
            "the activities menu draws %d row(s) and the key register "
            "declares %d" % (len(esperado), len(m["rows"]))
        )
    return not problemas, problemas


def run_checks(
    referencia: bytes, fresh: bytes, tocados: set[int], verbose: bool = True
) -> bool:
    todo_ok = True

    ok_cadena = check_relocation_chain(referencia, fresh)
    todo_ok &= ok_cadena
    if verbose:
        print(
            "  (a) reubicar.chain() identical before/after: %s"
            % ("YES" if ok_cadena else "NO")
        )

    ok_cmd, problemas_cmd = check_commands_resolve(fresh)
    todo_ok &= ok_cmd
    if verbose:
        print(
            "  (b) activities' commands resolve through section [5]: %s%s"
            % (
                "YES" if ok_cmd else "NO",
                "" if ok_cmd else "  " + "; ".join(problemas_cmd),
            )
        )

    ok_pos, dif_pos = write.nothing_moved(referencia, fresh, extra=tocados)
    ok_neg, _dif_neg = write.nothing_moved(referencia, fresh, extra=set())
    ok_repunte = ok_pos and not ok_neg
    todo_ok &= ok_repunte
    if verbose:
        print(
            "  (c) grabar.nada_se_movio: positive=%s (with %d declared "
            "repoint(s)), negative=%s (without declaring them)  %s"
            % (
                ok_pos,
                len(tocados),
                ok_neg,
                "OK" if ok_repunte else "FAIL -- the negative should have been False",
            )
        )
        if not ok_pos:
            allowed = write.ALLOWED | tocados
            no_declaradas = [d for d in dif_pos if d not in allowed]
            print(
                "      NOT declared differences: %s"
                % [hex(d) for d in no_declaradas[:10]]
            )

    problemas_check = [t for t, ok, _d in configcheck.revisar(fresh) if not ok]
    ok_check = not problemas_check
    todo_ok &= ok_check
    if verbose:
        print(
            "  (d) configcheck.revisar(): %s%s"
            % (
                "green" if ok_check else "FAIL",
                "" if ok_check else "  " + str(problemas_check),
            )
        )

    ok_huerf, problemas_huerf = check_activities_not_orphaned(fresh)
    todo_ok &= ok_huerf
    if verbose:
        print(
            "  (e) no activity references a nonexistent device: %s%s"
            % (
                "YES" if ok_huerf else "NO",
                "" if ok_huerf else "  " + "; ".join(problemas_huerf),
            )
        )

    ok_menu, problemas_menu = check_menu_synced(fresh)
    todo_ok &= ok_menu
    if verbose:
        print(
            "  (f) the menu draws as many names as live rows: %s%s"
            % (
                "YES" if ok_menu else "NO",
                "" if ok_menu else "  " + "; ".join(problemas_menu),
            )
        )

    ok_ico, problemas_ico = check_menu_icons_synced(fresh)
    todo_ok &= ok_ico
    if verbose:
        print(
            "  (h) the menu draws the ICONS on the same rows as the names "
            "(no half-erased ghost row): %s%s"
            % (
                "YES" if ok_ico else "NO",
                "" if ok_ico else "  " + "; ".join(problemas_ico),
            )
        )

    # NEGATIVE of (b)/(e): if poisoning a command reachable ONLY through
    # the state machine does NOT make (b) and (e) fail, those two checks
    # aren't measuring anything -- which is exactly what used to happen.
    neg = negative_check_of_check_b(fresh)
    ok_neg_b = (
        bool(neg.get("corrio")) and neg["check_b_saw_it"] and neg["check_e_saw_it"]
    )
    todo_ok &= ok_neg_b
    if verbose:
        print(
            "  (g) negative of (b)/(e): with a k1=%s injected in object #%s "
            "(reachable ONLY through [14]), (b) sees it=%s and (e) sees it=%s  %s"
            % (
                neg.get("k1_invalido"),
                neg.get("objeto"),
                neg.get("check_b_saw_it"),
                neg.get("check_e_saw_it"),
                "OK" if ok_neg_b else "FAIL -- the checks don't discriminate",
            )
        )

    return todo_ok


def negative_check_of_check_b(b: bytes) -> dict:
    """NEGATIVE CHECK for (b)/(e): poison a command-object reachable ONLY
    through `[14]` and require that the checks SEE it.

    This is the check that was missing and the reason (b) and (e) came
    back green on a blob where an activity sent a command to a
    nonexistent device: the walk never crossed the state machine. Writes
    nothing to disk.
    """
    dest11, t11_off = _dest11(b)
    devs5 = D.read_section5(b)
    k1_invalido = len(devs5) + 4
    regs = A.engine_records(b)
    objetivo = None
    for pid in range(len(regs)):
        for tr in A.transitions_of(b, pid, regs):
            if tr["tag"] != TAG_OBJETO:
                continue
            ident = tr["atomo"]
            rs = D._slots(b, dest11[ident]) if 0 <= ident < len(dest11) else None
            if rs and any(t == TAG_CMD for _v, t in rs):
                objetivo = (pid, ident, rs)
                break
        if objetivo:
            break
    if objetivo is None:
        return {"corrio": False, "reason": "there's no command-object in [14]"}
    _pid, ident, rs = objetivo
    envenenado = [
        ((k1_invalido << 8) | (v & 0xFF), t) if t == TAG_CMD else (v, t) for v, t in rs
    ]
    bad, _off = repoint_table11(b, dest11, t11_off, ident, serialize_slots(envenenado))
    ok_cmd, problemas = check_commands_resolve(bad)
    ok_huerf, _ = check_activities_not_orphaned(bad)
    return {
        "corrio": True,
        "objeto": ident,
        "k1_invalido": k1_invalido,
        "check_b_saw_it": not ok_cmd,
        "check_e_saw_it": not ok_huerf,
        "problemas": problemas[:3],
    }


# =================================================================== CLI ===


def _parse_prop_value(s: str) -> tuple[str, int]:
    if "=" not in s:
        raise SystemExit("expected format PROPERTY=VALUE, got %r" % s)
    name, value = s.split("=", 1)
    return name.strip(), int(value.strip())


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument(
        "--index", type=int, help="activity ordinal: 7=TV HD, 8=PC, 9=All Off"
    )
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--gold-check", action="store_true")
    ap.add_argument("--remove-set", metavar="PROPIEDAD")
    ap.add_argument("--add-set", metavar="PROPIEDAD=VALOR")
    ap.add_argument("--change-value", metavar="PROPIEDAD=VALOR")
    ap.add_argument("--renombrar", metavar="NOMBRE")
    ap.add_argument("--erase", action="store_true")
    ap.add_argument(
        "--sin-refresh",
        action="store_true",
        help="don't mirror the change onto REFRESH",
    )
    ap.add_argument("--salida")
    ap.add_argument("--ezhex", help="besides the .bin, wrap it in a .EZHex")
    ap.add_argument("--plantilla", help="EZHex the header comes from")
    ap.add_argument(
        "--ida-y-vuelta",
        action="store_true",
        help="applies the edit and its inverse; compares the model",
    )
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    if b[:4] != b"GSPM":
        raise SystemExit("the blob doesn't start with GSPM")
    D.T6 = D.u24(b, D.MAESTRO_T6) - BASE
    print("input blob: %s  %d B" % (a.blob, len(b)))

    if a.gold_check:
        gold_check(b)

    if a.listar:
        indices = (a.index,) if a.index is not None else TODOS_LOS_ORDINALES
        for i in indices:
            print_report(b, i)

    ediciones = [
        opt
        for opt in ("remove_set", "add_set", "change_value", "renombrar", "erase")
        if getattr(a, opt)
    ]
    if not ediciones:
        return 0
    if len(ediciones) > 1:
        raise SystemExit("only one edit per run: %s" % ediciones)
    if a.index is None:
        raise SystemExit("--indice is required to edit")

    also_refresh = not a.sin_refresh

    def apply(blob_in: bytes) -> tuple[bytes, set[int], list[str]]:
        if a.remove_set:
            return remove_set(blob_in, a.index, a.remove_set, also_refresh)
        if a.add_set:
            name, value = _parse_prop_value(a.add_set)
            return add_set(blob_in, a.index, name, value, also_refresh)
        if a.change_value:
            name, value = _parse_prop_value(a.change_value)
            return change_value(blob_in, a.index, name, value, also_refresh)
        if a.renombrar:
            if a.index not in ORDINALES_CON_FILA:
                raise SystemExit(
                    "activity %d has no menu row: cannot be renamed" % a.index
                )
            return rename_activity(blob_in, a.index, a.renombrar)
        if a.erase:
            if a.index not in ORDINALES_CON_FILA:
                raise SystemExit(
                    "activity %d has no menu row (it's physical, global "
                    "key 0xA5): there's no row to cut" % a.index
                )
            return delete_activity(blob_in, a.index)
        raise AssertionError

    fresh, tocados, notas = apply(b)
    for n in notas:
        print("  " + n)
    # The offsets being repointed on purpose, in the SAME format
    # `delete_device.py` prints, so whoever builds the write command (by hand or
    # the app) can declare them as-is. Changing a pointer's target doesn't
    # move a byte, but it still falls under `grabar.nothing_moved`'s net.
    print("\npointers repointed on purpose (declare them when grabbing):")
    print("   " + " ".join("--repunta %#08x" % q for q in sorted(tocados)))
    print("\nchecks:")
    ok = run_checks(b, fresh, tocados)
    print(
        "\nRESULT: %s"
        % ("OK, every check came back green" if ok else "one or more checks FAILED")
    )

    if a.ida_y_vuelta:
        print("\nround trip:")
        if a.remove_set:
            # rebuild EXACTLY what was removed: for each hook, whether the
            # property was there before and with what value -- not
            # "tambien_refresh" blindly, which would add it to REFRESH
            # even if it was never there (that was precisely the bug this
            # self-test caught).
            pid0, _prop0 = _property_by_name(b, a.remove_set)
            dest11_b, _ = _dest11(b)
            ganchos_b = activity_hooks(b, a.index)
            vuelta = fresh
            objetivos_previos = ["ENTER"] + (["REFRESH"] if also_refresh else [])
            for gancho in objetivos_previos:
                idx_b = ganchos_b.get(gancho)
                if idx_b is None:
                    continue
                loc_b = _locate_set(b, dest11_b, idx_b, pid0)
                if loc_b is None:
                    continue
                idx_obj_b, pos_b = loc_b
                value_b = D._slots(b, dest11_b[idx_obj_b])[pos_b][0]
                vuelta, _t2, _n2 = _add_set_in_hooks(
                    vuelta, a.index, a.remove_set, value_b, [gancho]
                )
        elif a.change_value:
            name, _value = _parse_prop_value(a.change_value)
            _pid, prop_original = _property_by_name(b, name)
            ganchos = activity_hooks(b, a.index)
            dest11, _ = _dest11(b)
            loc = _locate_set(b, dest11, ganchos["ENTER"], _pid)
            original_value = D._slots(b, dest11[loc[0]])[loc[1]][0] if loc else None
            if original_value is None:
                print(
                    "  (could not determine the original value: skipping the round trip)"
                )
                vuelta = None
            else:
                vuelta, _t2, _n2 = change_value(
                    fresh, a.index, name, original_value, also_refresh
                )
        elif a.add_set:
            name, _value = _parse_prop_value(a.add_set)
            vuelta, _t2, _n2 = remove_set(fresh, a.index, name, also_refresh)
        elif a.renombrar:
            dec = delete_device._decodificador(b)
            names_before = A.activity_names(b, dec)
            original_name = names_before.get(a.index)
            if not original_name or "?" in original_name:
                print(
                    "  (could not decode the original name with certainty "
                    "(%r): skipping the round trip)" % original_name
                )
                vuelta = None
            else:
                vuelta, _t2, _n2 = rename_activity(fresh, a.index, original_name)
        else:
            print(
                "  (--borrar has no automatic inverse: a removed row cannot be recreated)"
            )
            vuelta = None

        if vuelta is not None:
            report_before = activity_report(b, a.index)
            report_after = activity_report(vuelta, a.index)

            def _clave(par):
                name, value = par
                return (name is None, name or "", value)

            def _normalizar(inf):
                out = {}
                for gn, g in inf["ganchos"].items():
                    if g is None:
                        out[gn] = None
                        continue
                    sets = [(x[2], x[1]) for x in g["atomos"] if x[0] == "SET"] + [
                        (s[2], s[1])
                        for x in g["atomos"]
                        if x[0] == "OBJ"
                        for s in x[2]
                        if s[0] == "SET"
                    ]
                    out[gn] = sorted(sets, key=_clave)
                return out

            igual = _normalizar(report_before) == _normalizar(report_after)
            print(
                "  model before == model after the round trip: %s"
                % ("YES" if igual else "NO")
            )
            if not igual:
                print("    before: %s" % _normalizar(report_before))
                print("    after:  %s" % _normalizar(report_after))

    if a.salida:
        if not ok:
            print("\n--salida is NOT written: not every check came back green.")
            return 1
        pathlib.Path(a.salida).write_bytes(fresh)
        print(
            "\nwritten %s  (%d B, +%d against the input)"
            % (a.salida, len(fresh), len(fresh) - len(b))
        )
        if a.ezhex:
            # by SUBPROCESS, same as `delete_device.py`: the EZHex wrapper
            # (header + installer checksum) is built by `ezhex.py`, not
            # reimplemented here.
            if not a.plantilla:
                raise SystemExit("--ezhex needs --plantilla")
            import subprocess  # noqa: PLC0415
            import sys  # noqa: PLC0415

            r = subprocess.run(
                [sys.executable, "ezhex.py", "armar", a.plantilla, a.salida, a.ezhex],
                capture_output=True,
                text=True,
                cwd=str(pathlib.Path(__file__).parent),
                check=False,
            )
            print("ezhex.py armar:")
            for ln in (r.stdout or "").strip().splitlines():
                print("   " + ln)
            if r.returncode:
                print((r.stderr or "").strip())
                return 1

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
