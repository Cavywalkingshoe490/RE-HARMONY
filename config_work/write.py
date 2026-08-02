#!/usr/bin/env python3
"""Writes a configuration `.EZHex` to the Harmony One. **This DOES write flash.**

The only tool in the project that writes to the device, and it uses **only**
the configuration path:

    prep_config -> invalidate_flash -> erase_config -> write_config_to_remote

`erase_config` erases starting at `ri.arch->config_base`, which for arch 12
is **`0x040000`** (verified in `remote_info.h`): the configuration region and
nothing else. **Does not touch** the bootloader (`0xFE0000`), the safemode
(`0xFE1000`), the application (`0x020000`) or the embedded config
(`0x002000`).

## Why that matters

In arch 12, `firmware_base` and `firmware_update_base` are **0**, so the
*firmware* primitives -- `erase_firmware`, `write_firmware_to_remote`,
`erase_safemode` -- erase starting at `0x000000` and **destroy the embedded
config with no possible repair** (`write_safemode_to_remote` does not exist).
This script neither calls nor exposes them.

## The recovery net

1. If the written config were invalid, the application validates magics +
   XOR-16 and **falls back to the embedded config**, which this path does
   not touch.
2. Safemode is forced with **POWER** on power-up (measured).
3. And above all: **there is an exact backup of the current config**, so it
   can always be written back.

Usage:
    python3 write.py <file.EZHex>
    python3 write.py <file.EZHex> --verificar-solo   # does not write
"""

from __future__ import annotations

import argparse
import ctypes
import os
import pathlib
import platform
import sys

#: there is NO last-resort path. libconcord is NOT distributed with
#: this project: it is free software from another project (Concordance,
#: GPLv3) and is installed separately. When it is not found, `LIB` is left
#: at None and `cargar()` raises `LibconcordAusente` with instructions,
#: instead of a ctypes `OSError` with somebody else's machine path.
_LIB_RUTA_DEV = None
_ENV_LIB = "RE_HARMONY_LIBCONCORD"
_LIB_NOMBRE_POR_SO = {
    "Darwin": "libconcord.6.dylib",
    "Linux": "libconcord.so.6",
    "Windows": "libconcord.dll",
}


def _lib_predeterminada() -> str | None:
    """Resolves libconcord's path without leaving anything hardcoded.

    Priority order:
    1. `RE_HARMONY_LIBCONCORD` in the environment, if set.
    2. The library next to this script (name per `platform.system()`) -- the
       case of a `.dylib`/`.so`/`.dll` bundled alongside the source code,
       e.g. via PyInstaller's `--add-binary`.
    3. The library next to the running executable (`sys.executable`) -- the
       case of a packaged app where the final binary lives in a different
       directory than the extracted source code.
    4. The library installed on the system, resolved BY NAME by the OS
       loader (`/usr/local/lib`, `/usr/lib`, `LD_LIBRARY_PATH`...) --
       the normal case after installing `concordance`.
    5. `None`, if none of the above worked. `cargar()` then raises
       `LibconcordAusente` with instructions. There is deliberately NO
       hardcoded last-resort path: a path from somebody else's machine
       only produces an `OSError` that explains nothing.
    """
    from_env = os.environ.get(_ENV_LIB)
    if from_env:
        return from_env

    name = _LIB_NOMBRE_POR_SO.get(platform.system(), _LIB_NOMBRE_POR_SO["Darwin"])

    junto_al_script = pathlib.Path(__file__).resolve().parent / name
    if junto_al_script.exists():
        return str(junto_al_script)

    junto_al_ejecutable = pathlib.Path(sys.executable).resolve().parent / name
    if junto_al_ejecutable.exists():
        return str(junto_al_ejecutable)

    # 5. installed on the system: let the OS loader look it up by
    #    nombre (`/usr/local/lib`, `/usr/lib`, `LD_LIBRARY_PATH`...).
    #    That is the case if a `make install` was done of the libconcord
    #    parcheada (ver README.md).
    try:
        ctypes.CDLL(name)
    except OSError:
        return None
    return name


LIB = _lib_predeterminada()
CB = ctypes.CFUNCTYPE(
    None,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32),
)


class LibconcordAusente(RuntimeError):
    """libconcord was not found. Carries the text the UI shows."""


TEXT_WITHOUT_LIBCONCORD = (
    "I can't find libconcord. It is the library that talks over USB to the "
    "remote; it does not ship with this app because it is free software from another "
    "project (Concordance, GPLv3) and is built separately.\n"
    "And the PATCHED one is needed: the upstream one loads fine and then it can't "
    "read anything, because this remote has firmware_base = 0.\n"
    "The instructions are in README.md.\n"
    "If you already built it, point me there with {env}=/path/to/libconcord.6.dylib\n"
    "In the meantime the app works just the same: you can browse the library, "
    "import .ir codes and prepare changes. The only thing I can't "
    "do is read or write the remote."
).format(env=_ENV_LIB)


def cargar():
    if LIB is None:
        raise LibconcordAusente(TEXT_WITHOUT_LIBCONCORD)
    try:
        lib = ctypes.CDLL(LIB)
    except OSError as exc:
        raise LibconcordAusente("%s\n\n(%s)" % (TEXT_WITHOUT_LIBCONCORD, exc)) from exc
    lib.init_concord.restype = ctypes.c_int
    lib.deinit_concord.restype = ctypes.c_int
    lib.get_identity.argtypes = [CB, ctypes.c_void_p]
    lib.get_identity.restype = ctypes.c_int
    lib.read_and_parse_file.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
    lib.read_and_parse_file.restype = ctypes.c_int
    lib.update_configuration.argtypes = [CB, ctypes.c_void_p, ctypes.c_int]
    lib.update_configuration.restype = ctypes.c_int
    lib.delete_opfile_obj.restype = None
    lib.lc_strerror.argtypes = [ctypes.c_int]
    lib.lc_strerror.restype = ctypes.c_char_p
    for f in ("get_arch", "get_skin", "get_fw_ver_maj", "get_fw_ver_min"):
        getattr(lib, f).restype = ctypes.c_int
    return lib


