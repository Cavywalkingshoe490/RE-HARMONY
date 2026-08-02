#!/usr/bin/env python3
"""What the firmware ACTUALLY REACHES when a key is pressed -- and the check
that a key reassignment landed on that record and not on a copy of it.

## Why this module exists

Grabada #7 (`Key D-pad up -> DirectionUp (LG, in PC)`) passed every check
this project had, was written, the remote booted fine -- and the key did
nothing. Every check looked at the record that had been EDITED. None looked
at the record the firmware **arrives at**, walking the pointers the way the
firmware walks them.

The two are not the same thing, and this project has already been bitten by
that twice:

  * section `[9]`'s records are reached ONLY through a slot pointer
    (`table[6] -> trailer -> slot -> keyreg`), never by ordinal. Measured on
    the #7 blob: of the **237** live keyreg fields, **0** point inside the
    `[9]` that the master index calls live -- they all still point at four
    older generations. Editing "section `[9]`, record N" by ordinal edits a
    corpse.
  * a key press is resolved by a STACK: the current screen's header
    (`table[6][n] -> trailer -> hdr`) is consulted first and, when it
    declares the code, the event is consumed there -- `0x02E2F2` matches by
    code alone, and does so even when the object behind it is empty
    (`ESTADO.md`, "las tres causas", control 156/156). Only if the screen
    does not declare the code does it reach the `[10]` keyboard context.

So "the byte changed" is worth nothing on its own. What is worth something
is: **start at the master index, walk to the record, and read what is
there.** That is all this module does, for the three sites where a key can
be bound:

    site           reached by
    -------------  ------------------------------------------------------
    contexto       master index -> [10] header -> ptr24 of the context
    dispositivo    master index -> tabla[6] -> trailer -> hdr (the screen's
                   own key register: what the remote obeys while you are on
                   that device's page)
    pantalla       master index -> tabla[6] -> trailer -> SLOT -> keyreg
                   (the touch zones; the pointer path, never the ordinal)

Nothing here writes. It does not import `write.py`, does not touch USB and
does not read `account_export/`.

NOTE ON NAMING: the dict keys are Spanish for the same reason the rest of
the key stack keeps them (`keys_map`, `keys_physical`, `teclas_foto`):
`app/api.py` forwards these dicts to the UI as they are.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import add_device as D
import relocate
import keys_map as TM

TAG_CMD, TAG_DEV, TAG_PAG, TAG_OBJ = 0x7D, 0x7C, 0x7E, 0x7F

#: the three sites, with the name `api.py`/`changes.py` use for each batch
SITIOS = ("contexto", "device", "screen")


# ============================================================== reading ===


def _base(b: bytes):
    """`(sections, table[11])`, with `D.T6` pointed at THIS blob's table.

    `TM.set_t6` reads `table[6]` out of the master index instead of the
    factory constant: after a relocation the constant points anywhere, and
    every walk in here has to start where the firmware starts.
    """
    TM.set_t6(b)
    sec = relocate.sections(b)
    return sec, relocate.table(b, sec[relocate.OBJECT_TABLE][0])


def register_rows(b: bytes, off: int) -> list[tuple[int, int, int, int]]:
    """`<count><count x {cod u8, id u16, cls u8}>` -> `[(k, cod, campo, id, cls)]`.

    `campo` is the ABSOLUTE offset of the `id` field: the two bytes a
    reassignment overwrites, and the two bytes this module goes back to read.
    Handles the long form (`<00><n><n x {flag,cod,id,cls}>`) too, the same
    way `device.read_header` does -- the structure is the same one for
    a context, a screen header and a slot's key register.
    """
    if not 0 <= off < len(b) - 1:
        raise ValueError("register at %#08x: pointer out of range" % off)
    rows = []
    if b[off] == 0:
        n = b[off + 1]
        base = off + 2
        if base + 5 * n > len(b):
            raise ValueError("register at %#08x: long form truncated" % off)
        for k in range(n):
            p = base + 5 * k
            rows.append((k, b[p + 1], p + 2, D.u16(b, p + 2), b[p + 4]))
    else:
        n = b[off]
        if off + 1 + 4 * n > len(b):
            raise ValueError("register at %#08x: short form truncated" % off)
        for k in range(n):
            p = off + 1 + 4 * k
            rows.append((k, b[p], p + 1, D.u16(b, p + 1), b[p + 3]))
    return rows


def object_commands(
    b: bytes, dest11: list[int], ident: int, profundidad: int = 0
) -> list[tuple[int, int | None]]:
    """Every `(cmd_id, dev_id)` reachable from object `ident`.

    A list and not a single value on purpose: an object with more than one
    `0x7F` slot reaches more than one command, and a check that silently
    took the first would go green on a record that fires something else.
    """
    outside: list[tuple[int, int | None]] = []
    rs = relocate._slots(b, dest11, ident)
    cmd = next((v for v, t in rs if t == TAG_CMD), None)
    dev = next((v for v, t in rs if t == TAG_DEV), None)
    if cmd is not None:
        outside.append((cmd, dev))
    if profundidad < 4:
        for v, t in rs:
            if t == TAG_OBJ:
                outside += object_commands(b, dest11, v, profundidad + 1)
    return outside


def _resolve_row(b, dest11, row, camino: list[str]) -> dict:
    """The tail shared by the three sites: row -> object -> command -> [5]."""
    _k, cod, campo, idv, category = row
    r: dict = {
        "declarado": True,
        "codigo": cod,
        "campo": campo,
        "objeto": idv,
        "category": category,
        "camino": camino + ["fila cod=%#04x campo=%#08x id=%d" % (cod, campo, idv)],
    }
    if category != TAG_OBJ:
        r["reason"] = "class %#04x is not 0x7F: the row does not jump to an object" % (
            category
        )
        return r
    if not 0 <= idv < len(dest11):
        r["reason"] = "id %d outside tabla[11] (%d entries)" % (idv, len(dest11))
        return r
    cmds = object_commands(b, dest11, idv)
    if not cmds:
        r["reason"] = "object %d does not reach any {cmd,0x7D}" % idv
        return r
    if len(cmds) > 1:
        r["reason"] = "object %d reaches %d commands: %s" % (
            idv,
            len(cmds),
            [hex(c) for c, _d in cmds],
        )
        r["commands"] = cmds
        return r
    cmd, dev = cmds[0]
    reg, reason = D.resolve_section5(b, cmd)
    r.update(
        {
            "cmd_id": cmd,
            "dev_id": dev,
            "k1": cmd >> 8,
            "k2": cmd & 0xFF,
            "registro5": reg,
            "reason5": reason,
        }
    )
    r["camino"].append(
        "objeto %d -> {cmd %#06x, dev %s} -> [5] %s"
        % (
            idv,
            cmd,
            "%#06x" % dev if dev is not None else "-",
            "%#08x" % reg if reg is not None else "NO RESUELVE",
        )
    )
    return r


def en_contexto(b: bytes, contexto: int, codigo: int) -> dict:
    """What `[10][contexto]` does with `codigo`, from the master index."""
    sec, dest11 = _base(b)
    base10 = sec[10][0]
    n = b[base10]
    if not 0 <= contexto < n:
        return {
            "declarado": False,
            "reason": "context %d does not exist (there are %d)" % (contexto, n),
        }
    off = D.u24(b, base10 + 1 + 3 * contexto) - D.BASE
    camino = [
        "indice maestro -> [10] @%#08x -> contexto %d @%#08x" % (base10, contexto, off)
    ]
    rows = register_rows(b, off)
    row = next((f for f in rows if f[1] == codigo), None)
    if row is None:
        return {
            "declarado": False,
            "camino": camino,
            "reason": "context %d does not declare %#04x (it declares %d codes)"
            % (contexto, codigo, len(rows)),
        }
    return _resolve_row(b, dest11, row, camino)


def on_screen(b: bytes, ordinal: int, codigo: int) -> dict:
    """What the SCREEN's own key register (the trailer's header) does.

    This is the device-mode keymap: the three factory device pages
    (`table[6]` 78 / 103 / 140) each declare 38 rubber codes here and every
    single one of them resolves to a command of that one device -- k1 1 / 0
    / 2, a perfect partition. It is the register the remote obeys while you
    are standing on that device's page.
    """
    sec, dest11 = _base(b)
    n = D.u16(b, D.T6)
    if not 0 <= ordinal < n:
        return {
            "declarado": False,
            "reason": "screen %d does not exist (there are %d)" % (ordinal, n),
        }
    tr = D.read_trailer(b, D.u24(b, D.T6 + 3 + 3 * ordinal) - D.BASE, max_n=200)
    if tr is None:
        return {
            "declarado": False,
            "reason": "screen %d's trailer does not parse" % ordinal,
        }
    off = tr["hdr"] - D.BASE
    camino = [
        "indice maestro -> tabla[6] @%#08x -> pantalla %d -> trailer @%#08x -> "
        "cabecera @%#08x" % (D.T6, ordinal, tr["off"], off)
    ]
    rows = register_rows(b, off)
    row = next((f for f in rows if f[1] == codigo), None)
    if row is None:
        return {
            "declarado": False,
            "camino": camino,
            "header": off,
            "trailer": tr["off"],
            "reason": "screen %d's header does not declare %#04x (it declares %s)"
            % (ordinal, codigo, ", ".join("%#04x" % f[1] for f in rows)),
        }
    r = _resolve_row(b, dest11, row, camino)
    r["header"] = off
    r["trailer"] = tr["off"]
    return r


def in_slot(b: bytes, ordinal: int, slot: int, codigo: int) -> dict:
    """The touch zone: `table[6] -> trailer -> SLOT -> keyreg`, by POINTER.

    Never by `[9]`'s ordinal. That distinction is the whole point: on the #7
    blob the live `[9]` holds 0 of the 237 keyregs actually in use.
    """
    sec, dest11 = _base(b)
    n = D.u16(b, D.T6)
    if not 0 <= ordinal < n:
        return {
            "declarado": False,
            "reason": "screen %d does not exist (there are %d)" % (ordinal, n),
        }
    tr = D.read_trailer(b, D.u24(b, D.T6 + 3 + 3 * ordinal) - D.BASE, max_n=200)
    if tr is None:
        return {
            "declarado": False,
            "reason": "screen %d's trailer does not parse" % ordinal,
        }
    if not 0 <= slot < tr["N"]:
        return {
            "declarado": False,
            "reason": "screen %d has no slot %d (it has %d)"
            % (ordinal, slot, tr["N"]),
        }
    s = D.read_slot(b, tr["slots"][slot] - D.BASE)
    if s is None:
        return {
            "declarado": False,
            "reason": "slot %d of screen %d does not parse" % (slot, ordinal),
        }
    off = s["keyreg"] - D.BASE
    a9, z9 = sec[9]
    camino = [
        "indice maestro -> tabla[6] @%#08x -> pantalla %d -> trailer @%#08x -> "
        "ranura %d @%#08x -> keyreg @%#08x (%s la [9] viva %#08x-%#08x)"
        % (
            D.T6,
            ordinal,
            tr["off"],
            slot,
            tr["slots"][slot] - D.BASE,
            off,
            "DENTRO de" if a9 <= off < z9 else "FUERA de",
            a9,
            z9,
        )
    ]
    rows = register_rows(b, off)
    row = next((f for f in rows if f[1] == codigo), None)
    if row is None:
        return {
            "declarado": False,
            "camino": camino,
            "keyreg": off,
            "reason": "screen %d slot %d does not declare %#04x"
            % (ordinal, slot, codigo),
        }
    r = _resolve_row(b, dest11, row, camino)
    r["keyreg"] = off
    r["en_seccion9_viva"] = bool(a9 <= off < z9)
    return r


def alcanzado(b: bytes, change: dict) -> dict:
    """Dispatches to the right walk according to the change's site."""
    kind = change.get("kind") or "contexto"
    codigo = int(change["codigo"])
    if kind in ("fisica", "contexto"):
        return en_contexto(b, int(change["contexto"]), codigo)
    if kind == "device":
        return on_screen(b, int(change["screen"]), codigo)
    if kind == "screen":
        return in_slot(b, int(change["screen"]), int(change["slot"]), codigo)
    return {"declarado": False, "reason": "unknown site %r" % kind}


