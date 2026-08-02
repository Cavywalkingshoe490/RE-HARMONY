#!/usr/bin/env python3
"""AUTO-BINDING of a device's rubber keys, on the device's OWN page.

## What this is for

Adding a device gives it a page full of touchscreen buttons and a key
register that declares four codes (`06 07 b7 2d`) and not one rubber key. So
"Devices -> LG -> Volume +" does nothing, and the user has to bind 30-odd
keys by hand, one at a time. The three pages the remote SHIPPED with do not
look like that: each declares the same 49 codes and binds the whole keyboard
to a single device -- a perfect `k1` partition, three times over.

This module reproduces that, for any device, from the device's own command
list. It does not invent a layout: the layout is READ (`roles_medidos`) out
of the three factory pages, and `ROLES` below is that reading, frozen.

    plan = planificar(b, k1)          # what WOULD be written, and why
    out, repuntes, detalle = aplicar(b, plan)
    controles(b, out, plan, detalle, repuntes)

Nothing here writes to the device, imports `grabar.cargar`, or touches
`account_export/`. The only writer it calls is
`keys_physical.apply_device`, which refuses anything that would hang
the remote before building a byte.

## The five rules, and where each one is enforced

**1. Match by FUNCTION, never by position.** `ROLES` maps a key CODE to the
role the factory gives it, expressed as the command names the factory bound
there. A device's command is matched by name, normalized
(`normalizar`/`SINONIMOS`): `VolumeUp`, `Volume Up`, `VOL_UP`, `Vol +` and
`volumeIncrease` all land on `VolumeUp`. Ordinals are never used to match --
`k2` is only ever the ANSWER, never the question.

**2. What cannot be matched with confidence is NOT bound.** A key bound to
the wrong command is worse than an unbound key, because nothing on screen
tells the user it is wrong. So: a name that normalizes to nothing known is
dropped; a role with two candidate `k2` that the hold rule cannot separate
AND whose IR waveforms differ is dropped; a `(k1,k2)` that
`device.resolve_section5` does not resolve is dropped (that one HANGS
the remote -- see `PELIGROS` in the project notes). Every drop carries its
measured reason in `plan["rows"][]["reason"]`.

**3. The user's manual assignments win.** The rule is structural, so it needs
no ledger and cannot drift: **auto never overwrites a row that already
resolves to a command.** A row is free only if it does not exist yet
("sin fila") or if it is the factory's disabled row (`cod 00 00 00`,
"declarada y apagada"). Anything already bound -- by the user, by an earlier
auto run, by the factory -- is reported as `accion="respetada"` and left
alone. Two escape hatches, both explicit: `respetar=(codes...)` adds codes the
caller knows the user owns, and `forzar=True` (default False) is the only way
to overwrite a bound row.

**4. Works on a device already in the remote**, not only on a new one. The
only inputs are the blob, the `k1`, and a `{k2: name}` map; the map is
recovered from the blob itself (`commands_from_blob`: decode each command's
IR waveform and match `(protocol, payload)` against the Hub's `DeviceList`)
when the JSON the device came from is not at hand.

**5. A key the device does not have is DECLARED AND DISABLED**, not omitted
(`apagar_lo_no_ligado=True`, the default). That is what the factory does --
page 140 disables 13 rubber keys with `cod 00 00 00` -- and it is not
cosmetic: `0x02E2F2` matches by CODE ALONE and consumes the event, so a
declared-disabled key does nothing, whereas an ABSENT one falls through to
the global keymap `[10][2]`, which jumps to page 146 ("pick a device"). Same
key, two very different behaviours.

## What is never touched

  * `06` / `07` -- the page's enter/leave hooks (they light and unlight the
    LEDs). `2D` -- the internal pager between the page's N sub-screens.
    All three are page infrastructure, all three are in the 55-key inventory,
    and `keys_physical.CODIGOS_INFRAESTRUCTURA` now refuses them.
  * `AE` / `AF` -- the side strips: they are what pages. Declaring one kills
    paging on that page.
  * `A5` -- Power. On the global keymap it enters the "All Off" Activity.
    The factory device template does not declare it and neither does this.
  * `A2`, `A6`, `B7` -- also outside the factory template, left to fall
    through on purpose. A `B7` row that a page already carries is PRESERVED:
    this module adds rows, it never removes one.

NOTE ON NAMING: dict keys are Spanish, like the rest of the key stack
(`keys_map`, `keys_physical`, `teclas_alcance`): `app/api.py` forwards
these dicts to the UI as they are.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import add_device as D
import relocate
import keys_reach as TA
import keys_physical as TF
import keys_map as TM

BASE = D.BASE
TAG_OBJ = TF.TAG_OBJ

# ==========================================================================
# 1. THE FACTORY LAYOUT, read from the blob and frozen here
# ==========================================================================

#: `(code, (name, ...))` -- the role of each rubber key, as the FACTORY binds
#: it, in the factory template's own row order. The names are the command
#: names the three factory device pages (`table[6]` 78 DVR / 103
#: Sony TV / 140 Home) actually point at, recovered by decoding each command's
#: IR waveform and matching `(protocol, payload)` against the user's Hub
#: `DeviceList.json`. NOT typed from a table: produced by `roles_medidos()`
#: over grabada #9, and `check_template_vs_blob()` re-derives it and
#: compares, so the constant cannot drift away from the blob in silence.
#:
#: More than one name means the factory used different names on different
#: pages for the SAME physical key (a DVR's `Info` is a TV's `Display`; a
#: receiver's Channel+ is `NextPreset`). The tuple is a PREFERENCE ORDER --
#: first one the device has, wins -- and its order is the order the pages
#: were read in (78, then 103, then 140), i.e. the DVR's word first.
ROLES: tuple[tuple[int, tuple[str, ...]], ...] = (
    (0x89, ("Mute",)),
    (0x88, ("Number1",)),
    (0x8B, ("DirectionLeft",)),
    (0x8A, ("Info", "Display")),
    (0x8C, ("Dot",)),
    (0x8F, ("Number9",)),
    (0x8E, ("Number0",)),
    (0x81, ("Number4",)),
    (0x83, ("VolumeUp",)),
    (0x82, ("Exit", "Return")),
    (0x85, ("Rewind",)),
    (0x84, ("VolumeDown",)),
    (0x87, ("Record",)),
    (0x86, ("SkipBackward",)),
    (0x98, ("Number3",)),
    (0x99, ("Number5",)),
    (0x9B, ("DirectionUp",)),
    (0x9C, ("Select",)),
    (0x9D, ("DirectionDown",)),
    (0x9E, ("Play",)),
    (0x9F, ("Pause",)),
    (0x90, ("Number8",)),
    (0x91, ("Number6",)),
    (0x92, ("Guide",)),
    (0x93, ("ChannelUp", "NextPreset")),
    (0x94, ("ChannelDown", "PrevPreset")),
    (0x95, ("FastForward",)),
    (0x96, ("SkipForward",)),
    (0x97, ("Stop",)),
    (0xA8, ("Menu", "Home")),
    (0xA3, ("Back", "PrevChannel")),
    (0xA1, ("DirectionRight",)),
    (0xA0, ("Number2",)),
    (0xA7, ("Number7",)),
)

#: the four rubber codes the factory declares DISABLED on all three of its
#: device pages. `graphics/mando/keys.json` labels `0x9A` "D-pad down" and
#: `0xA4` "arrow up", and the Activity context `[10][8]` does bind them -- so
#: the keys exist. The DEVICE page picks `0x9B`/`0x9D` instead, all three
#: times. Bind what the factory binds, not what the photo says.
CODIGOS_APAGADOS_DE_FABRICA = (0x8D, 0x9A, 0xAD, 0xA4)

#: the 6 inventory codes the factory template does NOT declare, so they fall
#: through to the global keymap `[10][1]` on purpose: `AF`/`AE` page, `A5` is
#: Power -> "All Off", `A2` is a UI action, `A6`/`B7` reach the catch-all.
CODES_OUTSIDE_TEMPLATE = (0xAF, 0xAE, 0xA2, 0xA6, 0xA5, 0xB7)


def plantilla_codigos(b: bytes) -> tuple[list[int], str]:
    """The 49 codes a device page declares, `(codes, source)`.

    Read from the blob when the factory template is in it (a `table[6]`
    header whose code set is exactly the inventory minus
    `CODES_OUTSIDE_TEMPLATE`); otherwise DERIVED from this blob's own
    inventory by removing those 6. The derived order differs from the factory
    one only in that `A4` and `2D` are swapped, and order is not load-bearing
    (`0x02E2F2` matches by code), but reading it is free and makes a diff
    against page 78 legible.
    """
    inv = TF.inventario(b)
    objetivo = [c for c in inv if c not in CODES_OUTSIDE_TEMPLATE]
    TM.set_t6(b)
    n6 = D.u16(b, D.T6)
    counts: dict[tuple[int, ...], int] = {}
    for o in range(n6):
        tr = D.read_trailer(b, D.u24(b, D.T6 + 3 + 3 * o) - BASE, max_n=200)
        if tr is None:
            continue
        try:
            rows = TA.register_rows(b, tr["hdr"] - BASE)
        except ValueError:
            continue
        firma = tuple(f[1] for f in rows)
        if set(firma) == set(objetivo) and len(firma) == len(objetivo):
            counts[firma] = counts.get(firma, 0) + 1
    if counts:
        firma = max(counts, key=lambda f: counts[f])
        return list(firma), "factory template, %d pages declare it" % counts[firma]
    return objetivo, "derived from this blob's %d-key inventory" % len(inv)


def roles_medidos(b: bytes, n5: dict[int, dict[int, str]]) -> dict[int, list[str]]:
    """Re-derives `ROLES` from a blob's own factory device pages.

    `n5` is `{k1: {k2: name}}` (see `nombres_seccion5`). A page counts as a
    factory device page when its key register declares the 49-code template.

    ONE cleaning rule, and it is not a taste call: a name that is the FIRST
    (primary) name of another code cannot be a variant of this one. That is
    what removes the single anomaly in the blob -- page 140's device has no
    `Play`, `Pause` or `Guide`, so the factory pointed those three keys at
    the SAME object as OK (`Select`, 4 keys -> 1 object). It happens on 1 of
    3 pages and would otherwise teach this module that Play means Select.
    """
    TM.set_t6(b)
    dest11 = relocate.table(b, relocate.sections(b)[11][0])
    plantilla, _source = plantilla_codigos(b)
    objetivo = set(plantilla)
    n6 = D.u16(b, D.T6)
    outside: dict[int, list[str]] = {}
    for o in range(n6):
        tr = D.read_trailer(b, D.u24(b, D.T6 + 3 + 3 * o) - BASE, max_n=200)
        if tr is None:
            continue
        try:
            rows = TA.register_rows(b, tr["hdr"] - BASE)
        except ValueError:
            continue
        if {f[1] for f in rows} != objetivo:
            continue
        for _k, cod, _campo, idv, cls in rows:
            outside.setdefault(cod, [])
            if cls != TAG_OBJ or not 0 <= idv < len(dest11):
                continue
            _f, cmd, _dev, _pag, _rs = TF._forma(b, dest11, idv)
            if cmd is None:
                continue
            name = n5.get(cmd >> 8, {}).get(cmd & 0xFF)
            if name and name not in outside[cod]:
                outside[cod].append(name)
    primarios = {v[0] for v in outside.values() if v}
    return {
        cod: [n for i, n in enumerate(nn) if i == 0 or n not in primarios]
        for cod, nn in outside.items()
    }


# ==========================================================================
# 2. NORMALIZATION -- the same function under a hundred spellings
# ==========================================================================

#: `+` and `-` are the only punctuation that carries meaning in a command
#: name (`Vol +`, `CH-`), so they become words before everything else is
#: stripped. Doing it the other way round turns `Vol +` and `Vol -` into the
#: same string, which is exactly the kind of collision this module must not
#: have.
_SIGNOS = (
    ("+", " plus "),
    ("−", " minus "),  # U+2212 MINUS SIGN
    ("–", " minus "),  # EN DASH
    ("-", " minus "),
)


def normalizar(name: str) -> str:
    """`"Volume Up"`, `"VOL_UP"`, `"vol+"` -> a single comparable token.

    Lowercases, turns `+`/`-` into words, drops everything that is not a
    letter or a digit. It does NOT try to be clever about word order: two
    names that differ in order are two names.
    """
    s = (name or "").strip().lower()
    for old, fresh in _SIGNOS:
        s = s.replace(old, fresh)
    return "".join(ch for ch in s if ch.isalnum())


#: normalized alias -> the canonical name `ROLES` speaks. Only aliases that
#: are unambiguous across the whole table are here: `next` (SkipForward on a
#: player, NextPreset on a receiver), `forward` (FastForward or SkipForward)
#: and `playpause` (two roles at once) are deliberately ABSENT -- an unbound
#: key is cheap, a wrongly bound one is not.
SINONIMOS: dict[str, str] = {}


def _alias(cano: str, *alias: str) -> None:
    for a in alias:
        n = normalizar(a)
        anterior = SINONIMOS.get(n)
        if anterior is not None and anterior != cano:
            raise ValueError("alias %r would mean both %r and %r" % (a, anterior, cano))
        SINONIMOS[n] = cano


_alias("Mute", "Mute", "MuteToggle", "VolumeMute", "Silence", "Sound Off")
_alias(
    "VolumeUp",
    "VolumeUp",
    "Volume Up",
    "Vol Up",
    "Vol +",
    "Volume +",
    "VolumePlus",
    "VolumeIncrease",
    "Volume Increase",
)
_alias(
    "VolumeDown",
    "VolumeDown",
    "Volume Down",
    "Vol Down",
    "Vol -",
    "Volume -",
    "VolumeMinus",
    "VolumeDecrease",
    "Volume Decrease",
)
_alias(
    "ChannelUp",
    "ChannelUp",
    "Channel Up",
    "Ch Up",
    "Ch +",
    "Channel +",
    "ChannelPlus",
    "ChanUp",
    "ProgramUp",
    "Program Up",
    "ProgUp",
)
_alias(
    "ChannelDown",
    "ChannelDown",
    "Channel Down",
    "Ch Down",
    "Ch -",
    "Channel -",
    "ChannelMinus",
    "ChanDown",
    "ProgramDown",
    "Program Down",
    "ProgDown",
)
_alias("NextPreset", "NextPreset", "Preset Up", "PresetUp", "Preset +")
_alias("PrevPreset", "PrevPreset", "Preset Down", "PresetDown", "Preset -")
for _d in range(10):
    _alias(
        "Number%d" % _d,
        "Number%d" % _d,
        "Num%d" % _d,
        "Digit%d" % _d,
        "Key%d" % _d,
        "Numeric%d" % _d,
        "%d" % _d,
    )
_alias("Dot", "Dot", "Period", "Point", "Decimal")
_alias(
    "DirectionUp",
    "DirectionUp",
    "Direction Up",
    "CursorUp",
    "Cursor Up",
    "ArrowUp",
    "NavUp",
    "DPadUp",
    "MoveUp",
    "Up",
)
_alias(
    "DirectionDown",
    "DirectionDown",
    "Direction Down",
    "CursorDown",
    "Cursor Down",
    "ArrowDown",
    "NavDown",
    "DPadDown",
    "MoveDown",
    "Down",
)
_alias(
    "DirectionLeft",
    "DirectionLeft",
    "Direction Left",
    "CursorLeft",
    "Cursor Left",
    "ArrowLeft",
    "NavLeft",
    "DPadLeft",
    "MoveLeft",
    "Left",
)
_alias(
    "DirectionRight",
    "DirectionRight",
    "Direction Right",
    "CursorRight",
    "Cursor Right",
    "ArrowRight",
    "NavRight",
    "DPadRight",
    "MoveRight",
    "Right",
)
_alias(
    "Select", "Select", "OK", "Enter", "Confirm", "CursorSelect", "DPadSelect", "Accept"
)
_alias("Play", "Play")
_alias("Pause", "Pause")
_alias("Stop", "Stop")
_alias("Record", "Record", "Rec")
_alias("Rewind", "Rewind", "Rew", "FastRewind", "Fast Rewind")
_alias("FastForward", "FastForward", "Fast Forward", "FF", "FastFwd", "Fwd")
_alias(
    "SkipForward",
    "SkipForward",
    "Skip Forward",
    "SkipFwd",
    "SkipNext",
    "NextTrack",
    "Next Track",
    "ChapterForward",
)
_alias(
    "SkipBackward",
    "SkipBackward",
    "Skip Backward",
    "SkipBack",
    "SkipPrevious",
    "PreviousTrack",
    "Previous Track",
    "PrevTrack",
    "Replay",
    "InstantReplay",
    "ChapterBackward",
)
_alias("Guide", "Guide", "EPG", "TVGuide", "TV Guide", "ProgramGuide")
_alias("Menu", "Menu", "MainMenu", "Main Menu")
_alias("Home", "Home")
_alias("Back", "Back")
_alias(
    "PrevChannel",
    "PrevChannel",
    "Previous Channel",
    "PreviousChannel",
    "LastChannel",
    "Last Channel",
    # the LG's own `hub-config-with-device.json` spells it this way round,
    # while the Hub's DeviceList calls the same payload `PrevChannel`: the
    # exact drift between vocabularies this table exists to absorb.
    "ChannelPrev",
    "Channel Prev",
    "ChPrev",
)
_alias("Exit", "Exit", "Close")
_alias("Return", "Return")
_alias("Info", "Info", "Information", "InfoBanner")
_alias("Display", "Display")

#: canonical name -> the code that owns it. Built from `ROLES`, and the build
#: refuses a name that two codes claim: the whole design rests on the match
#: being a function, and a name that means two keys is not one.
CODE_BY_NAME: dict[str, int] = {}
for _cod, _nombres in ROLES:
    for _n in _nombres:
        if _n in CODE_BY_NAME and CODE_BY_NAME[_n] != _cod:
            raise ValueError(
                "role name %r is claimed by %#04x and %#04x"
                % (_n, CODE_BY_NAME[_n], _cod)
            )
        CODE_BY_NAME[_n] = _cod
        _alias(_n, _n)  # a canonical name is its own alias


#: names that survive `normalizar` badly because they are ALL punctuation --
#: matched on the raw trimmed string instead, and only where the meaning is
#: not in doubt. The LG's own JSON names two commands `-` and `.`; `.` is the
#: dot/decimal key everywhere, so it resolves, and `-` does NOT: a lone dash
#: is the sub-channel separator on some brands and a minus on others, and
#: guessing it is exactly what rule 2 forbids. (On this device it costs
#: nothing either way: `-` and `.` carry the same IR payload.)
NOMBRES_LITERALES = {".": "Dot", "·": "Dot"}


def canonico(name: str) -> str | None:
    """A device's command name -> the canonical role name, or None."""
    crudo = (name or "").strip()
    if crudo in NOMBRES_LITERALES:
        return NOMBRES_LITERALES[crudo]
    n = normalizar(crudo)
    return SINONIMOS.get(n) if n else None