BASE = 0x040000
# Of the old body only the close pointer (+4..+6) and the master index's
# [9][10][11] entries are allowed to change. Any other difference means
# something **moved**, and moving is what bricked the remote.
ALLOWED = set(range(4, 7)) | set(range(0x0C + 4 * 9, 0x0C + 4 * 12))


def nothing_moved(
    referencia: bytes, fresh: bytes, extra: set[int] | None = None
) -> tuple[bool, list]:
    """The new blob has to contain the old one **without shifting a byte**.

    This is the validation that was missing. It was added after writing a
    blob shifted by 54 bytes that left the remote in a boot loop: every
    earlier check looked at **the model** (whether the known pointers still
    resolved) and none looked at **the physical fact** of data having moved.

    Appending at the end passes this test; shifting the blob does not. And
    shifting cannot be validated: there are 84,145 in-range u24s of
    unclassified nature that would cross the cut.

    `extra` allows declaring **intentional repoints**: changing an existing
    pointer's destination moves no data, but still falls into this net. The
    offsets have to be passed **one at a time** (`--repoint`), not a range:
    the point is that whoever writes enumerates exactly which bytes they
    touch, and that an unanticipated change keeps blocking the write. It is
    the opposite of `--igual-grabo`, which turns off the whole check.
    """
    close = int.from_bytes(referencia[4:7], "little") - BASE
    end = min(close - 2, len(fresh))
    dif = [i for i in range(end) if referencia[i] != fresh[i]]
    return (set(dif) <= (ALLOWED | (extra or set())), dif)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("ezhex")
    ap.add_argument(
        "--referencia",
        help="known-good .bin blob; requires that no byte has moved",
    )
    ap.add_argument(
        "--repoint",
        metavar="OFFSET",
        action="append",
        default=[],
        type=lambda x: int(x, 0),
        help="offset of a pointer that is intentionally repointed; 3 bytes from "
        "there are allowed. Repeatable. Does not turn off the check: makes it explicit",
    )
    ap.add_argument(
        "--igual-grabo",
        action="store_true",
        help="skip the check that nothing moved (dangerous)",
    )
    ap.add_argument(
        "--verify-only",
        action="store_true",
        help="identifies the remote and validates the file, but does NOT write",
    )
    a = ap.parse_args()

    path = pathlib.Path(a.ezhex)
    if not path.exists():
        print("%s does not exist" % path, file=sys.stderr)
        return 1

    lib = cargar()
    progreso = {"last": -1}

    def avance(etapa, hecho, total, *resto):
        pct = 100 * hecho // max(total, 1)
        if pct // 10 != progreso["last"]:
            progreso["last"] = pct // 10
            print("   stage %d: %d%%" % (etapa, pct), flush=True)

    cb = CB(lambda e, d, t, *r: avance(e, d, t))

    if lib.init_concord():
        print("remote not found", file=sys.stderr)
        return 1
    try:
        if lib.get_identity(cb, None):
            print("could not identify", file=sys.stderr)
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
        if lib.get_arch() != 12:
            print("unexpected arch; nothing will be written", file=sys.stderr)
            return 1

        kind = ctypes.c_int(0)
        e = lib.read_and_parse_file(str(path).encode(), ctypes.byref(kind))
        if e:
            print(
                "the file does not validate (%d): %s"
                % (e, lib.lc_strerror(e).decode()[:90]),
                file=sys.stderr,
            )
            return 1
        print("file accepted by libconcord, type %d" % kind.value)

        if a.referencia:
            import ezhex

            _, binario = ezhex.split(path.read_bytes())
            ref = pathlib.Path(a.referencia).read_bytes()
            extra = {p + k for p in a.repoint for k in range(3)}
            ok, dif = nothing_moved(ref, binario, extra)
            print(
                "nothing moved relative to %s: %s (%d different bytes)"
                % (a.referencia, "YES" if ok else "NO", len(dif))
            )
            if a.repoint:
                sin_declarar = sorted(set(dif) - ALLOWED - extra)
                print(
                    "declared repoints: %s   bytes outside what was declared: %s"
                    % (
                        ", ".join("%#08x" % p for p in a.repoint),
                        sin_declarar if sin_declarar else "none",
                    )
                )
            if not ok and not a.igual_grabo:
                print(
                    "\nNOT WRITING: the blob has shifted data.\n"
                    "Shifting the blob already left the remote in a boot loop once.\n"
                    "Use --igual-grabo only if you know exactly why.",
                    file=sys.stderr,
                )
                return 1
        elif not a.verify_only and not a.igual_grabo:
            print(
                "\nNOT WRITING: --referencia with a known-good blob is missing.\n"
                "Without it there is no way to validate that nothing has moved.",
                file=sys.stderr,
            )
            return 1

        if a.verify_only:
            print("\n--verificar-solo: nothing was written.")
            return 0

        print("\nwriting the configuration (this takes a while)...")
        e = lib.update_configuration(cb, None, 0)
        print(
            "result: %d%s"
            % (e, "" if e == 0 else "  %s" % lib.lc_strerror(e).decode()[:90])
        )
        return 0 if e == 0 else 1
    finally:
        try:
            lib.delete_opfile_obj()
        except Exception:  # noqa: BLE001
            pass
        try:
            lib.deinit_concord()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
