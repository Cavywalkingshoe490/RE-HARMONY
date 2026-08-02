#!/usr/bin/env python3
"""Reassign the Harmony One's PHYSICAL KEYS (not screen zones), without
moving a single byte. Complements `keys_map.py` (which only covers
`table[6]`'s 8 touch-zone codes) with the mechanism that was missing:
section `[10]`.

## TWO SITES, and they are not interchangeable (added after grabada #7)

A rubber key is not bound once. The remote picks what to send by looking at
WHERE the user is, and this module writes both places:

  * `apply_physical()` -- the keyboard CONTEXT `[10][n]`: in force only while
    that Activity is RUNNING. This is all the module used to do, and it is
    what grabada #7 wrote: correct, verified, and invisible, because the user
    was standing on the LG's page in Devices with no activity running.
  * `apply_device()` -- the DEVICE PAGE's own key register (its
    `table[6]` trailer's `hdr`): in force whenever you are on that device's
    page. It is where the factory binds each of its three devices' rubber
    keys, and it is the site somebody who just added a TV is going to test.

Which one a change reached is not argued: `teclas_alcance.checks` walks
the pointers from the master index over the generated blob and reads what is
at the end of the walk.

## THE FINDING (measured today against `output/config_empaquetada.bin`,
## md5 976bc70edd15b40f56cb49aa5113594f -- the GRABBED and RUNNING blob)

An earlier round had measured that, of the 55 buttons in the `0x67`
inventory, only 8 (the touch-zone ones) hang off an editable `table[6]`
entry, and that the other 47 ("rubber" ones: Volume, Channel, transport,
numeric) tie to no data **through that path**. That is TRUE but
incomplete: they don't tie through `table[6]`/`[9]`, but they DO tie
through section `[10]`, which until now had only been used for the 3
Activities (`edit_activity.py`).

Section `[10]` (master index slot 10) is, in its header, a fixed table of
**10 KEYBOARD CONTEXTS** (`<u8 n=10><10 x ptr24>`), and each context is a
`<code,operand,class>` record **in the exact same format** as a screen's
key register (`device.read_header`, short OR long form). Measured,
context by context, in the grabbed blob:

    [0]  4 rows    universal softkeys (EXIT/paging), not IR
    [1] 14 rows    GLOBAL keymap (menu/standby screens). Its row
                   `0xA5 -> {0xFF|9, 0x1F}` is the SAME shape a menu row
                   uses to enter an Activity (`activities.py` line 76):
                   it enters context [10][9] = "All Off". Its other rows
                   resolve to UI/LED actions (classes 0x71/0x72/0x75/0x92/
                   0x3F, the SAME family as the drawing layer, see
                   ESTADO.md), not to IR commands: not editable by this
                   module, and declared as such, not skipped.
    [2] 53 rows    standby CATCH-ALL: the 53 rows (the whole "rubber"
                   vocabulary) share, further down the chain, the same
                   object -> PAGE 146 ("pick a device"). There's no IR to
                   reassign here: intentional, not a gap.
    [3][4]  54-56  variants of the same catch-all for other menu screens.
    [5][6]   1-2    loose hooks, no "rubber" codes.
    [7]     39     Activity **TV HD**: pressing Vol+/Vol-/Ch+/Ch-/transport/
                   numbers under this Activity emits a REAL IR command --
                   `{cmd_id,0x7D},{dev_id,0x7C}` DIRECT, no intermediate
                   level (measured: **34** of 39 rows resolve to a
                   command; the other 5 are the 0x01/0x02/0x05 hooks,
                   strip 0xA6, and 0xB7).
    [8]     41 rows, 36 editable -- Activity **PC**: same mechanism,
                   different dev_id (Sony TV instead of DVR).
    [9]      5     "All Off": ONLY hooks + one strip; no "rubber" code
                   lives here (physically not needed: All Off turns off).

**In other words:** the mechanism by which Volume/Channel/transport/numbers
fire a specific IR command IS in the data, and it is **exactly the same
primitive** `{cmd_id,0x7D},{dev_id,0x7C}` any screen button already uses --
just indexed by (active Activity, key code) instead of by (screen, zone).
The task's hypothesis ("candidate: role of the active device, no table")
gets **half-refuted**: there IS a table (`[10]`'s), but it's indexed by
Activity, not by "role". This isn't a verdict about ALL codes: on the
global keymap `[1]` the same codes carry NO command (they're UI), and on
the catch-all `[2..4]` neither (they're navigation). Editable = **only**
while an Activity (7 or 8) is active. Verified by running, not assumed --
see `--mapear`.

## The TWO shapes of the pointed-to object, and why it matters

    direct:      tabla[11][id] = <02><{cmd_id,0x7D}><{dev_id,0x7C}>  (nothing else)
    indirect:    tabla[11][id] = <object A> with ONE 0x7F slot -> object B
                 (shape B), and possibly OTHER sibling slots -- the most
                 important, `{page,0x7E}` (36 `table[6]` keys use it, per
                 `keys_map.py`; measured in `[10][7]`/`[10][8]` that
                 NO row with a command carries it, but the code doesn't
                 assume that: if it ever shows up, it gets PRESERVED byte
                 for byte, same as `keys_map._clone_to`).

The 34+36 = 70 ROWS with a real command in `[7]`/`[8]` (which are **36
distinct codes**: the two contexts share 34) are **direct shape**: there's
no object A to clone, the row gets repointed straight to a new object B's
`id`. If some other context ever brought an indirect shape, this module
resolves that too (clones A preserving its siblings, same criterion as
`keys_map._clone_to`) -- **never the "other" one**: if the shape isn't
one of the two recognized ones, it ABORTS with the reason instead of
guessing.

## Where the byte to overwrite lives, and why relocation isn't needed

Each context's record **does not live inside the section `[10]` that
`relocate.py` moves** (that section is the 10-pointer header + the object
STORE `table[11]` indexes): it lives at a FIXED address, outside the
`sec[10]` range, that never was part of any relocation in this project
(measured: context `[1]` falls at `0x0013aa`, well below `sec[10][0]`).
That's why the field is overwritten **in place** (`repuntar_campo`, 2
bytes, the same criterion `edit_activity.py` already uses for a slot's
`prog`/`keyreg` field) -- only the new-object STORE (`{cmd,dev}`) needs to
grow, and for that `reubicar.relocate(..., {10: ...},
objetos_extra=...)` IS needed, exactly like `teclas_mapa.aplicar()`
already does.

## Checks (run on their own, write nothing to the device)

  (a) `reubicar.chain()` identical before/after (this module never
      touches `[9]`, has to be EXACT);
  (b) each new `(k1,k2)` resolves through `cmd_setup_ir`'s arithmetic
      (`device.resolve_section5`);
  (c) `grabar.nothing_moved`, WITH and WITHOUT the declared repoints
      (positive and negative), and also with a byte corrupted on purpose
      (has to still say NO);
  (d) `configcheck.revisar()` all green;
  (e) `reubicar.reachable_pages`/`page_references` identical
      (navigation didn't move, even though this module never touches it);
  (f) ROUND TRIP: applying the change and then the inverse change (back
      to the original `(k1,k2)`) gives the SAME physical-keys table
      `mapear()` reads as the starting blob -- not necessarily the same
      file byte for byte (each step adds objects to the tail, same
      criterion as the whole project), but the SAME logical model.

Writes nothing to the device. Does not import `grabar.cargar()` or any
`erase_*`/`write_firmware_*`. Does not modify `account_export/`.

Usage:
    python3 keys_physical.py blob.bin --mapear
    python3 keys_physical.py blob.bin --mapear --out mapa_fisicas.json
    python3 keys_physical.py blob.bin --asignar 7,0x88,4,0 \\
        --salida output/tvhd_tecla_1_al_LG.bin   # 0x88 = Number1
    python3 keys_physical.py blob.bin --asignar 7,0x88,4,0 --ida-y-vuelta

NOTE ON NAMING: `mapear`, `apply_physical`, and `checks` keep their
exact Spanish names and dict-key shapes -- `keys_photo.py` (`import
teclas_fisicas as TF`) calls `TF.mapear`, and `app/api.py` imports this
module directly and calls `getattr(keys_physical, "apply_physical")(...)`
and `keys_physical.checks(...)` as live Python (same pattern as
`keys_map.py`, see that module's docstring). Every dict key in this
file's data model was left in Spanish accordingly. `TM._dev_id_de` and
`TM._rehacer_checksum` are `keys_map.py`'s contracted names, reused
here unchanged.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import command_records
import configcheck
import add_device as D
import write
import relocate
import keys_reach as TA
import keys_map as TM

BASE = D.BASE

#: contexts [7]/[8] are factory Activities (TV HD / PC); [9] is "All Off"
#: (no "rubber" codes measured). The rest (0..6) are menu/standby
#: screens: their "rubber" codes exist but resolve to UI/paging, not IR
#: -- they're read all the same (so nothing is hidden) but `mapear()`
#: flags them not editable with the measured reason, never silently skipped.
CONTEXTOS_ACTIVITY = (7, 8)

#: The STRUCTURAL fact (what EACH context IS), which doesn't depend on the
#: config: it's a shape description, not a name. Activity NAMES are READ
#: from the blob (`_activity_names`) -- hardcoding "TV HD"/"PC"
#: would make the tool print those labels on ANY config, silently lying
#: on a remote that wasn't this one.
FORMA_CONTEXTO = {
    0: "universal softkeys (EXIT/paging)",
    1: "global keymap (menu/standby)",
    2: "standby catch-all (-> page 146)",
    3: "menu catch-all (variant)",
    4: "menu catch-all (variant)",
    5: "loose hook",
    6: "loose hook",
}


def _activity_names(b: bytes) -> dict[int, str]:
    """`{ordinal: name}` read from THIS blob's own activities menu. If it
    doesn't decode, returns empty and the caller labels it "context N"."""
    try:
        import activities as A
        import delete_device

        dest11 = relocate.table(b, relocate.sections(b)[11][0])
        return {
            int(k): v
            for k, v in (
                A.activity_names(b, delete_device._decodificador(b), dest11) or {}
            ).items()
            if v
        }
    except Exception:  # noqa: BLE001 -- without a name, none gets invented
        return {}