# ==========================================================================
# 3. WHERE THE {k2: name} MAP COMES FROM
# ==========================================================================


def nombres_seccion5(b: bytes, hub=None) -> dict[int, dict[int, str]]:
    """`{k1: {k2: name}}` for every command section `[5]` resolves.

    Decodes each command's PRESS waveform (`registro+16`) and matches
    `(protocol, payload)` against the Hub's `DeviceList.json` -- the same
    join `commands.py` uses. A command whose payload the Hub does not know
    simply has no name, and a device with no names binds nothing.
    """
    import commands as CMDS
    import irscan

    path = str(hub) if hub else str(TM.HUB_VOCAB)
    hubnames = CMDS.hub_names(path)
    outside: dict[int, dict[int, str]] = {}
    for k1, d in enumerate(D.read_section5(b)):
        m: dict[int, str] = {}
        for k2 in range(d.get("n", 0)):
            reg, _reason = D.resolve_section5(b, (k1 << 8) | k2)
            if reg is None:
                continue
            try:
                r = irscan.decode(irscan.read_waveform(b, D.u24(b, reg + 16) - BASE))
            except Exception:  # noqa: BLE001 -- an undecodable command has no name
                r = None
            nn = sorted(
                x
                for x in (hubnames.get((r[0], r[2])) if r else None) or []
                if x != "Unknown"
            )
            if nn:
                m[k2] = nn[0]
        outside[k1] = m
    return outside


