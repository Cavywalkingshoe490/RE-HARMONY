#!/usr/bin/env python3
"""Adds the 4th device (Philips) to the Devices menu as a SECOND SUB-SCREEN.

Why this route and not `fourth_device.py`'s
-------------------------------------------
`fourth_device.py` crams a fourth row into the same screen. It doesn't fit: the
rows are `02 <X> <Y> <ptr>` with Y = 38, 92, 146 and a **54 pixel step** (the
"PASO 54" in ESTADO.md is not an instance number, it's the vertical step). The
large icon is 164x50, so the fourth row would start at Y=200 and end at 250,
against a 220 panel (`lcd_fill_rect` 0x02294C clips at 0xB0 x 0xDC).
That's why "the fourth one ends up below the cut".

The page takes N sub-screens through the trailer, and the firmware pages them
by itself:

    trailer   <flag u8> <ptr24 header> <u16 N> <N x ptr24 slot>
    SLOT (7B) <K u8> <ptr24 key register [9]> <ptr24 program>
    0x028280  loads the page: slot = trailer + 6 + 3*[0x21D],  [0x21D] = 0
    0x0284BC  pager: [0x21D]++ (or --), wraps against N read LIVE from the
              trailer (0x02853C: ptr_add(4) + get_u16), and re-renders
    0x025D5C  touch: K = slot[0] -> section [19][K] (33 templates)

That is: **adding a slot to the trailer is enough; there is no separate
counter**.

The header is NOT touched (the previous attempt, refuted)
---------------------------------------------------------
This script's first version copied page 74's 25 B header and stuck two new
"atoms" into classes `0xAF`/`0xAE` (which in the original are null: id=0,
tag=0x00), pointing at `table[11][242]`/`[244]` -- a pair of shared slots
`{4042,0x75}{0xFFA1/0xFFA0,0x0F}` that already exist in the blob. Negative
control before trusting that: the **2,904** objects of `table[11]` were swept
looking for class `0xAF` or `0xAE` with id != 0 in ANY header of the blob.
**Zero matches.** And sweeping the 14 factory objects that DO have N>1 (the
only ones with paging exercised), none uses `0xAF`/`0xAE`: their first two
classes are always `0x06`/`0x07` (each one with its **own and distinct** id,
not shared -- 949/950, 1023/1024, ... 1476/1477), and what those two classes
reference in `table[11]` looks nothing like the 242/244 pair (they point at
class `0x1F` objects, the namespace ESTADO.md flags as untracked). That is:
`0xAF`/`0xAE` is a class **never exercised by the factory firmware**, and
`0x06`/`0x07` -- which it is -- would require adding two new entries to
`table[11]` (extending section [11], which ESTADO.md flags as "the only
thing not exercised yet").

Neither of the two documented paging routes (`0x028280` loads the slot by
*trailer offset*, `0x0284BC` pages by reading *N from the trailer*) touches
the header at all, and `PROLOGO` (the style+title+background subroutine
shared by dozens of pre-existing pages, with headers completely different
from each other) cannot depend on a specific class in the caller's header
either. Conclusion: the header is dispensable for the sub-screen to work, and
inventing never-exercised content for it is risk with no benefit.
**This script reuses the pointer to page 74's original header without
copying it or touching it.**

What gets touched in the original body
--------------------------------------
Three bytes: entry 74 of section [6]'s table (0x01C77A). Nothing else.
Everything new goes to the blob's tail. The program, the new key register
and the trailer reuse the original bytes by pointer (header, prologue,
sub-screen 1's key register, the "N of M" texts); they don't copy them.

Controls before writing the file (none of them writes to the device)
-------------------------------------------------------------------
(a) The new trailer is parsed with the SAME reader (`read_trailer`) used to
    walk `table[6]`'s 156 factory trailers, and with which it is confirmed
    that there are exactly 14 with N>1 (ESTADO.md's "exercised path").
(b) Every internal pointer of the relocated object (header, the 2 slots,
    their key registers, their programs, and the reused constants) resolves
    inside the blob, with a valid shape (key register with a small count, a
    program that starts at opcode 0x16 -- 100% of the slot programs in the
    14 factory objects start that way -- and the name's text re-decodes byte
    for byte to the requested string).
(c) Field-by-field comparison against the 14 factory objects with N>1: the
    trailer's flag, the size `6 + 3*N`, each new slot's K value and each new
    program's initial opcode have to fall inside the set observed in the 14
    -- a value the firmware never exercised is not accepted.
(d) The usual gate (`nothing_moved`): not one byte of the original body
    changes except the close and tabla[6]'s entry 74, both declared
    explicitly.

Usage:
    python3 subscreen.py <blob.bin> --salida nuevo.bin
"""

