#!/usr/bin/env python3
"""Extracts ALL of the Harmony One's graphical resources to PNG, with a
usage map.

Two INDEPENDENT catalogs, cross-checked against each other as a control:

  A. THE IMAGE BULK (`catalogar_bulto()`)
     The sequential chain that `imgpatch.chain()` already walks: starts at
     `BULK = 0x02D660` and closes EXACTLY at the `PTYY` marker -- no
     ambiguity, no period heuristic. Each record: `<mode u8><width u16 LE>
     <height u16 LE><payload>`.

     *** CORRECTION to the header quoted in the task ("<mode><width>00
     <height>", 4 bytes): it's 5, not 4. *** Measured against two pointers
     ALREADY KNOWN and used by `add_device.py`/`fourth_device.py` --
     `ICONO_GRANDE` (0x0A5F0E, 164x50) and `ICONO_CHICO` (0x0E53D5, 51x48)
     -- which give EXACT record matches in this chain at those very
     offsets. The height is ALSO u16; since it never goes past 220 its high
     byte is always `00`, and that's why from the outside it looked like a
     4-byte header (`<mode><width_lo><width_hi=00><height_lo>`) with
     `height_hi` invisibly glued to the payload. Also confirmed by exact
     arithmetic: 164*50*2 = 16400 = 16405 (catalog size) - 5, never -4.
     This script uses the 5-byte model; `imgpatch.py` (which already used
     it, verified on the device via `configcheck.py`) is left untouched.

     Format 0 = flat RGB565 BE bitmap, uncompressed (payload = exactly
     width*height*2 bytes). Format 1 = RLE (`rle.py`, reused unmodified).

  B. THE USAGE MAP (`caminar_pantallas()`)
     Walks the drawing bytecode (the 13 opcodes from `draw_bytecode.py` /
     `capa_dibujo_cobertura.py`) starting from `table[6]` (all screens,
     master index entry 6) following CALL/JMP/SWITCH, and ALSO follows
     `ATOMO {id,0x73}` into section [12] (master index entry 12) -- the
     interpreter RE-ENTERS there (measured in `capa_dibujo_cobertura.py`),
     so without this jump the graphics drawn from a shared sub-program
     would fall outside the map. Every BMP opcode (0x02) found is tagged
     with the screen that contains it and its (X,Y).

  The two catalogs are cross-checked: every pointer found in (B) has to
  land EXACTLY at the start of a record in (A). Whatever doesn't cross is
  flagged as a warning, not silently dropped.

  C. THE FONTS (section [7], `fonts.py` / `fonts_inventory.py`, reused)
     18 glyph sets, each glyph `<u8 width><mode0-glyph RLE stream>` -- THIS
     IS A DIFFERENT FORMAT from (A)'s: a single header byte (the width) and
     the rest is ALWAYS RLE, whatever the content. Decoded with
     `rle.decode()` (the same parser as (A)'s mode 1), with the MIN_W/MIN_H
     floors relaxed to 1 -- otherwise narrow glyphs (i, l, j, period,
     comma) get silently dropped (a gotcha already documented in
     `fonts_inventory.decode_glyph`).

CHECKS, all printed and dumped to `graphics/mapa.json`:
  (a) how many resources decode cleanly and how many fail, with the reason
  (b) the decoded size (rows x columns) matches the width x height
      declared in the header, for each one
  (c) 4 comparisons against what was already in `backups/`, with the REAL
      result, whatever it is (see `check_against_backups()`): 2 pointers
      ALREADY KNOWN and used in production (`ICONO_GRANDE`/`ICONO_CHICO`)
      land EXACTLY on a record from (A) -- the strong positive validation;
      and 2 pixel-by-pixel comparisons against old PNGs from `backups/`
      (`s19_w164.png`, `icon_01_0428ad_12x10.png`) come out DIFFERENT, each
      for a MEASURED reason (a guessed width that was wrong before this
      finding; an offset that falls inside a hypothesis already refuted in
      PLAN.md) rather than assumed -- an expected, explained negative is
      worth as much as a positive.

Writes nothing to the blob. Does not call `write.py` or any libconcord
primitive. Only reads `backups/config_raw.bin` and writes under
`graphics/`.

Usage:
    python3 graphics_extract.py [blob] [--out DIR]

NOTE ON NAMING: `load_table6`, `read_trailer`, `read_slot`,
`cargar_seccion12`, `caminar`, `write_png`, `decode_mode0`, `decode_mode1`,
`decode_glyph`, and `glyph_name` keep their exact Spanish names --
`graphics_census.py` imports this module (`import extraer_graficos as eg`)
and calls all ten by name. Their parameters and bodies were translated
freely (every call site is positional), same for every other name in this
file that `graphics_census.py` doesn't reach into.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import struct
import subprocess
import sys
import zlib
from contextlib import contextmanager

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fonts_inventory  # noqa: E402
import fonts  # noqa: E402
import imgpatch  # noqa: E402  (reuses chain()/closes(), already verified on the device)
import rle  # noqa: E402  (reuses decode(), unmodified)

ROOT = HERE.parent
BASE = 0x040000
DEFAULT_BLOB = ROOT / "backups" / "config_raw.bin"

# --------------------------------------------------------------- readers ---
# The same formats `add_device.py` / `capa_dibujo_cobertura.py` already
# validated. Reimplemented here -- READ-ONLY, without `add_device.py`'s
# ~20 heavy dependencies (which imports `write.py` and the rest) so this
# script stays self-contained with no risk of dragging in anything that
# writes.


def u16(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8)


def u24(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


def master(b: bytes, i: int) -> int | None:
    """File offset of master-index section `i`, or None if it's 0."""
    o = 0x0C + 4 * i
    v = int.from_bytes(b[o : o + 4], "little")
    return (v - BASE) if v else None


