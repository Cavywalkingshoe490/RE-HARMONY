#!/usr/bin/env python3
"""COMPLETE census of the Harmony One blob's graphical resources: it scans
offsets looking for valid bitmap headers, walks the 156 drawing programs of
`table[6]` to know WHO draws each one, and separates real / orphans /
false positives with a positive and a negative check.

Destination format of opcode 02 <X><Y><ptr24> (measured and documented in
`draw_bytecode.py`, reused without re-deriving it):

    <u8 mode><u8 width><u8 00><u8 height>   header, 4 B
    mode 0   flat map: `width*height` RGB565 BIG ENDIAN pixels, 2 B each
    mode 1   compressed: `rle.py` RLE stream (00 end, 80 end of row,
             01-7F literal run, 81-FF skip/transparency)

Verified against the real headers of the 3 check resources the task asks
for (bytes read straight out of the blob before writing this script):

    background 0x0e66fa  00 b0 00 dc  -> mode 0, 176x220
    icono_g    0x0a5f0e  00 a4 00 32  -> mode 0, 164x50
    icono_p    0x0e53d5  00 33 00 30  -> mode 0, 51x48
    button x6  (BOTON_BMP)  00 51 00 32  -> mode 0, 81x50 (6/6 identical)

Method (golden rule: positive AND negative check, everything else
[ASSUMED]):

  1. WALK: goes through the 156 trailers of `table[6]`, follows
     CALL/JMP/SWITCH (same interpreter as `capa_dibujo_cobertura.caminar()`,
     rewritten here so that it also records every opcode 02 with
     (ordinal, slot, X, Y, ptr)) -- this gives the list of REAL resources,
     along with who draws them.
  2. HEADER: for each unique ptr found, it reads the 4 B header, computes
     the size (mode 0: width*height*2; mode 1: decodes with `rle.py` and
     measures where the stream ends) and checks that it fits in the blob.
  3. EXHAUSTIVE SCAN (step A): tries the header grammar at EVERY offset of
     the blob. Counts how many match -- this includes false positives
     inside the pixels of real images themselves (byte coincidence).
  4. PACKED SCAN (step B): the same, but skipping the size of the resource
     after each hit (the way `rle.py`/`scan_mode0.py` do) -- giving a
     non-overlapping partition, orphan candidates.
  5. NEGATIVE CHECK: step B run over the STRUCTURE region (offset 0 ..
     BULK, which from earlier work (`sprites.py`, PLAN.md) is known not to
     contain general-purpose opcode 02 bitmaps) counts the method's false
     positives.
  6. CLASSIFICATION: every step B candidate in the BULK region that the
     walker did not visit is an ORPHAN (unused art, or a residual false
     positive -- lower confidence for mode 0 than for mode 1, because mode
     1 self-validates with the RLE stream and mode 0 does not).

Writes nothing to the blob. Does not call `write.py` or any libconcord
primitive.

Usage:
    python3 graphics_resources.py [--blob PATH] [--out DIR] [--no-png]
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import add_device as D
import rle
import fonts as F

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = D.BASE
BULK = 0x02D660  # limit measured by sprites.py: no opcode 02 lives below this
MAX_W, MAX_H = 176, 220


def u24(b, o):
    return D.u24(b, o)


def u16(b, o):
    return D.u16(b, o)


# ============================================================ 1. CAMINAR ===


def caminar_bitmaps(b, start, out_bmp, out_txt, visitado_local, prof=0):
    """Walks the bytecode from `start` (same 13-opcode interpreter as
    `draw_bytecode.py`/`capa_dibujo_cobertura.py`), recording every BITMAP (02)
    and TEXT BY POINTER (04). `visitado_local` is PER-CALL (one for each
    (ordinal, slot) above) so that a shared resource (prologue, or section
    [12]'s sub-program 2) counts as "drawn by" EVERY screen that reaches
    it, not just the first one in the whole blob."""
    if start in visitado_local or not (0 <= start < len(b)) or prof > 60:
        return
    visitado_local.add(start)
    o = start
    for _ in range(4000):
        if not (0 <= o < len(b)):
            return
        op = b[o]
        if op == 0x00:  # FIN
            return
        if op == 0x17:  # RET
            return
        if op == 0x16:  # CALL
            target = u24(b, o + 1) - BASE
            caminar_bitmaps(b, target, out_bmp, out_txt, visitado_local, prof + 1)
            o += 4
        elif op == 0x14:  # JMP
            target = u24(b, o + 1) - BASE
            caminar_bitmaps(b, target, out_bmp, out_txt, visitado_local, prof + 1)
            return
        elif op == 0x10:  # ATTR
            o += 2
        elif op == 0x02:  # BMP
            x, y = b[o + 1], b[o + 2]
            ptr = u24(b, o + 3) - BASE
            out_bmp.append((o, x, y, ptr))
            o += 6
        elif op == 0x04:  # TXT (by pointer -- glyph string, not a bitmap)
            x, y = b[o + 1], b[o + 2]
            ptr = u24(b, o + 3) - BASE
            out_txt.append((o, x, y, ptr))
            o += 6
        elif op == 0x05:  # TXTIN
            e = b.index(b"\x00", o + 3)
            o = e + 1
        elif op == 0x11:  # ATOMO
            o += 4
        elif op == 0x01:  # RECT
            o += 7
        elif op == 0x12:  # SWITCH
            nc = b[o + 2]
            q = o + 3 + 4 * nc
            n2 = b[q]
            for k in range(nc):
                target = u24(b, o + 4 + 4 * k) - BASE
                caminar_bitmaps(b, target, out_bmp, out_txt, visitado_local, prof + 1)
            for k in range(n2):
                target = u24(b, q + 3 + 5 * k) - BASE
                caminar_bitmaps(b, target, out_bmp, out_txt, visitado_local, prof + 1)
            o = q + 1 + 5 * n2
        else:
            # 0 instancias esperadas (13 opcodes cerrados, control de
            # capa_dibujo_cobertura.py); si aparece uno, cortar sin inventar.
            return


def walk_table6(b):
    """Devuelve (dibuja: {ptr: [(ordinal, slot_idx, x, y)]}, textos: idem,
    avisos: [str])."""
    dibuja: dict[int, list] = defaultdict(list)
    textos: dict[int, list] = defaultdict(list)
    avisos: list[str] = []

    table = D.scan_table6(b)
    faltantes = [i for i, t in table if t is None]
    if faltantes:
        avisos.append("trailers sin parsear: %s" % faltantes)

    for i, t in table:
        if t is None:
            continue
        for slot_idx, slot_ptr in enumerate(t["slots"]):
            sp = slot_ptr - BASE
            s = D.read_slot(b, sp)
            if s is None:
                avisos.append("slot ilegible: ordinal %d slot %d" % (i, slot_idx))
                continue
            prog_off = s["prog"] - BASE
            bmp_local: list = []
            txt_local: list = []
            caminar_bitmaps(b, prog_off, bmp_local, txt_local, set())
            for _o, x, y, ptr in bmp_local:
                dibuja[ptr].append((i, slot_idx, x, y))
            for _o, x, y, ptr in txt_local:
                textos[ptr].append((i, slot_idx, x, y))
    return dibuja, textos, avisos


# ======================================================= 2. CABECERA/SIZE ===


#: header length measured per mode -- NOT symmetric, and the asymmetry is an
#: empirical finding (see further down), not an assumption:
#:   modo 0  4 B  <00><ancho><00><alto>                     BE, consumida
#:   modo 1  5 B  <01><ancho_lo><00><alto_lo><00>            LE, DESCARTADA
#: Measured by reproducing the decoder with the 3 mode 1 images that
#: `caminar_bitmaps()` encuentra REALMENTE dibujadas (control positivo real,
#: not synthetic): with the cut at 4 B the RLE stream NEVER decodes (the
#: first byte falls mid-header and reads 0x00 = immediate "end");
#: with the cut at 5 B it decodes 3/3 exact against the declared width/height.
#: Fits with PLAN.md ("mode 1 does FOUR cfg_getbyte and discards them"):
#: the firmware ignores those 4 size bytes (it derives them from the
#: stream itself, by terminator), but the PACKER wrote them with the real
#: size anyway -- so they predict perfectly even if the decoder never uses them.
HDRLEN = {0: 4, 1: 5}


def read_bitmap_header(b, off):
    """(mode, width, height) or None if it does not match the header grammar."""
    if off < 0 or off + 4 > len(b):
        return None
    modo, width, cero, height = b[off], b[off + 1], b[off + 2], b[off + 3]
    if cero != 0 or modo not in (0, 1):
        return None
    if not (1 <= width <= MAX_W and 1 <= height <= MAX_H):
        return None
    if modo == 1:
        if off + 5 > len(b) or b[off + 4] != 0:
            return None
    return modo, width, height


def tamano_recurso(b, off, modo, width, height):
    """Total size in bytes (header + data) or None if it does not close."""
    hl = HDRLEN[modo]
    if modo == 0:
        total = hl + width * height * 2
        return total if off + total <= len(b) else None
    # mode 1: decode with rle.py (WITHOUT TOUCHING the module) and require that the
    # stream predicts EXACTLY the width/height declared in the 5 B header.
    got = rle.decode(b, off + hl, limit=2 * width * height + 4096)
    if not got:
        return None
    w, h, _rows, end = got
    if w != width or h != height:
        return None
    return end - off


# ===================================================== 3/4. BLOB SWEEPS ===


def barrido_exhaustivo(b, start, end):
    """STEP A: tries the grammar at EVERY offset. Returns dict offset->info,
    WITHOUT filtering by size/decoding (header structure only)."""
    out = {}
    for o in range(start, min(end, len(b) - 4)):
        h = read_bitmap_header(b, o)
        if h:
            out[o] = h
    return out


def barrido_empaquetado(b, start, end):
    """STEP B: like A, but it validates size/decoding and SKIPS the whole
    resource after a hit (non-overlapping partition, rle.py style)."""
    out = {}
    o = start
    end = min(end, len(b))
    while o < end - 4:
        h = read_bitmap_header(b, o)
        if h:
            modo, width, height = h
            total = tamano_recurso(b, o, modo, width, height)
            if total:
                out[o] = (modo, width, height, total)
                o += total
                continue
        o += 1
    return out


# ================================================================ PNG =====


def rgb565_be(hi, lo):
    v = (hi << 8) | lo
    r, g, bl = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (bl << 3) | (bl >> 2)


def render_modo0(b, off, width, height, path, bg=(255, 0, 255)):
    """Flat RGB565 BIG ENDIAN map (same order as `rle.py`.render()). No
    transparency -- mode 0 has no skip runs, it is pure raster."""
    data = off + 4
    ppm = bytearray(b"P6\n%d %d\n255\n" % (width, height))
    need = width * height * 2
    px = b[data : data + need]
    if len(px) < need:
        px = px + bytes(need - len(px))
    for i in range(0, need, 2):
        ppm += bytes(rgb565_be(px[i], px[i + 1]))
    tmp = path.with_suffix(".ppm")
    tmp.write_bytes(ppm)
    subprocess.run(
        ["sips", "-s", "format", "png", str(tmp), "--out", str(path)],
        capture_output=True,
    )
    tmp.unlink(missing_ok=True)


def render_modo1(b, off, width, height, path):
    ok = rle.render(b, off + HDRLEN[1], path)
    return ok is not None


# ============================================================== MAIN ======


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--blob", default=str(ROOT / "backups" / "config_raw.bin"))
    ap.add_argument("--out", default=str(ROOT / "recursos_graficos"))
    ap.add_argument(
        "--no-png", action="store_true", help="census only, no rendering"
    )
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    out_dir = pathlib.Path(a.out)
    bmp_dir = out_dir / "bitmaps"
    orph_dir = out_dir / "huerfanos"
    font_dir = out_dir / "fonts"
    for d in (out_dir, bmp_dir, orph_dir, font_dir):
        d.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("PASO 1: caminar tabla[6] (156 pantallas) -- recursos REALES")
    print("=" * 78)
    dibuja, textos, avisos = walk_table6(b)
    if avisos:
        print("avisos:", avisos)
    print("ptrs de BITMAP (opcode 02) unicos referenciados: %d" % len(dibuja))
    print("unique TEXT ptrs (opcode 04, glyph strings): %d" % len(textos))
    total_bitmap_sites = sum(len(v) for v in dibuja.values())
    print(
        "total opcode 02 instances across the 156 screens: %d" % total_bitmap_sites
    )

    # -------------------------------------------------- control positivo ---
    print("\n" + "=" * 78)
    print("POSITIVE CHECK: the resources the task demands be found")
    print("=" * 78)
    import draw_bytecode as DB

    checks = [
        ("fondo 176x220", DB.PTR_FONDO),
        ("icono TV grande 164x50", DB.PTR_ICONO_TV_G),
        ("icono TV chico 51x48", DB.PTR_ICONO_TV_P),
        ("icono Home grande 164x50", DB.PTR_ICONO_HOME_G),
        ("icono Home chico 51x48", DB.PTR_ICONO_HOME_P),
        ("icono DVR grande 164x50", DB.PTR_ICONO_DVR_G),
        ("icono DVR chico 51x48", DB.PTR_ICONO_DVR_P),
    ]
    for i, off in enumerate(D.BUTTON_BMP):
        checks.append(("boton grilla %d, 81x50" % i, off))

    positive_control_ok = True
    for name, off in checks:
        hdr = read_bitmap_header(b, off)
        hallado = off in dibuja
        state = "OK" if (hdr and hallado) else "** FALLA **"
        if not (hdr and hallado):
            positive_control_ok = False
        print(
            "  %-30s off=%#08x  header=%s  found_by_walker=%s  %s"
            % (name, off, hdr, hallado, state)
        )
    print(
        "\nCONTROL POSITIVO: %s"
        % ("7/7 + 6 botones OK" if positive_control_ok else "FALLO -- ver arriba")
    )

    # ---------------------------------------------- clasificar recursos reales
    print("\n" + "=" * 78)
    print("STEP 2: header + size of every REAL resource")
    print("=" * 78)
    recursos = {}  # off -> dict
    no_valid_header = []
    for off, sitios in dibuja.items():
        hdr = read_bitmap_header(b, off)
        if hdr is None:
            no_valid_header.append(off)
            continue
        modo, width, height = hdr
        total = tamano_recurso(b, off, modo, width, height)
        recursos[off] = {
            "modo": modo,
            "width": width,
            "height": height,
            "tam": total,
            "sitios": sitios,
            "state": "real" if total else "real-but-does-not-decode",
        }
    print("recursos reales con cabecera valida: %d" % len(recursos))
    print(
        "recursos reales SIN cabecera valida (anomalos): %d" % len(no_valid_header)
    )
    if no_valid_header:
        for off in no_valid_header[:15]:
            print(
                "  %#08x  bytes: %s  (dibujado en %d sitio(s))"
                % (off, b[off : off + 6].hex(" "), len(dibuja[off]))
            )
    no_decodifica = [o for o, r in recursos.items() if r["state"] != "real"]
    if no_decodifica:
        print(
            "real resources with an OK header but whose RLE does not close: %d"
            % len(no_decodifica)
        )
        for off in no_decodifica[:15]:
            print("  %#08x  %s" % (off, recursos[off]))

    by_mode = defaultdict(int)
    for r in recursos.values():
        by_mode[r["modo"]] += 1
    print(
        "\npor modo: modo 0 (mapa plano) = %d, modo 1 (RLE) = %d"
        % (by_mode[0], by_mode[1])
    )

    # ------------------------------------------------- paso 3: exhaustivo ---
    print("\n" + "=" * 78)
    print("STEP 3: EXHAUSTIVE scan (every offset of the blob)")
    print("=" * 78)
    estructura = barrido_exhaustivo(b, 0, BULK)
    bulk = barrido_exhaustivo(b, BULK, len(b))
    print(
        "region ESTRUCTURA (0..%#x, %d B): %d offsets matchean la gramatica"
        % (BULK, BULK, len(estructura))
    )
    print(
        "region BULK (%#x..fin, %d B): %d offsets matchean la gramatica"
        % (BULK, len(b) - BULK, len(bulk))
    )
    print(
        "(includes matches INSIDE the pixels of real resources -- "
        "this is not a resource count, it is the grammar's base rate)"
    )

    # ------------------------------------------------- paso 4: empaquetado -
    print("\n" + "=" * 78)
    print("PASO 4: barrido EMPAQUETADO (particion sin superposicion)")
    print("=" * 78)
    pack_estructura = barrido_empaquetado(b, 0, BULK)
    pack_bulk = barrido_empaquetado(b, BULK, len(b))
    print("region ESTRUCTURA: %d recursos empaquetados" % len(pack_estructura))
    print("region BULK: %d recursos empaquetados" % len(pack_bulk))

    # ------------------------------------------------- control negativo ---
    print("\n" + "=" * 78)
    print("NEGATIVE CHECK: the STRUCTURE region should not have op02 bitmaps")
    print("=" * 78)
    fp_estructura = [o for o in pack_estructura if o not in recursos]
    print(
        "false positives of the PACKED scan in STRUCTURE: %d / %d "
        "(%.1f%% of what it found there)"
        % (
            len(fp_estructura),
            len(pack_estructura),
            100 * len(fp_estructura) / max(1, len(pack_estructura)),
        )
    )
    fp_modo = defaultdict(int)
    for o in fp_estructura:
        fp_modo[pack_estructura[o][0]] += 1
    print(
        "  of that: mode 0 = %d, mode 1 (self-validated by RLE) = %d"
        % (fp_modo[0], fp_modo[1])
    )
    if fp_modo[1]:
        print(
            "  ** mode 1 has self-validation by RLE decoding -- that there are "
            "%d in a region with NO images is a real signal, not noise, check **"
            % fp_modo[1]
        )

    # ---------------------------------------------------------- huerfanos -
    print("\n" + "=" * 78)
    print("ORPHANS: candidates in BULK that the walker NEVER draws")
    print("=" * 78)
    huerfanos = {}
    superpuestos = 0
    for off, (modo, width, height, total) in pack_bulk.items():
        if off in recursos:
            continue
        # discard it if it falls STRICTLY inside the range of an already known
        # real resource (byte coincidence inside its own pixels)
        inside_real = False
        for roff, r in recursos.items():
            if r["tam"] and roff < off < roff + r["tam"]:
                inside_real = True
                break
        if inside_real:
            superpuestos += 1
            continue
        huerfanos[off] = (modo, width, height, total)
    print(
        "candidatos huerfanos (fuera de cualquier recurso real conocido): %d"
        % len(huerfanos)
    )
    print(
        "discarded for falling INSIDE the pixels of a real resource: %d"
        % superpuestos
    )

    # mode 0 does not self-validate (there is no stream that can fail): a header
    # that by chance lands right before a long run of a single byte (0x00
    # of unused flash, typically) "validates" all the same because the size
    # check only looks at whether it fits in the blob. Extra filter, cheap
    # and verifiable: if the whole payload is ONE SINGLE repeated byte, there
    # is no way to tell it from padding -- it is reclassified apart, it is not
    # counted as a trusted orphan.
    def es_relleno_constante(off, modo, width, height):
        hl = HDRLEN[modo]
        payload = b[off + hl : off + hl + width * height * 2]
        return len(set(payload)) <= 1

    huerfanos_relleno = {}
    for off in list(huerfanos):
        modo, width, height, total = huerfanos[off]
        if modo == 0 and es_relleno_constante(off, modo, width, height):
            huerfanos_relleno[off] = huerfanos.pop(off)

    horph_modo = defaultdict(int)
    for m, _a, _h, _t in huerfanos.values():
        horph_modo[m] += 1
    print(
        "  of the orphans: mode 0 with real pixel variation (medium confidence) = %d"
        % horph_modo[0]
    )
    print(
        "  of the orphans: mode 1 (high confidence, RLE self-validated) = %d"
        % horph_modo[1]
    )
    print(
        "  reclassified as PROBABLE FALSE POSITIVE (payload of a single "
        "byte repetido, modo 0 sin autovalidacion): %d -- %s"
        % (len(huerfanos_relleno), [hex(o) for o in sorted(huerfanos_relleno)])
    )

    # ============================================================ FUENTES =
    print("\n" + "=" * 78)
    print("FONTS (section [7]): 18 glyph sets, already mapped in FUENTES.md")
    print("=" * 78)
    fonts = F.fonts_by_attribute(b)
    glyph_offsets: dict[int, set[tuple[int, int]]] = defaultdict(
        set
    )  # off -> {(attr, codigo)}
    for attr, f in fonts.items():
        for i, p in enumerate(f["ptr"]):
            if p:
                glyph_offsets[p].add((attr, i + 1))
    print("atributos (fuentes): %d" % len(fonts))
    print(
        "ranuras no nulas (attr,codigo): %d"
        % sum(len(f["ptr"]) - f["ptr"].count(0) for f in fonts.values())
    )
    print(
        "bitmaps de glifo UNICOS (offset compartido entre fuentes): %d"
        % len(glyph_offsets)
    )

    # =========================================================== EXPORTAR =
    print("\n" + "=" * 78)
    print("EXPORTAR: manifest + PNGs a %s" % out_dir)
    print("=" * 78)

    manifest_path = out_dir / "mapa.tsv"
    with open(manifest_path, "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(
            [
                "categoria",
                "offset_hex",
                "modo",
                "width",
                "height",
                "tam_bytes",
                "state",
                "n_sitios",
                "drawn_by",
                "file",
            ]
        )

        n_png = 0
        for off in sorted(recursos):
            r = recursos[off]
            fname = "%06x_%dx%d_m%d.png" % (off, r["width"], r["height"], r["modo"])
            fpath = bmp_dir / fname
            if not a.no_png and r["tam"]:
                try:
                    if r["modo"] == 0:
                        render_modo0(b, off, r["width"], r["height"], fpath)
                    else:
                        render_modo1(b, off, r["width"], r["height"], fpath)
                    n_png += 1
                except Exception as e:
                    fname = "(error render: %s)" % e
            ordinales = sorted({o_ for o_, s_, x_, y_ in r["sitios"]})
            MUESTRA = 12
            sitios = ";".join(
                "ord%d.slot%d@(%d,%d)" % (o_, s_, x_, y_)
                for o_, s_, x_, y_ in r["sitios"][:MUESTRA]
            )
            if len(r["sitios"]) > MUESTRA:
                sitios += "...+%d more site(s) across %d ordinal(s) in total" % (
                    len(r["sitios"]) - MUESTRA,
                    len(ordinales),
                )
            w.writerow(
                [
                    "bitmap",
                    "%#08x" % off,
                    r["modo"],
                    r["width"],
                    r["height"],
                    r["tam"] or "",
                    r["state"],
                    len(r["sitios"]),
                    sitios,
                    fname,
                ]
            )

        for off in sorted(huerfanos):
            modo, width, height, total = huerfanos[off]
            fname = "huerfano_%06x_%dx%d_m%d.png" % (off, width, height, modo)
            fpath = orph_dir / fname
            if not a.no_png:
                try:
                    if modo == 0:
                        render_modo0(b, off, width, height, fpath)
                    else:
                        render_modo1(b, off, width, height, fpath)
                    n_png += 1
                except Exception as e:
                    fname = "(error render: %s)" % e
            w.writerow(
                [
                    "huerfano",
                    "%#08x" % off,
                    modo,
                    width,
                    height,
                    total,
                    "huerfano-confianza-%s" % ("alta" if modo == 1 else "media"),
                    0,
                    "",
                    fname,
                ]
            )

        for off in sorted(huerfanos_relleno):
            modo, width, height, total = huerfanos_relleno[off]
            fname = "sospechoso_%06x_%dx%d_m%d.png" % (off, width, height, modo)
            fpath = orph_dir / fname
            if not a.no_png:
                try:
                    render_modo0(b, off, width, height, fpath)
                    n_png += 1
                except Exception as e:
                    fname = "(error render: %s)" % e
            w.writerow(
                [
                    "huerfano",
                    "%#08x" % off,
                    modo,
                    width,
                    height,
                    total,
                    "probable-false-positive (payload of 1 single repeated byte)",
                    0,
                    "",
                    fname,
                ]
            )

        # fonts: copy from mode0_glyphs if it exists, if not, do not block
        glyphs_src = pathlib.Path(__file__).resolve().parent / "mode0_glyphs"
        copiados = 0
        for off, usos in sorted(glyph_offsets.items()):
            glyph_width = b[off] if off < len(b) else None
            used_by = ";".join("attr%d:cod%d" % (a_, c_) for a_, c_ in sorted(usos))
            fname = "glifo_%06x_w%s.png" % (off, glyph_width)
            # look for the already-extracted PNG (named g###_o<off in hex 6>_w#_h#.png)
            match = (
                list(glyphs_src.glob("g*_o%06x_*.png" % off))
                if glyphs_src.exists()
                else []
            )
            if match and not a.no_png:
                dst = font_dir / fname
                dst.write_bytes(match[0].read_bytes())
                copiados += 1
                fname_out = fname
            else:
                fname_out = "(no extraido: %s)" % fname
            w.writerow(
                [
                    "source",
                    "%#08x" % off,
                    0,
                    glyph_width,
                    "",
                    "",
                    "glifo-modo0",
                    len(usos),
                    used_by,
                    fname_out,
                ]
            )
        print(
            "glyphs copied from mode0_glyphs/: %d / %d"
            % (copiados, len(glyph_offsets))
        )

    print("manifest: %s" % manifest_path)
    print("PNGs de bitmap/huerfano escritos: %d" % n_png)

    print("\n" + "=" * 78)
    print("RESUMEN")
    print("=" * 78)
    print("REAL resources (drawn by some opcode 02):            %d" % len(recursos))
    print("  modo 0 (mapa plano):                                %d" % by_mode[0])
    print("  modo 1 (RLE comprimido):                            %d" % by_mode[1])
    print(
        "ORPHAN resources (valid, with pixel variation, nobody draws them): %d"
        % len(huerfanos)
    )
    print("  de eso modo 1 (RLE autovalidado, confianza alta):   %d" % horph_modo[1])
    print("  de eso modo 0 (mapa plano, confianza media):        %d" % horph_modo[0])
    print(
        "bitmaps de FUENTE (glifos, seccion [7], unicos):      %d" % len(glyph_offsets)
    )
    print(
        "false positives of the method (negative control, STRUCTURE region): %d"
        % len(fp_estructura)
    )
    print(
        "false positives reclassified in BULK (payload of 1 repeated byte): %d"
        % len(huerfanos_relleno)
    )
    print(
        "TOTAL falsos positivos conocidos (estructura + relleno constante en bulk): %d"
        % (len(fp_estructura) + len(huerfanos_relleno))
    )
    print(
        "candidates discarded for falling inside the pixels of a real resource: %d"
        % superpuestos
    )
    print("\nCONTROL POSITIVO: %s" % ("PASA" if positive_control_ok else "FALLA"))

    return 0 if positive_control_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
