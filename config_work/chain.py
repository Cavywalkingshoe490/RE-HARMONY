#!/usr/bin/env python3
"""Walk the command chain of the Harmony One (arch 12) configuration blob.

    section [6]        u24 count + count u24 pointers -> 156 records
    record (104 B)     +98: u24 pointer into section [9]
    section [9]        <count:u8> + count x {code:u8, index:u16, tag:u8}
    index - 2316       -> entry of section [10]
    section [10]       <count:u8> + count x {value:u16, op:u8}

Sections [9] and [10] both tile their byte range exactly under those grammars,
which is what makes the reading trustworthy: a wrong element size drifts and
overruns the boundary within a few dozen entries.

The 2316 offset is not a guess either. The tags in [9] that equal 0x7f carry
588 distinct indices forming the contiguous run 2316..2903, and [10] holds 589
entries -- so the indices are global entry numbers and [10] owns the tail of
that numbering.

Usage:
    python3 chain.py <blob.bin>            # whole chain, grouped by device
    python3 chain.py <blob.bin> --ops      # opcode census of section [10]
"""

from __future__ import annotations

import argparse
import pathlib
import re
from collections import Counter

BASE = 0x040000
SEC6 = 0x01C699
SEC9 = (0x0291E7, 0x029CAF)
SEC10 = (0x029CAF, 0x02B1AA)
INDEX_BASE = 2316  # global entry number of section[10] entry 0

NAME_RE = re.compile(rb"^[A-Za-z][A-Za-z0-9_.\- ]*$")


def u24(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


def read_records(b: bytes) -> list[tuple[int, int]]:
    """Return (offset, size) for every record in the section [6] index."""
    n = u24(b, SEC6)
    ptrs = [u24(b, SEC6 + 3 + i * 3) - BASE for i in range(n)]
    return [(p, ptrs[i + 1] - p if i + 1 < n else 0) for i, p in enumerate(ptrs)]


def read_sec9(b: bytes) -> dict[int, list[tuple[int, int, int]]]:
    """Parse section [9] into {offset: [(code, index, tag), ...]}."""
    o, end = SEC9
    out = {}
    while o < end:
        c = b[o]
        out[o] = [
            (
                b[o + 1 + 4 * i],
                b[o + 2 + 4 * i] | (b[o + 3 + 4 * i] << 8),
                b[o + 4 + 4 * i],
            )
            for i in range(c)
        ]
        o += 1 + 4 * c
    return out


def read_sec10(b: bytes) -> list[list[tuple[int, int]]]:
    """Parse section [10] into a list of entries, each a list of (value, op)."""
    o, end = SEC10
    out = []
    while o < end:
        c = b[o]
        out.append(
            [
                (b[o + 1 + 3 * i] | (b[o + 2 + 3 * i] << 8), b[o + 3 + 3 * i])
                for i in range(c)
            ]
        )
        o += 1 + 3 * c
    return out


def record_name(b: bytes, off: int, size: int) -> str:
    """Longest printable run inside a record -- records carry their own label."""
    best = b""
    for m in re.finditer(rb"[ -~]{4,}", b[off : off + max(size, 0)]):
        if len(m.group()) > len(best) and NAME_RE.match(m.group()):
            best = m.group()
    return best.decode("ascii", "replace")


def fmt_atom(value: int, op: int) -> str:
    signed = value - 0x10000 if value > 0x8000 else value
    return "%02x:%d" % (op, signed)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blob")
    ap.add_argument("--ops", action="store_true", help="opcode census only")
    args = ap.parse_args()
    b = pathlib.Path(args.blob).read_bytes()

    sec9 = read_sec9(b)
    sec10 = read_sec10(b)

    if args.ops:
        atoms = [a for e in sec10 for a in e]
        print("section [10]: %d entries, %d atoms" % (len(sec10), len(atoms)))
        for op, n in Counter(o for _, o in atoms).most_common():
            vals = [v for v, o in atoms if o == op]
            print(
                "  op %#04x  n=%4d  value %6d..%6d  distinct %4d"
                % (op, n, min(vals), max(vals), len(set(vals)))
            )
        return 0

    records = read_records(b)
    hits = 0
    for off, size in records:
        if size != 104:
            continue
        target = u24(b, off + 98) - BASE
        group = sec9.get(target)
        if group is None:
            continue
        hits += 1
        print(
            "\n%-28s  record %#08x -> [9] %#08x  (%d)"
            % (record_name(b, off, size), off, target, len(group))
        )
        for code, index, tag in group:
            entry = index - INDEX_BASE
            body = sec10[entry] if 0 <= entry < len(sec10) else None
            shown = (
                " ".join(fmt_atom(v, o) for v, o in body) if body else "<out of range>"
            )
            print("    code %02x  entry %4d  %s" % (code, entry, shown))
    print("\n%d command records resolved" % hits)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
