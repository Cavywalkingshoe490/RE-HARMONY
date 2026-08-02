#!/usr/bin/env python3
"""Recover whole sprites from the Harmony One config blob.

The bitmap bulk that follows 0x02d660 is a heap of RGB565 sprites stored back to
back, and nothing points at them -- the search for a pointer table into the bulk
came back at exactly the chance level for 3-byte windows, so there is no index to
read. What there is instead is the raster itself: a sprite of width W repeats its
structure every 2*W bytes, and that period is measurable.

So the boundaries are recovered from the pixels. Slide a window, measure the
dominant period at each position, and a sprite is a run of positions that agree
on one period. Where the agreement breaks, one sprite ends and the next begins.
That is why a fixed width renders the bulk as coherent bands separated by noise:
the widths are per sprite, not global. Measured so far: 176 for the full-width
elements, 164 for labelled buttons, and 163, 147 and 126 elsewhere.

Height then comes from extending the run while consecutive rows keep agreeing,
which is what stops a sprite from bleeding into the next one.

Usage:
    python3 sprites.py <blob.bin>                  # list what it finds
    python3 sprites.py <blob.bin> --dump outdir    # write every sprite as PNG
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess

BULK = 0x02D660
MIN_W, MAX_W = 32, 260


def period(b: bytes, off: int, span: int = 6000):
    """Dominant 16-bit period at `off`, and how far it stands above the median."""
    end = min(off + span, len(b) - 2 * MAX_W)
    scores = []
    for w in range(MIN_W, MAX_W):
        st = 2 * w
        hits = sum(
            1 for i in range(off, end, 2) if b[i : i + 2] == b[i + st : i + st + 2]
        )
        scores.append((hits, w))
    scores.sort(reverse=True)
    med = sorted(s for s, _ in scores)[len(scores) // 2]
    return scores[0][1], scores[0][0] / max(med, 1)


def row_match(b: bytes, off: int, w: int) -> float:
    """How much two consecutive rows of width `w` agree at `off`."""
    st = 2 * w
    if off + 2 * st > len(b):
        return 0.0
    same = sum(
        1
        for i in range(0, st, 2)
        if b[off + i : off + i + 2] == b[off + st + i : off + st + i + 2]
    )
    return same / (st // 2)


def find_sprites(b: bytes, step: int = 2048, min_strength: float = 1.3):
    """Group consecutive positions that agree on a width into sprites."""
    marks = []
    for o in range(BULK, len(b) - 2 * MAX_W - 6000, step):
        w, strength = period(b, o)
        marks.append((o, w if strength >= min_strength else None))

    out, run_w, run_start = [], None, None
    for o, w in marks + [(None, None)]:
        if w == run_w and w is not None:
            continue
        if run_w is not None and run_start is not None:
            end = o if o is not None else len(b)
            # trim the tail: stop where consecutive rows stop agreeing
            y = run_start
            last = y
            while y + 4 * run_w < end:
                if row_match(b, y, run_w) > 0.15:
                    last = y
                y += 2 * run_w
            rows = (last - run_start) // (2 * run_w)
            if rows >= 8:
                out.append((run_start, run_w, rows))
        run_w, run_start = w, o
    return out


def rgb565(v: int):
    r, g, bl = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (bl << 3) | (bl >> 2)


def write_png(b: bytes, off: int, w: int, h: int, path: pathlib.Path) -> None:
    ppm = bytearray(b"P6\n%d %d\n255\n" % (w, h))
    for y in range(h):
        for x in range(w):
            p = off + 2 * (y * w + x)
            ppm += bytes(
                rgb565(b[p] | (b[p + 1] << 8)) if p + 1 < len(b) else (0, 0, 0)
            )
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
    ap.add_argument("--max-rows", type=int, default=400)
    a = ap.parse_args()
    b = pathlib.Path(a.blob).read_bytes()

    sprites = find_sprites(b)
    total = sum(w * h * 2 for _, w, h in sprites)
    print(
        "%d sprites, %d KB of the %d KB bulk (%.0f%%)"
        % (
            len(sprites),
            total // 1024,
            (len(b) - BULK) // 1024,
            100 * total / (len(b) - BULK),
        )
    )
    from collections import Counter

    print("widths:", Counter(w for _, w, _ in sprites).most_common(8))
    for off, w, h in sorted(sprites, key=lambda s: -s[1] * s[2])[:20]:
        print("  %#08x  %3d x %4d px  %6d B" % (off, w, h, w * h * 2))

    if a.dump:
        out = pathlib.Path(a.dump)
        out.mkdir(parents=True, exist_ok=True)
        for off, w, h in sprites:
            write_png(
                b, off, w, min(h, a.max_rows), out / ("%06x_%dx%d.png" % (off, w, h))
            )
        print("wrote %d PNGs to %s" % (len(sprites), out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
