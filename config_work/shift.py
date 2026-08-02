#!/usr/bin/env python3
"""Inserts bytes in the middle of the blob and fixes every pointer that crosses.

**This is what was dodged all session**, and for a reason: 90,461 positions
in the blob contain a u24 that falls in range and almost all of them are
coincidence. Shifting the blob forces you to tell which one is a real
pointer, and fixing one that wasn't corrupts data silently.

It is done now because the device list lives in the heap, **with no padding
behind it and with nobody pointing at it**, so it can neither be extended in
place nor relocated. And because the risk dropped: writing to the device is
already proven and the original config is restored in one command.

## The pointer classes that get fixed

A u24 is only touched if **the structure that contains it declares it is a
pointer**:

    header +4                the close
    master index             19 entries of 4 B at +0x0C
    global table [11]        <u16 count><count x ptr24>
    section [6] table        <u24 count><count x ptr24>
    command records          self-pointer, press waveform, hold waveform
    name references          <04><i><g><ptr24>, with the (i,g) matching
                             the <05><i><g> record they point at

That last condition is what separates the real references from the noise:
without it the sweep turns up 2,056 candidates, of which only **409** have
the id matching.

## The test

`--nulo` shifts **zero** bytes and demands that the resulting blob be **byte
for byte identical** to the original. If the rewriter touched something it
shouldn't, it shows up there. Same discipline as `relocate.py --nulo`.

**It writes nothing to the device.**

Usage:
    python3 shift.py <blob.bin> --nulo
    python3 shift.py <blob.bin> --en 0x0117bd --bytes <file> --salida nuevo.bin
"""

from __future__ import annotations

import argparse
import pathlib

BASE = 0x040000
N_SECCIONES = 19


def u24(b, o):
    return int.from_bytes(b[o : o + 3], "little")


def secciones(b):
    ptr = {}
    for i in range(N_SECCIONES):
        o = 0x0C + 4 * i
        v = int.from_bytes(b[o : o + 4], "little")
        if v:
            ptr[i] = v - BASE
    return ptr


def pointers(b: bytes) -> dict[int, str]:
    """{position of the ptr24: class}. Only the ones declared by structure."""
    p: dict[int, str] = {}
    n = len(b)

    def poner(o, category):
        if 0 <= o and o + 3 <= n and BASE <= u24(b, o) < BASE + n:
            p[o] = category

    poner(4, "close")

    sec = secciones(b)
    for i in sec:
        poner(0x0C + 4 * i, "indice maestro")

    # tabla global de objetos
    if 11 in sec:
        at = sec[11]
        cnt = int.from_bytes(b[at : at + 2], "little")
        for k in range(cnt):
            poner(at + 2 + 3 * k, "tabla[11]")

    # section [6] table: <u24 count><count x ptr24>
    if 6 in sec:
        at = sec[6]
        cnt = u24(b, at)
        if 0 < cnt < 4096:
            for k in range(cnt):
                poner(at + 3 + 3 * k, "tabla[6]")

    # command records: located by their inline waveform
    import irscan

    for o in range(11, n - 3):
        if b[o - 1] != 1 or not 0x04 <= b[o + 2] <= 0x18:
            continue
        d = u24(b, o) - BASE
        if not (0 <= d < n - 1) or (b[d] | (b[d + 1] << 8)) != irscan.LEAD_IN:
            continue
        # `o` is the position of the ptr to the press waveform, i.e. record+16:
        #   registro+11 `01` +12..14 autopuntero
        #   registro+15 `01` +16..18 press
        #   registro+19..21 hold
        # The hold falls at `o+3`. It said `o+4` and **the null shift does not
        # catch it**: with delta 0 nothing moves, so that test only sees the
        # pointers touched in excess, never the missing ones. It was found
        # because the count by class showed 268 press and no hold.
        poner(o - 4, "reg: autopuntero")
        poner(o, "reg: onda press")
        poner(o + 3, "reg: onda hold")

    # name references, with the condition that separates them from the noise
    for o in range(n - 6):
        if b[o] != 0x04:
            continue
        d = u24(b, o + 3) - BASE
        if d < 3 or d >= n:
            continue
        if b[d - 3] == 0x05 and b[d - 2] == b[o + 1] and b[d - 1] == b[o + 2]:
            poner(o + 3, "referencia de nombre")

    return p


def sin_clasificar(b: bytes, en: int) -> int:
    """How many in-range u24 are NOT classified and point after `en`.

    **This is the number that decides whether shifting the blob is safe, and
    the measured answer is that it is not.** For an insertion at `0x0117bd`
    it gives **84,145**, against 4,614 classified pointers: a coverage of
    **4.76%**.

    It was added after breaking the remote. The previous test checked that
    the **known** pointers kept pointing at the same content, gave 0
    failures, and **measured the model instead of the blob**: the ones that
    aren't in the model are neither checked nor fixed, and with 84 thousand
    crossing the cut it is enough for one in a thousand to be real to leave
    the device in a boot loop. That is what happened.
    """
    conocidos = set(pointers(b))
    n = 0
    for o in range(len(b) - 2):
        if o in conocidos:
            continue
        v = u24(b, o) - BASE
        if en <= v < len(b):
            n += 1
    return n


