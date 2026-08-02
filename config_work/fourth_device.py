#!/usr/bin/env python3
"""Adds a FOURTH device to the Devices menu by relocating the menu objects.

## Why inserting 18 bytes is not enough

The device list lives inside a menu object, and putting an entry in the middle
**shifts everything that follows**. That already left the remote in a boot loop
once: there are ~84,000 in-range u24s left unclassified that would cross the cut,
so a shift **cannot be validated**.

And the cheap way out does not exist either: the `10 03` bytes at the start of the
object **are not a counter**. Refuted with a controlled counterexample -- the same
`10 09` sequence precedes 3 devices at `0x0148e8` and **just one** at `0x0149c0`.

## What does work

Each object gets **exactly one external reference**, and it comes from a counted
table at the start of section [6]. So: the whole object is copied to the end of the
body with the new entry inside it, its internal pointers are fixed, and that single
reference is repointed. **Not one byte of the original body moves.**

It is the same pattern `relocate.py` applies to sections [9][10][11], which gave
198/198 on the null check.

## Parent table convention (the bug that cost a cycle)

Section [6]'s table entries start at **+3**, not at +2: with +2 only 34.6% of them
land in range, with +3 **156/156** land and they are monotonic. Reading it wrong is
what produced the false negative "nobody points at the list".

And **an object's start is never pointed at**: what is pointed at is its *trailer*,
the final 9 bytes `00 <ptr24 al inicio>` (147/156 entries have that shape).

## The new entry

    02 06 <inst>   <ptr24 to the large icon 164x50>
    02 0b <inst+1> <ptr24 to the small icon  51x48>
    04 3f <grupo>  <ptr24 to the name text>

The 6 bytes that used to be listed as "unresolved" are those two icon pointers: the
destination carries a `00 <width> 00 <height>` header, and the six of the three
devices are different addresses -- their own art, not a shared default.

The instances follow the 54 step, verified 3/3, and they are checked not to collide.

Usage:
    python3 fourth_device.py <blob.bin> --nombre Philips --salida nuevo.bin
"""

from __future__ import annotations

import argparse
import pathlib

import glyphs

BASE = 0x040000
PASO = 54  # el paso entre instancias, verificado 3/3
TAG_NAME = 0x3F


def u24(b: bytes, o: int) -> int:
    return int.from_bytes(b[o : o + 3], "little")


def p24(v: int) -> bytes:
    return v.to_bytes(3, "little")


def index(b: bytes) -> list[int]:
    return [u24(b, 0x0C + 4 * k) - BASE for k in range(19)]


def table_six(b: bytes) -> tuple[int, int, list[int]]:
    """(table offset, count, destinations). The entries start at +3."""
    a6 = index(b)[6]
    n = int.from_bytes(b[a6 : a6 + 2], "little")
    dest = [u24(b, a6 + 3 + 3 * k) - BASE for k in range(n)]
    return a6, n, dest


def objeto_de(b: bytes, trailer: int) -> tuple[int, int]:
    """The object a trailer belongs to: (start, end). end = trailer + 9."""
    return u24(b, trailer + 1) - BASE, trailer + 9


def bloques_de(b: bytes, o: int) -> tuple[int, int]:
    """(offset of the `02 06` block, offset of the `02 0b` block) of the entry whose
    name field is at `o`.

    **They cannot be taken at `o-12` and `o-6`.** The TV entry carries a `10 04`
    slipped in between the payload and the name that the other two do not have, so
    a fixed offset lands two bytes off and returns pointers to garbage (seen:
    `0 x 0` icons). They have to be searched for backwards by content.
    """
    chico = next(
        (o - j for j in range(6, 15) if b[o - j] == 0x02 and b[o - j + 1] == 0x0B), -1
    )
    if chico < 0:
        raise SystemExit("the `02 0b` block of entry %#08x was not found" % o)
    grande = next(
        (
            chico - j
            for j in range(6, 15)
            if b[chico - j] == 0x02 and b[chico - j + 1] == 0x06
        ),
        -1,
    )
    if grande < 0:
        raise SystemExit("the `02 06` block of entry %#08x was not found" % o)
    return grande, chico


