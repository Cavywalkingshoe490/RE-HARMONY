#!/usr/bin/env python3
"""Object model of the Harmony One (arch 12) configuration blob.

The blob is not a database, it is a heap of small objects plus a master
pointer table. Everything else in the file -- the master index at offset 0,
the record index, sections [9] and [10] -- is scaffolding around that heap.

    section [11]   u16 count = 2904, then 2904 x ptr24   <- master table
    object         <count:u8> + count x atom
    atom           <operand:u16 LE> <op:u8>

The reading is forced by the data, not chosen: every gap between consecutive
master-table pointers is 7 + 3k bytes, which is exactly what `1 + 3*count`
produces for an object holding `count` atoms of three bytes with two atoms of
slack -- and the table is strictly ascending with all 2904 targets inside the
blob.

Atoms whose op is a reference carry an operand that is a master-table index.
`classify()` measures that per op instead of assuming it: an op is called a
reference only when every one of its operands lands inside 0..2903, which is a
strong signal because the observed operand ranges are far wider than the table
(op 0x75 alone reaches 18020).

Usage:
    python3 objects.py <blob.bin> ops        # opcode census
    python3 objects.py <blob.bin> map        # byte accounting of the whole file
    python3 objects.py <blob.bin> tree N     # expand object N recursively
    python3 objects.py <blob.bin> commands   # records -> [9] -> objects
"""

from __future__ import annotations

import argparse
import pathlib
from collections import Counter

BASE = 0x040000
SEC6 = 0x01C699
SEC9 = (0x0291E7, 0x029CAF)
SEC11 = 0x02B1AA


