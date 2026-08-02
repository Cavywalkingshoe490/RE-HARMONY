#!/usr/bin/env python3
"""What the app's **Activities** screen needs to know, as JSON.

READS ONLY. Doesn't touch USB, doesn't import `write.py` to write,
doesn't write blobs. Same pattern as `list_devices.py` for the devices screen:
the app doesn't reimplement the model, it runs this as a subprocess and
paints whatever comes out.

## What it resolves, and with what check

**Per-activity attribution** -- which device each one touches -- comes
from `activities.attribution()`, i.e. from the data chain

    section[10][ordinal] -> ENTER hook -> `tag>=0x80` slots = SET
    -> record `[14][property]` -> the transition whose `end`/to is the value
    -> `{cmd_id,0x7D}` -> `k1 = cmd_id >> 8`

and not from the old heuristic ("the screens that mix k1"). That chain's
check is in `activities.py`'s docstring: 5/0/0/7 contingency over the 12
records with transitions (one-tail Fisher 1/792), `index == id` mapping
9/9 against the best competing shift's 5/9, and a cross-check with an
INDEPENDENT ORACLE (each command's decoded IR waveform looked up in the
Hub's `DeviceList.json`) that gives 5/5 the same device the property's
name says.

**Command names** (`InputHdmi1`, `PowerOn`...) come from that same
oracle: `(k1,k2)` is resolved through section `[5]`, the 25 B record's
waveform is read, decoded with `irscan`, and looked up by
`(protocol, payload)` in the Hub's JSON. If the JSON isn't there, the
field stays `null` and the UI shows the number -- it never invents a name.

Usage:
    python3 screen_activities.py <blob.bin> [--json output.json]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import activities  # noqa: E402
import add_device as D  # noqa: E402
import glyphs  # noqa: E402
import relocate  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: A Hub DeviceList: the INDEPENDENT oracle that names commands by their IR
#: waveform (not by position, which doesn't match -- see `keys_map.py`'s
#: negative check: 1 hit against 75 misses). Per-user data, resolved and not
#: hardcoded; see `glyphs.devicelist_path()`. Any that don't exist are
#: skipped (`_hub_by_wave` already returns {} for an unreadable file).
CANDIDATOS_HUB = [
    glyphs.devicelist_path(),
    *sorted(ROOT.glob("account_export/output/*/resources/DeviceList.json")),
]


# ------------------------------------------------------------ IR oracle ---


def _hub_by_wave(path: pathlib.Path) -> dict[tuple[str, int], list[tuple[str, str]]]:
    """`{(protocol, payload): [(device, command)]}` from the Hub's JSON."""
    try:
        dl = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return {}
    out: dict[tuple[str, int], list[tuple[str, str]]] = {}
    for e in dl.get("DevicesWithFeatures") or []:
        d = e.get("Device") or {}
        etq = d.get("Label") or d.get("Name") or "?"
        for c in e.get("Commands") or []:
            kc = c.get("KeyCode") or ""
            m = re.search(r"0x([0-9A-Fa-f]+)", kc)
            if not m or ":" not in kc:
                continue
            out.setdefault((kc.split(":")[1], int(m.group(1), 16)), []).append(
                (etq, c.get("Name") or "?")
            )
    return out


def _wave_of(b: bytes, cmd_id: int):
    """The IR waveform for a `cmd_id`, through section [5]. `None` if it
    doesn't resolve."""
    try:
        import irscan  # noqa: PLC0415

        reg, reason = D.resolve_section5(b, cmd_id)
        if reg is None:
            return None
        # measured layout of the 25 B record (see `commands.py`):
        #   +0 01 | +5 u24 period | +8 u24 half | +11 01 +12 self-pointer
        #   +15 01 | +16 u24 -> WAVEFORM
        p = D.u24(b, reg + 16) - D.BASE
        if not 0 <= p < len(b) - 1:
            return None
        if (b[p] | (b[p + 1] << 8)) != irscan.LEAD_IN:
            return None
        return irscan.read_waveform(b, p)
    except Exception:  # noqa: BLE001
        return None


def command_namer(b: bytes):
    """`f(cmd_id) -> {'device','command','protocolo','carga'} | None`."""
    hub = {}
    source = None
    for c in CANDIDATOS_HUB:
        hub = _hub_by_wave(c)
        if hub:
            source = str(c)
            break
    cache: dict[int, dict | None] = {}

    def nombrar(cmd_id: int):
        if cmd_id in cache:
            return cache[cmd_id]
        res = None
        w = _wave_of(b, cmd_id)
        if w:
            try:
                import irscan  # noqa: PLC0415

                r = irscan.decode(w)
            except Exception:  # noqa: BLE001
                r = None
            if r:
                proto, _bits, value = r
                hit = hub.get((proto, value))
                res = {
                    "protocolo": proto,
                    "carga": value,
                    "device": hit[0][0] if hit else None,
                    "command": hit[0][1] if hit else None,
                }
        cache[cmd_id] = res
        return res

    nombrar.source = source  # type: ignore[attr-defined]
    return nombrar