# ========================================================= the precedence ==


def sombras(b: bytes, codigo: int) -> list[int]:
    """The screens whose HEADER declares `codigo` -- i.e. the screens where
    the press never reaches the `[10]` context, because `0x02E2F2` matched
    it first and consumed it (even against an empty object)."""
    _sec, _dest = _base(b)
    n = D.u16(b, D.T6)
    outside = []
    for k in range(n):
        tr = D.read_trailer(b, D.u24(b, D.T6 + 3 + 3 * k) - D.BASE, max_n=200)
        if tr is None:
            continue
        try:
            rows = register_rows(b, tr["hdr"] - D.BASE)
        except ValueError:
            continue
        if any(f[1] == codigo for f in rows):
            outside.append(k)
    return outside


def device_screen(b: bytes, hub=None) -> dict[int, dict]:
    """`{k1: {"name", "screen"}}` -- each device's own commands page,
    resolved through the Devices menu the same way `list_devices.py` does (it is
    the walk that closes: `reubicar.reachable_pages` is a conservative
    closure and calls even factory pages unreachable)."""
    outside: dict[int, dict] = {}
    try:
        import list_devices

        list_devices.set_t6(b)
        dest11 = relocate.table(b, relocate.sections(b)[11][0])
        # Same vocabulary `keys_map._device_names` uses. `make_decoder`
        # takes ONE path; `api.py` passes the LIST of captured hub configs, and
        # handing it a list decoded the names to "T?" / "??" -- the glyph table
        # that DOES cover them is `TM.HUB_VOCAB`, which is what that function
        # already proved out.
        decode, _warning = list_devices.make_decoder(
            b,
            str(hub) if isinstance(hub, (str, pathlib.Path)) else str(TM.HUB_VOCAB),
        )
        zones19 = D.read_section19(b)
        for row in list_devices.menu_rows(b, 74, decode, dest11, zones19) or []:
            k1 = row.get("k1")
            ordinal = row.get("screen_ordinal")
            if k1 is None or ordinal is None:
                continue
            outside[int(k1)] = {
                "name": row.get("name") or "device %d" % k1,
                "screen": int(ordinal),
            }
    except Exception:  # noqa: BLE001 -- without the menu, no site is offered
        return {}
    return outside


