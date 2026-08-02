#!/usr/bin/env python3
"""Transitive walk of the Harmony One (arch 12) configuration blob.

The blob is a graph, not a layout. Sections point at tables, tables point at
other tables or at objects, and objects point back at objects by index. So the
only honest way to account for the bytes is to start at the master index and
follow every edge until nothing new is reachable.

Two node shapes, both self-delimiting:

    table   <count> + count x ptr24        count is u8, u16 or u24
    object  <count:u8> + count x atom      atom = <operand:u16 LE> <op:u8>

Telling them apart is not a judgement call. Both are triplet arrays, but the
third byte means different things: in a pointer it is the high byte of a flash
address, which for this image is always 0x04..0x18, and in an atom it is the
opcode, which is 0x00 or 0x70..0xAB. The two ranges do not overlap. A run of
triplets whose third bytes are all in the pointer range *and* whose values
ascend is a table; anything else is read as an object.

Usage:
    python3 walk.py <blob.bin>
    python3 walk.py <blob.bin> --runs 20
"""

from __future__ import annotations

import argparse
import pathlib
from collections import Counter, deque

BASE = 0x040000
TABLE = 1
RECORD = 2
OBJECT = 3
INDEX = 4
BULK = 5
LABEL = {
    TABLE: "pointer tables",
    RECORD: "records",
    OBJECT: "objects (atoms)",
    INDEX: "header + master index + button map",
    BULK: "bulk graphics",
}


def u24(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


def as_table(b: bytes, at: int, limit: int):
    """Read a pointer table at `at`, or None. `limit` caps the entry count."""
    for hdr, cnt in ((1, b[at]), (2, b[at] | (b[at + 1] << 8)), (3, u24(b, at))):
        if not 1 <= cnt <= limit or at + hdr + 3 * cnt > len(b):
            continue
        out, o, ok = [], at + hdr, True
        for _ in range(cnt):
            if not 0x04 <= b[o + 2] <= 0x18:  # high byte of a flash pointer
                ok = False
                break
            p = u24(b, o) - BASE
            o += 3
            if not 0 <= p < len(b) or (out and p <= out[-1]):
                ok = False
                break
            out.append(p)
        if ok and out:
            return hdr, out
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blob")
    ap.add_argument("--runs", type=int, default=12)
    a = ap.parse_args()
    b = pathlib.Path(a.blob).read_bytes()
    owner = bytearray(len(b))

    def claim(s, e, tag):
        for k in range(max(s, 0), min(e, len(b))):
            owner[k] = tag

    # master index at 0x0c, ending at the first value that is not a pointer
    secs, i = [], 0
    while True:
        v = int.from_bytes(b[0x0C + 4 * i : 0x10 + 4 * i], "little")
        if v and not 0 <= v - BASE < len(b):
            break
        if v:
            secs.append(v - BASE)
        i += 1
        if i > 32:
            break
    claim(0, 0x0C + 4 * i, INDEX)

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
    claim(magic, o, INDEX)

    seen, q = set(), deque(secs)
    tables = objects = records = 0
    while q:
        at = q.popleft()
        if at in seen or not 0 <= at < len(b):
            continue
        seen.add(at)
        t = as_table(b, at, 200000)
        if t:
            hdr, targets = t
            end = at + hdr + 3 * len(targets)
            # a 156-entry table is the record index; its targets are records,
            # sized by the gap to the next one
            if len(targets) == 156:
                records += 1
                claim(at, end, TABLE)
                for k, p in enumerate(targets):
                    size = targets[k + 1] - p if k + 1 < len(targets) else 0
                    if 0 < size < 400:
                        claim(p, p + size, RECORD)
                    q.append(p)
                continue
            tables += 1
            claim(at, end, TABLE)
            q.extend(targets)
        else:
            objects += 1
            claim(at, at + 1 + 3 * b[at], OBJECT)

    # bulk: everything after the highest claimed index byte
    last = max(k for k in range(len(b)) if owner[k] in (TABLE, OBJECT, RECORD, INDEX))
    claim(last + 1, len(b), BULK)

    c = Counter(owner)
    total = len(b)
    print("blob %d bytes" % total)
    print(
        "nodes: %d tables, %d objects, %d record indexes" % (tables, objects, records)
    )
    for k in sorted(LABEL):
        if c[k]:
            print("  %-38s %9d  %6.2f%%" % (LABEL[k], c[k], 100 * c[k] / total))
    print("  %-38s %9d  %6.2f%%" % ("UNCLAIMED", c[0], 100 * c[0] / total))

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
