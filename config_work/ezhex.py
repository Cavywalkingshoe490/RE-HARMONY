#!/usr/bin/env python3
"""Reads and builds `.EZHex` files, the container used to push configuration.

Came out of decompiling `oemdata.jar`, the only installer jar that hadn't
been touched -- and it turned out to hold the entire format layer: `EZFile`,
`EZHex`, `HarmonyConfigFile`, `IntelHex32File`, `FileConverter`.

The format, from `EZFile.parseByteArray()`:

    <?xml version="1.0"?>
    <INFORMATION>
      ... header ...
    </INFORMATION>
    <binary bytes through the end of the file>

The cut is literal: lines are read until one that **ends** in
`</INFORMATION>`, and everything after that is the binary. The original
counts the line breaks it normalizes to compute where it starts; here the
same is done by skipping any trailing `\\r`/`\\n` stuck to the closing tag.

The checksum, from `EZHex.verifyChecksum()`, is **a single byte**: XOR of
the whole binary with seed **105 (0x69)**, compared against the `CHECKSUM`
element. If there is no `CHECKSUM`, the original treats the check as passed.

`INTENDEDVERSION` is the compatibility check, and its five fields are
exactly what libconcord's `get_identity` returns. On this device:

    PROTOCOL 12   SKIN 54   FLASH 0x1F:0xC8   BOARD 0.5.0   SOFTWARETYPE 0

Verified against the three real `.EZHex` files: 3 of 3 with `BINARYDATASIZE`
and `CHECKSUM` matching, and the binary is the 1,316,666-byte `GSPM` blob.

**This writes nothing to the device.** It builds the file; pushing it to the
device is a separate decision.

Usage:
    python3 ezhex.py leer <archivo.EZHex>
    python3 ezhex.py extraer <archivo.EZHex> <salida.bin>
    python3 ezhex.py armar <plantilla.EZHex> <config.bin> <salida.EZHex>
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

CIERRE = b"</INFORMATION>"
SEMILLA = 0x69


def split(datos: bytes):
    """Returns (header_bytes, binary). Raises if there is no header."""
    i = datos.find(CIERRE)
    if i < 0:
        raise ValueError("no <INFORMATION> header: not an EZHex")
    fin = i + len(CIERRE)
    while fin < len(datos) and datos[fin] in (13, 10):
        fin += 1
    return datos[:fin], datos[fin:]


def checksum(binario: bytes) -> int:
    c = SEMILLA
    for x in binario:
        c ^= x
    return c


def get_field(header: str, name: str):
    m = re.search(r"<%s>(.*?)</%s>" % (name, name), header, re.S)
    return m.group(1).strip() if m else None


def set_field(header: str, name: str, value: str) -> str:
    return re.sub(
        r"(<%s>)(.*?)(</%s>)" % (name, name),
        lambda m: m.group(1) + value + m.group(3),
        header,
        count=1,
        flags=re.S,
    )


def check(header: str, binario: bytes):
    """(check, ok, detail) for each check the installer performs."""
    out = []
    tam = get_field(header, "BINARYDATASIZE")
    out.append(
        (
            "BINARYDATASIZE",
            tam == str(len(binario)),
            "says %s, there are %d" % (tam, len(binario)),
        )
    )
    chk = get_field(header, "CHECKSUM")
    calc = checksum(binario)
    out.append(
        (
            "CHECKSUM",
            chk == str(calc) if chk else True,
            "says %s, computed %d%s"
            % (chk, calc, "" if chk else " (no field: accepted)"),
        )
    )
    out.append(("binary magic", binario[:4] == b"GSPM", repr(binario[:4])))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("accion", choices=["leer", "extraer", "armar"])
    ap.add_argument("resto", nargs="+")
    a = ap.parse_args()

    datos = pathlib.Path(a.resto[0]).read_bytes()
    cab_b, binario = split(datos)
    cab = cab_b.decode("utf-8", "replace")

    if a.accion == "leer":
        print(
            "file   %d B   header %d B   binary %d B"
            % (len(datos), len(cab_b), len(binario))
        )
        print("\nINTENDEDVERSION, which is the compatibility check:")
        for k in ("PROTOCOL", "SKIN", "FLASH", "BOARD", "SOFTWARETYPE"):
            print("  %-14s %s" % (k, get_field(cab, k)))
        print("\ninstaller checks:")
        for n, ok, det in check(cab, binario):
            print("  %-18s %-6s %s" % (n, "OK" if ok else "FAIL", det))
        return 0

    if a.accion == "extraer":
        pathlib.Path(a.resto[1]).write_bytes(binario)
        print("extracted %d bytes -> %s" % (len(binario), a.resto[1]))
        return 0

    fresh = pathlib.Path(a.resto[1]).read_bytes()
    salida = pathlib.Path(a.resto[2])
    if fresh[:4] != b"GSPM":
        print("binary does not start with GSPM: not a config", file=sys.stderr)
        return 1

    cab2 = set_field(cab, "BINARYDATASIZE", str(len(fresh)))
    cab2 = set_field(cab2, "CHECKSUM", str(checksum(fresh)))
    pruebas = check(cab2, fresh)
    ok = all(p[1] for p in pruebas)
    for n, correct, det in pruebas:
        print("  %-18s %-6s %s" % (n, "OK" if correct else "FAIL", det))
    if not ok:
        print("\nnothing was written.", file=sys.stderr)
        return 1
    salida.write_bytes(cab2.encode("utf-8") + fresh)
    print("\nwrote %s (%d B)" % (salida, salida.stat().st_size))
    print("The header keeps the template's INTENDEDVERSION: if the config is")
    print("for another device, it has to be changed or the installer rejects it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
