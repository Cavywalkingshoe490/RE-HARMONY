#!/usr/bin/env python3
"""Second page of the Devices menu: moves the 4th device to a screen of its own.

## The problem

`fourth_device.py` puts the 4th device into the menu object and the remote draws it,
but the row is 50 px tall (the height of the large icon) and three fit in 220 px of
screen. The fourth one ends up below the cut.

## Why no new page ever worked

**Every screen is two halves with the same ordinal**: the button layout in section
`[9]` and the visual object in section `[6]`'s table. The proxy extended `[9]` from
156 to 162 and left the table at 156, so the 6 new pages existed with nothing to
draw them.

    original blob   tabla[6]=156  paginas[9]=156   they match
    proxy blob      tabla[6]=156  paginas[9]=162   6 orphans

Those 6 orphans are exactly the ones the proxy added.

## STATUS: INCOMPLETE -- DO NOT WRITE WHAT IT PRODUCES

Of the four steps below **only 2 and 3 are done**. Missing:

- **step 1**: the page is built in `s9` and never written. Adding a page to
  `[9]` forces relocating the whole section, which is `relocate.py`'s route
  (198/198 on the null check), not a loose append.
- **step 4**: the `{ordinal, 0x7E}` transition needs a new slot in the object
  table `[11]`, that is, relocating `[10]` and `[11]` too.

That is: the relocation of `[9][10][11]` has to be composed with that of `[6]`'s
table in a single pass. Each one on its own is verified; together not yet.

Meanwhile the remote has the 4 devices on a single screen and it **works**.
Writing this output as it stands leaves an ordinal pointing at a page that does not
exist.

## What this does

1. Adds a **page** in `[9]` by cloning the layout of the Devices menu.
2. Adds a **visual object** to `[6]`'s table, cloning the menu object and
   leaving it a single device entry: the new one.
3. **Extends `[6]`'s table** so that it covers the new ordinal. The table cannot
   grow in place without shifting the blob, so it is copied to the end and its
   master index entry (`0x0c + 4*6`) is repointed.
4. Wires the transition: a free key of the Devices menu points at the new ordinal
   with the `{ordinal, 0x7E}` slot, which is the verified shape of a transition
   (page 90 uses `{77, 0x7E}` and `{119, 0x7E}`).

Everything is added at the end and repointed. **Not one byte of the original body
moves.**

Usage:
    python3 second_page.py <blob-con-4-dispositivos.bin> --salida nuevo.bin
"""

from __future__ import annotations

import argparse
import pathlib

import fourth_device as C
import relocate as R

BASE = 0x040000