def entradas_de(b: bytes, ini: int, fin: int) -> list[int]:
    """Offsets of the `04 3f <grupo>` name fields inside the object."""
    out = []
    for o in range(ini, fin - 5):
        if b[o] == 0x04 and b[o + 1] == TAG_NAME:
            g = b[o + 2]
            if (g - 0x39) % PASO == 0 and 0 <= (g - 0x39) // PASO < 16:
                out.append(o)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("--name", default="Philips")
    ap.add_argument("--config")
    ap.add_argument("--salida")
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    a6, n6, dest6 = table_six(b)
    print("section [6] table at %#08x: %d entries" % (a6, n6))
    en_rango = sum(1 for d in dest6 if 0 <= d < len(b))
    print("   alignment check: %d/%d destinations in range" % (en_rango, n6))
    if en_rango != n6:
        raise SystemExit("the table is not aligned at +3: I am not going on")

    # the objects that contain a device list: the ones with >=3 name fields
    # with consecutive groups from the 54 step
    objetos = []
    for k, d in enumerate(dest6):
        if not (0 <= d < len(b) - 9) or b[d] != 0x00:
            continue
        ini, fin = objeto_de(b, d)
        if not (0 <= ini < fin <= len(b)) or fin - ini > 4096:
            continue
        ent = entradas_de(b, ini, fin)
        if len(ent) >= 3:
            objetos.append((k, d, ini, fin, ent))

    print("\nmenu objects with a device list: %d" % len(objetos))
    for k, d, ini, fin, ent in objetos:
        grupos = [b[o + 2] for o in ent]
        print(
            "   tabla[6][%3d] -> trailer %#08x  objeto %#08x-%#08x (%3d B)  "
            "%d entradas, grupos %s"
            % (k, d, ini, fin, fin - ini, len(ent), [hex(g) for g in grupos])
        )
    if not objetos:
        raise SystemExit("no object with a device list was found")

    # the fourth slot's instance and group, and the collision check
    ref = objetos[0]
    grupos = sorted(b[o + 2] for o in ref[4])
    new_group = max(grupos) + PASO
    ult = max(ref[4])
    g_ult, c_ult = bloques_de(b, ult)
    new_a, new_b = b[g_ult + 2] + PASO, b[c_ult + 2] + PASO
    print(
        "\ncuarto slot: instancias %#04x/%#04x, grupo de nombre %#04x"
        % (new_a, new_b, new_group)
    )
    for pat, etq in (
        (bytes([0x02, 0x06, new_a]), "02 06 inst"),
        (bytes([0x02, 0x0B, new_b]), "02 0b inst+1"),
        (bytes([0x04, TAG_NAME, new_group]), "04 3f grupo"),
        (bytes([0x05, TAG_NAME, new_group]), "05 3f grupo"),
    ):
        c = b.count(pat)
        print("   colision %-14s %s: %d apariciones" % (etq, pat.hex(" "), c))
        if c:
            raise SystemExit("the fourth slot collides with something that already exists")
    if max(new_a, new_b, new_group) > 0xFF:
        raise SystemExit("the fourth slot overflows the instance byte")

    # The icons are copied **from the TV**, which is the first entry: the Philips
    # Philips TV is a television, and in the Hub config its DeviceType is 1
    # (Television), the same one as the TV that is already there. In the five
    # devices of Logitech's JSON the icon matches the DeviceType.
    prim = min(ref[4])
    g_prim, c_prim = bloques_de(b, prim)
    icono_g = b[g_prim + 3 : g_prim + 6]
    icono_c = b[c_prim + 3 : c_prim + 6]
    for etq, ptr in (("grande", icono_g), ("chico", icono_c)):
        d = int.from_bytes(ptr, "little") - BASE
        print(
            "   icono %-7s -> %#08x  cabecera %s = %d x %d"
            % (etq, d, b[d : d + 4].hex(" "), b[d + 1], b[d + 3])
        )

    # the name record, at the end of the body
    vocab = glyphs.vocabulario(a.config) if a.config else set()
    table, _ = glyphs.extender(b, vocab)
    cod = glyphs.codificar(a.name, table)
    if cod is None:
        inv = {v: k for k, v in table.items()}
        raise SystemExit(
            "cannot encode %r: %r missing"
            % (a.name, "".join(sorted({c for c in a.name if c not in inv})))
        )

    close = u24(b, 4) - BASE
    out = bytearray(b[: close - 2])
    off_name = len(out) + 3
    out += bytes([0x05, TAG_NAME, new_group]) + cod
    print("\nname record %r at %#08x (%d B)" % (a.name, off_name, len(cod)))

    entrada = (
        bytes([0x02, 0x06, new_a])
        + icono_g
        + bytes([0x02, 0x0B, new_b])
        + icono_c
        + bytes([0x04, TAG_NAME, new_group])
        + p24(BASE + off_name)
    )
    assert len(entrada) == 18

    # each object: it is copied whole with the entry inside it and its internal
    # pointers are fixed. The ones pointing outside are not touched.
    repuntes = []
    for k, d, ini, fin, ent in objetos:
        corte = max(ent) + 6  # right after the last entry
        delta = len(out) - ini
        cuerpo = bytearray(b[ini:corte] + entrada + b[corte:fin])
        arreglados = 0
        for o in range(len(cuerpo) - 2):
            v = int.from_bytes(cuerpo[o : o + 3], "little") - BASE
            if not (ini <= v < fin):
                continue
            nv = v + delta + (18 if v >= corte else 0)
            cuerpo[o : o + 3] = p24(BASE + nv)
            arreglados += 1
        new_trailer = d + delta + (18 if d >= corte else 0)
        repuntes.append((a6 + 3 + 3 * k, BASE + new_trailer))
        print(
            "   objeto %#08x-%#08x -> %#08x  (%d punteros internos corregidos)"
            % (ini, fin, len(out), arreglados)
        )
        out += cuerpo

    if len(out) % 2:
        out += b"\x00"
    nc = len(out) + 2
    out += b"\x00\x00" + b"PTYY"
    out[4:7] = p24(BASE + nc)
    for o, v in repuntes:
        out[o : o + 3] = p24(v)

    lo, hi = 0x21, 0x43
    for k in range(0, nc - 2, 2):
        lo ^= out[k]
        hi ^= out[k + 1]
    out[nc - 2] = lo
    out[nc - 1] = hi

    fresh = bytes(out)
    dif = [i for i in range(close - 2) if b[i] != fresh[i]]
    esperados = {4, 5, 6} | {o + j for o, _ in repuntes for j in range(3)}
    sobra = sorted(set(dif) - esperados)
    print("\nbytes of the old body changed: %d" % len(dif))
    print("   declarados: cierre [4:7] + %d repuntes de 3 B" % len(repuntes))
    print("   sin declarar: %s" % (sobra if sobra else "none"))
    if sobra:
        raise SystemExit("there are undeclared changes: nothing gets written")
    print("blob: %d -> %d B" % (len(b), len(fresh)))

    # check: the new objects have to have 4 entries and resolve
    _, _, d2 = table_six(fresh)
    for k, _, _, _, _ in objetos:
        t = d2[k]
        ini2, fin2 = objeto_de(fresh, t)
        ent2 = entradas_de(fresh, ini2, fin2)
        txt = []
        for o in ent2:
            p = u24(fresh, o + 3) - BASE
            s = fresh[p : fresh.index(b"\x00", p)]
            txt.append("".join(table.get(c, "?") for c in s))
        print("   control tabla[6][%d]: %d entradas -> %s" % (k, len(ent2), txt))
        if len(ent2) != 4:
            raise SystemExit("the relocated object does not have 4 entries")

    if a.salida:
        pathlib.Path(a.salida).write_bytes(fresh)
        print("\nescrito %s" % a.salida)
        print("repuntes a declarar en write.py:")
        print("   " + " ".join("--repunta %#08x" % o for o, _ in repuntes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