def run(b: bytes, en: int, datos: bytes, igual_corro: bool = False) -> bytes:
    """Inserts `datos` at offset `en` and fixes the pointers that cross.

    **It refuses if there are unclassified u24 crossing the cut**, unless
    `igual_corro=True`. It is not a theoretical precaution: shifting 54 bytes
    with 84,145 unclassified left the remote in a boot loop and it had to be
    recovered through safemode.
    """
    if datos and not igual_corro:
        cruzan = sin_clasificar(b, en)
        if cruzan:
            raise SystemExit(
                "NOT SHIFTING: %d unclassified in-range u24 have a destination "
                "after %#x.\nShifting the blob with that coverage already broke the "
                "device once. Appending at the end does not have this problem."
                % (cruzan, en)
            )
    delta = len(datos)
    p = pointers(b)
    out = bytearray(b[:en]) + bytearray(datos) + bytearray(b[en:])

    for o, _kind in p.items():
        # the pointer position also shifts if it was after the cut
        pos = o + delta if o >= en else o
        v = u24(b, o) - BASE
        if v >= en:
            fresh = BASE + v + delta
            out[pos : pos + 3] = fresh.to_bytes(3, "little")
            # the master index stores u32: the fourth byte stays 0
            if 0x0C <= o < 0x0C + 4 * N_SECCIONES and (o - 0x0C) % 4 == 0:
                out[pos + 3] = 0

    # cierre y checksum
    close = u24(out, 4) - BASE
    if len(out) % 2 != close % 2:
        pass
    lo, hi = 0x21, 0x43
    for k in range(0, close - 2, 2):
        lo ^= out[k]
        hi ^= out[k + 1]
    out[close - 2] = lo
    out[close - 1] = hi
    return bytes(out)


def probar(b: bytes, en: int, n_bytes: int) -> int:
    """Shifts `n_bytes` of padding and demands that **everything still points
    to the same thing**.

    This is the test that counts, not the null shift. For each declared
    pointer **the pointed-at content** is compared before and after: if a
    pointer class was left unfixed, its destination changes and it shows up
    here.

    The whole button chain is walked too, and the command record and
    waveform counts are compared.
    """
    import irscan
    import relocate

    before = pointers(b)
    fresh = run(b, en, b"\x00" * n_bytes)

    # When comparing the pointed-at content you have to **mask the bytes that
    # are themselves pointers**: those change on purpose. Without the mask the
    # test gives 43 false positives -- command records whose internal
    # self-pointer updated fine, and the comparison window included it.
    mascara = set()
    for o in before:
        mascara.update(range(o, o + 3))

    def limpio(buf, d, desplazar):
        out = bytearray(buf[d : d + 24])
        for k in range(24):
            p_orig = (
                (d + k - n_bytes) if desplazar and (d + k) >= en + n_bytes else (d + k)
            )
            if p_orig in mascara:
                out[k] = 0
        return bytes(out)

    malos = []
    for o in before:
        pos = o + n_bytes if o >= en else o
        d_ant = u24(b, o) - BASE
        d_new = u24(fresh, pos) - BASE
        if not (0 <= d_ant < len(b) and 0 <= d_new < len(fresh)):
            continue
        if limpio(b, d_ant, False) != limpio(fresh, d_new, True):
            malos.append((o, before[o]))

    from collections import Counter

    print("\nTEST shift: %d bytes at %#x" % (n_bytes, en))
    print(
        "  pointers whose destination changed content: %d of %d"
        % (len(malos), len(before))
    )
    if malos:
        for k, v in Counter(c for _, c in malos).most_common():
            print("     %-24s %d" % (k, v))

    ca, cn = relocate.chain(b), relocate.chain(fresh)
    print("  cadena de botones: %d -> %d, identica: %s" % (len(ca), len(cn), ca == cn))
    wa, wn = len(irscan.find_waveforms(b)), len(irscan.find_waveforms(fresh))
    print("  ondas: %d -> %d" % (wa, wn))

    ok = not malos and ca == cn and wa == wn
    print("  VEREDICTO: %s" % ("the shift is safe" if ok else "NO CORRER"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("--nulo", action="store_true")
    ap.add_argument(
        "--probar",
        type=int,
        metavar="N",
        help="shift N bytes of padding and check that everything still points the same",
    )
    ap.add_argument("--en", type=lambda x: int(x, 0))
    ap.add_argument("--bytes")
    ap.add_argument("--salida")
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    p = pointers(b)
    from collections import Counter

    c = Counter(p.values())
    print("pointers declared by structure: %d" % len(p))
    for k, v in c.most_common():
        print("   %-24s %6d" % (k, v))

    if a.nulo:
        n = run(b, len(b) // 2, b"")
        igual = n == b
        print(
            "\ncorrimiento NULO (0 bytes): %s"
            % (
                "IDENTICO"
                if igual
                else "DIFFERENT -- the rewriter touches something it must not"
            )
        )
        if not igual:
            dif = [i for i in range(min(len(n), len(b))) if n[i] != b[i]]
            print("   %d bytes distintos, primeros: %s" % (len(dif), dif[:10]))
        return 0 if igual else 1

    if a.probar:
        return probar(b, a.en if a.en is not None else 0x0117BD, a.probar)

    if a.en is None or not a.bytes:
        ap.print_help()
        return 1
    datos = pathlib.Path(a.bytes).read_bytes()
    n = run(b, a.en, datos)
    print("\n%d B -> %d B (insertados %d en %#x)" % (len(b), len(n), len(datos), a.en))
    if a.salida:
        pathlib.Path(a.salida).write_bytes(n)
        print("escrito %s" % a.salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
