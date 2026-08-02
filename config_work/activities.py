#!/usr/bin/env python3
"""The Harmony One's ACTIVITIES: who lists them, which devices they use,
and how it's checked that a deletion doesn't break them.

READS ONLY. Writes nothing, doesn't touch USB. This is the module
`delete_device.py` uses for its check (f) and to tell the user, in plain
sentences, what is lost.

DEVICE NAMES IN THE EXAMPLES BELOW ARE GENERIC (`TV`, `DVR`, `Home`).
On a real remote they are whatever that user's Hub called each device, and
nothing in this module hardcodes them: `device_prefixes()` reads them out
of the blob being processed. The measurements are real; the names standing
in for the devices are not anyone's in particular.

## What an activity is, in the blob

Three pieces, all three measured on `backups/config_raw.bin` (factory):

  1. **The activities menu**: a `table[6]` screen whose rows, instead of
     resolving to `{ordinal,0x7E}` (which is what a Devices menu row
     does -- "open screen N"), resolve to `{0xFF00|ord, 0x1F}`. At the
     factory it's `table[6][44]`, template K=7, with 2 rows: `TV HD`
     (`0xFF07`) and `PC` (`0xFF08`). Its right softkey goes back to the
     Devices menu (`{74,0x7E}`), which is the positive check that it's
     THE screen paired with the devices menu.

  2. **`All Off`** (`0xFF09`) is NOT in that menu: it lives in object 513
     (`{0xff09,0x1F} {0xf101,0x3F} {0x213,0x7F}`), hanging off a PHYSICAL
     key. That's why the menu declares 2 rows but there are 3 activities.

  3. **The engine**: section `[14]`, `8 + 3*n` bytes -- `<u16 n><u16><u16><u16>`
     and `n` 24-bit pointers to records
     `<u16 initial><u16 limit><u16 count><u8 pad>` + `count` 8 B entries,
     each `<u8 flags><u16 from><u16 to><u16 value><u8 tag>`. Entries with
     `tag=0x7F` are `table[11]` indices, i.e. objects: following them
     reaches `{cmd_id,0x7D}`, and that `cmd_id`'s high byte is the
     device's `k1`.

     **THE VARIABLE'S IDENTITY IS THE POINTER'S INDEX, NOT A FIELD.**
     `engine_records()` historically calls the first `u16` `vid` and
     the second `value`; the names are WRONG and are kept only because
     `delete_device.py` already consumes them (it uses `entradas`, not those two
     fields). The first `u16` is the INITIAL VALUE and the second is the
     LIMIT. The sentence that used to be here -- "the `vid`s match
     EXACTLY with section [0]'s ids" -- is FALSE: the first `u16` of the
     45 records are `{0,1,2,3,4,5,7,19,21,23}` and `[0]`'s ids are
     `{20,21,23,31,33,34,35,39,42}`; that record 1 starts at 23 (=0x17)
     and record 2 at 21 (=0x15) is numeric coincidence in two cases.

     CHECK of the true mapping `index == id from [0]`, run over
     `backups/config_raw.bin` (FULL population: the 9 named properties,
     not a sample): the name's `_N` suffix has to give `limit == N-1`.

         shift  0  ->  9/9      <- the real mapping
         shift -2  ->  5/9
         shift -3, +1, +3 -> 3/9 ; shift -1 -> 1/9 ; shift +2 -> 0/9
         2000 random permutations of the Ns: 3 reach 9/9  (p = 0.0015)

     POSITIVE CHECK of the layout: 42/44 intra-cluster records have their
     computed length `7 + 8*count` EXACTLY equal to the distance to the
     next record. NEGATIVE CHECKS (fake layouts): `7+7n` gives 30/44,
     `7+9n` gives 30/44, `8+8n` gives 0/44, `6+8n` gives 0/44. The layout
     is fixed by measurement, not chosen. (The 33 records with `count=0`
     don't distinguish `7+7n` from `7+8n` from `7+9n`: the informative
     pairs are 12.)

  4. **The keyboard context table**: section `[10]`'s first bytes are
     `<u8 10><10 x ptr24>`, indexed by ACTIVITY ORDINAL. Each context is
     `<u8 n><n x {u8 code, u16 id, u8 tag}>`; if `n == 0` it's followed by
     `<u8 m><m x {u8 flags, u8 code, u16 id, u8 tag}>` (long form, the one
     All Off uses). Check for the short form: `1 + 4*39 = 157` is exactly
     the distance from `ctx[7]` to `ctx[8]`, and `1 + 4*41 = 165` the one
     from `ctx[8]` to `ctx[9]`. The reserved codes are `0x01` ENTER, `0x02`
     LEAVE, `0x05` REFRESH, and their `{id, 0x7F}` is a `table[11]`
     object: 2066/2067/2068 (ordinal 7), 2070/2071/2072 (8), 2074/-/2075
     (9, no LEAVE).

## Which devices each activity uses -- NOW POSSIBLE, from data

`attribution()` closes the whole chain and `activity_devices()`
returns it per activity:

    menu row {0xFF|ord,0x1F}  ->  section[10][ord]  ->  ENTER hook
    ->  slots with `tag >= 0x80` = SET(property = tag & 0x7F, value)
    ->  record `[14][property]`  ->  the transition whose `to` is that
        value  ->  its atom `{idx,0x7F}` -> `{cmd_id,0x7D}` -> `k1 = cmd_id>>8`

CONTINGENCY that SEPARATES, measured on the factory blob (the 12 `[14]`
records with transitions):

                              reaches a command   doesn't reach
    id named in [0]                  5                  0
    id unnamed in [0]                0                  7

Fisher's exact test, one-tailed, `1/C(12,5) = 1/792 = 0.00126`, unrounded.

CROSS-CHECK WITH AN INDEPENDENT ORACLE (not the name again): each reached
command's IR waveform is decoded with `irscan` and looked up by
`(protocol, payload)` in the same user's Hub `DeviceList.json`. It gives,
5 out of 5, the same device the property's name says:

    DVR_Power_2 -> k1=1 -> Toshiba 32 Bit 0xFFCC33 -> Hub: DVR / PowerToggle
    TV_Power_2           -> k1=0 -> Sony 12 Bit 0x750/0xF50 -> Hub: TV / PowerOn, PowerOff
    TV_Input_5           -> k1=0 -> Sony 15 Bit x5          -> Hub: TV / InputHdmi1, Netflix, InputHdmi2, InputHdmi3, InputHdmi4
    Home_Input_8         -> k1=2 -> Sony 12 Bit x8          -> Hub: Home / InputVideo1, InputVideo2, InputLd, InputTv/Sat, InputMd/Tape, InputCd, InputTuner, Input5.1Ch
    Home_Power_2         -> k1=2 -> Sony 12 Bit 0x741/0xF41 -> Hub: Home / PowerOn, PowerOff

And the two activities' VALUES match against the Hub's
`ActivityList.json`, 3 out of 3: TV HD `TV_Input=0` -> `InputHdmi1` <->
Hub `"HDMI 1"`; PC `TV_Input=4` -> `InputHdmi4` <-> Hub `"HDMI 4/MHL"`;
both `Home_Input=2` -> `InputLd` <-> Hub `"DVD/LD"`.

## What's still OPEN, and isn't dressed up

  * **All Off (ordinal 9)**: its ENTER (#2074) writes TEN SETs and NONE
    of them is a named device property -- just `0x13, 0x19, 0x1b, 0x1c,
    0x20, 0x24, 0x25, 0x29`, none with transitions. By this chain its
    attribution is the EMPTY set, which is implausible for something
    called "turn everything off". The real mechanism (does the engine
    reset every property to its `initial` value on activity change?
    another path?) was NOT measured. `attribution()` returns
    `determinado=False` for that ordinal and the UI has to say "could not
    be determined", never "none".
  * **`TV_OnlinePower_2` (id 0x2A)**: the name says TV, record `[14][42]`
    has 0 transitions. The data path can neither confirm nor deny it.
    `[ASSUMED]` to be the TV's, by the name.
  * **The 8 anonymous properties** the ENTERs write (`0x13, 0x19, 0x1b,
    0x1c, 0x20, 0x24, 0x25, 0x29`): they appear in SETs, `[0]` doesn't
    name them and none reaches any command. Unknown semantics.
  * **Underdetermination from n=3**: the mechanism is measured over 2
    activities with a populated context and 1 degenerate one. At the
    PROPERTY level the population is complete (9/9) and there's no
    sampling bias there; at the ACTIVITY level there's no third one to
    check that the pattern generalizes.

`activity_screens()` still exists -- it's the `table[6]` screens
that MIX devices (43 and 142 at the factory) -- but it is NO LONGER the
attribution criterion: it's a screen heuristic and it doesn't even agree
with the chain (142 mixes `{0,2}` while activity PC comes out `{0,1,2}`
because its ENTER turns off the DVR too).
"""

