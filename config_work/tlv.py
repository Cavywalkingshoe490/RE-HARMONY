#!/usr/bin/env python3
"""Infers the heap's TLV format by solving the operand sizes.

**Why it is needed.** Shifting the blob requires knowing which u24 is really a
pointer, and today **4.76%** is classified. At that coverage, shifting breaks the
device -- it already happened. Statistical heuristics are not enough: for a
174-byte window the expected coincidences are ~14 and 9 are observed, i.e. **below
chance**. The signal only shows up when the structure **declares** the field.

**The observation that makes it solvable.** The heap is a stream of
`<tag><operando>` records, and the operand size looks fixed per tag:

    06 5b 04 7f    tag 06, u16 + type          operand 3
    b7 14 04 72    tag b7, same with type 0x72 operand 3
    10 03          tag 10                      operand 1
    04 06 04 56 f6 04   name reference         operand 5

There are **125 containers** delimited by the `<af 00 00 00><ae 00 00 00>`
signature, and parsing each one has to **land exactly** on the start of the next.
That is 125 equations over the unknown sizes: a constraint satisfaction problem,
not a heuristic.

If it gets solved, every field ends up declared and **which one is a pointer is
known**, which is exactly what is missing to shift the blob safely.

Usage:
    python3 tlv.py <blob.bin>
    python3 tlv.py <blob.bin> --mostrar 3
"""

from __future__ import annotations

import argparse
import pathlib
from collections import Counter, defaultdict

FIRMA = bytes.fromhex("af000000ae000000")
# Sizes already read by hand off the dump; the rest is inferred.
SEMILLA = {0x04: 5, 0x06: 3, 0x07: 3, 0xB7: 3, 0x2D: 3, 0x10: 1}
MAX_OPERANDO = 8


def contenedores(b: bytes) -> list[tuple[int, int]]:
    """[(start, end)] of the container records, by their signature."""
    pos, o = [], 0
    while True:
        i = b.find(FIRMA, o)
        if i < 0:
            break
        pos.append(i - 1)
        o = i + 1
    # absurdly long stretches are discarded: there the signature is missing and the
    # "container" is really several of them plus something else
    out = []
    for k in range(len(pos) - 1):
        largo = pos[k + 1] - pos[k]
        if 32 <= largo <= 1024:
            out.append((pos[k], pos[k + 1]))
    return out


def parsear(b, ini, fin, tam):
    """[(offset, tag)] if the stream lands exactly on `fin`; None if not."""
    out, o = [], ini
    # the signature is two operand-3 fields with a null value, already known
    while o < fin:
        t = b[o]
        n = tam.get(t)
        if n is None:
            return None
        out.append((o, t))
        o += 1 + n
    return out if o == fin else None


def total_progress(b, muestras, tam) -> tuple[int, int]:
    """(bytes parsed in total, containers that land exactly)."""
    bytes_ok, exactos = 0, 0
    for ini, fin in muestras:
        o = ini
        while o < fin and b[o] in tam:
            o += 1 + tam[b[o]]
        if o == fin:
            exactos += 1
        bytes_ok += min(o, fin) - ini
    return bytes_ok, exactos


def aprender(b: bytes, muestras, vueltas: int = 60):
    """Extends the sizes maximising how far the parse gets.

    **The previous version picked one tag per round according to "does this
    container end?" and committed to it.** With that, one badly chosen size
    poisoned everything else: **2 out of 118** parsed. Here the criterion is the
    **total progress over the 118 samples**, much harder to improve by accident,
    and on top of that the improvement is required to be strict before accepting
    a size: if none improves, **none is invented**.
    """
    tam = dict(SEMILLA)
    for _ in range(vueltas):
        frena = Counter()
        for ini, fin in muestras:
            o = ini
            while o < fin and b[o] in tam:
                o += 1 + tam[b[o]]
            if o < fin:
                frena[b[o]] += 1
        if not frena:
            break
        tag = frena.most_common(1)[0][0]
        base = total_progress(b, muestras, tam)
        mejor, mejor_n = base, None
        for n in range(0, MAX_OPERANDO + 1):
            t2 = dict(tam)
            t2[tag] = n
            r = total_progress(b, muestras, t2)
            if r > mejor:
                mejor, mejor_n = r, n
        if mejor_n is None:
            break
        tam[tag] = mejor_n
    return tam


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("--mostrar", type=int, default=0)
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    m = contenedores(b)
    print("contenedores utilizables: %d" % len(m))
    print("largos: %s" % sorted(Counter(f - i for i, f in m).most_common(5)))

    tam = aprender(b, m)
    ok = sum(1 for i, f in m if parsear(b, i, f, tam) is not None)
    print("\ntamaños de operando inferidos: %d tags" % len(tam))
    print("   %s" % ", ".join("%#04x=%d" % kv for kv in sorted(tam.items())))
    print("containers that parse exactly: %d of %d" % (ok, len(m)))

    if a.mostrar:
        for i, f in m[: a.mostrar]:
            r = parsear(b, i, f, tam)
            print("\n%#08x..%#08x %s" % (i, f, "" if r else "(NO PARSEA)"))
            for o, t in (r or [])[:24]:
                n = tam[t]
                print(
                    "   +%3d tag %#04x  %s"
                    % (o - i, t, " ".join("%02x" % x for x in b[o + 1 : o + 1 + n]))
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