def pages(b: bytes) -> list[tuple[int, int]]:
    sec = R.sections(b)
    a9, z9 = sec[9]
    out, o = [], a9
    while o < z9:
        L = R._is_page(b, o, z9)
        if not L:
            o += 1
            continue
        out.append((o, L))
        o += L
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("--salida")
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    a6, n6, d6 = C.table_six(b)
    pg = pages(b)
    print("tabla[6] = %d entradas   paginas[9] = %d" % (n6, len(pg)))
    if len(pg) < n6:
        raise SystemExit("there are fewer pages than entries: the model does not add up")

    # the menu object with the most device entries, and its page
    cand = []
    for k, d in enumerate(d6):
        if not (0 <= d < len(b) - 9) or b[d] != 0x00:
            continue
        ini, fin = C.objeto_de(b, d)
        if not (0 <= ini < fin <= len(b)) or fin - ini > 4096:
            continue
        ent = C.entradas_de(b, ini, fin)
        if len(ent) >= 4:
            cand.append((k, d, ini, fin, ent))
    if not cand:
        raise SystemExit("no object has 4 entries: run fourth_device.py first")
    k0, d0, ini0, fin0, ent0 = cand[0]
    print(
        "menu elegido: tabla[6][%d]  objeto %#08x-%#08x  %d entradas"
        % (k0, ini0, fin0, len(ent0))
    )

    # the entry to move: the last one
    ult = max(ent0)
    g_ult, c_ult = C.bloques_de(b, ult)
    entrada = b[g_ult : g_ult + 6] + b[c_ult : c_ult + 6] + b[ult : ult + 6]
    print("entrada a mover: %s" % entrada.hex(" "))

    # --- 1. the visual object of the new page: the menu with ONE single entry
    remove = set()
    for o in ent0[:-1]:
        g, c = C.bloques_de(b, o)
        remove |= set(range(g, g + 6)) | set(range(c, c + 6)) | set(range(o, o + 6))
    new_obj = bytes(b[o] for o in range(ini0, fin0) if o not in remove)
    print("new object: %d B (the menu with a single entry)" % len(new_obj))

    # --- 2. the original menu is left without its last entry
    drop0 = (
        set(range(g_ult, g_ult + 6))
        | set(range(c_ult, c_ult + 6))
        | set(range(ult, ult + 6))
    )
    old_obj = bytes(b[o] for o in range(ini0, fin0) if o not in drop0)
    print(
        "menu object: %d -> %d B (back to 3 entries)"
        % (fin0 - ini0, len(old_obj))
    )

    close = C.u24(b, 4) - BASE
    out = bytearray(b[: close - 2])

    def relocate_obj(cuerpo: bytes, ini_orig: int, fin_orig: int, trailer: int) -> int:
        """Writes the object at the end, fixing its internal pointers.

        Returns the address of the new trailer. The internal pointers are
        recomputed by their **relative position inside the original object**, which
        is the only thing that is stable when fields are removed from the middle.
        """
        new_base = len(out)
        cuerpo = bytearray(cuerpo)
        # map of old offset -> new, for the ones that survived
        vivos = [o for o in range(ini_orig, fin_orig) if o not in drop_current]
        mapa = {o: new_base + i for i, o in enumerate(vivos)}
        n = 0
        for i in range(len(cuerpo) - 2):
            v = int.from_bytes(cuerpo[i : i + 3], "little") - BASE
            if v in mapa:
                cuerpo[i : i + 3] = C.p24(BASE + mapa[v])
                n += 1
        out.extend(cuerpo)
        print("   objeto -> %#08x  (%d punteros internos corregidos)" % (new_base, n))
        return mapa.get(trailer, new_base + len(cuerpo) - 9)

    drop_current = drop0
    tr_menu = relocate_obj(old_obj, ini0, fin0, d0)
    drop_current = remove
    new_trailer_off = relocate_obj(new_obj, ini0, fin0, d0)

    # --- 3. the new page in [9]: clone of the menu layout
    sec = R.sections(b)
    a9, z9 = sec[9]
    po, pl = pg[k0]
    new_page_ordinal = len(pg)
    s9 = bytes(b[a9:z9]) + bytes(b[po : po + pl])
    print(
        "new page: ordinal %d, clone of the layout of page %d"
        % (new_page_ordinal, k0)
    )

    # --- 4. the extended [6] table
    table = bytearray()
    table += (n6 + 1).to_bytes(2, "little") + b[a6 + 2 : a6 + 3]
    for i in range(n6):
        dd = d6[i]
        if i == k0:
            dd = tr_menu
        table += C.p24(BASE + dd)
    table += C.p24(BASE + new_trailer_off)
    off_table = len(out)
    out += table
    print("tabla[6]: %d -> %d entradas, reubicada a %#08x" % (n6, n6 + 1, off_table))

    # cierre
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
    dif = [i for i in range(close - 2) if b[i] != fresh[i]]
    esperados = {4, 5, 6} | set(range(0x0C + 4 * 6, 0x0C + 4 * 6 + 3))
    sobra = sorted(set(dif) - esperados)
    print("\nbytes of the old body changed: %d" % len(dif))
    print("   sin declarar: %s" % (sobra if sobra else "none"))
    if sobra:
        raise SystemExit("there are undeclared changes: nothing gets written")

    # controles
    a6b, n6b, d6b = C.table_six(fresh)
    print("\ncontrol: tabla[6] = %d entradas" % n6b)
    en_rango = sum(1 for d in d6b if 0 <= d < len(fresh))
    print("   %d/%d destinos en rango" % (en_rango, n6b))
    if en_rango != n6b:
        raise SystemExit("the new table has destinations out of range")
    for etq, t in (("menu", tr_menu), ("pagina nueva", new_trailer_off)):
        i2, f2 = C.objeto_de(fresh, t)
        e2 = C.entradas_de(fresh, i2, f2)
        print("   %-13s objeto %#08x %4dB  %d entradas" % (etq, i2, f2 - i2, len(e2)))

    print("blob: %d -> %d B" % (len(b), len(fresh)))
    if a.salida:
        pathlib.Path(a.salida).write_bytes(fresh)
        print("escrito %s" % a.salida)
        print("repunte a declarar: --repunta %#08x" % (0x0C + 4 * 6))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
