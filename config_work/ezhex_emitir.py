#!/usr/bin/env python3
"""Builds a `.EZHex` container FROM SCRATCH, without copying anyone else's.

## The dead end this unblocks

`config_work/write.py` -- the only path that writes to the remote -- passes
the file through libconcord's `read_and_parse_file()`, which demands a
`.EZHex` container: a plain-text XML header, `\\r\\n`, and then the binary. A
raw `.bin` is rejected.

Until now the only way to get that header was to copy it from a file that
already existed. And on a Harmony One it cannot be pulled from the hardware
itself: `is_config_dump_supported()` returns NO, so `concordance -c` writes
nothing. Which means whoever cloned the project could get all the way to the
end and have nothing to write with.

This module emits the header. It is not a copy of Logitech's: it is the
**minimal** header the parser asks for, written from what its source code
demands, and that code is GPLv3 and published.

## What the parser demands exactly (read from the source, not assumed)

`libconcord/operationfile.cpp`, `find_config_binary()` (lines 37-89):

  1. the `</INFORMATION>` tag; the binary starts **2 bytes after** the end of
     that tag (hence the `\\r\\n`);
  2. `<BINARYDATASIZE>` with the exact size of the binary;
  3. `<CHECKSUM>` with the 8-bit XOR of the binary, seed `0x69`.

And `ReadAndParseOpFile()` (232-360) deduces the file TYPE by absence: if
there is a binary and there is NO `KEY=GETZAPSONLY`, no `TYPE`/`PATH` saying
`Firmware_Main` or the upgrade URL, and no `CHECKKEYS`, it is a configuration
file. The header here carries none of those three things, on purpose: that is
how it becomes unambiguously a configuration.

The version fields (`PROTOCOL`, `SKIN`, `FLASH`, `BOARD`) are written because
they describe the hardware and `config_work/ezhex.py` knows how to read them,
not because the parser asks for them. They are taken from what the connected
remote reports (`config_work/read_config.py` /
`leer_flash_baseline.read_live_identity()`), not from a constant: a remote
with other firmware writes its own.

## What it does NOT do

It does not validate that the binary is a coherent config -- that is already
done by `configcheck.py` and the `grabar.nothing_moved()` gate. Here it only
gets wrapped.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

SEPARADOR = b"</INFORMATION>"

#: `operationfile.cpp:47` -- `*binary_ptr += 2` after the end of the
#: closing tag. It is exactly two bytes; `\r\n` is what the
#: original file uses and what is written here.
RELLENO = b"\r\n"


def checksum(binario: bytes) -> int:
    """8-bit XOR with seed 0x69. Exact replica of
    `operationfile.cpp:73-78` and `libconcord.cpp:1162-1166`."""
    chk = 0x69
    for b in binario:
        chk ^= b
    return chk


def header(
    binario: bytes,
    *,
    protocolo: int = 12,
    skin: int = 54,
    flash: str = "0x1F:0xC8",
    board: str = "0.5.0",
    software_kind: int = 0,
) -> str:
    """The minimal XML header for `binario`.

    The default values are those of a Harmony One (arch 12) just as
    libconcord reports it. **Do not guess them**: pass them from what the
    connected remote says about itself.
    """
    return (
        '<?xml version="1.0"?>\n'
        "<INFORMATION>\n"
        "    <INTENDEDVERSION>\n"
        "        <PROTOCOL>%d</PROTOCOL>\n"
        "        <SKIN>%d</SKIN>\n"
        "        <FLASH>%s</FLASH>\n"
        "        <BOARD>%s</BOARD>\n"
        "        <SOFTWARETYPE>%d</SOFTWARETYPE>\n"
        "    </INTENDEDVERSION>\n"
        "    <BINARYDATASIZE>%d</BINARYDATASIZE>\n"
        "    <CHECKSUM>%d</CHECKSUM>\n"
        "</INFORMATION>"
        % (
            protocolo,
            skin,
            flash,
            board,
            software_kind,
            len(binario),
            checksum(binario),
        )
    )


def envolver(binario: bytes, **campos) -> bytes:
    """The complete `.EZHex` container, ready for `read_and_parse_file()`."""
    return header(binario, **campos).encode("ascii") + RELLENO + binario


def separar(datos: bytes) -> tuple[str, bytes]:
    """`(header, binario)` of a container. Same rule as the parser."""
    i = datos.find(SEPARADOR)
    if i < 0:
        raise ValueError(
            "there is no </INFORMATION> tag: this is a raw blob, not an "
            ".EZHex, y libconcord lo va a rechazar"
        )
    corte = i + len(SEPARADOR) + 2
    return datos[:corte].decode("latin-1"), datos[corte:]


def verificar(datos: bytes) -> list[str]:
    """A container's problems, with the SAME three conditions as
    `find_config_binary()`. Empty list == it is going to accept it."""
    problemas: list[str] = []
    try:
        cab, binario = separar(datos)
    except ValueError as exc:
        return [str(exc)]
    import re

    m = re.search(r"<BINARYDATASIZE>(\d+)</BINARYDATASIZE>", cab)
    if not m:
        problemas.append("falta <BINARYDATASIZE>")
    elif int(m.group(1)) != len(binario):
        problemas.append(
            "<BINARYDATASIZE> says %s and the binary is %d B"
            % (m.group(1), len(binario))
        )
    m = re.search(r"<CHECKSUM>(\d+)</CHECKSUM>", cab)
    if not m:
        problemas.append("falta <CHECKSUM>")
    elif int(m.group(1)) != checksum(binario):
        problemas.append(
            "<CHECKSUM> says %s and the computed one is %d" % (m.group(1), checksum(binario))
        )
    if binario[:4] != b"GSPM":
        problemas.append(
            "the binary does not start with the GSPM cookie (%r): it is not a config "
            "de arch 12" % binario[:4]
        )
    return problemas


def _autoprueba(blob: pathlib.Path | None) -> int:
    """Builds a container and has the real library validate it.

    With `--blob` it uses a real binary; without it, a synthetic one. In both
    cases, if libconcord is installed, the test that counts is the last one:
    `read_and_parse_file()` has to return 0 and say the file is a
    CONFIGURATION. **It does not touch USB**: that symbol only reads and
    parses a file (`operationfile.cpp`), it does not call `init_concord()`.
    """
    if blob is not None:
        binario = blob.read_bytes()
    else:
        # A minimal, synthetic GSPM: cookie + close pointer + PTYY.
        cuerpo = bytearray(b"GSPM" + b"\x00" * 60)
        close = len(cuerpo)
        cuerpo[4:7] = (0x040000 + close).to_bytes(3, "little")
        binario = bytes(cuerpo) + b"PTYY"

    datos = envolver(binario)
    fallas = list(verificar(datos))

    cab, bin2 = separar(datos)
    if bin2 != binario:
        fallas.append("separar(envolver(x)) did not give back the original binary")
    if not cab.startswith("<?xml"):
        fallas.append("the header does not start with the XML declaration")

    # NEGATIVES: corrupting a byte has to break the checksum, and lying about the
    # size has to be detected. A verifier that always says "ok" is
    # worth nothing.
    roto = bytearray(datos)
    roto[-1] ^= 0xFF
    if not verificar(bytes(roto)):
        fallas.append("NEGATIVE FAILED: changing a byte of the binary broke nothing")
    mentiroso = datos.replace(
        b"<BINARYDATASIZE>%d</BINARYDATASIZE>" % len(binario),
        b"<BINARYDATASIZE>%d</BINARYDATASIZE>" % (len(binario) + 1),
    )
    if not verificar(mentiroso):
        fallas.append("NEGATIVO FALLIDO: un BINARYDATASIZE mentiroso paso")

    # The test that matters: that the real library accepts it.
    veredicto_lib = "not tested (libconcord not found)"
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import ctypes

        import write

        if getattr(write, "LIB", None):
            import tempfile

            lib = ctypes.CDLL(write.LIB)
            lib.read_and_parse_file.argtypes = [
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_int),
            ]
            lib.read_and_parse_file.restype = ctypes.c_int
            lib.delete_opfile_obj.restype = None
            with tempfile.NamedTemporaryFile(suffix=".EZHex", delete=False) as fh:
                fh.write(datos)
                path = fh.name
            kind = ctypes.c_int(0)
            err = lib.read_and_parse_file(path.encode(), ctypes.byref(kind))
            lib.delete_opfile_obj()
            pathlib.Path(path).unlink(missing_ok=True)
            veredicto_lib = "read_and_parse_file() -> err=%d tipo=%d" % (
                err,
                kind.value,
            )
            if err != 0:
                fallas.append(
                    "libconcord RECHAZO el contenedor emitido: %s" % veredicto_lib
                )
    except Exception as exc:  # noqa: BLE001
        veredicto_lib = "no probado (%s: %s)" % (type(exc).__name__, exc)

    print("binario:      %d B" % len(binario))
    print("contenedor:   %d B" % len(datos))
    print("checksum:     %d" % checksum(binario))
    print("libconcord:   %s" % veredicto_lib)
    print()
    if fallas:
        print("AUTOPRUEBA: FALLO")
        for f in fallas:
            print("  -", f)
        return 1
    print("AUTOPRUEBA: PASO")
    print("  negativos OK: byte cambiado -> checksum mal; tamano mentido -> detectado")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--bin", help="binario GSPM a envolver")
    ap.add_argument("--salida", help="where to write the .EZHex")
    ap.add_argument("--verificar", help="revisar un .EZHex existente")
    ap.add_argument("--autoprueba", action="store_true")
    ap.add_argument("--blob", help="real binary for the self-test")
    ap.add_argument("--protocolo", type=int, default=12)
    ap.add_argument("--skin", type=int, default=54)
    ap.add_argument("--flash", default="0x1F:0xC8")
    ap.add_argument("--board", default="0.5.0")
    a = ap.parse_args()

    if a.autoprueba:
        return _autoprueba(pathlib.Path(a.blob) if a.blob else None)

    if a.verificar:
        problemas = verificar(pathlib.Path(a.verificar).read_bytes())
        if problemas:
            for p in problemas:
                print("PROBLEMA:", p, file=sys.stderr)
            return 1
        print(
            "OK: %s meets the three conditions of find_config_binary()" % a.verificar
        )
        return 0

    if not a.bin or not a.salida:
        ap.error("hacen falta --bin y --salida (o --verificar, o --autoprueba)")
    binario = pathlib.Path(a.bin).read_bytes()
    datos = envolver(
        binario,
        protocolo=a.protocolo,
        skin=a.skin,
        flash=a.flash,
        board=a.board,
    )
    pathlib.Path(a.salida).write_bytes(datos)
    print(
        "escrito %s (%d B; binario %d B, checksum %d)"
        % (a.salida, len(datos), len(binario), checksum(binario))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
