#!/usr/bin/env python3
"""Moves the 4th device to a screen of its own, with a key that leads to it.

Starts from a blob that already has 4 devices in a single menu (output of
`fourth_device.py`) and splits it up: the menu goes back to 3 and the fourth ends
up on a new screen, reachable from a free key on the menu.

## What was missing, and why no new page was ever seen

**Every screen is two halves with the same ordinal**: the button layout in
section `[9]` and the visual object in the table of section `[6]`. The proxy
extended `[9]` and left the table at 156, so its 6 pages existed with nothing to
draw them:

    original blob   table[6]=156  pages[9]=156   they match
    proxy blob      table[6]=156  pages[9]=162   6 orphans

This extends **both** in the same pass. That is the only difference from what had
already been tried.

## The two relocations

1. `reubicar.relocate()` moves `[9][10][11]` (198/198 in the null check) to
   add the page and the navigation object `{ordinal, 0x7E}`.
2. The `[6]` table is copied to the end extended by one entry and its master
   index entry is repointed (`0x0c + 4*6`).

Both are append-at-the-end-and-repoint. **Not one byte of the original body moves.**

Usage:
    python3 page2.py ../salida/cuarto.bin --salida ../salida/pagina2.bin
"""

from __future__ import annotations

import argparse
import pathlib

import fourth_device as C
import relocate as R

BASE = 0x040000
TECLA_NAV = 0xB1  # the key that ALREADY pages in the blob's screens (b2 and b1)


def menus_con(b: bytes, minimo: int):
    """The table[6] objects with at least `minimo` device entries."""
    _, _, d6 = C.table_six(b)
    out = []
    for k, d in enumerate(d6):
        if not (0 <= d < len(b) - 9) or b[d] != 0x00:
            continue
        ini, fin = C.objeto_de(b, d)
        if not (0 <= ini < fin <= len(b)) or fin - ini > 4096:
            continue
        ent = C.entradas_de(b, ini, fin)
        if len(ent) >= minimo:
            out.append((k, d, ini, fin, ent))
    return out


