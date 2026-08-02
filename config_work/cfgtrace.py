#!/usr/bin/env python3
"""Read the config record layout off the firmware instead of guessing it.

Six statistical attempts failed to find the grammar of the section [6] records,
and every one of them was trying to infer field widths from the bytes. The
widths are not in the bytes. They are in the code: the firmware reaches a field
by advancing the cursor a literal number of bytes and then calling a reader that
consumes a known width. So the accessor declares the layout.

    MOVLW 4 ; MOVWF [0x1EF] ; CALL cfg_ptr_add     skip 4
    CALL cfg_get_u16                               read 2

reads a u16 at offset +4. That is a fact about the format, obtained without
looking at a single data byte.

This walks each routine, carries the literals written to the argument registers,
and prints the resulting sequence of operations per routine.

    0x1F1  section number for cfg_section_ptr
    0x1EF  byte count for cfg_ptr_add
    0x1F2, 0x1F3  cfg_ptr_advance moves 2*[0x1F3] + [0x1F2]

Usage:
    python3 cfgtrace.py <firmware.bin> [--routine 0xADDR] [--min 2]
"""

from __future__ import annotations

import argparse
import pathlib

import pic18dis
import xref

PRIM = {
    0x02B8F8: ("getbyte", 1),
    0x02B90A: ("get_u16", 2),
    0x02B93C: ("get_u24", 3),
    0x02B98C: ("get_u24_b", 3),
    0x02E70A: ("read_u16", 2),
    0x02B8AC: ("follow_ptr", 0),
    0x02BA14: ("ptr_add", 0),
    0x02BA90: ("ptr_advance", 0),
    0x02BA76: ("section_ptr", 0),
    0x02B88A: ("ptr_to_logical", 0),
}
ARGS = {0x1F1: "sec", 0x1EF: "add", 0x1F2: "advlo", 0x1F3: "advhi"}


def trace(fw: bytes, start: int, limit: int = 0x400):
    """Yield (site, text) for the config operations a routine performs."""
    lit, bsr, out, off = {}, None, [], 0
    o = start
    while o < min(start + limit, len(fw) - 1):
        w = fw[o] | (fw[o + 1] << 8)
        txt, size = pic18dis.decode(fw, o)
        if (w & 0xFF00) == 0x0100:
            bsr = w & 0x0F
        elif (w & 0xFF00) == 0x0E00:
            lit["W"] = w & 0xFF
        elif (w & 0xFE00) == 0x6E00 and bsr == 1:
            reg = 0x100 | (w & 0xFF)
            if reg in ARGS and "W" in lit:
                lit[ARGS[reg]] = lit["W"]
        elif txt.startswith(("RETURN", "RETLW", "RETFIE")):
            break
        else:
            dest = None
            if (w & 0xFF00) == 0xEC00:
                n = fw[o + 2] | (fw[o + 3] << 8)
                if (n & 0xF000) == 0xF000:
                    dest = (((n & 0xFFF) << 8) | (w & 0xFF)) * 2
            elif (w & 0xF800) == 0xD800:
                rel = (w & 0x7FF) - (0x800 if w & 0x400 else 0)
                dest = o + 2 + 2 * rel
            if dest in PRIM:
                name, width = PRIM[dest]
                if name == "ptr_add":
                    k = lit.get("add")
                    out.append((o, "salta %s" % (k if k is not None else "?")))
                    if k is not None:
                        off += k
                elif name == "ptr_advance":
                    hi, lo = lit.get("advhi"), lit.get("advlo", 0)
                    k = None if hi is None else 2 * hi + lo
                    out.append(
                        (o, "salta %s (2*hi+lo)" % (k if k is not None else "?"))
                    )
                    if k is not None:
                        off += k
                elif name == "section_ptr":
                    out.append((o, "seccion [%s]" % lit.get("sec", "?")))
                    off = 0
                elif width:
                    out.append((o, "+%-4d lee %s (%d B)" % (off, name, width)))
                    off += width
                else:
                    out.append((o, name))
        o += 2 * size
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("firmware")
    ap.add_argument("--routine", type=lambda x: int(x, 0))
    ap.add_argument("--min", type=int, default=3)
    a = ap.parse_args()
    fw = pathlib.Path(a.firmware).read_bytes()

    starts = [a.routine] if a.routine else sorted(xref.entries(fw))
    shown = 0
    for s in starts:
        if not 0x020000 <= s < 0x030000:  # only where we know it is code
            continue
        ops = trace(fw, s)
        if len(ops) < a.min:
            continue
        shown += 1
        print("=== %#08x ===" % s)
        for site, txt in ops:
            print("   %#08x  %s" % (site, txt))
        print()
    print("%d routines with at least %d config operations" % (shown, a.min))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