def commands_from_blob(b: bytes, k1: int, hub=None) -> tuple[dict[int, str], str]:
    """`({k2: name}, source)` for a device ALREADY in the remote."""
    n5 = nombres_seccion5(b, hub)
    return n5.get(k1, {}), "blob + Hub DeviceList (%s)" % (hub or TM.HUB_VOCAB)


def commands_from_config(
    cfg: str | pathlib.Path, device: str | None = None
) -> tuple[dict[int, str], str]:
    """`({k2: name}, source)` from the `hub-config-with-device.json` a device
    was added from.

    `k2` is the command's INDEX in `commands.commands_of()`, which is exactly
    how `add_device.py` numbers them when it builds the blob (`ordinal =
    [c[0] for c in cmds].index(...)`) -- the same function, so the two cannot
    drift.
    """
    import command_records as CMD

    protos, devs = CMD.load_hub_config(str(cfg))
    _i, dev = CMD.choose_device(devs, device)
    if dev is None:
        raise ValueError(
            "%r is not in %s (there are: %s)"
            % (device, cfg, [CMD.device_name(d) for d in devs])
        )
    cmds, _saltados = CMD.commands_of(dev, protos)
    return (
        {k2: c[0] for k2, c in enumerate(cmds)},
        "config %s (%s)" % (pathlib.Path(cfg).name, CMD.device_name(dev)),
    )


