#!/usr/bin/env python3
"""Replace an image inside a Harmony One config, without disturbing anything else.

The image bulk is one chain: each record is
<mode:1><width:u16 LE><height:u16 LE><payload>, and the next record starts where
the previous one ends. There is no table of offsets, so the chain is only
consistent as long as every record keeps its length. Change a size and
everything after it shifts, and the chain no longer lands on the closing
<u16><'PTYY'> marker.

That is what makes in-place replacement the safe operation: a mode 0 record is
raw RGB565 and its length is fixed at 5 + width*height*2, so swapping the pixels
of an image for different pixels of the same dimensions changes no offset at
all. 56 of the 71 images are mode 0.

Mode 1 records are RLE and their length depends on the content, so replacing one
means re-encoding to exactly the same byte count, which is not generally
possible. This refuses to touch them.

Every write is verified by re-walking the chain and checking it still ends on
the marker, and by confirming that only the intended byte range differs.

    list                       what is in the config
    extract <n> <out.ppm>      pull image n out
    replace <n> <in.ppm> <out.bin>   write it back into a new config

Usage:
    python3 imgpatch.py <config.bin> list
    python3 imgpatch.py <config.bin> extract 7 /tmp/img.ppm
    python3 imgpatch.py <config.bin> replace 7 /tmp/img.ppm /tmp/nueva.bin
"""

from __future__ import annotations

import argparse
import pathlib

import configcheck
import rle

START = 0x02D660
MAXW, MAXH = 176, 220


def chain(buf: bytes):
    """Walk the image chain. Returns (records, end) with records as tuples."""
    o, regs = START, []
    while o < len(buf) - 5:
        mode = buf[o]
        w = buf[o + 1] | (buf[o + 2] << 8)
        h = buf[o + 3] | (buf[o + 4] << 8)
        if mode > 1 or not (1 <= w <= MAXW and 1 <= h <= MAXH):
            break
        if mode == 0:
            nxt = o + 5 + w * h * 2
        else:
            got = rle.decode(buf, o + 5)
            if not got or got[0] != w or got[1] != h:
                break
            nxt = got[3]
        if nxt > len(buf):
            break
        regs.append((o, mode, w, h, nxt - o))
        o = nxt
    return regs, o


def closes(buf: bytes) -> bool:
    """True when the chain lands exactly on the closing PTYY marker."""
    _, end = chain(buf)
    return buf[end + 2 : end + 6] == b"PTYY" and end + 6 == len(buf)


def read_ppm(path: pathlib.Path):
    data = path.read_bytes()
    if not data.startswith(b"P6"):
        raise ValueError("a binary PPM (P6) is expected")
    parts, o = [], 2
    while len(parts) < 3:
        while o < len(data) and data[o : o + 1].isspace():
            o += 1
        if data[o : o + 1] == b"#":
            while data[o : o + 1] not in (b"\n", b""):
                o += 1
            continue
        s = o
        while o < len(data) and not data[o : o + 1].isspace():
            o += 1
        parts.append(int(data[s:o]))
    return parts[0], parts[1], data[o + 1 :]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config")
    ap.add_argument("accion", choices=["list", "extract", "replace"])
    ap.add_argument("resto", nargs="*")
    a = ap.parse_args()
    buf = bytearray(pathlib.Path(a.config).read_bytes())
    regs, end = chain(buf)

    if a.accion == "list":
        print(
            "%d images, the chain ends at %#08x, close %s"
            % (len(regs), end, "OK" if closes(buf) else "ROTO")
        )
        for i, (o, m, w, h, sz) in enumerate(regs):
            print(
                "  [%2d] %#08x  modo %d  %3d x %3d  %7d B%s"
                % (i, o, m, w, h, sz, "" if m == 0 else "   (RLE: no reemplazable)")
            )
        return 0

    n = int(a.resto[0])
    o, mode, w, h, sz = regs[n]

    if a.accion == "extract":
        out = pathlib.Path(a.resto[1])
        ppm = bytearray(b"P6\n%d %d\n255\n" % (w, h))
        for y in range(h):
            for x in range(w):
                p = o + 5 + 2 * (y * w + x)
                v = (buf[p] << 8) | buf[p + 1]  # RGB565 big endian
                r, g, b = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
                ppm += bytes(
                    ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))
                )
        out.write_bytes(ppm)
        print("imagen %d (%dx%d, modo %d) -> %s" % (n, w, h, mode, out))
        return 0

    if mode != 0:
        print(
            "image %d is RLE: its length depends on the content and it cannot be "
            "replaced without shifting everything that follows. Rejected." % n
        )
        return 1
    pw, ph, px = read_ppm(pathlib.Path(a.resto[1]))
    if (pw, ph) != (w, h):
        print(
            "different dimensions: image %d is %dx%d and the PPM is %dx%d.\n"
            "A size change shifts the whole chain and breaks the close."
            % (n, w, h, pw, ph)
        )
        return 1

    fresh = bytearray(buf)
    for y in range(h):
        for x in range(w):
            i = 3 * (y * w + x)
            r, g, b = px[i] >> 3, px[i + 1] >> 2, px[i + 2] >> 3
            v = (r << 11) | (g << 5) | b
            p = o + 5 + 2 * (y * w + x)
            fresh[p] = v >> 8
            fresh[p + 1] = v & 0xFF

    # The firmware validates an XOR-16 with seed (0x21,0x43) over the whole
    # body and compares it against the u16 that precedes PTYY. Changing
    # pixels invalidates it, and a config with the old checksum gets
    # rejected with no message: so it is recalculated here rather than
    # leaving that responsibility to the caller.
    recalculado = configcheck.arreglar(fresh)

    difs = [i for i in range(len(buf)) if buf[i] != fresh[i]]
    fin = configcheck.close(bytes(fresh))
    esperados = set(range(o + 5, o + sz)) | {fin, fin + 1}
    inside = all(i in esperados for i in difs)

    pruebas = configcheck.revisar(bytes(fresh))
    valida = all(t[1] for t in pruebas)
    ok = valida and closes(bytes(fresh)) and len(fresh) == len(buf) and inside

    print("  tamaño identico          : %s" % (len(nuevo) == len(buf)))
    print(
        "  checksum                 : %s"
        % ("recalculado" if recalculado else "sin cambios")
    )
    print(
        "  bytes changed            : %d, only image %d and the checksum: %s"
        % (len(difs), n, inside)
    )
    print("  la cadena sigue cerrando : %s" % closes(bytes(fresh)))
    for name, correct, detail in pruebas:
        print("  %-24s : %-6s %s" % (name[:24], "OK" if correct else "FALLA", detail))
    print(
        "  VEREDICTO                : %s"
        % ("OK, fit to write" if ok else "RECHAZAR")
    )
    if not ok:
        print("\n  nothing was written.")
        return 1
    pathlib.Path(a.resto[2]).write_bytes(bytes(fresh))
    print("\n  escrito %s" % a.resto[2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