from __future__ import annotations

import pathlib
import struct
import sys

import add_device as D
import relocate

#: slot tag marking "reference to an activity / engine variable", as
#: opposed to `0x7E` (screen ordinal) and `0x7F` ([11] object).
TAG_ACTIVIDAD = 0x1F
#: high byte of the `{0xFF00|ordinal, 0x1F}` slot a ROW of the activities
#: menu uses. Measured: 0xFF07 / 0xFF08 in factory menu 44, and 0xFF09
#: (All Off) outside the menu, in object 513.
ALTO_FILA_ACTIVIDAD = 0xFF
#: tag of the device/screen slot of a Devices menu row.
TAG_ORDINAL = 0x7E
TAG_OBJETO = 0x7F
TAG_CMD = 0x7D
#: `tag >= 0x80` in a slot = direct SET of property `tag & 0x7F`.
TAG_SET = 0x80
#: reserved codes of the keyboard context table (section [10]).
COD_ENTER, COD_LEAVE, COD_REFRESH = 0x01, 0x02, 0x05
#: wildcards for a transition's `start`/from field. `0xFFFE` = any.
DESDE_CUALQUIERA = 0xFFFE
DESDE_CAMBIO = 0xFFFD
#: ordinal of the activity that has NO menu row (hangs off physical key
#: 0xA5). Only used to label it; attribution doesn't hardcode it.
ORDINAL_ALL_OFF = 9
NOMBRE_ALL_OFF = "All Off"


