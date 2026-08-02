#!/usr/bin/env python3
"""Adds a device to the Devices menu **without shifting one byte of the blob**.

The previous attempt (`agregar_a_devices.py`) inserted 18 bytes in the middle
and shifted everything that followed. **That left the remote in a boot loop**
and it had to be recovered through safemode: of the in-range u24 that crossed
the cut, **84.145 were unclassified**, on a coverage of 4,76%.

This is the shift-free route, and it rests on a structure that took work to
find.

## The container

The device list does not float loose: it lives **at +72 of a container
record** that starts with the signature

    <tag 06><af 00 00 00><ae 00 00 00>

There are **125** in the blob, of variable length, and **all 125 have their
start pointed at by a u24** -- which means **they are not reached by
sequential walking alone**, which is what was needed to be able to move them.
The one for the first list measures 174 B.

What makes this safe is the scale: in those 174 bytes there is **a single u24
pointing inside the container itself**, and **a single external pointer**
coming in. Two pointers that can be looked at one at a time, against 84.145
that cannot.

## What it does

1. copies the container to the end of the blob, with the 4th device inserted
2. fixes the internal pointer and the external one so they point at the copy
3. leaves the old bytes where they are: **unreachable, not erased**

No existing data moves. It passes the `write.py` gate, which is the one that
rejects exactly what broke the remote.

**It writes nothing to the hardware.**

Usage:
    python3 devices_no_shift.py <blob.bin> "Philips TV" \\
            --config <hub.json> --salida nuevo.bin
"""

from __future__ import annotations

import argparse
import pathlib

import glyphs

BASE = 0x040000
FIRMA = bytes.fromhex("af000000ae000000")
# Offset of the list inside the container, measured the same in all three. It is **73**
# and not 72 because the container starts at the tag byte, which goes **before** the
# firma: `contenedores()` devuelve `i-1`.
LISTA_EN = 73
PASO_DISPOSITIVO = 18
IDX_NOMBRE = 0x3F
PASO_GRUPO = 54
GRUPOS = (0x39, 0x6F, 0xA5)


def contenedores(b: bytes) -> list[int]:
    """The offsets of the container records (the tag goes before the signature)."""
    out, o = [], 0
    while True:
        i = b.find(FIRMA, o)
        if i < 0:
            return out
        out.append(i - 1)
        o = i + 1


def con_lista(b: bytes) -> list[tuple[int, int]]:
    """[(container start, length)] of the ones that carry a device list."""
    cs = contenedores(b)
    out = []
    for k, c in enumerate(cs):
        fin = cs[k + 1] if k + 1 < len(cs) else len(b)
        p = c + LISTA_EN
        if b[p] == 0x04 and b[p + 1] == IDX_NOMBRE and b[p + 2] == GRUPOS[0]:
            out.append((c, fin - c))
    return out


def u24(b, o):
    return int.from_bytes(b[o : o + 3], "little")


def add(b: bytes, name: str, vocab: set[str]):
    table, _ = glyphs.extender(b, vocab)
    cod = glyphs.codificar(name, table)
    if cod is None:
        inv = {v: k for k, v in table.items()}
        missing = "".join(sorted({c for c in name if c not in inv}))
        raise SystemExit("cannot encode %r: %r missing" % (name, missing))
    grupo = GRUPOS[-1] + PASO_GRUPO

    objetivo = con_lista(b)
    if not objetivo:
        raise SystemExit("no container with a list was found")

    close = u24(b, 4) - BASE
    out = bytearray(b[: close - 2])

    # the name, at the end: the reference carries a physical ptr24
    off_name = len(out) + 3
    out += bytes([0x05, IDX_NOMBRE, grupo]) + cod

    informe = []
    for ini, largo in objetivo:
        old = b[ini : ini + largo]
        corte = LISTA_EN + PASO_DISPOSITIVO * len(GRUPOS)  # behind the 3rd device
        ref = bytes([0x04, IDX_NOMBRE, grupo]) + (BASE + off_name).to_bytes(
            3, "little"
        )
        carga = old[LISTA_EN + 6 : LISTA_EN + 18]  # the payload of the first device
        fresh = bytearray(old[:corte]) + ref + carga + bytearray(old[corte:])

        target = len(out)

        def reubica(d):
            """old relative offset -> absolute in the copy."""
            return BASE + target + (d if d < corte else d + PASO_DISPOSITIVO)

        # INTERNAL pointers: within 174 bytes, the coincidences expected by
        # chance are ~0.002, so **any u24 that points inside is real** and
        # can be fixed without hesitating.
        internos = 0
        for o in range(len(fresh) - 2):
            rel = o if o < corte else o - PASO_DISPOSITIVO
            if not 0 <= rel + 3 <= largo:
                continue
            v = u24(old, rel) - BASE
            if ini <= v < ini + largo:
                fresh[o : o + 3] = reubica(v - ini).to_bytes(3, "little")
                internos += 1
        out += fresh

        # ENTRY pointer: the one that points at the container's exact start. The
        # 125 containers have one, against the ~10 expected by chance: it is real.
        #
        # **The other external u24s are NOT touched.** They point into the middle of
        # the container, and there the expected coincidences are ~14 while 9 are
        # observed: **below chance**, i.e. noise. Rewriting them was what
        # made the previous version change 132 bytes of the old body and
        # the gate reject it -- rightly so.
        objetivo_ini = (BASE + ini).to_bytes(3, "little")
        ext = 0
        for o in range(close - 2):
            if out[o : o + 3] == objetivo_ini and not (ini <= o < ini + largo):
                out[o : o + 3] = reubica(0).to_bytes(3, "little")
                ext += 1
        informe.append((ini, largo, target, internos, ext))

    if len(out) % 2:
        out += b"\x00"
    nc = len(out) + 2
    out += b"\x00\x00" + b"PTYY"
    out[4:7] = (BASE + nc).to_bytes(3, "little")
    lo, hi = 0x21, 0x43
    for k in range(0, nc - 2, 2):
        lo ^= out[k]
        hi ^= out[k + 1]
    out[nc - 2] = lo
    out[nc - 1] = hi
    return bytes(out), informe, grupo, off_name


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("name")
    ap.add_argument("--config")
    ap.add_argument("--salida")
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    print("containers with a device list:")
    for ini, largo in con_lista(b):
        print("   %#08x  %d B" % (ini, largo))

    vocab = glyphs.vocabulario(a.config) if a.config else set()
    n, inf, grupo, off = add(b, a.name, vocab)
    print("\ngrupo nuevo %#04x   nombre en %#08x" % (grupo, off))
    for ini, largo, dest, ar, ex in inf:
        print(
            "   %#08x (%d B) -> copia en %#08x   punteros internos %d, externos %d"
            % (ini, largo, dest, ar, ex)
        )
    print("\nblob: %d -> %d B  (+%d)" % (len(b), len(n), len(n) - len(b)))
    if a.salida:
        pathlib.Path(a.salida).write_bytes(n)
        print("escrito %s" % a.salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
