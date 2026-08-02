#!/usr/bin/env python3
"""Structural parser for the Harmony One (arch 12) configuration blob.

Everything here was recovered by analysis of a real dump; concordance itself
never parses this blob, it only moves it byte-for-byte.

Layout established so far
-------------------------
    +0x0000  header
             +0x00  char[4]  "GSPM"  (cookie 0x4D505347)
             +0x04  u32      end_vector
             +0x08  u32      0x1600 constant
             +0x0C  u32[]    master index: absolute flash pointers, ascending
    +0x0063  "LWJL7" magic, then 4-byte entries {code, index, 0x00, 0x7f}
             with a sequential index -- a button/function map
    +0x018d  records referenced by the section-6 index
    section[0]   A7 records: A7 len:u16 type:u16 id:u16 name[len-4], ends 0xBEEF
    section[6]   u24 count, then count u24 pointers to the records above
    section[19]  RGB565 graphics, 164 px wide (86% of the blob)

Pointers are absolute flash addresses; subtract 0x040000 to index the blob.
"""

from __future__ import annotations

import argparse
import pathlib
import struct
from collections import Counter

BASE = 0x040000
COOKIE = b"GSPM"


def u24(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


def read_header(blob: bytes) -> dict:
    cookie, end_vector, const = struct.unpack_from("<4sII", blob, 0)
    if cookie != COOKIE:
        raise SystemExit("not an arch-12 config blob (cookie %r)" % cookie)
    pointers = []
    off = 0x0C
    while off + 4 <= len(blob):
        value = struct.unpack_from("<I", blob, off)[0]
        if value and not 0 <= value - BASE < len(blob):
            break
        pointers.append(value)
        off += 4
        if len(pointers) >= 32:
            break
    return {"end_vector": end_vector, "const": const, "pointers": pointers}


def read_index(blob: bytes, at: int) -> list[int]:
    """Read a u24 count followed by that many u24 pointers."""
    count = u24(blob, at)
    if not 0 < count < 100000:
        return []
    out = []
    for i in range(count):
        o = at + 3 + i * 3
        if o + 3 > len(blob):
            break
        out.append(u24(blob, o))
    return out


def read_button_map(blob: bytes) -> list[tuple[int, int]]:
    """Parse the 4-byte {code, index, 0x00, 0x7f} entries after the LWJL7 magic."""
    magic = blob.find(b"LWJL7")
    if magic < 0:
        return []
    out = []
    o = magic + 5
    expected = 0
    while o + 4 <= len(blob):
        code, index, z, term = blob[o : o + 4]
        if z != 0x00 or term != 0x7F or index != expected & 0xFF:
            break
        out.append((code, index))
        expected += 1
        o += 4
    return out


def classify(blob: bytes, start: int, size: int) -> str:
    body = blob[start : start + min(size, 8192)]
    if not body:
        return "empty"
    distinct = len(set(body))
    if distinct < 8:
        return "uniform (%d values)" % distinct
    if body[:1] == b"\xa7":
        return "A7 records"
    # 16-bit periodicity with few distinct values = RGB565 graphics
    if size > 100000 and distinct < 128:
        pairs = sum(
            1
            for i in range(0, min(len(body) - 2, 4000), 2)
            if body[i : i + 2] == body[i + 2 : i + 4]
        )
        if pairs > 400:
            return "RGB565 graphics (164 px wide)"
    return "data (%d distinct bytes)" % distinct


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blob")
    args = ap.parse_args()
    blob = pathlib.Path(args.blob).read_bytes()

    head = read_header(blob)
    print("blob            : %d bytes" % len(blob))
    print("end_vector      : %#08x" % head["end_vector"])
    print("constant        : %#08x" % head["const"])

    sections = [(i, p, p - BASE) for i, p in enumerate(head["pointers"]) if p]
    print("master index    : %d sections\n" % len(sections))
    for n, (i, ptr, rel) in enumerate(sections):
        nxt = sections[n + 1][2] if n + 1 < len(sections) else len(blob)
        print(
            "  [%2d] flash %#08x  blob %#08x  %8d B  %s"
            % (i, ptr, rel, nxt - rel, classify(blob, rel, nxt - rel))
        )

    buttons = read_button_map(blob)
    print("\nbutton map      : %d entries" % len(buttons))
    if buttons:
        codes = Counter(c for c, _ in buttons)
        print("  distinct codes: %d" % len(codes))
        print(
            "  first 12      : "
            + ", ".join("idx%02x=code%02x" % (i, c) for c, i in buttons[:12])
        )

    # section 6 is the record index
    for i, ptr, rel in sections:
        idx = read_index(blob, rel)
        if len(idx) > 50 and all(0 <= p - BASE < len(blob) for p in idx):
            sizes = [idx[k + 1] - idx[k] for k in range(len(idx) - 1)]
            print("\nrecord index    : section [%d] at blob %#08x" % (i, rel))
            print("  entries       : %d" % len(idx))
            print("  targets       : %#08x .. %#08x" % (idx[0] - BASE, idx[-1] - BASE))
            print("  common sizes  : %s" % Counter(sizes).most_common(5))
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