# ==========================================================================
# 4. PICKING THE k2 -- the duplicate that is not a duplicate
# ==========================================================================


def _onda(b: bytes, k1: int, k2: int):
    """The command's press waveform, as the list of words the emitter reads."""
    import irscan

    reg, _ = D.resolve_section5(b, (k1 << 8) | k2)
    if reg is None:
        return None
    try:
        return tuple(irscan.read_waveform(b, D.u24(b, reg + 16) - BASE))
    except Exception:  # noqa: BLE001
        return None


def _has_hold(b: bytes, k1: int, k2: int) -> bool:
    """Whether the record carries a HOLD pointer (`registro+19` != 0): the
    one that repeats while the key is held."""
    reg, _ = D.resolve_section5(b, (k1 << 8) | k2)
    return reg is not None and D.u24(b, reg + 19) != 0


def elegir_k2(b: bytes, k1: int, candidatos: list[int]) -> tuple[int | None, str]:
    """Which `k2` of several with the same name, and why.

    The factory's own rule, measured: **21 of 21** duplicates that pages 78
    and 103 bind are the record WITH a hold pointer. When that does not
    separate them (the LG's three duplicates all have hold), the waveforms
    are compared: identical waveform means identical command and the lowest
    `k2` is taken; different waveforms mean this module genuinely does not
    know which one the user meant, and it binds neither.
    """
    utiles = [
        k2 for k2 in candidatos if D.resolve_section5(b, (k1 << 8) | k2)[0] is not None
    ]
    if not utiles:
        return None, "no candidate resolves through section [5]"
    if len(utiles) == 1:
        return utiles[0], "the only candidate"
    con_hold = [k2 for k2 in utiles if _has_hold(b, k1, k2)]
    if len(con_hold) == 1:
        return con_hold[0], "the only one with a hold pointer (factory rule, 21/21)"
    finalistas = con_hold or utiles
    ondas = {k2: _onda(b, k1, k2) for k2 in finalistas}
    distintas = {o for o in ondas.values() if o is not None}
    if len(distintas) == 1 and None not in ondas.values():
        return (
            min(finalistas),
            "%d candidates%s and all emit the same waveform: taking k2=%d"
            % (
                len(finalistas),
                " with hold" if con_hold else "",
                min(finalistas),
            ),
        )
    return None, (
        "%d candidates (%s) that the hold rule does not separate and whose IR "
        "waveforms are NOT the same: not bound, so as not to guess"
        % (len(finalistas), finalistas)
    )


# ==========================================================================
# 5. THE PLAN
# ==========================================================================

#: the states of a header row in which auto is allowed to write. Anything
#: else is the user's (or the factory's) and is left alone -- rule 3.
ESTADOS_LIBRES = ("sin fila", "declarada y apagada")