def sin_campos(b: bytes, ini: int, fin: int, remove: set[int]) -> tuple[bytes, dict]:
    """The object without the `remove` bytes, and the map old_offset -> new index."""
    vivos = [o for o in range(ini, fin) if o not in remove]
    return bytes(b[o] for o in vivos), {o: i for i, o in enumerate(vivos)}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("--salida")
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    menus = menus_con(b, 4)
    if not menus:
        raise SystemExit("no menu has 4 entries: run fourth_device.py first")
    print("menus con 4 dispositivos: %s" % [k for k, *_ in menus])

    sec = R.sections(b)
    a9, z9 = sec[9]
    a10, z10 = sec[10]
    n_pag = R.count_pages(b, a9, z9)
    ids = R.table(b, sec[11][0])
    print("paginas[9] = %d   objetos[11] = %d" % (n_pag, len(ids)))

    # --- [9]: the new page, a clone of the first menu page's layout
    pg, o = [], a9
    while o < z9:
        L = R._is_page(b, o, z9)
        if not L:
            o += 1
            continue
        pg.append((o, L))
        o += L
    k0 = menus[0][0]
    po, pl = pg[k0]
    s9 = bytearray(b[a9:z9]) + bytes(b[po : po + pl])
    new_page_ordinal = n_pag
    print(
        "new page: ordinal %d (clone of page %d's layout)"
        % (new_page_ordinal, k0)
    )

    # --- [10]: the transition is ADDED to the object of the key that already pages.
    #
    # Adding a new key (`b3`) did not work: it was written and **it was not drawn**. In the
    # 156 original pages the ones that change screen are always `b2` and `b1`, and
    # one same object can carry command and transition together:
    #
    #     pag  0   b2: cmd 436|PAG 66    b1: PAG 42
    #     pag 90   b2: cmd 895|PAG 77    b1: PAG 119
    #
    # So `b1`'s object is cloned with one extra `{ordinal, 0x7E}` slot.
    # That way what the key already did is not lost.
    s10 = bytearray(b[a10:z10])
    dest_ids = R.table(b, sec[11][0])

    # --- the key that leads to the new page, in the menu pages,
    #     **in its canonical position** (appending at the end breaks the drawing)
    offs_nuevos = []
    for k, *_ in menus:
        po, pl = pg[k]
        rel = po - a9
        campo = next(
            (j for j in range(s9[rel]) if s9[rel + 1 + 4 * j] == TECLA_NAV), None
        )
        if campo is None:
            raise SystemExit(
                "page %d does not have key %#04x: it cannot page"
                % (k, TECLA_NAV)
            )
        pos = rel + 1 + 4 * campo
        old = int.from_bytes(s9[pos + 1 : pos + 3], "little")
        ranuras = R._slots(b, dest_ids, old)
        if any(t == 0x7E for _, t in ranuras):
            # Not overwritten: that screen already has its own page chain and
            # getting in there means breaking its existing navigation.
            print(
                "   page %3d: key %#04x already pages to %d -- skipped"
                % (k, TECLA_NAV, next(v for v, t in ranuras if t == 0x7E))
            )
            continue
        cuerpo = bytearray([len(ranuras) + 1])
        for v, t in ranuras:
            cuerpo += R.slot(v, t)
        cuerpo += R.slot(new_page_ordinal, 0x7E)
        offs_nuevos.append(len(s10))
        s10 += cuerpo
        new_id = len(ids) + len(offs_nuevos) - 1
        s9[pos + 1 : pos + 3] = new_id.to_bytes(2, "little")
        print(
            "   pagina %3d: tecla %#04x  obj %d -> %d  (%s + PAG %d)"
            % (
                k,
                TECLA_NAV,
                old,
                new_id,
                " ".join("{%#06x,%#04x}" % r for r in ranuras),
                new_page_ordinal,
            )
        )

    inter = R.relocate(b, {9: bytes(s9), 10: bytes(s10)}, objetos_extra=offs_nuevos)
    print("reubicacion de [9][10][11]: %d -> %d B" % (len(b), len(inter)))

    # --- now the [6] table: the menu loses its 4th entry and the new object is born
    a6, n6, d6 = C.table_six(inter)
    menus2 = menus_con(inter, 4)
    close = C.u24(inter, 4) - BASE
    out = bytearray(inter[: close - 2])
    nuevos_trailers = {}
    new_page_trailer = None

    for k, d, ini, fin, ent in menus2:
        ult = max(ent)
        g, c2 = C.bloques_de(inter, ult)
        drop_last = (
            set(range(g, g + 6)) | set(range(c2, c2 + 6)) | set(range(ult, ult + 6))
        )
        cuerpo, mapa = sin_campos(inter, ini, fin, drop_last)
        new_base = len(out)
        cuerpo = bytearray(cuerpo)
        n = 0
        for i in range(len(cuerpo) - 2):
            v = int.from_bytes(cuerpo[i : i + 3], "little") - BASE
            if v in mapa:
                cuerpo[i : i + 3] = C.p24(BASE + new_base + mapa[v])
                n += 1
        out += cuerpo
        nuevos_trailers[k] = new_base + mapa[d]
        print(
            "   menu[%d] -> %#08x, 3 entradas (%d punteros internos)"
            % (k, new_base, n)
        )

        if new_page_trailer is None:
            drop_others = set()
            for o2 in ent[:-1]:
                g2, c3 = C.bloques_de(inter, o2)
                drop_others |= (
                    set(range(g2, g2 + 6))
                    | set(range(c3, c3 + 6))
                    | set(range(o2, o2 + 6))
                )
            cuerpo2, mapa2 = sin_campos(inter, ini, fin, drop_others)
            base2 = len(out)
            cuerpo2 = bytearray(cuerpo2)
            for i in range(len(cuerpo2) - 2):
                v = int.from_bytes(cuerpo2[i : i + 3], "little") - BASE
                if v in mapa2:
                    cuerpo2[i : i + 3] = C.p24(BASE + base2 + mapa2[v])
            out += cuerpo2
            new_page_trailer = base2 + mapa2[d]
            print("   object of the new page -> %#08x, 1 entry" % base2)

    table = bytearray((n6 + 1).to_bytes(2, "little") + inter[a6 + 2 : a6 + 3])
    for i in range(n6):
        table += C.p24(BASE + nuevos_trailers.get(i, d6[i]))
    table += C.p24(BASE + new_page_trailer)
    off_table = len(out)
    out += table
    print("tabla[6]: %d -> %d entradas en %#08x" % (n6, n6 + 1, off_table))

    if len(out) % 2:
        out += b"\x00"
    nc = len(out) + 2
    out += b"\x00\x00" + b"PTYY"
    out[4:7] = C.p24(BASE + nc)
    out[0x0C + 4 * 6 : 0x0C + 4 * 6 + 3] = C.p24(BASE + off_table)
    lo, hi = 0x21, 0x43
    for i in range(0, nc - 2, 2):
        lo ^= out[i]
        hi ^= out[i + 1]
    out[nc - 2] = lo
    out[nc - 1] = hi
    fresh = bytes(out)

    # --- controles
    print()
    dif = [i for i in range(close - 2) if inter[i] != fresh[i]]
    esperados = {4, 5, 6} | set(range(0x0C + 4 * 6, 0x0C + 4 * 6 + 3))
    sobra = sorted(set(dif) - esperados)
    print(
        "versus the intermediate: %d bytes, undeclared %s"
        % (len(dif), sobra or "none")
    )
    if sobra:
        raise SystemExit("cambios sin declarar")

    _, n6b, d6b = C.table_six(fresh)
    secn = R.sections(fresh)
    n_pag2 = R.count_pages(fresh, *secn[9])
    print(
        "tabla[6] = %d   paginas[9] = %d   %s"
        % (n6b, n_pag2, "COINCIDEN" if n6b == n_pag2 else "DIFIEREN")
    )
    if n6b != n_pag2:
        raise SystemExit("the two halves do not match")
    if sum(1 for d in d6b if 0 <= d < len(fresh)) != n6b:
        raise SystemExit("there are destinations out of range")

    glyph_tbl, _ = __import__("glyphs").extender(fresh, set())
    for k in list(nuevos_trailers) + [n6b - 1]:
        t = d6b[k]
        i2, f2 = C.objeto_de(fresh, t)
        e2 = C.entradas_de(fresh, i2, f2)
        txt = []
        for o2 in e2:
            p = C.u24(fresh, o2 + 3) - BASE
            txt.append(
                "".join(
                    glyph_tbl.get(ch, "?") for ch in fresh[p : fresh.index(b"\x00", p)]
                )
            )
        print("   tabla[6][%3d]: %d entradas -> %s" % (k, len(e2), txt))

    before, desp = R.chain(b), R.chain(fresh)
    ig = sum(1 for k, v in before.items() if desp.get(k) == v)
    print(
        "botones: %d -> %d,  %d/%d identicos" % (len(before), len(desp), ig, len(before))
    )
    if ig != len(before):
        raise SystemExit("buttons that already existed changed")

    print("blob: %d -> %d B" % (len(b), len(fresh)))
    if a.salida:
        pathlib.Path(a.salida).write_bytes(fresh)
        print("escrito %s" % a.salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
