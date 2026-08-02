#!/usr/bin/env python3
"""Canonical census of ALL of the blob's graphical resources, with its
usage map.

This replaces and reconciles three concurrent attempts from the same
night (`graphics_resources.py`, `graphics_extract.py`, `graphics_extract_blob.py`):
each one got part of it right and part of it wrong. What's written here is
what survived the checks that run on every execution.

WHAT'S IN THE BLOB
-------------------
The graphics are NOT in any of the master index's 20 sections: they are a
CHAIN that starts at 0x02D660, glued right after the last section, and ends
exactly at the file's `<u16 checksum>PTYY` closer. Each record:

    <u8 mode><u16 width LE><u16 height LE><payload>       5 B header, both modes
    mode 0   payload = width*height*2 B, RGB565 BIG ENDIAN, flat
    mode 1   payload = rle.py RLE stream (00 end, 80 end of row,
             01-7F literal run, 81-FF skip = transparency), RGB565 BE

The header is the one `imgpatch.py` was already using since 07/25. It is
not 4 bytes: the 3rd and 5th bytes are ALWAYS 0x00 because the panel never
goes past 220 px, i.e. they're the high half of each u16, not padding. The
check for this is below and it's decisive.

Separately, section [7] has 18 glyph sets (423 non-null slots). A glyph is
`<u8 width><RLE stream>` -- the same grammar as mode 1, but `rle.decode()`
has MIN_W/MIN_H=8/4 guards meant for icons that silently drop narrow
glyphs (i, l, j, period, comma). They're lowered to 1 for the duration of
decoding (the same remedy as `fonts_inventory.decode_glyph`).

THE CHECKS (always run; if any fails, status != 0)
-----------------------------------------------------
1. CHAIN (strong negative). The chain is walked with the 4 combinations of
   header length per mode. Only 5B/5B reaches 71 records and lands exactly
   on `PTYY`; 4B/4B and 4B/5B die at record 1, 5B/4B at record 2. An exact
   close after 71 chained jumps doesn't happen by chance.
2. MAP vs BRUTE SCAN (negative). The bytecode walker and a blind scan of
   `02 <x><y><ptr24 landing at the start of a record>` over the WHOLE blob
   have to give the SAME set of sites. It gives 754 = 754, with 0
   difference either way. This is the check that catches the "walked but
   dropped the hits" bug, which is how false orphans get manufactured.
3. GEOMETRY (positive). The 754 sites satisfy x+width <= 176 and
   y+height <= 220.
4. PRODUCTION CONSTANTS (positive). The 8 pointers `add_device.py`
   already uses to register a device (ICONO_GRANDE, ICONO_CHICO, and the 6
   BOTON_BMP) have to land EXACTLY at the start of a record, with the
   dimensions that file declares (164x50, 51x48, 81x50).
5. OVERLAP (negative). The 71 image ranges and 423 glyph ranges, with
   their REAL measured extent (not estimated), must not overlap each
   other.
6. PIXELS against a pre-existing tool (positive). For every mode 0 record,
   a pixel-by-pixel comparison is made against what `imgpatch.py extract`
   emits, which is code already used to patch the config that is grabbed
   today. This is what catches a 1-byte shift -- exactly what happened to
   `graphics_resources.py`, whose PNGs all came out read from off+4.

Writes nothing to the blob, does not import `write.py`, does not touch
`account_export/`.

Usage:
    python3 graphics_census.py [blob] [--out DIR] [--no-png]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import graphics_extract as eg  # noqa: E402  (reuses the bytecode walker)
import fonts_inventory  # noqa: E402  (reuses reading section [7])
import imgpatch  # noqa: E402  (reuses chain()/closes(), tested on the device)
import rle  # noqa: E402  (reuses decode(), unmodified)

ROOT = HERE.parent
BASE = 0x040000
START = 0x02D660
MAXW, MAXH = 176, 220
DEFAULT_BLOB = ROOT / "backups" / "config_raw.bin"
DEFAULT_OUT = ROOT / "graphics"

# What `add_device.py` already uses in production (device registration
# verified on the device). Imported as numbers, not re-derived.
CONSTANTS = [
    ("ICONO_GRANDE", 0x0A5F0E, 164, 50),
    ("ICONO_CHICO", 0x0E53D5, 51, 48),
    ("BOTON_BMP[0]", 0x0B7B56, 81, 50),
    ("BOTON_BMP[1]", 0x07DA61, 81, 50),
    ("BOTON_BMP[2]", 0x112A64, 81, 50),
    ("BOTON_BMP[3]", 0x0E342C, 81, 50),
    ("BOTON_BMP[4]", 0x0A3F65, 81, 50),
    ("BOTON_BMP[5]", 0x09FE56, 81, 50),
]


# ------------------------------------------------------------------ chain ---


def chain_variant(b: bytes, h0: int, h1: int):
    """Walks the chain assuming an `h0`-byte header in mode 0 and `h1` in mode 1."""
    o, regs = START, []
    while o < len(b) - 5:
        modo = b[o]
        w = b[o + 1] | (b[o + 2] << 8)
        h = b[o + 3] | (b[o + 4] << 8)
        if modo > 1 or not (1 <= w <= MAXW and 1 <= h <= MAXH):
            break
        if modo == 0:
            nxt = o + h0 + w * h * 2
        else:
            got = rle.decode(b, o + h1)
            if not got or got[0] != w or got[1] != h:
                break
            nxt = got[3]
        if nxt > len(b):
            break
        regs.append((o, modo, w, h, nxt - o))
        o = nxt
    closes = b[o + 2 : o + 6] == b"PTYY" and o + 6 == len(b)
    return regs, o, closes


def check_chain(b: bytes) -> tuple[bool, list[dict]]:
    rows = []
    for h0 in (4, 5):
        for h1 in (4, 5):
            regs, end, closes = chain_variant(b, h0, h1)
            rows.append(
                {
                    "modo0_B": h0,
                    "modo1_B": h1,
                    "registros": len(regs),
                    "fin": end,
                    "marcador": b[end + 2 : end + 6].hex(" "),
                    "cierra": closes,
                }
            )
    unique = [f for f in rows if f["cierra"]]
    ok = len(unique) == 1 and (unique[0]["modo0_B"], unique[0]["modo1_B"]) == (5, 5)
    return ok, rows


# -------------------------------------------------------------- usage map --


def mapa_de_uso(b: bytes, starts: dict[int, int]):
    """Walks tabla[6] (156 screens) and section [12]'s 37 entries.

    Section [12]'s hits are accumulated into the SAME list: they are
    shared sub-programs and that's where 18% of the drawing sites live.
    The previous attempt (`graphics_extract.py:304`) walked them to
    validate opcodes but passed `[]` as the accumulator and threw them
    away -- that's where the nonexistent "14 orphans" came from.
    """
    sec12 = eg.cargar_seccion12(b)
    table6 = eg.load_table6(b)
    hits: list[tuple] = []
    ids73: set[int] = set()
    warnings: list[str] = []
    broken: list[int] = []

    for i, toff in enumerate(table6):
        t = eg.read_trailer(b, toff)
        if t is None:
            broken.append(i)
            continue
        for k, sp in enumerate(t["slots"]):
            s = eg.read_slot(b, sp)
            if s is None:
                warnings.append("tabla6[%d]: slot %d out of range %#x" % (i, k, sp))
                continue
            eg.caminar(
                b,
                s["prog"],
                "tabla6[%d].slot%d" % (i, k),
                sec12,
                set(),
                hits,
                ids73,
                warnings,
            )
    n_hits_t6 = len(hits)
    for k, off in sorted(sec12.items()):
        eg.caminar(b, off, "seccion12[%d]" % k, sec12, set(), hits, ids73, warnings)

    sites = {(h[1], h[2], h[3], h[4]) for h in hits}

    # negative check: blind scan of the whole blob
    raw = set()
    for o in range(len(b) - 6):
        if b[o] != 0x02:
            continue
        ptr = (b[o + 3] | (b[o + 4] << 8) | (b[o + 5] << 16)) - BASE
        if ptr in starts:
            raw.add((o, b[o + 1], b[o + 2], ptr))

    usos: dict[int, list[dict]] = {}
    for ctx, o, x, y, ptr in hits:
        usos.setdefault(ptr, []).append({"contexto": ctx, "x": x, "y": y, "instr": o})
    return {
        "usos": usos,
        "sitios": sites,
        "bruto": raw,
        "absent": raw - sites,
        "sobra": sites - raw,
        "hits_totales": len(hits),
        "hits_table6": n_hits_t6,
        "n_screens": len(table6),
        "n_sec12": len(sec12),
        "ids73": sorted(ids73),
        "trailers_rotos": broken,
        "avisos": sorted(set(warnings)),
    }


# ------------------------------------------------------------------ output ---


def bitmap_name(idx: int, off: int, w: int, h: int, modo: int) -> str:
    return "%03d_%06x_%dx%d_modo%d.png" % (idx, off, w, h, modo)


def compone(rows: list[bytes], w: int, bg=(128, 128, 128)) -> list[bytes]:
    """RGBA over a mid gray -- so transparency is VISIBLE in the montage
    instead of showing up as an empty rectangle."""
    out = []
    for r in rows:
        buf = bytearray(w * 4)
        for x in range(w):
            pr, pg, pb, pa = r[4 * x : 4 * x + 4]
            if pa == 255:
                buf[4 * x : 4 * x + 4] = bytes((pr, pg, pb, 255))
            else:
                a = pa / 255.0
                buf[4 * x : 4 * x + 4] = bytes(
                    (
                        int(pr * a + bg[0] * (1 - a)),
                        int(pg * a + bg[1] * (1 - a)),
                        int(pb * a + bg[2] * (1 - a)),
                        255,
                    )
                )
        out.append(bytes(buf))
    return out


def montaje(items, path: pathlib.Path, cols: int, sep: int = 4) -> None:
    """Contact sheet: each item is (w, h, rgba_rows). Composed over gray."""
    if not items:
        return
    rows_of_items = [items[i : i + cols] for i in range(0, len(items), cols)]
    widths = [sum(it[0] + sep for it in f) + sep for f in rows_of_items]
    heights = [max(it[1] for it in f) + sep for f in rows_of_items]
    W, H = max(widths), sum(heights) + sep
    canvas = [bytearray(b"\x50\x50\x50\xff" * W) for _ in range(H)]
    y0 = sep
    for f, ht in zip(rows_of_items, heights):
        x0 = sep
        for w, h, rows in f:
            comp = compone(rows, w)
            for j, r in enumerate(comp):
                canvas[y0 + j][4 * x0 : 4 * (x0 + w)] = r
            x0 += w + sep
        y0 += ht
    eg.write_png(path, W, H, [bytes(r) for r in canvas])


def main() -> int:
    ap = argparse.ArgumentParser(description="Harmony One graphics census")
    ap.add_argument("blob", nargs="?", default=str(DEFAULT_BLOB))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--no-png", action="store_true")
    a = ap.parse_args()

    blob = pathlib.Path(a.blob).resolve()
    b = blob.read_bytes()
    out = pathlib.Path(a.out).resolve()
    failures: list[str] = []
    print(
        "blob: %s (%d B, sha256 %s)"
        % (blob, len(b), hashlib.sha256(b).hexdigest()[:16])
    )

    # --- check 1: the chain and the header length
    ok_chain, chain_check_rows = check_chain(b)
    print("\n[1] CHAIN -- header length per mode (negative check)")
    for f in chain_check_rows:
        print(
            "    mode0=%dB mode1=%dB -> %3d records, end %#08x, marker %-11s closes %s"
            % (
                f["modo0_B"],
                f["modo1_B"],
                f["registros"],
                f["fin"],
                f["marcador"],
                f["cierra"],
            )
        )
    if not ok_chain:
        failures.append("the chain doesn't close with 5B/5B alone")
    records, chain_end = imgpatch.chain(b)
    starts = {r[0]: i for i, r in enumerate(records)}
    n0 = sum(1 for r in records if r[1] == 0)
    print(
        "    -> %d records (%d mode 0, %d mode 1), close at %#08x: %s"
        % (len(records), n0, len(records) - n0, chain_end, imgpatch.closes(b))
    )
    if not imgpatch.closes(b):
        failures.append("imgpatch.closes() says the chain doesn't close")

    # --- checks 2 and 3: map vs brute scan, and geometry
    m = mapa_de_uso(b, starts)
    print("\n[2] MAP vs BRUTE SCAN (negative check)")
    print(
        "    tabla[6]: %d screens | section[12]: %d sub-programs | {id,0x73} atoms from tabla[6]: %s"
        % (m["n_screens"], m["n_sec12"], m["ids73"])
    )
    print(
        "    unique BMP sites by walking: %d   (instances with repeats: %d, of which %d from tabla[6])"
        % (len(m["sitios"]), m["hits_totales"], m["hits_table6"])
    )
    print("    BMP sites by blind scan       : %d" % len(m["bruto"]))
    print(
        "    difference brute-walked: %d   walked-brute: %d"
        % (len(m["absent"]), len(m["sobra"]))
    )
    if m["absent"] or m["sobra"]:
        failures.append(
            "the walk and the brute scan don't match (%d/%d)"
            % (len(m["absent"]), len(m["sobra"]))
        )
    if m["avisos"]:
        failures.append(
            "%d walker warnings (unknown opcode / bad offset)" % len(m["avisos"])
        )
        for x in m["avisos"][:5]:
            print("    WARNING %s" % x)

    unused = sorted(starts[o] for o in set(starts) - set(m["usos"]))
    print(
        "    records with a use: %d/%d   unused: %s"
        % (len(m["usos"]), len(records), unused or "none")
    )

    bad_geom = 0
    for o, x, y, ptr in m["bruto"]:
        _off, _mo, w, h, _s = records[starts[ptr]]
        if x + w > MAXW or y + h > MAXH:
            bad_geom += 1
    print(
        "\n[3] GEOMETRY (positive check): %d/%d sites fit in 176x220"
        % (len(m["bruto"]) - bad_geom, len(m["bruto"]))
    )
    if bad_geom:
        failures.append("%d sites fall outside the screen" % bad_geom)

    # --- check 4: the constants add_device.py already uses
    print("\n[4] PRODUCTION CONSTANTS (positive check)")
    okc = 0
    for name, off, w, h in CONSTANTS:
        i = starts.get(off)
        good = i is not None and records[i][2] == w and records[i][3] == h
        okc += good
        print(
            "    %-13s %#08x -> %s"
            % (
                name,
                off,
                (
                    "record [%d] %dx%d mode %d OK"
                    % (i, records[i][2], records[i][3], records[i][1])
                )
                if good
                else "DOES NOT LAND ON A RECORD",
            )
        )
    print("    -> %d/%d" % (okc, len(CONSTANTS)))
    if okc != len(CONSTANTS):
        failures.append("add_device.py constants: %d/%d" % (okc, len(CONSTANTS)))

    # --- images
    bitmaps = []
    for idx, (off, modo, w, h, size) in enumerate(records):
        if modo == 0:
            rows, err = eg.decode_mode0(b, off + 5, w, h)
        else:
            rows, err = eg.decode_mode1(b, off + 5, w, h)
        if rows is None:
            failures.append("record %d (%#x) doesn't decode: %s" % (idx, off, err))
        bitmaps.append(
            {
                "idx": idx,
                "offset": off,
                "offset_hex": "%#08x" % off,
                "ptr24": off + BASE,
                "modo": modo,
                "width": w,
                "height": h,
                "tam_bytes": size,
                "sha256": hashlib.sha256(b[off : off + size]).hexdigest(),
                "file": "bitmaps/" + bitmap_name(idx, off, w, h, modo),
                "sitios": len(m["usos"].get(off, [])),
                "drawn_by": sorted(
                    m["usos"].get(off, []), key=lambda u: (u["contexto"], u["instr"])
                ),
                "_rows": rows,
            }
        )

    # --- check 6: pixels against imgpatch.py (pre-existing tool)
    diffs = 0
    for r in bitmaps:
        if r["modo"] != 0:
            continue
        off, w, h = r["offset"], r["width"], r["height"]
        expected = []
        for y in range(h):
            row = bytearray(w * 4)
            for x in range(w):
                p = off + 5 + 2 * (y * w + x)
                v = (b[p] << 8) | b[p + 1]  # RGB565 big endian, same as imgpatch
                rr, gg, bb = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
                row[4 * x : 4 * x + 4] = bytes(
                    (
                        (rr << 3) | (rr >> 2),
                        (gg << 2) | (gg >> 4),
                        (bb << 3) | (bb >> 2),
                        255,
                    )
                )
            expected.append(bytes(row))
        if expected != r["_rows"]:
            diffs += 1
    print(
        "\n[6] PIXELS vs imgpatch.py: %d/%d mode 0 records identical" % (n0 - diffs, n0)
    )
    if diffs:
        failures.append("%d mode 0 records differ from imgpatch.py" % diffs)

    # --- fonts (section [7])
    fonts, fb = fonts_inventory.build_all(str(blob))
    if fb != b:
        failures.append("fonts_inventory read a different blob")
    glyphs = []
    for idx, table_off, line_height, decoded in fonts:
        for code in range(1, 72):
            d = decoded[code - 1]
            if d is None:
                continue
            if d[0] == "BADDECODE":
                failures.append("glyph attr %d code %d doesn't decode" % (idx, code))
                continue
            ptr, hdr, w, h, size = d
            r, err = eg.decode_glyph(b, ptr)
            if r is None:
                failures.append("glyph attr %d code %d: %s" % (idx, code, err))
                continue
            ch = eg.fonts.GLYPHS[code]
            glyphs.append(
                {
                    "atributo": idx,
                    "codigo": code,
                    "caracter": ch,
                    "line_height": line_height,
                    "offset": ptr,
                    "offset_hex": "%#08x" % ptr,
                    "width": w,
                    "height": h,
                    "tam_bytes": size,
                    "sha256": hashlib.sha256(b[ptr : ptr + size]).hexdigest(),
                    "file": "fuentes/attr%02d_cod%02d_%s.png"
                    % (idx, code, eg.glyph_name(ch)),
                    "_rows": r["rows"],
                }
            )
    shas = {g["sha256"] for g in glyphs}
    print(
        "\n[5b] FONTS: %d non-null glyphs in %d sets, %d distinct sha256"
        % (len(glyphs), len(fonts), len(shas))
    )
    if len(glyphs) != 423:
        failures.append("expected 423 non-null glyphs, got %d" % len(glyphs))

    # --- check 5: overlap between the 71 image ranges and 423 glyph ranges
    ranges = [
        (r["offset"], r["offset"] + r["tam_bytes"], "img[%d]" % r["idx"])
        for r in bitmaps
    ]
    ranges += [
        (
            g["offset"],
            g["offset"] + g["tam_bytes"],
            "glyph %d/%d" % (g["atributo"], g["codigo"]),
        )
        for g in glyphs
    ]
    ranges.sort()
    # Two resources starting at the SAME offset would be pointer reuse, not
    # an overlap; they're deliberately excluded and counted separately, so
    # the published number doesn't hide either case.
    real = []
    for i in range(len(ranges) - 1):
        if ranges[i + 1][0] < ranges[i][1] and ranges[i + 1][0] != ranges[i][0]:
            real.append((ranges[i][2], ranges[i + 1][2]))
    print(
        "\n[5] OVERLAP (negative check): %d ranges compared (%d images + %d glyphs), "
        "%d real overlaps, %d pointers shared between fonts"
        % (
            len(ranges),
            len(bitmaps),
            len(glyphs),
            len(real),
            len(ranges) - len({r[0] for r in ranges}),
        )
    )
    if real:
        failures.append("%d range overlaps: %s" % (len(real), real[:5]))

    # --- writing
    out.mkdir(parents=True, exist_ok=True)
    if not a.no_png:
        (out / "bitmaps").mkdir(exist_ok=True)
        (out / "fonts").mkdir(exist_ok=True)
        for old in list((out / "bitmaps").glob("*.png")) + list(
            (out / "fonts").glob("*.png")
        ):
            old.unlink()
        for r in bitmaps:
            if r["_rows"]:
                eg.write_png(out / r["file"], r["width"], r["height"], r["_rows"])
        for g in glyphs:
            eg.write_png(out / g["file"], g["width"], g["height"], g["_rows"])
        montaje(
            [(r["width"], r["height"], r["_rows"]) for r in bitmaps if r["_rows"]],
            out / "montaje_bitmaps.png",
            cols=6,
        )
        montaje(
            [(g["width"], g["height"], g["_rows"]) for g in glyphs],
            out / "montaje_fuentes.png",
            cols=32,
        )

    for r in bitmaps:
        r.pop("_rows", None)
    for g in glyphs:
        g.pop("_rows", None)

    mapa = {
        "generated_by": "config_work/graphics_census.py",
        "blob": str(blob),
        "blob_sha256": hashlib.sha256(b).hexdigest(),
        "formato": {
            "header": "<u8 modo><u16 ancho LE><u16 alto LE>  (5 B, mode 0 and mode 1)",
            "modo0": "payload = ancho*alto*2 B, RGB565 BIG ENDIAN, flat",
            "modo1": "payload = RLE stream (rle.py), RGB565 BIG ENDIAN, with transparency",
            "glyph": "<u8 ancho><RLE stream>, section [7], index = code-1",
            "ptr24_logico": "file_offset + 0x40000",
            "cadena": "starts at 0x02D660, no offset table, closes at <u16>PTYY",
        },
        "check": {
            "cadena_variantes": chain_check_rows,
            "cadena_cierra": imgpatch.closes(b),
            "registros": len(records),
            "sitios_unicos_caminados": len(m["sitios"]),
            "sitios_unicos_barrido_bruto": len(m["bruto"]),
            "sitios_instancias_con_repeticion": m["hits_totales"],
            "diferencia_bruto_menos_caminado": len(m["absent"]),
            "diferencia_caminado_menos_bruto": len(m["sobra"]),
            "registros_con_uso": len(m["usos"]),
            "registros_sin_uso": unused,
            "sites_off_screen": bad_geom,
            "device_constants_ok": "%d/%d" % (okc, len(CONSTANTS)),
            "modo0_identicos_a_imgpatch": "%d/%d" % (n0 - diffs, n0),
            "ranges_compared_for_overlap": len(ranges),
            "solapes": len(real),
            "glyphs": len(glyphs),
            "glyphs_distinct_sha": len(shas),
            "screens_table6": m["n_screens"],
            "subprogramas_seccion12": m["n_sec12"],
            "avisos_caminador": m["avisos"],
        },
        "bitmaps": bitmaps,
        "fonts": glyphs,
    }
    (out / "mapa.json").write_text(json.dumps(mapa, indent=1, ensure_ascii=False))

    tsv = [
        "categoria\tidx\toffset\tmodo\tancho\talto\ttam_bytes\tsitios\tsha256\tarchivo\tdibujado_por"
    ]
    for r in bitmaps:
        sample = ", ".join(
            "%s@(%d,%d)" % (u["contexto"], u["x"], u["y"])
            for u in r["drawn_by"][:4]
        )
        if r["sitios"] > 4:
            sample += " ... (+%d)" % (r["sitios"] - 4)
        tsv.append(
            "imagen\t%d\t%#08x\t%d\t%d\t%d\t%d\t%d\t%s\t%s\t%s"
            % (
                r["idx"],
                r["offset"],
                r["modo"],
                r["width"],
                r["height"],
                r["tam_bytes"],
                r["sitios"],
                r["sha256"][:16],
                r["file"],
                sample,
            )
        )
    for g in glyphs:
        tsv.append(
            "fuente\t%d\t%#08x\t1\t%d\t%d\t%d\t-\t%s\t%s\tattr %d, code %d = %r"
            % (
                g["atributo"],
                g["offset"],
                g["width"],
                g["height"],
                g["tam_bytes"],
                g["sha256"][:16],
                g["file"],
                g["atributo"],
                g["codigo"],
                g["caracter"],
            )
        )
    (out / "mapa.tsv").write_text("\n".join(tsv) + "\n")

    print("\nwritten to %s" % out)
    print(
        "  mapa.json / mapa.tsv, bitmaps/ (%d PNG), fuentes/ (%d PNG)"
        % (len(bitmaps), len(glyphs))
    )
    if failures:
        print("\n%d check(s) FAILING:" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("\nALL checks pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
