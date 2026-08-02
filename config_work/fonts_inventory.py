#!/usr/bin/env python3
"""Complete inventory of the 18 fonts in section [7] (0x0291AF).

Verified structure (positive check: 18/18 pointers of index [7] match
exactly the offset where each font's glyph chain ends, measured
INDEPENDENTLY by chaining rle.decode()):

    [7] @ 0x0291AF:  <u16 count=18><18 x ptr24>          (ptr24 = offset + 0x40000)
    font (216 B):    <u8 height><u16 count=71><71 x nullable ptr24>
                      index = code - 1; 00 00 00 = the glyph doesn't exist here

    physical layout: [font0 glyphs][table0 216B][font1 glyphs][table1 216B]...
    region 0x01C870 - 0x0291AF, 423 glyphs + 18 tables, tiles exactly.

Each glyph is RLE mode 0: <u8 hdr><stream>, hdr == decoded width (verified
421/423 in the earlier mapping). Reuses rle.decode() unmodified.
"""

from __future__ import annotations

import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import rle

BASE_LOGICAL = 0x040000
SEC7_OFF = 0x0291AF


def read_ptr24(b: bytes, off: int) -> int:
    return b[off] | (b[off + 1] << 8) | (b[off + 2] << 16)


def parse_section7(b: bytes):
    """-> [ (font_idx, table_file_offset) ] x 18, from the on-disk index."""
    off = SEC7_OFF
    count = b[off] | (b[off + 1] << 8)
    assert count == 18, f"esperaba 18 fuentes, hay {count}"
    out = []
    p = off + 2
    for i in range(count):
        logical = read_ptr24(b, p)
        file_off = logical - BASE_LOGICAL
        out.append((i, file_off))
        p += 3
    return out


def parse_font_table(b: bytes, table_off: int):
    """-> (height, [ptr24 or None for 71 codes]) reading the 216 B table."""
    height = b[table_off]
    count = b[table_off + 1] | (b[table_off + 2] << 8)
    assert count == 71, f"tabla en {table_off:#x}: count={count}, esperaba 71"
    slots = []
    p = table_off + 3
    for code in range(1, count + 1):
        v = read_ptr24(b, p)
        slots.append(None if v == 0 else v - BASE_LOGICAL)
        p += 3
    end = p
    assert end - table_off == 216, f"tabla no mide 216 B: {end - table_off}"
    return height, slots


def decode_glyph(b: bytes, file_off: int):
    """-> (hdr, w, h, size_total_incl_hdr) or None if it fails to decode.

    rle.MIN_W/MIN_H are guards meant for icons (>=8 px), not glyphs: they
    silently drop the i, the l, the j, the period and the comma (width
    2-7), the same gotcha already documented in PLAN.md for scan_mode0.py.
    They are lowered to 1 only for the duration of this call and always
    restored.
    """
    if file_off is None or file_off <= 0 or file_off >= len(b):
        return None
    hdr = b[file_off]
    old_w, old_h = rle.MIN_W, rle.MIN_H
    rle.MIN_W, rle.MIN_H = 1, 1
    try:
        got = rle.decode(b, file_off + 1, limit=4096)
    finally:
        rle.MIN_W, rle.MIN_H = old_w, old_h
    if not got:
        return None
    w, h, rows, end = got
    return hdr, w, h, end - file_off


def main():
    blob_path = sys.argv[1] if len(sys.argv) > 1 else "../backups/config_raw.bin"
    b = pathlib.Path(blob_path).read_bytes()

    sec7 = parse_section7(b)
    print(f"# seccion [7] @ {SEC7_OFF:#x}: {len(sec7)} fuentes")
    print(f"{'font':>4} {'table_off':>10} {'height':>4} {'no_nulas':>8} {'hdr!=w':>7}")

    fonts = []
    for idx, table_off in sec7:
        height, slots = parse_font_table(b, table_off)
        decoded = []
        nonnull = 0
        mismatches = 0
        for code, ptr in enumerate(slots, start=1):
            if ptr is None:
                decoded.append(None)
                continue
            nonnull += 1
            got = decode_glyph(b, ptr)
            if got is None:
                decoded.append(("BADDECODE", ptr))
                continue
            hdr, w, h, size = got
            if hdr != w:
                mismatches += 1
            decoded.append((ptr, hdr, w, h, size))
        fonts.append((idx, table_off, height, decoded))
        print(f"{idx:>4} {table_off:>#10x} {height:>4} {nonnull:>8} {mismatches:>7}")

    return fonts, b


def build_all(blob_path: str):
    b = pathlib.Path(blob_path).read_bytes()
    sec7 = parse_section7(b)
    fonts = []
    for idx, table_off in sec7:
        height, slots = parse_font_table(b, table_off)
        decoded = []
        for code, ptr in enumerate(slots, start=1):
            if ptr is None:
                decoded.append(None)
                continue
            got = decode_glyph(b, ptr)
            decoded.append((ptr,) + got if got else ("BADDECODE", ptr))
        fonts.append((idx, table_off, height, decoded))
    return fonts, b


if __name__ == "__main__":
    main()