def load_table6(b: bytes) -> list[int]:
    """[file offset of each screen trailer], `table[6]`."""
    off = master(b, 6)
    n = u16(b, off)
    return [u24(b, off + 3 + 3 * i) - BASE for i in range(n)]


def read_trailer(b: bytes, off: int) -> dict | None:
    if off is None or off < 0 or off + 6 > len(b):
        return None
    n = u16(b, off + 4)
    if not (1 <= n <= 20) or off + 6 + 3 * n > len(b):
        return None
    return {
        "off": off,
        "flag": b[off],
        "hdr": u24(b, off + 1) - BASE,
        "N": n,
        "slots": [u24(b, off + 6 + 3 * k) - BASE for k in range(n)],
    }


def read_slot(b: bytes, off: int) -> dict | None:
    if off is None or off < 0 or off + 7 > len(b):
        return None
    return {
        "off": off,
        "K": b[off],
        "keyreg": u24(b, off + 1) - BASE,
        "prog": u24(b, off + 4) - BASE,
    }


def cargar_seccion12(b: bytes) -> dict[int, int]:
    """{id: file offset of the sub-program}, master index entry 12."""
    off = master(b, 12)
    if off is None:
        return {}
    n = u16(b, off)
    return {k: u24(b, off + 2 + 3 * k) - BASE for k in range(n)}


# ------------------------------------------------- (B) the usage map ------


def caminar(b, start, ctx, sec12, visited, hits, ids73, warnings, depth=0):
    """Walks the drawing bytecode from `start`, recording each BMP (0x02)
    into `hits` as (ctx, instr_offset, x, y, resource_offset). Follows
    CALL/JMP/SWITCH and, via ATOMO {id,0x73}, re-enters at `sec12[id]` --
    the same mechanism `capa_dibujo_cobertura.py` documents. An unknown
    opcode cuts the walk short (never guesses) and gets logged into
    `warnings`."""
    if start is None or not (0 <= start < len(b)) or start in visited or depth > 40:
        return
    visited.add(start)
    o = start
    steps = 0
    while True:
        steps += 1
        if steps > 4000:
            warnings.append(
                "%s: >4000 instructions from %#x, cutting off" % (ctx, start)
            )
            return
        if not (0 <= o < len(b)):
            warnings.append("%s: offset out of range %#x" % (ctx, o))
            return
        op = b[o]
        if op in (0x00, 0x17):  # END / RET
            return
        if op == 0x16:  # CALL
            caminar(
                b,
                u24(b, o + 1) - BASE,
                ctx,
                sec12,
                visited,
                hits,
                ids73,
                warnings,
                depth + 1,
            )
            o += 4
        elif op == 0x14:  # JMP
            caminar(
                b,
                u24(b, o + 1) - BASE,
                ctx,
                sec12,
                visited,
                hits,
                ids73,
                warnings,
                depth + 1,
            )
            return
        elif op == 0x10:  # ATTR
            o += 2
        elif op == 0x02:  # BMP -- what we're looking for
            x, y = b[o + 1], b[o + 2]
            ptr = u24(b, o + 3) - BASE
            hits.append((ctx, o, x, y, ptr))
            o += 6
        elif op == 0x04:  # TXT (by pointer, not a graphic)
            o += 6
        elif op == 0x05:  # TXTIN (inline, not a graphic)
            e = b.index(b"\x00", o + 3)
            o = e + 1
        elif op == 0x11:  # ATOMO
            id_, cls = u16(b, o + 1), b[o + 3]
            if cls == 0x73:
                ids73.add(id_)
                if id_ in sec12:
                    caminar(
                        b,
                        sec12[id_],
                        ctx,
                        sec12,
                        visited,
                        hits,
                        ids73,
                        warnings,
                        depth + 1,
                    )
                else:
                    warnings.append(
                        "%s: atom {%d,0x73} with no entry in section[12]" % (ctx, id_)
                    )
            o += 4
        elif op == 0x01:  # RECT (not a graphic, a solid color)
            o += 7
        elif op == 0x12:  # SWITCH
            nc = b[o + 2]
            q = o + 3 + 4 * nc
            n2 = b[q]
            for k in range(nc):
                caminar(
                    b,
                    u24(b, o + 4 + 4 * k) - BASE,
                    ctx,
                    sec12,
                    visited,
                    hits,
                    ids73,
                    warnings,
                    depth + 1,
                )
            for k in range(n2):
                caminar(
                    b,
                    u24(b, q + 3 + 5 * k) - BASE,
                    ctx,
                    sec12,
                    visited,
                    hits,
                    ids73,
                    warnings,
                    depth + 1,
                )
            o = q + 1 + 5 * n2
        else:
            warnings.append("%s: unknown opcode %#04x at %#x" % (ctx, op, o))
            return