TAG_CMD, TAG_DEV, TAG_PAG, TAG_OBJ = 0x7D, 0x7C, 0x7E, 0x7F


# ============================================================== reading ===


def _header10(b: bytes) -> tuple[int, int]:
    """`(base10, n_contexts)`: the offset of the 10-pointer table and its
    count -- section `[10]`'s master-index FIRST byte."""
    sec = relocate.sections(b)
    base10 = sec[10][0]
    return base10, b[base10]


def _context_rows(b: bytes, base10: int, contexto: int):
    """Rows `(k, code, abs_id_field, id, class)` of context `contexto`,
    in either of the two shapes (`device.read_header`), but with
    the ABSOLUTE offset of the `id` field needed to repoint -- something
    `read_header`/`edit_activity.leer_contexto10` don't return."""
    off = D.u24(b, base10 + 1 + 3 * contexto) - BASE
    if not 0 <= off < len(b) - 2:
        raise ValueError("context[%d]: pointer out of range" % contexto)
    rows = []
    if b[off] == 0:
        n = b[off + 1]
        base = off + 2
        if base + 5 * n > len(b):
            raise ValueError("context[%d]: long form truncated" % contexto)
        for k in range(n):
            p = base + 5 * k
            rows.append((k, b[p + 1], p + 2, D.u16(b, p + 2), b[p + 4]))
    else:
        n = b[off]
        if off + 1 + 4 * n > len(b):
            raise ValueError("context[%d]: short form truncated" % contexto)
        for k in range(n):
            p = off + 1 + 4 * k
            rows.append((k, b[p], p + 1, D.u16(b, p + 1), b[p + 3]))
    return rows


def _forma(b: bytes, dest11: list[int], idx: int):
    """Classifies object `table[11][idx]`: `('directo'|'indirecto'|'otro'|
    'vacio', cmd, dev, pagina, top_object_slots)`.

    directo:    {cmd_id,0x7D} and/or {dev_id,0x7C} on the TOP object,
                nothing else (plus, optionally, {pagina,0x7E} -- preserved).
    indirecto:  EXACTLY one 0x7F slot toward an object that DOES carry
                {cmd_id,0x7D},{dev_id,0x7C} (same criterion as
                `keys_map._clone_to`: if there's more than one 0x7F
                there's no way to know which to repoint, flagged 'otro').
    """
    rs = relocate._slots(b, dest11, idx)
    if not rs:
        return "vacio", None, None, None, rs
    tags = [t for _v, t in rs]
    cmd = next((v for v, t in rs if t == TAG_CMD), None)
    dev = next((v for v, t in rs if t == TAG_DEV), None)
    pag = next((v for v, t in rs if t == TAG_PAG), None)
    resto = [t for t in tags if t not in (TAG_CMD, TAG_DEV, TAG_PAG)]
    if (cmd is not None or dev is not None) and not resto:
        return "directo", cmd, dev, pag, rs
    if tags.count(TAG_OBJ) == 1:
        v7f = next(v for v, t in rs if t == TAG_OBJ)
        rs2 = relocate._slots(b, dest11, v7f)
        cmd2 = next((v for v, t in rs2 if t == TAG_CMD), None)
        dev2 = next((v for v, t in rs2 if t == TAG_DEV), None)
        if cmd2 is not None and dev2 is not None:
            return "indirecto", cmd2, dev2, pag, rs
    return "otro", cmd, dev, pag, rs


