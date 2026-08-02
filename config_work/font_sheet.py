#!/usr/bin/env python3
"""Composes a contact sheet of the 71 codes (highest available representative),
with the code number drawn using a custom 3x5 mini-font (does not depend on
PIL, which is not installed). For manual visual recognition.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import fonts_inventory as fi
import rle

DIGITS = {
    "0": ["111", "101", "101", "101", "111"],
    "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"],
    "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"],
    "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"],
    "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"],
    "9": ["111", "101", "111", "001", "111"],
}


def decode_pixels(b: bytes, file_off: int):
    """-> (w,h, rows-of-rgb) using rle.decode with MIN_W/H relaxed."""
    old_w, old_h = rle.MIN_W, rle.MIN_H
    rle.MIN_W, rle.MIN_H = 1, 1
    try:
        got = rle.decode(b, file_off + 1, limit=4096)
    finally:
        rle.MIN_W, rle.MIN_H = old_w, old_h
    if not got:
        return None
    w, h, rows, _ = got
    img = [[None] * w for _ in range(h)]
    for y, row in enumerate(rows):
        x = 0
        for kind, n, src in row:
            if kind == "lit":
                for k in range(n):
                    v = (b[src + 2 * k] << 8) | b[src + 2 * k + 1]
                    r, g, bl = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
                    img[y][x + k] = (
                        (r << 3) | (r >> 2),
                        (g << 2) | (g >> 4),
                        (bl << 3) | (bl >> 2),
                    )
            x += n
    return w, h, img


def put_digit(canvas, ox, oy, ch, color, scale):
    pat = DIGITS[ch]
    for ry, row in enumerate(pat):
        for rx, v in enumerate(row):
            if v == "1":
                for dy in range(scale):
                    for dx in range(scale):
                        yy, xx = oy + ry * scale + dy, ox + rx * scale + dx
                        if 0 <= yy < len(canvas) and 0 <= xx < len(canvas[0]):
                            canvas[yy][xx] = color


def main():
    blob_path = sys.argv[1] if len(sys.argv) > 1 else "../backups/config_raw.bin"
    out_dir = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "font_sheet_out")
    out_dir.mkdir(exist_ok=True)
    b = pathlib.Path(blob_path).read_bytes()

    fonts, _ = fi.build_all(blob_path)
    rep = {}
    for code in range(1, 72):
        candidates = []
        for idx, table_off, height, decoded in fonts:
            d = decoded[code - 1]
            if d and d[0] != "BADDECODE":
                candidates.append((idx, height, d))
        candidates.sort(key=lambda c: -c[1])
        rep[code] = candidates[0]

    GSCALE = 5
    CELL_W, CELL_H = 100, 130
    COLS, ROWS = 9, 8
    LABEL_SCALE = 3
    BG = (30, 30, 30)
    GRID = (70, 70, 70)
    LABEL_COLOR = (255, 200, 60)
    GLYPH_COLOR_FALLBACK = (0, 255, 0)

    W = COLS * CELL_W
    H = ROWS * CELL_H
    canvas = [[BG] * W for _ in range(H)]

    for code in range(1, 72):
        idx, height, d = rep[code]
        ptr, hdr, w, h, size = d
        got = decode_pixels(b, ptr)
        row_i, col_i = divmod(code - 1, COLS)
        ox, oy = col_i * CELL_W, row_i * CELL_H
        # gridlines
        for x in range(W):
            pass
        for yy in range(CELL_H):
            canvas[oy + yy][ox] = GRID
        for xx in range(CELL_W):
            canvas[oy][ox + xx] = GRID
        # label: code number
        s = f"{code:02d}"
        put_digit(canvas, ox + 3, oy + 3, s[0], LABEL_COLOR, LABEL_SCALE)
        put_digit(
            canvas, ox + 3 + 4 * LABEL_SCALE, oy + 3, s[1], LABEL_COLOR, LABEL_SCALE
        )
        # font index used (small, below)
        fs = f"{idx:02d}"
        put_digit(canvas, ox + 3, oy + 3 + 6 * LABEL_SCALE, fs[0], (100, 160, 255), 2)
        put_digit(
            canvas, ox + 3 + 4 * 2, oy + 3 + 6 * LABEL_SCALE, fs[1], (100, 160, 255), 2
        )

        if got is None:
            continue
        gw, gh, img = got
        gy0 = 3 + 8 * LABEL_SCALE + 4
        gx0 = 3
        for y in range(gh):
            for x in range(gw):
                px = img[y][x]
                if px is None:
                    continue
                for dy in range(GSCALE):
                    for dx in range(GSCALE):
                        yy = oy + gy0 + y * GSCALE + dy
                        xx = ox + gx0 + x * GSCALE + dx
                        if 0 <= yy < H and 0 <= xx < W:
                            canvas[yy][xx] = px

    ppm = out_dir / "sheet.ppm"
    with open(ppm, "wb") as f:
        f.write(b"P6\n%d %d\n255\n" % (W, H))
        for row in canvas:
            for p in row:
                f.write(bytes(p))
    png = out_dir / "sheet.png"
    subprocess.run(
        ["sips", "-s", "format", "png", str(ppm), "--out", str(png)],
        capture_output=True,
    )
    print("wrote", png, W, "x", H)


if __name__ == "__main__":
    main()
