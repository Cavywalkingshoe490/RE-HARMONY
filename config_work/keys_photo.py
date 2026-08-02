#!/usr/bin/env python3
"""The model the app's "Keys" screen consumes: the drawing of the remote,
which key code each button is, what it does today, and which one can be
changed.

Joins three things that used to live apart:

  1. `app/ui/remote/keys.json` -- the 44 clickable zones over the
     drawing, with each one's key `codigo`. The same measured
     coordinates draw `remote.svg` (`config_work/draw_remote_svg.py`),
     so zone and pixel share one source.
  2. `keys_physical.mapear()` -- section `[10]`'s header's 10 KEYBOARD
     CONTEXTS: which command each code emits under each Activity.
  3. `keys_map.read()` -- the LCD's TOUCH zones, with section `[19]`'s
     real geometry, editable per screen via `table[6]`.

## THE code <-> silkscreen JOIN, the piece that was missing (and how it was measured)

`keys.py` correctly said "which English name corresponds to each code
is NOT in the blob". True that it isn't **as text**. But it CAN be
derived, without guessing and without pressing a button:

    key code                                 (row in context [10][7] or [8])
      -> cmd_id                              ({cmd_id,0x7D} slot of the object)
      -> command record offset               (`device.resolve_section5`,
                                              the firmware's exact arithmetic)
      -> +15                                 (the record that indexes [5]
                                              starts 15 B before the one
                                              `command_table.json` tabulates;
                                              measured: cmd 0 -> 0x2781+15 =
                                              0x2790, which is the table's
                                              first row)
      -> command NAME                        (`backups/command_table.json`:
                                              "VolumeUp", "Number7", "Select")

**POSITIVE CHECK, and it's a strong one**: the two configured Activity
contexts are independent -- `[7]` "TV HD" sends almost everything to the
**DVR** and `[8]` "PC" to the **Sony TV**, different devices,
different commands, different offsets. Even so: **34 codes appear in
BOTH**, and in **31 of those 34** the command NAME is identical; the 3
that differ are synonyms for the same spot on the remote (`Exit`/`Return`,
`Info`/`Display`, `Menu`/`Home`). The 2 that complete the 36-code union
(`0x8C` `Dot`, `0xA3` `PrevChannel`) are only in `[8]`, because the
DVR doesn't have those commands. A made-up map doesn't reproduce
itself twice through two different paths.

**NEGATIVE CHECK**: the codes that carry NO command in any context (`0x06`,
`0x07`, `0x2D`, `0x8D`, `0xA2`, `0xA5`, `0xA6`, `0xAD`, `0xAE`, `0xAF`,
`0xB7`) are still unnamed here -- none was invented for them. They show
up as not editable with the measured reason.

## How many of the 55 keys end up editable

    36  codes with a real command in `[10][7]` and/or `[10][8]` (the
        "rubber" ones: numbers, volume, channel, d-pad, transport, mute,
        menu/info/guide/exit). Changed with `keys_physical.apply_physical()`.
     8  LCD touch-zone codes (`0xAB 0xAC 0xB0..0xB5`), editable PER
        SCREEN with `keys_map.apply()`.
    --
    44  of 55 (the remaining 11 are listed above, with the reason).

Writes nothing. Doesn't touch the device. Does not import `write.py`.

NOTE ON NAMING: `modelo()` keeps its exact Spanish name -- `app/api.py`
imports this module directly and calls `teclas_foto.modelo(datos, hub)`,
forwarding its return dict straight to the UI as `foto=...`. Every dict
key in that return value (and in `keys_physical`/`keys_map`'s, which
feed into it) was therefore left in Spanish. The `reason`/`human_reason`
prose VALUES, however, are translated to English: `app/ui/app.js` reads
`human_reason` (falling back to `reason`) and shows it directly to the
user, and `app/api.py`'s own `TEXTO_TECLAS_NO_EDITABLES` -- already
English, already describing this exact 36+8=44/55 breakdown -- confirms
the "Keys" screen's user-facing text is meant to be English, the same
precedent established by `activities.human_sentences()`.
"""

from __future__ import annotations

import json
import pathlib

