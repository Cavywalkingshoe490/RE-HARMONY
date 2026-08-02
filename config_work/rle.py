#!/usr/bin/env python3
"""Decode the Harmony One sprite format, and find sprites by decoding.

The format was read off the decoder at 0x022F9A. It is a byte-oriented run
encoding with transparency, and no repeat runs at all:

    <u16 width><u16 height>    header, 4 bytes -- exactly what mode 1 skips
    0x00          end of image
    0x80          end of row: x back to the left edge, y down one
    0x01..0x7F    literal run: that many pixels follow, two bytes each
    0x81..0xFF    skip run: advance x by (b & 0x7F) and draw nothing

The firmware never decompresses into a buffer. A literal run is handed straight
to lcd_blit_row, which enables the flash-to-panel passthrough, so the pixels
travel from flash to the display without the MCU touching them. A skip run just
advances the cursor. That is why the drawing primitive looks uncompressed while
the stored format is not.

This also settles how to find sprites, which statistics could not do. A run of
bytes either decodes into a rectangle -- every row the same width, terminated --
or it does not. Decoding is the segmentation.

Usage:
    python3 rle.py <blob.bin> [--scan] [--at 0xADDR] [--png out.png]
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess

MIN_W, MAX_W = 8, 176  # the panel is 176 x 220, nothing can exceed it
MIN_H, MAX_H = 4, 220


def decode(b: bytes, at: int, limit: int = 200000):
    """Decode one sprite. Returns (width, height, rows, end) or None.

    rows is a list of runs per row: (kind, count, data_offset) with kind 'lit'
    or 'skip'. Width is taken from the first row and every later row has to
    agree, which is what makes a false start fail fast.
    """
    o, rows, row, x, width = at, [], [], 0, None
    while o < min(at + limit, len(b)):
        c = b[o]
        o += 1
        if c == 0x00:
            if row:
                rows.append(row)
            if width is None or not rows:
                return None
            return width, len(rows), rows, o
        if c == 0x80:
            if width is None:
                width = x
            elif x != width:
                return None
            if not MIN_W <= width <= MAX_W:
                return None
            rows.append(row)
            row, x = [], 0
            if len(rows) > MAX_H:
                return None
            continue
        n = c & 0x7F
        if n == 0:
            return None
        if c & 0x80:
            row.append(("skip", n, None))
        else:
            if o + 2 * n > len(b):
                return None
            row.append(("lit", n, o))
            o += 2 * n
        x += n
        if width is not None and x > width:
            return None
        if x > MAX_W:
            return None
    return None


def render(b: bytes, at: int, path: pathlib.Path, bg=(255, 0, 255)):
    got = decode(b, at)
    if not got:
        return None
    w, h, rows, _ = got
    img = [[bg] * w for _ in range(h)]
    for y, row in enumerate(rows):
        x = 0
        for kind, n, src in row:
            if kind == "lit":
                for k in range(n):
                    # RGB565 BIG ENDIAN: the passthrough hands bytes to the
                    # LCD data port in order, and both controllers take the
                    # high byte first. Decoding little endian turns a parrot
                    # into noise, which is exactly what it did.
                    v = (b[src + 2 * k] << 8) | b[src + 2 * k + 1]
                    r, g, bl = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
                    img[y][x + k] = (
                        (r << 3) | (r >> 2),
                        (g << 2) | (g >> 4),
                        (bl << 3) | (bl >> 2),
                    )
            x += n
    ppm = bytearray(b"P6\n%d %d\n255\n" % (w, h))
    for r in img:
        for p in r:
            ppm += bytes(p)
    tmp = path.with_suffix(".ppm")
    tmp.write_bytes(ppm)
    subprocess.run(
        ["sips", "-s", "format", "png", str(tmp), "--out", str(path)],
        capture_output=True,
    )
    tmp.unlink(missing_ok=True)
    return w, h


def scan(b: bytes, start: int = 0):
    """Find images by the header agreeing with the decode.

    Accepting any self-consistent decode does not work: run it over the blob's
    structure region, which holds no images at all, and it reports 84 of them --
    a higher density than the bitmap region's 15. A mostly-zero area with sparse
    non-zero bytes reads as skip runs plus literal runs of black.

    What is specific is requiring the 4-byte header to predict the decode. The
    header says 62 x 42 and the stream has to decode to exactly 62 x 42. On the
    structure region that yields zero.
    """
    out, o = [], start
    while o < len(b) - 8:
        w = b[o] | (b[o + 1] << 8)
        h = b[o + 2] | (b[o + 3] << 8)
        if MIN_W <= w <= MAX_W and MIN_H <= h <= MAX_H:
            got = decode(b, o + 4, limit=2 * w * h + 4096)
            if got and got[0] == w and got[1] == h:
                lit = sum(n for row in got[2] for k, n, _ in row if k == "lit")
                out.append((o, w, h, got[3] - o, lit))
                o = got[3]
                continue
        o += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blob")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--at", type=lambda x: int(x, 0))
    ap.add_argument("--png")
    ap.add_argument("--start", type=lambda x: int(x, 0), default=0)
    a = ap.parse_args()
    b = pathlib.Path(a.blob).read_bytes()

    if a.at is not None:
        got = decode(b, a.at)
        print(
            "%#08x -> %s"
            % (
                a.at,
                "does not decode"
                if not got
                else "%d x %d px, %d bytes" % (got[0], got[1], got[3] - a.at),
            )
        )
        if got and a.png:
            print("render:", render(b, a.at, pathlib.Path(a.png)))
        return 0

    if a.scan:
        found = scan(b, a.start)
        total = sum(f[3] for f in found)
        print(
            "%d sprites, %d bytes (%.1f%% of the blob)"
            % (len(found), total, 100 * total / len(b))
        )
        for off, w, h, size, lit in sorted(found, key=lambda f: -f[3])[:25]:
            print(
                "  %#08x  %3d x %3d px  %7d B  %6d px literal (%.0f%% opaque)"
                % (off, w, h, size, lit, 100 * lit / max(1, w * h))
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
