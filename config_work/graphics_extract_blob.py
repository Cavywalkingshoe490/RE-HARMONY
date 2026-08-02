#!/usr/bin/env python3
"""Extracts ALL of the blob's graphical resources (icons, sprites, backgrounds
and font glyphs) with their map: who draws them, at what coordinates, and
which device they belong to where that applies.

Invents nothing new: it reuses what is already verified.
  - `device.scan_table6/read_slot/read_header/read_key_register`
    to walk the 156 objects of `table[6]` (same as
    `capa_dibujo_cobertura.caminar()`, but per-OBJECT instead of globally
    deduplicated, so that every drawing site can be attributed to ALL the
    ordinals that reach it, not just the first one).
  - `rle.py` to decode mode 1 (RLE) of the general bitmaps.
  - `fonts.py` for section [7] (18 fonts, 423 non-null glyphs) and its own
    glyph RLE decoder (`_rle`, a smaller format: no <mode><00><height>
    header, just <u8 width><stream>).

HEADER of a bitmap pointed at by opcode 02 -- 5 bytes, BOTH MODES,
CORRECTED this session against an earlier 4-byte reading that seemed to
work (see "THE CORRECTION" below, and `resolver_orden_y_cabecera()`):

    <u8 mode><u16 width LE><u16 height LE>    5 bytes, mode and width/height
                                               same as ESTADO.md/draw_bytecode.py
                                               (the 3rd and 5th byte ALWAYS
                                               0x00 in this blob because the
                                               panel never goes past 220 px:
                                               they are the high byte of each
                                               u16, not loose "padding")
    mode 0  payload = width*height*2 B, RGB565 BIG ENDIAN, flat
    mode 1  payload = RLE stream (rle.decode), BIG ENDIAN, ends in 0x00

    THE CORRECTION: ESTADO.md/draw_bytecode.py describe the header as 4
    bytes (<mode><width><00><height>) and the payload as RGB565 (without
    pinning the order there -- `rle.py` pins it for ANOTHER region, the
    sprite heap at 0x02D660, as BIG ENDIAN). This session started out
    taking the payload at offset+4 little endian because ONE icon (164x50,
    the big TV) decodes cleanly that way -- but it is an IMAGE WITH A SOFT
    GRADIENT, and a soft gradient still looks almost right if every pixel
    gets the low byte of the neighbour next to it stuck onto it (which is
    EXACTLY what reading one byte too far to the left with the order
    flipped produces: even "wrong", the error is one neighbouring pixel,
    imperceptible in a gradient). The SMALL icon (51x48, TV with a
    hard-contrast frame) gives it away: offset+4 LE leaves a rainbow band
    glued to the vertical frame (the seam of the 1-byte shift); offset+5
    BIG ENDIAN removes it entirely -- identical image but without the
    seam. And the check that DECIDES, not just suggests: the SEQUENTIAL
    bitmap chain of `imgpatch.py` (71 records from 0x02D660, each one
    ending where the next one starts) ONLY closes exactly on the final
    `PTYY` marker with a 5-byte header -- with 4 bytes it goes out of
    alignment and the second record no longer parses (negative check: it
    was verified that it breaks). An exact chain close after 71 jumps does
    not come out by chance. It is corrected here: 5 bytes, BIG ENDIAN,
    both modes.

CHECK, assuming nothing:
  positive   every ptr24 of every drawing site RESOLVES (offset in range,
             mode in {0,1}, decodes, and for mode 0 the payload fits in the
             blob); reported in the map. On top of that,
             `verificar_cadena_heap()` walks the 71 SEQUENTIAL records of
             the 0x02D660 heap with this same 5-byte header and confirms
             they close exactly on the `PTYY` marker -- the strong
             structural check.
  negative   no pair of resources (by byte range) overlaps; if it finds an
             overlap, it prints it instead of keeping quiet. And the SAME
             chain with a 4-byte header (the discarded reading) is verified
             to BREAK (it does not reach the 71 records).

Writes nothing to the blob, and calls neither `write.py` nor any libconcord
primitive. It only reads `--blob` and writes PNGs + JSON + MD into `--out`.

Usage:
    python3 graphics_extract_blob.py [--blob PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import add_device as D  # noqa: E402
import fonts  # noqa: E402
import rle  # noqa: E402

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = D.BASE

X_ICONO_GRANDE, X_ICONO_CHICO = 0x06, 0x0B
W_ICONO_GRANDE, H_ICONO_GRANDE = 164, 50
W_ICONO_CHICO, H_ICONO_CHICO = 51, 48
X_TEXTO_FILA = 0x3F

MAESTRO_SECCION12 = 0x0C + 4 * 12  # 0x3C


def u24(b, o):
    return D.u24(b, o)


def u16(b, o):
    return D.u16(b, o)


# ============================================================ decodificacion


def resolver_ptr(b: bytes, ptr_off: int):
    """Reads the 5-byte header at `ptr_off` (file offset, ALREADY without
    BASE): `<u8 modo><u16 width LE><u16 height LE>` -- BOTH modes (see
    "THE CORRECTION" in the module docstring). Returns a dict with the
    result, or {'ok': False, 'error': ...} if something does not close --
    never raises."""
    n = len(b)
    if not (0 <= ptr_off < n - 5):
        return {"ok": False, "error": "offset outside the blob"}
    modo = b[ptr_off]
    width = b[ptr_off + 1] | (b[ptr_off + 2] << 8)
    height = b[ptr_off + 3] | (b[ptr_off + 4] << 8)
    if modo not in (0, 1):
        return {"ok": False, "error": "mode %d is neither 0 nor 1" % modo}
    if not (1 <= width <= 176 and 1 <= height <= 220):
        return {
            "ok": False,
            "error": "width/height %dx%d outside the panel (176x220)" % (width, height),
        }
    payload_off = ptr_off + 5
    if modo == 0:
        payload_len = width * height * 2
        if payload_off + payload_len > n:
            return {"ok": False, "error": "payload modo 0 excede el blob"}
        return {
            "ok": True,
            "modo": 0,
            "width": width,
            "height": height,
            "header_len": 5,
            "payload_off": payload_off,
            "payload_len": payload_len,
        }
    got = rle.decode(b, payload_off, limit=min(2 * 176 * 220 + 4096, n - payload_off))
    if not got:
        return {"ok": False, "error": "stream RLE modo 1 no decodifica"}
    w, h, rows, end = got
    if (w, h) != (width, height):
        return {
            "ok": False,
            "error": "RLE decodes %dx%d, header says %dx%d" % (w, h, width, height),
        }
    return {
        "ok": True,
        "modo": 1,
        "width": width,
        "height": height,
        "header_len": 5,
        "payload_off": payload_off,
        "payload_len": end - payload_off,
        "rows": rows,
    }


def render_mode0(b: bytes, payload_off: int, w: int, h: int, path: pathlib.Path):
    # RGB565 LITTLE ENDIAN. WATCH OUT, this contradicts what `rle.py` says for the
    # RGB565 BIG ENDIAN -- same as the sprite heap at 0x02D660 (`rle.py`)
    # and as the same payload in mode 1 (see "THE CORRECTION" in the module
    # docstring: it was confirmed by the `imgpatch.py` chain closing exactly
    # on `PTYY` and by the one-pixel seam that disappears from the
    # small TV icon's frame when going from offset+4 LE to offset+5 BE).
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            p = payload_off + 2 * (y * w + x)
            v = (b[p] << 8) | b[p + 1]
            r, g, bl = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
            px[x, y] = ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (bl << 3) | (bl >> 2))
    img.save(path)


def render_mode1(b: bytes, rows, w: int, h: int, path: pathlib.Path):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(rows):
        x = 0
        for kind, n, src in row:
            if kind == "lit":
                for k in range(n):
                    v = (b[src + 2 * k] << 8) | b[src + 2 * k + 1]
                    r, g, bl = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
                    px[x + k, y] = (
                        (r << 3) | (r >> 2),
                        (g << 2) | (g >> 4),
                        (bl << 3) | (bl >> 2),
                        255,
                    )
            x += n
    img.save(path)


def render_glyph(b: bytes, rows, w: int, h: int, path: pathlib.Path):
    render_mode1(b, rows, w, h, path)


HEAP_INICIO = 0x02D660  # `sprites.py`/`imgpatch.py`: the bitmap heap starts here


def verificar_cadena_heap(b: bytes, header_bytes: int):
    """STRUCTURAL CHECK of the header length (5 bytes vs the 4 that
    seemed to work at first -- see "THE CORRECTION" in the module
    docstring). The bitmap heap at `HEAP_INICIO` is a CHAIN: each record
    (mode 0 or 1) ends exactly where the next one starts, up to a
    `<u16 checksum>PTYY` closer (the same one
    `configcheck.py`/`imgpatch.py` verifies). Walking it with the wrong
    header knocks the first `width/height` of the NEXT record out of
    alignment almost immediately -- there is no room for 71 jumps to land
    right by chance. Returns (n_records, final_offset, closes: bool)."""
    o, n_reg = HEAP_INICIO, 0
    while o < len(b) - header_bytes:
        modo = b[o]
        w = b[o + 1] | (b[o + 2] << 8)
        h = b[o + 3] | (b[o + 4] << 8)
        if modo > 1 or not (1 <= w <= 176 and 1 <= h <= 220):
            break
        if modo == 0:
            nxt = o + header_bytes + w * h * 2
        else:
            got = rle.decode(b, o + header_bytes, limit=2 * 176 * 220 + 4096)
            if not got or got[0] != w or got[1] != h:
                break
            nxt = got[3]
        if nxt > len(b):
            break
        o = nxt
        n_reg += 1
    cierra = b[o + 2 : o + 6] == b"PTYY" and o + 6 == len(b)
    return n_reg, o, cierra


# ==================================================================== camino


def caminar_local(b, start, sitios, visitado, ordinal, slot, prof=0):
    """Walks the bytecode from `start`, collecting BITMAP sites (op 0x02).
    `visitado` is LOCAL to this (ordinal, slot): it avoids cycles inside a
    single object, but is NOT shared between objects -- that way a shared
    sub-program (e.g. section [12] entry 2) gets walked once FOR EACH
    object that reaches it, and every site ends up attributed to all of
    them."""
    if start in visitado or not (0 <= start < len(b)) or prof > 60:
        return
    visitado.add(start)
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
            caminar_local(b, target, sitios, visitado, ordinal, slot, prof + 1)
            o += 4
        elif op == 0x14:  # JMP
            target = u24(b, o + 1) - BASE
            caminar_local(b, target, sitios, visitado, ordinal, slot, prof + 1)
            return
        elif op == 0x10:  # ATTR
            o += 2
        elif op == 0x02:  # BMP
            x, y = b[o + 1], b[o + 2]
            ptr = u24(b, o + 3) - BASE
            sitios.append(
                {
                    "ordinal": ordinal,
                    "slot": slot,
                    "instr_off": o,
                    "x": x,
                    "y": y,
                    "ptr": ptr,
                }
            )
            o += 6
        elif op == 0x04:  # TXT (pointer to glyphs, not a bitmap)
            o += 6
        elif op == 0x05:  # TXTIN
            e = b.index(b"\x00", o + 3)
            o = e + 1
        elif op == 0x11:  # ATOMO -- {2, 0x73} re-enters the sub-program
            id_ = u16(b, o + 1)
            category = b[o + 3]
            if (
                category == 0x73
                and id_ == SEC12_ID_USADO[0]
                and SEC12_TARGET[0] is not None
            ):
                caminar_local(
                    b, SEC12_TARGET[0], sitios, visitado, ordinal, slot, prof + 1
                )
            o += 4
        elif op == 0x01:  # RECT
            o += 7
        elif op == 0x12:  # SWITCH
            nc = b[o + 2]
            q = o + 3 + 4 * nc
            n2 = b[q]
            tot = 1 + 1 + 1 + 4 * nc + 1 + 5 * n2
            for k in range(nc):
                target = u24(b, o + 4 + 4 * k) - BASE
                caminar_local(b, target, sitios, visitado, ordinal, slot, prof + 1)
            for k in range(n2):
                target = u24(b, q + 3 + 5 * k) - BASE
                caminar_local(b, target, sitios, visitado, ordinal, slot, prof + 1)
            o += tot
        else:
            return  # opcode desconocido: no seguir inventando


SEC12_ID_USADO = [2]  # measured: the 135 {id,0x73} sites always use id==2
SEC12_TARGET = [None]  # filled in by main() by resolving the master index


def resolver_seccion12_entrada(b: bytes, entrada: int) -> int | None:
    """Follows the SAME path as the firmware for `{entrada, 0x73}` (measured
    in `capa_dibujo_cobertura.py`): master index[12] -> <u16 count><count
    x ptr24> -> entry `entrada` (0-based). Returns a file offset or None
    if something does not resolve."""
    off12 = u24(b, MAESTRO_SECCION12) - BASE
    if not (0 <= off12 < len(b) - 2):
        return None
    cnt = u16(b, off12)
    if not (0 <= entrada < cnt):
        return None
    p = off12 + 2 + 3 * entrada
    if p + 3 > len(b):
        return None
    return u24(b, p) - BASE


# =============================================================== nombres


def decode_glyphs(b: bytes, off: int, max_len: int = 40) -> str | None:
    """Decodes a string of glyph codes (0x00-terminated) with the BIJECTIVE
    table from `fonts.GLYPHS`. `?` for codes outside the table."""
    if not (0 <= off < len(b)):
        return None
    out = []
    o = off
    for _ in range(max_len):
        if o >= len(b):
            return None
        c = b[o]
        if c == 0:
            return "".join(out)
        out.append(fonts.GLYPHS.get(c, "?"))
        o += 1
    return None


# ======================================================================= main


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--blob", default=str(ROOT / "backups" / "config_raw.bin"))
    ap.add_argument("--out", default=str(ROOT / "graphics"))
    a = ap.parse_args()

    if Image is None:
        print("Pillow MISSING (pip install pillow) -- PNGs cannot be written")
        return 1

    b = pathlib.Path(a.blob).read_bytes()
    out_dir = pathlib.Path(a.out)
    dir_bitmaps = out_dir / "bitmaps"
    dir_fonts = out_dir / "fonts"
    dir_bitmaps.mkdir(parents=True, exist_ok=True)
    dir_fonts.mkdir(parents=True, exist_ok=True)

    SEC12_TARGET[0] = resolver_seccion12_entrada(b, SEC12_ID_USADO[0])
    print(
        "seccion [12] entrada %d -> %s"
        % (
            SEC12_ID_USADO[0],
            "%#08x" % SEC12_TARGET[0] if SEC12_TARGET[0] is not None else "NO RESUELVE",
        )
    )

    # ---------------------------------------------------- 1) recorrer tabla[6]
    table = D.scan_table6(b)
    faltantes = [i for i, t in table if t is None]
    if faltantes:
        print("ABORTA: trailers sin parsear:", faltantes)
        return 1

    all_sites = []
    avisos_recorrido = []
    for ordinal, t in table:
        for k, slot_ptr in enumerate(t["slots"]):
            sp = slot_ptr - BASE
            if not (0 <= sp < len(b) - 7):
                avisos_recorrido.append(
                    "ordinal %d slot %d: puntero fuera de rango" % (ordinal, k)
                )
                continue
            slot = D.read_slot(b, sp)
            if slot is None:
                avisos_recorrido.append("ordinal %d slot %d: no parsea" % (ordinal, k))
                continue
            prog_off = slot["prog"] - BASE
            visitado = set()
            caminar_local(b, prog_off, all_sites, visitado, ordinal, k)

    print(
        "sitios BITMAP (opcode 02) encontrados recorriendo tabla[6]: %d"
        % len(all_sites)
    )

    # -------------------------------------------- 2) parear iconos de dispositivo
    by_block: dict[tuple[int, int], list[dict]] = {}
    for s in all_sites:
        by_block.setdefault((s["ordinal"], s["slot"]), []).append(s)
    pares_icono: dict[int, dict] = {}  # instr_off of the big one -> info
    for (ordinal, slot), sitios in by_block.items():
        by_off = {s["instr_off"]: s for s in sitios}
        for s in sitios:
            if s["x"] != X_ICONO_GRANDE:
                continue
            chico = by_off.get(s["instr_off"] + 6)
            if chico is None or chico["x"] != X_ICONO_CHICO or chico["y"] != s["y"] + 1:
                continue
            name = None
            for op in D.disassemble(b, chico["instr_off"] + 6, limite=6):
                _o, kind, args = op
                if kind == "TXT":
                    name = decode_glyphs(b, args[2])
                    break
                if kind == "TXTIN":
                    name = "".join(fonts.GLYPHS.get(g, "?") for g in args[2])
                    break
                if kind not in ("ATTR",):
                    break
            info = {
                "grande_ptr": s["ptr"],
                "chico_ptr": chico["ptr"],
                "name": name,
                "ordinal": ordinal,
                "slot": slot,
            }
            pares_icono[s["ptr"]] = info
            pares_icono.setdefault("_chicos", {})
    ptr_to_device: dict[int, tuple[str, str | None]] = {}
    for ptr, info in pares_icono.items():
        if ptr == "_chicos":
            continue
        ptr_to_device[info["grande_ptr"]] = ("grande", info["name"])
        ptr_to_device[info["chico_ptr"]] = ("chico", info["name"])

    # ------------------------------------------------ 3) resource by unique ptr
    recursos: dict[int, dict] = {}
    ptr_resueltos = 0
    ptr_total = len(all_sites)
    errores_resolucion = []
    for s in all_sites:
        ptr = s["ptr"]
        res = recursos.get(ptr)
        if res is None:
            info = resolver_ptr(b, ptr)
            if info["ok"]:
                ptr_resueltos += 1
            else:
                errores_resolucion.append((ptr, s["ordinal"], s["slot"], info["error"]))
            icon_kind, disp_name = ptr_to_device.get(ptr, (None, None))
            res = {
                "offset": ptr,
                "resuelto": info["ok"],
                "info": info,
                "is_device_icon": icon_kind,
                "device": disp_name,
                "drawn_by": [],
            }
            recursos[ptr] = res
        else:
            ptr_resueltos += 1 if res["resuelto"] else 0
        clave = (s["ordinal"], s["slot"], s["x"], s["y"], s["instr_off"])
        if clave not in {
            (d["ordinal"], d["slot"], d["x"], d["y"], d["instr_off"])
            for d in res["drawn_by"]
        }:
            res["drawn_by"].append(
                {
                    "ordinal": s["ordinal"],
                    "slot": s["slot"],
                    "x": s["x"],
                    "y": s["y"],
                    "instr_off": s["instr_off"],
                }
            )
    # ptr_resueltos recomputed properly (per site, not per unique resource) for the
    # reporte "positivo": cuantos SITIOS de dibujo resuelven
    ptr_resueltos = sum(1 for s in all_sites if recursos[s["ptr"]]["resuelto"])

    print(
        "\nPOSITIVE CONTROL: ptr24 that resolve: %d/%d drawing sites"
        % (ptr_resueltos, ptr_total)
    )
    if errores_resolucion:
        print("  %d SIN resolver (detalle, primeros 10):" % len(errores_resolucion))
        for ptr, ordn, slot, err in errores_resolucion[:10]:
            print("    ordinal %d slot %d -> ptr %#x: %s" % (ordn, slot, ptr, err))

    ok_resueltos = [r for r in recursos.values() if r["resuelto"]]
    print(
        "UNIQUE resources (by offset) resolved: %d of %d"
        % (len(ok_resueltos), len(recursos))
    )

    # --------------------------------- 3b) STRUCTURAL CHECK: heap chain, 5 B vs 4 B
    n5, off5, cierra5 = verificar_cadena_heap(b, 5)
    n4, off4, cierra4 = verificar_cadena_heap(b, 4)
    print("\nCONTROL bitmap header -- heap chain from %#x:" % HEAP_INICIO)
    print(
        "  5 B (chosen): %d records, ends at %#x, closes on PTYY: %s"
        % (n5, off5, cierra5)
    )
    print(
        "  4 B (discarded): %d records, ends at %#x, closes on PTYY: %s"
        % (n4, off4, cierra4)
    )
    if not cierra5:
        print(
            "  ** the 5 B header does NOT close the chain -- check before going on **"
        )
    if cierra4:
        print(
            "  ** warning: the 4 B one closed TOO -- the check no longer discriminates **"
        )

    # ----------------------------------------------------------- 4) control de solape
    rangos = []
    for r in ok_resueltos:
        i = r["info"]
        rangos.append(
            (
                r["offset"],
                r["offset"] + i["header_len"] + i["payload_len"],
                "bitmap:%#x" % r["offset"],
            )
        )

    # -------------------------------------------------------- 5) recursos de fuente
    fpa = fonts.fonts_by_attribute(b)
    glyph_resources = []
    seen_glyph_ptr: dict[int, dict] = {}
    for attr, f in sorted(fpa.items()):
        for idx, ptr in enumerate(f["ptr"]):
            if not ptr:
                continue
            codigo = idx + 1
            caracter = fonts.GLYPHS.get(codigo, "?")
            existente = seen_glyph_ptr.get(ptr)
            if existente is not None:
                existente["also_used_by"].append(
                    {"atributo": attr, "codigo": codigo}
                )
                continue
            got = fonts._rle(b, ptr + 1)
            entry = {
                "offset": ptr,
                "declared_width": b[ptr] if ptr < len(b) else None,
                "atributo": attr,
                "codigo": codigo,
                "caracter": caracter,
                "also_used_by": [],
            }
            if got is None:
                entry["resuelto"] = False
                entry["error"] = "glyph RLE stream does not decode"
            else:
                w, h, rows = got
                entry["resuelto"] = True
                entry["width"] = w
                entry["height"] = h
                # stream length: walk the offsets again with rle.decode
                # (same grammar, to get `end` and be able to check for overlap)
                got2 = rle.decode(b, ptr + 1, limit=2 * 176 * 220 + 4096)
                if got2:
                    entry["payload_len"] = got2[3] - (ptr + 1)
                    rangos.append((ptr, got2[3], "glifo:attr%d:cod%d" % (attr, codigo)))
                entry["_rows"] = rows
            seen_glyph_ptr[ptr] = entry
            glyph_resources.append(entry)

    print(
        "\nnon-null font glyphs: %d slots -> %d unique pointers"
        % (sum(1 for f in fpa.values() for p in f["ptr"] if p), len(glyph_resources))
    )
    glyphs_ok = sum(1 for g in glyph_resources if g["resuelto"])
    print("glyphs that decode: %d/%d" % (glyphs_ok, len(glyph_resources)))

    # ----------------------------------------------------------- control negativo: solapes
    rangos.sort()
    solapes = []
    for i in range(1, len(rangos)):
        prev_ini, prev_fin, prev_id = rangos[i - 1]
        ini, fin, id_ = rangos[i]
        if ini < prev_fin:
            solapes.append((prev_id, prev_ini, prev_fin, id_, ini, fin))
    print("\nCONTROL NEGATIVO: solapes entre recursos: %d" % len(solapes))
    for s in solapes[:10]:
        print("  %s [%#x,%#x) solapa con %s [%#x,%#x)" % s)

    # ================================================================== escritura
    mapa = {
        "blob": str(pathlib.Path(a.blob).resolve()),
        "blob_sha256": hashlib.sha256(b).hexdigest(),
        "base_ptr24": BASE,
        "seccion12_entrada2_offset": SEC12_TARGET[0],
        "check": {
            "sitios_bitmap_totales": ptr_total,
            "sitios_bitmap_resueltos": ptr_resueltos,
            "recursos_bitmap_unicos": len(recursos),
            "recursos_bitmap_unicos_resueltos": len(ok_resueltos),
            "glyphs_non_null_slots": sum(
                1 for f in fpa.values() for p in f["ptr"] if p
            ),
            "glyphs_unique_pointers": len(glyph_resources),
            "glyphs_resolved": glyphs_ok,
            "solapes_detectados": len(solapes),
            "avisos_recorrido": avisos_recorrido,
            "bitmap_header_bytes": 5,
            "cadena_heap_5B_registros": n5,
            "cadena_heap_5B_cierra_ptyy": cierra5,
            "chain_heap_4B_records_negative_control": n4,
            "chain_heap_4B_closes_ptyy_negative_control": cierra4,
        },
        "bitmaps": [],
        "fonts": [],
    }

    print("\nescribiendo PNGs de bitmaps...")
    for r in sorted(recursos.values(), key=lambda x: x["offset"]):
        entry = {
            "offset": "%#08x" % r["offset"],
            "ptr24": "%#08x" % (r["offset"] + BASE),
            "resuelto": r["resuelto"],
            "is_device_icon": r["is_device_icon"],
            "device": r["device"],
            "drawn_by": r["drawn_by"],
        }
        if not r["resuelto"]:
            entry["error"] = r["info"]["error"]
            mapa["bitmaps"].append(entry)
            continue
        i = r["info"]
        w, h = i["width"], i["height"]
        digest = hashlib.sha256(
            bytes(b[r["offset"] : r["offset"] + i["header_len"] + i["payload_len"]])
        ).hexdigest()
        fname = "%08x_%dx%d_modo%d.png" % (r["offset"], w, h, i["modo"])
        path = dir_bitmaps / fname
        if i["modo"] == 0:
            render_mode0(b, i["payload_off"], w, h, path)
        else:
            render_mode1(b, i["rows"], w, h, path)
        entry.update(
            {
                "modo": i["modo"],
                "width": w,
                "height": h,
                "header_len": i["header_len"],
                "payload_off": "%#08x" % i["payload_off"],
                "payload_len": i["payload_len"],
                "sha256": digest,
                "png": "bitmaps/%s" % fname,
            }
        )
        mapa["bitmaps"].append(entry)

    print("escribiendo PNGs de glifos...")
    for g in glyph_resources:
        entry = {
            "offset": "%#08x" % g["offset"],
            "ptr24": "%#08x" % (g["offset"] + BASE),
            "atributo": g["atributo"],
            "codigo": g["codigo"],
            "caracter": g["caracter"],
            "also_used_by": g["also_used_by"],
            "resuelto": g["resuelto"],
        }
        if not g["resuelto"]:
            entry["error"] = g["error"]
            mapa["fonts"].append(entry)
            continue
        w, h = g["width"], g["height"]
        digest = hashlib.sha256(
            bytes(b[g["offset"] : g["offset"] + 1 + g.get("payload_len", 0)])
        ).hexdigest()
        safe_char = {
            " ": "space",
            "/": "slash",
            "\\": "bslash",
            ":": "colon",
            "?": "qmark",
            '"': "dquote",
            "'": "squote",
            ".": "dot",
            ",": "comma",
        }.get(g["caracter"], g["caracter"])
        fname = "attr%02d_cod%02d_%s_%dx%d.png" % (
            g["atributo"],
            g["codigo"],
            safe_char,
            w,
            h,
        )
        path = dir_fonts / fname
        render_glyph(b, g["_rows"], w, h, path)
        entry.update(
            {
                "width": w,
                "height": h,
                "declared_width": g["declared_width"],
                "payload_len": g.get("payload_len"),
                "sha256": digest,
                "png": "fuentes/%s" % fname,
            }
        )
        mapa["fonts"].append(entry)

    (out_dir / "mapa.json").write_text(json.dumps(mapa, indent=2, ensure_ascii=False))
    print("\nescrito %s" % (out_dir / "mapa.json"))
    print("PNGs in %s and %s" % (dir_bitmaps, dir_fonts))

    return 0 if (not errores_resolucion and not solapes) else 2


if __name__ == "__main__":
    raise SystemExit(main())