def mapear(b: bytes) -> dict:
    """The full map of `[10]`'s 10 contexts, row by row."""
    base10, n_ctx = _header10(b)
    sec = relocate.sections(b)
    dest11 = relocate.table(b, sec[11][0])
    devs5 = D.read_section5(b)

    nombres_act = _activity_names(b)
    contextos = []
    n_editable = 0
    for contexto in range(n_ctx):
        out_rows = []
        for k, codigo, campo, idv, category in _context_rows(b, base10, contexto):
            row = {
                "k": k,
                "codigo": "0x%02X" % codigo,
                "campo": "0x%06X" % campo,
                "category": "0x%02X" % category,
            }
            if category != TAG_OBJ:
                row["editable"] = False
                row["reason"] = (
                    "class 0x%02X (not 0x7F): not a jump to a command" % category
                )
            elif not 0 <= idv < len(dest11):
                row["editable"] = False
                row["reason"] = "id %d outside tabla[11] (%d entries)" % (
                    idv,
                    len(dest11),
                )
            else:
                forma, cmd, dev, pag, _rs = _forma(b, dest11, idv)
                row["forma"] = forma
                if cmd is not None:
                    row["cmd_id"] = cmd
                    row["k1"] = cmd >> 8
                    row["k2"] = cmd & 0xFF
                if dev is not None:
                    row["dev_id"] = "0x%04X" % dev
                if pag is not None:
                    row["target_page"] = pag
                editable = (
                    forma in ("directo", "indirecto")
                    and cmd is not None
                    and dev is not None
                )
                row["editable"] = editable
                if editable:
                    n_editable += 1
                elif forma in ("directo", "indirecto"):
                    row["reason"] = (
                        "resolves but without a complete (cmd,dev): %s" % row
                    )
                else:
                    row["reason"] = (
                        "shape '%s': not a direct {cmd,dev} nor a single "
                        "0x7F jump to {cmd,dev} -- not reassigned by this "
                        "module, so as not to guess the shape" % forma
                    )
            out_rows.append(row)
        contextos.append(
            {
                "contexto": contexto,
                "name": FORMA_CONTEXTO.get(contexto)
                or (
                    "Activity: %s" % nombres_act[contexto]
                    if contexto in nombres_act
                    else "context %d (no readable name in the blob)" % contexto
                ),
                "n_rows": len(out_rows),
                "n_editables": sum(1 for f in out_rows if f["editable"]),
                "rows": out_rows,
            }
        )

    return {
        "base10": "0x%06X" % base10,
        "n_contextos": n_ctx,
        "contextos": contextos,
        "n_rows_total": sum(c["n_rows"] for c in contextos),
        "n_editables_totales": n_editable,
        "devices": [
            {"k1": i, "commands": d.get("n", 0)} for i, d in enumerate(devs5)
        ],
    }


# ============================================================== writing ===


def apply_physical(
    b: bytes, changes: list[dict]
) -> tuple[bytes, list[int], list[dict]]:
    """Reassigns N physical keys at once. `changes`: `[{contexto, codigo,
    k1, k2, dev_id?}, ...]`. Returns `(new_blob, repoints, detail)`.

    Raises `ValueError` (writing nothing) if a change doesn't add up: code
    not declared in that context, class != 0x7F, unrecognized shape, or
    `(k1,k2)` not reachable through section [5].
    """
    if not changes:
        raise ValueError("there is no change to apply")
    base10, n_ctx = _header10(b)
    sec = relocate.sections(b)
    a10, z10 = sec[10]
    dest11 = relocate.table(b, sec[11][0])

    s10 = bytearray(b[a10:z10])
    extra: list[int] = []
    parches: list[tuple[int, int]] = []  # (absolute field, new id_a)
    detail: list[dict] = []
    vistos: set[tuple[int, int]] = set()

    for c in changes:
        contexto = int(c["contexto"])
        codigo = int(c["codigo"])
        k1 = int(c["k1"])
        k2 = int(c["k2"])
        clave = (contexto, codigo)
        if clave in vistos:
            raise ValueError(
                "key (context %d, code %#04x) appears twice in the same batch" % clave
            )
        vistos.add(clave)

        if not 0 <= contexto < n_ctx:
            raise ValueError(
                "context %d doesn't exist (there are %d)" % (contexto, n_ctx)
            )

        row = next(
            (f for f in _context_rows(b, base10, contexto) if f[1] == codigo), None
        )
        if row is None:
            declarados = ["%#04x" % f[1] for f in _context_rows(b, base10, contexto)]
            raise ValueError(
                "context %d doesn't declare key %#04x (it declares %s)"
                % (contexto, codigo, ", ".join(declarados))
            )
        _k, _cod, campo, idv, category = row
        if category != TAG_OBJ:
            raise ValueError(
                "context %d key %#04x: class %#04x is not 0x7F, doesn't "
                "jump to a command object -- can't be reassigned this way"
                % (contexto, codigo, category)
            )
        if not 0 <= idv < len(dest11):
            raise ValueError(
                "context %d key %#04x: id %d outside tabla[11]"
                % (contexto, codigo, idv)
            )
        forma, _cmd, _dev, pag, rs = _forma(b, dest11, idv)
        if forma not in ("directo", "indirecto"):
            raise ValueError(
                "context %d key %#04x: shape '%s' not recognized -- "
                "aborting instead of guessing" % (contexto, codigo, forma)
            )

        cmd_id = (k1 << 8) | k2
        reg, reason = D.resolve_section5(b, cmd_id)
        if reg is None:
            raise ValueError(
                "(device %d, command %d) -> %#06x is NOT reachable "
                "through section [5]: %s -- grabbing this would hang the remote"
                % (k1, k2, cmd_id, reason)
            )
        dev_id = c.get("dev_id")
        dev_id = int(dev_id) if dev_id is not None else TM._dev_id_de(b, dest11, k1)

        id_base = len(dest11) + len(extra)
        if forma == "directo":
            # the new object is {cmd,0x7D},{dev,0x7C} -- PLUS, if the old
            # object carried a sibling {page,0x7E}, it's preserved:
            # silently losing it is exactly the bug this task asked not
            # to repeat (36 tabla[6] keys combine a command and a
            # transition; none was measured here, but the code doesn't
            # assume that).
            cuerpo = command_records.command_object(cmd_id, dev_id)
            if pag is not None:
                cuerpo = bytes([3]) + cuerpo[1:] + relocate.slot(pag, TAG_PAG)
            extra.append(len(s10))
            s10 += cuerpo
            id_a = id_base
        else:  # indirecto: clone the TOP object preserving its siblings
            extra.append(len(s10))
            s10 += command_records.command_object(cmd_id, dev_id)
            id_b = id_base
            nuevas = [(id_b if t == TAG_OBJ else v, t) for v, t in rs]
            extra.append(len(s10))
            s10 += bytes([len(nuevas)]) + b"".join(
                relocate.slot(v, t) for v, t in nuevas
            )
            id_a = id_base + 1

        parches.append((campo, id_a))
        detail.append(
            {
                "contexto": contexto,
                "codigo": codigo,
                "campo": campo,
                "forma": forma,
                "old_object": idv,
                "new_object": id_a,
                "cmd_id": cmd_id,
                "dev_id": dev_id,
                "k1": k1,
                "k2": k2,
                "page_preserved": pag,
            }
        )

    out = bytearray(relocate.relocate(b, {10: bytes(s10)}, objetos_extra=extra))
    repuntes: list[int] = []
    for campo, id_a in parches:
        out[campo : campo + 2] = id_a.to_bytes(2, "little")
        repuntes.append(campo)
    TM._rehacer_checksum(out)
    return bytes(out), sorted(repuntes), detail