def planificar(
    b: bytes,
    k1: int,
    commands: dict[int, str] | None = None,
    screen: int | None = None,
    respetar=(),
    apagar_lo_no_ligado: bool = True,
    forzar: bool = False,
    hub=None,
    config: str | pathlib.Path | None = None,
    device: str | None = None,
) -> dict:
    """What auto WOULD write on device `k1`'s page, and why -- writes nothing.

    ## THE CONTRACT (read this before wiring anything to it)

    One shape, not two: `plan["changes"]` is **exactly** the list
    `keys_physical.apply_device()` eats, and `apply()` below hands
    it over unchanged. Nothing translates keys on the way, because a key that
    gets translated is a key that can be mistranslated.

        plan = {
          "k1", "pantalla", "nombre",      # which device, which page
          "dev_id",                        # (k1<<8)|0x01 on the factory's 3
          "fuente",                        # where the {k2: name} map came from
          "plantilla",                     # where the 49 codes came from
          "n_comandos", "n_nombrados",
          "comandos": {k2: name},          # the map actually used
          "respetar": [codes], "apagar_lo_no_ligado": bool, "forzar": bool,
          "sin_rol": [(k2, name)],         # commands no key wants
          "filas":   [ ... one per template code, ALWAYS ... ],
          "cambios": [ ... for aplicar_dispositivo, may be empty ... ],
          "avisos":  [str],
          "resumen": {"ligar", "apagar", "respetada", "omitida",
                      "roles_ligados_ahora", "roles_con_comando",
                      "comandos_sin_rol"},
        }

    Every value is JSON-clean -- no `set`, no `bytes`, no offsets as objects
    -- because `api.py` forwards these dicts to the UI as they are, and this
    project has already sent a `set` and a 100 KB `bytes` down that pipe.

    `rows[]` is the audit trail, one entry per template code, always:

        {"codigo", "codigo_hex", "estado_actual", "rol": [names]|None,
         "accion": "ligar"|"apagar"|"respetada"|"omitida",
         "motivo": str,                       # measured, never a shrug
         "nombre", "nombre_original", "k2", "cmd_id", "dev_id",   # if ligar
         "cmd_actual", "k1_actual", "k2_actual"}                  # if bound

    A key that ends up unbound says so and says WHY -- which is the whole
    point: the alternative to a wrong binding is not silence, it is a
    readable reason.

    ## The two summary counts are not the same number

    `roles_ligados_ahora` is what THIS run binds; `roles_with_command` is how
    many of the 34 roles have a command on the page once the rows already
    there are counted. On a second (idempotent) run the first is 0/34 and the
    second is unchanged -- collapsing them into one made a correct no-op look
    like a total failure.
    """
    TM.set_t6(b)
    n5 = D.read_section5(b)
    if not 0 <= k1 < len(n5):
        raise ValueError(
            "device %d does not exist in section [5] (there are %d)" % (k1, len(n5))
        )

    menu = TA.device_screen(b, hub)
    info = menu.get(k1) or {}
    if screen is None:
        screen = info.get("screen")
    if screen is None:
        raise ValueError(
            "device %d has no page in the Devices menu and none was given "
            "(the menu resolves %s)" % (k1, sorted(menu))
        )

    avisos: list[str] = []
    if commands is None and config is not None:
        commands, source = commands_from_config(config, device)
    elif commands is None:
        commands, source = commands_from_blob(b, k1, hub)
    else:
        source = "given by the caller"
    n_cmd = n5[k1].get("n", 0)
    out_of_range = sorted(k2 for k2 in commands if not 0 <= k2 < n_cmd)
    if out_of_range:
        avisos.append(
            "%d names come with a k2 outside device %d's %d commands (%s): "
            "dropped -- an out-of-range (k1,k2) hangs the remote"
            % (len(out_of_range), k1, n_cmd, out_of_range[:8])
        )
        commands = {k2: v for k2, v in commands.items() if 0 <= k2 < n_cmd}
    if not commands:
        avisos.append(
            "no command of device %d could be named (%s): nothing gets bound"
            % (k1, source)
        )

    # name -> the k2 that carry it, canonicalized
    by_role: dict[str, list[int]] = {}
    sin_rol: list[tuple[int, str]] = []
    for k2 in sorted(commands):
        name = commands[k2]
        can = canonico(name)
        if can is None:
            sin_rol.append((k2, name))
            continue
        by_role.setdefault(can, []).append(k2)

    plantilla, template_source = plantilla_codigos(b)
    try:
        tr, current_rows = TF._screen_header(b, screen)
    except ValueError as exc:
        raise ValueError("device %d's page: %s" % (k1, exc)) from exc
    current_state = {}
    dest11 = relocate.table(b, relocate.sections(b)[11][0])
    for _k, cod, campo, idv, cls in current_rows:
        if cls == 0 and idv == 0:
            current_state[cod] = ("declarada y apagada", None, campo)
        elif cls != TAG_OBJ or not 0 <= idv < len(dest11):
            current_state[cod] = ("otra clase", None, campo)
        else:
            forma, cmd, _dev, _pag, _rs = TF._forma(b, dest11, idv)
            if forma in ("directo", "indirecto") and cmd is not None:
                current_state[cod] = ("asignada", cmd, campo)
            else:
                current_state[cod] = ("forma no reconocida", None, campo)

    respetar = {int(c) for c in respetar}
    dev_id = TM._dev_id_de(b, dest11, k1)

    rows: list[dict] = []
    changes: list[dict] = []
    roles = dict(ROLES)
    for cod in plantilla:
        state, cmd_hoy, _campo = current_state.get(cod, ("sin fila", None, None))
        f: dict = {
            "codigo": cod,
            "codigo_hex": "0x%02X" % cod,
            "current_state": state,
            "rol": list(roles.get(cod, ())) or None,
        }
        if cmd_hoy is not None:
            f["cmd_actual"] = cmd_hoy
            f["k1_actual"] = cmd_hoy >> 8
            f["k2_actual"] = cmd_hoy & 0xFF

        # ---- (1) codes auto is not allowed to touch at all ---------------
        if cod in TF.CODIGOS_INFRAESTRUCTURA:
            f.update(
                accion="omitida",
                reason="page infrastructure (%s): touching it costs the LEDs "
                "or the internal pager"
                % ("internal pager" if cod == 0x2D else "enter/leave hook"),
            )
            rows.append(f)
            continue

        # ---- (2) rule 3: whatever is already the user's stays the user's --
        if cod in respetar:
            f.update(accion="respetada", reason="the caller reserved this code")
            rows.append(f)
            continue
        if state == "asignada" and not forzar:
            f.update(
                accion="respetada",
                reason="already bound to (device %d, command %d): a binding "
                "already in the page is never overwritten -- pass forzar=True "
                "to" % (cmd_hoy >> 8, cmd_hoy & 0xFF),
            )
            rows.append(f)
            continue
        if state not in ESTADOS_LIBRES and state != "asignada":
            f.update(
                accion="omitida",
                reason="the row is in state %r: not written, so as not to guess"
                % state,
            )
            rows.append(f)
            continue

        # ---- (3) is there a command for this key, and which one? ----------
        nombres = tuple(roles.get(cod, ()))
        if cod in TF.CODIGOS_TACTILES:
            k2 = None
            reason = (
                "touchscreen zone: it is bound per LCD page in the slot's own "
                "key register (teclas_mapa), never in the page header -- the "
                "factory declares all 8 of them disabled here"
            )
        elif cod in CODIGOS_APAGADOS_DE_FABRICA:
            k2 = None
            reason = (
                "the factory declares this code disabled on all three of its "
                "device pages (it binds 0x9B/0x9D for the d-pad instead)"
            )
        elif not nombres:
            k2, reason = None, "no factory role is measured for this code"
        else:
            k2, reason = None, None
            for name in nombres:
                cands = by_role.get(name)
                if not cands:
                    continue
                k2, explanation = elegir_k2(b, k1, cands)
                if k2 is not None:
                    f["name"] = name
                    f["original_name"] = commands[k2]
                    reason = "%r -> %s (%s)" % (commands[k2], name, explanation)
                    break
                reason = "%s: %s" % (name, explanation)
            if k2 is None and reason is None:
                reason = "device %d has no command named %s" % (
                    k1,
                    " / ".join(nombres),
                )

        # ---- (4) bind, disable, or leave alone ---------------------------
        if k2 is not None:
            cmd_id = (k1 << 8) | k2
            reg, m5 = D.resolve_section5(b, cmd_id)
            if reg is None:
                # unreachable here (`elegir_k2` filters on the same call), and
                # kept anyway: an out-of-range (k1,k2) is the one mistake that
                # hangs the remote, so it gets two independent refusals.
                f.update(
                    accion="omitida",
                    reason="(device %d, command %d) does not resolve through "
                    "section [5]: %s -- writing it would hang the remote"
                    % (k1, k2, m5),
                )
            else:
                f.update(
                    accion="ligar",
                    k2=k2,
                    cmd_id=cmd_id,
                    dev_id=dev_id,
                    reason=reason,
                )
                changes.append(
                    {
                        "screen": screen,
                        "codigo": cod,
                        "k1": k1,
                        "k2": k2,
                        "dev_id": dev_id,
                    }
                )
        elif not apagar_lo_no_ligado:
            f.update(accion="omitida", reason=reason)
        elif state == "declarada y apagada":
            f.update(
                accion="respetada",
                reason="already declared and disabled -- %s" % reason,
            )
        else:  # estado == "sin fila"
            f.update(
                accion="apagar",
                reason="%s; declared DISABLED rather than left out, so the "
                "press dies here instead of falling through to the global "
                "keymap and jumping to page 146" % reason,
            )
            changes.append({"screen": screen, "codigo": cod, "apagar": True})
        rows.append(f)

    # rule 1, checked and not merely intended: two codes may not end up on the
    # same command. `ROLES` makes it impossible by construction (no name is
    # claimed by two codes), which is exactly why it is worth measuring: if it
    # ever stops holding, it stops here and not on the remote.
    used: dict[int, list[int]] = {}
    for c in changes:
        if "k2" in c:
            used.setdefault(c["k2"], []).append(c["codigo"])
    repetidos = {k2: v for k2, v in used.items() if len(v) > 1}
    if repetidos:
        raise ValueError(
            "the plan would bind one command to several keys: %s"
            % {k2: ["%#04x" % c for c in v] for k2, v in repetidos.items()}
        )

    ligadas = [f for f in rows if f["accion"] == "ligar"]
    return {
        "k1": k1,
        "screen": screen,
        "name": info.get("name") or "device %d" % k1,
        "dev_id": dev_id,
        "source": source,
        "plantilla": template_source,
        "n_commands": n_cmd,
        "n_nombrados": len(commands),
        # the exact inputs, carried along so a re-plan (check (n), and the UI
        # showing a preview before applying) uses the SAME ones and not a
        # freshly guessed set: an idempotence check fed different input is
        # not an idempotence check.
        "commands": dict(commands),
        "respetar": sorted(respetar),
        "apagar_lo_no_ligado": bool(apagar_lo_no_ligado),
        "forzar": bool(forzar),
        "sin_rol": sin_rol,
        "rows": rows,
        "changes": changes,
        "avisos": avisos,
        "trailer": "0x%06X" % tr["off"],
        "header": "0x%06X" % (tr["hdr"] - BASE),
        "summary": {
            "ligar": len(ligadas),
            "apagar": sum(1 for f in rows if f["accion"] == "apagar"),
            "respetada": sum(1 for f in rows if f["accion"] == "respetada"),
            "omitida": sum(1 for f in rows if f["accion"] == "omitida"),
            # two different numbers, kept apart on purpose: what this run
            # BINDS, and how many of the 34 roles end up with a command on
            # the page once the rows already there are counted in. Collapsing
            # them made a second (idempotent) run look like it had covered 0.
            "roles_ligados_ahora": "%d/%d" % (len(ligadas), len(ROLES)),
            "roles_with_command": "%d/%d"
            % (
                sum(
                    1
                    for f in rows
                    if f["accion"] == "ligar"
                    or (f["accion"] == "respetada" and f.get("cmd_actual") is not None)
                ),
                len(ROLES),
            ),
            "commands_without_role": len(sin_rol),
        },
    }