def u24(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


class Blob:
    def __init__(self, data: bytes):
        self.b = data
        n = data[SEC11] | (data[SEC11 + 1] << 8)
        self.ptr = [u24(data, SEC11 + 2 + 3 * i) - BASE for i in range(n)]
        self.objects = [self._read(p) for p in self.ptr]

    def _read(self, at: int) -> list[tuple[int, int]]:
        c = self.b[at]
        return [
            (
                self.b[at + 1 + 3 * i] | (self.b[at + 2 + 3 * i] << 8),
                self.b[at + 3 + 3 * i],
            )
            for i in range(c)
        ]

    def atoms(self):
        for i, o in enumerate(self.objects):
            for v, op in o:
                yield i, v, op

    def classify(self) -> dict[int, str]:
        """Label each op 'ref' when all its operands are valid object indices."""
        per = {}
        for _, v, op in self.atoms():
            per.setdefault(op, []).append(v)
        n = len(self.objects)
        return {
            op: "ref" if all(0 <= v < n for v in vs) else "literal"
            for op, vs in per.items()
        }

    def sec9(self) -> dict[int, list[tuple[int, int, int]]]:
        o, end = SEC9
        out = {}
        while o < end:
            c = self.b[o]
            out[o] = [
                (
                    self.b[o + 1 + 4 * i],
                    self.b[o + 2 + 4 * i] | (self.b[o + 3 + 4 * i] << 8),
                    self.b[o + 4 + 4 * i],
                )
                for i in range(c)
            ]
            o += 1 + 4 * c
        return out

    def records(self) -> list[tuple[int, int]]:
        n = u24(self.b, SEC6)
        p = [u24(self.b, SEC6 + 3 + 3 * i) - BASE for i in range(n)]
        return [(x, p[i + 1] - x if i + 1 < n else 0) for i, x in enumerate(p)]


def signed(v: int) -> int:
    return v - 0x10000 if v > 0x8000 else v


def show(bl: Blob, i: int, kind: dict[int, str], depth: int, seen: set[int]) -> None:
    pad = "  " * depth
    if i in seen:
        print("%s#%d ..." % (pad, i))
        return
    seen.add(i)
    body = bl.objects[i]
    head = " ".join(
        ("#%d" % v) if kind.get(op) == "ref" else ("%02x=%d" % (op, signed(v)))
        for v, op in body
    )
    print("%s#%-5d %s" % (pad, i, head))
    if depth < 4:
        for v, op in body:
            if kind.get(op) == "ref":
                show(bl, v, kind, depth + 1, seen)


def cmd_ops(bl: Blob) -> None:
    kind = bl.classify()
    per = {}
    for _, v, op in bl.atoms():
        per.setdefault(op, []).append(v)
    print("objects %d   atoms %d" % (len(bl.objects), sum(len(o) for o in bl.objects)))
    for op, vs in sorted(per.items(), key=lambda kv: -len(kv[1])):
        print(
            "  op %#04x  %-8s n=%5d  operand %7d..%7d  distinct %5d"
            % (op, kind[op], len(vs), min(vs), max(vs), len(set(vs)))
        )


def cmd_map(bl: Blob) -> None:
    b = bl.b
    owner = bytearray(len(b))  # 0 unclaimed
    tags = {
        1: "header + button map",
        2: "records",
        3: "object heap",
        4: "sections [0]-[8]",
        5: "master pointer table [11]",
        6: "sections [12]-[18]",
        7: "graphics [19]",
    }

    def claim(a, z, t):
        for k in range(max(a, 0), min(z, len(b))):
            owner[k] = t

    claim(0, 0x18D, 1)
    for off, size in bl.records():
        if 0 < size < 400:
            claim(off, off + size, 2)
    for i, p in enumerate(bl.ptr):
        claim(p, p + 1 + 3 * len(bl.objects[i]), 3)
    claim(0x01C23E, 0x0291E7, 4)
    claim(*SEC9, 4)
    claim(0x029CAF, SEC11, 4)
    claim(SEC11, 0x02D3B4, 5)
    claim(0x02D3B4, 0x02D5FC, 6)
    claim(0x02D5FC, len(b), 7)

    c = Counter(owner)
    total = len(b)
    print("blob %d bytes" % total)
    for t in sorted(tags):
        if c[t]:
            print("  %-26s %9d  %5.2f%%" % (tags[t], c[t], 100 * c[t] / total))
    print("  %-26s %9d  %5.2f%%" % ("UNCLAIMED", c[0], 100 * c[0] / total))
    if c[0]:
        runs, start = [], None
        for k in range(total + 1):
            here = k < total and owner[k] == 0
            if here and start is None:
                start = k
            elif not here and start is not None:
                runs.append((start, k))
                start = None
        runs.sort(key=lambda r: r[1] - r[0], reverse=True)
        print("  largest unclaimed runs:")
        for a, z in runs[:8]:
            print("    %#08x .. %#08x  %d B" % (a, z, z - a))


def cmd_commands(bl: Blob) -> None:
    kind = bl.classify()
    s9 = bl.sec9()
    shown = 0
    for off, size in bl.records():
        if not 6 < size < 400:
            continue
        g = s9.get(u24(bl.b, off + size - 6) - BASE)
        if not g:
            continue
        shown += 1
        print("\nrecord %#08x (%d B) -> %d slot(s)" % (off, size, len(g)))
        for code, idx, op in g:
            if not 0 <= idx < len(bl.objects):
                print("    code %02x  -> %d (out of table)" % (code, idx))
                continue
            body = bl.objects[idx]
            txt = " ".join(
                ("#%d" % v) if kind.get(o) == "ref" else ("%02x=%d" % (o, signed(v)))
                for v, o in body
            )
            print("    code %02x op %02x  #%-4d  %s" % (code, op, idx, txt))
    print("\n%d records with slots" % shown)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blob")
    ap.add_argument("cmd", choices=["ops", "map", "tree", "commands"])
    ap.add_argument("arg", nargs="?", type=int)
    a = ap.parse_args()
    bl = Blob(pathlib.Path(a.blob).read_bytes())
    if a.cmd == "ops":
        cmd_ops(bl)
    elif a.cmd == "map":
        cmd_map(bl)
    elif a.cmd == "commands":
        cmd_commands(bl)
    else:
        show(bl, a.arg or 0, bl.classify(), 0, set())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
