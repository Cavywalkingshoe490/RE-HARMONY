#!/usr/bin/env python3
"""THE FACTORY TEMPLATE applied to a device: the ONLY call point from the app
into `config_work/`'s planner.

WHAT IT SOLVES
--------------
When this project adds a device, its own page (the header of its `table[6]`
trailer) ends up declaring `06 07 b7 2d` and nothing else -- the MENU
template, not the device one. Measured result: you enter the device in
Devices and no rubber key does anything, because the header of the current
screen is the one that wins (`0x02E2F2` matches by code and consumes the
event right there). The three factory pages (78 DVR, 103 Sony TV, 140 Home)
declare instead 49 rows, the same 49 and in the same order, with the WHOLE
keypad bound to a single `k1`.

This module says, for a given device, WHICH ROW GOES ON EACH KEY -- and
crosses it with what the page has today, so as not to overwrite anything the
user put there by hand.

THE CALL POINT, IN ONE SINGLE PLACE
-----------------------------------
The real planner is `config_work/`'s job (it is its data model, not the
app's). While it finishes being exposed, this module looks it up by the names
declared in `ENTRADAS_CONFIG_WORK` and, if it isn't there yet, computes the
plan here with the SAME data the Keys screen already has loaded
(`keys_map.read` + `keys_physical.map_devices`).

The difference matters and is published: every plan comes out with `origin`,
and the screen shows it under "Show details". When `config_work/` exposes the
function, this file is the ONLY one to touch -- `api.py`, `changes.py` and the
JS always call `device_plan()` / `plan_from_names()`.

WHAT THIS MODULE DOES NOT DO, ON PURPOSE
----------------------------------------
- **It doesn't write the blob.** The only one that writes is still
  `keys_physical.apply_device()`, with its checks and its
  `teclas_alcance`. Here only the `{screen, codigo, k1, k2}` list that
  function eats gets built.
- **It doesn't write OFF rows** (`<cod> 00 00 00`). The factory declares off
  the key the device doesn't have, and that is NOT cosmetic (an off row
  swallows the event; a missing row lets it fall through to the global
  keymap). But `apply_device()` today only knows how to bind a row to a
  command: it has no way to write the off row. So the keys with no command
  come out REPORTED, not written -- and the screen names them one by one.
  Nothing promised and missing.
- **It doesn't touch `0x06`, `0x07`, `0x2D`, `0xB7`, `0xAE`, `0xAF` or
  `0xA5`.** `0x06`/`0x07` are the enter/exit hooks (they turn the LEDs on
  and off), `0x2D` is the internal pager of the N sub-screens, `0xAE`/`0xAF`
  are the strips that turn the page and `0xA5` is Power/All Off. `0x2D` IS in
  the inventory of 55, so `apply_device()` would accept it as a
  bindable key: the guard is here, at the very top, in `RESERVADOS`.

THE ROLE TABLE
--------------
Measured on the three factory pages of the write #9 blob (see
`config_work/_leer_plantilla_fabrica.py`): the same physical key goes to the
same ROLE on all three, with six synonym variants (`Info`/`Display`,
`Exit`/`Return`, `Menu`/`Home`, `Back`/`PrevChannel`,
`ChannelUp`/`NextPreset`, `ChannelDown`/`PrevPreset`).

The names travel in TWO different vocabularies depending on where they come
from, and both are in `FILAS` as synonyms of the same role:

  - the **Hub** one (`DeviceList.json` from the user's hub): `Number1`,
    `Select`, `DirectionUp`...
  - the **local catalog** one (`hub-config-with-device.json`, which is what
    `keys_map.read()` publishes as `origin="device catalog"` and what the
    Keys screen dropdown shows): `1`, `OK`, `DirectionUp`...

Measured on the LG (k1=5, 63 commands) of today's remote: 32 of 34 roles
resolve; `SkipBackward` and `SkipForward` are missing -- that TV doesn't
have them.

PURE: it doesn't touch the disk on its own, it doesn't talk to the remote, it
doesn't write anything. It takes bytes and dicts, it returns dicts.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

# ==========================================================================
# THE CONTRACT WITH `config_work/`
# ==========================================================================
#: The names assumed on the other side, in order of preference. The
#: first one that exists wins. Each entry is `(modulo, funcion)` and the
#: function is ALWAYS called with the same signature:
#:
#:     f(blob: bytes, k1: int, hub=None) -> dict
#:
#: and it has to return, at minimum, `{"rows": [...]}` where each row
#: carries `codigo` and (if the device has that command) `k2`. Everything
#: else -- the cross with what the page has today, the respect for what is
#: manual, the counters and the texts -- is done here, so that the answer
#: the UI consumes has ONE single shape no matter where it comes from.
ENTRADAS_CONFIG_WORK: tuple[tuple[str, str], ...] = (
    ("keys_auto", "planificar"),
    ("keys_physical", "plan_plantilla"),
    ("factory_template", "plan"),
)

#: THE HANDOVER SWITCH, and why it is `False` today.
#:
#: `config_work/keys_auto.py` already exists and exposes `planificar(b, k1,
#: hub=...)` with the shape that was asked for -- and it does MORE: it also
#: knows how to write the OFF row (`{"screen","codigo","apagar":True}`),
#: which is what the factory does with the key this device lacks and that this
#: module cannot do.
#:
#: MEASURED today against the reference blob (`medida.bin`, sha256
#: `9c866d183927876d1de30251`), device k1=5 (the LG, 63 commands):
#:
#:   teclas_auto.planificar(b, 5, hub=<the hub-configs from disk>)
#:     -> fuente "blob + Hub DeviceList", aviso: "no command of device 5
#:        could be named: nothing gets bound"
#:     -> resumen {"ligar": 0, "apagar": 45, "respetada": 1}
#:
#:   this module, same blob, same k1
#:     -> 31 to bind, 1 already bound, 2 with no command (SkipBackward,
#:        SkipForward), verified in the resulting blob
#:
#: That is: its planner runs and its 14 checks come out green, but its
#: NAME resolution still doesn't name a single command of this device, and
#: preferring it now would bind zero keys. Since its `rows` never come
#: empty, a "the first one that exists wins" would pick it anyway -- hence the
#: switch, and not an automatic detection.
#:
#: FOR THE HANDOVER: when `planificar` names the commands, set this to
#: `True`. It is the only line to touch in the whole app.
PREFERIR_CONFIG_WORK = False

# ==========================================================================
# WHAT IS NEVER BOUND
# ==========================================================================
#: `codigo -> por what`. It is checked BEFORE anything else: neither the role
#: table nor a plan coming from `config_work/` can slip one of these in.
RESERVADOS: dict[int, str] = {
    0x06: "it is the page's ENTER hook (it turns the remote's lights on)",
    0x07: "it is the page's EXIT hook (it turns the lights back off)",
    0x2D: "it is the pager between that device's own sub-pages -- binding it "
    "breaks paging inside the device, the same way the side strips do",
    0xB7: "it is the Activities key: the three factory device pages leave it "
    "to the global keymap on purpose",
    0xAE: "it is a side strip: it is what turns the page",
    0xAF: "it is a side strip: it is what turns the page",
    0xA5: "it is Power / All Off",
}

#: The LCD zones. They are bound by slot (`keys_map`), never in the
#: page header: there the factory declares them off.
TACTILES = frozenset({0xAB, 0xAC, 0xB0, 0xB1, 0xB2, 0xB3, 0xB4, 0xB5})

# ==========================================================================
# THE TEMPLATE: 49 rows, in the exact order of pages 78 / 103 / 140
# ==========================================================================
#: `(code, what the key is called for a person, roles it accepts)`.
#: `roles = None` = the factory declares it OFF on the three pages, or it is
#: infrastructure (`RESERVADOS`), or it is touch. The first role in the tuple
#: is the name shown to the user when the device doesn't have it.
FILAS: tuple[tuple[int, str | None, tuple[str, ...] | None], ...] = (
    (0x89, "Mute", ("Mute",)),
    (0x88, "1", ("Number1", "1")),
    (0x8B, "left arrow", ("DirectionLeft", "Left")),
    (0x8A, "Info", ("Info", "Display")),
    (0x8D, None, None),  # no physical key: off on the 3 factory ones
    (0x8C, "+", ("Dot", ".")),
    (0x8F, "9", ("Number9", "9")),
    (0x06, None, None),  # RESERVADO
    (0x8E, "0", ("Number0", "0")),
    (0x07, None, None),  # RESERVADO
    (0x81, "4", ("Number4", "4")),
    (0x83, "Volume +", ("VolumeUp",)),
    (0x82, "Exit", ("Exit", "Return")),
    (0x85, "Rewind", ("Rewind",)),
    (0x84, "Volume -", ("VolumeDown",)),
    (0x87, "Record", ("Record",)),
    (0x86, "Replay", ("SkipBackward", "Replay")),
    (0x98, "3", ("Number3", "3")),
    (0x99, "5", ("Number5", "5")),
    (0x9A, None, None),  # off on the 3 factory ones (see the photo note)
    (0x9B, "up arrow", ("DirectionUp", "Up")),
    (0x9C, "OK", ("Select", "OK", "Enter")),
    (0x9D, "down arrow", ("DirectionDown", "Down")),
    (0x9E, "Play", ("Play",)),
    (0x9F, "Pause", ("Pause",)),
    (0x90, "8", ("Number8", "8")),
    (0x91, "6", ("Number6", "6")),
    (0x92, "Guide", ("Guide",)),
    (0x93, "Channel +", ("ChannelUp", "NextPreset")),
    (0x94, "Channel -", ("ChannelDown", "PrevPreset")),
    (0x95, "Fast forward", ("FastForward",)),
    (0x96, "Skip", ("SkipForward",)),
    (0x97, "Stop", ("Stop",)),
    (0xAB, None, None),  # tactil
    (0xA8, "Menu", ("Menu", "Home")),
    (0xAD, None, None),  # sin tecla fisica
    (0xAC, None, None),  # tactil
    (0xA3, "Prev channel", ("Back", "PrevChannel", "ChannelPrev")),
    (0xA1, "right arrow", ("DirectionRight", "Right")),
    (0xA0, "2", ("Number2", "2")),
    (0xA7, "7", ("Number7", "7")),
    (0xA4, None, None),  # off on the 3 factory ones
    (0x2D, None, None),  # RESERVADO
    (0xB2, None, None),
    (0xB3, None, None),
    (0xB0, None, None),
    (0xB1, None, None),
    (0xB4, None, None),
    (0xB5, None, None),
)

#: How many roles the template carries (the rubber keys the factory DOES
#: bind). It comes out of `FILAS`, not out of a hand-written number.
ROLES_TOTALES = sum(1 for _c, _t, r in FILAS if r)


def nombres_de_json(config_json, device=None) -> tuple[list, str | None]:
    """`([nombres de command EN ORDEN], error)` read from the file the device was
    added with (or is about to be added with).

    WHY IT EXISTS, instead of using the names `keys_map.read()` already
    publishes: those get matched against the JSON **by the prefix of the name
    drawn in the remote's menu** (`_list_from_json`: "LG" against "LG TV").
    That works for reading a remote that is already written, but for BINDING
    the keys of a device that was just added it is fragile in a way that shows
    up immediately: if the user calls it "Tele", the prefix doesn't match, and
    the screen that promised "32 keys bound" ends up binding zero. MEASURED
    exactly like that.

    When adding a device we already know which one it is: it is the
    `--device` passed to `add_device.py`. So the names are read from
    there, without guessing.

    The index in the list IS the `k2`: `add_device.py` emits section [5]'s
    sub-table by walking this very list in order. The caller has to check the
    LENGTH against what [5] declares before using it -- a shifted name sends
    the wrong command, which is worse than not binding.
    """
    try:
        import command_records  # noqa: PLC0415 -- soft: config_work may not be there
    except Exception as exc:  # noqa: BLE001
        return [], "command_records.py is not importable: %s" % exc
    try:
        _protos, devs = command_records.load_hub_config(str(config_json))
    except Exception as exc:  # noqa: BLE001
        return [], "could not read %s: %s" % (config_json, exc)
    chosen = None
    for d in devs:
        if device is None or command_records.device_name(d) == device:
            chosen = d
            break
    if chosen is None:
        return [], "device %r is not in that file (it has: %s)" % (
            device,
            ", ".join(command_records.device_name(d) for d in devs) or "nothing",
        )
    return [(c or {}).get("Name") for c in (chosen.get("Commands") or [])], None


def names_by_count(hub, cuantos: int) -> tuple[list, str | None]:
    """`([nombres], error)` -- the LAST resort, for the Keys screen.

    `keys_map._list_from_json` matches the device with its file by the
    PREFIX of the name drawn in the remote's menu ("LG" against "LG LG TV").
    If the user gave it another name -- "Tele" -- the prefix doesn't match and
    the device is left with no command names: the Keys screen card can't
    propose anything even though the file is sitting right there.

    Here it is looked up by COUNT, which is a fact of the blob and not of the
    name: out of all the `hub-config-*.json` files on disk, the command lists
    that measure EXACTLY what section [5] declares for that `k1`. And the same
    hard criterion `keys_map` already uses is applied: if several
    candidates are left they have to be IDENTICAL. If they are not, empty is
    returned -- a shifted name sends the wrong command, which is worse than
    not binding.
    """
    try:
        import command_records  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return [], "command_records.py is not importable: %s" % exc
    rutas = [hub] if isinstance(hub, (str, bytes)) else list(hub or [])
    cands: list[list] = []
    for r in rutas:
        try:
            _protos, devs = command_records.load_hub_config(str(r))
        except Exception:  # noqa: BLE001 -- an unreadable file is not an error
            continue
        for d in devs:
            lst = [(c or {}).get("Name") for c in (d.get("Commands") or [])]
            if len(lst) == cuantos and lst not in cands:
                cands.append(lst)
    if not cands:
        return [], "no device file on this computer lists exactly %d commands" % cuantos
    if len(cands) > 1:
        return [], (
            "%d different device files list exactly %d commands and they don't "
            "agree on the names, so there is no way to tell which is this one"
            % (len(cands), cuantos)
        )
    return cands[0], None


#: The `origin` values of `keys_map.read()` whose name is a COMMAND NAME
#: and not a label drawn on the screen. Only against these can the role
#: table be compared. See the long note in `device_plan`.
VOCABULARIO_FIABLE = frozenset({"device catalog"})


def _norm(name: Any) -> str:
    """Normalises a command name for comparison.

    Lowercase and with no spaces or underscores. Hyphens and dots are NOT
    touched: `-` and `.` are real command names (the LG has them at k2=0 and
    k2=1) and stripping them would turn them into the empty string.
    """
    return str(name or "").strip().lower().replace(" ", "").replace("_", "")


def hex2(codigo: int) -> str:
    return "0x%02X" % int(codigo)


# ==========================================================================
# THE PLAN, FROM A LIST OF NAMES (what is known BEFORE adding)
# ==========================================================================
def plan_from_names(nombres: Sequence[Any]) -> dict:
    """Which roles resolve against `nombres` -- the device's command list, IN
    ORDER (the index is the `k2` it is going to have).

    It is used at the moment of queueing an addition, when the device does not
    exist in the blob yet and therefore has no page: it is what makes it
    possible to tell the user, BEFORE syncing, how many keys will end up bound
    and which ones won't.

    `[{codigo, key, rol, k2, command}]` for the ones that resolve, and the
    list of roles that are missing.
    """
    by_name: dict[str, list[int]] = {}
    for k2, n in enumerate(nombres or []):
        by_name.setdefault(_norm(n), []).append(k2)

    ligadas: list[dict] = []
    missing: list[dict] = []
    for codigo, key, roles in FILAS:
        if not roles or codigo in RESERVADOS or codigo in TACTILES:
            continue
        chosen = None
        for rol in roles:
            k2s = by_name.get(_norm(rol))
            if k2s:
                chosen = (rol, k2s)
                break
        if chosen is None:
            missing.append({"codigo": codigo, "key": key, "rol": roles[0]})
            continue
        rol, k2s = chosen
        ligadas.append(
            {
                "codigo": codigo,
                "key": key,
                "rol": rol,
                "k2": k2s[0],
                "command": str(nombres[k2s[0]]),
                "empatados": k2s[1:],
            }
        )
    return {
        "ligadas": ligadas,
        "missing": missing,
        "roles_totales": ROLES_TOTALES,
        "n_ligadas": len(ligadas),
        "n_missing": len(missing),
    }


# ==========================================================================
# THE FULL PLAN, AGAINST THE BLOB
# ==========================================================================
def _plan_de_config_work(blob: bytes, k1: int, hub) -> tuple[dict | None, str]:
    """The plan from the other side. `(plan crudo, origin)`.

    It is only queried with `PREFERIR_CONFIG_WORK = True` -- see the long note
    where the switch is declared, with the measurement that explains why it is
    off today.
    """
    if not PREFERIR_CONFIG_WORK:
        return None, ""
    for modulo, funcion in ENTRADAS_CONFIG_WORK:
        try:
            mod = __import__(modulo)
        except Exception:  # noqa: BLE001 -- it doesn't exist yet: that is not an error
            continue
        f = getattr(mod, funcion, None)
        if f is None:
            continue
        try:
            crudo = f(blob, k1, hub=hub)
        except TypeError:
            try:
                crudo = f(blob, k1, hub)
            except Exception as exc:  # noqa: BLE001
                return None, "%s.%s failed: %s: %s" % (
                    modulo,
                    funcion,
                    type(exc).__name__,
                    exc,
                )
        except Exception as exc:  # noqa: BLE001
            return None, "%s.%s failed: %s: %s" % (
                modulo,
                funcion,
                type(exc).__name__,
                exc,
            )
        if isinstance(crudo, dict) and crudo.get("rows"):
            return crudo, "config_work:%s.%s" % (modulo, funcion)
    return None, ""


def _con_hold(device_module, blob: bytes, k1: int, k2s: Iterable[int]) -> int:
    """The `k2` the factory would pick among several homonyms.

    MEASURED (21 out of 21 hits on the duplicates that pages 78 and 103 bind):
    when a name has two ordinals, the factory ALWAYS binds the record that has
    a HOLD pointer (the u24 at `registro+19`, the one that repeats while you
    hold the key down). If neither has one, or if it can't be read, the lower
    one wins -- the IR payload is the same, so they emit the same thing.
    """
    k2s = list(k2s)
    if len(k2s) == 1 or device_module is None:
        return k2s[0]
    for k2 in k2s:
        try:
            reg, reason = device_module.resolve_section5(blob, (k1 << 8) | k2)
            if reg is None or reason:
                continue
            if int.from_bytes(blob[reg + 19 : reg + 22], "little"):
                return k2
        except Exception:  # noqa: BLE001 -- with no tie-break, the lower one wins
            break
    return k2s[0]


def _sin_vocabulario(empty, disp, page, name, ordinal, nombres) -> dict:
    """The envelope for a device whose commands carry no catalog name: what
    the page ALREADY has bound is counted (a fact of the blob, not of the
    names) and it says, with no dressing up, why there is nothing to propose.
    """
    page_codes = page.get("codigos") or {}
    rows = []
    ya = 0
    for codigo, key, roles in FILAS:
        if codigo in RESERVADOS or codigo in TACTILES or not roles:
            continue
        hoy = page_codes.get(hex2(codigo)) or {}
        atada = hoy.get("state") == "asignada"
        ya += 1 if atada else 0
        rows.append(
            {
                "codigo": codigo,
                "codigo_hex": hex2(codigo),
                "key": key,
                "rol": roles[0],
                "k2": hoy.get("k2") if atada else None,
                "command": None,
                "state": "ya_ligada" if atada else "sin_nombres",
                "reason": None
                if atada
                else "no command name is known for this "
                "device, so the app can't tell which command belongs here",
            }
        )
    with_name = sum(1 for n in nombres if n)
    reason = (
        "%s's commands don't carry catalog names -- %s. Without them the app "
        "can't tell which command belongs on which key, so it proposes "
        "nothing instead of guessing."
        % (
            name,
            "only the labels drawn on its screen are known"
            if with_name
            else "none of its %d commands has a known name" % len(nombres),
        )
    )
    # What was tried and wasn't enough (looking the file up by command
    # count) travels inside `empty`: it is said, not kept quiet.
    if empty.get("no_plan_reason"):
        reason += (
            " Looking the file up by command count didn't work either: %s."
            % (empty["no_plan_reason"])
        )
    return dict(
        empty,
        ok=True,
        name=name,
        screen=ordinal,
        plan_posible=False,
        no_plan_reason=reason,
        rows=rows,
        n_ya_ligadas=ya,
        summary="%d of %d standard keys are already bound on this page. %s"
        % (ya, ROLES_TOTALES, reason),
    )


def device_plan(
    blob: bytes,
    k1: int,
    *,
    hub=None,
    modelo: dict | None = None,
    page: dict | None = None,
    nombres: Sequence[Any] | None = None,
    keys_map=None,
    keys_physical=None,
    device_module=None,
) -> dict:
    """THE call point. Which keys can be bound on device `k1`'s own page, and
    what happens with the ones that can't.

    `modelo` (the output of `keys_map.read`) and `page` (one entry of
    `keys_physical.map_devices`) are passed already computed when the
    caller has them at hand -- the Keys screen has them -- so as not to
    disassemble ~950 slots all over again. If they don't come, they are
    computed.

    `nombres`: the command list IN ORDER, when the caller knows it for certain
    (`nombres_de_json`, when adding a device). It is preferred over the one
    `modelo` carries, which matches by the prefix of the menu name and fails
    as soon as the user gives the device another name. It is accepted ONLY if
    it measures exactly what section [5] declares for that `k1`: if it doesn't
    measure the same, the order can't be the same and a shifted name sends the
    wrong command.

    It ALWAYS returns the same shape:

        {'ok', 'origen', 'k1', 'nombre', 'pantalla',
         'filas':   [{codigo, codigo_hex, tecla, rol, k2, comando, estado,
                      motivo}],
         'cambios': [{pantalla, codigo, k1, k2}],   # for aplicar_dispositivo
         'n_por_ligar', 'n_ya_ligadas', 'n_respetadas', 'n_sin_comando',
         'n_bloqueadas', 'roles_totales',
         'sin_comando': [{codigo, tecla, rol}],
         'respetadas': [...], 'bloqueadas': [...],
         'resumen': one-line text, already written,
         'error'}

    the `state` of each row, and what it means to the user:
      - `por_ligar`   we are going to bind it (today there is no row, or
                      there is an off row)
      - `ya_ligada`   it already points at the command the template asks for
      - `respetada`   it already points at SOMETHING ELSE: it is yours, it is
                      not overwritten
      - `no_command` this device doesn't have that command
      - `bloqueada`   `map_devices` said it can't be done, with a
                      reason
      - `sin_nombres` there is no way to know (see `plan_posible`, below)
    """
    k1 = int(k1)
    empty = {
        "ok": False,
        "origin": "app",
        "k1": k1,
        "name": "device %d" % k1,
        "screen": None,
        "plan_posible": False,
        "no_plan_reason": None,
        "rows": [],
        "changes": [],
        "n_to_bind": 0,
        "n_ya_ligadas": 0,
        "n_respetadas": 0,
        "n_no_command": 0,
        "n_bloqueadas": 0,
        "roles_totales": ROLES_TOTALES,
        "no_command": [],
        "respetadas": [],
        "bloqueadas": [],
        "summary": "",
        "error": None,
    }

    # --- 1. where the data comes from -----------------------------------
    if modelo is None:
        if keys_map is None:
            return dict(empty, error="keys_map.py is not available")
        try:
            modelo = keys_map.read(blob, hub)
        except Exception as exc:  # noqa: BLE001
            return dict(empty, error="could not read the key map: %s" % exc)
    if page is None:
        if keys_physical is None:
            return dict(empty, error="keys_physical.py is not available")
        try:
            pages = keys_physical.map_devices(blob, hub)
        except Exception as exc:  # noqa: BLE001
            return dict(empty, error="could not read the device pages: %s" % exc)
        page = next((p for p in pages if p.get("k1") == k1), None)
    if page is None or page.get("screen") is None:
        return dict(
            empty,
            error="device %d has no page of its own that the firmware reaches, "
            "so there is no header to write the keys into" % k1,
        )
    if page.get("error"):
        return dict(
            empty,
            name=page.get("name") or empty["name"],
            screen=page.get("screen"),
            error=str(page["error"]),
        )

    disp = next(
        (d for d in (modelo.get("devices") or []) if d.get("k1") == k1), None
    )
    if disp is None:
        return dict(
            empty,
            screen=page.get("screen"),
            name=page.get("name") or empty["name"],
            error="section [5] does not declare a device %d" % k1,
        )
    from_model = [c.get("name") for c in (disp.get("commands") or [])]
    ordinal = int(page["screen"])
    name = disp.get("name") or page.get("name") or "device %d" % k1

    # THE CERTAIN LIST, if the caller brought it. THE CHECK THAT DECIDES is
    # the length against what section [5] declares: it is the same hard
    # criterion `keys_map._list_from_json` uses, and for the same reason -- a
    # shifted name goes unnoticed and sends the wrong command.
    ciertos = False
    if nombres is not None:
        if len(nombres) == len(from_model) and any(nombres):
            ciertos = True
        else:
            return dict(
                empty,
                screen=ordinal,
                name=name,
                error="the device file lists %d commands but section [5] "
                "declares %d for device %d: the order can't be the same, so "
                "no key is bound rather than binding the wrong ones"
                % (len(nombres), len(from_model), k1),
            )
    if not ciertos:
        nombres = from_model

    # --- 1b. THE VOCABULARY. Without this the screen lied ----------------
    #
    # `keys_map.read()` publishes where each name came from, and not all
    # of them are good for this:
    #
    #   `device catalog`   from the `hub-config-with-device.json` the device
    #                      was added with. It is the canonical vocabulary
    #                      (`VolumeUp`, `DirectionLeft`, `Select`/`OK`) and it
    #                      is the only one the role table can be compared
    #                      comparar.
    #   `on-screen label`  the text DRAWN on the LCD ("Vol +", "Ch"). It is
    #                      the only thing there is for the three devices that
    #                      came with the remote, and it is NOT a command
    #                      comando.
    #   `no name known`    there is nothing.
    #
    # Comparing the role table against on-screen labels produced false and
    # alarming sentences -- measured: "TV has no Mute, Number1, ..." about
    # page 103, which has all 34 keys bound from the factory. So if the
    # vocabulary is not the catalog one NO plan is built: what the page
    # already has is counted (that comes from the blob, not from the names)
    # and it says why nothing can be proposed.
    fiables = ciertos or bool(
        sum(
            1
            for c in (disp.get("commands") or [])
            if c.get("origin") in VOCABULARIO_FIABLE and c.get("name")
        )
    )
    by_count = None
    if not fiables and hub and not any(nombres):
        # LAST RESORT, and only when NO name at all is known: look the file up
        # by command count. It is never used against a device that already has
        # drawn labels (the three factory ones): those have names, only they
        # are not command names, and overwriting them with a list guessed by
        # count would be exactly the kind of assumption this module
        # avoids.
        by_count, by_count_err = names_by_count(hub, len(from_model))
        if by_count:
            nombres = by_count
            fiables = True
        else:
            empty = dict(empty, no_plan_reason=by_count_err)
    if not fiables:
        return _sin_vocabulario(empty, disp, page, name, ordinal, nombres)

    # --- 2. the raw plan: `config_work/` if it exposes it, or here ------
    crudo, origin = _plan_de_config_work(blob, k1, hub)
    if origin.endswith("failed") or (crudo is None and origin):
        # the other side exists but it broke: it is said, it is not covered up
        # with the computation here (that would hide that the real tool failed).
        return dict(empty, screen=ordinal, name=name, error=origin)
    if crudo is not None:
        # NORMALISATION of the other side's shape. Only two things are read from
        # each row -- `codigo` and `k2` -- and a row with no `k2` is a row that
        # the other side does NOT bind (in `teclas_auto` those carry
        # `accion="apagar"|"respetada"|"omitida"`). `rol` over there is a LIST
        # of names; here it is a text, and the first one is taken.
        propuesta = {}
        for f in crudo.get("rows") or []:
            cod = int(f.get("codigo"))
            if f.get("k2") is None or f.get("accion") not in (None, "ligar"):
                continue
            rol = f.get("rol") or f.get("name")
            if isinstance(rol, (list, tuple)):
                rol = rol[0] if rol else None
            propuesta[cod] = {
                "k2": int(f["k2"]),
                "rol": rol,
                "command": f.get("name") or f.get("command"),
            }
    else:
        origin = "app"
        by_name: dict[str, list[int]] = {}
        for k2, n in enumerate(nombres):
            if n:
                by_name.setdefault(_norm(n), []).append(k2)
        propuesta = {}
        for codigo, _key, roles in FILAS:
            if not roles:
                continue
            for rol in roles:
                k2s = by_name.get(_norm(rol))
                if k2s:
                    k2 = _con_hold(device_module, blob, k1, k2s)
                    propuesta[codigo] = {
                        "k2": k2,
                        "rol": rol,
                        "command": nombres[k2],
                    }
                    break

    # --- 3. the cross with what the page has TODAY ----------------------
    page_codes = page.get("codigos") or {}
    rows: list[dict] = []
    changes: list[dict] = []
    no_command: list[dict] = []
    respetadas: list[dict] = []
    bloqueadas: list[dict] = []
    n_ya = 0

    for codigo, key, roles in FILAS:
        if codigo in RESERVADOS or codigo in TACTILES or not roles:
            continue
        hexc = hex2(codigo)
        hoy = page_codes.get(hexc)
        prop = propuesta.get(codigo)
        base = {
            "codigo": codigo,
            "codigo_hex": hexc,
            "key": key,
            "rol": (prop or {}).get("rol") or roles[0],
            "k2": (prop or {}).get("k2"),
            "command": (prop or {}).get("command"),
        }

        if prop is None:
            row = dict(
                base,
                state="no_command",
                reason="%s doesn't have a %s command" % (name, roles[0]),
            )
            no_command.append(row)
            rows.append(row)
            continue
        if hoy is None:
            row = dict(
                base,
                state="bloqueada",
                reason="that key isn't on the list this page can bind",
            )
            bloqueadas.append(row)
            rows.append(row)
            continue
        if not hoy.get("editable"):
            row = dict(
                base,
                state="bloqueada",
                reason=hoy.get("reason") or "this page refuses that key",
            )
            bloqueadas.append(row)
            rows.append(row)
            continue
        if hoy.get("state") == "asignada":
            # RULE: what already points at something is NOT overwritten. If it
            # points exactly at the command the template asks for, it counts as
            # already done; if it points elsewhere, it is the user's and is left.
            same = hoy.get("k1_hoy") == k1 and hoy.get("k2") == prop["k2"]
            if same:
                n_ya += 1
                rows.append(dict(base, state="ya_ligada", reason=None))
            else:
                row = dict(
                    base,
                    state="respetada",
                    k2_actual=hoy.get("k2"),
                    k1_actual=hoy.get("k1_hoy"),
                    reason="you already set this one: it is left exactly as it is",
                )
                respetadas.append(row)
                rows.append(row)
            continue

        rows.append(dict(base, state="por_ligar", reason=None))
        changes.append(
            {"screen": ordinal, "codigo": codigo, "k1": k1, "k2": prop["k2"]}
        )

    n_to_bind = len(changes)
    summary = "%d key%s will be bound" % (n_to_bind, "" if n_to_bind == 1 else "s")
    if n_ya:
        summary += ", %d already were" % n_ya
    if respetadas:
        summary += ", %d left as you set %s" % (
            len(respetadas),
            "it" if len(respetadas) == 1 else "them",
        )
    if no_command:
        summary += ", %d can't be (%s has no %s)" % (
            len(no_command),
            name,
            ", ".join(f["rol"] for f in no_command),
        )
    if bloqueadas:
        summary += ", %d refused by the page" % len(bloqueadas)

    return {
        "ok": True,
        "origin": origin,
        "k1": k1,
        "name": name,
        "screen": ordinal,
        "plan_posible": True,
        "no_plan_reason": None,
        "rows": rows,
        "changes": changes,
        "n_to_bind": n_to_bind,
        "n_ya_ligadas": n_ya,
        "n_respetadas": len(respetadas),
        "n_no_command": len(no_command),
        "n_bloqueadas": len(bloqueadas),
        "roles_totales": ROLES_TOTALES,
        "no_command": no_command,
        "respetadas": respetadas,
        "bloqueadas": bloqueadas,
        "summary": summary + ".",
        "error": None,
    }


# ==========================================================================
# SELFTEST -- pure, no blob, no USB
# ==========================================================================
def _selftest() -> int:
    fallas = []

    # 1. the template has the 49 factory rows, in their order
    if len(FILAS) != 49:
        fallas.append(
            "FILAS has %d rows, the factory template has 49" % len(FILAS)
        )
    codigos = [c for c, _t, _r in FILAS]
    if len(set(codigos)) != len(codigos):
        fallas.append("there is a repeated code in FILAS")
    if ROLES_TOTALES != 34:
        fallas.append("roles con comando: %d, medidos 34" % ROLES_TOTALES)

    # 2. NOTHING reserved may have a role
    for c, _t, r in FILAS:
        if r and (c in RESERVADOS or c in TACTILES):
            fallas.append("%s has a role and is reserved/touch" % hex2(c))

    # 3. the real LG: 32 of 34, and exactly those two are missing.
    #    Local catalog names, exactly as `keys_map.read` publishes them.
    lg = [
        "-",
        ".",
        "0",
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
        "8",
        "9",
        "Back",
        "Blue",
        "Caption",
        "ChannelDown",
        "ChannelPrev",
        "ChannelUp",
        "DirectionDown",
        "DirectionLeft",
        "DirectionRight",
        "DirectionUp",
        "Exit",
        "FastForward",
        "Green",
        "Guide",
        "Home",
        "Info",
        "InputAv1",
        "InputComponent1",
        "InputHdmi1",
        "InputHdmi2",
        "InputHdmi3",
        "InputHdmi4",
        "InputNext",
        "InputTv",
        "In-Start",
        "In-Stop",
        "Mute",
        "Netflix",
        "OK",
        "Pause",
        "PictureMode",
        "Play",
        "PowerOff",
        "PowerOn",
        "PowerToggle",
        "PrimeVideo",
        "Record",
        "Red",
        "Return",
        "Rewind",
        "ServiceMenu",
        "Settings",
        "Simplink",
        "Sleep",
        "SmartMenu",
        "SoundMode",
        "Stop",
        "VolumeDown",
        "VolumeUp",
        "Yellow",
        "Signal",
    ]
    p = plan_from_names(lg)
    if p["n_ligadas"] != 32:
        fallas.append("LG: %d roles ligados, medidos 32" % p["n_ligadas"])
    if [f["rol"] for f in p["missing"]] != ["SkipBackward", "SkipForward"]:
        fallas.append(
            "LG: faltan %r, medidos SkipBackward/SkipForward"
            % [f["rol"] for f in p["missing"]]
        )
    esperado = {
        0x89: 38,
        0x88: 3,
        0x8B: 19,
        0x8A: 27,
        0x8C: 1,
        0x8F: 11,
        0x8E: 2,
        0x81: 6,
        0x83: 60,
        0x82: 22,
        0x85: 51,
        0x84: 59,
        0x87: 48,
        0x98: 5,
        0x99: 7,
        0x9B: 21,
        0x9C: 40,
        0x9D: 18,
        0x9E: 43,
        0x9F: 41,
        0x90: 10,
        0x91: 8,
        0x92: 25,
        0x93: 17,
        0x94: 15,
        0x95: 23,
        0x97: 58,
        0xA8: 26,
        0xA3: 12,
        0xA1: 20,
        0xA0: 4,
        0xA7: 9,
    }
    dio = {f["codigo"]: f["k2"] for f in p["ligadas"]}
    for c, k2 in esperado.items():
        if dio.get(c) != k2:
            fallas.append("LG %s: k2=%r, medido %d" % (hex2(c), dio.get(c), k2))

    # 4. a device with no known name at all binds NOTHING
    if plan_from_names([None] * 40)["n_ligadas"] != 0:
        fallas.append("with no names it should bind 0")

    # 5. `-` and `.` are not clobbered when normalising
    if _norm("-") == _norm("."):
        fallas.append("_norm() confunde '-' con '.'")

    print("plantilla_teclas: %d filas, %d roles" % (len(FILAS), ROLES_TOTALES))
    print(
        "LG (63 commands from the catalog): %d bound, missing %s"
        % (p["n_ligadas"], ", ".join(f["rol"] for f in p["missing"]))
    )
    if fallas:
        print("SELFTEST: FAILED")
        for f in fallas:
            print("  -", f)
        return 1
    print("SELFTEST: PASSED (pure: no blob, no USB, nothing written).")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