def apply(b: bytes, plan: dict) -> tuple[bytes, list[int], list[dict]]:
    """Writes the plan. `(new blob, repoints, detail)`.

    The detail comes back already tagged `kind="device"` -- which is the
    key `teclas_alcance.alcanzado()` dispatches on, and the one every caller
    of `apply_device` has had to remember to add by hand.
    """
    if not plan.get("changes"):
        return b, [], []
    out, repuntes, detail = TF.apply_device(b, plan["changes"])
    return out, repuntes, [dict(d, kind="device") for d in detail]


def auto_asignar(b: bytes, k1: int, **kw) -> tuple[bytes, list[int], list[dict], dict]:
    """`planificar` + `apply` in one call. `(out, repoints, detail, plan)`."""
    plan = planificar(b, k1, **kw)
    out, repuntes, detail = apply(b, plan)
    return out, repuntes, detail, plan


# ==========================================================================
# 6. CHECKS
# ==========================================================================


def checks(
    b: bytes, out: bytes, plan: dict, detail: list[dict], repuntes: list[int]
) -> list[dict]:
    """The battery for an auto-assignment, on top of the two the writer
    already has (`keys_physical.device_checks` and
    `teclas_alcance.checks`, both run here).

    The ones this module adds are the ones a wrong TABLE would survive:

      (k) every bound key REACHES the command whose NAME its role asks for --
          re-read from the new blob by the firmware's own walk, and the name
          re-derived from the blob, not taken from the plan;
      (l) no `(k1,k2)` on the whole page is out of range in section `[5]`
          (the thing that hangs the remote), counted over every reachable
          pair, not only the ones this plan touched;
      (m) the rows the plan called `respetada` are byte-identical afterwards;
      (n) re-planning over the result asks for nothing more (idempotent).
    """
    ch: list[dict] = []
    if detail:
        ch += TF.device_checks(b, out, detail, repuntes)
        ch += TA.checks(b, out, detail)

    screen = plan["screen"]
    k1 = plan["k1"]

    # (k) NAME IN, NAME OUT. Walk the firmware's own path over the new blob,
    #     see which (k1,k2) the key lands on, and ask what that command is
    #     CALLED -- twice, from two sources that do not share a code path:
    #
    #       * the plan's own `{k2: name}` map, which is the specification of
    #         what this device's ordinals mean;
    #       * the name re-derived from the NEW blob (decode the command's IR
    #         waveform, match `(protocol,payload)` against the Hub's
    #         `DeviceList`), which knows nothing about the plan.
    #
    #     Both have to name the role. If the two sources name DIFFERENT roles
    #     for the same command, that is a genuine "cannot be matched with
    #     confidence" and it goes red rather than being averaged away -- the
    #     Hub and a device's own JSON really do use different vocabularies
    #     (the LG's JSON says `ChannelPrev` where the Hub says `PrevChannel`),
    #     and absorbing that is what SINONIMOS is for, not what a check is.
    roles = dict(ROLES)
    n5_out = nombres_seccion5(out)
    mapa = plan.get("commands") or {}
    malos = []
    for f in plan["rows"]:
        if f["accion"] != "ligar":
            continue
        r = TA.on_screen(out, screen, f["codigo"])
        alcanzado_k2 = r.get("k2")
        plan_name = mapa.get(alcanzado_k2)
        blob_name = n5_out.get(k1, {}).get(alcanzado_k2)
        esperados = roles.get(f["codigo"], ())
        correct = (
            r.get("cmd_id") == f["cmd_id"]
            and r.get("k1") == k1
            and r.get("registro5") is not None
            and canonico(plan_name or "") == f["name"]
            and (blob_name is None or canonico(blob_name) in esperados)
        )
        if not correct:
            malos.append(
                (
                    f["codigo_hex"],
                    "role=%s" % (esperados,),
                    "plan=%r" % plan_name,
                    "blob=%r" % blob_name,
                    TA._said(r),
                )
            )
    ch.append(
        {
            "name": "(k) every bound key reaches the command its ROLE names",
            "ok": not malos,
            "detail": "%d keys: the walk lands on a command that BOTH the "
            "plan's name map and the name re-derived from the new blob call "
            "the role's" % plan["summary"]["ligar"]
            if not malos
            else "role and reached command disagree: %s" % malos[:6],
        }
    )

    # (l) NOTHING out of range, over the whole page and not only the plan.
    TM.set_t6(out)
    n5 = D.read_section5(out)
    out_rows = TA.register_rows(
        out,
        D.read_trailer(out, D.u24(out, D.T6 + 3 + 3 * screen) - BASE, max_n=200)[
            "hdr"
        ]
        - BASE,
    )
    dest11 = relocate.table(out, relocate.sections(out)[11][0])
    pares, outside = [], []
    for _k, cod, _campo, idv, cls in out_rows:
        if cls != TAG_OBJ or not 0 <= idv < len(dest11):
            continue
        for cmd, _dev in TA.object_commands(out, dest11, idv):
            pares.append((cod, cmd >> 8, cmd & 0xFF))
            reg, reason = D.resolve_section5(out, cmd)
            if reg is None:
                outside.append(("0x%02X" % cod, cmd >> 8, cmd & 0xFF, reason))
            elif not (0 <= (cmd >> 8) < len(n5)) or not (
                0 <= (cmd & 0xFF) < n5[cmd >> 8].get("n", 0)
            ):
                outside.append(
                    ("0x%02X" % cod, cmd >> 8, cmd & 0xFF, "outside [5]'s counts")
                )
    ch.append(
        {
            "name": "(l) no (k1,k2) on the page is out of range in [5]",
            "ok": not outside,
            "detail": "%d reachable (k1,k2) pairs on page %d, %d distinct, all "
            "resolve through the firmware's own arithmetic; [5] declares %s"
            % (
                len(pares),
                screen,
                len({(a, c) for _b2, a, c in pares}),
                [d.get("n", 0) for d in n5],
            )
            if not outside
            else "OUT OF RANGE (this hangs the remote): %s" % outside[:8],
        }
    )

    # (m) RULE 3, measured. Every row that was already in the page and that
    #     the plan did NOT claim has to come out byte-identical -- the manual
    #     bindings the plan called `respetada`, the `06`/`07`/`2D` hooks, and
    #     a `B7` the page happened to carry (auto adds rows, never removes).
    before = _page_rows(b, screen)
    after = {f[1]: (f[3], f[4]) for f in out_rows}
    reclamadas = {
        f["codigo"] for f in plan["rows"] if f["accion"] in ("ligar", "apagar")
    }
    intactas = [cod for cod in before if cod not in reclamadas]
    movidas = ["0x%02X" % cod for cod in intactas if after.get(cod) != before[cod]]
    ch.append(
        {
            "name": "(m) every row auto did not claim is byte-identical",
            "ok": not movidas,
            "detail": "the page had %d rows; auto claimed %d of them and the "
            "other %d (%s) come out identical"
            % (
                len(before),
                len(before) - len(intactas),
                len(intactas),
                " ".join("0x%02X" % c for c in intactas),
            )
            if not movidas
            else "moved without being asked: %s" % movidas,
        }
    )

    # (n) IDEMPOTENT, which is rule 3 stated the other way round: what auto
    #     writes, auto has to respect on the next run. If it did not, every
    #     re-sync would silently undo whatever the user had changed since.
    try:
        plan2 = planificar(
            out,
            k1,
            commands=plan.get("commands"),
            screen=screen,
            respetar=plan.get("respetar", ()),
            apagar_lo_no_ligado=plan.get("apagar_lo_no_ligado", True),
        )
        resto = plan2["changes"]
        summary2 = plan2["summary"]
    except ValueError as exc:  # noqa: BLE001
        resto, summary2 = [("planificar failed", str(exc))], {}
    ch.append(
        {
            "name": "(n) running auto again asks for nothing more",
            "ok": not resto,
            "detail": "the second plan has 0 changes: %s" % summary2
            if not resto
            else "it would write %d more: %s" % (len(resto), resto[:6]),
        }
    )
    return ch


