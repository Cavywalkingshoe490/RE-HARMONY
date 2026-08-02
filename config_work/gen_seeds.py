#!/usr/bin/env python3
"""Emit every call target in a Harmony One firmware image, as Ghidra seeds.

Ghidra's auto-analysis finds nothing in this image on its own. There is no
vector table at the architectural reset address, so it has no entry point, so it
disassembles zero bytes. Seeding fixes that, and the seeds are free: a PIC18
CALL or RCALL encodes its destination in the instruction, so scanning for those
two opcodes yields the routine set without needing to understand any of it.

    CALL  1110 1100 s kkkkkkk + 1111 kkkkkkkkkkkk   destination = k * 2
    RCALL 1101 1nnn nnnnnnnn                        destination = PC + 2 + 2n

The result is over-inclusive by design. A byte pattern inside data can look like
a CALL, so some seeds point at nothing. That is the right trade: Ghidra drops a
seed that fails to disassemble, whereas a routine never seeded stays invisible.

Usage:
    python3 gen_seeds.py <firmware.bin> [--out seeds.txt] [--base 0]
"""

from __future__ import annotations

import argparse
import pathlib


def call_targets(fw: bytes) -> dict[int, int]:
    """Map destination -> number of call sites reaching it."""
    hits: dict[int, int] = {}
    for o in range(0, len(fw) - 3, 2):
        word = fw[o] | (fw[o + 1] << 8)
        nxt = fw[o + 2] | (fw[o + 3] << 8)
        if (word & 0xFF00) == 0xEC00 and (nxt & 0xF000) == 0xF000:
            dest = (((nxt & 0xFFF) << 8) | (word & 0xFF)) * 2
            if 0 <= dest < len(fw):
                hits[dest] = hits.get(dest, 0) + 1
        if (word & 0xF800) == 0xD800:
            rel = (word & 0x7FF) - (0x800 if word & 0x400 else 0)
            dest = o + 2 + 2 * rel
            if 0 <= dest < len(fw):
                hits[dest] = hits.get(dest, 0) + 1
        # GOTO is also used as a tail call: "RCALL x ; GOTO y" ends a routine by
        # jumping into another one. Scanning only CALL and RCALL missed 41 entry
        # points that way, and Ghidra never analysed them.
        if (word & 0xFF00) == 0xEF00 and (nxt & 0xF000) == 0xF000:
            dest = (((nxt & 0xFFF) << 8) | (word & 0xFF)) * 2
            if 0 <= dest < len(fw):
                hits[dest] = hits.get(dest, 0) + 1
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("firmware")
    ap.add_argument("--out", default="seeds.txt")
    ap.add_argument("--base", type=lambda x: int(x, 0), default=0)
    a = ap.parse_args()

    fw = pathlib.Path(a.firmware).read_bytes()
    hits = call_targets(fw)
    # most-called first: those are the shared helpers, and seeding them first
    # gives the analyser the widest reach before it starts chasing one-offs
    order = sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))
    lines = ["%#08x %d" % (a.base + dest, n) for dest, n in order]
    pathlib.Path(a.out).write_text("\n".join(lines) + "\n")

    print("%d call targets from %d bytes -> %s" % (len(hits), len(fw), a.out))
    print("top 10 by call count:")
    for dest, n in order[:10]:
        print("  %#08x  called %d times" % (a.base + dest, n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
