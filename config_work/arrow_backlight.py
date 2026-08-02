"""Turns on the backlight of the physical paging keys. Self-contained."""

from __future__ import annotations

BASE = 0x040000
SEMILLA = (0x21, 0x43)

# --- factory actions that already exist in tabla[11] (not created, just referenced) ---
ACC_FLECHAS_ON_02 = (
    0x037C  # [11][892] = {C022,3F}{C002,3F}{0001,93}  channels 0 and 2 ON
)
ACC_FLECHAS_OFF_02 = (
    0x0398  # [11][920] = {C020,3F}{C000,3F}{0000,93}  channels 0 and 2 OFF
)
ACC_FLECHAS_ON_13 = (
    0x0245  # [11][581] = {C032,3F}{C012,3F}{0001,A4}  channels 1 and 3 ON
)
ACC_FLECHAS_OFF_13 = (
    0x02B3  # [11][691] = {C030,3F}{C010,3F}{0000,A4}  channels 1 and 3 OFF
)
CATEGORY_ACTION = 0x7F
CODE_ENTER, CODE_EXIT = 0x06, 0x07


def _u16(b, o):
    return int.from_bytes(b[o : o + 2], "little")


def _u24(b, o):
    return int.from_bytes(b[o : o + 3], "little")


def _p24(v):
    return (v + BASE).to_bytes(3, "little")


def _section(b, n):
    return _u24(b, 0x0C + 4 * n) - BASE


def _checksum(b, fin):
    lo, hi = SEMILLA
    for i in range(0, fin, 2):
        lo ^= b[i]
        hi ^= b[i + 1]
    return lo, hi


def _close(b):
    return _u24(b, 4) - BASE - 2


def _atomos_cab(b, off):
    """<u8 count><count x {u8 code, u16 operand, u8 class}>"""
    n = b[off]
    return [
        (b[off + 1 + 4 * k], _u16(b, off + 2 + 4 * k), b[off + 4 + 4 * k])
        for k in range(n)
    ]


def _obj11(b, t11, k):
    """<u8 count><count x {u16 id, u8 class}> -> (offset, [(id,class)...])"""
    o = _u24(b, t11 + 2 + 3 * k) - BASE
    n = b[o]
    return o, [(_u16(b, o + 1 + 3 * j), b[o + 3 + 3 * j]) for j in range(n)]


def _arma_obj11(atomos):
    out = bytearray([len(atomos)])
    for i, c in atomos:
        out += i.to_bytes(2, "little") + bytes([c])
    return bytes(out)


def turn_on_paging_arrows(
    blob: bytes,
    ordinales=(74, 90, 141),
    par=(ACC_FLECHAS_ON_02, ACC_FLECHAS_OFF_02),
    also_other_pair=False,
    estricto=True,
):
    """Adds the 'turn arrows on' action to the ENTER hook of every multi-page
    screen, and the 'turn arrows off' one to the LEAVE hook if it is not
    already there.

    Does not invent actions: it references the factory ones in tabla[11] by
    index. Only grows tabla[11] objects (copied to the tail) and re-points
    their entries. Returns (new_blob, report). Does not write anything to disk.
    """
    b = bytearray(blob)
    acc_on, acc_off = par
    on_extra = [acc_on] + ([ACC_FLECHAS_ON_13] if also_other_pair else [])
    off_extra = [acc_off] + ([ACC_FLECHAS_OFF_13] if also_other_pair else [])

    t6, t11 = _section(b, 6), _section(b, 11)
    n6 = _u16(b, t6)
    informe, pending, cache = [], [], {}

    for ordi in ordinales:
        if not 0 <= ordi < n6:
            raise ValueError("ordinal %d out of tabla[6] (%d)" % (ordi, n6))
        tr = _u24(b, t6 + 3 + 3 * ordi) - BASE
        N = _u16(b, tr + 4)
        hdr = _u24(b, tr + 1) - BASE
        if N <= 1:
            informe.append("ord %d: N=%d, no page -> no changes" % (ordi, N))
            continue
        for cod, extra in ((CODE_ENTER, on_extra), (CODE_EXIT, off_extra)):
            gancho = [
                a for a in _atomos_cab(b, hdr) if a[0] == cod and a[2] == CATEGORY_ACTION
            ]
            if not gancho:
                if estricto:
                    raise ValueError(
                        "ord %d: header has no hook %02x/0x7F" % (ordi, cod)
                    )
                informe.append("ord %d: no hook %02x -> skipped" % (ordi, cod))
                continue
            k = gancho[0][1]
            _, atomos = _obj11(b, t11, k)
            missing = [x for x in extra if (x, CATEGORY_ACTION) not in atomos]
            if not missing:
                informe.append(
                    "ord %d code %02x: [11][%d] already turns on/off -> no changes"
                    % (ordi, cod, k)
                )
                continue
            nuevos = _arma_obj11(atomos + [(x, CATEGORY_ACTION) for x in missing])
            pending.append((k, nuevos))
            informe.append(
                "ord %d code %02x: [11][%d] %s -> + %s"
                % (
                    ordi,
                    cod,
                    k,
                    " ".join("{%04X,%02X}" % a for a in atomos),
                    " ".join("{%04X,7F}" % x for x in missing),
                )
            )

    if not pending:
        return bytes(b), informe

    fin = _close(b)
    trailer = bytes(b[fin:])  # <u16 checksum><'PTYY'>
    del b[fin:]
    for k, nuevos in pending:
        if nuevos not in cache:
            if len(b) % 2:  # the checksum works in pairs: keep parity
                b += b"\x00"
            cache[nuevos] = len(b)
            b += nuevos
        b[t11 + 2 + 3 * k : t11 + 5 + 3 * k] = _p24(cache[nuevos])
    if len(b) % 2:
        b += b"\x00"
    new_end = len(b)
    b += trailer
    b[4:7] = _p24(new_end + 2)
    lo, hi = _checksum(b, new_end)
    b[new_end], b[new_end + 1] = lo, hi
    informe.append(
        "tail: %d B new, close %#08x -> %#08x, checksum %02x %02x"
        % (new_end - fin, fin, new_end, lo, hi)
    )
    return bytes(b), informe