import activities as A
import delete_device
import add_device as D
import keys as T
import keys_physical as TF
import keys_map as TM
import relocate

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: `app/ui/remote/keys.json`, NOT `graphics/mando/keys.json`. The two are
#: byte-identical here, but `graphics/` is a working directory that is not
#: published: reading from there left the Keys screen with zero clickable
#: zones ("there is nothing to click on") in every clone of the repo. The
#: published copy is the one the drawing (`remote.svg`) and the UI already
#: share -- see `draw_remote_svg.py`, which reads this same path.
FOTO_JSON = ROOT / "app" / "ui" / "remote" / "keys.json"
TABLA_COMANDOS = ROOT / "backups" / "command_table.json"

#: the record that indexes section [5] starts 15 B before the one
#: `command_table.json` tabulates. Measured, not assumed: cmd_id 0 ->
#: 0x2781, and the table's first row is 0x2790 = 0x2781 + 15; cmd_id 2 ->
#: 0x2993, and there's a row at 0x29A2. Verified in
#: `command_names()`: if the offset doesn't land on most rows, it
#: returns empty instead of wrong names.
DESFASE_TABLA = 15

#: reserved codes of a keyboard context: not keys the user presses, they
#: are the screen's lifecycle hooks.
GANCHOS = {
    0x01: "hook for ENTERING the activity (not a key)",
    0x02: "hook for LEAVING the activity (not a key)",
    0x05: "REDRAW hook (not a key)",
}

#: The SAME reason, said for a person. The UI shows this one and puts the
#: technical version below, small: "class 0x1F (not 0x7F)" is correct but
#: tells nobody anything. The key is the row record's `category`/class,
#: which is what the firmware looks at to decide what to do with the key.
HUMANO_POR_CLASE = {
    0x1F: "this key ENTERS AN ACTIVITY (it doesn't send a command to a "
    "device), so there's no command to change on it.",
    0x7E: "this key OPENS ANOTHER SCREEN on the remote. It doesn't send "
    "anything over infrared, so there's no command to change.",
    0x00: "this key changes something on the remote's own screen (an "
    "internal property), it doesn't send a command to a device.",
    0x07: "this key turns the remote's own lights on or off; it doesn't "
    "send any command to a device.",
    0x72: "this key moves something in the remote's own interface (an "
    "animation or an LED), it doesn't send a command to a device.",
    0x92: "this key writes an internal property of the remote, it "
    "doesn't send a command to a device.",
}
HUMANO_FORMA_OTRA = (
    "in this activity the key doesn't point at a command: it points at "
    "an object of another class (navigation or the remote's interface). "
    "Changing it blindly would mean guessing, so the app doesn't offer it."
)
HUMANO_SIN_CONTEXTO = (
    "the remote knows this key (it's in the inventory of 55 codes), but "
    "NONE of the activities your control stores gives it a command: there's no "
    "data to change. It isn't hidden, it just doesn't exist."
)
HUMANO_PANTALLA = (
    "this is a TOUCHSCREEN zone, and those are changed PER PAGE: pick "
    "the LCD sheet above and touch it there."
)


def _humano(category: int | None, forma: str | None, has_context: bool) -> str:
    if not has_context:
        return HUMANO_SIN_CONTEXTO
    if category is not None and category in HUMANO_POR_CLASE:
        return HUMANO_POR_CLASE[category]
    if forma not in ("directo", "indirecto"):
        return HUMANO_FORMA_OTRA
    return (
        "the row exists but doesn't reach a complete (command, device) "
        "pair, so it can't be reassigned without inventing half of it."
    )


