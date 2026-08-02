#!/usr/bin/env python3
"""BYTECODE GENERATOR for the visual-object interpreter (firmware 0x0295AC).

Specification (measured in the firmware, round 3 of 27/07/2026 -- see ESTADO.md
"LO PROXIMO" and the full synthesis cited there):

    tabla[6][ordinal] -> TRAILER   <flag u8><ptr24 HEADER><u16 N><N x ptr24 SLOT>
    SLOT (7 B)                     <K u8><ptr24 KEY REGISTER><ptr24 PROGRAM>
    HEADER / KEYS                  <u8 count><count x {u8 code,u16 operand,u8 class}>
    PROGRAM                        this module generates THIS: the interpreter bytecode

3 B pointer: ALWAYS `offset_de_archivo + BASE`, LE (proven at 0x02B8AC).
Screen origin top-left, 176x220.

The interpreter's 13 opcodes at `0x0295AC` (XORLW at 0x0295E6-0x029616),
operand length read out of the firmware, not assumed:

    op    shape                              what it does
    00    00                                 END (presents the frame)
    01    01 <X><Y><W><H><color u16>         solid rectangle (13 uses in the blob)
    02    02 <X><Y><ptr24>                   bitmap; target <mode><width>00<height>
    04    04 <X><Y><ptr24>                   text by pointer (glyphs at the target)
    05    05 <X><Y><glyphs...><00>           inline text (payload = the cursor itself)
    10    10 <n>                             attribute: index into section [7] (font/style)
    11    11 <u16 id><u8 class>                queues an atom in the ring 0x0E29-0x0EA0
    12    12 <sel><n1>{case,ptr24}*n1<n2>{low,high,ptr24}*n2   1 B SWITCH (inclusive range)
    14    14 <ptr24>                         unconditional JMP, no return
    16    16 <ptr24>                         CALL: saves cursor+3 at 0xD34-D36
    17    17                                 RET: restores the saved cursor

(03 and 13 have their operand length read out but ZERO instances across the 156
factory screens -- they are implemented anyway, without being able to give them
a positive check.)

CHECK on this generator: `verificar_contra_fabrica()` further down reproduces,
from a HIGH-LEVEL DESCRIPTION (not by copying bytes), the program of THREE
different factory objects -- the three copies of the "Devices" menu
(tabla[6][74], [90] and [141], decoded byte by byte against
`backups/config_raw.bin` with an ad-hoc linear disassembler before this
generator was written) -- and compares byte for byte against the original blob.
Run:

    python3 draw_bytecode.py                 # runs the check + a sample new screen
    python3 draw_bytecode.py --blob PATH      # alternative blob for the check

Writes nothing. Does not call write.py or any libconcord primitive.
"""

from __future__ import annotations

import argparse
import pathlib

import glyphs

BASE = 0x040000

ANCHO_PANTALLA = 176
ALTO_PANTALLA = 220

# device-row geometry (18/18 B, ESTADO.md, corrected in round 2:
# "inst"/"grupo" are not instance numbers, they are the row's Y coordinate)
X_ICONO_GRANDE = 0x06
X_ICONO_CHICO = 0x0B
X_TEXTO_FILA = 0x3F
Y_ICONO_CHICO_OFFSET = 1
Y_TEXTO_OFFSET = 19
ROW_STEP = 54
Y_ROW_0 = 38

ANCHO_ICONO_GRANDE, ALTO_ICONO_GRANDE = 164, 50
ANCHO_ICONO_CHICO, ALTO_ICONO_CHICO = 51, 48


def row_y(index: int) -> int:
    """Y of row `index` (0-based) in a device menu."""
    return Y_ROW_0 + ROW_STEP * index


# --------------------------------------------------------------------------
# 1. INSTRUCCIONES DE ALTO NIVEL
#
# Each one is a tuple (`op`, *args). The arguments that are pointers
# aceptan DOS formas:
#   - int   -> ALREADY KNOWN file offset (an existing blob resource)
#   - str   -> label of ANOTHER BLOCK in this same assembly, resolved
#              only in the second pass (allows forward and backward)
# --------------------------------------------------------------------------


