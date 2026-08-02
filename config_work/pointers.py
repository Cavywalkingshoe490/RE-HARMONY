#!/usr/bin/env python3
"""Enumerates the blob's pointers **by structure**, not by value.

This is the core of the rewriter. To insert anything into the config you have
to shift everything that follows and fix every pointer -- and for that you have
to know which ones they are.

**By value it cannot be done.** 90,461 positions in the blob hold a u24 that
falls inside the range, and the vast majority of them are coincidence: pixels
and data that look like addresses by chance. Shifting one of those corrupts
data silently, which is the worst way to break something.

So a pointer is declared **only if the structure that contains it says that
field is a pointer**:

    header               the u24 at +4, to the close
    master index         19 four-byte entries at +0x0C
    pointer tables       <u16 count><count x ptr24>
    enumeration tables   <counter:u8><ptr24>
    index tables         the ones that follow a run and point inside it
    section [6] records  u24 at +0 and +4 (declared by the firmware)
    IR command records   the pointer to the waveform

And fields that look like one and are **not** a pointer are not declared. The
case that took learning: slot lists are `{codigo:u8, u16, label:u8}`, and
their first three bytes read as a u24 fall in range 81% of the time -- by
chance, because the u16s are consecutive identifiers. That was measured and
then retracted; see `MAPA.md` section 11.

Usage:
    python3 pointers.py <config.bin>
    python3 pointers.py <config.bin> --listar 40
"""

from __future__ import annotations

import argparse
import pathlib

BASE = 0x040000


def u24(b: bytes, o: int) -> int:
    return int.from_bytes(b[o : o + 3], "little")


def enumerar(blob: bytes):
    """Returns {position: destination} of the pointers declared by structure."""
    import coverage
    import gaps

    n = len(blob)
    tope = BASE + n
    p = {}

    def poner(o, etq):
        if o + 3 > n:
            return
        v = u24(blob, o)
        if BASE <= v < tope:
            p[o] = (v - BASE, etq)

    # header: the u24 at +4 points at the close
    poner(4, "close")

    # master index: 4-byte entries from +0x0C, up to the first invalid one
    i = 0
    while True:
        o = 0x0C + 4 * i
        v = int.from_bytes(blob[o : o + 4], "little")
        if v and not 0 <= v - BASE < n:
            break
        if v:
            poner(o, "seccion [%d]" % i)
        i += 1
        if i > 64:
            break

    # pointer tables with a count, and index tables: the recognisers from
    # gaps.py are reused so as not to keep two different criteria
    tramos = gaps.cargar_tramos(blob)
    for ini, fin in tramos:
        o = ini
        while o < fin:
            vals, end = gaps.pointers(blob, o, fin)
            if len(vals) >= gaps.MIN_POINTERS:
                for k in range(len(vals)):
                    poner(o + 2 + 3 * k, "tabla de punteros")
                o = end
                continue
            idx = gaps.index_table(blob, o, fin)
            if len(idx) >= 3:
                # the table lives after the stretch; its start is located
                for arranque in range(fin, min(fin + 16, n - 3)):
                    if u24(blob, arranque) - BASE == idx[0]:
                        for k in range(len(idx)):
                            poner(arranque + 3 * k, "tabla indice")
                        break
                break
            o += 1

    # enumeration tables <counter:u8><ptr24>, from coverage.py's parser
    for off, count in coverage.enum_tables(blob, n):
        for k in range(count):
            poner(off + 4 * k + 1, "tabla de enumeracion")

    return p


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("config")
    ap.add_argument("--listar", type=int, default=0)
    a = ap.parse_args()
    blob = pathlib.Path(a.config).read_bytes()

    p = enumerar(blob)
    from collections import Counter

    c = Counter(etq for _, (_, etq) in p.items())
    print("pointers declared by structure: %d\n" % len(p))
    for k, v in c.most_common():
        print("  %-26s %6d" % (k, v))

    # how many in-range u24s there are in total, to size the problem
    tot = sum(
        1 for o in range(len(blob) - 2) if BASE <= u24(blob, o) < BASE + len(blob)
    )
    print("\n  in-range u24 in the whole blob: %d" % tot)
    print("  declared as pointer:         %d  (%.2f%%)" % (len(p), 100 * len(p) / tot))
    print("  the rest are coincidences, and they must NOT be touched when shifting the blob")

    if a.listar:
        print("\nprimeros %d:" % a.listar)
        for o in sorted(p)[: a.listar]:
            d, etq = p[o]
            print("  %#08x -> %#08x   %s" % (o, d, etq))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