def command_names(b: bytes) -> dict[int, dict]:
    """`{cmd_id: {"command","aparato","protocolo"}}` for EVERY cmd_id that
    resolves through section [5] and has a row in `command_table.json`."""
    if not TABLA_COMANDOS.exists():
        return {}
    rows = json.loads(TABLA_COMANDOS.read_text())
    by_offset = {int(f["record"], 16): f for f in rows}
    out: dict[int, dict] = {}
    aciertos = intentos = 0
    devs = D.read_section5(b)
    for k1, d in enumerate(devs):
        for k2 in range(d.get("n", 0)):
            cmd_id = (k1 << 8) | k2
            reg, _ = D.resolve_section5(b, cmd_id)
            if reg is None:
                continue
            intentos += 1
            f = by_offset.get(reg + DESFASE_TABLA)
            if f is None:
                continue
            aciertos += 1
            nom = [c for c in (f.get("command") or []) if c and c != "Unknown"]
            out[cmd_id] = {
                "command": nom[0] if nom else None,
                "aparato": f.get("device"),
                "protocolo": f.get("protocol"),
            }
    # check: if the offset were wrong, almost no row would land.
    if intentos and aciertos * 3 < intentos:
        return {}
    return out


def context_names(b: bytes, hub=None) -> dict[int, str]:
    """What each of the 10 contexts is called, READ FROM THE BLOB.

    Activity ordinals are the same indices as the context table
    (`activities.attribution` iterates `enumerate(keyboard_contexts)`),
    so the name comes from the blob's own activities menu. The only fixed
    part is the structural one, which isn't a name but a shape
    description; and if the name can't be read, it says "context N", it
    isn't invented.
    """
    nombres: dict[int, str] = {}
    try:
        dest11 = relocate.table(b, relocate.sections(b)[11][0])
        # `erase._decodificador` satisfies the `(text, complete)`
        # contract `activity_names` expects; `keys_map`'s
        # returns `str|None` and does NOT work here (tested: it blows up
        # with a TypeError).
        dec = delete_device._decodificador(b)
        leidos = A.activity_names(b, dec, dest11)
        for k, v in (leidos or {}).items():
            if v:
                nombres[int(k)] = v
    except Exception:  # noqa: BLE001 -- without a name it's labeled "context N"
        pass
    return nombres


def _is_activity(b: bytes, contexto: int) -> bool:
    """A context is an Activity if it has an ENTER hook."""
    try:
        offs = A.keyboard_contexts(b)
        return A.COD_ENTER in A.context_hooks(b, offs[contexto])
    except Exception:  # noqa: BLE001
        return False