def _page_rows(b: bytes, screen: int) -> dict[int, tuple[int, int]]:
    """`{code: (id, class)}` of a page's key register, reached the way the
    firmware reaches it (master index -> tabla[6] -> trailer -> hdr)."""
    TM.set_t6(b)
    tr = D.read_trailer(b, D.u24(b, D.T6 + 3 + 3 * screen) - BASE, max_n=200)
    return {f[1]: (f[3], f[4]) for f in TA.register_rows(b, tr["hdr"] - BASE)}


def check_template_vs_blob(b: bytes, hub=None) -> dict:
    """Re-derives `ROLES` from `b`'s own factory pages and compares.

    This is what keeps the frozen table from drifting away from the hardware
    it was read off. It needs the Hub's `DeviceList.json` to name the
    commands; without it, it reports that and does not pretend to have run.
    """
    path = pathlib.Path(str(hub) if hub else str(TM.HUB_VOCAB))
    if not path.exists():
        return {
            "ok": None,
            "reason": "the Hub DeviceList is not at %s: the names cannot be "
            "re-derived, so the table was NOT re-checked" % path,
        }
    n5 = nombres_seccion5(b, path)
    medidos = roles_medidos(b, n5)
    esperado = {cod: list(nn) for cod, nn in ROLES}
    dif = []
    for cod, nn in sorted(esperado.items()):
        if medidos.get(cod) != nn:
            dif.append(("0x%02X" % cod, nn, medidos.get(cod)))
    vacios = sorted(
        "0x%02X" % c for c, nn in medidos.items() if not nn and c in esperado
    )
    return {
        "ok": not dif,
        "n_roles": len(esperado),
        "diferencias": dif,
        "no_name_in_this_blob": vacios,
        "reason": "the %d roles re-derived from this blob's factory pages are "
        "identical to the frozen table" % len(esperado)
        if not dif
        else "the table and the blob disagree: %s" % dif,
    }