def FIN():
    return ("fin",)


def RET():
    return ("ret",)


def LLAMAR(target):
    return ("llamar", target)


def SALTAR(target):
    return ("saltar", target)


def ATRIBUTO(n: int):
    return ("atributo", n)


def BITMAP(x: int, y: int, target):
    return ("bitmap", x, y, target)


def TEXT(x: int, y: int, target):
    """Text by pointer: `target` points at a glyph string already terminated with 00."""
    return ("text", x, y, target)


def TEXT_INLINE(x: int, y: int, text_or_glyphs):
    """Embedded text: `text_or_glyphs` is a `str` (encoded with the glyph
    table) or already-encoded `bytes` (they must end in `\\x00`)."""
    return ("texto_inline", x, y, text_or_glyphs)


def ATOMO(id_: int, category: int):
    return ("atomo", id_, category)


def SWITCH(sel: int, casos=(), rangos=()):
    """`casos`: [(valor_u8, destino)], equality. `rangos`: [(inf_u8,sup_u8,destino)],
    inclusive range. If nothing matches, flow continues after the SWITCH."""
    return ("switch", sel, tuple(casos), tuple(rangos))


def RECT(x: int, y: int, w: int, h: int, color: int):
    """op 0x01: solid rectangle in `color` (u16, panel format)."""
    return ("rect", x, y, w, h, color)


def device_row_ops(y: int, icono_grande, icono_chico, name, font_attribute=None):
    """A device-menu row at Y=`y`: big icon 164x50 at (6,Y),
    small icon 51x48 at (11,Y+1), name at (63,Y+19). Use `row_y(index)`
    for the Y of row `index` (0-based, 54 px step, first one at Y=38).
    `icono_grande`/`icono_chico`/`name` are file offsets (int) to the
    resources, or `name` can be a `str` for new inline text.
    `font_attribute`: optional, selects the name's font (it only needs
    declaring once per screen, not per row -- that's how factory does it)."""
    ops = [
        BITMAP(X_ICONO_GRANDE, y, icono_grande),
        BITMAP(X_ICONO_CHICO, y + Y_ICONO_CHICO_OFFSET, icono_chico),
    ]
    if font_attribute is not None:
        ops.append(ATRIBUTO(font_attribute))
    if isinstance(name, str):
        ops.append(TEXT_INLINE(X_TEXTO_FILA, y + Y_TEXTO_OFFSET, name))
    else:
        ops.append(TEXT(X_TEXTO_FILA, y + Y_TEXTO_OFFSET, name))
    return ops


def prologo_estandar(atributo, titulo, fondo, atomos=()):
    """The common sub-program the 156 factory screens open with: font,
    title, full-screen background, N queue atoms, title again, RET.
    `titulo` and `fondo` are file offsets (int); `titulo` can be a `str`
    for inline text. `atomos`: [(id,clase), ...] for OP11 (mechanism measured,
    purpose not fully nailed down -- see ESTADO.md / round 3 map SS2)."""
    txt = TEXT_INLINE(6, 4, titulo) if isinstance(titulo, str) else TEXT(6, 4, titulo)
    ops = [ATRIBUTO(atributo), txt, BITMAP(0, 0, fondo)]
    for id_, category in atomos:
        ops.append(ATOMO(id_, category))
    ops.append(txt)
    ops.append(RET())
    return ops


def full_screen_background(color_o_ptr):
    """If `color_o_ptr` is a 16-bit int with the high bit clear (0..0xFFFF) and
    there is no resource, use RECT(0,0,176,220,color) as a solid background; if
    it is a file offset to an already-prepared bitmap (176x220), use BITMAP."""
    return RECT(0, 0, ANCHO_PANTALLA, ALTO_PANTALLA, color_o_ptr)