# ------------------------------------------------------- the output ---


def _devices(b: bytes) -> list[dict]:
    """`[{k1, name, commands, de_fabrica}]`, reusing `list_devices.py`."""
    import list_devices  # noqa: PLC0415

    list_devices.set_t6(b)
    dest11 = relocate.table(b, relocate.sections(b)[11][0])
    decode, _warning = list_devices.make_decoder(b, list_devices.DEFAULT_HUB)
    zones19 = D.read_section19(b)
    out = []
    vistos = set()
    for menu in [o["ordinal"] for o in D.menu_objects(b)]:
        for row in list_devices.menu_rows(b, menu, decode, dest11, zones19) or []:
            if row.get("k1") is None or row["k1"] in vistos:
                continue
            vistos.add(row["k1"])
            out.append(
                {
                    "k1": row["k1"],
                    "name": row.get("name"),
                    "de_fabrica": row["k1"] < list_devices.K1_DE_FABRICA,
                }
            )
    devs5 = D.read_section5(b, D.u24(b, D.MAESTRO_S5) - D.BASE)
    for d in out:
        d["commands"] = devs5[d["k1"]]["n"] if d["k1"] < len(devs5) else None
    for k1 in range(len(devs5)):
        if k1 not in vistos:
            out.append(
                {
                    "k1": k1,
                    "name": None,
                    "de_fabrica": k1 < list_devices.K1_DE_FABRICA,
                    "commands": devs5[k1]["n"],
                    "huerfano": True,
                }
            )
    return sorted(out, key=lambda d: d["k1"])


def properties_by_device(b: bytes, dest11, nombrar) -> dict[int, list[dict]]:
    """`{k1: [{id, name, limite, values:[{value, cmd_id, command}]}]}`.

    These are the levers an activity HAS to drive that device: the `[0]`
    properties whose `[14]` record reaches commands for that `k1`. A
    device with none can't be brought into an activity through this path
    -- that's exactly what happens to the added ones (Philips, LG).
    """
    regs = activities.engine_records(b)
    nombres = activities.named_properties(b)
    out: dict[int, list[dict]] = {}
    for pid in range(len(regs)):
        ks = activities.property_k1(b, dest11, pid, regs)
        if len(ks) != 1:
            continue  # 5/5 of the real ones give a single k1; anything with 2 is left alone
        k1 = next(iter(ks))
        values = []
        for tr in activities.transitions_of(b, pid, regs):
            if tr["tag"] != activities.TAG_OBJETO:
                continue
            cmds = activities._cmds_from_object(b, dest11, tr["atomo"])
            cid = cmds[0] if cmds else None
            info = nombrar(cid) if cid is not None else None
            values.append(
                {
                    "value": tr["end"],
                    "start": tr["start"],
                    "cmd_id": cid,
                    "command": (info or {}).get("command"),
                }
            )
        values.sort(key=lambda v: v["value"])
        out.setdefault(k1, []).append(
            {
                "id": pid,
                "name": nombres.get(pid),
                "limite": regs[pid]["value"],
                "n_transiciones": regs[pid]["cuantos"],
                "es_encendido": bool(
                    nombres.get(pid) and nombres[pid].endswith("_Power_2")
                ),
                "values": values,
            }
        )
    return out