# ============================== THE SECOND SITE: the device's own page ====
#
# ## What grabada #7 taught, and why the context alone is not enough
#
# #7 reassigned the four d-pad keys to the LG in context `[10][8]`
# ("Activity: PC"). Everything above is what wrote it, the write is CORRECT
# (the row the master index reaches carries the LG's command and its IR
# resolves), and the key still did nothing -- because a `[10]` context is
# only consulted **while that Activity is running**, and only on screens
# that do not declare the code first. Out of an Activity, on the page of the
# device the user had just added, the press never gets there.
#
# The place the FACTORY binds a device's rubber keys is not the context: it
# is the DEVICE PAGE's own key register, the `hdr` of its `table[6]` trailer.
# Measured on the live blob (`000007.bin`, 159 screens):
#
#     screen  hdr        rows  rubber  commands  device
#     78      0x0119BF   49    38      33        all k1=1  (DVR)
#     103     0x012821   49    38      34        all k1=0  (Sony TV)
#     140     0x013FAB   49    38      25        all k1=2  (Home)
#     156/157/158  ...    4     0       0        the devices THIS project added
#
# Three screens, one per factory device, and every command each one resolves
# to belongs to that single device -- a perfect k1 partition, which no
# accident reproduces three times. The three screens this project added
# declare four codes (`06` enter, `07` leave, `b7`, `2d`) and not one rubber
# key: that is the hole, and it is why "Devices -> LG -> d-pad" does nothing
# no matter what the context says.
#
# So this site writes THERE. Two cases, and the cheap one is the common one:
#
#   * the code is already a row in the header (the three factory pages, and
#     any page grown before): only the row's `{id,category}` is overwritten, 3
#     bytes, nothing moves -- the same primitive `apply_physical` uses;
#   * the code is not a row (the pages this project adds): the header is
#     REBUILT with the row appended and the trailer's `hdr` pointer is
#     repointed at it (3 bytes). The rebuild keeps the existing rows byte for
#     byte -- the enter/leave hooks are what light the LEDs and pay the exit.
#
# The row is appended at the END: the factory's own headers prove the order
# is not load-bearing (`table[6][0]` and `[9]` declare the same codes in a
# different order, and `0x02E2F2` matches by code alone), and appending is
# the only variant that leaves every existing row where it was.

#: the 8 touch codes are NOT bound here: each slot has its own key register
#: (`keys_map.py`), one per LCD page, which is a different mechanism.
CODIGOS_TACTILES = tuple(TM.SCREEN_CODES)

#: the two side strips are the PAGER. A header that declares them takes the
#: event as handled and the global register `[10][1]` -- the one that pages
#: (`0x0257B4 CALL 0x0284BC`) -- never runs: that is measured, it is one of
#: the three causes closed in `ESTADO.md` ("paging didn't work"), and the
#: factory's own 49-row device template does NOT declare them. Binding a
#: command here would silently kill paging on a page that has 3, 6, 10 or 11
#: of them, so this site refuses.
CODIGOS_FRANJA = tuple(D.CODIGOS_FRANJA)

#: THE THREE CODES THAT ARE NOT KEYS. They are in the 55-key inventory --
#: which is why they used to sail straight through the "is it in the
#: inventory" filter -- but no rubber key sends them: they are the page's own
#: infrastructure, and the factory's 49-row device template carries all three
#: with their factory objects untouched.
#:
#:   `06` enter hook  -> obj with four 0x7F slots down to `{0xC0nm,0x3F}`
#:                       atoms: it is what LIGHTS the PCA9532 LEDs on entry.
#:   `07` leave hook  -> the same channels back to 0: unlights them.
#:   `2D` pager       -> `{0,0x73}{3,0x73}{2,0x73}`, the walk through the
#:                       page's N sub-screens (`0x0284BC`, wrap mod N). It is
#:                       to the INSIDE of a page what `AE`/`AF` are to the
#:                       outside, and binding it kills paging just as dead.
#:
#: Binding any of the three silently trades a working page for a command.
CODIGOS_INFRAESTRUCTURA = (0x06, 0x07, 0x2D)

#: `table[6][0]`'s header IS the remote's key inventory: the 55 codes this
#: hardware can emit. Fixed low address (`teclas_foto.modelo` reads the same
#: one), never part of any relocated section.
OFF_INVENTARIO = 0x67


def inventario(b: bytes) -> list[int]:
    """The 55 key codes the remote can emit, read from the blob."""
    return [b[OFF_INVENTARIO + 1 + 4 * i] for i in range(b[OFF_INVENTARIO])]


def _sobrevive(b: bytes, off: int, largo: int = 3) -> bool:
    """Whether a byte patched in place at `off` is still there afterwards.

    `relocate()` copies `b[:close-2]` and appends the new bodies of [9],
    [10] and [11] at the end: it never writes below the close, so ANY offset
    in that prefix survives. That includes the old bodies of the relocated
    sections -- they stay exactly where they were, they just stop being what
    the master index points at. Which of the two copies the firmware ends up
    reading is not a question of ranges, it is a question of who points at
    what -- and that is measured, per change, by `teclas_alcance.checks`
    (h)/(i): the device pages this project adds have their trailer inside the
    old `[11]` region and `table[6]` keeps pointing right at it, so patching
    it in place is patching what the remote reads.
    """
    close = int.from_bytes(b[4:7], "little") - relocate.BASE
    return 0 <= off and off + largo <= close - 2


def _screen_header(b: bytes, ordinal: int):
    """`(trailer, rows)` of a screen's own key register, or raises."""
    n = D.u16(b, D.T6)
    if not 0 <= ordinal < n:
        raise ValueError("screen %d doesn't exist (there are %d)" % (ordinal, n))
    tr = D.read_trailer(b, D.u24(b, D.T6 + 3 + 3 * ordinal) - BASE, max_n=200)
    if tr is None:
        raise ValueError("screen %d's trailer doesn't parse" % ordinal)
    return tr, TA.register_rows(b, tr["hdr"] - BASE)