# ================================================================ control ==


def _said(r: dict) -> str:
    if not r.get("declarado"):
        return "NOT REACHED: %s" % r.get("reason", "?")
    if r.get("cmd_id") is None:
        return "reached but resolves to nothing: %s" % r.get("reason", "?")
    return "reaches cmd %#06x (device %d, command %d)" % (
        r["cmd_id"],
        r["k1"],
        r["k2"],
    )


def _es_apagado(change: dict) -> bool:
    """Whether the change asked for the factory's DISABLED row rather than a
    binding (`keys_physical.apply_device`'s `{apagar: True}`).

    Its success condition is the opposite of a binding's: the code has to be
    DECLARED (so the screen swallows the press) and to reach NOTHING. Being
    undeclared is the failure, not the success -- that is the case that falls
    through to the global keymap and jumps to page 146.

    Keyed on the EXPLICIT flag and never inferred from a missing `cmd_id`. An
    inferred version would turn any change that arrived without a `cmd_id` --
    a bug, a renamed key, a third site added later -- into one whose success
    condition is "reaches nothing", i.e. a check that passes by doing nothing.
    `verificar` fails such a change outright instead.
    """
    return bool(change.get("apagar"))


def verificar(fresh: bytes, detail: list[dict]) -> list[dict]:
    """Per change: what the walk finds in the NEW blob, and whether it is
    what was asked for. Nothing here reuses the offsets the writer computed
    -- everything is re-derived from the master index."""
    outside = []
    for d in detail:
        r = alcanzado(fresh, d)
        if _es_apagado(d):
            esperado = None
            ok = r.get("declarado") and r.get("category") == 0 and r.get("cmd_id") is None
        elif d.get("cmd_id") is None:
            # neither a binding nor an explicit disable: a malformed change,
            # and the one thing it must not do is come out green by default.
            esperado, ok = None, False
        else:
            esperado = int(d["cmd_id"])
            ok = (
                r.get("declarado")
                and r.get("cmd_id") == esperado
                and r.get("registro5") is not None
            )
            if ok and d.get("dev_id") is not None:
                ok = r.get("dev_id") == int(d["dev_id"])
        outside.append(
            {
                "change": d,
                "alcanzado": r,
                "ok": bool(ok),
                "said": _said(r),
                "esperado": esperado,
            }
        )
    return outside