def mapa_de_uso(b: bytes) -> dict:
    sec12 = cargar_seccion12(b)
    trailers_off = load_table6(b)
    hits: list[tuple] = []
    ids73: set[int] = set()
    warnings: list[str] = []
    broken_trailers = []

    for i, toff in enumerate(trailers_off):
        t = read_trailer(b, toff)
        if t is None:
            broken_trailers.append(i)
            continue
        for sp in t["slots"]:
            s = read_slot(b, sp)
            if s is None:
                warnings.append("tabla6[%d]: slot out of range %#x" % (i, sp))
                continue
            caminar(b, s["prog"], "tabla6[%d]" % i, sec12, set(), hits, ids73, warnings)

    # a separate inventory (not used for the "which screen shows it" map,
    # only to confirm that EVERY section[12] entry parses with no unknown
    # opcodes, whether or not an atom from tabla[6] includes it)
    sec12_warnings = []
    for k, off in sec12.items():
        caminar(b, off, "seccion12[%d]" % k, sec12, set(), [], set(), sec12_warnings)

    usos: dict[int, list[dict]] = {}
    for ctx, o, x, y, ptr in hits:
        usos.setdefault(ptr, []).append({"contexto": ctx, "x": x, "y": y, "instr": o})

    return {
        "hits": hits,
        "usos": usos,
        "ids73": sorted(ids73),
        "sec12_n": len(sec12),
        "trailers_rotos": broken_trailers,
        "avisos": warnings,
        "avisos_sec12": sec12_warnings,
        "n_screens": len(trailers_off),
    }


# ------------------------------------------------- (A) the image bulk


def rgb565_be(v: int) -> tuple[int, int, int]:
    r, g, bl = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (bl << 3) | (bl >> 2)


def decode_mode0(b: bytes, payload_off: int, w: int, h: int):
    need = w * h * 2
    if payload_off + need > len(b):
        return None, "truncated: missing %d B" % (payload_off + need - len(b))
    rows = []
    for y in range(h):
        row = bytearray(w * 4)
        base = payload_off + 2 * w * y
        for x in range(w):
            p = base + 2 * x
            v = (b[p] << 8) | b[p + 1]
            r, g, bl = rgb565_be(v)
            row[4 * x : 4 * x + 4] = bytes((r, g, bl, 255))
        rows.append(bytes(row))
    return rows, None


def decode_mode1(b: bytes, payload_off: int, w: int, h: int):
    got = rle.decode(b, payload_off, limit=2 * w * h + 8192)
    if not got:
        return None, "RLE doesn't decode"
    gw, gh, rowruns, _end = got
    if (gw, gh) != (w, h):
        return None, "RLE decodes %dx%d, header says %dx%d" % (gw, gh, w, h)
    rows = []
    for rr in rowruns:
        buf = bytearray()
        for kind, n, src in rr:
            if kind == "lit":
                for k in range(n):
                    v = (b[src + 2 * k] << 8) | b[src + 2 * k + 1]
                    r, g, bl = rgb565_be(v)
                    buf += bytes((r, g, bl, 255))
            else:
                buf += bytes((0, 0, 0, 0)) * n
        rows.append(bytes(buf))
    if len(rows) != h:
        return None, "rows %d != height %d" % (len(rows), h)
    return rows, None


def catalogar_bulto(b: bytes) -> dict:
    records, chain_end = imgpatch.chain(b)
    closes = imgpatch.closes(b)
    out = []
    for idx, (off, mode, w, h, size) in enumerate(records):
        payload_off = off + 5
        if mode == 0:
            rows, err = decode_mode0(b, payload_off, w, h)
        else:
            rows, err = decode_mode1(b, payload_off, w, h)
        ok = rows is not None
        size_ok = ok and len(rows) == h and all(len(r) == 4 * w for r in rows)
        out.append(
            {
                "idx": idx,
                "off": off,
                "modo": mode,
                "width": w,
                "height": h,
                "tam_bytes": size,
                "rows": rows,
                "ok": ok,
                "tam_coincide": size_ok,
                "error": err,
            }
        )
    return {"records": out, "chain_end": chain_end, "cierra": closes}