def map_devices(b: bytes, hub=None) -> list[dict]:
    """Per device: its own page, and what each physical key does THERE.

    This is what the "Keys" screen needs in order to stop offering a site
    that cannot work: a key with `editable=False` here is one this module
    refuses to write, with the measured reason attached.
    """
    TM.set_t6(b)
    sec = relocate.sections(b)
    dest11 = relocate.table(b, sec[11][0])
    inv = [
        c
        for c in inventario(b)
        if c not in CODIGOS_TACTILES and c not in CODIGOS_FRANJA
    ]
    outside: list[dict] = []
    for k1, info in sorted(TA.device_screen(b, hub).items()):
        ordinal = info["screen"]
        try:
            tr, rows = _screen_header(b, ordinal)
        except ValueError as exc:
            outside.append(
                {
                    "k1": k1,
                    "name": info["name"],
                    "screen": ordinal,
                    "error": str(exc),
                    "codigos": {},
                }
            )
            continue
        # growing the header means repointing the trailer's `hdr` IN PLACE
        puede_crecer = _sobrevive(b, tr["off"] + 1)
        by_code = {f[1]: f for f in rows}
        codigos: dict[str, dict] = {}
        for cod in inv:
            f = by_code.get(cod)
            d: dict = {"codigo": cod}
            if cod in CODIGOS_INFRAESTRUCTURA:
                # listed, so the count of what a page declares stays honest,
                # but never offered: see CODIGOS_INFRAESTRUCTURA.
                d.update(
                    {
                        "state": "infraestructura",
                        "editable": False,
                        "reason": "code %#04x is the page's own %s, not a key: "
                        "binding it costs the LEDs or the internal pager"
                        % (
                            cod,
                            "internal pager" if cod == 0x2D else "enter/leave hook",
                        ),
                    }
                )
                codigos["0x%02X" % cod] = d
                continue
            if f is None:
                d.update(
                    {
                        "state": "sin fila",
                        "editable": bool(puede_crecer),
                        "reason": None
                        if puede_crecer
                        else "this page's trailer sits past the file's close: "
                        "repointing it would not survive the rebuild",
                    }
                )
            else:
                _k, _c, campo, idv, category = f
                d.update({"campo": campo, "objeto": idv, "category": "0x%02X" % category})
                if category == 0 and idv == 0:
                    d.update({"state": "declarada y apagada", "editable": True})
                elif category != TAG_OBJ:
                    d.update(
                        {
                            "state": "otra clase",
                            "editable": False,
                            "reason": "class %#04x is not 0x7F: the row doesn't "
                            "jump to a command object" % category,
                        }
                    )
                elif not 0 <= idv < len(dest11):
                    d.update(
                        {
                            "state": "id fuera de tabla",
                            "editable": False,
                            "reason": "id %d outside tabla[11]" % idv,
                        }
                    )
                else:
                    forma, cmd, dev, _pag, _rs = _forma(b, dest11, idv)
                    d["forma"] = forma
                    if cmd is not None:
                        d.update({"cmd_id": cmd, "k1_hoy": cmd >> 8, "k2": cmd & 0xFF})
                    if dev is not None:
                        d["dev_id"] = "0x%04X" % dev
                    if forma in ("directo", "indirecto"):
                        d.update({"state": "asignada", "editable": True})
                    else:
                        d.update(
                            {
                                "state": "forma no reconocida",
                                "editable": False,
                                "reason": "shape '%s': not a direct {cmd,dev} nor a "
                                "single 0x7F jump -- not written, so as not to "
                                "guess" % forma,
                            }
                        )
            codigos["0x%02X" % cod] = d
        outside.append(
            {
                "k1": k1,
                "name": info["name"],
                "screen": ordinal,
                "header": "0x%06X" % (tr["hdr"] - BASE),
                "trailer": "0x%06X" % tr["off"],
                "pages": tr["N"],
                "n_rows": len(rows),
                "puede_crecer": bool(puede_crecer),
                "n_editables": sum(1 for d in codigos.values() if d["editable"]),
                "codigos": codigos,
            }
        )
    return outside


