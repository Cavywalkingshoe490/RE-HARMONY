#!/usr/bin/env python3
"""Complete key map of the Harmony One: what each one does, today, in a given blob.

Mandatory starting point for this task: `reubicar.chain(b)` (button ->
command in section `[9]`). Completed with the two pieces `cadena()`
doesn't cover:

    (a) tabla[6]'s HEADERS -- each screen (`table[6][ordinal]` -> TRAILER
        -> N sub-screens -> SLOT -> KEY register) has its own record
        `<u8 count><count x {code u8, operand u16, class u8}>`, the SAME
        shape as a `[9]` page but addressed separately. This is the
        finer-grained, more complete model: it covers all 156+ screens
        (menus AND device command screens), while `cadena()` only sees
        what falls inside the LIVE section `[9]`'s byte range. CORRECTED:
        an earlier version of this paragraph said `cadena()` sees
        "systematically FEWER (198 vs. the 173+...)", a sentence that
        contradicts itself -- 198 is MORE than 173. The real count is the
        other way around and the cause is different: `reubicar.relocate()`
        moves section `[9]` to the end and fixes the master index, but NOT
        `table[6]`'s pointers, and since it doesn't erase the old bytes
        those pointers still resolve to the OLD copy. In the grabbed blob
        (`config_empaquetada.bin`) 212 of tabla[6]'s 226 keyregs
        point at `0x291eb..0x29ca6` -- outside the live `[9]`. In other
        words: the two walks see DIFFERENT COPIES of the same pages, and
        the one the firmware reads is tabla[6]'s. See
        `config_work/keys_map.py`, the module that draws the
        operational consequence from this.

    (b) section `[19]`'s 33 TEMPLATES
        (`config_work/pantalla_dispositivo.read_section19`), to know which
        codes are a touch zone on SOME screen: `codigo = tag | 0x80`,
        measured over all 33 complete templates (not a sample).

## The two key classes, and why they get confused

A key code (the same byte, 0x00-0xFF) can originate in TWO ways the data
itself doesn't distinguish:

    - touching a screen zone (the zone lives in `[19]`, its code is
      `tag | 0x80`);
    - pressing a PHYSICAL key on the remote (the firmware, at `0x02E2F2`,
      matches the event **by code alone**, without looking at where it
      came from).

So a code that appears as a touch zone in SOME of the 33 templates is, at
minimum, touch-capable; if that same code is ALSO part of the canonical
group of 6 `relocate.py` already established as physical by order
statistics (`PHYSICAL_KEYS = b2 b3 b0 b1 b4 b5`, without a single
exception in 69 pages), **there is no way to tell, looking at the data,
whether a specific event came from touch or the physical button**: these
are honestly flagged `ambigua_fisica_y_tactil`, instead of inventing a
verdict.

## The 55-button inventory (fixed offset 0x67) -- what it is and is NOT

`<u8 55><55 x {code u8, index u16, tag u8}>` at offset 0x67 of the blob
(confirmed by direct reading, not assumed). It's tempting to read it as a
dispatch table -- **that was already tried and REFUTED in another
session** (`screen_device.py`, comment in `read_section19`: "refutes
the round-1 0x67-table reading"). Confirmed again here, just in case: the
"index" field of the 55 rows is **0, 1, 2, ..., 54** -- the row's
position, not a pointer to anything. It is not a bindings table: it's an
INVENTORY/vocabulary of the 55 key codes the firmware knows about (that
they exist as a concept), without saying what each one does.

Cross-checking those 55 codes against the 10 that DO appear as a touch
zone on some of the 33 templates (`0xAB 0xAC 0xAE 0xAF 0xB0-0xB5`), **45
codes NEVER show up as a touch zone in any known template** (43 if the two
system hooks `0x06`/`0x07` are subtracted, which aren't user buttons: 43
is what the output flags as `fisica_candidata`) -- the strongest evidence
available in this blob that they are pure physical buttons (Power,
Volume, Channel, cursors, OK, Menu, Exit, etc. would have to be among
those 45, but **which English name corresponds to each one is NOT in the
blob**: the remote's silkscreen is external to the firmware. This script
does NOT invent that association.)

Two of the 55 (`0x06`, `0x07`) are NOT user buttons: they are the
ENTER/EXIT screen hooks (ESTADO.md, section "The three causes that closed
that day", point 2 -- confirmed by the same behavior verified on the
device).

## What this script does NOT resolve (said explicitly, not guessed)

The 43 "pure physical" codes don't show up tied to any command on any of
tabla[6]'s 156+ screens nor in `cadena()`: the mechanism by which
Volume/Channel/Power/etc. fire a specific IR command **is not identified
by this work** (candidate: they're resolved in the firmware itself
against the active device's "role" (FunctionId), without going through a
bindings table of this kind -- but that is a HYPOTHESIS, not verified
here). What's delivered is the honest map of what COULD be tied to data,
plus the exact list of what was left out.

Usage:
    python3 keys.py <blob.bin> [--out mapa.json] [--offset-inventario 0x67]

Writes nothing to the device. Does not import `write.py`.

NOTE ON NAMING: `codigos_tactiles` and `clasificar_codigo` keep their
exact Spanish names -- `keys_photo.py` (`import keys as T`) calls both
by name, positionally. Everything else in this file (no other external
caller) was translated freely.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from collections import Counter

import screen_device as pd
import relocate

BASE = relocate.BASE

# -- code classification ------------------------------------------------

# Established in another session, with measured evidence (relocate.py:
# fixed order b2 b3 b0 b1 b4 b5 in 69/69 pages of 4-6 buttons, 0
# exceptions) and reused as-is, not re-derived.
FISICAS_CANONICAS = relocate.PHYSICAL_KEYS  # (0xB2,0xB3,0xB0,0xB1,0xB4,0xB5)
# The "two labels that are not physical keys" per relocate.py (comment on
# ORDEN_CANONICO). The attribution is kept as-is: it's a fact already
# established in the project, not something this script measures again.
NO_FISICAS_ESTABLECIDAS = (0xAB, 0xAC)
# The two universal paging strips for swipe/tap at the screen edges
# (pantalla_dispositivo.PAGING_STRIPS, tags 0x2E/0x2F -> AE/AF).
FRANJAS_PAGINADO = (0xAE, 0xAF)
# Screen lifecycle hooks, NOT buttons the user presses (ESTADO.md, "The
# three causes that closed that day", point 2).
GANCHOS_SISTEMA = {
    0x06: "ENTER hook (screen on-enter, turns on softkey LEDs)",
    0x07: "EXIT hook (screen on-exit, turns them off)",
}

#: `dev_id -> readable name`, OPTIONAL and EMPTY by default.
#:
#: This used to be a baked-in list of the five devices of the remote this
#: was developed on. That is the wrong shape twice over: those names are the
#: user's, not the format's, and on anybody else's remote the same `dev_id`
#: means a different device -- so a hardcoded table doesn't just leak, it
#: mislabels. `list_devices.py` is what actually resolves names, out of the blob's
#: own menu; this script is a diagnostic dump and is happy with the index.
#:
#: Fill it in at run time (`keys.DISPOSITIVOS_CONOCIDOS[0x0101] = "..."`)
#: if you want the dump annotated with your own names.
DISPOSITIVOS_CONOCIDOS: dict[int, str] = {}


def device_name(dev_id: int | None) -> str | None:
    """A readable name for a `dev_id`, or the index when none is known.

    The index is always derivable (`dev_id >> 8` is the section [5] `k1`);
    the name is not, so it is never guessed.
    """
    if dev_id is None:
        return None
    if dev_id in DISPOSITIVOS_CONOCIDOS:
        return DISPOSITIVOS_CONOCIDOS[dev_id]
    return "device index %d (run list_devices.py for its name)" % (dev_id >> 8)


def codigos_tactiles(b: bytes) -> set[int]:
    """Every code (`tag | 0x80`) that appears as a zone on SOME of section
    [19]'s 33 templates. This is the only objective criterion -- "can
    originate by touching the screen on SOME screen of this blob" -- that
    doesn't depend on naming anything by hand.
    """
    z19 = pd.read_section19(b)
    return {tag | 0x80 for zones in z19.values() for tag, *_ in zones}


def clasificar_codigo(codigo: int, tactiles: set[int]) -> str:
    if codigo in GANCHOS_SISTEMA:
        return "gancho_sistema"
    if codigo in NO_FISICAS_ESTABLECIDAS:
        return "tactil_establecida"
    if codigo in FRANJAS_PAGINADO:
        return "tactil_franja_paginado"
    es_fisica_canonica = codigo in FISICAS_CANONICAS
    es_tactil = codigo in tactiles
    if es_fisica_canonica and es_tactil:
        return "ambigua_fisica_y_tactil"
    if es_fisica_canonica:
        return "fisica_canonica"
    if es_tactil:
        return "tactil"
    return "fisica_candidata"  # never a touch zone on any of the 33 templates


# -- button -> (command|page) resolution, same path as reubicar.chain --


def _slots(b: bytes, dest: list[int], i: int | None):
    if i is None or not 0 <= i < len(dest):
        return []
    d = dest[i]
    if not 0 <= d < len(b):
        return []
    n = b[d]
    if not 0 < n < 40 or d + 1 + 3 * n > len(b):
        return []
    return [
        (int.from_bytes(b[d + 1 + 3 * j : d + 3 + 3 * j], "little"), b[d + 3 + 3 * j])
        for j in range(n)
    ]


def resolver_operando(b: bytes, dest: list[int], operando: int):
    """`operando` (tabla[11] index) -> object A -> follows the same
    two-hop pattern as `reubicar.chain()`: if A carries `{id_B, 0x7F}`, B
    carries `{cmd_id, 0x7D}` and `{dev_id, 0x7C}` (a command); if A
    carries `{ordinal, 0x7E}` directly, it's an indirect page transition
    (the next/previous sub-screen paging pattern). Returns (cmd, dev, page).
    """
    cmd = dev = page = None
    for sid, t in _slots(b, dest, operando):
        if t == 0x7F:
            for v, t2 in _slots(b, dest, sid):
                if t2 == 0x7D:
                    cmd = v
                elif t2 == 0x7C:
                    dev = v
        elif t == 0x7E:
            page = sid
    return cmd, dev, page


# -- the 55-button inventory (fixed offset, outside any section) -------


def read_button_inventory(b: bytes, off: int = 0x67) -> dict:
    n = b[off]
    entradas = []
    for k in range(n):
        p = off + 1 + 4 * k
        entradas.append(
            {
                "codigo": b[p],
                "row_index": int.from_bytes(b[p + 1 : p + 3], "little"),
                "tag": b[p + 3],
            }
        )
    secuencial = all(e["row_index"] == k for k, e in enumerate(entradas))
    return {
        "offset": off,
        "n": n,
        "entradas": entradas,
        "index_is_sequential": secuencial,
    }


# -- the main walk: every screen, every sub-screen, every key ---


def map_screens(b: bytes) -> dict:
    sec = relocate.sections(b)
    dest = relocate.table(b, sec[11][0])
    tactiles = codigos_tactiles(b)
    t6 = pd.read_table6(b)
    z19 = pd.read_section19(b)

    screens = []
    stats_codigo: dict[int, Counter] = {}
    dev_counter: Counter = Counter()
    n_cmd = n_pag = n_sin = n_keys = 0

    for ordinal, addr in enumerate(t6):
        off = addr - BASE
        if not 0 <= off < len(b):
            screens.append({"ordinal": ordinal, "error": "trailer out of range"})
            continue
        trailer = pd.read_trailer(b, off)
        subpantallas = []
        for si, s in enumerate(trailer["slots"]):
            if not 0 <= s < len(b):
                subpantallas.append({"index": si, "error": "slot out of range"})
                continue
            slot = pd.read_slot(b, s)
            K = slot["K"]
            template_zones = z19.get(K, [])
            zones_by_code = {
                (tag | 0x80): {"x0": x0, "w": w, "y0": y0, "h": h}
                for tag, x0, w, y0, h in template_zones
            }
            toff = slot["keys"]
            reg = []
            if 0 <= toff < len(b):
                try:
                    reg = pd.read_record(b, toff)
                except Exception:
                    reg = []

            keys_out = []
            for codigo, operando, category in reg:
                n_keys += 1
                entrada = {
                    "codigo": "0x%02X" % codigo,
                    "category": "0x%02X" % category,
                    "operando": operando,
                    "screen_zone": zones_by_code.get(codigo),
                    "key_classification": clasificar_codigo(codigo, tactiles),
                }
                if category == 0x7E:
                    entrada["kind"] = "transicion_pagina"
                    entrada["target_page"] = operando
                    n_pag += 1
                elif category == 0x7F:
                    cmd, dev, page = resolver_operando(b, dest, operando)
                    # CORRECTED: this used to be an if/elif and the page
                    # branch was UNREACHABLE when a key had both -- the
                    # transition was silently dropped. Measured: 36 keys
                    # per blob carry a command AND a page at the same
                    # time (e.g. screen 45, sub 0, 0xB2 -> cmd 5 / dev
                    # 0x0001 and page 137). The JSON is the UI's input:
                    # describing them halfway is describing 36 keys wrong.
                    has_command = cmd is not None and dev is not None
                    if has_command:
                        entrada["cmd_id"] = cmd
                        entrada["dev_id"] = "0x%04X" % dev
                        entrada["device"] = device_name(dev)
                        dev_counter[dev] += 1
                    if page is not None:
                        entrada["target_page"] = page
                    if has_command and page is not None:
                        entrada["kind"] = "comando_y_transicion"
                        n_cmd += 1
                        n_pag += 1
                    elif has_command:
                        entrada["kind"] = "command"
                        n_cmd += 1
                    elif page is not None:
                        entrada["kind"] = "transicion_pagina"
                        n_pag += 1
                    else:
                        entrada["kind"] = "sin_resolver"
                        entrada["reason"] = (
                            "class 0x7F but the object resolves to neither "
                            "a command nor a page"
                        )
                        n_sin += 1
                else:
                    entrada["kind"] = "sin_resolver"
                    entrada["reason"] = (
                        "class %#04x: unidentified namespace "
                        "(see ESTADO.md, 'What was left open in the map')" % category
                    )
                    n_sin += 1
                keys_out.append(entrada)
                stats_codigo.setdefault(codigo, Counter())[entrada["kind"]] += 1

            subpantallas.append(
                {
                    "index": si,
                    "K": K,
                    "keys_offset": "0x%06X" % toff if 0 <= toff < len(b) else None,
                    "n_touch_zones_in_template": len(template_zones),
                    "keys": keys_out,
                }
            )
        screens.append(
            {
                "ordinal": ordinal,
                "flag": trailer["flag"],
                "n_subpantallas": trailer["n"],
                "subpantallas": subpantallas,
            }
        )

    used_codes = {
        "0x%02X" % cod: {
            "clasificacion": clasificar_codigo(cod, tactiles),
            "by_kind": dict(c),
            "total": sum(c.values()),
        }
        for cod, c in sorted(stats_codigo.items())
    }

    return {
        "screens": screens,
        "summary": {
            "n_screens": len(t6),
            "n_keys_total": n_keys,
            "n_commands": n_cmd,
            "n_page_transitions": n_pag,
            "n_sin_resolver": n_sin,
            "commands_per_device": {
                "0x%04X" % k: {"n": v, "device": device_name(k)}
                for k, v in sorted(dev_counter.items())
            },
        },
        "used_codes": used_codes,
        "codigos_tactiles_globales": sorted("0x%02X" % c for c in tactiles),
    }


def chain_cross_check(b: bytes, mapa: dict) -> dict:
    """`reubicar.chain(b)`, as a check: how many of its (cmd,dev) pairs
    also appear among the commands this script resolved via tabla[6], and
    which codes `cadena()` uses that this script didn't see (or the other
    way around). It is NOT assumed that one includes the other -- it is
    measured.
    """
    c = relocate.chain(b)
    pares_cadena = Counter(v for v in c.values() if v[0] is not None)
    pares_propios = Counter()
    for p in mapa["screens"]:
        for sp in p.get("subpantallas", []):
            for t in sp.get("keys", []):
                if t.get("kind") == "command":
                    pares_propios[(t["cmd_id"], int(t["dev_id"], 16))] += 1

    codigos_cadena = sorted({"0x%02X" % button for (_, button) in c})

    return {
        "cadena_n_entradas": len(c),
        "chain_button_codes": codigos_cadena,
        "own_n_commands": sum(pares_propios.values()),
        "pairs_only_in_cadena": sorted(
            "cmd=%d dev=%#06x" % k for k in (set(pares_cadena) - set(pares_propios))
        ),
        "pairs_only_in_table6": sorted(
            "cmd=%d dev=%#06x" % k for k in (set(pares_propios) - set(pares_cadena))
        ),
        "nota": (
            "cadena() only walks section [9]'s byte range; this script's "
            "tabla[6] walk follows each screen's real pointer whether or "
            "not it's inside [9]. Not matching isn't an error: it's the "
            "proof that these are two different read paths over the same "
            "model."
        ),
    }


def clasificar_inventario(inv: dict, tactiles: set[int]) -> dict:
    rows = []
    for e in inv["entradas"]:
        cod = e["codigo"]
        rows.append(
            {
                **e,
                "codigo_hex": "0x%02X" % cod,
                "clasificacion": clasificar_codigo(cod, tactiles),
                "es_gancho_sistema": cod in GANCHOS_SISTEMA,
            }
        )
    counts = Counter(f["clasificacion"] for f in rows)
    return {
        "offset": "0x%02X" % inv["offset"],
        "n": inv["n"],
        "index_is_sequential": inv["index_is_sequential"],
        "nota": (
            "the 'indice_fila' field is the row's position (0..n-1), not "
            "a pointer: this is NOT a dispatch table, already refuted as "
            "one in another session (see the module docstring). It's the "
            "vocabulary of key codes the firmware recognizes."
        ),
        "rows": rows,
        "count_by_classification": dict(counts),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("--out", help="where to write the full JSON")
    ap.add_argument(
        "--offset-inventario",
        type=lambda x: int(x, 0),
        default=0x67,
        help="offset of the 55-button record (default 0x67)",
    )
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()

    mapa = map_screens(b)
    tactiles = {int(c, 16) for c in mapa["codigos_tactiles_globales"]}
    inv = read_button_inventory(b, a.offset_inventario)
    mapa["inventory_55_buttons"] = clasificar_inventario(inv, tactiles)
    mapa["chain_cross_check"] = chain_cross_check(b, mapa)
    mapa["blob"] = str(a.blob)

    r = mapa["summary"]
    print("blob: %s (%d B)" % (a.blob, len(b)))
    print(
        "screens: %d   total keys: %d   commands: %d   "
        "page transitions: %d   unresolved: %d"
        % (
            r["n_screens"],
            r["n_keys_total"],
            r["n_commands"],
            r["n_page_transitions"],
            r["n_sin_resolver"],
        )
    )
    print("commands per device:")
    for dev, info in r["commands_per_device"].items():
        print("  %s  %-45s %d" % (dev, info["device"], info["n"]))

    print("\ncodes seen on screens (tabla[6]), classified:")
    for cod, info in mapa["used_codes"].items():
        print("  %s  %-28s %s" % (cod, info["clasificacion"], info["by_kind"]))

    inv_r = mapa["inventory_55_buttons"]
    print(
        "\n55-button inventory (offset %s): sequential index=%s"
        % (inv_r["offset"], inv_r["index_is_sequential"])
    )
    print("  classification:", inv_r["count_by_classification"])

    cc = mapa["chain_cross_check"]
    print(
        "\nreubicar.chain() cross-check: %d entries, codes %s"
        % (cc["cadena_n_entradas"], cc["chain_button_codes"])
    )
    print(
        "  own commands (tabla[6]): %d   only-in-cadena: %d   only-in-tabla6: %d"
        % (
            cc["own_n_commands"],
            len(cc["pairs_only_in_cadena"]),
            len(cc["pairs_only_in_table6"]),
        )
    )

    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(mapa, indent=2, ensure_ascii=False))
        print("\nwritten %s" % a.out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
