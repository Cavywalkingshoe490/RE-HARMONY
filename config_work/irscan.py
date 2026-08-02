#!/usr/bin/env python3
"""Find and decode the IR waveforms stored in a Harmony One config blob.

The codes sit in the blob in exactly the format the firmware plays back.
`play_seq` at flash 0x02DA78 reads 16-bit little-endian words from RAM 0x0500
and treats bit 15 as the carrier gate and bits 0..14 as a duration, so a stored
code is a plain list of marks and spaces in microseconds.

Finding them does not need pattern matching. Every waveform starts with the
word 0x7FFF -- a 32767 us space -- and the config reaches it through a one-entry
pointer table, the byte 0x01 followed by a ptr24. Those two facts together are
specific enough to enumerate the codes structurally:

    01 a9 26 04        <count=1> <ptr24 -> flash 0x0426a9>
    7fff 4351          32767 + 17233 = 50 ms lead-in, split because a word
                       only holds 15 bits once bit 15 is the carrier gate
    <header> <bits> <trailer> <gap> <repeat frame>

An earlier version of this file looked for runs of words whose bit 15 alternates.
That misses the head of every sequence: the lead-in is two *consecutive* spaces,
which breaks the alternation, so the scan started two bytes late. It also found
nothing but Sony, because the decoder only knew Sony. Both are fixed here.

Two encodings appear in this blob, and both are confirmed against an independent
oracle -- the same user's Harmony Hub, whose config lists the codes in clear:

    header mark 2400, no header space   Sony SIRC, bit is in the MARK length
    header mark 8990, space 4490        NEC/Toshiba, bit is in the SPACE length

Usage:
    python3 irscan.py <blob.bin>                 # list the waveforms
    python3 irscan.py <blob.bin> --decode        # decode and group by payload
    python3 irscan.py <blob.bin> --at 0x26a9     # dump one waveform
"""

from __future__ import annotations

import argparse
import pathlib
from collections import Counter

BASE = 0x040000
LEAD_IN = 0x7FFF


def find_waveforms(b: bytes):
    """Yield the start offset of every waveform the config points at."""
    out = set()
    for o in range(1, len(b) - 3):
        if b[o - 1] != 1 or not 0x04 <= b[o + 2] <= 0x18:
            continue
        p = (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)) - BASE
        if 0 <= p < len(b) - 1 and (b[p] | (b[p + 1] << 8)) == LEAD_IN:
            out.add(p)
    return sorted(out)


def read_waveform(b: bytes, at: int, cap: int = 600):
    """Read one waveform; it ends at two consecutive spaces, the last one short."""
    out = []
    o = at
    while o + 1 < len(b) and len(out) < cap:
        w = b[o] | (b[o + 1] << 8)
        out.append(w)
        o += 2
        if (
            len(out) > 8
            and not w & 0x8000
            and not out[-2] & 0x8000
            and (w & 0x7FFF) < 200
        ):
            break
    return out


def _header(words):
    """Return (index of header mark, mark us, following space us or None)."""
    i = 0
    while i < len(words) and not words[i] & 0x8000:
        i += 1
    if i >= len(words):
        return None
    space = None
    if i + 1 < len(words) and not words[i + 1] & 0x8000:
        space = words[i + 1] & 0x7FFF
    return i, words[i] & 0x7FFF, space


def decode(words):
    """Decode one waveform to (protocol, bit count, payload) or None.

    The payload is the on-air bit string read in transmission order as
    big-endian, first bit sent being the most significant. That convention is
    not assumed: it is what reproduces the Hub's codes, 145/145 for Sony and
    50/50 for Toshiba, against 2/145 for the opposite order.
    """
    h = _header(words)
    if not h:
        return None
    i, mark, space = h

    if 2200 <= mark <= 2600:  # Sony SIRC: the bit is the mark length
        i += 1
        bits = []
        while i + 1 < len(words):
            if words[i] & 0x8000 or not words[i + 1] & 0x8000:
                break
            m = words[i + 1] & 0x7FFF
            if 1000 <= m <= 1400:
                bits.append(1)
            elif 450 <= m <= 800:
                bits.append(0)
            else:
                break
            i += 2
        if len(bits) not in (12, 15, 20):
            return None
        proto = "Sony %d Bit" % len(bits)

    elif 8000 <= mark <= 9500 and space and 4000 <= space <= 5000:
        # NEC family: fixed mark, the bit is the space length
        i += 2
        spaces = []
        while i + 1 < len(words) and words[i] & 0x8000 and not words[i + 1] & 0x8000:
            s = words[i + 1] & 0x7FFF
            if s > 5000:  # the inter-frame gap, payload is over
                break
            spaces.append(s)
            i += 2
        if len(spaces) < 16:
            return None
        lo, hi = min(spaces), max(spaces)
        if hi < 2 * lo:
            return None
        mid = (lo + hi) / 2
        bits = [1 if s > mid else 0 for s in spaces]
        proto = "Toshiba %d Bit" % len(bits)

    else:
        return None

    value = 0
    for bit in bits:
        value = (value << 1) | bit
    return proto, len(bits), value


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blob")
    ap.add_argument("--decode", action="store_true")
    ap.add_argument("--at", type=lambda x: int(x, 0))
    a = ap.parse_args()
    b = pathlib.Path(a.blob).read_bytes()

    if a.at is not None:
        w = read_waveform(b, a.at)
        print("waveform at %#08x, %d words -> %s" % (a.at, len(w), decode(w)))
        for k, x in enumerate(w):
            print(
                "  %3d  %s %6d us" % (k, "mark " if x & 0x8000 else "space", x & 0x7FFF)
            )
        return 0

    starts = find_waveforms(b)
    print("%d waveforms reached through one-entry pointer tables" % len(starts))
    if not a.decode:
        for p in starts[:40]:
            w = read_waveform(b, p)
            print("  %#08x  %3d words  %s" % (p, len(w), decode(w)))
        return 0

    codes, kinds, bad = {}, Counter(), 0
    for p in starts:
        r = decode(read_waveform(b, p))
        if not r:
            bad += 1
            continue
        proto, _, value = r
        kinds[proto] += 1
        codes.setdefault((proto, value), []).append(p)
    print("decoded by protocol:", dict(kinds), "  undecoded:", bad)
    print("\n%d distinct (protocol, payload):" % len(codes))
    for (proto, val), where in sorted(codes.items()):
        print("  %-16s %#010x  x%-2d  first %#08x" % (proto, val, len(where), where[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
