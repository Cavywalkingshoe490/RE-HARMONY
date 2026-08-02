#!/usr/bin/env python3
"""Byte accounting of the Harmony One config blob, with no catch-all buckets.

Every byte is attributed to a structure that has actually been reverse
engineered, or it is counted as unexplained. That discipline is the point: an
earlier version of this count reported 3.12% unknown while silently leaving
section [9] out of the claim list, which inflated the figure with data that was
already understood. Anything claimed here has a parser behind it.

The structures, in the order they are claimed:

    header, master index, LWJL7 key map
    pointer tables            <count> + count x ptr24
    records of section [6]    sized by the gap to the next record
    slot lists                <count:u8> + count x {code:u8, u16, tag:u8}
    enumeration tables        <counter:u8> <ptr24>, counter ascending
    heap objects              <count:u8> + count x <u16><tag>
    IR command records        21-byte header ending in the waveform pointer
    IR waveforms              u16 marks and spaces, bit 15 is the carrier gate
    bitmaps                   whatever is left that reads as RGB565 raster

Usage:
    python3 coverage.py <blob.bin> [--runs 10]
"""

from __future__ import annotations

import argparse
import pathlib
from collections import Counter, deque

import irscan
from objects import Blob
from walk import as_table

BASE = 0x040000
STRUCT = 0x02D660  # where the bitmap bulk starts
# 0x00 would be an empty slot, {code, 0x0000, 0x00}, but including it makes the
# scan greedy: it swallows bytes that read better as raster and the total
# explained share drops from 97.91% to 97.72%. Left out on measurement.
SLOT_TAGS = {0x7F, 0x7E, 0x72, 0x73, 0x7C}
LABEL = {
    1: "header + master index + key map",
    2: "pointer tables",
    3: "records of section [6]",
    4: "slot lists {code, u16, tag}",
    5: "enumeration tables {n, ptr24}",
    6: "heap objects",
    7: "IR command records",
    8: "IR waveforms",
    9: "bitmaps",
}


def slot_lists(b: bytes, start: int, end: int):
    """Claim a region that tiles exactly as <count:u8> + count x 4 bytes."""
    o = start
    while o < end:
        c = b[o]
        if o + 1 + 4 * c > end:
            return False
        o += 1 + 4 * c
    return o == end


def enum_tables(b: bytes, n: int):
    """Find every <counter:u8><ptr24> run whose counter ascends by one."""
    out, o = [], 0
    while o < n - 4:
        k, count, p = b[o], 0, o
        while p + 4 <= n and b[p] == (k + count) & 0xFF and 0x04 <= b[p + 3] <= 0x18:
            target = (b[p + 1] | (b[p + 2] << 8) | (b[p + 3] << 16)) - BASE
            if not 0 <= target < n:
                break
            count += 1
            p += 4
        if count >= 6:
            out.append((o, count))
            o = p
        else:
            o += 1
    return out


