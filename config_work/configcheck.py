#!/usr/bin/env python3
"""The write's safety rules, as code that refuses -- not as a note.

Everything below came out of reading the firmware's own validator (0x028D92) and
auditing libconcord's write path. It is worth having it be executable and not
prose: a config that fails any of these five tests either gets rejected by the
device, or worse, triggers the only known path to real damage.

    1. 'GSPM' magic at offset 0
    2. 'LWJL' at offset 0x63
    3. the u24 at offset 4 points at the 'PTYY' close
    4. 16-bit XOR, seed (0x21, 0x43), over [0, close) == the u16 that
       precedes 'PTYY'
    5. size under 0x3B0000

The fourth is the one that bites silently. The firmware computes and compares
it; an image modified without recomputing it gets rejected with no message at
all. Verified: the original config gives c6 26 and matches; changing its pixels
to a background moves it to 3f 66, and the stored value goes stale.

The fifth has two independent causes and keeps the stricter one.

The first is from the map: `CODE_NORMAL_APP_ADDRESS` sits at `0x3D0000` and
holds a **byte-for-byte identical** copy of the application running at
`0x020000` (verified: 60,050 B, 60050/60050 equal). The config starts at
`0x040000`, so from `0x390000` of size onward it overwrites that copy. Logitech
declares the config region as `0x040000 + 0x3C0000`, which reaches `0x400000`
and therefore **overlaps its own backup copy**; the declared limit does not
protect.

The second is not the device's but the host's. libconcord's
`CRemote::EraseFlash` walks the sector table with no guard, and `sectors6` ends
in a literal `0`: with a size above 0x3B0000 the loop reads past the array and
emits that garbage as erase-sector addresses. If any of it lands between
0x002000 and 0x03FFFF it destroys the embedded copy, which is precisely the
safety net -- the firmware loads it when the user config fails to validate.
Losing it does not brick the device, but there stops being anything to fall
back to.

Usage:
    python3 configcheck.py <config.bin>
    python3 configcheck.py <config.bin> --arreglar <salida.bin>
"""

from __future__ import annotations

import argparse
import pathlib

BASE = 0x040000
NORMAL_APP = 0x3D0000  # CODE_NORMAL_APP_ADDRESS, from Protocol12's official map
MAX_LIBCONCORD = 0x3B0000  # above this, libconcord's sector loop reads past
# the array and erases where it shouldn't
MAX_COLISION = NORMAL_APP - BASE  # 0x390000: above this, the config overwrites
# the application's backup copy
MAX_SEGURO = min(MAX_LIBCONCORD, MAX_COLISION)
SEMILLA = (0x21, 0x43)


def checksum(b: bytes, fin: int) -> tuple:
    """16-bit XOR over [0, fin), even and odd bytes separately."""
    lo, hi = SEMILLA
    for i in range(0, fin, 2):
        lo ^= b[i]
        hi ^= b[i + 1]
    return lo, hi


def close(b: bytes):
    """Where the tail <u16 checksum><'PTYY'> starts, per the pointer at +4."""
    ptr = int.from_bytes(b[4:7], "little")
    off = ptr - BASE
    if not 0 < off <= len(b):
        return None
    return off - 2  # the u16 sits right before PTYY


def revisar(b: bytes):
    """Returns the list of (test, ok, detail)."""
    out = []
    out.append(("GSPM magic at +0", b[:4] == b"GSPM", repr(b[:4])))
    out.append(("LWJL at +0x63", b[0x63:0x67] == b"LWJL", repr(b[0x63:0x67])))

    c = close(b)
    if c is None:
        out.append(
            (
                "the u24 at +4 points inside",
                False,
                "%#08x out of range" % int.from_bytes(b[4:7], "little"),
            )
        )
        return out
    ptyy_ok = b[c + 2 : c + 6] == b"PTYY"
    out.append(
        (
            "the u24 at +4 points at PTYY",
            ptyy_ok,
            "close at %#08x, there is %r" % (c, b[c + 2 : c + 6]),
        )
    )

    calc = checksum(b, c)
    stored = (b[c], b[c + 1])
    out.append(
        (
            "XOR-16 checksum",
            calc == stored,
            "computed %02x %02x, stored %02x %02x"
            % (calc[0], calc[1], stored[0], stored[1]),
        )
    )

    out.append(
        (
            "size below %#08x" % MAX_SEGURO,
            len(b) < MAX_SEGURO,
            "%d bytes (%#08x)" % (len(b), len(b)),
        )
    )
    return out


def arreglar(b: bytearray) -> bool:
    """Recomputes the checksum in place. True if something changed."""
    c = close(bytes(b))
    if c is None:
        return False
    lo, hi = checksum(bytes(b), c)
    if (b[c], b[c + 1]) == (lo, hi):
        return False
    b[c], b[c + 1] = lo, hi
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("config")
    ap.add_argument("--arreglar", metavar="SALIDA")
    a = ap.parse_args()
    b = bytearray(pathlib.Path(a.config).read_bytes())

    if a.arreglar:
        change = arreglar(b)
        pathlib.Path(a.arreglar).write_bytes(bytes(b))
        print(
            "checksum %s -> %s"
            % ("recomputed" if change else "was already fine", a.arreglar)
        )
        print()

    pruebas = revisar(bytes(b))
    width = max(len(p[0]) for p in pruebas)
    for name, ok, detail in pruebas:
        print("  %-*s  %-8s %s" % (width, name, "OK" if ok else "FAIL", detail))
    todo = all(p[1] for p in pruebas)
    print(
        "\n  VERDICT: %s" % ("fit to write" if todo else "DO NOT WRITE: a test failed")
    )
    return 0 if todo else 1


if __name__ == "__main__":
    raise SystemExit(main())