# --------------------------------------------------------------------------
# 2. LENGTH AND ENCODING (they always include the opcode byte)
# --------------------------------------------------------------------------


def _glyphs_of(payload) -> bytes:
    if isinstance(payload, bytes):
        if not payload.endswith(b"\x00"):
            raise ValueError("glifos crudos sin terminador 0x00: %r" % payload)
        return payload
    cod = glyphs.codificar(payload, glyphs.BASE)
    if cod is None:
        missing = sorted({c for c in payload if c not in glyphs.BASE.values()})
        raise ValueError(
            "cannot encode %r: missing glyphs %r" % (payload, missing)
        )
    return cod


def _largo(instr) -> int:
    op = instr[0]
    if op in ("fin", "ret"):
        return 1
    if op in ("llamar", "saltar"):
        return 1 + 3
    if op == "atributo":
        return 1 + 1
    if op in ("bitmap", "text"):
        return 1 + 1 + 1 + 3
    if op == "texto_inline":
        _, _x, _y, payload = instr
        return 1 + 1 + 1 + len(_glyphs_of(payload))
    if op == "atomo":
        return 1 + 2 + 1
    if op == "rect":
        return 1 + 1 + 1 + 1 + 1 + 2
    if op == "switch":
        _, _sel, casos, rangos = instr
        return 1 + 1 + 1 + 4 * len(casos) + 1 + 5 * len(rangos)
    raise ValueError("opcode desconocido: %r" % (instr,))


def _pointer(offset: int) -> bytes:
    if offset < 0:
        raise ValueError("offset de archivo negativo: %d" % offset)
    return (offset + BASE).to_bytes(3, "little")


def _resolver(simtab: dict):
    def ptr_de(target) -> bytes:
        if isinstance(target, str):
            if target not in simtab:
                raise KeyError("etiqueta sin resolver: %r" % target)
            return _pointer(simtab[target])
        return _pointer(target)

    return ptr_de


def _codificar(instr, ptr_de) -> bytes:
    op = instr[0]
    if op == "fin":
        return b"\x00"
    if op == "ret":
        return b"\x17"
    if op == "llamar":
        return b"\x16" + ptr_de(instr[1])
    if op == "saltar":
        return b"\x14" + ptr_de(instr[1])
    if op == "atributo":
        _, n = instr
        return bytes([0x10, n & 0xFF])
    if op == "bitmap":
        _, x, y, target = instr
        return bytes([0x02, x & 0xFF, y & 0xFF]) + ptr_de(target)
    if op == "text":
        _, x, y, target = instr
        return bytes([0x04, x & 0xFF, y & 0xFF]) + ptr_de(target)
    if op == "texto_inline":
        _, x, y, payload = instr
        return bytes([0x05, x & 0xFF, y & 0xFF]) + _glyphs_of(payload)
    if op == "atomo":
        _, id_, category = instr
        return (
            bytes([0x11]) + (id_ & 0xFFFF).to_bytes(2, "little") + bytes([category & 0xFF])
        )
    if op == "rect":
        _, x, y, w, h, color = instr
        return bytes([0x01, x & 0xFF, y & 0xFF, w & 0xFF, h & 0xFF]) + (
            color & 0xFFFF
        ).to_bytes(2, "little")
    if op == "switch":
        _, sel, casos, rangos = instr
        out = bytes([0x12, sel & 0xFF, len(casos)])
        for value, target in casos:
            out += bytes([value & 0xFF]) + ptr_de(target)
        out += bytes([len(rangos)])
        for inf, sup, target in rangos:
            out += bytes([inf & 0xFF, sup & 0xFF]) + ptr_de(target)
        return out
    raise ValueError("opcode desconocido: %r" % (instr,))


# --------------------------------------------------------------------------
# 3. ENSAMBLADOR: dos pasadas, bloques con nombre, offset fijo u OFFSET
#    automatic (they stack up one behind the other in order of addition).
# --------------------------------------------------------------------------