def is_raster(x: bytes) -> bool:
    if len(x) < 64:
        return False
    same = sum(1 for i in range(0, len(x) - 4, 2) if x[i : i + 2] == x[i + 2 : i + 4])
    return same / max(1, (len(x) // 2 - 1)) > 0.2


def tramos_sin_explicar(own: bytearray):
    """The contiguous stretches nobody claimed, as (start, end)."""
    out, i, n = [], 0, len(own)
    while i < n:
        if own[i]:
            i += 1
            continue
        j = i
        while j < n and not own[j]:
            j += 1
        out.append((i, j))
        i = j
    return out


def mapa_de_reclamos(b: bytes, pointers: dict | None = None) -> bytearray:
    """Returns one byte per position: 0 = unexplained, >0 = tag of the
    structure that claims it. Extracted out of main() so that other tools
    (gaps.py, mapa.py) use exactly the same claim logic instead of a copy
    that could drift out of sync."""
    n = len(b)
    bl = Blob(b)
    own = bytearray(n)

    def claim(start, stop, tag):
        for k in range(max(start, 0), min(stop, n)):
            if own[k] == 0:
                own[k] = tag

    # header, master index, key map
    i, sections = 0, []
    while True:
        v = int.from_bytes(b[0x0C + 4 * i : 0x10 + 4 * i], "little")
        if v and not 0 <= v - BASE < n:
            break
        if v:
            sections.append(v - BASE)
        i += 1
    claim(0, 0x0C + 4 * i, 1)
    if pointers is not None:
        pointers[4] = (int.from_bytes(b[4:7], "little") - BASE, "close")
        for k, s in enumerate(sections):
            pointers[0x0C + 4 * k] = (s, "indice maestro [%d]" % k)
    magic = b.find(b"LWJL7")
    claim(magic, magic + 5 + 55 * 4, 1)

    # pointer tables and the records they index
    seen, queue = set(), deque(sections)
    while queue:
        at = queue.popleft()
        if at in seen or not 0 <= at < n:
            continue
        seen.add(at)
        table = as_table(b, at, 200000)
        if not table:
            continue
        hdr, targets = table
        claim(at, at + hdr + 3 * len(targets), 2)
        if pointers is not None:
            for k in range(len(targets)):
                pointers[at + hdr + 3 * k] = (targets[k], "tabla de punteros")
        if len(targets) == 156:
            for k, p in enumerate(targets):
                size = targets[k + 1] - p if k + 1 < len(targets) else 0
                if 0 < size < 400:
                    claim(p, p + size, 3)
        queue.extend(targets)

    # slot lists. They are not confined to section [9]: the same
    # {code, u16, tag} shape appears 223 times across the structure region, and
    # several of those runs open with the codes 89 88 8b 8a, which is the head of
    # the LWJL7 key map. So the physical keys are bound per context, not once.
    # The value has to be a valid object index or the scan matches noise inside
    # the bitmaps, where those tag bytes occur by chance.
    o = 0
    while o < STRUCT - 4:
        count, p = 0, o
        while (
            p + 4 <= STRUCT
            and b[p + 3] in SLOT_TAGS
            and b[p] >= 0x04
            and (b[p + 1] | (b[p + 2] << 8)) < len(bl.objects)
        ):
            count += 1
            p += 4
        if count >= 4:
            claim(o, o + 4 * count, 4)
            o = p
        else:
            o += 1

    for off, count in enum_tables(b, n):
        claim(off, off + 4 * count, 5)

    for k, p in enumerate(bl.ptr):
        claim(p, p + 1 + 3 * len(bl.objects[k]), 6)

    for o in range(11, n - 3):
        if b[o - 1] != 1 or not 0x04 <= b[o + 2] <= 0x18:
            continue
        p = (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)) - BASE
        if 0 <= p < n - 1 and (b[p] | (b[p + 1] << 8)) == irscan.LEAD_IN:
            claim(o - 11, o + 10, 7)
            claim(p, p + 2 * len(irscan.read_waveform(b, p)), 8)

    # leftovers that read as raster are bitmaps; the rest is unexplained
    runs, start = [], None
    for k in range(n + 1):
        here = k < n and own[k] == 0
        if here and start is None:
            start = k
        elif not here and start is not None:
            runs.append((start, k))
            start = None
    for s, e in runs:
        if is_raster(b[s:e]):
            claim(s, e, 9)

    return own


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blob")
    ap.add_argument("--runs", type=int, default=10)
    a = ap.parse_args()
    b = pathlib.Path(a.blob).read_bytes()
    n = len(b)
    own = mapa_de_reclamos(b)
    c = Counter(own)
    explained = sum(v for k, v in c.items() if k)
    runs = tramos_sin_explicar(own)

    print("blob %d bytes\n" % n)
    for k in sorted(LABEL):
        if c[k]:
            print("  %-34s %9d  %6.2f%%" % (LABEL[k], c[k], 100 * c[k] / n))
    print("  %-34s %9d  %6.2f%%" % ("EXPLAINED", explained, 100 * explained / n))
    print("  %-34s %9d  %6.2f%%" % ("unexplained", c[0], 100 * c[0] / n))

    rest = sorted(runs, key=lambda r: r[0] - r[1])
    print("\n%d unexplained runs, largest:" % len(rest))
    for s, e in rest[: a.runs]:
        print(
            "  %#08x .. %#08x  %6d B  %s"
            % (s, e, e - s, " ".join("%02x" % x for x in b[s : s + 14]))
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