from __future__ import annotations

import argparse
import pathlib

import glyphs

BASE = 0x040000

# --- constantes medidas sobre backups/config_raw.bin (todas verificadas) ---
T6 = 0x01C699  # tabla[6]: <u16 cuenta=156><pad><156 x ptr24>, entradas en +3
T6_ENTRY = 0x01C77A  # tabla[6] + 3 + 3*74
OBJ74_HDR = 0x01174A  # page 74's original header (not touched)
PROLOGO = 0x011763  # sub-programa comun: estilo + titulo + fondo + 2 atomos + RET
MAIN_TRAS_CALL = 0x011785  # just after the original program's `16 <ptr prologo>`
PIE_SWITCH = 0x0117BD  # `12 25 ...` -> Activities / Current Activity -> op 00
REC9_ORIG = 0x02921A  # page 74's original key register (K=0x04, 4 keys)
ICONO_GRANDE = 0x0A5F0E  # 164x50, the TV's (row 1) -- file OFFSET
ICONO_CHICO = 0x0E53D5  # 51x48,  the TV's (row 1) -- file OFFSET
TXT_1 = 0x00F821  # "1"
TXT_OF = 0x00F826  # "of"
TXT_2 = 0x00F89C  # "2"
TXT_PAGES = 0x00F7A2  # "pages"


def u24(b, o):
    return int.from_bytes(b[o : o + 3], "little")


def u16(b, o):
    return int.from_bytes(b[o : o + 2], "little")


def p(v):
    return (v + BASE).to_bytes(3, "little")


def read_trailer(b, off: int, max_n: int = 20) -> dict | None:
    """Parses `<flag u8><ptr24 header><u16 N><N x ptr24 slot>` at `off`.

    Returns None if it doesn't fit -- the same criterion the 156 `table[6]`
    trailers were classified with (0 malformed, 14 with N>1). It's the reader
    used both on the factory blob and on the new trailer.
    """
    if off < 0 or off + 6 > len(b):
        return None
    flag = b[off]
    hdr = u24(b, off + 1)
    n = u16(b, off + 4)
    if not (1 <= n <= max_n) or off + 6 + 3 * n > len(b):
        return None
    slots = [u24(b, off + 6 + 3 * k) for k in range(n)]
    return {"off": off, "flag": flag, "hdr": hdr, "N": n, "slots": slots}


def read_slot(b, off: int) -> dict | None:
    """Parses `<K u8><ptr24 registro de keys><ptr24 programa>` (7 B) at `off`."""
    if off < 0 or off + 7 > len(b):
        return None
    return {"off": off, "K": b[off], "keyreg": u24(b, off + 1), "prog": u24(b, off + 4)}