def _u16(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


# ---------------------------------------------------------------- engine ---


def engine_records(b: bytes) -> list[dict]:
    """Section `[14]`'s records, already parsed.

    `[{'off','vid','value','cuantos','entradas':[(raw5, slot, tag), ...]}]`.
    Aborts if the section doesn't have the measured `8 + 3*n` shape --
    rather than silently returning garbage (if the format were something
    else, `delete_device.py`'s check (f) would be looking at noise and could go
    green by accident).
    """
    sec = relocate.sections(b)
    if 14 not in sec:
        raise SystemExit("the blob doesn't declare section [14] (activity engine)")
    a, z = sec[14]
    s = b[a:z]
    if len(s) < 8:
        raise SystemExit("section [14] measures %d B: too short" % len(s))
    n = _u16(s, 0)
    if len(s) != 8 + 3 * n:
        raise SystemExit(
            "section [14] measures %d B but declares %d pointer(s) "
            "(8+3*%d=%d): doesn't have the measured shape, aborting "
            "instead of interpreting noise" % (len(s), n, n, 8 + 3 * n)
        )
    out = []
    for i in range(n):
        p = s[8 + 3 * i] | (s[9 + 3 * i] << 8) | (s[10 + 3 * i] << 16)
        o = p - D.BASE
        if not 0 <= o < len(b) - 7:
            raise SystemExit("section [14] has a pointer outside the blob (%#08x)" % p)
        vid, value, cuantos = struct.unpack_from("<HHH", b, o)
        if o + 7 + 8 * cuantos > len(b):
            raise SystemExit(
                "section [14]'s record %d (%#08x) declares %d entries "
                "and they don't fit in the blob" % (i, o, cuantos)
            )
        entradas = []
        for j in range(cuantos):
            blk = b[o + 7 + 8 * j : o + 15 + 8 * j]
            entradas.append((blk[:5], _u16(blk, 5), blk[7]))
        out.append(
            {
                "off": o,
                "vid": vid,
                "value": value,
                "cuantos": cuantos,
                "entradas": entradas,
            }
        )
    return out


def engine_layout_check(b: bytes) -> tuple[int, int, dict[str, int]]:
    """`(hits, total, negatives)` for the `7 + 8*count` layout.

    This is the POSITIVE+NEGATIVE check for the parser above, run over
    whatever blob is at hand: how many records end exactly where the next
    one starts, against what three FAKE layouts would give. If the real
    layout doesn't separate from the fake ones, the parser isn't
    measuring anything.
    """
    regs = engine_records(b)
    offs = sorted(r["off"] for r in regs)
    by_off = {r["off"]: r for r in regs}
    total = 0
    hits = 0
    negatives = {"7+7n": 0, "7+9n": 0, "8+8n": 0, "6+8n": 0}
    for i, o in enumerate(offs[:-1]):
        nxt = offs[i + 1]
        if nxt - o > 4096:  # cluster jump: not contiguous, doesn't count
            continue
        total += 1
        c = by_off[o]["cuantos"]
        if o + 7 + 8 * c == nxt:
            hits += 1
        for etq, (h, w) in (
            ("7+7n", (7, 7)),
            ("7+9n", (7, 9)),
            ("8+8n", (8, 8)),
            ("6+8n", (6, 8)),
        ):
            if o + h + w * c == nxt:
                negatives[etq] += 1
    return hits, total, negatives


def engine_k1(b: bytes, dest11: list[int] | None = None) -> tuple[set[int], int]:
    """`(set of k1 the activity engine can reach, objects visited)`.

    Transitive closure from section `[14]`'s `tag=0x7F` entries, following
    `table[11]` through `0x7F` slots until reaching `{cmd_id,0x7D}`. Same
    arithmetic `reubicar.chain()` and `listar.k1_of_screen()` already
    walk, with a different seed.
    """
    if dest11 is None:
        dest11 = relocate.table(b, relocate.sections(b)[11][0])
    stack = [
        slot
        for r in engine_records(b)
        for _raw, slot, tag in r["entradas"]
        if tag == TAG_OBJETO
    ]
    seen: set[int] = set()
    k1: set[int] = set()
    while stack:
        i = stack.pop()
        if i in seen or not 0 <= i < len(dest11):
            continue
        seen.add(i)
        for v, t in D._slots(b, dest11[i]) or []:
            if t == TAG_CMD:
                k1.add(v >> 8)
            elif t == TAG_OBJETO:
                stack.append(v)
    return k1, len(seen)


def engine_commands(b: bytes, dest11: list[int] | None = None) -> set[int]:
    """The concrete `cmd_id`s the engine can emit. What `delete_device.py`'s
    check (f) re-resolves one by one against section [5]."""
    if dest11 is None:
        dest11 = relocate.table(b, relocate.sections(b)[11][0])
    stack = [
        slot
        for r in engine_records(b)
        for _raw, slot, tag in r["entradas"]
        if tag == TAG_OBJETO
    ]
    seen: set[int] = set()
    cmds: set[int] = set()
    while stack:
        i = stack.pop()
        if i in seen or not 0 <= i < len(dest11):
            continue
        seen.add(i)
        for v, t in D._slots(b, dest11[i]) or []:
            if t == TAG_CMD:
                cmds.add(v)
            elif t == TAG_OBJETO:
                stack.append(v)
    return cmds


# ------------------------------------------------- the activities menu ---


def activities_menu(b: bytes, dest11: list[int] | None = None) -> dict | None:
    """The `table[6]` object that LISTS the activities, or `None`.

    Criterion (not the hardcoded ordinal 44): a screen whose key register
    has at least one zone resolving to an object with a `{0xFF0X, 0x1F}`
    slot -- the shape an activity row has, and that NO Devices menu row
    has (those carry `{ordinal,0x7E}`).

    Returns `{'ordinal','filas':[{'y','off_texto','codigo','id','act'}],
    'prog','keyreg','slot','trailer'}` with the rows IN GEOMETRIC ORDER.
    """
    if dest11 is None:
        dest11 = relocate.table(b, relocate.sections(b)[11][0])
    n6 = D.u16(b, D.T6)
    for k in range(n6):
        t = D.u24(b, D.T6 + 3 + 3 * k) - D.BASE
        if not 0 <= t < len(b) or b[t] != 0x00:
            continue
        tr = D.read_trailer(b, t, max_n=200)
        if tr is None:
            continue
        s = D.read_slot(b, tr["slots"][0] - D.BASE)
        if s is None:
            continue
        kr = D.read_key_register(b, s["keyreg"] - D.BASE) or []
        rows = []
        for cod, ident, cls in kr:
            if cls != TAG_OBJETO or not 0 <= ident < len(dest11):
                continue
            rs = D._slots(b, dest11[ident]) or []
            acts = [
                v
                for v, tg in rs
                if tg == TAG_ACTIVIDAD and (v >> 8) == ALTO_FILA_ACTIVIDAD
            ]
            if acts:
                rows.append({"codigo": cod, "id": ident, "act": acts[0] & 0xFF})
        if not rows:
            continue
        return {
            "ordinal": k,
            "trailer": t,
            "slot": tr["slots"][0] - D.BASE,
            "prog": s["prog"] - D.BASE,
            "keyreg": s["keyreg"] - D.BASE,
            "K": s["K"],
            "rows": rows,
        }
    return None


def activity_names(b: bytes, decode, dest11: list[int] | None = None) -> dict:
    """`{activity_ordinal: name}` reading the menu's program.

    `decode(ptr) -> (text, complete)` is `listar.make_decoder`'s
    glyph decoder (names are glyph indices, not ASCII). INLINE names
    (`TXTIN`, opcode 0x05 -- how the factory stores "PC") are decoded with
    the same table, from the embedded bytes.
    """
    m = activities_menu(b, dest11)
    if m is None:
        return {}
    ins = D.disassemble(b, m["prog"])
    dibujadas = []
    for _off, op, ar in ins:
        if op == "TXT" and ar[0] == D.TAG_NAME:
            txt, _ok = decode(ar[2])
            dibujadas.append((ar[1], txt))
        elif op == "TXTIN" and ar[0] == D.TAG_NAME:
            txt, _ok = decode(None, ar[2])
            dibujadas.append((ar[1], txt))
    dibujadas.sort()
    # the template's reading order gives each row's zone code; the row
    # drawn k-th from top to bottom uses code k -- the same geometric
    # invariant `device.read_extra_rows` already requires.
    plant = D.read_section19(b)
    order = D.template_buttons(plant.get(m["K"], []))
    by_code = {f["codigo"]: f["act"] for f in m["rows"]}
    presentes = [c for c in order if c in by_code]
    if len(presentes) != len(dibujadas):
        return {}
    return {by_code[c]: nom for c, (_and_join, nom) in zip(presentes, dibujadas)}


# ------------------------------------------------ activity screens (the mixed ones) ---


def activity_screens(b: bytes, dest11: list[int] | None = None) -> list[dict]:
    """The `table[6]` screens that MIX devices.

    The project's own criterion (PLAN.md): a commands screen belongs to
    ONE device; the only ones that mix `k1` are activity screens. Returns
    `[{'ordinal', 'k1': [...]}, ...]`.
    """
    import list_devices  # noqa: PLC0415 -- avoids the listar -> actividades cycle

    if dest11 is None:
        dest11 = relocate.table(b, relocate.sections(b)[11][0])
    out = []
    for k in range(D.u16(b, D.T6)):
        ks = list_devices.k1_of_screen(b, dest11, k)
        if len(ks) > 1:
            out.append({"ordinal": k, "k1": sorted(ks)})
    return out


# =====================================================================
# ATTRIBUTION FROM DATA: which device each activity touches
# =====================================================================


def keyboard_contexts(b: bytes) -> list[int]:
    """The offsets of the 10 keyboard contexts (section [10]'s header).

    `<u8 n><n x ptr24>`, indexed by ACTIVITY ORDINAL. Aborts if the header
    doesn't have that shape instead of returning noise.
    """
    sec = relocate.sections(b)
    if 10 not in sec:
        raise SystemExit("the blob doesn't declare section [10]")
    a, z = sec[10]
    n = b[a]
    if not 1 <= n <= 64 or a + 1 + 3 * n > z:
        raise SystemExit(
            "section [10]'s header declares %d context(s) and they don't "
            "fit in %d B: doesn't have the measured shape" % (n, z - a)
        )
    out = []
    for i in range(n):
        p = D.u24(b, a + 1 + 3 * i) - D.BASE
        if not 0 <= p < len(b) - 1:
            raise SystemExit("section [10]'s context %d falls outside the blob" % i)
        out.append(p)
    return out


def context_hooks(b: bytes, off: int) -> dict[int, tuple[int, int]]:
    """`{code: (id, tag)}` of a keyboard context.

    SHORT form `<u8 n><n x {u8 code, u16 id, u8 tag}>`; if `n == 0`, LONG
    form `<u8 0><u8 m><m x {u8 flags, u8 code, u16 id, u8 tag}>` -- the one
    All Off uses. The short form is fixed by offset arithmetic (`1+4*39`
    and `1+4*41` give exactly the distances between ctx 7, 8 and 9); the
    long one, by content: its two `0x01`/`0x05` rows resolve to objects
    2074/2075, which decode into a coherent vector of SETs.
    """
    n = b[off]
    out: dict[int, tuple[int, int]] = {}
    if n:
        for j in range(n):
            o = off + 1 + 4 * j
            if o + 4 > len(b):
                break
            out[b[o]] = (_u16(b, o + 1), b[o + 3])
        return out
    m = b[off + 1] if off + 1 < len(b) else 0
    for j in range(m):
        o = off + 2 + 5 * j
        if o + 5 > len(b):
            break
        out[b[o + 1]] = (_u16(b, o + 2), b[o + 4])
    return out


def named_properties(b: bytes) -> dict[int, str]:
    """`{id: name}` from section [0] (`blob_records`), leaves only."""
    try:
        import blob_records  # noqa: PLC0415
    except Exception:  # noqa: BLE001 -- attribution still runs without names
        return {}
    return {r.ident: r.name for r in blob_records.scan(b) if r.type == 1}


def object_sets(
    b: bytes,
    dest11: list[int],
    ident: int,
    vistos: set[int] | None = None,
) -> list[tuple[int, int]]:
    """`[(property, value), ...]` of an object and its nested ones (`tag=0x7F`).

    A slot with `tag >= 0x80` is a direct SET: `property = tag & 0x7F`.
    """
    if vistos is None:
        vistos = set()
    out: list[tuple[int, int]] = []
    if ident in vistos or not 0 <= ident < len(dest11):
        return out
    vistos.add(ident)
    for v, t in D._slots(b, dest11[ident]) or []:
        if t >= TAG_SET:
            out.append((t & 0x7F, v))
        elif t == TAG_OBJETO:
            out.extend(object_sets(b, dest11, v, vistos))
    return out


def _cmds_from_object(b: bytes, dest11: list[int], ident: int) -> list[int]:
    stack, seen, cmds = [ident], set(), []
    while stack:
        i = stack.pop()
        if i in seen or not 0 <= i < len(dest11):
            continue
        seen.add(i)
        for v, t in D._slots(b, dest11[i]) or []:
            if t == TAG_CMD:
                cmds.append(v)
            elif t == TAG_OBJETO:
                stack.append(v)
    return cmds


def transitions_of(b: bytes, pid: int, regs: list[dict] | None = None) -> list[dict]:
    """Record `[14][pid]`'s transitions, already with `flags/start/end`."""
    if regs is None:
        regs = engine_records(b)
    if not 0 <= pid < len(regs):
        return []
    out = []
    for crudo, slot, tag in regs[pid]["entradas"]:
        out.append(
            {
                "flags": crudo[0],
                "start": _u16(crudo, 1),
                "end": _u16(crudo, 3),
                "atomo": slot,
                "tag": tag,
            }
        )
    return out


def command_of_value(
    b: bytes,
    dest11: list[int],
    pid: int,
    value: int,
    regs: list[dict] | None = None,
) -> int | None:
    """The `cmd_id` writing `value` to property `pid` emits, or `None`.

    The transition whose `end`/to is that value is used. `start`/from
    can be the `0xFFFE` wildcard (any) or a literal -- with a literal, the
    transition only fires coming from that value, so the command is the
    one for the change, not for the state.
    """
    for tr in transitions_of(b, pid, regs):
        if tr["end"] != value or tr["tag"] != TAG_OBJETO:
            continue
        cmds = _cmds_from_object(b, dest11, tr["atomo"])
        if cmds:
            return cmds[0]
    return None


def property_k1(
    b: bytes,
    dest11: list[int],
    pid: int,
    regs: list[dict] | None = None,
) -> set[int]:
    """The `k1`s property `pid`'s transitions can emit."""
    out: set[int] = set()
    for tr in transitions_of(b, pid, regs):
        if tr["tag"] != TAG_OBJETO:
            continue
        for c in _cmds_from_object(b, dest11, tr["atomo"]):
            out.add(c >> 8)
    return out


def attribution(b: bytes, dest11: list[int] | None = None) -> dict[int, dict]:
    """`{ordinal: {...}}` -- which device each activity touches, FROM DATA.

    One entry per keyboard context that has an ENTER hook. Fields:

      `k1`           list of device indices the activity emits to
      `determinado`  `False` if ENTER writes NO property with
                     transitions -- All Off's case, where the chain
                     doesn't explain the behavior and the UI must NOT say
                     "none" (see the module docstring)
      `sets`         `[{propiedad, id, value, k1, cmd_id, gancho}]` from
                     ENTER and REFRESH, with the property already named
                     if `[0]` names it
      `ganchos`      `{'enter','leave','refresh'}` -> object id or `None`
    """
    if dest11 is None:
        dest11 = relocate.table(b, relocate.sections(b)[11][0])
    regs = engine_records(b)
    nombres = named_properties(b)
    ctxs = keyboard_contexts(b)
    out: dict[int, dict] = {}
    for ordinal, off in enumerate(ctxs):
        g = context_hooks(b, off)
        if COD_ENTER not in g:
            continue
        ganchos = {}
        for etq, cod in (
            ("enter", COD_ENTER),
            ("leave", COD_LEAVE),
            ("refresh", COD_REFRESH),
        ):
            par = g.get(cod)
            ganchos[etq] = par[0] if (par and par[1] == TAG_OBJETO) else None
        sets = []
        k1: set[int] = set()
        for etq in ("enter", "refresh"):
            if ganchos[etq] is None:
                continue
            for pid, value in object_sets(b, dest11, ganchos[etq]):
                trs = transitions_of(b, pid, regs)
                ks = property_k1(b, dest11, pid, regs)
                cmd = command_of_value(b, dest11, pid, value, regs)
                k1 |= ks
                sets.append(
                    {
                        "gancho": etq,
                        "id": pid,
                        "propiedad": nombres.get(pid),
                        "value": value,
                        "limite": regs[pid]["value"] if pid < len(regs) else None,
                        "n_transiciones": len(trs),
                        "k1": sorted(ks),
                        "cmd_id": cmd,
                    }
                )
        # `determinado` is DELIBERATELY conservative: if the chain reaches
        # NO command at all, "this activity uses no device" can't be told
        # apart from "the chain doesn't cover this activity". With 3
        # activities (and only one in that state) there's nothing to tell
        # the two cases apart with, so it's reported as NOT determined and
        # the UI says so.
        out[ordinal] = {
            "ordinal": ordinal,
            "k1": sorted(k1),
            "determinado": bool(k1),
            "sets": sets,
            "ganchos": ganchos,
        }
    return out


def activity_devices(
    b: bytes, ordinal: int, dest11: list[int] | None = None
) -> tuple[list[int], bool]:
    """`([k1...], determined)` for ONE activity. See `attribution()`."""
    a = attribution(b, dest11).get(ordinal)
    if a is None:
        return [], False
    return a["k1"], a["determinado"]


def activities_using(b: bytes, index: int, dest11: list[int] | None = None) -> dict:
    """Which activities use device `index`, and which are unknown.

    `{'usan': [ordinals], 'no_usan': [...], 'sin_determinar': [...]}`.
    """
    usan, no_usan, sin_det = [], [], []
    for ordinal, a in sorted(attribution(b, dest11).items()):
        if not a["determinado"]:
            sin_det.append(ordinal)
        elif index in a["k1"]:
            usan.append(ordinal)
        else:
            no_usan.append(ordinal)
    return {"usan": usan, "no_usan": no_usan, "sin_determinar": sin_det}


def device_prefixes(b: bytes) -> dict[str, int]:
    """`{name prefix: k1}` read from THIS blob's own device menu.

    A named property in section `[0]` is called `<device>_<what>_<n>`, with
    the device name exactly as the menu shows it and spaces turned into
    underscores: a device the menu lists as `DVR` gives properties
    named `DVR_Power_2`.

    Which name goes with which `k1` is **derived from the blob being read**,
    never hardcoded: the source is `listar.menu_rows()`, the same menu
    the remote draws. That makes this work on any remote (yours has other
    devices than whoever wrote this) and keeps nobody's device list baked
    into the source.

    Returns `{}` -- and the callers degrade to "path A says nothing" --
    when the menu can't be read or the names don't decode, instead of
    guessing. A `{}` here shows up as an honest 0/0, not as a false match.
    """
    import list_devices  # noqa: PLC0415 -- avoids the listar -> actividades cycle

    try:
        list_devices.set_t6(b)
        decode, _warning = list_devices.make_decoder(b, list_devices.DEFAULT_HUB)
        dest11 = relocate.table(b, relocate.sections(b)[11][0])
        zones19 = D.read_section19(b)
        menus = [o["ordinal"] for o in D.menu_objects(b)]
        if not menus:
            return {}
        rows = list_devices.menu_rows(b, menus[0], decode, dest11, zones19)
    except Exception:  # noqa: BLE001 -- path A is the WEAK path; it may be silent
        return {}
    out: dict[str, int] = {}
    for f in rows:
        name = (f.get("name") or "").strip()
        if not name or f.get("incomplete_glyphs") or f.get("k1") is None:
            continue
        out[name.replace(" ", "_")] = f["k1"]
    return out


def k1_by_name(name: str | None, prefijos: dict[str, int]) -> int | None:
    """Which `k1` a property name belongs to, by its prefix. LONGEST prefix
    wins, so `TV` and `TV_Box` can coexist without the shorter one
    swallowing the longer one's properties."""
    if not name or not prefijos:
        return None
    mejor: tuple[int, int] | None = None
    for pre, k1 in prefijos.items():
        if name.startswith(pre) and (mejor is None or len(pre) > mejor[0]):
            mejor = (len(pre), k1)
    return None if mejor is None else mejor[1]


def gold_check(b: bytes, dest11: list[int] | None = None) -> dict:
    """The TWO paths, cross-checked, with the contingency and its negative check.

    Path A: the `[0]` name's prefix says the device.
    Path B: `[14][id]`'s transitions reach a `k1`.

    INFORMATIVE rows (the ones with transitions: where the two paths
    actually speak) are reported separately from the ones that aren't --
    a row with 0 transitions crosses nothing, and counting it as
    "matches" inflates the result. Also the shift-test for the
    `index == id` mapping.
    """
    if dest11 is None:
        dest11 = relocate.table(b, relocate.sections(b)[11][0])
    prefijos = device_prefixes(b)
    regs = engine_records(b)
    nombres = named_properties(b)
    rows = []
    for pid, nom in sorted(nombres.items()):
        trs = transitions_of(b, pid, regs)
        ks = sorted(property_k1(b, dest11, pid, regs))
        rows.append(
            {
                "id": pid,
                "name": nom,
                "n_transiciones": len(trs),
                "k1": ks,
                "limite": regs[pid]["value"] if pid < len(regs) else None,
            }
        )
    informativas = [f for f in rows if f["n_transiciones"]]
    # contingency over the records with transitions: named x reaches a command
    con_trans = [i for i, r in enumerate(regs) if r["cuantos"] and i < len(regs)]
    a = b_ = c = d = 0
    for pid in con_trans:
        named = pid in nombres
        reaches = bool(property_k1(b, dest11, pid, regs))
        if named and reaches:
            a += 1
        elif named:
            b_ += 1
        elif reaches:
            c += 1
        else:
            d += 1

    # shift-test: name's _N suffix against `limite == N-1`
    def _n(nom):
        try:
            return int(nom.rsplit("_", 1)[1])
        except Exception:  # noqa: BLE001
            return None

    shifts = {}
    for sh in (-3, -2, -1, 0, 1, 2, 3):
        ok = tot = 0
        for pid, nom in sorted(nombres.items()):
            N = _n(nom)
            if N is None:
                continue
            tot += 1
            j = pid + sh
            if 0 <= j < len(regs) and regs[j]["value"] == N - 1:
                ok += 1
        shifts[sh] = (ok, tot)
    return {
        "rows": rows,
        "informativas": len(informativas),
        # path A agrees with path B: the name's prefix names a device the
        # MENU also lists, and the transitions reach some k1. The prefixes
        # come from `device_prefixes(b)` -- this blob's own menu -- so no
        # device list is baked into this file.
        "coinciden": sum(
            1
            for f in informativas
            if f["k1"] and k1_by_name(f["name"], prefijos) is not None
        ),
        "contingencia": {
            "named_with_command": a,
            "named_without_command": b_,
            "unnamed_with_command": c,
            "unnamed_without_command": d,
        },
        "shifts": shifts,
    }


# --------------------------------------------------- what the user is told ---


def report(b: bytes, index: int, decode=None) -> dict:
    """Everything `delete_device.py` and the app need to know about activities
    BEFORE deleting device `index`.

    Decides nothing destructive: it describes. `usan_el_aparato` is `True`
    if the engine (section [14]) can reach commands for that `k1`.
    """
    dest11 = relocate.table(b, relocate.sections(b)[11][0])
    k1, n_obj = engine_k1(b, dest11)
    aciertos, total, negativos = engine_layout_check(b)
    m = activities_menu(b, dest11)
    nombres = {}
    if decode is not None:
        try:
            nombres = activity_names(b, decode, dest11)
        except Exception:  # noqa: BLE001 -- an unreadable name can't flip the deletion
            nombres = {}
    screens = activity_screens(b, dest11)
    afectadas = [p for p in screens if index in p["k1"]]
    # REAL ATTRIBUTION (the one that replaces "name them all"). If the data
    # model can't be read on this blob, it falls back to the old --
    # conservative -- behavior instead of under-reporting.
    try:
        atrib = attribution(b, dest11)
        quien = activities_using(b, index, dest11)
    except Exception:  # noqa: BLE001
        atrib, quien = {}, None
    return {
        "engine_k1": sorted(k1),
        "engine_objects": n_obj,
        "layout_ok": aciertos,
        "layout_total": total,
        "layout_negativos": negativos,
        "menu_ordinal": None if m is None else m["ordinal"],
        "activities_in_menu": [] if m is None else [f["act"] for f in m["rows"]],
        "nombres": nombres,
        "activity_screens": screens,
        "affected_screens": [p["ordinal"] for p in afectadas],
        "usan_el_aparato": index in k1,
        "attribution": {str(k): v for k, v in atrib.items()},
        "quien_lo_usa": quien,
    }


def activity_name(inf: dict, ordinal: int) -> str:
    """What an activity is called, to show a person.

    The name comes from the menu; the ordinal with NO row (All Off) has
    no name IN THE BLOB, so it's labeled and it's made clear the label is
    ours.
    """
    nom = (inf.get("nombres") or {}).get(ordinal) or (inf.get("nombres") or {}).get(
        str(ordinal)
    )
    if nom:
        return nom
    if ordinal == ORDINAL_ALL_OFF:
        return NOMBRE_ALL_OFF
    return "activity %d" % ordinal


def _and_join(nombres: list[str]) -> str:
    if len(nombres) == 1:
        return nombres[0]
    return " and ".join([", ".join(nombres[:-1]), nombres[-1]])


def human_sentences(inf: dict, device_name: str, n_commands: int) -> list[str]:
    """WHAT IS LOST, in plain sentences, without a single technical word.

    This is the only thing the app shows the user about this topic: it
    doesn't ask anything, it INFORMS. Returns a list of sentences.

    Names ONLY the activities that actually use the device -- resolved by
    `activities_using()`, i.e. `ENTER -> SETs -> [14][id] -> k1`. It
    used to name every menu row, whether it used the device or not. If the
    data chain can't say anything about some activity (today: `All Off`,
    whose ENTER writes no device property), it is said EXACTLY THAT way,
    instead of assuming "doesn't use it".
    """
    frases = [
        "%s disappears from the device list, with its %d commands and its "
        "screen." % (device_name, n_commands)
    ]
    quien = inf.get("quien_lo_usa")
    if quien is None:
        # no readable attribution: falls back to the old, conservative behavior
        if not inf.get("usan_el_aparato"):
            frases.append(
                "No activity uses this device, so your activities stay "
                "exactly as they are today."
            )
            return frases
        frases.append(
            "Some of your activities use this device. Your activities are "
            "NOT deleted and will keep working the same way. What you lose "
            "is being able to control it by hand from the device list."
        )
        return frases

    usan = [activity_name(inf, a) for a in quien["usan"]]
    dudosas = [activity_name(inf, a) for a in quien["sin_determinar"]]
    if usan:
        uno = len(usan) == 1
        frases.append(
            "%s use%s this device. %s NOT deleted and will keep working "
            "the same way: %s still send%s it the same commands. What you "
            "lose is being able to control it by hand from the device list."
            % (
                _and_join(usan),
                "s" if uno else "",
                "It is" if uno else "They are",
                "it" if uno else "they",
                "s" if uno else "",
            )
        )
    else:
        frases.append(
            "None of your activities send commands to this device, so "
            "your activities stay exactly as they are today."
        )
    if dudosas:
        frases.append(
            "For %s there is no way to know: it doesn't store anywhere "
            "which devices it touches, so I can neither confirm nor rule "
            "it out." % _and_join(dudosas)
        )
    return frases


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python3 activities.py <blob.bin> [index]")
        return 2
    b = pathlib.Path(sys.argv[1]).read_bytes()
    D.T6 = D.u24(b, D.MAESTRO_T6) - D.BASE
    index = int(sys.argv[2]) if len(sys.argv) > 2 else -1
    import list_devices  # noqa: PLC0415

    decode, _warning = list_devices.make_decoder(b, list_devices.DEFAULT_HUB)

    def dec(ptr, inline=None):
        if inline is not None:
            return "".join(
                list_devices.glyphs.extender(b, set())[0].get(c, "?") for c in inline
            ), True
        return decode(ptr)

    inf = report(b, index, dec)
    print(
        "engine (section [14]): %d object(s) reached, k1 = %s"
        % (inf["engine_objects"], inf["engine_k1"])
    )
    print(
        "   layout 7+8n: %d/%d exact; negatives %s"
        % (inf["layout_ok"], inf["layout_total"], inf["layout_negativos"])
    )
    print(
        "activities menu: ordinal %s, rows %s, names %s"
        % (inf["menu_ordinal"], inf["activities_in_menu"], inf["nombres"])
    )
    print("activity screens (the ones that mix k1): %s" % inf["activity_screens"])
    if index >= 0:
        print(
            "device %d %s used by the engine"
            % (index, "IS" if inf["usan_el_aparato"] else "is NOT")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