class Ensamblador:
    def __init__(self):
        self._bloques: list[tuple[str, list, int | None]] = []

    def bloque(self, name: str, instrucciones, offset: int | None = None) -> str:
        """Adds a block. `offset`: fixed position (file offset) to reproduce
        an existing object; if omitted, it is stacked right after the
        previous block (to generate new content at the end of the blob)."""
        self._bloques.append((name, list(instrucciones), offset))
        return name

    def ensamblar(self, cursor_inicial: int = 0):
        """Returns (bloques: {nombre: bytes}, simtab: {nombre: offset},
        completo: bytes concatenated in order of addition)."""
        simtab: dict[str, int] = {}
        largos: list[int] = []
        cursor = cursor_inicial
        for name, instrs, offset_fijo in self._bloques:
            inicio = offset_fijo if offset_fijo is not None else cursor
            if name in simtab:
                raise ValueError("bloque duplicado: %r" % name)
            total_length = sum(_largo(i) for i in instrs)
            simtab[name] = inicio
            largos.append(total_length)
            cursor = inicio + total_length

        ptr_de = _resolver(simtab)
        salida: dict[str, bytes] = {}
        completo = bytearray()
        for (name, instrs, _off), total_length in zip(self._bloques, largos):
            crudo = bytearray()
            for instr in instrs:
                crudo += _codificar(instr, ptr_de)
            if len(crudo) != total_length:
                raise AssertionError(
                    "largo inconsistente en %r: previsto %d, emitido %d"
                    % (name, total_length, len(crudo))
                )
            salida[name] = bytes(crudo)
            completo += crudo
        return salida, simtab, bytes(completo)


# --------------------------------------------------------------------------
# 4. CONTROL: reproducir 3 objetos de fabrica byte a byte
#
# The offsets and pointers below come from hand-decoding
# `backups/config_raw.bin` with a linear disassembler (not from copying hex):
# the three objects are the SAME list of 3 devices (TV / Home / DVR)
# under the title "Devices", repeated at three points in the config
# (tabla[6][74], [90], [141] -- ver ESTADO.md, seccion "La tecnica: reubicar
# the whole object"). They share resources (icons, texts) but each one has
# ITS OWN embedded prologue (headers and one atom change) and two of the three
# also share a footer with a SWITCH; the third one does not.
# --------------------------------------------------------------------------

# resources shared by the three objects (file offsets)
PTR_TITULO_DEVICES = 0x00F656
PTR_FONDO = 0x0E66FA  # 176x220
PTR_ICONO_TV_G, PTR_ICONO_TV_P, PTR_TEXTO_TV = 0x0A5F0E, 0x0E53D5, 0x00F689
PTR_ICONO_HOME_G, PTR_ICONO_HOME_P, PTR_TEXTO_HOME = 0x0B39A5, 0x044770, 0x00F6B7
PTR_ICONO_DVR_G, PTR_ICONO_DVR_P, PTR_TEXTO_DVR = (
    0x0DE020,
    0x04A62B,
    0x00F69B,
)
PTR_TEXTO_ACTIVITIES = 0x00F6CE
PTR_TEXTO_CURRENT, PTR_TEXTO_ACTIVITY = 0x00F6E2, 0x00F6ED