def modelo(b: bytes, hub=None) -> dict:
    """Everything the Keys screen needs, in a single pass."""
    foto = json.loads(FOTO_JSON.read_text())
    mapa = TF.mapear(b)
    nombres_cmd = command_names(b)
    nombres_ctx = context_names(b, hub)
    dev_nombres = TM._device_names(b, hub, len(D.read_section5(b)))
    tactiles = T.codigos_tactiles(b)

    contextos = []
    for c in mapa["contextos"]:
        i = c["contexto"]
        act = _is_activity(b, i)
        rows = []
        for f in c["rows"]:
            cod = int(f["codigo"], 16)
            g = dict(f)
            g["codigo_num"] = cod
            if cod in GANCHOS:
                g["editable"] = False
                g["reason"] = GANCHOS[cod]
                g["human_reason"] = (
                    "this isn't a key you press: it's an internal screen "
                    "hook (enter / leave / redraw)."
                )
            elif not g["editable"]:
                g["human_reason"] = _humano(int(f["category"], 16), f.get("forma"), True)
            cid = f.get("cmd_id")
            if cid is not None:
                info = nombres_cmd.get(cid, {})
                g["command_name"] = info.get("command")
                g["device_from_table"] = info.get("aparato")
                g["device_name"] = dev_nombres.get(f.get("k1"))
            rows.append(g)
        contextos.append(
            {
                "contexto": i,
                "is_activity": act,
                "name": nombres_ctx.get(i)
                or ("activity %d" % i if act else "context %d" % i),
                "n_rows": c["n_rows"],
                "n_editables": sum(1 for x in rows if x["editable"]),
                "rows": rows,
            }
        )

    # --- by CODE: the summary the UI paints over the photo --------------
    inv = [b[0x67 + 1 + 4 * i] for i in range(b[0x67])]
    by_code: dict[str, dict] = {}
    for cod in sorted(
        set(inv) | {int(f["codigo"], 16) for c in mapa["contextos"] for f in c["rows"]}
    ):
        hexc = "0x%02X" % cod
        where = []
        for c in contextos:
            for f in c["rows"]:
                if f["codigo_num"] == cod:
                    where.append(
                        {
                            "contexto": c["contexto"],
                            "name": c["name"],
                            "is_activity": c["is_activity"],
                            "editable": f["editable"],
                            "reason": f.get("reason"),
                            "human_reason": f.get("human_reason"),
                            "cmd_id": f.get("cmd_id"),
                            "k1": f.get("k1"),
                            "k2": f.get("k2"),
                            "command_name": f.get("command_name"),
                            "device_name": f.get("device_name"),
                        }
                    )
        editables = [d for d in where if d["editable"]]
        category = T.clasificar_codigo(cod, tactiles)
        humano = None
        if editables:
            reason = None
        elif cod in TM.SCREEN_CODES:
            reason = (
                "this is a TOUCHSCREEN zone: it's changed from the LCD "
                "screen, not from the photo (each page has its own)"
            )
            humano = HUMANO_PANTALLA
        elif not where:
            humano = HUMANO_SIN_CONTEXTO
            reason = (
                "none of the %d activity key maps your control stores declares it: the "
                "remote knows the code (it's in the 55-entry inventory) "
                "but there's no data tying a command to it" % len(contextos)
            )
        else:
            motivos = {d["reason"] for d in where if d.get("reason")}
            reason = "; ".join(sorted(m for m in motivos if m)) or (
                "appears in %d context(s) but none ties it to a command" % len(where)
            )
            # the reason that wins is an ACTIVITY's: it's the context the
            # person has active when they press the key. If it doesn't
            # appear in any activity, the first one there is gets used,
            # and if there's none it says it doesn't exist in the activities.
            act = [d for d in where if d["is_activity"] and d.get("human_reason")]
            resto = [d["human_reason"] for d in where if d.get("human_reason")]
            if act:
                humano = act[0]["human_reason"]
            elif resto:
                humano = (
                    "this key doesn't appear in the configured activities; "
                    "where it does appear, " + resto[0]
                )
            else:
                humano = HUMANO_SIN_CONTEXTO
        by_code[hexc] = {
            "codigo": hexc,
            "category": category,
            "en_inventario": cod in inv,
            "editable_fisica": bool(editables),
            "editable_screen": cod in TM.SCREEN_CODES,
            "reason": reason,
            "human_reason": humano,
            "contextos": where,
        }

    n_fis = sum(1 for v in by_code.values() if v["editable_fisica"])
    n_tac = len(TM.SCREEN_CODES)
    return {
        "foto": foto,
        "contextos": contextos,
        "activities": [
            {"contexto": c["contexto"], "name": c["name"]}
            for c in contextos
            if c["is_activity"] and c["n_editables"]
        ],
        "by_code": by_code,
        "command_names": {str(k): v for k, v in nombres_cmd.items()},
        "summary": {
            "inventario": len(inv),
            "editables_fisicas": n_fis,
            "editables_screen": n_tac,
            "editables_total": n_fis + n_tac,
            "no_editables": [
                v["codigo"]
                for v in by_code.values()
                if v["en_inventario"]
                and not v["editable_fisica"]
                and not v["editable_screen"]
            ],
        },
    }


def main() -> int:  # pragma: no cover -- manual inspection
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blob")
    ap.add_argument("--out")
    a = ap.parse_args()
    b = pathlib.Path(a.blob).read_bytes()
    m = modelo(b)
    r = m["summary"]
    print(
        "editable: %d of %d  (%d physical per Activity + %d LCD zones)"
        % (
            r["editables_total"],
            r["inventario"],
            r["editables_fisicas"],
            r["editables_screen"],
        )
    )
    print("not editable: %s" % ", ".join(r["no_editables"]))
    for c in m["contextos"]:
        print(
            "  [%d] %-28s %3d rows %2d editable%s"
            % (
                c["contexto"],
                c["name"],
                c["n_rows"],
                c["n_editables"],
                "  <- Activity" if c["is_activity"] else "",
            )
        )
    sin = [t["id"] for t in m["foto"]["keys"] if not t["codigo"]]
    print(
        "photo zones: %d (%d without a code: %s)"
        % (len(m["foto"]["keys"]), len(sin), ", ".join(sin))
    )
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(m, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