def create_diagnostic(b: bytes, dest11) -> dict:
    """Can a NEW activity be created? What's missing, measured on this blob."""
    missing = []
    m = activities.activities_menu(b, dest11)
    plant = D.read_section19(b)
    zones = D.template_buttons(plant.get(m["K"], [])) if m else []
    usadas = {f["codigo"] for f in (m or {"rows": []})["rows"]}
    libres = [z for z in zones if z not in usadas]
    if m is not None and not libres:
        missing.append(
            {
                "what": "a free row in the activities menu",
                "measured": "template K=%d of screen %d has %d row zone(s) "
                "(%s) and all %d are taken"
                % (
                    m["K"],
                    m["ordinal"],
                    len(zones),
                    ", ".join(hex(z) for z in zones),
                    len(usadas),
                ),
                "salida_conocida": "the same one already grabbed and "
                "running in the Devices menu: move the trailer to N=2 "
                "(second sheet) and remove strip codes 0xAE/0xAF from the "
                "header. NOT tested on the activities menu.",
            }
        )
    ctxs = activities.keyboard_contexts(b)
    con_enter = [
        i
        for i, o in enumerate(ctxs)
        if activities.COD_ENTER in activities.context_hooks(b, o)
    ]
    missing.append(
        {
            "what": "a slot on the control to remember the new activity's buttons",
            "measured": "section [10] declares %d context(s) and %d have an "
            "ENTER hook (%s). Nothing measured says the firmware accepts "
            "an 11th one: the count byte can be raised, but the consumer "
            "of that table was not disassembled."
            % (len(ctxs), len(con_enter), con_enter),
            "salida_conocida": None,
        }
    )
    props = properties_by_device(b, dest11, lambda _c: None)
    devs5 = D.read_section5(b, D.u24(b, D.MAESTRO_S5) - D.BASE)
    sin_palanca = [k1 for k1 in range(len(devs5)) if k1 not in props]
    if sin_palanca:
        missing.append(
            {
                "what": "a way for the control to remember the added devices' state",
                "measured": "k1 %s have no property in section [0] nor a "
                "record in [14] reaching their commands, so an activity "
                "can't power them on or change their input through this "
                "path" % sin_palanca,
                "salida_conocida": "a 'macro' activity that fires the "
                "commands directly from its ENTER, with no state "
                "tracking. NOT tested.",
            }
        )
    return {
        "se_puede": False,
        "missing": missing,
        "row_zones": [hex(z) for z in zones],
        "free_zones": [hex(z) for z in libres],
    }


def read(path: str) -> dict:
    b = pathlib.Path(path).read_bytes()
    D.T6 = D.u24(b, D.MAESTRO_T6) - D.BASE
    dest11 = relocate.table(b, relocate.sections(b)[11][0])
    nombrar = command_namer(b)

    import list_devices  # noqa: PLC0415

    list_devices.set_t6(b)
    decode, warning = list_devices.make_decoder(b, list_devices.DEFAULT_HUB)

    def dec(ptr, inline=None):
        if inline is not None:
            table = list_devices.glyphs.extender(b, set())[0]
            return "".join(table.get(c, "?") for c in inline), True
        return decode(ptr)

    inf = activities.report(b, -1, dec)
    atrib = activities.attribution(b, dest11)
    devs = _devices(b)
    by_k1 = {d["k1"]: d for d in devs}
    props = properties_by_device(b, dest11, nombrar)
    m = activities.activities_menu(b, dest11)
    en_menu = {f["act"] for f in (m or {"rows": []})["rows"]}

    salida = []
    for ordinal, a in sorted(atrib.items()):
        used = []
        for k1 in a["k1"]:
            palancas = []
            for s in a["sets"]:
                if k1 not in s["k1"] or s["gancho"] != "enter":
                    continue
                cid = s["cmd_id"]
                info = nombrar(cid) if cid is not None else None
                palancas.append(
                    {
                        "id": s["id"],
                        "propiedad": s["propiedad"],
                        "value": s["value"],
                        "limite": s["limite"],
                        "cmd_id": cid,
                        "command": (info or {}).get("command"),
                    }
                )
            used.append(
                {
                    "k1": k1,
                    "name": (by_k1.get(k1) or {}).get("name") or "device %d" % k1,
                    "palancas": palancas,
                }
            )
        salida.append(
            {
                "ordinal": ordinal,
                "name": activities.activity_name(inf, ordinal),
                "name_in_blob": ordinal in (inf.get("nombres") or {}),
                "en_menu": ordinal in en_menu,
                "determinado": a["determinado"],
                "devices": used,
                "sets": a["sets"],
                "ganchos": a["ganchos"],
            }
        )

    return {
        "blob": str(path),
        "tamano": len(b),
        "activities": salida,
        "devices": devs,
        "levers_by_device": {str(k): v for k, v in props.items()},
        "menu": None
        if m is None
        else {"ordinal": m["ordinal"], "K": m["K"], "rows": m["rows"]},
        "create": create_diagnostic(b, dest11),
        "gold_check": activities.gold_check(b, dest11),
        "oraculo_ir": getattr(nombrar, "source", None),
        "glyph_warning": warning,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blob")
    ap.add_argument("--json", dest="target")
    a = ap.parse_args()
    d = read(a.blob)
    if a.target:
        pathlib.Path(a.target).write_text(json.dumps(d, indent=1, ensure_ascii=False))
        print("written %s" % a.target)
    else:
        print(json.dumps(d, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
