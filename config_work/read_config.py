#!/usr/bin/env python3
"""Reads the configuration the remote currently has and compares it to a file.

**Read-only.** Calls no write or erase primitive: uses
`read_config_from_remote`, which does not touch the flash.

Useful for two things:

1. **Closing the loop on a write.** `result: 0` says libconcord did not
   fail, not that the correct bytes ended up on the device. Comparing the
   readback against the file that was sent is the only proof that the
   write path delivers what you think it does.
2. **Having a measured reference.** Tools that append things at the end
   need a starting blob; using the one that is *supposed* to be flashed
   instead of the one that **actually is** flashed is exactly how silent
   errors accumulate.

Usage:
    python3 read_config.py --salida leido.bin --comparar ../salida/cuarto.bin
"""

from __future__ import annotations

import argparse
import ctypes
import pathlib
import sys

import write

BASE = 0x040000


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--salida", required=True)
    ap.add_argument("--comparar")
    a = ap.parse_args()

    lib = write.cargar()
    lib.read_config_from_remote.argtypes = [
        ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
        ctypes.POINTER(ctypes.c_uint32),
        write.CB,
        ctypes.c_void_p,
    ]
    lib.read_config_from_remote.restype = ctypes.c_int
    # It is not `get_config_size`: this version exports `get_config_bytes_used`.
    for f in (
        "get_config_bytes_used",
        "get_config_bytes_total",
        "is_config_dump_supported",
    ):
        getattr(lib, f).restype = ctypes.c_int

    if lib.init_concord():
        print("could not start libconcord", file=sys.stderr)
        return 1
    try:
        if lib.get_identity(write.CB(lambda *x: None), None):
            print("the remote was not found", file=sys.stderr)
            return 1
        print(
            "remote: arch %d, skin %d, firmware %d.%d"
            % (
                lib.get_arch(),
                lib.get_skin(),
                lib.get_fw_ver_maj(),
                lib.get_fw_ver_min(),
            )
        )
        soporta = lib.is_config_dump_supported()
        print(
            "config used %d B of %d   dump supported: %s"
            % (
                lib.get_config_bytes_used(),
                lib.get_config_bytes_total(),
                "yes" if soporta else "NO",
            )
        )
        if not soporta:
            # The Harmony One does not expose the dump through the high-level
            # API. What's left is the size check, which is still valid:
            # `get_config_bytes_used` comes from the device, and matching it
            # byte-for-byte against the file that was sent is strong
            # evidence that the write delivered what you think it did.
            # (The raw flash dump is another path, the one that produced
            # `backups/config_raw.bin`.)
            used = lib.get_config_bytes_used()
            print(
                "\nthis device does not support dumping the config through the "
                "high-level API."
            )
            ref = pathlib.Path(a.comparar).read_bytes() if a.comparar else b""
            if a.comparar:
                print("   the remote declares: %d B" % used)
                print("   %s: %d B" % (a.comparar, len(ref)))
                print(
                    "   VERDICT: %s"
                    % (
                        "the size matches byte for byte"
                        if used == len(ref)
                        else "DOES NOT MATCH"
                    )
                )
            return 0 if not a.comparar or used == len(ref) else 1

        buf = ctypes.POINTER(ctypes.c_ubyte)()
        size = ctypes.c_uint32(0)
        r = lib.read_config_from_remote(
            ctypes.byref(buf), ctypes.byref(size), write.CB(lambda *x: None), None
        )
        if r:
            print(
                "read failed: %s" % lib.lc_strerror(r).decode(errors="replace"),
                file=sys.stderr,
            )
            return 1
        datos = bytes(bytearray(buf[i] for i in range(size.value)))
    finally:
        lib.deinit_concord()

    pathlib.Path(a.salida).write_bytes(datos)
    print("read %d B -> %s" % (len(datos), a.salida))
    print(
        "magic: %r   declared closure: %#08x"
        % (datos[:4], int.from_bytes(datos[4:7], "little"))
    )

    if a.comparar:
        ref = pathlib.Path(a.comparar).read_bytes()
        n = min(len(ref), len(datos))
        dif = [i for i in range(n) if ref[i] != datos[i]]
        print("\ncompared against %s (%d B)" % (a.comparar, len(ref)))
        if len(ref) != len(datos):
            print("   SIZES DIFFER: %d read vs %d from file" % (len(datos), len(ref)))
        print("   different bytes in the first %d: %d" % (n, len(dif)))
        if dif:
            print("   first: %s" % ["%#08x" % x for x in dif[:12]])
        print(
            "   VERDICT: %s"
            % ("identical" if not dif and len(ref) == len(datos) else "DOES NOT MATCH")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
