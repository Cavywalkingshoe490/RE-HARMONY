#!/usr/bin/env python3
"""Patches the Harmony One firmware image and fixes its header.

It is the equivalent of `imgpatch.py` but for firmware instead of config. The
important difference is which safety net there is, and it is worth being
clear about it before using this:

    config    the firmware validates magics and the full XOR-16, and if that
              fails it loads the embedded copy. A bad config does not boot,
              but it breaks nothing.
    firmware  **the bootloader only looks at the `HG` magic**. It does not
              recompute the checksum. A corrupt image with the right magic
              RUNS.

So here the checksum protects nothing at boot: the host imposes it when
building the image. The real protection is another one, and it is structural:

  1. The write goes to `0x3D0000` (`CODE_NORMAL_APP`), **not** to `0x020000`.
     The application that is running is never the destination.
  2. The new image becomes active **only when the safemode runs** with the
     RAM flag 0 set to 2, which is what the copier at `0x0068B8` does.
  3. The safemode lives in the PIC's internal flash and the normal write path
     does not touch it.

That is why this tool **does not write**. It builds the image and verifies
it; writing it is another decision and another moment.

The header, from `UpdateHidService.getValidFirmware` case 12:

    [0:2]  XOR-16 checksum, seed 0x4321, over [4, 8+size)
    [2:4]  0xFFFF     [4:7] size u24 LE (total = size + 8)
    [8:10] magic 0x4847      [10:] code

Usage:
    python3 fwpatch.py <imagen.bin> info
    python3 fwpatch.py <imagen.bin> libre
    python3 fwpatch.py <imagen.bin> parchear <offset_hex> <bytes_hex> <salida.bin>
"""

from __future__ import annotations

import argparse
import pathlib
import sys

SEMILLA = (0x21, 0x43)
MAGIA = b"\x48\x47"
REGION = 0x20000  # the size of the region, not that of the image


def header(b: bytes):
    """(tamaño, total, checksum_guardado, checksum_calculado) o None."""
    if len(b) < 10 or b[8:10] != MAGIA:
        return None
    tam = int.from_bytes(b[4:7], "little")
    total = tam + 8
    if total > len(b):
        return tam, total, (b[0], b[1]), None
    lo, hi = SEMILLA
    for i in range(4, total, 2):
        lo ^= b[i]
        hi ^= b[i + 1]
    return tam, total, (b[0], b[1]), (lo, hi)


def arreglar(b: bytearray) -> bool:
    """Recomputes the checksum in place. True if anything changed."""
    c = header(bytes(b))
    if not c or c[3] is None:
        return False
    if c[2] == c[3]:
        return False
    b[0], b[1] = c[3]
    return True


def libres(b: bytes, total: int):
    """Runs of 0xFF of 16 bytes or more after the image body."""
    out = []
    i = total
    while i < len(b):
        if b[i] != 0xFF:
            i += 1
            continue
        j = i
        while j < len(b) and b[j] == 0xFF:
            j += 1
        if j - i >= 16:
            out.append((i, j - i))
        i = j
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("imagen")
    ap.add_argument("accion", choices=["info", "libre", "parchear"])
    ap.add_argument("resto", nargs="*")
    a = ap.parse_args()

    b = bytearray(pathlib.Path(a.imagen).read_bytes())
    c = header(bytes(b))
    if not c:
        print(
            "does not have the 0x4847 magic at offset 8: it is not an image of "
            "firmware de arch 12",
            file=sys.stderr,
        )
        return 1
    tam, total, stored, calculado = c

    if a.accion == "info":
        print("body              : %d B  (total %d with the header)" % (tam, total))
        print("checksum guardado : %02x %02x" % stored)
        print(
            "checksum calculado: %s"
            % ("%02x %02x" % calculado if calculado else "no se pudo")
        )
        print("coincide          : %s" % (stored == calculado))
        print("archivo           : %d B" % len(b))
        gap = REGION - total
        print(
            "space in the %#x region: %d B free after the body" % (REGION, gap)
        )
        return 0

    if a.accion == "libre":
        ls = libres(bytes(b), total)
        print("runs of 0xFF of 16 B or more after the body (%d):" % len(ls))
        for o, n in ls[:20]:
            print("  %#08x  %7d B" % (o, n))
        if not ls:
            print("  (none inside the file; the free space is between")
            print(
                "   the end of the body %#x and the end of the region %#x)" % (total, REGION)
            )
        return 0

    off = int(a.resto[0], 16)
    datos = bytes.fromhex(a.resto[1].replace(" ", ""))
    salida = pathlib.Path(a.resto[2])

    if off + len(datos) > len(b):
        print("the patch runs past the end of the file", file=sys.stderr)
        return 1
    if off < 10:
        print(
            "the header is not patched: bytes 0..9 are checksum, size and "
            "magic, and this tool handles them",
            file=sys.stderr,
        )
        return 1

    fresh = bytearray(b)
    fresh[off : off + len(datos)] = datos
    recalc = arreglar(fresh)
    c2 = header(bytes(fresh))
    if c2 is None:
        # The patch overwrote the magic or left the header unreadable. Without it
        # there is nothing to verify, so it stops before printing a report that
        # would suggest the image is good.
        print("the patch destroyed the header: nothing was written", file=sys.stderr)
        return 1

    difs = [i for i in range(len(b)) if b[i] != fresh[i]]
    esperados = set(range(off, off + len(datos))) | {0, 1}
    inside = all(i in esperados for i in difs)
    ok = len(fresh) == len(b) and c2[1] == total and c2[2] == c2[3] and inside

    print("  tamaño identico       : %s" % (len(nuevo) == len(b)))
    print("  body size             : %d (unchanged: %s)" % (c2[0], c2[1] == total))
    print("  checksum              : %s" % ("recalculado" if recalc else "sin cambios"))
    print("  checksum coincide     : %s" % (c2[2] == c2[3]))
    print(
        "  bytes changed         : %d, only the patch and the checksum: %s"
        % (len(difs), inside)
    )
    print(
        "  VEREDICTO             : %s" % ("imagen bien formada" if ok else "RECHAZAR")
    )
    print()
    print("  Remember: the bootloader does NOT validate the checksum. The image")
    print("  being well formed does not say the patched code is correct.")
    if not ok:
        print("\n  nothing was written.")
        return 1
    salida.write_bytes(bytes(fresh))
    print("\n  escrito %s" % salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