def _programa_devices(prefijo, atomo2_id, con_switch, offsets):
    """Builds, with the high-level API, the program of a factory "Devices menu"
    object and adds it to a fresh `Ensamblador`, at the EXACT offsets where the
    original lives -- so the internal pointers (CALL to the prologue, SWITCH,
    JMP back) come out bit for bit the same as the real blob."""
    asm = Ensamblador()

    prologo = prefijo + "_prologo"
    programa = prefijo + "_programa"
    asm.bloque(
        prologo,
        prologo_estandar(
            atributo=0x03,
            titulo=PTR_TITULO_DEVICES,
            fondo=PTR_FONDO,
            atomos=[(0x0002, 0x73), (atomo2_id, 0x7F)],
        ),
        offset=offsets["prologo"],
    )
    # the 0x00 byte between the RET and the program entry: padding never
    # executed (the RET leaves first), 156/156 factory objects have it.
    asm.bloque(prefijo + "_relleno", [FIN()])

    rows = (
        device_row_ops(
            row_y(0),
            PTR_ICONO_TV_G,
            PTR_ICONO_TV_P,
            PTR_TEXTO_TV,
            font_attribute=0x04,
        )
        + device_row_ops(
            row_y(1), PTR_ICONO_HOME_G, PTR_ICONO_HOME_P, PTR_TEXTO_HOME
        )
        + device_row_ops(row_y(2), PTR_ICONO_DVR_G, PTR_ICONO_DVR_P, PTR_TEXTO_DVR)
    )

    if con_switch:
        fin = prefijo + "_fin"
        caso0 = prefijo + "_caso0"
        caso1 = prefijo + "_caso1"
        asm.bloque(
            programa,
            [LLAMAR(prologo), *rows, SWITCH(0x25, casos=[(0, caso0), (1, caso1)])],
            offset=offsets["programa"],
        )
        asm.bloque(fin, [FIN()], offset=offsets["fin"])
        asm.bloque(
            caso0,
            [ATRIBUTO(0x05), TEXT(16, 202, PTR_TEXTO_ACTIVITIES), SALTAR(fin)],
            offset=offsets["caso0"],
        )
        asm.bloque(
            caso1,
            [
                ATRIBUTO(0x05),
                TEXT(22, 196, PTR_TEXTO_CURRENT),
                TEXT(21, 207, PTR_TEXTO_ACTIVITY),
                SALTAR(fin),
            ],
            offset=offsets["caso1"],
        )
    else:
        asm.bloque(
            programa,
            [
                LLAMAR(prologo),
                *rows,
                ATRIBUTO(0x05),
                TEXT(16, 202, PTR_TEXTO_ACTIVITIES),
                FIN(),
            ],
            offset=offsets["programa"],
        )

    return asm


OBJETOS_DE_CONTROL = {
    # name: (file start, file end, program offsets, atomo2, switch?)
    "tabla[6][74]": dict(
        inicio=0x011763,
        fin=0x0117E8,
        atomo2_id=0x045E,
        con_switch=True,
        offsets=dict(
            prologo=0x011763,
            programa=0x011781,
            fin=0x0117C9,
            caso0=0x0117CA,
            caso1=0x0117D6,
        ),
    ),
    "tabla[6][90]": dict(
        inicio=0x012229,
        fin=0x0122AE,
        atomo2_id=0x049E,
        con_switch=True,
        offsets=dict(
            prologo=0x012229,
            programa=0x012247,
            fin=0x01228F,
            caso0=0x012290,
            caso1=0x01229C,
        ),
    ),
    "tabla[6][141]": dict(
        inicio=0x01482F,
        fin=0x014892,
        atomo2_id=0x0597,
        con_switch=False,
        offsets=dict(prologo=0x01482F, programa=0x01484D),
    ),
}


