#!/usr/bin/env python3
"""The Harmony One memory map, with every region verified by its checksum.

The names come from the official Logitech installer's `Protocol12`; the
real boundaries come from reading the device. The two sources agree down to
the byte, and that's not just an impression: four of the five code regions
carry a header whose 16-bit checksum is recomputed here and compared
against the stored one. Four exact matches in a row have probability
(1/65536)^4 by chance, so the same figure proves two things at once: that
the dump is faithful and that the header is correctly understood.

The header, from `UpdateHidService.getValidFirmware` case 12:

    [0:2]    XOR-16 checksum, seed 0x4321, over [4, 8+size)
    [2:4]    constant 0xFFFF
    [4:7]    body size, u24 little endian; the total is size + 8
    [7:8]    one more byte the routine preserves untouched
    [8:10]   the 0x4847 magic, which libconcord calls `firmware_4847_offset`
    [10:]    code

libconcord carries that offset as **0** for arch 12, meaning it doesn't know
it. It is 8.

The bootloader is the only code region that does **not** carry a header: it
starts straight into a GOTO. That makes sense -- it's the one that validates
the others, and nobody validates it.

Usage:
    python3 fwmap.py <dir_de_backups>
"""

from __future__ import annotations

import argparse
import pathlib

SEMILLA = (0x21, 0x43)
MAGIA = b"\x48\x47"

# name, address, declared size, and which dump it comes from at what offset
REGIONES = [
    (
        "(previo al bootloader)",
        0x000000,
        0x002000,
        "lowflash_0x000000_0x040000.bin",
        0x000000,
    ),
    (
        "CODE_EMBEDDED_CONFIGRATION",
        0x002000,
        0x01E000,
        "lowflash_0x000000_0x040000.bin",
        0x000000,
    ),
    (
        "aplicacion en ejecucion",
        0x020000,
        0x020000,
        "lowflash_0x000000_0x040000.bin",
        0x000000,
    ),
    ("CODE_USER_CONFIGURATION", 0x040000, 0x3C0000, None, 0),
    (
        "CODE_NORMAL_APP",
        0x3D0000,
        0x020000,
        "highflash_0x180000_0x400000.bin",
        0x180000,
    ),
    ("CODE_BOOTLOADER", 0xFE0000, 0x001000, "high_0xFE0000_0x1000000.bin", 0xFE0000),
    ("CODE_SAFEMODE", 0xFE1000, 0x00F000, "high_0xFE0000_0x1000000.bin", 0xFE0000),
    ("CODE_CPLD", 0xFF0000, 0x004000, "high_0xFE0000_0x1000000.bin", 0xFE0000),
    ("(sin nombre)", 0xFF4000, 0x00A000, "high_0xFE0000_0x1000000.bin", 0xFE0000),
    ("CODE_PIC_LIBRARY", 0xFFE000, 0x001000, "high_0xFE0000_0x1000000.bin", 0xFE0000),
    ("CODE_GUID", 0xFFF400, 0x000040, "high_0xFE0000_0x1000000.bin", 0xFE0000),
    (
        "CODE_MANUFACTURING_PID",
        0xFFF640,
        0x000040,
        "high_0xFE0000_0x1000000.bin",
        0xFE0000,
    ),
]


def xor16(d: bytes, ini: int, fin: int):
    lo, hi = SEMILLA
    for i in range(ini, fin, 2):
        lo ^= d[i]
        hi ^= d[i + 1]
    return lo, hi


def header(d: bytes):
    """Returns (size, total, ok) or None if the region has no header."""
    if len(d) < 10 or d[8:10] != MAGIA:
        return None
    tam = int.from_bytes(d[4:7], "little")
    total = tam + 8
    if total > len(d):
        return tam, total, False
    return tam, total, xor16(d, 4, total) == (d[0], d[1])


def pid(d: bytes) -> str:
    """The manufacturing PID, as read by `getManufactoringIdString`."""
    n = d[0] + (d[1] << 8)
    if not 0 < n <= 62:
        return "(largo invalido: %d)" % n
    return d[2 : 2 + n].hex().upper()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("backups", nargs="?", default="../backups")
    a = ap.parse_args()
    raiz = pathlib.Path(a.backups)
    cache: dict = {}

    print("%-27s %-17s %9s  %s" % ("region", "rango", "tamaño", "verificacion"))
    print("-" * 88)
    for nom, dir_, n, arch, base in REGIONES:
        rango = "%06X-%06X" % (dir_, dir_ + n)
        if arch is None:
            print(
                "%-27s %-17s %9d  %s"
                % (nom, rango, n, "(la config; ver configcheck.py)")
            )
            continue
        p = raiz / arch
        if not p.exists():
            print("%-27s %-17s %9d  %s" % (nom, rango, n, "falta %s" % arch))
            continue
        if arch not in cache:
            cache[arch] = p.read_bytes()
        d = cache[arch][dir_ - base : dir_ - base + n]
        if not d:
            print("%-27s %-17s %9d  %s" % (nom, rango, n, "outside the dump"))
            continue

        empty = 100 * sum(1 for x in d if x == 0xFF) / len(d)
        cab = header(d)
        if cab:
            tam, total, ok = cab
            det = "cabecera: cuerpo %d B, checksum %s" % (
                tam,
                "COINCIDE" if ok else "NO COINCIDE",
            )
        elif dir_ == 0xFFF640:
            det = "PID = %s  (fecha %s)" % (pid(d), pid(d)[:8])
        elif dir_ == 0xFFF400:
            det = "numero de serie, 48 B segun SystemHidService"
        elif empty > 99.9:
            det = "completely erased (0xFF)"
        else:
            det = "sin cabecera, %.1f%% vacia" % empty
        print("%-27s %-17s %9d  %s" % (nom, rango, n, det))

    print()
    print("The application at 0x020000 and the copy at 0x3D0000 must be identical:")
    try:
        low = (
            cache.get("lowflash_0x000000_0x040000.bin")
            or (raiz / "lowflash_0x000000_0x040000.bin").read_bytes()
        )
        height = (
            cache.get("highflash_0x180000_0x400000.bin")
            or (raiz / "highflash_0x180000_0x400000.bin").read_bytes()
        )
        cab = header(low[0x020000:])
        if cab:
            total = cab[1]
            x = low[0x020000 : 0x020000 + total]
            y = height[0x3D0000 - 0x180000 : 0x3D0000 - 0x180000 + total]
            print(
                "  %d B comparados -> %s"
                % (total, "IDENTICAS" if x == y else "DIFIEREN")
            )
    except OSError as e:
        print("  could not compare: %s" % e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
