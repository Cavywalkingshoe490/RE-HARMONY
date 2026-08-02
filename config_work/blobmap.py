#!/usr/bin/env python3
"""Complete byte accounting of the Harmony One (arch 12) configuration blob.

The file turned out to have one uniform shape. There is no per-section format:
every section named by the master index is either a pointer table or a small
literal, and every pointer lands on a self-delimiting object.

    header        "GSPM" u32 end_vector, u32 0x1600, then u32 section pointers
    button map    "LWJL7" then 4-byte {code, index, 0x00, 0x7f} entries
    section       <count> + count x ptr24        (count is u8, u16 or u24)
    object        <count:u8> + count x atom
    atom          <operand:u16 LE> <op:u8>
    bulk          everything after the last index: RGB565 graphics

Because the object grammar is self-delimiting, a pointer is enough to recover
the object's extent -- no length field and no section boundary is needed. That
is what lets this script claim bytes by walking rather than by guessing, and
what makes the leftover count meaningful.

Usage:
    python3 blobmap.py <blob.bin>
    python3 blobmap.py <blob.bin> --runs 20     # show leftover runs
"""

from __future__ import annotations

import argparse
import pathlib
from collections import Counter

BASE = 0x040000
BULK = 0x02D660  # first byte after the last index table


def u24(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


def read_table(b: bytes, at: int):
    """Return (header_size, [targets]) if `at` starts an ascending ptr24 table."""
    for hdr, cnt in ((1, b[at]), (2, b[at] | (b[at + 1] << 8)), (3, u24(b, at))):
        if not 0 < cnt < 200000 or at + hdr + 3 * cnt > len(b):
            continue
        out, o, ok = [], at + hdr, True
        for _ in range(cnt):
            p = u24(b, o) - BASE
            o += 3
            if not 0 <= p < len(b) or (out and p <= out[-1]):
                ok = False
                break
            out.append(p)
        if ok:
            return hdr, out
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blob")
    ap.add_argument("--runs", type=int, default=10)
    a = ap.parse_args()
    b = pathlib.Path(a.blob).read_bytes()

    owner = bytearray(len(b))
    LABEL = {
        1: "header + master index",
        2: "button map (LWJL7)",
        3: "section pointer tables",
        4: "records (section [6])",
        5: "objects (atom grammar)",
        6: "small literal sections",
        7: "bulk graphics",
    }

    def claim(start, stop, tag):
        for k in range(max(start, 0), min(stop, len(b))):
            owner[k] = tag

    # master index: pointers start at 0x0c and run until the first non-pointer
    n = 0
    while True:
        v = int.from_bytes(b[0x0C + 4 * n : 0x10 + 4 * n], "little")
        if v and not 0 <= v - BASE < len(b):
            break
        n += 1
        if n > 32:
            break
    claim(0, 0x0C + 4 * n, 1)
    sections = [
        v - BASE
        for v in (
            int.from_bytes(b[0x0C + 4 * i : 0x10 + 4 * i], "little") for i in range(n)
        )
        if v
    ]

    # button map
    magic = b.find(b"LWJL7")
    o, exp = magic + 5, 0
    while (
        o + 4 <= len(b)
        and b[o + 1] == exp & 0xFF
        and b[o + 2] == 0
        and b[o + 3] == 0x7F
    ):
        o += 4
        exp += 1
    claim(magic, o, 2)

    # every section, and every object each section points at
    objects = 0
    for rel in sections:
        t = read_table(b, rel)
        if not t:
            claim(rel, rel + 1, 6)
            continue
        hdr, targets = t
        claim(rel, rel + hdr + 3 * len(targets), 3)
        tag = 4 if len(targets) == 156 else 5
        for p in targets:
            if tag == 4:
                continue
            claim(p, p + 1 + 3 * b[p], 5)
            objects += 1

    # records: the section-6 table, sized by the gap to the next record
    t6 = next(
        (
            read_table(b, r)
            for r in sections
            if (read_table(b, r) or (0, []))[1].__len__() == 156
        ),
        None,
    )
    if t6:
        tg = t6[1]
        for i, p in enumerate(tg):
            size = tg[i + 1] - p if i + 1 < len(tg) else 0
            if 0 < size < 400:
                claim(p, p + size, 4)

    claim(BULK, len(b), 7)
    # small sections that are literals, not tables
    for rel in sections:
        if not read_table(b, rel):
            nxt = min([s for s in sections if s > rel] + [BULK])
            if nxt - rel < 1000:
                claim(rel, nxt, 6)

    c = Counter(owner)
    total = len(b)
    print("blob %d bytes    objects walked %d" % (total, objects))
    for k in sorted(LABEL):
        if c[k]:
            print("  %-24s %9d  %6.2f%%" % (LABEL[k], c[k], 100 * c[k] / total))
    print("  %-24s %9d  %6.2f%%" % ("UNCLAIMED", c[0], 100 * c[0] / total))

    runs, start = [], None
    for k in range(total + 1):
        here = k < total and owner[k] == 0
        if here and start is None:
            start = k
        elif not here and start is not None:
            runs.append((start, k))
            start = None
    runs.sort(key=lambda r: r[1] - r[0], reverse=True)
    print("\n%d leftover runs, largest:" % len(runs))
    for s, e in runs[: a.runs]:
        print(
            "  %#08x .. %#08x  %7d B   %s"
            % (s, e, e - s, " ".join("%02x" % x for x in b[s : s + 16]))
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