def scan_table6(b) -> list[tuple[int, dict | None]]:
    """[(ordinal, trailer_parseado_o_None)] for tabla[6]'s 156 entries."""
    n = u16(b, T6)
    base = T6 + 3
    out = []
    for i in range(n):
        ptr = u24(b, base + 3 * i) - BASE
        out.append((i, read_trailer(b, ptr)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("--name", default="Philips")
    ap.add_argument("--salida")
    a = ap.parse_args()
    b = pathlib.Path(a.blob).read_bytes()

    # --- controls that the blob is the expected one, before touching anything ---
    esperado = {
        T6_ENTRY: bytes.fromhex("ef1705"),
        OBJ74_HDR: bytes([0x06, 0xAF, 0x00, 0x00, 0x00, 0xAE, 0x00, 0x00, 0x00]),
        PROLOGO: bytes.fromhex("1003"),
        MAIN_TRAS_CALL: bytes.fromhex("020626"),
        PIE_SWITCH: bytes.fromhex("122502"),
    }
    for off, pat in esperado.items():
        if b[off : off + len(pat)] != pat:
            raise SystemExit(
                "anchor check FAILED at %#08x: %s != %s"
                % (off, b[off : off + len(pat)].hex(" "), pat.hex(" "))
            )
    print("anclajes verificados: %d/%d" % (len(esperado), len(esperado)))

    # control of the resources: an icon's header is `00 <width> 00 <height>`.
    for etq, off, wh in (
        ("icono grande", ICONO_GRANDE, (164, 50)),
        ("icono chico", ICONO_CHICO, (51, 48)),
    ):
        cab = b[off : off + 4]
        if cab[0] or cab[2] or (cab[1], cab[3]) != wh:
            raise SystemExit(
                "%s at %#08x: header %s = %dx%d, expected %dx%d"
                % (etq, off, cab.hex(" "), cab[1], cab[3], wh[0], wh[1])
            )
        print("   %-13s %#08x  %dx%d" % (etq, off, cab[1], cab[3]))

    # --- control (a) + comparison bank: the 156 factory trailers with
    # the same reader that is used afterwards on the new trailer ---
    factory = scan_table6(b)
    malos = [i for i, t in factory if t is None]
    if malos:
        raise SystemExit(
            "factory trailers that do not parse with leer_trailer(): %s" % malos
        )
    n_mayor1 = [(i, t) for i, t in factory if t["N"] > 1]
    if len(n_mayor1) != 14:
        raise SystemExit(
            "14 factory objects with N>1 were expected, %d were found -- "
            "the blob is not the expected one or the reader changed shape" % len(n_mayor1)
        )
    print(
        "\ncontrol (a): 156/156 tabla[6] trailers parse with leer_trailer(); "
        "%d have N>1 (expected: 14, the path the firmware exercises)" % len(n_mayor1)
    )

    flags_fabrica = {t["flag"] for _, t in n_mayor1}
    ks_fabrica: set[int] = set()
    ops_fabrica: set[int] = set()
    for i, t in n_mayor1:
        for sp in t["slots"]:
            s = read_slot(b, sp - BASE)
            if s is None:
                raise SystemExit(
                    "factory slot out of range (ordinal %d, %#08x)" % (i, sp)
                )
            ks_fabrica.add(s["K"])
            op = b[s["prog"] - BASE]
            ops_fabrica.add(op)
            if op != 0x16:
                raise SystemExit(
                    "ordinal %d: a factory slot program does not start at 0x16 (it starts at %#04x) "
                    "-- the assumption '100%% of the slots start at 0x16' is false, check it"
                    % (i, op)
                )
    print(
        "   factory bank -- flags: %s   K: %s   program's initial opcode: %s"
        % (
            sorted(flags_fabrica),
            sorted(hex(k) for k in ks_fabrica),
            sorted(hex(o) for o in ops_fabrica),
        )
    )

    table, _ = glyphs.extender(b, set())
    txt = glyphs.codificar(a.name, table)
    if txt is None:
        raise SystemExit("cannot encode %r with the glyph table" % a.name)

    close = u24(b, 4) - BASE
    out = bytearray(b[: close - 2])

    def emit(blk):
        nonlocal out
        at = len(out)
        out += blk
        return at

    off_txt = emit(txt)

    total_indicator = (
        bytes([0x10, 0x0C])
        + bytes([0x04, 0x17, 0x12])
        + p(TXT_2)
        + bytes([0x04, 0x23, 0x12])
        + p(TXT_PAGES)
    )
    prog0 = (
        bytes([0x16])
        + p(PROLOGO)
        + bytes([0x10, 0x0C])
        + bytes([0x04, 0x0D, 0x12])
        + p(TXT_1)
        + bytes([0x04, 0x12, 0x12])
        + p(TXT_OF)
        + total_indicator[2:]
        + bytes([0x14])
        + p(MAIN_TRAS_CALL)
    )
    off_prog0 = emit(prog0)

    prog1 = (
        bytes([0x16])
        + p(PROLOGO)
        + bytes([0x10, 0x0C])
        + bytes([0x04, 0x0D, 0x12])
        + p(TXT_2)
        + bytes([0x04, 0x12, 0x12])
        + p(TXT_OF)
        + total_indicator[2:]
        + bytes([0x02, 0x06, 0x26])
        + p(ICONO_GRANDE)
        + bytes([0x02, 0x0B, 0x27])
        + p(ICONO_CHICO)
        + bytes([0x10, 0x04])
        + bytes([0x04, 0x3F, 0x39])
        + p(off_txt)
        + bytes([0x14])
        + p(PIE_SWITCH)
    )
    off_prog1 = emit(prog1)

    # Sub-screen 1's key register.
    #
    # With K = 0x04 the zone layout is b0 = row 1, b1 = row 2, b2 = row 3,
    # b3 = pie (medido: paginas 41/74/90/141 -> b0 = "TV" = pag 103, b1 = fila 2,
    # b2 = row 3; 6/6 rows with the destination page's title matching).
    # WATCH OUT: with K = 0x07 (page 44, "My Activities") the layout is DIFFERENT --
    # b2/b3 are the rows and b0/b1 the foot -- because K is the GEOMETRY, not the set of
    # codigos.
    #
    # On sub-screen 1 there is only one row and there is no commands page for the
    # Philips, so the four zones all go to the same inert class-0x72 literal
    # the original page already uses in b3 (id 0x0825): touching the screen does
    # nothing. Positive control that 0x72 is "inert, fires nothing" and not an
    # accident of this page: ordinal 109's slot0 (factory, N=2, K=0x04
    # too) uses the EXACT SAME value `25 08 72` in its own b3.
    # The CANONICAL ORDER b2 b3 b0 b1 is respected.
    r = REC9_ORIG
    orig = {b[r + 1 + 4 * k]: b[r + 2 + 4 * k : r + 5 + 4 * k] for k in range(b[r])}
    inert = orig[0xB3]
    if inert[2] != 0x72:
        raise SystemExit("page 74's b3 is not the inert class-0x72 literal")
    rec1 = bytes([4])
    for cod in (0xB2, 0xB3, 0xB0, 0xB1):
        rec1 += bytes([cod]) + inert
    off_rec1 = emit(rec1)

    off_slot0 = emit(bytes([0x04]) + p(REC9_ORIG) + p(off_prog0))
    off_slot1 = emit(bytes([0x04]) + p(off_rec1) + p(off_prog1))
    off_trailer = emit(
        bytes([0x00])
        + p(OBJ74_HDR)
        + (2).to_bytes(2, "little")
        + p(off_slot0)
        + p(off_slot1)
    )

    if len(out) % 2:
        out += b"\x00"
    nc = len(out) + 2
    out += b"\x00\x00" + b"PTYY"
    out[4:7] = p(nc)
    out[T6_ENTRY : T6_ENTRY + 3] = p(off_trailer)

    lo, hi = 0x21, 0x43
    for k in range(0, nc - 2, 2):
        lo ^= out[k]
        hi ^= out[k + 1]
    out[nc - 2] = lo
    out[nc - 1] = hi
    fresh = bytes(out)

    # --- control (a), again: the NEW trailer with the same reader ---
    new_trailer_rec = read_trailer(fresh, off_trailer)
    if new_trailer_rec is None:
        raise SystemExit("the new trailer does not parse with leer_trailer()")
    if new_trailer_rec["N"] != 2 or (new_trailer_rec["hdr"] - BASE) != OBJ74_HDR:
        raise SystemExit("the new trailer does not have the expected shape: %s" % new_trailer_rec)
    print(
        "\ncontrol (a): new trailer parses with the same reader as the 156 factory ones "
        "-- N=%d flag=%#04x hdr=%#08x (= page 74's original header, reused)"
        % (new_trailer_rec["N"], new_trailer_rec["flag"], new_trailer_rec["hdr"])
    )

    # --- control (c): field-by-field shape against the 14 factory objects with N>1 ---
    problemas_forma = []
    if new_trailer_rec["flag"] not in flags_fabrica:
        problemas_forma.append(
            "flag=%#04x is not in the factory set %s"
            % (new_trailer_rec["flag"], flags_fabrica)
        )
    tam_esperado = 6 + 3 * new_trailer_rec["N"]
    tam_real = (
        len(fresh) - off_trailer if off_trailer + tam_esperado <= len(fresh) else -1
    )
    if tam_real < tam_esperado:
        problemas_forma.append("trailer shorter than 6+3N=%d" % tam_esperado)
    slots_nuevos = [read_slot(fresh, sp - BASE) for sp in new_trailer_rec["slots"]]
    for idx, s in enumerate(slots_nuevos):
        if s is None:
            problemas_forma.append("slot%d no parsea (7 B K+ptr+ptr)" % idx)
            continue
        if s["K"] not in ks_fabrica:
            problemas_forma.append(
                "slot%d: K=%#04x appears in no slot of the 14 factory ones"
                % (idx, s["K"])
            )
        op = fresh[s["prog"] - BASE]
        if op not in ops_fabrica:
            problemas_forma.append(
                "slot%d: program starts at %#04x, no factory slot starts that way"
                % (idx, op)
            )
    if problemas_forma:
        raise SystemExit(
            "control (c) [forma vs 14 de fabrica] FALLO:\n  "
            + "\n  ".join(problemas_forma)
        )
    print(
        "control (c): field-by-field shape == 14 factory objects with N>1 -- OK "
        "(flag %#04x in %s, trailer 6+3*%d B, each slot's K %s in %s, initial opcode %s in %s)"
        % (
            new_trailer_rec["flag"],
            sorted(flags_fabrica),
            new_trailer_rec["N"],
            [hex(s["K"]) for s in slots_nuevos],
            sorted(hex(k) for k in ks_fabrica),
            [hex(fresh[s["prog"] - BASE]) for s in slots_nuevos],
            sorted(hex(o) for o in ops_fabrica),
        )
    )

    # --- control (b): every internal pointer of the relocated object resolves ---
    def en_rango(off_, largo=1):
        return 0 <= off_ and off_ + largo <= len(fresh)

    problemas_b = []
    if not en_rango(OBJ74_HDR, 25):
        problemas_b.append("cabecera (%#08x) fuera de rango" % OBJ74_HDR)
    for idx, s in enumerate(slots_nuevos):
        kr = s["keyreg"] - BASE
        if not en_rango(kr, 1):
            problemas_b.append(
                "slot%d.keyreg (%#08x) fuera de rango" % (idx, s["keyreg"])
            )
        else:
            n_keys = fresh[kr]
            if not (0 < n_keys <= 40) or not en_rango(kr, 1 + 4 * n_keys):
                problemas_b.append(
                    "slot%d.keyreg forma invalida (n=%d)" % (idx, n_keys)
                )
        pr = s["prog"] - BASE
        if not en_rango(pr, 1) or fresh[pr] != 0x16:
            problemas_b.append(
                "slot%d.prog (%#08x) does not start at 0x16" % (idx, s["prog"])
            )
    # the name's text: every byte (except the final 0x00) has to be a
    # known glyph, and decoded back it has to give the requested name
    reconstruido = "".join(table.get(cbyte, "�") for cbyte in txt[:-1])
    if reconstruido != a.name:
        problemas_b.append(
            "the text does not re-decode to %r (gave %r)" % (a.name, reconstruido)
        )
    # the constants reused by pointer (not copied, they have to keep existing)
    for name_, direccion, largo in (
        ("PROLOGO", PROLOGO, 1),
        ("TXT_1", TXT_1, 1),
        ("TXT_OF", TXT_OF, 1),
        ("TXT_2", TXT_2, 1),
        ("TXT_PAGES", TXT_PAGES, 1),
        ("MAIN_TRAS_CALL", MAIN_TRAS_CALL, 1),
        ("PIE_SWITCH", PIE_SWITCH, 1),
        ("ICONO_GRANDE", ICONO_GRANDE, 4),
        ("ICONO_CHICO", ICONO_CHICO, 4),
        ("REC9_ORIG", REC9_ORIG, 1),
    ):
        if not en_rango(direccion, largo):
            problemas_b.append("%s (%#08x) fuera de rango" % (name_, direccion))
    if problemas_b:
        raise SystemExit(
            "control (b) [punteros internos] FALLO:\n  " + "\n  ".join(problemas_b)
        )
    print(
        "control (b): all internal pointers resolve -- header reused, "
        "%d slots (keyreg de cuenta chica, programa op0=0x16), texto %r redecodifica, "
        "constantes reusadas dentro de rango" % (len(slots_nuevos), a.name)
    )

    # --- control (d): nothing of the original body moved except what was declared ---
    dif = [i for i in range(close - 2) if b[i] != fresh[i]]
    declarados = {4, 5, 6, T6_ENTRY, T6_ENTRY + 1, T6_ENTRY + 2}
    sobra = sorted(set(dif) - declarados)
    print("\nbloques nuevos (offset de archivo):")
    for n_, v in (
        ("texto %r" % a.name, off_txt),
        ("programa sub-pantalla 0", off_prog0),
        ("programa sub-pantalla 1", off_prog1),
        ("registro de teclas 1", off_rec1),
        ("slot 0", off_slot0),
        ("slot 1", off_slot1),
        ("trailer nuevo", off_trailer),
    ):
        print("   %-24s %#08x  (dir %#08x)" % (n_, v, v + BASE))
    print("\nbytes of the original body changed: %d" % len(dif))
    print(
        "   declarados: [4:7] (cierre) + %#08x..%#08x (tabla[6][74])"
        % (T6_ENTRY, T6_ENTRY + 2)
    )
    print("   sin declarar: %s" % (sobra if sobra else "none"))
    if sobra:
        raise SystemExit("there are undeclared changes: nothing gets written")
    print("blob: %d -> %d B  (+%d)" % (len(b), len(fresh), len(fresh) - len(b)))
    print("cola nueva: %#08x..%#08x" % (off_txt, len(out)))

    if a.salida:
        pathlib.Path(a.salida).write_bytes(fresh)
        print("\nescrito %s" % a.salida)
        print("to write it you have to declare the repoint:")
        print("   --repunta %#08x" % T6_ENTRY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