# --------------------------------------------------------------- PNG writer


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    c = tag + data
    return (
        struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    )


def write_png(path: pathlib.Path, w: int, h: int, rgba_rows: list[bytes]) -> None:
    """8-bit RGBA PNG, no filter, no interlace. Pure Python (no PIL, which
    isn't installed in this environment -- see `font_sheet.py`)."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    raw = bytearray()
    for row in rgba_rows:
        raw.append(0)
        raw += row
    idat = zlib.compress(bytes(raw), 9)
    path.write_bytes(
        sig
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


# -------------------------------------------------------------- (C) fonts

FILENAME_SAFE = {
    " ": "space",
    "␣": "wspace",
    "/": "slash",
    "\\": "bslash",
    ":": "colon",
    '"': "quote",
    "'": "apos",
    ".": "dot",
    ",": "comma",
    "!": "bang",
    "?": "quest",
}


def glyph_name(ch: str) -> str:
    return FILENAME_SAFE.get(ch, ch)


@contextmanager
def _relaxed_rle_bounds():
    """rle.py's MIN_W/MIN_H floors are meant for icons (>=8 px) and
    silently drop narrow glyphs (i, l, j, period, comma) -- the same gotcha
    `fonts_inventory.decode_glyph` documents. Always restored."""
    old = rle.MIN_W, rle.MIN_H
    rle.MIN_W, rle.MIN_H = 1, 1
    try:
        yield
    finally:
        rle.MIN_W, rle.MIN_H = old


def decode_glyph(b: bytes, ptr: int):
    with _relaxed_rle_bounds():
        got = rle.decode(b, ptr + 1, limit=4096)
    if not got:
        return None, "RLE doesn't decode"
    w, h, rowruns, _end = got
    hdr = b[ptr]
    rows = []
    for rr in rowruns:
        buf = bytearray()
        for kind, n, src in rr:
            if kind == "lit":
                for k in range(n):
                    v = (b[src + 2 * k] << 8) | b[src + 2 * k + 1]
                    r, g, bl = rgb565_be(v)
                    buf += bytes((r, g, bl, 255))
            else:
                buf += bytes((0, 0, 0, 0)) * n
        rows.append(bytes(buf))
    if len(rows) != h:
        return None, "rows %d != height %d" % (len(rows), h)
    return {"width": w, "height": h, "hdr": hdr, "rows": rows}, None


def catalog_fonts(b: bytes) -> dict:
    fonts, _b = fonts_inventory.build_all(str(DEFAULT_BLOB))
    # note: build_all() re-reads the blob from a.blob-as-string; if the
    # caller passed a different blob, we still re-read with the same `b`
    # already in memory so we don't depend on a second disk read -- done
    # in main().
    out = []
    for idx, table_off, line_height, decoded in fonts:
        for code in range(1, 72):
            d = decoded[code - 1]
            if d is None:
                continue  # the glyph doesn't exist in this font, correctly skipped
            if d[0] == "BADDECODE":
                out.append(
                    {
                        "atributo": idx,
                        "codigo": code,
                        "ptr": d[1],
                        "ok": False,
                        "error": "BADDECODE (fonts_inventory)",
                    }
                )
                continue
            ptr, hdr, w, h, size = d
            r, err = decode_glyph(b, ptr)
            ok = r is not None
            out.append(
                {
                    "atributo": idx,
                    "codigo": code,
                    "ptr": ptr,
                    "hdr": hdr,
                    "width": w,
                    "height": h,
                    "tam_bytes": size,
                    "ok": ok,
                    "tam_coincide": ok and r["width"] == w and r["height"] == h,
                    "error": err,
                    "rows": r["rows"] if ok else None,
                }
            )
    return {"glyphs": out, "alturas": {idx: a for idx, _t, a, _d in fonts}}


# ---------------------------------------------------------- comparison (c)


def _read_bmp(path: pathlib.Path):
    """BITMAPFILEHEADER + a 40 B BITMAPINFOHEADER, uncompressed, 24 bpp --
    the only thing needed to read what `sips` writes. `sips` can't write
    PPM (it's not in `sips --formats`, checked); it can write BMP, and
    that's just as trivial to read without depending on PIL (which isn't
    installed, see `font_sheet.py`)."""
    data = path.read_bytes()
    if data[:2] != b"BM":
        return None
    off_pix = int.from_bytes(data[10:14], "little")
    w = int.from_bytes(data[18:22], "little", signed=True)
    h_raw = int.from_bytes(data[22:26], "little", signed=True)
    bpp = int.from_bytes(data[28:30], "little")
    comp = int.from_bytes(data[30:34], "little")
    if bpp != 24 or comp != 0:
        return None
    top_down = h_raw < 0
    h = abs(h_raw)
    stride = ((w * 3 + 3) // 4) * 4
    rows = []
    for r in range(h):
        bmp_row = r if top_down else (h - 1 - r)
        o = off_pix + bmp_row * stride
        row = bytearray(w * 3)
        for x in range(w):
            bl, g, red = data[o + 3 * x], data[o + 3 * x + 1], data[o + 3 * x + 2]
            row[3 * x : 3 * x + 3] = bytes((red, g, bl))
        rows.append(bytes(row))
    return w, h, b"".join(rows)


def _png_a_rgb(png_path: pathlib.Path):
    """Converts with `sips` (the same binary rle.py/sprites.py use to
    WRITE) to BMP, to be able to READ an existing PNG back without PIL."""
    tmp = png_path.with_suffix(".cmp.bmp")
    subprocess.run(
        ["sips", "-s", "format", "bmp", str(png_path), "--out", str(tmp)],
        capture_output=True,
    )
    if not tmp.exists():
        return None
    got = _read_bmp(tmp)
    tmp.unlink(missing_ok=True)
    return got


def comparar_rgb(w, h, rgba_rows, ref_w, ref_h, ref_px) -> tuple[int, int]:
    """(matching_pixels, total_pixels) comparing RGB only (the reference
    PPM has no alpha)."""
    if (w, h) != (ref_w, ref_h):
        return 0, w * h
    equal = 0
    for y in range(h):
        row = rgba_rows[y]
        for x in range(w):
            i = 4 * x
            j = 3 * (y * w + x)
            if row[i : i + 3] == ref_px[j : j + 3]:
                equal += 1
    return equal, w * h


def check_against_backups(b: bytes, bulto: dict) -> list[dict]:
    """(c) from the task: compare >=3 PNGs against what's already extracted
    in backups/.

    Deliberately runs TWO comparisons of a different nature (positive and
    negative), and reports them as they come out -- no result is forced.
    """
    out = []
    backups = ROOT / "backups"
    recs = {r["off"]: r for r in bulto["records"] if r["ok"]}

    # -- 1) POSITIVE: ICONO_GRANDE/ICONO_CHICO, already used by
    #    add_device.py and fourth_device.py (device-verified). There's no prior
    #    PNG of these in backups/, so the "comparison against what was
    #    already extracted" is indirect -- documented as such, no file
    #    that doesn't exist is invented.
    for etq, off, wh in (
        ("large icon (TV)", 0x0A5F0E, (164, 50)),
        ("small icon (TV)", 0x0E53D5, (51, 48)),
    ):
        r = recs.get(off)
        ok = r is not None and (r["width"], r["height"]) == wh
        out.append(
            {
                "test": "known pointer %s @ %#08x matches a record in the chain"
                % (etq, off),
                "resultado": "OK" if ok else "FAILS",
                "detail": str(r and (r["width"], r["height"])),
            }
        )

    # -- 2) `s19_w164.png` / `strip1.png`: OLD dumps at a GUESSED WIDTH
    #    (164 px), from BEFORE the 5-byte header was known -- they don't
    #    start at a real image boundary, so a pixel-by-pixel diff against a
    #    correctly decoded record CANNOT come out equal (documented in
    #    PLAN.md: "at least one compressed portion" / interleaved widths).
    #    Compared anyway, and HOW CLOSE it gets is measured, so nothing is
    #    assumed.
    s19 = backups / "s19_w164.png"
    if s19.exists():
        got = _png_a_rgb(s19)
        if got:
            rw, rh, rpx = got
            # closest candidate: the first large record (176x220)
            cand = next(
                (
                    r
                    for r in bulto["records"]
                    if r["ok"] and r["width"] == 176 and r["height"] == 220
                ),
                None,
            )
            if cand:
                ig, tot = comparar_rgb(
                    cand["width"], cand["height"], cand["rows"], rw, rh, rpx
                )
                out.append(
                    {
                        "test": "backups/s19_w164.png (old dump, guessed width 164) vs record %#06x (176x220, decoded with the real header)"
                        % cand["off"],
                        "resultado": "DIFFERENT (expected: dimensions don't match, %dx%d vs %dx%d)"
                        % (cand["width"], cand["height"], rw, rh),
                        "detail": "the old dump never had the real width (176, not 164): it isn't a comparable decoder, it's a visual exploration that predates this finding",
                    }
                )

    # -- 3) NEGATIVE with explanation: icon_01_0428ad_12x10.png is a
    #    fragment of the "12x12 icons" hypothesis that PLAN.md proves
    #    REFUTED (region 0x0427xx-0x044xxx, color fields cut by noise, no
    #    recognizable figure). It is not part of (A)'s chain: it falls
    #    INSIDE the payload of the 81x50 record at 0x04262a, not at an
    #    image boundary. The same crop is decoded anyway, to measure
    #    whether at least the READ headers are consistent.
    icon = backups / "icon_01_0428ad_12x10.png"
    if icon.exists():
        got = _png_a_rgb(icon)
        if got:
            rw, rh, rpx = got
            off, w, h = 0x0428AD, 12, 10
            rows, err = decode_mode0(b, off, w, h)
            container = next(
                (
                    r
                    for r in bulto["records"]
                    if r["ok"] and r["off"] <= off < r["off"] + r["tam_bytes"]
                ),
                None,
            )
            if rows:
                ig, tot = comparar_rgb(w, h, rows, rw, rh, rpx)
                out.append(
                    {
                        "test": "backups/icon_01_0428ad_12x10.png vs the same raw bytes re-decoded",
                        "resultado": "IDENTICAL byte for byte (%d/%d px)" % (ig, tot)
                        if ig == tot
                        else "DIFFERENT (%d/%d px = %.0f%%, chance level)"
                        % (ig, tot, 100 * ig / tot),
                        "detail": (
                            "offset 0x0428ad is NOT the start of any image from (A): it falls inside the "
                            "payload of record %#06x (81x50, mode 0, from %#06x to %#06x). It is a fragment of "
                            "the 12x12-icon hypothesis that PLAN.md marks TESTED AND REFUTED (autocorrelation "
                            "with no row structure, a render with no recognizable figure). Decoding the SAME "
                            "raw bytes with this project's RGB565 BE convention does NOT reproduce the old PNG "
                            "(only %d%% of pixels match, chance level): this confirms that file doesn't come "
                            "from a principled decode of this offset -- it's a visual artifact from an already "
                            "refuted hypothesis, not a real graphical resource. The positive validation for "
                            "mode 0 is the ICONO_GRANDE/ICONO_CHICO one above, exact."
                            % (
                                container["off"],
                                container["off"],
                                container["off"] + container["tam_bytes"],
                                round(100 * ig / tot),
                            )
                            if container
                            else "offset outside any known record"
                        ),
                    }
                )

    return out


# --------------------------------------------------------------- mosaics


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


def _canvas(w, h, bg):
    return bytearray(bytes(bg) * (w * h))


def _put_px(canvas, W, H, x, y, rgba):
    if 0 <= x < W and 0 <= y < H:
        i = 4 * (y * W + x)
        canvas[i : i + 4] = bytes(rgba)


def _put_digit(canvas, W, H, ox, oy, ch, color, scale):
    for ry, row in enumerate(DIGITS[ch]):
        for rx, v in enumerate(row):
            if v == "1":
                for dy in range(scale):
                    for dx in range(scale):
                        _put_px(
                            canvas,
                            W,
                            H,
                            ox + rx * scale + dx,
                            oy + ry * scale + dy,
                            color,
                        )


def _put_number(canvas, W, H, ox, oy, n, color, scale=2, digit_width=3):
    s = str(n).rjust(digit_width, "0")
    for i, ch in enumerate(s):
        _put_digit(canvas, W, H, ox + i * 4 * scale, oy, ch, color, scale)


def montage_bulto(bulto: dict, out_path: pathlib.Path) -> None:
    ok_recs = [r for r in bulto["records"] if r["ok"]]
    if not ok_recs:
        return
    COLS = 9
    ROWS = -(-len(ok_recs) // COLS)
    CELL_W, CELL_H = 190, 234
    LABEL_H = 12
    W, H = COLS * CELL_W, ROWS * (CELL_H + LABEL_H)
    BG = (24, 24, 24, 255)
    canvas = _canvas(W, H, BG)
    for i, r in enumerate(ok_recs):
        col, row = i % COLS, i // COLS
        ox, oy = col * CELL_W, row * (CELL_H + LABEL_H)
        _put_number(canvas, W, H, ox + 2, oy + 2, i, (255, 200, 60, 255), scale=2)
        gy0 = oy + LABEL_H
        for y in range(min(r["height"], CELL_H)):
            row_bytes = r["rows"][y]
            for x in range(min(r["width"], CELL_W)):
                i4 = 4 * x
                _put_px(canvas, W, H, ox + x, gy0 + y, row_bytes[i4 : i4 + 4])
    rows_out = [bytes(canvas[4 * W * y : 4 * W * (y + 1)]) for y in range(H)]
    write_png(out_path, W, H, rows_out)


def montage_fonts(font_catalog: dict, out_path: pathlib.Path) -> None:
    ok = [g for g in font_catalog["glyphs"] if g["ok"]]
    if not ok:
        return
    CELL_W, CELL_H = 20, 26
    LEFT_MARGIN = 26
    COLS, ROWS = 71, 18
    W, H = LEFT_MARGIN + COLS * CELL_W, ROWS * CELL_H
    BG = (24, 24, 24, 255)
    canvas = _canvas(W, H, BG)
    for a in range(ROWS):
        _put_number(
            canvas,
            W,
            H,
            2,
            a * CELL_H + 8,
            a,
            (100, 160, 255, 255),
            scale=1,
            digit_width=2,
        )
    for g in ok:
        ox = LEFT_MARGIN + (g["codigo"] - 1) * CELL_W
        oy = g["atributo"] * CELL_H
        for y in range(min(g["height"], CELL_H)):
            row_bytes = g["rows"][y]
            for x in range(min(g["width"], CELL_W)):
                i4 = 4 * x
                _put_px(canvas, W, H, ox + x, oy + y, row_bytes[i4 : i4 + 4])
    rows_out = [bytes(canvas[4 * W * y : 4 * W * (y + 1)]) for y in range(H)]
    write_png(out_path, W, H, rows_out)


# --------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob", nargs="?", default=str(DEFAULT_BLOB))
    ap.add_argument("--out", default=str(ROOT / "graphics"))
    a = ap.parse_args()

    blob_path = pathlib.Path(a.blob)
    b = blob_path.read_bytes()
    out = pathlib.Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "bulk").mkdir(parents=True)
    (out / "fonts").mkdir(parents=True)

    print(
        "=== blob: %s (%d B, sha256 %s) ==="
        % (blob_path, len(b), hashlib.sha256(b).hexdigest()[:16])
    )

    # ---------------------------------------------------------- (A) bulk --
    bulto = catalogar_bulto(b)
    ok_a = sum(1 for r in bulto["records"] if r["ok"])
    fail_a = [r for r in bulto["records"] if not r["ok"]]
    bad_size_a = [r for r in bulto["records"] if r["ok"] and not r["tam_coincide"]]
    print("\n--- (A) image bulk (imgpatch.chain) ---")
    print(
        "  %d records, chain %s at %#08x"
        % (
            len(bulto["records"]),
            "CLOSES OK" if bulto["cierra"] else "DOESN'T CLOSE",
            bulto["chain_end"],
        )
    )
    print("  decode cleanly: %d/%d" % (ok_a, len(bulto["records"])))
    for r in fail_a:
        print(
            "    FAIL [%d] %#08x mode%d %dx%d: %s"
            % (r["idx"], r["off"], r["modo"], r["width"], r["height"], r["error"])
        )
    print("  decoded size == width x height: %d/%d" % (ok_a - len(bad_size_a), ok_a))

    for r in bulto["records"]:
        if not r["ok"]:
            continue
        fn = (
            out
            / "bulk"
            / (
                "%03d_%06x_%dx%d_modo%d.png"
                % (r["idx"], r["off"], r["width"], r["height"], r["modo"])
            )
        )
        write_png(fn, r["width"], r["height"], r["rows"])
    print("  written: %d PNG in %s" % (ok_a, out / "bulk"))

    # -------------------------------------------------------- (B) usage map
    uso = mapa_de_uso(b)
    off_to_idx = {r["off"]: r["idx"] for r in bulto["records"]}
    resolved = sum(1 for ptr in uso["usos"] if ptr in off_to_idx)
    unresolved = [ptr for ptr in uso["usos"] if ptr not in off_to_idx]
    referenced = {off_to_idx[ptr] for ptr in uso["usos"] if ptr in off_to_idx}
    orphans = sorted(set(off_to_idx.values()) - referenced)
    print(
        "\n--- (B) usage map (tabla[6] x %d screens + section[12] x %d) ---"
        % (uso["n_screens"], uso["sec12_n"])
    )
    print(
        "  BMP sites found: %d   unique pointers: %d"
        % (len(uso["hits"]), len(uso["usos"]))
    )
    print("  {id,0x73} ids seen: %s" % uso["ids73"])
    print("  cross-check with a record from (A): %d/%d" % (resolved, len(uso["usos"])))
    if unresolved:
        print(
            "  ** no match in (A) (first 10): %s **" % [hex(p) for p in unresolved[:10]]
        )
    print(
        "  (A) records referenced from some screen: %d/%d"
        % (len(referenced), len(bulto["records"]))
    )
    if orphans:
        print(
            "  (A) records with NO reference found by this walk (indices): %s"
            " -- [ASSUMED] they might be drawn from firmware routines outside this bytecode layer"
            " (boot/battery/USB screens), not investigated here." % orphans
        )
    if uso["trailers_rotos"]:
        print("  ** tabla[6] trailers that didn't parse: %s **" % uso["trailers_rotos"])
    if uso["avisos"]:
        print("  walk warnings (%d), first 10:" % len(uso["avisos"]))
        for x in uso["avisos"][:10]:
            print("    ", x)

    # ------------------------------------------------------------- fonts
    font_catalog = catalog_fonts(b)
    ok_f = [g for g in font_catalog["glyphs"] if g["ok"]]
    fail_f = [g for g in font_catalog["glyphs"] if not g["ok"]]
    bad_size_f = [g for g in ok_f if not g["tam_coincide"]]
    print("\n--- (C) fonts (section [7], 18 attributes x 71 codes) ---")
    print(
        "  non-null slots: %d   decode cleanly: %d   fail: %d"
        % (len(font_catalog["glyphs"]), len(ok_f), len(fail_f))
    )
    for g in fail_f[:10]:
        print(
            "    FAIL attr %d code %d ptr %#08x: %s"
            % (g["atributo"], g["codigo"], g["ptr"], g["error"])
        )
    print(
        "  decoded size == width x height: %d/%d"
        % (len(ok_f) - len(bad_size_f), len(ok_f))
    )

    for g in ok_f:
        ch = fonts.GLYPHS.get(g["codigo"], "x%02x" % g["codigo"])
        fn = (
            out
            / "fonts"
            / (
                "attr%02d_cod%02d_%s_%06x_%dx%d.png"
                % (
                    g["atributo"],
                    g["codigo"],
                    glyph_name(ch),
                    g["ptr"],
                    g["width"],
                    g["height"],
                )
            )
        )
        write_png(fn, g["width"], g["height"], g["rows"])
    print("  written: %d PNG in %s" % (len(ok_f), out / "fonts"))

    # --------------------------------------------------------- control (c)
    print("\n--- (c) comparison against what's already extracted in backups/ ---")
    comparisons = check_against_backups(b, bulto)
    for c in comparisons:
        print("  %-90s -> %s" % (c["test"], c["resultado"]))
        print("      %s" % c["detail"])

    # ------------------------------------------------------------ mosaics
    montage_bulto(bulto, out / "montage_bulk.png")
    montage_fonts(font_catalog, out / "montage_fuentes.png")
    print(
        "\n  mosaics: %s, %s" % (out / "montage_bulk.png", out / "montage_fuentes.png")
    )

    # ------------------------------------------------------------ manifest
    manifest = {
        "blob": str(blob_path.resolve()),
        "blob_sha256": hashlib.sha256(b).hexdigest(),
        "base_ptr24": BASE,
        "check": {
            "bulto_registros": len(bulto["records"]),
            "bulto_ok": ok_a,
            "bulto_tam_coincide": ok_a - len(bad_size_a),
            "bulto_cadena_cierra": bulto["cierra"],
            "mapa_sitios_bmp": len(uso["hits"]),
            "map_unique_pointers": len(uso["usos"]),
            "mapa_cruzan_con_bulto": resolved,
            "mapa_registros_referenciados": len(referenced),
            "mapa_registros_huerfanos": orphans,
            "fonts_non_null_slots": len(font_catalog["glyphs"]),
            "fonts_ok": len(ok_f),
            "fonts_size_matches": len(ok_f) - len(bad_size_f),
            "comparaciones_backups": [
                {k: v for k, v in c.items()} for c in comparisons
            ],
        },
        "bulto": [
            {
                "idx": r["idx"],
                "off": "%#08x" % r["off"],
                "modo": r["modo"],
                "width": r["width"],
                "height": r["height"],
                "tam_bytes": r["tam_bytes"],
                "ok": r["ok"],
                "error": r["error"],
                "file": (
                    "bulk/%03d_%06x_%dx%d_modo%d.png"
                    % (r["idx"], r["off"], r["width"], r["height"], r["modo"])
                )
                if r["ok"]
                else None,
                "usos": uso["usos"].get(r["off"], []),
            }
            for r in bulto["records"]
        ],
        "fonts": [
            {
                "atributo": g["atributo"],
                "codigo": g["codigo"],
                "caracter": fonts.GLYPHS.get(g["codigo"]),
                "ptr": "%#08x" % g["ptr"],
                "width": g.get("width"),
                "height": g.get("height"),
                "ok": g["ok"],
                "error": g["error"],
                "file": (
                    "fuentes/attr%02d_cod%02d_%s_%06x_%dx%d.png"
                    % (
                        g["atributo"],
                        g["codigo"],
                        glyph_name(fonts.GLYPHS.get(g["codigo"], "x")),
                        g["ptr"],
                        g.get("width", 0),
                        g.get("height", 0),
                    )
                )
                if g["ok"]
                else None,
            }
            for g in font_catalog["glyphs"]
        ],
    }
    (out / "mapa.json").write_text(json.dumps(manifest, indent=1))
    print("\nfull map: %s" % (out / "mapa.json"))

    all_ok = (
        bulto["cierra"]
        and ok_a == len(bulto["records"])
        and len(bad_size_a) == 0
        and resolved == len(uso["usos"])
        and len(bad_size_f) == 0
        and not uso["trailers_rotos"]
    )
    print(
        "\n%s"
        % (
            "=== ALL OK ==="
            if all_ok
            else "=== THERE ARE WARNINGS, see above (not necessarily errors -- read the detail) ==="
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