def verificar_contra_fabrica(blob_path: str) -> bool:
    b = pathlib.Path(blob_path).read_bytes()
    print("=== CONTROL: reproduce the program of the 3 factory objects ===\n")
    total_ok = 0
    total_bytes = 0
    todo_ok = True
    for name, spec in OBJETOS_DE_CONTROL.items():
        prefijo = (
            name.replace("[", "_").replace("]", "").replace("(", "").replace(")", "")
        )
        asm = _programa_devices(
            prefijo, spec["atomo2_id"], spec["con_switch"], spec["offsets"]
        )
        _bloques, _simtab, generado = asm.ensamblar()
        esperado = b[spec["inicio"] : spec["fin"]]

        n = min(len(generado), len(esperado))
        iguales = sum(1 for i in range(n) if generado[i] == esperado[i])
        ok = generado == esperado
        todo_ok &= ok
        total_ok += iguales
        total_bytes += len(esperado)

        state = "EXACTO" if ok else "DIFIERE"
        print(
            "%-16s %#08x..%#08x  %4d B   %s (%d/%d bytes iguales)"
            % (
                name,
                spec["inicio"],
                spec["fin"],
                len(esperado),
                state,
                iguales,
                len(esperado),
            )
        )
        if not ok:
            for i in range(n):
                if generado[i] != esperado[i]:
                    print(
                        "   primer byte distinto en +%d (%#08x): generado %02x, blob %02x"
                        % (i, spec["inicio"] + i, generado[i], esperado[i])
                    )
                    print("   generado: %s" % generado[max(0, i - 4) : i + 8].hex(" "))
                    print("   blob:     %s" % esperado[max(0, i - 4) : i + 8].hex(" "))
                    break
            if len(generado) != len(esperado):
                print(
                    "   largo generado %d != largo esperado %d"
                    % (len(generado), len(esperado))
                )

    print(
        "\nTOTAL: %d/%d bytes iguales sobre %d objetos -- %s"
        % (
            total_ok,
            total_bytes,
            len(OBJETOS_DE_CONTROL),
            "3/3 EXACTOS" if todo_ok else "hay diferencias",
        )
    )
    return todo_ok


# --------------------------------------------------------------------------
# 5. DEMONSTRATION: a NEW screen, from scratch (not a factory replay)
# --------------------------------------------------------------------------


def build_new_device_screen(
    titulo: str,
    fondo: int,
    device_name: str,
    icono_grande: int,
    icono_chico: int,
    cursor_inicial: int = 0,
):
    """Builds, from a high-level description, the complete bytecode of a
    screen with background + title + one device row. Returns
    (bloques, simtab, completo, entrada) -- `entrada` is the label of the
    block to put in `SLOT.ptr24_programa`. `fondo`/`icono_*` are file
    offsets to ALREADY existing resources (176x220, 164x50, 51x48); if
    there is no background bitmap, use `full_screen_background(color)`
    instead of `BITMAP` inside the prologue (see that function)."""
    asm = Ensamblador()
    prologo = asm.bloque(
        "prologo",
        prologo_estandar(atributo=0x03, titulo=titulo, fondo=fondo),
    )
    entrada = asm.bloque(
        "programa",
        [
            LLAMAR(prologo),
            *device_row_ops(
                row_y(0),
                icono_grande,
                icono_chico,
                device_name,
                font_attribute=0x04,
            ),
            FIN(),
        ],
    )
    bloques, simtab, completo = asm.ensamblar(cursor_inicial)
    return bloques, simtab, completo, entrada


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--blob",
        default=str(
            pathlib.Path(__file__).resolve().parent.parent
            / "backups"
            / "config_raw.bin"
        ),
        help="blob to run the check against (default: backups/config_raw.bin)",
    )
    a = ap.parse_args()

    ok = verificar_contra_fabrica(a.blob)

    print("\n=== DEMONSTRATION: new screen from scratch (background + title + row) ===\n")
    bloques, simtab, completo, entrada = build_new_device_screen(
        titulo="audio",  # inline, encodable with the base table in glyphs.py (no Hub vocabulary)
        fondo=PTR_FONDO,  # reuses the factory background, 176x220
        device_name="Sonos",
        icono_grande=PTR_ICONO_TV_G,  # demo: reusa un icono existente
        icono_chico=PTR_ICONO_TV_P,
    )
    for name, contenido in bloques.items():
        print(
            "%-10s offset %#06x  %3d B   %s"
            % (name, simtab[name], len(contenido), contenido.hex(" "))
        )
    print(
        "\nprogram entry point (for SLOT.ptr24_programa): block %r, offset %#06x"
        % (entrada, simtab[entrada])
    )
    print("total: %d B" % len(completo))

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