def apply_device(
    b: bytes, changes: list[dict]
) -> tuple[bytes, list[int], list[dict]]:
    """Binds N physical keys ON A DEVICE'S OWN PAGE.

    `changes`: `[{screen, codigo, k1, k2, dev_id?}, ...]`. Returns
    `(new blob, repoints, detail)`. Raises `ValueError`, having built
    nothing, if a change doesn't add up.

    A change may instead be `{screen, codigo, apagar: True}`, which writes
    the factory's DISABLED row (`<cod> 00 00 00`) rather than a binding. It
    is not the same as leaving the code out, and the difference is measured:
    `0x02E2F2` matches by CODE ALONE and takes the event as handled, so a
    declared-disabled key does nothing on that page, while an ABSENT one
    falls through to the global keymap `[10][2]` -- which jumps to page 146,
    "pick a device". The factory disables 5 / 4 / 13 rubber keys and all 8
    touch codes on its three device pages; `teclas_auto` reproduces that.
    """
    if not changes:
        raise ValueError("there is no change to apply")
    TM.set_t6(b)
    sec = relocate.sections(b)
    a10, z10 = sec[10]
    dest11 = relocate.table(b, sec[11][0])
    inv = set(inventario(b))

    s10 = bytearray(b[a10:z10])
    extra: list[int] = []
    detail: list[dict] = []
    vistos: set[tuple[int, int]] = set()
    # per screen: {"tr", "filas", "parches": {codigo: (id, clase)}, "nuevas": [...]}
    plan: dict[int, dict] = {}

    for c in changes:
        ordinal = int(c["screen"])
        codigo = int(c["codigo"])
        apagar = bool(c.get("apagar"))
        k1 = None if apagar else int(c["k1"])
        k2 = None if apagar else int(c["k2"])
        clave = (ordinal, codigo)
        if clave in vistos:
            raise ValueError(
                "key (screen %d, code %#04x) appears twice in the same batch" % clave
            )
        vistos.add(clave)
        if codigo in CODIGOS_INFRAESTRUCTURA:
            raise ValueError(
                "code %#04x is the page's own %s, not a key (see "
                "CODIGOS_INFRAESTRUCTURA): touching it costs the LEDs or the "
                "internal pager -- not written"
                % (codigo, "internal pager" if codigo == 0x2D else "enter/leave hook")
            )
        if apagar and codigo in CODIGOS_TACTILES:
            pass  # the factory declares all 8 of them disabled: allowed
        elif codigo in CODIGOS_TACTILES:
            raise ValueError(
                "code %#04x is a touchscreen zone: it is bound per LCD page in "
                "the slot's own key register, not in the page's header "
                "(teclas_mapa.aplicar)" % codigo
            )
        if codigo in CODIGOS_FRANJA:
            raise ValueError(
                "code %#04x is a side strip: it is what pages. A header that "
                "declares it consumes the event and the page stops paging "
                "(ESTADO.md, control 156/156) -- not written" % codigo
            )
        if codigo not in inv:
            raise ValueError(
                "code %#04x is not in the remote's %d-key inventory: this "
                "hardware never emits it" % (codigo, len(inv))
            )

        if ordinal not in plan:
            tr, rows = _screen_header(b, ordinal)
            plan[ordinal] = {"tr": tr, "rows": rows, "parches": {}, "nuevas": []}
        p = plan[ordinal]
        row = next((f for f in p["rows"] if f[1] == codigo), None)

        if apagar:
            cmd_id = dev_id = None
        else:
            cmd_id = (k1 << 8) | k2
            reg, reason = D.resolve_section5(b, cmd_id)
            if reg is None:
                raise ValueError(
                    "(device %d, command %d) -> %#06x is NOT reachable through "
                    "section [5]: %s -- grabbing this would hang the remote"
                    % (k1, k2, cmd_id, reason)
                )
            dev_id = c.get("dev_id")
            dev_id = int(dev_id) if dev_id is not None else TM._dev_id_de(b, dest11, k1)

        id_base = len(dest11) + len(extra)
        forma = "nueva"
        old = None
        if row is not None:
            _k, _c, campo, idv, category = row
            old = idv
            if category == TAG_OBJ:
                forma, _cmd, _dev, pag, rs = _forma(b, dest11, idv)
                if forma not in ("directo", "indirecto"):
                    raise ValueError(
                        "screen %d key %#04x: shape '%s' not recognized -- "
                        "aborting instead of guessing" % (ordinal, codigo, forma)
                    )
            elif category == 0 and idv == 0:
                forma, pag, rs = "apagada", None, []
            else:
                raise ValueError(
                    "screen %d key %#04x: class %#04x is neither 0x7F nor the "
                    "factory's disabled row -- not written" % (ordinal, codigo, category)
                )
        else:
            if not _sobrevive(b, p["tr"]["off"] + 1):
                raise ValueError(
                    "screen %d doesn't declare %#04x and its trailer sits past "
                    "the file's close: the repoint would not survive the "
                    "rebuild" % (ordinal, codigo)
                )
            pag, rs = None, []

        if apagar:
            # No object is created and none is repointed: the row IS the
            # answer (`<cod> 00 00 00`). If the row is already disabled this
            # writes the same three bytes back, which is a no-op the checks
            # still see (it stays in `detail`, so (f)/(g) account for it).
            id_a, kind_a, forma = 0, 0x00, "apagar"
        elif forma == "indirecto":
            extra.append(len(s10))
            s10 += command_records.command_object(cmd_id, dev_id)
            id_b = id_base
            nuevas = [(id_b if t == TAG_OBJ else v, t) for v, t in rs]
            extra.append(len(s10))
            s10 += bytes([len(nuevas)]) + b"".join(
                relocate.slot(v, t) for v, t in nuevas
            )
            id_a, kind_a = id_base + 1, TAG_OBJ
        else:
            cuerpo = command_records.command_object(cmd_id, dev_id)
            if pag is not None:
                cuerpo = bytes([3]) + cuerpo[1:] + relocate.slot(pag, TAG_PAG)
            extra.append(len(s10))
            s10 += cuerpo
            id_a, kind_a = id_base, TAG_OBJ

        if row is None:
            p["nuevas"].append((codigo, id_a, kind_a))
        else:
            p["parches"][codigo] = (id_a, kind_a)
        detail.append(
            {
                "screen": ordinal,
                "codigo": codigo,
                "forma": forma,
                "apagar": apagar,
                "new_row": row is None,
                "campo": None if row is None else row[2],
                "old_object": old,
                "new_object": id_a,
                "cmd_id": cmd_id,
                "dev_id": dev_id,
                "k1": k1,
                "k2": k2,
                "page_preserved": pag,
            }
        )

    # --- the headers that have to GROW get rebuilt and placed in [10] -----
    #
    # They go inside section [10]'s body for the same reason the new objects
    # do: it is the only body this project grows, `relocate()` puts it at the
    # end, and its new base is read back out of the master index afterwards.
    #
    # TWO PASSES, and the reason is that the first one is the only way to know
    # where the body LANDS. Keeping a rebuilt header from straddling a 64 KiB
    # page is a PRECAUTION, not a measured requirement: the only page
    # constraint measured in this blob is section [5]'s (`cfg_index_x3`), and
    # of the 396 key registers the factory ships not one crosses a page --
    # which at their size is not evidence either way. Padding costs bytes in a
    # file that has 1.9 MB of room; a wrong guess about a hardware quirk costs
    # a remote. Note the padding cannot be computed against `len(s10)`: what
    # matters is the ABSOLUTE address, and [10]'s base does not depend on how
    # long its body is (it is placed after [9], which this site never touches)
    # -- which is why the second pass sees the same base and settles.
    base10 = None
    crecen: dict[int, int] = {}
    out = bytearray()
    for _pasada in range(3):
        cuerpo10 = bytearray(s10)
        crecen = {}
        for ordinal, p in plan.items():
            if not p["nuevas"]:
                continue
            rows = [
                (cod, *p["parches"].get(cod, (idv, category)))
                for _k, cod, _campo, idv, category in p["rows"]
            ] + list(p["nuevas"])
            cuerpo = D.build_raw_register(rows)
            if base10 is not None:
                inicio = base10 + len(cuerpo10)
                if (inicio >> 16) != ((inicio + len(cuerpo) - 1) >> 16):
                    cuerpo10 += b"\x00" * (-inicio % 0x10000)
            crecen[ordinal] = len(cuerpo10)
            cuerpo10 += cuerpo
        out = bytearray(
            relocate.relocate(b, {10: bytes(cuerpo10)}, objetos_extra=extra)
        )
        new10 = relocate.sections(bytes(out))[10][0]
        if base10 == new10:
            break
        base10 = new10
    new10 = base10
    for ordinal, off in crecen.items():
        inicio = new10 + off
        largo = 1 + 4 * (len(plan[ordinal]["rows"]) + len(plan[ordinal]["nuevas"]))
        if (inicio >> 16) != ((inicio + largo - 1) >> 16):
            raise ValueError(
                "screen %d's rebuilt header would straddle a 64 KiB page at "
                "%#08x (%d B): not written" % (ordinal, inicio, largo)
            )
    repuntes: list[int] = []
    for ordinal, p in plan.items():
        if ordinal in crecen:
            target = new10 + crecen[ordinal] + BASE
            campo = p["tr"]["off"] + 1
            out[campo : campo + 3] = target.to_bytes(3, "little")
            repuntes.append(campo)
            for d in detail:
                if d["screen"] == ordinal:
                    d["new_header"] = new10 + crecen[ordinal]
            continue
        for _k, cod, campo, _idv, _kind in p["rows"]:
            if cod in p["parches"]:
                idv, category = p["parches"][cod]
                out[campo : campo + 2] = idv.to_bytes(2, "little")
                out[campo + 2] = category
                repuntes.append(campo)
    TM._rehacer_checksum(out)
    return bytes(out), sorted(set(repuntes)), detail


