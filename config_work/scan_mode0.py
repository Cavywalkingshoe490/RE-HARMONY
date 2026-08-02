#!/usr/bin/env python3
"""Scan for MODE 0 images: <1-byte header><RLE stream>, no width/height in
the header (the header is an opaque byte, per the decoder at 0x022F9A: it is
read once with cfg_getbyte, stashed in RAM[0xD2E], and simply returned to the
caller -- never consulted by the parsing loop itself).

This reuses rle.decode() unmodified (imported, not edited) as the sole
grammar validator: byte 0 is skipped as the header, and everything from
offset+1 has to fully self-terminate as one legal RLE image under exactly
the same rules already validated for mode 1.

Usage:
  python3 scan_mode0.py <blob> --area bitmap|structure|shuffled|all [--minw N]
"""

from __future__ import annotations
import argparse
import pathlib
import random
import rle

STRUCT_END = 0x02D660  # established boundary: no images below this offset


def scan_mode0(
    b: bytes, start: int, end: int, min_w=None, min_h=None, require_hdr_eq_w=False
):
    """Like rle.scan(), but for the 1-byte header. Returns list of
    (offset, header_byte, width, height, size, chained) where chained means
    this hit's start == previous hit's end (back-to-back, no gap).

    require_hdr_eq_w: the specific test, found empirically and confirmed by
    disassembly (the caller adds the returned header byte to its pen-x
    accumulator): a real mode-0 glyph's header equals its own decoded width.
    This is the same kind of redundancy check that made the mode-1 scan
    specific (header predicts decode), just discovered later for mode 0."""
    if min_w is not None:
        old_min_w = rle.MIN_W
        rle.MIN_W = min_w
    if min_h is not None:
        old_min_h = rle.MIN_H
        rle.MIN_H = min_h
    out = []
    o = start
    prev_end = None
    try:
        while o < end - 2:
            hdr = b[o]
            got = rle.decode(b, o + 1, limit=min(2 * 176 * 220 + 4096, end - o))
            if got:
                w, h, rows, dec_end = got
                # rle.decode() declares MIN_H/MAX_H but never actually checks
                # a lower bound on height at the 0x00 terminator -- only an
                # upper one mid-loop. Enforce the floor here instead of
                # patching the module.
                h_ok = (min_h is None or h >= min_h) and h <= rle.MAX_H
                w_ok = (not require_hdr_eq_w) or hdr == w
                if dec_end <= end and h_ok and w_ok:
                    chained = prev_end == o
                    out.append((o, hdr, w, h, dec_end - o, chained))
                    prev_end = dec_end
                    o = dec_end
                    continue
            o += 1
    finally:
        if min_w is not None:
            rle.MIN_W = old_min_w
        if min_h is not None:
            rle.MIN_H = old_min_h
    return out


def report(name, hits, area_bytes):
    total = sum(h[4] for h in hits)
    chains = sum(1 for h in hits if h[5])
    print(
        "%-12s %5d hits  %8d B  (%.3f%% of area)  chained=%d"
        % (name, len(hits), total, 100 * total / area_bytes, chains)
    )
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blob")
    ap.add_argument("--minw", type=int, default=None)
    ap.add_argument("--minh", type=int, default=None)
    ap.add_argument("--show", type=int, default=15)
    a = ap.parse_args()
    b = pathlib.Path(a.blob).read_bytes()

    bitmap = b[STRUCT_END:]
    structure = b[:STRUCT_END]
    rnd = random.Random(1234)
    shuffled = bytearray(b)
    rnd.shuffle(shuffled)
    shuffled = bytes(shuffled)

    print("=== mode-0 scan, min_w=%s min_h=%s ===" % (a.minw, a.minh))
    h_bitmap = report(
        "bitmap", scan_mode0(bitmap, 0, len(bitmap), a.minw, a.minh), len(bitmap)
    )
    h_struct = report(
        "structure",
        scan_mode0(structure, 0, len(structure), a.minw, a.minh),
        len(structure),
    )
    h_shuf = report(
        "shuffled",
        scan_mode0(shuffled, 0, len(shuffled), a.minw, a.minh),
        len(shuffled),
    )

    print()
    print("top hits in bitmap area:")
    for off, hdr, w, h, size, chained in sorted(h_bitmap, key=lambda x: -x[4])[
        : a.show
    ]:
        print(
            "  %#08x  hdr=%3d  %3dx%3d  %5d B  chained=%s"
            % (off + STRUCT_END, hdr, w, h, size, chained)
        )


if __name__ == "__main__":
    main()