def checks(referencia: bytes, fresh: bytes, detail: list[dict]) -> list[dict]:
    """THE check the project was missing, in three rows.

    (h) positive: walking the firmware's own path over the REGENERATED blob
        lands on a record that carries the new command.
    (i) the field the WRITER touched is the field the WALK arrives at. This
        is the dead-copy detector stated directly: "212 of 226 key records
        pointed at the dead copy" was a writer and a firmware reading two
        different addresses, and every check of the day compared the writer
        against itself. Here the writer's `campo` is compared against the
        offset the walk from the master index ends on.
    (j) load-bearing: corrupt, in a copy of the new blob, the two bytes the
        walk says it read, and the walk has to go red. That is what tells a
        check that reads the REACHED record apart from one that reads the
        record we happened to edit -- the exact failure mode that made #7
        pass five checks and do nothing.

    `referencia` is only used to report what the key used to do; no verdict
    depends on it (a key reassigned to the command it already had is a no-op,
    not a failure).
    """
    if not detail:
        return []
    res = verificar(fresh, detail)
    malos = [r for r in res if not r["ok"]]
    ch = [
        {
            "name": "(h) the record the firmware REACHES carries the new command",
            "ok": not malos,
            "detail": "; ".join(
                "%s %s -> %s"
                % (
                    r["change"].get("kind", "contexto"),
                    "%#04x" % r["change"]["codigo"],
                    r["said"],
                )
                for r in (malos or res)
            )
            or "nothing to check",
        }
    ]

    desviados = []
    for r in res:
        d = r["change"]
        llegada = (r.get("alcanzado") or {}).get("campo")
        escrito = d.get("campo")
        rebuilt = d.get("new_header")
        if rebuilt is not None:
            # The register was REBUILT (it had to grow), so it does not
            # matter whether this particular row already existed: the writer
            # copied every old row into the new register and repointed the
            # trailer, which means `campo` -- the row's address BEFORE the
            # rebuild -- is now, by construction, the dead copy. Comparing
            # against it reported "the edit landed on a copy" on a write that
            # was correct, and that verdict aborts the whole Sync (measured:
            # TV's page 103 with 0x9B, which has a row, plus 0xA6, which does
            # not; both keys reached their new command and the check went red
            # anyway). What has to hold instead is exact and stronger than a
            # window: the walk has to end on ONE OF THE FIELDS of the
            # register the writer built -- not near it, not in a copy of it.
            try:
                campos = {f[2] for f in register_rows(fresh, rebuilt)}
            except ValueError as exc:
                campos, reason = set(), str(exc)
            else:
                reason = "%d fields at %#08x" % (len(campos), rebuilt)
            if llegada not in campos:
                desviados.append(
                    (
                        "%#04x" % d["codigo"],
                        "walk ends at %s, which is not a field of the register "
                        "the writer built at %s (%s)" % (llegada, rebuilt, reason),
                    )
                )
        elif escrito is None:
            desviados.append(
                (
                    "%#04x" % d["codigo"],
                    "the writer reports neither a field it touched nor a "
                    "register it rebuilt: there is nothing to compare the "
                    "walk's arrival (%s) against" % llegada,
                )
            )
        elif llegada != escrito:
            desviados.append(
                (
                    "%#04x" % d["codigo"],
                    "the writer touched %#08x and the firmware's walk arrives at "
                    "%s: the edit landed on a copy" % (escrito, llegada),
                )
            )
    ch.append(
        {
            "name": "(i) the field written IS the field the walk arrives at",
            "ok": not desviados,
            "detail": "the %d edits and the %d walks end on the same offset"
            % (len(res), len(res))
            if not desviados
            else "writer and firmware disagree: %s" % desviados,
        }
    )

    # (j) the walk has to be reading the byte the firmware reads.
    rotos = []
    for r in res:
        campo = (r.get("alcanzado") or {}).get("campo")
        if campo is None:
            rotos.append((r["change"].get("codigo"), "no field to corrupt"))
            continue
        sucio = bytearray(fresh)
        if _es_apagado(r["change"]):
            # A disabled row carries no id to break -- `00 00` corrupted is
            # still `00 00`. What IS load-bearing there is the CODE byte (the
            # one `0x02E2F2` matches on, `campo - 1`): change it and the
            # screen stops declaring the key, which is precisely the failure
            # the disabled row exists to prevent. Corrupting the id instead
            # would leave this check green on a row that had been erased.
            sucio[campo - 1] ^= 0xFF
        else:
            sucio[campo : campo + 2] = (0xFFFE).to_bytes(2, "little")
        v = verificar(bytes(sucio), [r["change"]])[0]
        if v["ok"]:
            rotos.append(
                (
                    r["change"].get("codigo"),
                    "field %#08x corrupted and the check STILL says yes" % campo,
                )
            )
    ch.append(
        {
            "name": "(j) corrupting the reached field turns the check red",
            "ok": not rotos,
            "detail": "the %d walks read the field the firmware reads "
            "(overwritten on a copy -> the check goes red)" % len(res)
            if not rotos
            else "the check does NOT depend on that field: %s" % rotos,
        }
    )
    return ch


# =================================================================== cli ===


def main() -> int:  # pragma: no cover -- manual inspection
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("--codigo", type=lambda x: int(x, 0), required=True)
    ap.add_argument("--screen", type=int)
    ap.add_argument("--slot", type=int)
    ap.add_argument("--contexto", type=int)
    ap.add_argument("--sombras", action="store_true")
    a = ap.parse_args()
    b = pathlib.Path(a.blob).read_bytes()
    if a.contexto is not None:
        print(json.dumps(en_contexto(b, a.contexto, a.codigo), indent=1, default=str))
    if a.screen is not None and a.slot is None:
        print(json.dumps(on_screen(b, a.screen, a.codigo), indent=1, default=str))
    if a.screen is not None and a.slot is not None:
        print(
            json.dumps(
                in_slot(b, a.screen, a.slot, a.codigo), indent=1, default=str
            )
        )
    if a.sombras:
        print(
            "screens whose header consumes %#04x: %s" % (a.codigo, sombras(b, a.codigo))
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
