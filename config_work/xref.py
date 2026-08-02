#!/usr/bin/env python3
"""Find which firmware routine reads which flash address.

Naming routines one at a time by reading their disassembly does not scale: 466
were bounded and 58 named. What was missing is cross references. On PIC18 a
flash read has to go through TBLPTR, and loading TBLPTR from a constant is a
three-instruction idiom that can be recognised without understanding anything
around it:

    MOVLW  <low>    0x0Ekk      then  MOVWF TBLPTRL   0x6EF6
    MOVLW  <high>   0x0Ekk      then  MOVWF TBLPTRH   0x6EF7
    MOVLW  <upper>  0x0Ekk      then  MOVWF TBLPTRU   0x6EF8

The three pairs appear close together and in any order, so the scan collects
them in a window and combines whichever bytes it saw. A partial load -- only
TBLPTRL and TBLPTRH, with the upper byte left over from a previous read -- still
yields the low 16 bits, which is usually enough to identify the target.

That turns "which routine draws the error message" from a reading exercise into
a lookup: the bitmap is at 0x02B2C0, so whoever loads 0x02B2C0 into TBLPTR is
the drawing routine.

Usage:
    python3 xref.py <firmware.bin> [--base 0] [--to 0xADDR] [--window 24]
"""

from __future__ import annotations

import argparse
import pathlib
from collections import defaultdict

import gen_seeds

# MOVWF <sfr>, ACCESS -- the destination halves of the idiom
TBL = {0x6EF6: 0, 0x6EF7: 1, 0x6EF8: 2}
NAME = {0: "TBLPTRL", 1: "TBLPTRH", 2: "TBLPTRU"}


def loads(fw: bytes, window: int = 24):
    """Yield (site, address, mask) for every TBLPTR constant load.

    mask says which of the three bytes were actually written at this site: 0b011
    means low and high only, so the address is known modulo 64 KiB.
    """
    for o in range(0, len(fw) - 3, 2):
        w = fw[o] | (fw[o + 1] << 8)
        n = fw[o + 2] | (fw[o + 3] << 8)
        if (w & 0xFF00) != 0x0E00 or n not in TBL:
            continue
        # anchor on the first pair, then sweep forward for the other two
        byts, mask, start = [0, 0, 0], 0, o
        p = o
        while p < min(o + 2 * window, len(fw) - 3):
            w2 = fw[p] | (fw[p + 1] << 8)
            n2 = fw[p + 2] | (fw[p + 3] << 8)
            if (w2 & 0xFF00) == 0x0E00 and n2 in TBL:
                k = TBL[n2]
                if mask & (1 << k):  # a second write to the same half
                    break  # is a different load; stop here
                byts[k] = w2 & 0xFF
                mask |= 1 << k
                p += 4
                continue
            p += 2
        if mask & 1:  # a load with no low byte is not one
            yield start, byts[0] | (byts[1] << 8) | (byts[2] << 16), mask


def entries(fw):
    """Routine entry points, including code no CALL ever names.

    call_targets() finds destinations of CALL, RCALL and GOTO. That misses
    interrupt service routines completely: hardware reaches them through the
    vectors at 0x0008 and 0x0018, and in this dump 0x000000-0x00001F is
    unreadable garbage (0x67 repeated), so the vectors cannot be followed.

    The consequence was a wrong call graph, not a missing one. owner() maps a
    site to the nearest preceding entry, so every call made from inside an ISR
    was credited to whatever ordinary routine happened to sit above it. That is
    how "main -> 0x026F02 -> 0x0207F4 -> 0x027832" got asserted when 0x0207F4 is
    a four-instruction stub that calls nobody.

    So this adds the starts of orphan blocks: code that follows a RETURN,
    RETFIE or RETLW and is never named as a destination. Those are entered some
    other way -- by a vector, or by an indexed jump.
    """
    named = set(gen_seeds.call_targets(fw))
    out = set(named)
    ended = True
    for o in range(0, len(fw) - 1, 2):
        w = fw[o] | (fw[o + 1] << 8)
        if ended and w not in (0xFFFF, 0x0000) and o not in named:
            out.add(o)
        ended = w in (0x0012, 0x0013, 0x0010, 0x0011) or (w & 0xFF00) == 0x0C00
    return out


def owner_of(site, starts, fw):
    """The routine containing a site, or None when that cannot be decided.

    Returning the nearest preceding entry unconditionally is what produced the
    false edges. If a RETURN or RETFIE sits between the entry and the site, the
    routine visibly ended before it and the site belongs to something else, so
    this says None rather than guessing.
    """
    import bisect

    i = bisect.bisect_right(starts, site)
    if not i:
        return None
    start = starts[i - 1]
    for o in range(start, site, 2):
        w = fw[o] | (fw[o + 1] << 8)
        if w in (0x0012, 0x0013, 0x0010, 0x0011):
            return None
    return start


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("firmware")
    ap.add_argument("--base", type=lambda x: int(x, 0), default=0)
    ap.add_argument("--to", type=lambda x: int(x, 0))
    ap.add_argument("--window", type=int, default=24)
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()

    fw = pathlib.Path(a.firmware).read_bytes()
    starts = sorted(entries(fw))

    def owner(site):
        return owner_of(site, starts, fw)

    by_target = defaultdict(list)
    by_func = defaultdict(list)
    for site, addr, mask in loads(fw, a.window):
        f = owner(site)
        by_target[addr].append((f, site, mask))
        by_func[f].append((addr, site, mask))

    print(
        "%d TBLPTR constant loads in %d routines"
        % (sum(len(v) for v in by_target.values()), len(by_func))
    )

    if a.to is not None:
        want = a.to - a.base
        print("\nreaders of %#08x:" % a.to)
        hits = 0
        for addr, refs in sorted(by_target.items()):
            # a routine that walks a table loads its base, so accept a window
            if not 0 <= want - addr < 0x400:
                continue
            for f, site, mask in refs:
                print(
                    "  %#08x  in routine %#08x   (+%d, mask %s)"
                    % (
                        addr + a.base,
                        (f or 0) + a.base,
                        want - addr,
                        format(mask, "03b"),
                    )
                )
                hits += 1
        if not hits:
            print("  none")
        return 0

    print("\nmost-referenced targets:")
    for addr, refs in sorted(by_target.items(), key=lambda kv: -len(kv[1]))[: a.top]:
        who = sorted({f for f, _, _ in refs})
        print(
            "  %#08x   %2d refs   from %s%s"
            % (
                addr + a.base,
                len(refs),
                " ".join("%#08x" % ((f or 0) + a.base) for f in who[:4]),
                " ..." if len(who) > 4 else "",
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
