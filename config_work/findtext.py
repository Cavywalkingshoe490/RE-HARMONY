#!/usr/bin/env python3
"""Locate label sprites in the Harmony One blob by looking for the text itself.

Autocorrelation finds the width of a large textured sprite, but it fails on the
small ones: below roughly 100 px the short periods always score highest and win,
so a 133x21 label comes back with a width that renders sheared. Every label-sized
sprite the generic detector produced was wrong that way.

Text gives a better handle than periodicity. Light glyphs on a dark button are
short runs of bright pixels separated by dark, and -- this is the part that
identifies the width -- the glyph strokes are *vertical*, so at the correct width
the bright pixels stack into columns and at any other width they smear diagonally.
So the width is whatever maximises column alignment of bright pixels, which is a
direct measurement of the thing that makes text readable.

    brightness  RGB565 with all three channels in the top half
    candidate   a window where bright pixels are 2-25% of the area
                (below that there is no text, above it the region is not a button)
    width       argmax over 40..200 of how concentrated the bright pixels are
                into few columns

Usage:
    python3 findtext.py <blob.bin>
    python3 findtext.py <blob.bin> --dump outdir --zoom 4
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess

BULK = 0x02D660
MIN_W, MAX_W = 40, 200


def bright(v: int) -> bool:
    return ((v >> 11) & 0x1F) >= 18 and ((v >> 5) & 0x3F) >= 36 and (v & 0x1F) >= 18


def bright_mask(b: bytes, off: int, count: int):
    return [bright(b[off + 2 * i] | (b[off + 2 * i + 1] << 8)) for i in range(count)]


def column_score(mask, w: int) -> float:
    """How concentrated the bright pixels are into columns at this width.

    Text at the right width stacks strokes vertically, so a few columns hold
    most of the bright pixels. At a wrong width the same pixels spread evenly.
    Comparing the top-quarter of columns against the total measures exactly that,
    and normalising by w keeps wide guesses from winning for free.
    """
    rows = len(mask) // w
    if rows < 6:
        return 0.0
    cols = [0] * w
    for r in range(rows):
        base = r * w
        for c in range(w):
            if mask[base + c]:
                cols[c] += 1
    total = sum(cols)
    if total < 20:
        return 0.0
    cols.sort(reverse=True)
    return sum(cols[: max(1, w // 4)]) / total


def scan(b: bytes, win_px: int = 4000, step: int = 1024):
    out = []
    o = BULK
    while o + 2 * win_px < len(b):
        mask = bright_mask(b, o, win_px)
        frac = sum(mask) / win_px
        if 0.02 <= frac <= 0.25:
            best_w, best_s = 0, 0.0
            for w in range(MIN_W, MAX_W):
                s = column_score(mask, w)
                if s > best_s:
                    best_w, best_s = w, s
            if best_w:
                out.append((best_s, best_w, o, frac))
        o += step * 2
    return out


def rgb565(v: int):
    r, g, bl = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (bl << 3) | (bl >> 2)


def write_png(b: bytes, off: int, w: int, h: int, z: int, path: pathlib.Path):
    ppm = bytearray(b"P6\n%d %d\n255\n" % (w * z, h * z))
    for y in range(h):
        row = bytearray()
        for x in range(w):
            p = off + 2 * (y * w + x)
            row += (
                bytes(rgb565(b[p] | (b[p + 1] << 8)) if p + 1 < len(b) else (0, 0, 0))
                * z
            )
        ppm += row * z
    tmp = path.with_suffix(".ppm")
    tmp.write_bytes(ppm)
    subprocess.run(
        ["sips", "-s", "format", "png", str(tmp), "--out", str(path)],
        capture_output=True,
    )
    tmp.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blob")
    ap.add_argument("--dump")
    ap.add_argument("--zoom", type=int, default=4)
    ap.add_argument("--top", type=int, default=16)
    a = ap.parse_args()
    b = pathlib.Path(a.blob).read_bytes()

    hits = sorted(scan(b), reverse=True)
    print("%d windows with a text-like bright-pixel fraction" % len(hits))
    for s, w, o, frac in hits[: a.top]:
        print(
            "  %#08x  width %3d  column score %.2f  bright %.1f%%"
            % (o, w, s, 100 * frac)
        )

    if a.dump:
        out = pathlib.Path(a.dump)
        out.mkdir(parents=True, exist_ok=True)
        for s, w, o, _ in hits[: a.top]:
            write_png(
                b, o, w, min(4000 // w, 60), a.zoom, out / ("%06x_w%d.png" % (o, w))
            )
        print("wrote %d PNGs to %s" % (min(len(hits), a.top), out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