# ==========================================================================
# CLI
# ==========================================================================


def _imprimir(plan: dict) -> None:
    print(
        "device k1=%d (%s) -> page %d   %d commands, %d named   [%s]"
        % (
            plan["k1"],
            plan["name"],
            plan["screen"],
            plan["n_commands"],
            plan["n_nombrados"],
            plan["source"],
        )
    )
    print("template: %s" % plan["plantilla"])
    for a in plan["avisos"]:
        print("  ! %s" % a)
    for f in plan["rows"]:
        print(
            "  %s %-9s %-14s %s"
            % (
                f["codigo_hex"],
                f["accion"],
                (f.get("name") or "") if f["accion"] == "ligar" else "",
                ("k2=%-3d " % f["k2"] if f.get("k2") is not None else "")
                + (f.get("reason") or ""),
            )
        )
    print("summary: %s" % plan["summary"])


def main() -> int:  # pragma: no cover -- manual inspection
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("--k1", type=int, required=True)
    ap.add_argument("--screen", type=int)
    ap.add_argument("--config", help="hub-config-with-device.json for the names")
    ap.add_argument("--device")
    ap.add_argument("--no-apagar", action="store_true")
    ap.add_argument("--forzar", action="store_true")
    ap.add_argument("--salida", help="write the new blob (only if every check is OK)")
    ap.add_argument("--json", help="dump the plan")
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    if b[:4] != b"GSPM":
        print("the blob does not start with GSPM", file=sys.stderr)
        return 1
    plan = planificar(
        b,
        a.k1,
        screen=a.screen,
        config=a.config,
        device=a.device,
        apagar_lo_no_ligado=not a.no_apagar,
        forzar=a.forzar,
    )
    _imprimir(plan)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(plan, indent=1, default=str))
        print("plan -> %s" % a.json)
    if not plan["changes"]:
        print("nothing to write")
        return 0
    out, repuntes, detail = apply(b, plan)
    ch = checks(b, out, plan, detail, repuntes)
    width = max(len(c["name"]) for c in ch)
    print()
    for c in ch:
        print(
            "  %-*s %-6s %s"
            % (width, c["name"], "OK" if c["ok"] else "FAIL", c["detail"][:150])
        )
    todo = all(c["ok"] for c in ch)
    print(
        "\nblob: %d B -> %d B (+%d)  repoints: %s"
        % (len(b), len(out), len(out) - len(b), [hex(r) for r in repuntes])
    )
    print("VERDICT: %s" % ("fit to grab" if todo else "ABORTED"))
    if todo and a.salida:
        pathlib.Path(a.salida).write_bytes(out)
        print("written %s" % a.salida)
    return 0 if todo else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
