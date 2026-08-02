#!/usr/bin/env python3
"""Reassigns an already-existing key to a different command, without
moving a single byte.

## The mechanism (already known, nothing reinvented here)

A screen's key is an entry in section `[9]`:
`<button u8><u16 object_A><tag u8>`. `object_A` is an id in the global
table `[11]` and its body has the shape

    object A (button):    02 | {tipo, 0x75}   | {object_B, 0x7F}
    object B (command):   02 | {cmd_id, 0x7D} | {dev_id,  0x7C}

-- exactly what `reubicar.chain()` walks to build
`{(page, button): (cmd_id, dev_id)}`.

Reassigning = creating a NEW A' and B' object at the end of section `[10]`
(the same pattern `reubicar.device_objects` uses for a whole
device) and repointing ONLY the page entry's `u16` field so it points at
`A'` instead of the old `A`. No other byte of `[9]`'s old entry is
touched, and the old `A`/`B` stay intact but unreachable -- the same
philosophy `fourth_device.py` and `delete_device.py` already use ("old bytes stay dead,
and that's fine").

Everything goes through `reubicar.relocate()`, which already knows how to
relocate `[9]`/`[10]`/`[11]` to the end without shifting anything that was
there: that's why "without moving a byte" isn't an aspiration, it's what
`grabar.nothing_moved` verifies.

## WHICH key can be reassigned here

`--button` is **not** "physical key code". Only a minority of the remote's
buttons hangs off an entry in section [9]: the **screen slots** (touch
zones, `codigo = tag|0x80` from section [19]'s templates). The numeric
keypad, volume, channel, transport and Power hang off no entry in [9] at
all, so they **do not map through this path**. A UI that promised "every
button on the remote" through this mechanism would be lying; it has to
mark those not editable and say why.

WHICH codes exist is a property of the configuration you feed in, not a
constant of the format, so this script carries no list: `screen_slots()`
walks section [9] of YOUR blob and reads them off, `--button` is validated
against that, and `--inventory` prints the whole census (pages, entries,
one line per code, and how many entries carry a command and a screen
transition at the same time -- see `_clone_object_a`):

    python3 assign_key.py <blob.bin> --inventory

## The mandatory checks (abort, write nothing if they fail)

    (a) `reubicar.chain`: ALL other keys resolve identically, and the
        requested key resolves to exactly the new `(cmd_id, dev_id)`.
    (b) `device.resolve_section5`: the new `cmd_id` is reachable
        with `cmd_setup_ir`'s EXACT arithmetic (`k1` and `k2` in range) --
        this is what prevents a hang, and it's checked BEFORE building anything.
    (c) `grabar.nothing_moved`, POSITIVE (the new blob) and NEGATIVE (a
        copy corrupted on purpose, to prove the check isn't a rubber stamp).
    (d) `configcheck.revisar`: all green (magic, close, checksum, size).
    (e) `reubicar.reachable_pages` / `page_references`:
        navigation stays THE SAME. Without this check the other four came
        back OK on a blob that had been left with whole screens with no
        path to them at all -- `cadena()` only looks at 0x7D/0x7C and is
        blind to `{page, 0x7E}`. Both functions report what was LOST, so
        the damage is named on whatever blob is being edited.

Plus a round-trip test: reassigning the key and then reassigning it back
to its original `(k1, k2)` has to give a `cadena()` IDENTICAL to the
starting one.

Writes nothing to the device. Runs nothing against the blob.

Usage:
    python3 assign_key.py <blob.bin> --inventory
    python3 assign_key.py <blob.bin> --pagina 6 --boton 0xB2 --k1 2 --k2 5
    python3 assign_key.py <blob.bin> --pagina 6 --boton 0xB2 --k1 2 --k2 5 \\
        --salida new.bin
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import sys

import command_records
import configcheck
import add_device as D
import write
import relocate

#: The byte that `nothing_moved`'s NEGATIVE check corrupts on purpose. It has
#: to be an offset the check does NOT let through, and which offsets those are
#: is `grabar.ALLOWED`'s business, not this file's: the first one past the
#: last permitted offset is derived from it, so if that set ever grows this
#: check keeps testing what it says it tests instead of silently landing on a
#: legitimate offset and passing for the wrong reason.
CORRUPT_OFFSET = max(write.ALLOWED) + 1


def _entry(b: bytes, a9: int, z9: int, page: int, button: int):
    """Locates `(page, button)` in section [9]. Returns `(campo, tag)`:

    `campo` is the blob's ABSOLUTE offset of that entry's 2 `u16` bytes --
    the only thing that's going to be overwritten. `tag` is informational.

    Walks the section with the SAME arithmetic as `reubicar.chain()`
    (via `reubicar._is_page`, the helper `cadena()` is an inline copy
    of) so it can't diverge from what the chain considers a valid page.
    If a key code repeats within a page, the LAST occurrence wins -- that's
    what `cadena()` does when it writes `out[(ordinal, button)] = ...` in a
    loop, and this tie-break has to match or check (a) could compare
    against the wrong entry.
    """
    o, ordinal = a9, 0
    while o < z9:
        L = relocate._is_page(b, o, z9)
        if not L:
            o += 1
            continue
        if ordinal == page:
            c = b[o]
            hallazgo = None
            for k in range(c):
                if b[o + 1 + 4 * k] == button:
                    hallazgo = (o + 2 + 4 * k, b[o + 4 + 4 * k])
            if hallazgo is None:
                raise ValueError(
                    "page %d doesn't have button %#04x (it has %s)"
                    % (page, button, ["%#04x" % b[o + 1 + 4 * j] for j in range(c)])
                )
            return hallazgo
        ordinal += 1
        o += L
    raise ValueError("page %d doesn't exist in section [9]" % page)


def section9_inventory(b: bytes) -> dict:
    """The census of section [9] OF THIS BLOB. Nothing here is hardcoded.

    Which screen-slot codes exist, how many entries carry each one, and how
    many of those entries hang off an A object that carries a command **and**
    a screen transition at once are all properties of the configuration that
    is fed in -- a different remote, or the same one after an edit, gives
    different numbers. Carrying a list measured on one particular blob is how
    a general tool turns into a one-off, so the list is read, not written.

    Walks with the SAME arithmetic as `_entry()` and `reubicar.chain()`
    (`reubicar._is_page`), so what it counts is exactly what those two see.

    Returns `{"pages", "entries", "codes" (Counter code->entries),
    "with_command", "command_and_transition"}`.
    """
    sec = relocate.sections(b)
    a9, z9 = sec[9]
    dest = relocate.table(b, sec[relocate.OBJECT_TABLE][0])
    codes: collections.Counter[int] = collections.Counter()
    pages = entradas = with_command = ambos = 0
    o = a9
    while o < z9:
        L = relocate._is_page(b, o, z9)
        if not L:
            o += 1
            continue
        for k in range(b[o]):
            codes[b[o + 1 + 4 * k]] += 1
            entradas += 1
            id_a = int.from_bytes(b[o + 2 + 4 * k : o + 4 + 4 * k], "little")
            tags = {t for _v, t in (relocate._slots(b, dest, id_a) or [])}
            if 0x7F in tags:
                with_command += 1
                if 0x7E in tags:
                    ambos += 1
        pages += 1
        o += L
    return {
        "pages": pages,
        "entries": entradas,
        "codes": codes,
        "with_command": with_command,
        "command_and_transition": ambos,
    }


def screen_slots(b: bytes) -> list[int]:
    """The button codes that section [9] of THIS blob actually has, sorted.

    This is the real domain of `--button`, derived per blob. Everything else
    on the remote -- keypad, volume, channel, transport, Power -- hangs off
    no entry in [9] and cannot be reassigned through this mechanism.
    """
    return sorted(section9_inventory(b)["codes"])


def growth_budget(b: bytes, out: bytes) -> tuple[int, int]:
    """`(what this pass cost in bytes, how many more passes still fit)`.

    Measured on the blob at hand, never quoted from a particular one.
    `reubicar.relocate()` re-appends the WHOLE `[9]+[10]+[11]` to the end
    every time it is called and never reclaims the dead copies, so the cost
    is per PASS -- which is exactly why `assign_many()` exists -- and
    `configcheck.MAX_COLISION` is the hard ceiling (above it the config
    overwrites the application). `-1` means the pass did not grow the blob,
    so there is no budget to run out of.
    """
    crecimiento = len(out) - len(b)
    if crecimiento <= 0:
        return crecimiento, -1
    return crecimiento, max(0, (configcheck.MAX_COLISION - len(out)) // crecimiento)


def _clone_object_a(b: bytes, dest: list[int], id_a_old: int, id_b: int) -> bytes:
    """The new A object: the OLD one as-is, with its single `0x7F` slot
    repointed to `id_b`. Everything else (the transition's `{page,0x7E}`,
    the `{dev_id,0x7C}` of tag-0x72 entries, the `0x75`, `0x92`, `0x9A`,
    `0x3F`, `0x07`...) is copied without looking at it.

    WHY IT IS CLONED AND NOT RE-EMITTED. The canonical minimal shape of an A
    object is `02 | {kind, 0x75} | {id_comando, 0x7F}`, and emitting THAT to
    reassign an existing key ERASES every other slot the old object had -- in
    particular the `{page, 0x7E}` of keys that carry a command **and** a
    screen transition at the same time. Those keys are not a corner case:
    `section9_inventory()` counts them on the blob at hand
    (`command_and_transition`), and when this was built the canonical shape
    left whole pages with no path to them at all while checks (a)-(d) still
    came back green. Hence check (e), and hence copying the shape instead of
    rebuilding it.

    Raises `ValueError` if the object doesn't have EXACTLY one `0x7F`
    slot: with zero there's nothing to repoint and with two there's no
    way to know which one is the command -- guessing would be exactly the
    mistake this helper exists to avoid.
    """
    rs = relocate._slots(b, dest, id_a_old)
    if not rs:
        raise ValueError("object A %d could not be read" % id_a_old)
    cuantas = sum(1 for _v, t in rs if t == 0x7F)
    if cuantas != 1:
        raise ValueError(
            "object A %d has %d 0x7F slot(s) (%s): no way to know which "
            "to repoint" % (id_a_old, cuantas, [(hex(v), hex(t)) for v, t in rs])
        )
    nuevas = [(id_b if t == 0x7F else v, t) for v, t in rs]
    return bytes([len(nuevas)]) + b"".join(relocate.slot(v, t) for v, t in nuevas)


def dev_id_of(b: bytes, k1: int, cadena: dict | None = None) -> int:
    """The `dev_id` device `k1`'s existing commands use.

    DERIVED from the config, not assumed: the `dev_id`s of every
    `reubicar.chain()` entry whose `cmd_id` has `k1` as its high byte are
    collected. If there's more than one value it's ambiguous -- an
    explicit `--dev-id` is required instead of guessing. If there's none
    (the device has no key reaching it yet), it falls back to the
    convention measured on the three factory devices -- `dev_id =
    (indice<<8)|0x01`, i.e. 0x0001/0x0101/0x0201 (see
    `commands.command_object`, `reubicar.device_objects`) --.
    [ASSUMED] only in that no-precedent case.
    """
    cadena = relocate.chain(b) if cadena is None else cadena
    vistos = {
        dev for cmd, dev in cadena.values() if dev is not None and (cmd >> 8) == k1
    }
    if len(vistos) == 1:
        return next(iter(vistos))
    if len(vistos) > 1:
        raise ValueError(
            "ambiguous dev_id for k1=%d: %s -- pass an explicit --dev-id"
            % (k1, sorted(vistos))
        )
    return (k1 << 8) | 0x01


def assign_many(b: bytes, changes: list[tuple[int, int, int, int]], dev_ids=None):
    """Reassigns N keys in a SINGLE pass.

    `changes`: `[(page, button, k1, k2), ...]`. `dev_ids` (optional) is a
    parallel list of explicit `dev_id`s (or `None` to derive it).

    Why it exists: `reubicar()` re-appends the WHOLE `[9]+[10]+[11]` to
    the end every time it's called, and never reclaims the dead copies.
    So the cost is per PASS, not per key: chaining N calls to `assign_key`
    pays it N times and walks into `configcheck.MAX_COLISION`, while one
    call with N changes pays it once. How many bytes that is, and how many
    passes are left before the ceiling, depends on how big [9]+[10]+[11]
    are in the blob at hand -- `growth_budget()` measures both, and `main()`
    prints them. A mapping screen needs exactly this: accumulate the
    changes and apply them all at once.

    Returns `(new blob, [(cmd_id, dev_id), ...])`. Raises `ValueError`
    without building anything if any change doesn't add up.
    """
    if not changes:
        raise ValueError("there is no change to apply")
    dev_ids = list(dev_ids or [None] * len(changes))
    if len(dev_ids) != len(changes):
        raise ValueError("dev_ids doesn't have the same length as cambios")

    sec = relocate.sections(b)
    a9, z9 = sec[9]
    a10, z10 = sec[10]
    dest = relocate.table(b, sec[relocate.OBJECT_TABLE][0])
    cad = relocate.chain(b)

    s9 = bytearray(b[a9:z9])
    s10 = bytearray(b[a10:z10])
    extra: list[int] = []
    resueltos: list[tuple[int, int]] = []
    vistos: set[tuple[int, int]] = set()

    for (page, button, k1, k2), dev_id in zip(changes, dev_ids):
        if (page, button) in vistos:
            raise ValueError(
                "(pagina=%d, boton=%#04x) appears twice in the same batch"
                % (page, button)
            )
        vistos.add((page, button))

        campo, _tag = _entry(b, a9, z9, page, button)
        if (page, button) not in cad:
            raise ValueError(
                "(pagina=%d, boton=%#04x) doesn't resolve to any command "
                "TODAY -- assign_key.py reassigns existing keys, it doesn't "
                "register empty buttons" % (page, button)
            )

        cmd_id = (k1 << 8) | k2
        if dev_id is None:
            dev_id = dev_id_of(b, k1, cad)

        reg, reason = D.resolve_section5(b, cmd_id)
        if reg is None:
            raise ValueError(
                "(k1=%d, k2=%d) -> cmd_id %#06x is NOT reachable through "
                "section [5]: %s -- grabbing this would hang the remote"
                % (k1, k2, cmd_id, reason)
            )

        # the A object that has to be CLONED is the one the entry points
        # at TODAY, read from the original blob (this entry hasn't been
        # written into `s9` yet)
        id_a_old = int.from_bytes(b[campo : campo + 2], "little")
        id_base = len(dest) + len(extra)

        off_b = len(s10)
        s10 += command_records.command_object(cmd_id, dev_id)
        extra.append(off_b)
        id_b = id_base

        off_a = len(s10)
        s10 += _clone_object_a(b, dest, id_a_old, id_b)
        extra.append(off_a)
        id_a = id_base + 1

        rel = campo - a9
        s9[rel : rel + 2] = id_a.to_bytes(2, "little")
        resueltos.append((cmd_id, dev_id))

    out = relocate.relocate(b, {9: bytes(s9), 10: bytes(s10)}, objetos_extra=extra)
    return out, resueltos


def assign_key(
    b: bytes,
    page: int,
    button: int,
    k1: int,
    k2: int,
    dev_id: int | None = None,
) -> tuple[bytes, int, int]:
    """Reassigns `(page, button)` to command `(k1, k2)`.

    Returns `(new blob, cmd_id, dev_id)`. Raises `ValueError` (writes
    nothing, doesn't call `reubicar()`) if the key doesn't exist, if it
    doesn't resolve to any command TODAY (this mechanism REASSIGNS, it
    doesn't register an empty button), or if `(k1, k2)` isn't reachable
    through section [5].

    A single-change case of `assign_many`: the old A object gets
    CLONED (not re-emitted canonically), so the `{page, 0x7E}` of keys
    carrying a command *and* a transition survives.
    """
    out, resueltos = assign_many(b, [(page, button, k1, k2)], [dev_id])
    cmd_id, dev_id = resueltos[0]
    return out, cmd_id, dev_id


def checks(
    b: bytes, out: bytes, page: int, button: int, cmd_id: int, dev_id: int
) -> list[tuple[str, bool, str]]:
    """Runs the 4 mandatory checks + the round-trip test.

    Reads only; writes nothing. Returns `[(name, ok, detail), ...]`.
    """
    chequeos = []

    # (a) reubicar.chain: all other keys identical, the requested one new
    before = relocate.chain(b)
    after = relocate.chain(out)
    mismas_claves = set(before) == set(after)
    distintas = {
        k
        for k in before
        if k in after and k != (page, button) and before[k] != after[k]
    }
    llego = after.get((page, button)) == (cmd_id, dev_id)
    chequeos.append(
        (
            "(a) button chain",
            mismas_claves and not distintas and llego,
            "%d keys before, %d after (%s); %d changed unrequested; the "
            "requested key resolves to %s (expected %s)"
            % (
                len(before),
                len(after),
                "same set" if mismas_claves else "DIFFERENT SET",
                len(distintas),
                after.get((page, button)),
                (cmd_id, dev_id),
            ),
        )
    )

    # (b) reachable through section [5] with the firmware's arithmetic
    reg, reason = D.resolve_section5(out, cmd_id)
    chequeos.append(
        (
            "(b) resolves through section [5]",
            reg is not None,
            "cmd_id %#06x -> record %#08x" % (cmd_id, reg)
            if reg is not None
            else "cmd_id %#06x: %s" % (cmd_id, reason),
        )
    )

    # (c) nada_se_movio, positive and its negative
    ok_pos, dif_pos = write.nothing_moved(b, out)
    corrompido = bytearray(out)
    corrompido[CORRUPT_OFFSET] ^= 0xFF
    ok_neg, dif_neg = write.nothing_moved(b, bytes(corrompido))
    chequeos.append(
        (
            "(c) nada_se_movio (+/-)",
            ok_pos and not ok_neg and CORRUPT_OFFSET in dif_neg,
            "positive: %s (%d different bytes, only [9][10][11]'s master "
            "index); negative (byte %#06x corrupted on purpose): the "
            "check says %s (has to say NO)"
            % (
                "YES" if ok_pos else "NO -- FAIL",
                len(dif_pos),
                CORRUPT_OFFSET,
                "YES -- didn't detect the shift" if ok_neg else "NO",
            ),
        )
    )

    # (e) navigation intact -- the check that was missing, and without it
    # the other four come back OK while 6 screens are left with no path
    # at all. `cadena()` ONLY looks at the 0x7D/0x7C slots: it's blind to
    # `{page, 0x7E}`, so (a) can't see this damage. `reubicar` already
    # provides the two functions that DO see it, written the time "not
    # being referenced" and "not being reachable" broke the hook once.
    alc_a, alc_d = relocate.reachable_pages(b), relocate.reachable_pages(out)
    ref_a, ref_d = (
        relocate.page_references(b),
        relocate.page_references(out),
    )
    chequeos.append(
        (
            "(e) navigation intact",
            alc_a == alc_d and ref_a == ref_d,
            "reachable %d->%d (lost: %s); referenced %d->%d (lost: %s)"
            % (
                len(alc_a),
                len(alc_d),
                sorted(alc_a - alc_d) or "none",
                len(ref_a),
                len(ref_d),
                sorted(ref_a - ref_d) or "none",
            ),
        )
    )

    # (d) configcheck all green
    pruebas = configcheck.revisar(out)
    chequeos.append(
        (
            "(d) configcheck.revisar",
            all(p[1] for p in pruebas),
            "; ".join("%s:%s" % (n, "OK" if ok else "FAIL") for n, ok, _ in pruebas),
        )
    )

    # round trip: reassigning back to the original (k1,k2) has to give
    # the WHOLE chain identical to the starting one
    cmd_orig, dev_orig = before[(page, button)]
    k1_orig, k2_orig = cmd_orig >> 8, cmd_orig & 0xFF
    try:
        vuelta, _cmd_v, _dev_v = assign_key(
            out, page, button, k1_orig, k2_orig, dev_orig
        )
        cad_vuelta = relocate.chain(vuelta)
        ok_vyv = cad_vuelta == before
        detalle_vyv = (
            "reassigning back to (k1=%d,k2=%d) gives a chain %s to the original"
            % (
                k1_orig,
                k2_orig,
                "IDENTICAL" if ok_vyv else "DIFFERENT",
            )
        )
    except ValueError as e:
        ok_vyv, detalle_vyv = False, "could not reassign back: %s" % e
    chequeos.append(("round trip", ok_vyv, detalle_vyv))

    return chequeos


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument(
        "--inventory",
        action="store_true",
        help="print section [9]'s census FOR THIS BLOB (pages, entries, one "
        "line per screen-slot code, entries carrying a command and a screen "
        "transition at once) and exit without touching anything",
    )
    ap.add_argument(
        "--page",
        type=lambda x: int(x, 0),
        help="page ordinal in section [9] (reubicar.chain's key)",
    )
    ap.add_argument(
        "--boton",
        type=lambda x: int(x, 0),
        help="SCREEN slot code, one of the ones section [9] of THIS blob has "
        "(--inventory lists them; the value is validated against the blob, "
        "not against a fixed list). The rest of the remote -- keypad, volume, "
        "channel, transport, Power -- hangs off no entry in [9]",
    )
    ap.add_argument(
        "--k1",
        type=lambda x: int(x, 0),
        help="device index in section [5] (see list_devices.py)",
    )
    ap.add_argument(
        "--k2",
        type=lambda x: int(x, 0),
        help="command ordinal within the device",
    )
    ap.add_argument(
        "--dev-id",
        type=lambda x: int(x, 0),
        default=None,
        help="explicit dev_id; if omitted it's derived from the config",
    )
    ap.add_argument(
        "--salida", help="where to write the new blob, only if EVERYTHING comes back OK"
    )
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    if b[:4] != b"GSPM":
        print("the blob doesn't start with GSPM", file=sys.stderr)
        return 1

    if a.inventory:
        inv = section9_inventory(b)
        print("section [9]: %d pages, %d entries" % (inv["pages"], inv["entries"]))
        print(
            "  %d entries resolve to a command (0x7F); of those %d ALSO carry "
            "a screen transition (0x7E)"
            % (inv["with_command"], inv["command_and_transition"])
        )
        print("\n  screen slots present in this blob (--boton takes these):")
        for codigo, cuantas in sorted(inv["codes"].items()):
            print("    %#04x  x%d" % (codigo, cuantas))
        return 0

    missing = [n for n in ("pagina", "boton", "k1", "k2") if getattr(a, n) is None]
    if missing:
        print(
            "missing: %s (all four are required unless --inventory)"
            % ", ".join("--" + n for n in missing),
            file=sys.stderr,
        )
        return 1

    slots = screen_slots(b)
    if a.boton not in slots:
        print(
            "boton %#04x hangs off no entry in section [9] of this blob. The "
            "codes it does have are %s -- run --inventory for the census."
            % (a.boton, " ".join("%#04x" % c for c in slots)),
            file=sys.stderr,
        )
        return 1

    try:
        out, cmd_id, dev_id = assign_key(b, a.page, a.boton, a.k1, a.k2, a.dev_id)
    except ValueError as e:
        print("NOT REASSIGNED: %s" % e, file=sys.stderr)
        return 1

    print(
        "reassigning pagina=%d boton=%#04x -> cmd_id=%#06x (k1=%d k2=%d) "
        "dev_id=%#06x\n" % (a.page, a.boton, cmd_id, a.k1, a.k2, dev_id)
    )

    chequeos = checks(b, out, a.page, a.boton, cmd_id, dev_id)
    width = max(len(c[0]) for c in chequeos)
    for name, ok, detail in chequeos:
        print("  %-*s  %-8s %s" % (width, name, "OK" if ok else "FAIL", detail))
    todo = all(c[1] for c in chequeos)

    crecio, quedan = growth_budget(b, out)
    print("\nblob: %d B -> %d B  (+%d)" % (len(b), len(out), crecio))
    if quedan >= 0:
        print(
            "one pass costs +%d B (the whole [9]+[10]+[11] re-appended); %d "
            "more passes fit under configcheck's %#08x ceiling -- batch with "
            "asignar_varias() instead of chaining calls"
            % (crecio, quedan, configcheck.MAX_COLISION)
        )
    print("\nVERDICT: %s" % ("fit to grab" if todo else "ABORTED: some check failed"))

    if not todo:
        return 1
    if a.salida:
        pathlib.Path(a.salida).write_bytes(out)
        print("written %s" % a.salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