def _cabeceras_planas(b: bytes) -> dict:
    """`{(screen, code): (id, class)}` for EVERY screen's key register.

    The collateral check: a device page's header is rebuilt whole, so what
    has to be proven is that no OTHER row -- and no other screen -- moved.
    """
    TM.set_t6(b)
    n = D.u16(b, D.T6)
    outside = {}
    for k in range(n):
        tr = D.read_trailer(b, D.u24(b, D.T6 + 3 + 3 * k) - BASE, max_n=200)
        if tr is None:
            outside[(k, "trailer")] = None
            continue
        try:
            rows = TA.register_rows(b, tr["hdr"] - BASE)
        except ValueError:
            outside[(k, "header")] = None
            continue
        for _j, cod, _campo, idv, category in rows:
            outside[(k, cod)] = (idv, category)
    return outside


def device_checks(
    b: bytes, out: bytes, detail: list[dict], repuntes: list[int]
) -> list[dict]:
    """The same battery as `checks()`, aimed at the device-page site."""
    ch: list[dict] = []

    ch.append(
        {
            "name": "(a) reubicar.chain() identical before/after",
            "ok": relocate.chain(b) == relocate.chain(out),
            "detail": "this site doesn't touch [9] either: has to be EXACT",
        }
    )

    # a `{apagar}` change binds no command, so there is no (k1,k2) to resolve:
    # it is counted apart instead of being fed a `None` cmd_id.
    con_cmd = [d for d in detail if d.get("cmd_id") is not None]
    malos = [d for d in con_cmd if D.resolve_section5(out, d["cmd_id"])[0] is None]
    ch.append(
        {
            "name": "(b) reachable through section [5]",
            "ok": not malos,
            "detail": "the %d new commands resolve through the firmware's "
            "exact arithmetic (%d more rows are disabled rows, no command)"
            % (len(con_cmd), len(detail) - len(con_cmd))
            if not malos
            else "do NOT resolve: %s" % [hex(d["cmd_id"]) for d in malos],
        }
    )

    rep: set[int] = set()
    for r in repuntes:
        rep |= {r, r + 1, r + 2}
    ok_pos, dif_pos = write.nothing_moved(b, out, rep)
    ok_sin, _ = write.nothing_moved(b, out)
    corrompido = bytearray(out)
    corrompido[0x100] ^= 0xFF
    ok_neg, dif_neg = write.nothing_moved(b, bytes(corrompido), rep)
    ch.append(
        {
            "name": "(c) nothing moved (+/-)",
            "ok": ok_pos and not ok_sin and not ok_neg and 0x100 in dif_neg,
            "detail": "positive with the %d declared repoints: %s (%d different "
            "bytes); WITHOUT declaring them: %s (has to say NO); with a byte "
            "corrupted on purpose: %s"
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
                "%s:%s" % (n, "OK" if o else "FAIL") for n, o, _ in pruebas
            ),
        }
    )

    alc_a, alc_d = relocate.reachable_pages(b), relocate.reachable_pages(out)
    ref_a, ref_d = relocate.page_references(b), relocate.page_references(out)
    ch.append(
        {
            "name": "(e) navigation intact",
            "ok": alc_a == alc_d and ref_a == ref_d,
            "detail": "reachable %d->%d (lost: %s); referenced %d->%d (lost: %s)"
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

    # (f)/(g): the EFFECT, re-read from the new blob by the firmware's own
    # path -- and nothing else moved with it. A rebuilt header is a whole
    # structure written from scratch: (g) is what proves the enter/leave
    # hooks and every untouched row survived it byte for byte.
    esperados = {
        (d["screen"], d["codigo"]): (None if d.get("apagar") else d["cmd_id"])
        for d in detail
    }
    missing = []
    for (ordinal, codigo), cmd in esperados.items():
        r = TA.on_screen(out, ordinal, codigo)
        if cmd is None:
            # a disabled row: what has to hold is that the code IS declared
            # (that is what makes the remote swallow the press instead of
            # dropping it into the global keymap) and that it reaches nothing.
            if not (r.get("declarado") and r.get("category") == 0 and not r.get("cmd_id")):
                missing.append((ordinal, "%#04x" % codigo, "disabled: " + TA._said(r)))
        elif r.get("cmd_id") != cmd:
            missing.append((ordinal, "%#04x" % codigo, TA._said(r)))
    ch.append(
        {
            "name": "(f) the page's key register REACHES the new command",
            "ok": not missing,
            "detail": "the %d rows resolve to the new command when the new blob "
            "is walked from the master index (%d of them declared-and-disabled, "
            "which is what makes the key inert instead of falling through)"
            % (len(esperados), sum(1 for v in esperados.values() if v is None))
            if not missing
            else "did NOT resolve: %s" % missing,
        }
    )

    before, after = _cabeceras_planas(b), _cabeceras_planas(out)
    colaterales = [
        k
        for k in set(before) | set(after)
        if k not in esperados and before.get(k) != after.get(k)
    ]
    ch.append(
        {
            "name": "(g) no OTHER row of any header moved",
            "ok": not colaterales,
            "detail": "the other %d rows across the %d screens' key registers "
            "stay identical (the rebuilt header keeps its enter/leave hooks)"
            % (len(before) - len(esperados), D.u16(out, D.T6))
            if not colaterales
            else "changed without being asked: %s" % colaterales[:12],
        }
    )
    return ch


# ================================================================ checks ==


def checks(
    b: bytes, out: bytes, detail: list[dict], repuntes: list[int]
) -> list[dict]:
    ch: list[dict] = []

    ok_cadena = relocate.chain(b) == relocate.chain(out)
    ch.append(
        {
            "name": "(a) reubicar.chain() identical before/after",
            "ok": ok_cadena,
            "detail": "this module doesn't touch [9]: has to be EXACT",
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
        rep |= {r, r + 1}
    ok_pos, dif_pos = write.nothing_moved(b, out, rep)
    ok_sin, _ = write.nothing_moved(b, out)
    corrompido = bytearray(out)
    corrompido[0x100] ^= 0xFF
    ok_neg, dif_neg = write.nothing_moved(b, bytes(corrompido), rep)
    ch.append(
        {
            "name": "(c) nothing moved (+/-)",
            "ok": ok_pos and not ok_sin and not ok_neg and 0x100 in dif_neg,
            "detail": "positive with the %d declared repoints: %s (%d "
            "different bytes); WITHOUT declaring them: %s (has to say "
            "NO); with a byte corrupted on purpose: %s"
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
                "%s:%s" % (n, "OK" if o else "FAIL") for n, o, _ in pruebas
            ),
        }
    )

    alc_a, alc_d = relocate.reachable_pages(b), relocate.reachable_pages(out)
    ref_a, ref_d = (
        relocate.page_references(b),
        relocate.page_references(out),
    )
    # (f) and (g): the two checks that were missing. `nothing_moved` and
    # the chain go green EVEN IF the write were INERT (this project's
    # classic case: writing to the copy the firmware doesn't read). These
    # two look at the EFFECT, re-reading the new blob with the same
    # `mapear()` the UI uses.
    before = _flat_physical_table(b)
    after = _flat_physical_table(out)
    esperados = {(d["contexto"], "0x%02X" % d["codigo"]): d["cmd_id"] for d in detail}
    missing = [k for k, v in esperados.items() if (after.get(k) or (None,))[0] != v]
    ch.append(
        {
            "name": "(f) the change SHOWS UP on re-read (not inert)",
            "ok": not missing,
            "detail": "the %d touched rows return the new cmd_id when "
            "re-read with mapear()" % len(esperados)
            if not missing
            else "did NOT change: %s" % missing,
        }
    )
    colaterales = [
        k
        for k in set(before) | set(after)
        if k not in esperados and before.get(k) != after.get(k)
    ]
    ch.append(
        {
            "name": "(g) no OTHER key moved",
            "ok": not colaterales,
            "detail": "the other %d editable rows across the 10 contexts "
            "stay identical -- tabla[11] objects SHARED between contexts "
            "(0x83/0x84/0x89 are used by TV HD and PC at once) aren't "
            "mutated: a new one is created and ONLY the touched row is repointed"
            % (len(before) - len(esperados))
            if not colaterales
            else "changed without being asked: %s" % colaterales,
        }
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


def _flat_physical_table(b: bytes) -> dict:
    """`{(contexto, codigo): (cmd_id, page)}` of the EDITABLE rows, for
    the round-trip check -- compares the logical model, not the file."""
    m = mapear(b)
    out = {}
    for c in m["contextos"]:
        for f in c["rows"]:
            if f["editable"]:
                out[(c["contexto"], f["codigo"])] = (
                    f.get("cmd_id"),
                    f.get("target_page"),
                )
    return out


def check_round_trip(
    b: bytes, contexto: int, codigo: int, k1: int, k2: int
) -> dict:
    """Reassigns `(contexto,codigo)` to `(k1,k2)` and then back to the
    original value; the logical model (`mapear()`) has to come out
    identical to the starting one."""
    before = _flat_physical_table(b)
    orig = before.get((contexto, "0x%02X" % codigo))
    if orig is None or orig[0] is None:
        raise ValueError(
            "(context %d, code %#04x) isn't editable today: there's no "
            "round trip to test" % (contexto, codigo)
        )
    k1_o, k2_o = orig[0] >> 8, orig[0] & 0xFF

    ida, rep_ida, det_ida = apply_physical(
        b, [{"contexto": contexto, "codigo": codigo, "k1": k1, "k2": k2}]
    )
    ch_ida = checks(b, ida, det_ida, rep_ida)

    vuelta, rep_vta, det_vta = apply_physical(
        ida, [{"contexto": contexto, "codigo": codigo, "k1": k1_o, "k2": k2_o}]
    )
    ch_vta = checks(ida, vuelta, det_vta, rep_vta)

    after = _flat_physical_table(vuelta)
    identico = after == before
    return {
        "original": (k1_o, k2_o),
        "probado": (k1, k2),
        "checks_out": ch_ida,
        "checks_back": ch_vta,
        "modelo_identico_tras_ida_y_vuelta": identico,
        "ok": identico
        and all(c["ok"] for c in ch_ida)
        and all(c["ok"] for c in ch_vta),
    }


# ===================================================================== CLI ==


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument(
        "--mapear", action="store_true", help="prints the map of the 10 contexts"
    )
    ap.add_argument("--out", help="dumps --mapear to JSON")
    ap.add_argument(
        "--asignar",
        action="append",
        default=[],
        metavar="CONTEXTO,COD,K1,K2",
        help="one change; repeatable. Only writes if ALL checks come back OK",
    )
    ap.add_argument("--salida", help="new blob (only if the checks pass)")
    ap.add_argument(
        "--ida-y-vuelta",
        action="store_true",
        help="with ONE --asignar: tests a round trip instead of writing --salida",
    )
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    if b[:4] != b"GSPM":
        print("the blob doesn't start with GSPM", file=sys.stderr)
        return 1

    if a.mapear or not (a.asignar):
        m = mapear(b)
        print(
            "section[10]: %d contexts, %d rows, %d editable (real IR command)"
            % (m["n_contextos"], m["n_rows_total"], m["n_editables_totales"])
        )
        for c in m["contextos"]:
            print(
                "  [%d] %-32s %3d rows, %2d editable"
                % (c["contexto"], c["name"], c["n_rows"], c["n_editables"])
            )
        if a.out:
            pathlib.Path(a.out).write_text(json.dumps(m, indent=1))
            print("map -> %s" % a.out)

    if not a.asignar:
        return 0

    changes = []
    for spec in a.asignar:
        ctx, cod, k1, k2 = (int(x, 0) for x in spec.split(","))
        changes.append({"contexto": ctx, "codigo": cod, "k1": k1, "k2": k2})

    if a.ida_y_vuelta:
        if len(changes) != 1:
            print("--ida-y-vuelta only accepts ONE --asignar", file=sys.stderr)
            return 1
        c = changes[0]
        try:
            r = check_round_trip(b, c["contexto"], c["codigo"], c["k1"], c["k2"])
        except ValueError as e:
            print("CANNOT TEST: %s" % e, file=sys.stderr)
            return 1
        print(
            "round trip: original (k1=%d,k2=%d) -> tested (k1=%d,k2=%d) -> original"
            % (r["original"][0], r["original"][1], r["probado"][0], r["probado"][1])
        )
        for tag, chs in (
            ("out", r["checks_out"]),
            ("back", r["checks_back"]),
        ):
            for c2 in chs:
                print(
                    "  [%s] %-45s %s"
                    % (tag, c2["name"], "OK" if c2["ok"] else "FAIL")
                )
        print(
            "logical model identical to the starting one: %s"
            % ("YES" if r["modelo_identico_tras_ida_y_vuelta"] else "NO")
        )
        print("VERDICT: %s" % ("OK" if r["ok"] else "FAIL"))
        return 0 if r["ok"] else 1

    try:
        out, repuntes, detail = apply_physical(b, changes)
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
