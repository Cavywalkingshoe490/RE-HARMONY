#!/usr/bin/env python3
"""Moves config sections to the end of the blob so they can be grown.

To add a device you have to insert entries into three sections that sit **in
the middle** of the blob: `[9]` (pages), `[10]` (objects) and `[11]` (the
global object table). Extending them where they are would shift ~1.1 MB, and
shifting is precisely what cannot be done safely: 90,461 positions in the
blob contain a u24 that falls in range, and almost all of them are
coincidence.

The way out is to run nothing at all: **copy the section to the end,
extended, and change its entry in the master index**. Every DECLARED pointer
into the moved range then needs the same delta applied -- see
`declared_pointers()` / `references_into()` below:

    section [ 9]  pages     0x0291e7   <- master index + 206 from tabla[6]'s
                                          `slot.keyreg` field (see next)
    section [10]  objects   0x029caf   <- master index + 588 from table[11]
    section [11]  table     0x02b1aa   <- master index only

The 588 into [10] are not a problem because they live **inside table [11]**,
which is relocated too and is therefore rewritten whole.

## A cross-reference this file used to miss entirely

`table[6]` (section [6]'s body) walks `entry -> trailer -> slot -> keyreg`,
and `keyreg` is an ABSOLUTE pointer into section [9] -- the firmware's real
path to a screen's key bindings (see `config_work/keys_map.py`, which
diagnosed this independently). An earlier version of this docstring claimed
to have enumerated "3,630 pointers from known structures (master index +
table[11]'s 2,904 entries + every command record's 3 pointers)" and
concluded section [9] took master-index references only. That count never
walked `table[6]`, so it undercounted: measured on the factory blob,
`table[6]`'s 206 `keyreg` fields **all** land inside section [9] -- 206
pointers this file didn't know existed. Left unrepointed, they still resolve
after a move, just **to the dead copy the old bytes leave behind**: measured
on `output/config_empaquetada.bin` (today's grabbed config, produced
by this file's old, unrepaired `relocate()`), only 14 of 226 `keyreg`
pointers resolve inside the live section [9]; the other 212 resolve into
stale, unreachable bytes. Editing a factory screen's keys through that path
is silently inert -- the write lands, but the firmware never reads it.

## Repointing needs a MAP, not a delta -- a retracted claim

A first version of this fix applied ONE uniform delta per section
(`where[i] - sec[i][0]`) to every reference. That is only valid if the
section's body is copied unchanged, or grown **at the end**. It is not what
`add_device()` does: `hook_at_root()` inserts 4 bytes **in the middle** of
section [9] (measured on the factory blob: page ordinal 1, local offset
0x14, i.e. file offset 0x2091fb), so every byte after that point shifts by
+4 relative to the section start, and a uniform delta lands 4 bytes short.

Measured, running `add_device(config_raw.bin, 5, n_comandos=6,
reparar_referencias=True)` with the uniform delta:

    keyregs resolving inside the live [9]        206/206   <- control said GREEN
    keyregs seeing the SAME record as before       0/206
    keyregs whose record is exactly +4 further   202/206
    new targets whose first byte isn't a page     164/206

So the "fix" was **worse than the bug**: unrepointed, the 206 keyregs point
at a dead copy that is byte-identical to the factory [9], so the stale
pointers are INERT. Repointed with a uniform delta they are ACTIVELY WRONG.
The old control could not see it, because "no declared pointer lands in the
vacated zone" is satisfied by a pointer aimed at the *wrong place inside the
live zone*. **That claim -- that `add_device(..., reparar_referencias=True)`
was an end-to-end proof of the forward path -- is retracted.** The checks it
rested on (`chain()` plus the dead-zone sweep) never walk `table[6]` and
were blind to the defect by construction.

What is here now:

  * `relocate()` takes `mapas`, a per-section **piecewise relocation map**
    (`[(old_local_ini, old_local_fin, delta), ...]`) saying where each run of
    old bytes ended up. `hook_at_root()` returns its insertion point and
    `add_device()` turns it into a map, so targets past the insertion get
    `delta + 4` and targets before it get `delta`.
  * With no map, a section body is accepted only if it is the old body
    **extended at the end** (`fresh[:len(old)] == old`). Anything else
    raises instead of silently applying a delta that cannot be right.
  * The permanent control is now about **content**: for every declared
    pointer, the bytes seen through it after relocating must be the same
    bytes it saw before. The dead-zone sweep is kept as a complement, not as
    the proof. The content check is what caught the off-by-4 above.

## The fix is opt-in, on purpose

`relocate(..., reparar_referencias=True)` finds and repoints every declared
external reference into [9]/[10]/[11] -- `table[6]`'s chain included, plus a
`pointers.enumerar()` sweep as a safety net for anything else declared
elsewhere in the blob -- and **ABORTS** (raises `RuntimeError`, does not
warn) if any repointed pointer stops seeing the bytes it used to see, or if
any declared pointer still resolves into the vacated range.

It defaults to **False**. `app/check_load_bearing.py`'s anchor
(`976bc70edd15b40f56cb49aa5113594f`) is the blob **grabbed and running on
the user's remote today**, produced by the OLD, unrepaired path -- so this
file must never change what it emits by DEFAULT: that would silently move
the anchor to a blob nobody has flashed or verified, in a project whose
whole discipline is "verify running, don't assume" and whose remote is
currently disconnected (can't reverify on hardware right now). Fixing what's
on the device is a separate, deliberate step -- regenerate with
`reparar_referencias=True`, get a NEW md5, flash it, THEN move the anchor to
that new value -- not a side effect of editing this file.

## The proof

`--nulo` relocates all three **without changing a single byte** and then
walks the entire chain of the 198 buttons again over the resulting blob. If
the relocator broke something, that chain stops resolving. It is the same
discipline as the rest of the project: the rewrite that changes nothing has
to come out identical before attempting the one that does change something.
`--reparar` additionally asserts that every repointed pointer still sees the
same bytes AND that zero declared pointers land in the vacated range -- pass
it to see today's 206-pointer gap close to 0.

`--forward` is the control that actually exercises the path that matters: it
runs `add_device()` (which edits [9] in the middle) both ways and compares,
record by record, what `table[6]`'s keyregs resolve to. This is the control
the retracted claim above should have had.

**Writes nothing to the device.**

Usage:
    python3 relocate.py <blob.bin> --nulo
    python3 relocate.py <blob.bin> --nulo --salida nuevo.bin
    python3 relocate.py <blob.bin> --nulo --reparar   # closes the tabla[6] gap
    python3 relocate.py <blob.bin> --forward          # the add_device control
"""

from __future__ import annotations

import argparse
import pathlib

BASE = 0x040000
N_SECCIONES = 20
OBJECT_TABLE = 11
# **Order matters and it is not numeric.** Measured over the 156 original
# pages: the physical keys always appear as `b2 b3 b0 b1 b4 b5`, without a
# single exception (59 pages of 6 keys, 8 of 4, 2 of 5), and the first one is
# `0xb2` in 79 pages. The pages this used to generate used `b0 b1 b2 b3 b4
# b5`, an order that **does not appear even once** in the blob -- probably
# why they did not draw: the order is the on-screen position, not the key
# code.
PHYSICAL_KEYS = (0xB2, 0xB3, 0xB0, 0xB1, 0xB4, 0xB5)
# The full order, with the two labels that are not physical keys up front.
# **Any field added to an existing page goes in its position in this list,
# not at the end**: appending at the end left 22 of 40 pages with the order
# broken, and the remote showed nothing. Use `orden_de_campos()` to respect
# it.
CANONICAL_ORDER = (0xAB, 0xAC, 0xB2, 0xB3, 0xB0, 0xB1, 0xB4, 0xB5)
TIPO_IR_POR_DEFECTO = 0x0FCA


def sections(b: bytes) -> dict[int, tuple[int, int]]:
    """{index: (start, end)} of the master index's sections.

    A section ends where the next one starts; the last one, at the close.
    """
    ptr = {}
    for i in range(N_SECCIONES):
        o = 0x0C + 4 * i
        v = int.from_bytes(b[o : o + 4], "little")
        if v:
            ptr[i] = v - BASE
    close = int.from_bytes(b[4:7], "little") - BASE
    cortes = sorted(set(ptr.values())) + [close - 2]
    return {i: (p, next(x for x in cortes if x > p)) for i, p in ptr.items()}


def table(b: bytes, at: int) -> list[int]:
    """The global object table's entries: <u16 count><count x ptr24>."""
    n = int.from_bytes(b[at : at + 2], "little")
    return [
        int.from_bytes(b[at + 2 + 3 * k : at + 5 + 3 * k], "little") - BASE
        for k in range(n)
    ]


def build_table(dest: list[int]) -> bytes:
    out = bytearray(len(dest).to_bytes(2, "little"))
    for d in dest:
        out += (BASE + d).to_bytes(3, "little")
    return bytes(out)


def _u24(b: bytes, o: int) -> int:
    return int.from_bytes(b[o : o + 3], "little")


def table6_chain(
    b: bytes, sec: dict[int, tuple[int, int]]
) -> list[tuple[int, int, str]]:
    """Every u24 pointer FIELD in the `table[6]` -> trailer -> slot chain.

    `table[6]` is section [6]'s body: `<u16 count><pad 00><count x ptr24>`.
    Each entry points at a trailer `<flag u8><ptr24 hdr><u16 N><N x ptr24
    slot>`; each slot is `<K u8><ptr24 keyreg><ptr24 prog>` (7 B) -- the same
    formats `device.read_trailer`/`read_slot` already use to walk the
    firmware's real path. Reimplemented locally, not imported: `device`
    imports `reubicar`, so importing it back here would cycle.

    Returns `(field_offset, target_offset, label)`. Every field returned came
    from a shape that was validated structurally (count/N/K in range, enough
    bytes left) before its ptr24 was read -- the same rule `pointers.py`
    documents: "a pointer is declared only if the structure that contains
    it says that field is a pointer". Coincidence is not a risk here.

    A trailer whose slot count does not fit in the blob is skipped. That is
    fail-safe rather than silent: any keyreg missed this way keeps pointing
    into the vacated range, and `relocate()`'s reachability control turns
    that into an abort. The bound is the blob's own size, not a measured
    constant -- an earlier version capped it at 200 because that is what
    THIS user's config happens to hold (measured max: 10 in the factory
    blob, 11 in the grabbed one), which is a fact about one remote, not
    about the format.
    """
    if 6 not in sec:
        return []
    a6 = sec[6][0]
    if a6 + 2 > len(b):
        return []
    n = int.from_bytes(b[a6 : a6 + 2], "little")
    out: list[tuple[int, int, str]] = []
    for i in range(n):
        eo = a6 + 3 + 3 * i
        if eo + 3 > len(b):
            break
        t = _u24(b, eo) - BASE
        out.append((eo, t, "tabla[6] entry -> trailer"))
        if not (0 <= t and t + 6 <= len(b)):
            continue
        nslots = int.from_bytes(b[t + 4 : t + 6], "little")
        if nslots < 1 or t + 6 + 3 * nslots > len(b):
            continue
        out.append((t + 1, _u24(b, t + 1) - BASE, "tabla[6] trailer hdr"))
        for k in range(nslots):
            so = t + 6 + 3 * k
            s = _u24(b, so) - BASE
            out.append((so, s, "tabla[6] trailer slot -> slot record"))
            if not (0 <= s and s + 7 <= len(b)):
                continue
            out.append((s + 1, _u24(b, s + 1) - BASE, "tabla[6] slot keyreg"))
            out.append((s + 4, _u24(b, s + 4) - BASE, "tabla[6] slot prog"))
    return out


def declared_pointers(
    b: bytes, sec: dict[int, tuple[int, int]]
) -> dict[int, tuple[int, int, str]]:
    """Every pointer FIELD the blob's known structures declare as one,
    whatever its target -- `{field_offset: (target_offset, byte_width,
    etiqueta)}`.

    `sec` must be `sections(b)` (or equivalent) **for this exact blob**: it's
    used to locate table[11] and `table[6]` correctly, not to filter by
    target -- filtering happens in `references_into()`.

    Union of:
      * the master index's entries (4 B each, at `0x0C + 4*i`)
      * table [11]'s entries (3 B each)
      * `table6_chain()` -- the chain nobody enumerated before this fix
      * `pointers.enumerar()`'s generic structural sniffers (pointer tables,
        index tables, enumeration tables), as a safety net for anything
        declared elsewhere in the blob that isn't one of the above three
    """
    import pointers  # local: avoids a needless import for callers of relocate()

    out: dict[int, tuple[int, int, str]] = {}

    for i in range(N_SECCIONES):
        o = 0x0C + 4 * i
        v = int.from_bytes(b[o : o + 4], "little")
        if v:
            out[o] = (v - BASE, 4, "indice maestro [%d]" % i)

    if 11 in sec:
        dest = table(b, sec[11][0])
        for k, d in enumerate(dest):
            out[sec[11][0] + 2 + 3 * k] = (d, 3, "tabla[11]")

    for fo, t, etq in table6_chain(b, sec):
        out[fo] = (t, 3, etq)

    for o, (t, etq) in pointers.enumerar(b).items():
        out.setdefault(o, (t, 3, etq))

    return out


def references_into(
    b: bytes, sec: dict[int, tuple[int, int]], rangos: dict[int, tuple[int, int]]
) -> dict[int, tuple[int, int, int, str]]:
    """The subset of `declared_pointers(b, sec)` whose target falls inside one
    of `rangos` -- `{field_offset: (target, byte_width, id_rango, label)}`.

    `rangos` need not be `sec`'s own entries: it can be numeric ranges a
    section **used to** occupy, to check for stale references after a move
    (`sections()` on the moved blob can no longer see the vacated bytes as a
    section at all -- the master index doesn't point there any more).
    """

    def inside(v: int) -> int | None:
        for i, (a, z) in rangos.items():
            if a <= v < z:
                return i
        return None

    out: dict[int, tuple[int, int, int, str]] = {}
    for o, (t, w, etq) in declared_pointers(b, sec).items():
        i = inside(t)
        if i is not None:
            out[o] = (t, w, i, etq)
    return out


def orphaned(
    refs: dict[int, tuple[int, int, int, str]], zones: dict[int, tuple[int, int]]
) -> dict[int, tuple[int, int, int, str]]:
    """Drops entries whose OWN storage falls inside one of `zones`.

    After a relocation the old bytes are never erased (see `relocate()`'s
    docstring), so `pointers.enumerar()`'s generic sniffers -- which look at
    shapes, not at reachability -- keep re-discovering the dead copies of
    table[11]/`table[6]` sitting where the live ones used to be. Those hits
    are real structures, but they are themselves **unreachable**: nothing
    in the new master index points at them, so whatever they point AT is
    irrelevant garbage, not a stale live reference. Only fields living
    OUTSIDE `zones` can actually still be walked by the firmware.
    """
    return {
        o: v for o, v in refs.items() if not any(a <= o < z for a, z in zones.values())
    }


# --------------------------------------------------------------------------
# piecewise relocation maps
#
# A map says where every byte of a section's OLD body ended up in its NEW
# body, in LOCAL coordinates (0 == start of the section): a sorted list of
# `(old_ini, old_fin, delta)` runs covering `[0, len(old_body))` with no gaps
# and no overlaps. `old_local + delta` is the new local offset.
#
# One uniform delta is the special case `[(0, len(old), 0)]`, and it is only
# correct when the body was copied unchanged or grown AT THE END. Anything
# edited in the middle -- which is exactly what `hook_at_root()` does -- needs
# more than one run, or every pointer past the edit lands short.

Mapa = list[tuple[int, int, int]]


def append_map(old: bytes, fresh: bytes, editados=None) -> Mapa | None:
    """Identity map, if `fresh` is `old` grown at the END. Else None.

    Old-local ranges listed in `editados` are allowed to differ: an in-place
    byte change does not move anything, so the identity map still describes
    the relocation truthfully. Anything else different means bytes moved
    without a map, and None is the signal that a uniform delta is NOT safe:
    the caller has to supply a real map or stop.
    """
    if len(fresh) < len(old):
        return None
    if _primera_diferencia(old, fresh[: len(old)], 0, list(editados or [])) is None:
        return [(0, len(old), 0)]
    return None


def insertion_map(old_len: int, punto: int, cuantos: int) -> Mapa:
    """Map for `cuantos` bytes inserted at local offset `punto`.

    Everything at or after `punto` shifts by `cuantos`; everything before it
    stays. This is the shape `hook_at_root()` produces.
    """
    if cuantos == 0 or punto >= old_len:
        return [(0, old_len, 0)]
    if punto <= 0:
        return [(0, old_len, cuantos)]
    return [(0, punto, 0), (punto, old_len, cuantos)]


Editados = list[tuple[int, int]]


def _primera_diferencia(
    old: bytes, fresh: bytes, base: int, editados: Editados
) -> int | None:
    """First index where the two differ **without being declared edited**.

    `base` is the old local offset `old[0]` sits at, so the exclusion
    ranges can be expressed in the section's own local coordinates.
    """
    if old == fresh and not editados:
        return None
    for k in range(min(len(old), len(fresh))):
        if old[k] == fresh[k]:
            continue
        if any(ini <= base + k < fin for ini, fin in editados):
            continue
        return k
    return None


def check_map(
    old: bytes,
    fresh: bytes,
    mapa: Mapa,
    label: str,
    contenido: bool = True,
    editados: Editados | None = None,
) -> None:
    """Raises unless `mapa` faithfully describes `old` -> `fresh`.

    Structure is always checked: the runs must be sorted, contiguous, cover
    the whole old body, and land inside the new one. `contenido` additionally
    demands that the bytes actually match run by run -- true for bodies that
    are copied/edited (sections [9] and [10]), false for section [11], whose
    entries are legitimately rewritten in place by `build_table()`.

    `editados` are old-local ranges the caller **declares** it changed on
    purpose (e.g. the key-count byte `hook_at_root()` bumps from 1 to 2).
    Bytes inside them are exempt from the content check; every other
    difference still aborts. Declaring the edit is the point: an undeclared
    change is indistinguishable from a map that does not describe reality.
    """
    editados = list(editados or [])
    esperado = 0
    for ini, fin, d in mapa:
        if ini != esperado:
            raise RuntimeError(
                "relocation map %s: gap or overlap at %#x "
                "(the run was expected to start at %#x)"
                % (label, ini, esperado)
            )
        if fin < ini:
            raise RuntimeError("mapa de reubicacion %s: tramo invalido" % label)
        if ini + d < 0 or fin + d > len(fresh):
            raise RuntimeError(
                "relocation map %s: the run [%#x,%#x)+%d falls outside the "
                "cuerpo nuevo (%d B)" % (label, ini, fin, d, len(fresh))
            )
        if contenido:
            k = _primera_diferencia(
                old[ini:fin], fresh[ini + d : fin + d], ini, editados
            )
            if k is not None:
                raise RuntimeError(
                    "relocation map %s: the run [%#x,%#x)+%d does NOT "
                    "reproduce the old bytes (first difference not "
                    "declared at local offset %#x: %02x -> %02x) -- the "
                    "map does not describe this edit"
                    % (
                        label,
                        ini,
                        fin,
                        d,
                        ini + k,
                        old[ini + k],
                        fresh[ini + d + k],
                    )
                )
        esperado = fin
    if esperado != len(old):
        raise RuntimeError(
            "relocation map %s: covers %d B of the %d in the old body"
            % (label, esperado, len(old))
        )


def map_local(mapa: Mapa, x: int) -> int | None:
    """Old local offset -> new local offset, or None if not covered."""
    for ini, fin, d in mapa:
        if ini <= x < fin:
            return x + d
    return None


def _map_run_end(mapa: Mapa, x: int) -> int:
    """End (old, local) of the run holding `x` -- how far a target's bytes are
    guaranteed contiguous after the move."""
    for ini, fin, _d in mapa:
        if ini <= x < fin:
            return fin
    return x


def relocate(
    b: bytes,
    nuevos: dict[int, bytes] | None = None,
    objetos_extra: list[int] | None = None,
    reparar_referencias: bool = False,
    mapas: dict[int, Mapa] | None = None,
    editados: dict[int, Editados] | None = None,
) -> bytes:
    """Moves [9], [10] and [11] to the end. `nuevos` replaces a section's body.

    `objetos_extra` are offsets **relative to the new body of [10]** that get
    appended to the global table; their ids come right after the ones that
    already existed, which is where `device_objects`'s `id_base`
    comes from.

    `reparar_referencias` (default **False**, see the module docstring for
    why): when True, every declared external pointer into the moved ranges
    -- `table[6]`'s `entry/hdr/slot/keyreg/prog` chain included, not just the
    master index and table[11] this function always fixed -- is translated
    through that section's relocation map. Afterwards it re-reads every one
    of them out of the RESULT and **raises `RuntimeError`** if any of them
    stopped seeing the bytes it used to see, or still resolves into the range
    just vacated. Neither is ever a warning.

    `mapas` gives, per section, the piecewise map of where the old body's
    bytes ended up (see `insertion_map()` / `append_map()`). It is required
    whenever a body in `nuevos` was edited **in the middle**: without it, a
    body that is not simply the old one grown at the end is rejected rather
    than repointed with a delta that cannot be right. `editados` declares,
    per section, the old-local ranges whose CONTENT the caller changed on
    purpose, so the content checks do not fire on them. Both are ignored (as
    is `reparar_referencias`) on the default path, which keeps this
    function's output byte-identical to what produced the anchor.

    Returns the new blob, with the master index, the close and the checksum
    already fixed. The bytes left where the old sections used to be **are
    not erased**: they become unreachable, which is safer than compacting.
    """
    nuevos = nuevos or {}
    mapas = dict(mapas or {})
    editados = dict(editados or {})
    sec = sections(b)
    close = int.from_bytes(b[4:7], "little") - BASE
    out = bytearray(b[: close - 2])

    cuerpo = {
        i: bytes(nuevos.get(i, b[a:z])) for i, (a, z) in sec.items() if i in (9, 10, 11)
    }

    # gathered from the ORIGINAL blob, before anything moves. Empty (and
    # free) unless `reparar_referencias` is set -- the anchor must not pay
    # for, or shift because of, work nobody asked for.
    referencias = (
        references_into(b, sec, {i: sec[i] for i in (9, 10, 11) if i in sec})
        if reparar_referencias
        else {}
    )

    # The relocation maps. Only built when they are going to be USED: on the
    # default path nothing is repointed beyond the master index and table[11],
    # so demanding a map there would abort `add_device()` -- which edits [9]
    # in the middle -- and take the anchor with it.
    mapa: dict[int, Mapa] = {}
    if reparar_referencias:
        for i in (9, 10):
            if i not in sec:
                continue
            old = b[sec[i][0] : sec[i][1]]
            m = mapas.get(i) or append_map(old, cuerpo[i], editados.get(i))
            if m is None:
                raise RuntimeError(
                    "reparar_referencias: the new body of section [%d] "
                    "is NOT the old one extended at the end (%d B -> %d B), and "
                    "`mapas[%d]` was not passed. An edit IN THE MIDDLE shifts "
                    "the bytes after it, so a uniform delta leaves "
                    "every pointer into that zone pointing short -- see the "
                    "module docstring. Pass a map (`insertion_map()`) "
                    "o no pidas reparar." % (i, len(old), len(cuerpo[i]), i)
                )
            check_map(old, cuerpo[i], m, "[%d]" % i, editados=editados.get(i))
            mapa[i] = m

    # [9] and [10] first, so [10]'s final location is known before rewriting the table
    where: dict[int, int] = {}
    for i in (9, 10):
        where[i] = len(out)
        out += cuerpo[i]

    # table [11] points 588 times inside [10]: those get fixed by delta.
    # With a map (only ever present under `reparar_referencias`) the delta is
    # per-run instead; with the identity map the arithmetic is the same one,
    # which is why the default output does not move.
    a10, z10 = sec[10]
    delta10 = where[10] - a10
    dest = table(b, sec[11][0])
    if 10 in mapa:
        traducidos = []
        for d in dest:
            if not (a10 <= d < z10):
                traducidos.append(d)
                continue
            loc = map_local(mapa[10], d - a10)
            if loc is None:
                raise RuntimeError(
                    "reparar_referencias: tabla[11] apunta a %#08x, dentro de "
                    "[10] but outside the relocation map" % d
                )
            traducidos.append(where[10] + loc)
        dest = traducidos
    else:
        dest = [d + delta10 if a10 <= d < z10 else d for d in dest]
    for rel in objetos_extra or []:
        dest.append(where[10] + rel)
    where[11] = len(out)
    out += build_table(dest) if 11 not in nuevos else cuerpo[11]

    for i, p in where.items():
        out[0x0C + 4 * i : 0x10 + 4 * i] = (BASE + p).to_bytes(4, "little")

    if reparar_referencias:
        # Section [11] is rewritten entry by entry at the same stride, so its
        # bytes keep their local offsets; the content check is off because
        # `build_table()` legitimately changes the values it holds.
        old11 = b[sec[11][0] : sec[11][1]]
        m11 = mapas.get(11)
        if m11 is None and 11 in nuevos:
            m11 = append_map(old11, cuerpo[11])
            if m11 is None:
                raise RuntimeError(
                    "reparar_referencias: the new body of section [11] "
                    "is not the old one extended at the end and nobody passed "
                    "`mapas[11]`"
                )
        if m11 is None:
            new11 = bytes(out[where[11] : where[11] + len(old11)])
            if len(out) - where[11] < len(old11):
                raise RuntimeError(
                    "reparar_referencias: la tabla[11] reconstruida encogio "
                    "(%d B -> %d B); the local offsets stop being valid"
                    % (len(old11), len(out) - where[11])
                )
            m11 = [(0, len(old11), 0)]
            check_map(old11, new11, m11, "[11]", contenido=False)
        else:
            check_map(old11, cuerpo[11], m11, "[11]", contenido=False)
        mapa[11] = m11

        # already handled above, by construction: the master index slots for
        # [9]/[10]/[11] themselves, and table[11]'s own (rebuilt-whole) entries
        ya_cubiertos = {0x0C + 4 * i for i in where}
        ya_cubiertos |= {
            fo for fo, (_, _, _, etq) in referencias.items() if etq == "tabla[11]"
        }
        for fo, (t, w, i, etq) in referencias.items():
            if fo in ya_cubiertos:
                continue
            for j, (a, z) in sec.items():
                if j in (9, 10, 11) and a <= fo < z:
                    raise RuntimeError(
                        "reparar_referencias: the pointer field at %#08x (%s) "
                        "lives INSIDE section [%d], which is being moved; "
                        "in-place patching does not reach it -- do not "
                        "trust this result without reviewing the case" % (fo, etq, j)
                    )
            loc = map_local(mapa[i], t - sec[i][0])
            if loc is None:
                raise RuntimeError(
                    "reparar_referencias: el campo %#08x (%s) apunta a %#08x, "
                    "inside [%d] but outside its relocation map -- "
                    "the target was deleted by the edit, not moved" % (fo, etq, t, i)
                )
            new_target = where[i] + loc
            if w == 4:
                out[fo : fo + 4] = (BASE + new_target).to_bytes(4, "little")
            else:
                out[fo : fo + 3] = (BASE + new_target).to_bytes(3, "little")

    # The XOR-16 checksum walks even and odd bytes separately, so the body has
    # to measure an **even** number of bytes or the loop reads one too many.
    # In the original blob the close falls at 0x141734, even; when relocating
    # it can land odd. Padded with one byte to realign.
    if len(out) % 2:
        out += b"\x00"
    new_close = len(out) + 2
    out += b"\x00\x00" + b"PTYY"
    out[4:7] = (BASE + new_close).to_bytes(3, "little")
    lo, hi = 0x21, 0x43
    for k in range(0, new_close - 2, 2):
        lo ^= out[k]
        hi ^= out[k + 1]
    out[new_close - 2] = lo
    out[new_close - 1] = hi
    resultado = bytes(out)

    if reparar_referencias:
        # THE PERMANENT CONTROL, PART 1 -- CONTENT.
        #
        # "no declared pointer lands in the vacated zone" is too weak to be a
        # proof: a pointer aimed at the WRONG place inside the LIVE zone
        # satisfies it. That false green is what let a uniform delta ship
        # 206 keyregs pointing 4 bytes short (see the module docstring).
        #
        # So the invariant is about what the pointers SEE: read every
        # repointed field back out of the finished blob and demand that the
        # bytes behind it are the bytes that were behind it before. The
        # window is clipped to the end of the map run holding the target, so
        # a legitimate insertion right after a record is not a false alarm.
        malos: list[str] = []
        for fo, (t, w, i, etq) in referencias.items():
            local = t - sec[i][0]
            loc = map_local(mapa[i], local)
            if loc is None:
                malos.append("%#08x (%s): target outside the map" % (fo, etq))
                continue
            esperado = where[i] + loc
            # where does the FIELD itself live now? Outside the moved
            # sections it did not move; table[11]'s entries travelled with
            # their section, at the same local offset.
            new_fo = fo
            for j in (9, 10, 11):
                if j in sec and sec[j][0] <= fo < sec[j][1]:
                    lf = map_local(mapa[j], fo - sec[j][0])
                    new_fo = None if lf is None else where[j] + lf
                    break
            if new_fo is None:
                malos.append("%#08x (%s): the field itself got lost" % (fo, etq))
                continue
            leido = int.from_bytes(resultado[new_fo : new_fo + w], "little") - BASE
            if leido != esperado:
                malos.append(
                    "%#08x (%s): ended up pointing at %#08x, expected %#08x"
                    % (fo, etq, leido, esperado)
                )
                continue
            # Section [11] is the one body whose CONTENT legitimately changes:
            # `build_table()` rewrites every entry and bumps the count in its
            # 2-byte header (2904 -> 2917 when 13 objects are appended), which
            # is exactly what the master index's [11] slot points at. Its
            # entries are not left unchecked -- each one appears in
            # `referencias` as a `table[11]` reference into [10], and gets both
            # its position and its content verified there.
            fin_tramo = _map_run_end(mapa[i], local)
            n = min(24, fin_tramo - local, sec[i][1] - t)
            k = (
                _primera_diferencia(
                    b[t : t + n],
                    resultado[esperado : esperado + n],
                    local,
                    editados.get(i, []),
                )
                if n > 0 and i != 11
                else None
            )
            if k is not None:
                malos.append(
                    "%#08x (%s): %#08x -> %#08x but the bytes pointed at "
                    "CAMBIARON en +%d (%s -> %s)"
                    % (
                        fo,
                        etq,
                        t,
                        esperado,
                        k,
                        b[t : t + min(n, 8)].hex(" "),
                        resultado[esperado : esperado + min(n, 8)].hex(" "),
                    )
                )
        if malos:
            raise RuntimeError(
                "reparar_referencias: %d puntero(s) declarados dejaron de ver "
                "the bytes they used to see -- %s%s"
                % (len(malos), "; ".join(malos[:10]), " ..." if len(malos) > 10 else "")
            )

        # THE PERMANENT CONTROL, PART 2 -- REACHABILITY (the complement).
        # Zero declared pointers may still resolve inside the ranges
        # [9]/[10]/[11] just vacated. Any that do mean some structure isn't
        # walked by `declared_pointers()` yet -- shipping that would silently
        # reproduce the exact bug this option exists to close, so it aborts
        # instead of returning.
        dead_zones = {i: sec[i] for i in (9, 10, 11) if i in sec}
        restantes = orphaned(
            references_into(resultado, sections(resultado), dead_zones),
            dead_zones,
        )
        if restantes:
            detail = "; ".join(
                "%#08x -> %#08x (%s)" % (fo, t, etq)
                for fo, (t, _w, _i, etq) in list(restantes.items())[:10]
            )
            raise RuntimeError(
                "reparar_referencias left %d pointer(s) resolving in the "
                "zona muerta tras reubicar -- %s%s"
                % (len(restantes), detail, " ..." if len(restantes) > 10 else "")
            )

    return resultado


def slot(u16: int, tag: int) -> bytes:
    return u16.to_bytes(2, "little") + bytes([tag])


def device_objects(
    dev_index: int, n_commands: int, id_base: int, kind: int = TIPO_IR_POR_DEFECTO
):
    """The objects needed for `n_commands` to become reachable.

    Every command gets **two** objects, because the chain has two hops:

        object B   <02><{cmd_id, 0x7D}><{dev_id, 0x7C}>     the command and its owner
        object A   <02><{0x0FCA, 0x75}><{id_B, 0x7F}>       what the page points at

    `dev_id` is `<index><0x01>`, the shape measured in the three that already
    exist (`0x0001` Sony TV, `0x0101` DVR, `0x0201` Home), and `cmd_id`
    is `<index><ordinal>`. Returns (bodies, ids of the A objects).
    """
    dev_id = (dev_index << 8) | 0x01
    cuerpos, ids_a = [], []
    for i in range(n_commands):
        id_b = id_base + 2 * i
        id_a = id_b + 1
        cuerpos.append(b"\x02" + slot((dev_index << 8) | i, 0x7D) + slot(dev_id, 0x7C))
        cuerpos.append(b"\x02" + slot(kind, 0x75) + slot(id_b, 0x7F))
        ids_a.append(id_a)
    return cuerpos, ids_a


def page(buttons: list[tuple[int, int]]) -> bytes:
    """`<count u8><count x {button u8, u16 object, 0x7F}>`."""
    out = bytearray([len(buttons)])
    for button, obj in buttons:
        out += bytes([button]) + slot(obj, 0x7F)
    return bytes(out)


def _slots(b, dest, i):
    if not 0 <= i < len(dest):
        return []
    d = dest[i]
    if not 0 <= d < len(b):
        return []
    c = b[d]
    if not 0 < c < 40 or d + 1 + 3 * c > len(b):
        return []
    return [
        (int.from_bytes(b[d + 1 + 3 * j : d + 3 + 3 * j], "little"), b[d + 3 + 3 * j])
        for j in range(c)
    ]


def reachable_pages(b: bytes) -> set[int]:
    """The pages the user **can reach by navigating**.

    **FIXES a criterion that broke the hook-up.** This function used to
    return "the pages nobody references", on the reasoning that those would
    be the start-up ones. It is the other way around: not being referenced
    is precisely **not being reachable**. Of the 72 unreferenced ones, page 0
    -- where the Philips button got hooked up -- **the firmware never shows
    it**, which is why pressing the key did nothing.

    The correct answer is measured: section [9] starts with `<01><ref to the
    root object>`, that object is walked through the graph collecting every
    `0x7E` slot (page ordinal), and from those pages it keeps following the
    `0x7E`s of their buttons up to a fixed point. In this blob the seeds are
    **{1, 93, 111, 146}** and the transitive closure gives **36 pages**.
    """
    sec = sections(b)
    dest = table(b, sec[11][0])
    a9, z9 = sec[9]
    raiz = int.from_bytes(b[a9 + 1 : a9 + 3], "little")

    vis, trailer, semillas = {raiz}, [raiz], set()
    while trailer:
        for sid, t in _slots(b, dest, trailer.pop()):
            if t == 0x7E:
                semillas.add(sid)
            elif t in (0x7F, 0x75, 0x72) and sid not in vis and sid < len(dest):
                vis.add(sid)
                trailer.append(sid)

    # which page every button of every page leads to
    salidas: dict[int, list[int]] = {}
    o, ordinal = a9, 0
    while o < z9:
        L = _is_page(b, o, z9)
        if not L:
            o += 1
            continue
        tg = []
        for k in range(b[o]):
            u = int.from_bytes(b[o + 2 + 4 * k : o + 4 + 4 * k], "little")
            for sid, t in _slots(b, dest, u):
                if t == 0x7E:
                    tg.append(sid)
        salidas[ordinal] = tg
        ordinal += 1
        o += L

    alc = set(semillas)
    while True:
        fresh = {t for p in alc for t in salidas.get(p, [])}
        if fresh <= alc:
            return alc
        alc |= fresh


def ir_type(b: bytes) -> int:
    """The `{x, 0x75}` marker carried by button objects, read from the blob.

    It is 0x0FCA in this config and appears in 1,200 of its 1,209 uses, but
    it is a config value, not a format one: the most frequent one is taken
    instead of assumed.
    """
    from collections import Counter

    c = Counter()
    for d in table(b, sections(b)[11][0]):
        if not 0 <= d < len(b):
            continue
        n = b[d]
        if not 0 < n < 40 or d + 1 + 3 * n > len(b):
            continue
        for j in range(n):
            if b[d + 3 + 3 * j] == 0x75:
                c[int.from_bytes(b[d + 1 + 3 * j : d + 3 + 3 * j], "little")] += 1
    return c.most_common(1)[0][0] if c else TIPO_IR_POR_DEFECTO


def page_references(b: bytes) -> set[int]:
    """The page ordinals some key references (slot tag 0x7E)."""
    sec = sections(b)
    dest = table(b, sec[11][0])
    a9, z9 = sec[9]
    out: set[int] = set()
    o = a9
    while o < z9:
        L = _is_page(b, o, z9)
        if not L:
            o += 1
            continue
        for k in range(b[o]):
            u = int.from_bytes(b[o + 2 + 4 * k : o + 4 + 4 * k], "little")
            if not 0 <= u < len(dest):
                continue
            d = dest[u]
            if not 0 <= d < len(b):
                continue
            n = b[d]
            if not 0 < n < 40 or d + 1 + 3 * n > len(b):
                continue
            for j in range(n):
                if b[d + 3 + 3 * j] == 0x7E:
                    out.add(int.from_bytes(b[d + 1 + 3 * j : d + 3 + 3 * j], "little"))
        o += L
    return out


def _is_page(s: bytes, o: int, fin: int) -> int:
    """Length of the page starting at `o`, or 0."""
    c = s[o] if o < len(s) else 0
    if not 0 < c < 40 or o + 1 + 4 * c > fin:
        return 0
    if not all(s[o + 4 + 4 * k] in (0x7F, 0x7E, 0x72) for k in range(c)):
        return 0
    return 1 + 4 * c


def count_pages(s: bytes, ini: int, fin: int) -> int:
    n, o = 0, ini
    while o < fin:
        L = _is_page(s, o, fin)
        if L:
            n += 1
            o += L
        else:
            o += 1
    return n


def hook_at_root(s9: bytearray, largo: int, id_nav: int, raices: set[int]):
    """Adds a free key to the first root page that has one.

    Returns `(new section [9], insertion point, how many bytes, edited
    ranges)`, or None if no root has a free key. The edited ranges are the
    bytes changed IN PLACE (the hooked page's key-count byte, bumped by one);
    the insertion point is where new bytes were pushed in. Works on the local
    copy, whose offsets start at 0.

    **The insertion point is not decoration.** This edit lands in the MIDDLE
    of section [9] (measured on the factory blob: page ordinal 1, local
    0x14), so every byte past it shifts by 4. Callers that need to repoint
    references into [9] must translate through `insertion_map()` with these
    numbers; a single uniform delta leaves every pointer past the insertion
    aiming 4 bytes short. See the module docstring.
    """
    o, p = 0, 0
    while o < largo:
        L = _is_page(s9, o, largo)
        if not L:
            o += 1
            continue
        c = s9[o]
        usadas = {s9[o + 1 + 4 * k] for k in range(c)}
        libres = [t for t in PHYSICAL_KEYS if t not in usadas]
        if p in raices and libres:
            agregado = bytes([libres[0]]) + slot(id_nav, 0x7F)
            rebuilt = bytearray([c + 1])
            rebuilt += s9[o + 1 : o + L]
            rebuilt += agregado
            return s9[:o] + rebuilt + s9[o + L :], o + L, len(agregado), [(o, o + 1)]
        o += L
        p += 1
    return None


def navigation_object(page_ordinal: int, kind: int = TIPO_IR_POR_DEFECTO) -> bytes:
    """The object that makes a key jump to a page.

    **The `0x7E` tag is a page ordinal, not an object id.** Measured: of its
    847 uses, the range is exactly **0..155** and there are exactly **156
    pages**; of the 400 references coming out of buttons, **none** falls
    outside that range. If they were object ids, with 2,904 objects there
    would be plenty above 156.

    That fixes having resolved them earlier through the global table, which
    "worked" only because any value under 2,904 returns something.

    The shape comes from the navigation objects that already exist, for
    example 2317: `<02><{0x0FCA, 0x75}><{page, 0x7E}>`.
    """
    return b"\x02" + slot(kind, 0x75) + slot(page_ordinal, 0x7E)


def add_device(
    b: bytes,
    dev_index: int,
    n_commands: int | None = None,
    buttons_per_page: int = 6,
    reparar_referencias: bool = False,
):
    """Extends [9], [10] and [11] for a new device, and relocates.

    Returns (new blob, how many commands got hooked up). The **command
    records** have to already be in the blob (`proxy.py` puts them there);
    this only builds the path that makes them reachable from a key.

    `n_commands` is passed by whoever added the records, because they know
    it for certain. **It used to be deduced from this blob's counts wired in**
    (`{0:0, 1:81, 2:143}` and `[81, 62, 93]`), which broke the requirement of
    working for any config that comes in: with a different blob those
    numbers mean nothing. If not passed, it is deduced by counting the
    records **no existing device claims**, which is correct but more
    fragile.

    `reparar_referencias` is forwarded to `relocate()` as-is (default
    **False** -- see its docstring and the module docstring: this keeps
    `add_device()`'s output byte-identical to today's for every existing
    caller, `table[6]`'s stale-pointer bug included).
    """
    import commands as C

    sec = sections(b)
    if n_commands is None:
        tot = len(list(C.records(b)))
        reclamados = 0
        for cmd, dev in chain(b).values():
            if dev is not None:
                reclamados = max(reclamados, (cmd & 0xFF) + 1)
        # the ordinals no device reaches are the ones just added
        n_commands = max(tot - reclamados, 0)
    n_nuevos = n_commands
    if n_nuevos <= 0:
        return b, 0

    dest = table(b, sec[11][0])
    id_base = len(dest)
    cuerpos, ids_a = device_objects(dev_index, n_nuevos, id_base, ir_type(b))

    # the new objects get appended to the end of section [10]
    a10, z10 = sec[10]
    s10 = bytearray(b[a10:z10])
    offs = []
    for c in cuerpos:
        offs.append(len(s10))
        s10 += c

    # one page per batch of buttons, at the end of section [9]
    a9, z9 = sec[9]
    s9 = bytearray(b[a9:z9])
    BUTTONS = list(PHYSICAL_KEYS)  # the measured order: b2 b3 b0 b1 b4 b5
    n_pages = count_pages(b, a9, z9)
    first_new_page = n_pages
    for i in range(0, len(ids_a), buttons_per_page):
        tanda = ids_a[i : i + buttons_per_page]
        s9 += page([(BUTTONS[j % len(BUTTONS)], o) for j, o in enumerate(tanda)])

    # the hook: without this the new pages exist and **nobody reaches them**.
    # It is hung off a root page (0..11 are referenced by nobody: they are
    # the start-up ones) that has an unused physical key, so as not to step
    # on anything.
    offs.append(len(s10))
    s10 += navigation_object(first_new_page, ir_type(b))
    id_nav = id_base + len(offs) - 1
    old_len9 = z9 - a9
    gancho = hook_at_root(s9, old_len9, id_nav, reachable_pages(b))
    if gancho is None:
        raise RuntimeError("no page reachable with a free physical key")
    s9, punto, cuantos, editados9 = gancho

    # The hook lands in the MIDDLE of [9] (the new pages were appended, but
    # the key was added to an existing root page), so [9]'s relocation is NOT
    # one uniform delta: everything past `punto` moves an extra `cuantos`
    # bytes. `hook_at_root` also bumps that page's key-count byte in place --
    # reported in `editados9` so the content controls know it is intentional.
    # Without both, `relocate(..., reparar_referencias=True)` would either
    # abort or, worse, repoint 206 keyregs 4 bytes short.
    mapa9 = insertion_map(old_len9, punto, cuantos)

    return (
        relocate(
            b,
            {9: bytes(s9), 10: bytes(s10)},
            objetos_extra=offs,
            reparar_referencias=reparar_referencias,
            mapas={9: mapa9},
            editados={9: editados9},
        ),
        n_nuevos,
    )


# --------------------------------------------------------------------------
# the proof: walk the button -> command chain over the resulting blob


def chain(b: bytes) -> dict:
    """{(page ordinal, button): (cmd_id, dev_id)} walking the full model.

    The key is the page's **ordinal** within the section, not its offset:
    relocating moves pages on purpose, so comparing by offset would always
    say "different" and would not prove anything.


    page: <count u8><count x {button, u16 object, 0x7F}>
    u16 -> table[11] -> object -> slot {id,0x7F} -> table[11] -> object
                                                        {cmd_id,0x7D}
                                                        {dev_id,0x7C}
    """
    sec = sections(b)
    dest = table(b, sec[OBJECT_TABLE][0])

    def obj(i):
        return dest[i] if 0 <= i < len(dest) else None

    def slots(d):
        if d is None or not 0 <= d < len(b):
            return []
        c = b[d]
        if not 0 < c < 40 or d + 1 + 3 * c > len(b):
            return []
        return [
            (
                int.from_bytes(b[d + 1 + 3 * j : d + 3 + 3 * j], "little"),
                b[d + 3 + 3 * j],
            )
            for j in range(c)
        ]

    out = {}
    a9, z9 = sec[9]
    o = a9
    ordinal = 0
    while o < z9:
        c = b[o]
        if not 0 < c < 40 or o + 1 + 4 * c > z9:
            o += 1
            continue
        entradas = [
            (
                b[o + 1 + 4 * k],
                int.from_bytes(b[o + 2 + 4 * k : o + 4 + 4 * k], "little"),
            )
            for k in range(c)
        ]
        if not all(b[o + 4 + 4 * k] in (0x7F, 0x7E, 0x72) for k in range(c)):
            o += 1
            continue
        for button, u16 in entradas:
            cmd = dev = None
            for sid, tg in slots(obj(u16)):
                if tg != 0x7F:
                    continue
                for v, t2 in slots(obj(sid)):
                    if t2 == 0x7D:
                        cmd = v
                    elif t2 == 0x7C:
                        dev = v
            if cmd is not None:
                out[(ordinal, button)] = (cmd, dev)
        ordinal += 1
        o += 1 + 4 * c
    return out


def _keyregs(b: bytes) -> dict[int, int]:
    """`{field offset: target}` for `table[6]`'s keyreg fields."""
    return {
        fo: t
        for fo, t, etq in table6_chain(b, sections(b))
        if etq == "tabla[6] slot keyreg"
    }


def _check_forward(b: bytes) -> int:
    """Runs `add_device()` both ways and compares the RECORD each keyreg sees.

    This is the control the retracted "end-to-end proof" never had. `chain()`
    walks section [9] by ordinal and never follows `table[6]`, so it cannot
    see a keyreg repointed to the wrong place; comparing the page record
    behind each keyreg, before and after, can -- and does.

    The comparison is on a byte window clipped to the end of the OLD section
    (a blind 24-byte window overruns the last record into whatever follows
    and reports differences that are not there), with the ranges
    `hook_at_root()` reports as edited-on-purpose excluded. Note the keyreg
    targets are NOT all page starts: 50 of the 206 point two bytes earlier,
    at a `00 00` prefix, so parsing them as pages is not an option either.
    """
    viejos = _keyregs(b)
    print("tabla[6] keyreg fields in the input blob: %d" % len(viejos))
    if not viejos:
        print("nothing to check on this blob")
        return 0

    sec = sections(b)
    a9v, z9v = sec[9]

    # replay the hook to learn its geometry, so the control knows which bytes
    # are allowed to differ instead of being told
    s9 = bytearray(b[a9v:z9v])
    g = hook_at_root(s9, z9v - a9v, len(table(b, sec[11][0])) + 1, reachable_pages(b))
    if g is None:
        print("this blob has no root page with a free key; nothing to check")
        return 0
    _, punto, cuantos, editados9 = g
    print(
        "hook geometry: %d bytes inserted at local %#x, edited in place %s"
        % (cuantos, punto, ["%#x..%#x" % (i, f) for i, f in editados9])
    )

    def informe(nb: bytes, label: str, mapa: Mapa | None):
        nuevos = _keyregs(nb)
        a9, z9 = sections(nb)[9]
        inside = sum(1 for t in nuevos.values() if a9 <= t < z9)
        iguales = tocadas = rotas = 0
        for fo, t in viejos.items():
            if fo not in nuevos:
                rotas += 1
                continue
            local = t - a9v
            n = min(24, z9v - t)
            if mapa is not None:
                n = min(n, _map_run_end(mapa, local) - local)
            tn = nuevos[fo]
            crudo = nb[tn : tn + n] != b[t : t + n]
            limpio = (
                _primera_diferencia(b[t : t + n], nb[tn : tn + n], local, editados9)
                is None
            )
            if limpio and not crudo:
                iguales += 1
            elif limpio:
                tocadas += 1  # differs only where the hook declared an edit
            else:
                rotas += 1
        print(
            "  %-16s in live [9]: %3d/%d | same bytes: %3d | only the "
            "declared edit: %d | WRONG: %d"
            % (label, inside, len(nuevos), iguales, tocadas, rotas)
        )
        return iguales, tocadas, rotas

    print("\nadd_device(indice=5, n_comandos=6):")
    sin, _ = add_device(b, 5, n_commands=6)
    informe(sin, "sin --reparar", None)
    try:
        con, _ = add_device(b, 5, n_commands=6, reparar_referencias=True)
    except RuntimeError as e:
        print("  con --reparar    ABORTED: %s" % e)
        return 2
    mapa9 = insertion_map(z9v - a9v, punto, cuantos)
    ig, to, ro = informe(con, "con --reparar", mapa9)

    nuevos = _keyregs(con)
    a9, z9 = sections(con)[9]
    vivos = sum(1 for t in nuevos.values() if a9 <= t < z9)
    ok = ro == 0 and vivos == len(nuevos) and ig + to == len(viejos)
    print(
        "\nwith --reparar: every keyreg lands in the live [9] and still sees "
        "the bytes it saw before (bar the page the hook edited on purpose): "
        "%s" % ("YES" if ok else "NO")
    )
    print(
        "without it: they all still see the right bytes, but in the DEAD copy "
        "-- inert, not corrupt. Editing the live [9] does nothing."
    )
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument(
        "--nulo", action="store_true", help="relocate without changing anything"
    )
    ap.add_argument("--salida")
    ap.add_argument(
        "--reparar",
        action="store_true",
        help=(
            "repoint every declared external reference (tabla[6] included), "
            "not just the master index and table[11]; aborts if any declared "
            "pointer still lands in the vacated range. Default: off -- the "
            "output stays byte-identical to today's (the anchor)."
        ),
    )
    ap.add_argument(
        "--forward",
        action="store_true",
        help=(
            "the add_device control: runs the path that actually edits [9] "
            "in the middle and checks, record by record, what tabla[6]'s "
            "keyregs resolve to -- with and without --reparar"
        ),
    )
    a = ap.parse_args()

    b = pathlib.Path(a.blob).read_bytes()
    sec = sections(b)

    if a.forward:
        return _check_forward(b)
    print("sections being moved:")
    for i in (9, 10, 11):
        ini, fin = sec[i]
        print("  [%2d]  %#08x..%#08x  %6d B" % (i, ini, fin, fin - ini))

    before = chain(b)
    print("\nbuttons that resolve in the original blob: %d" % len(before))

    # diagnostic, always shown: how many EXTERNAL references point into the
    # ranges about to move, and how many of those are the master-index/
    # table[11] kind this file already fixed vs. the rest (tabla[6] et al.)
    refs = references_into(b, sec, {i: sec[i] for i in (9, 10, 11)})
    ya_conocidos = sum(
        1
        for _fo, (_t, _w, _i, etq) in refs.items()
        if etq.startswith("indice maestro") or etq == "tabla[11]"
    )
    print(
        "\nexternal references into [9]/[10]/[11]: %d total  "
        "(%d master-index/table[11], %d other -- e.g. tabla[6])"
        % (len(refs), ya_conocidos, len(refs) - ya_conocidos)
    )

    try:
        n = relocate(b, reparar_referencias=a.reparar)
    except RuntimeError as e:
        print("\nABORTED (--reparar): %s" % e)
        return 2

    after = chain(n)
    print("\nbuttons that resolve after relocating: %d" % len(after))

    igual = before == after
    print(
        "\nthe button -> (command, device) chain stays %s"
        % ("IDENTICAL" if igual else "DIFFERENT")
    )
    if not igual:
        missing = set(before) - set(after)
        cambian = {k for k in set(before) & set(after) if before[k] != after[k]}
        print("  disappeared %d, changed %d" % (len(missing), len(cambian)))
        for k in list(missing)[:3]:
            print("    missing %s -> %s" % (k, before[k]))
        for k in list(cambian)[:3]:
            print("    %s: %s -> %s" % (k, before[k], after[k]))

    # the permanent control's result, shown even off --reparar (where
    # `relocate()` doesn't check it): zero declared pointers should resolve
    # into the vacated zone after --reparar; without it, today's gap shows.
    dead_zones = {i: sec[i] for i in (9, 10, 11)}
    restantes = orphaned(references_into(n, sections(n), dead_zones), dead_zones)
    print(
        "\nreferences still resolving into the vacated zone: %d  (--reparar=%s)"
        % (len(restantes), a.reparar)
    )
    if not a.reparar and restantes:
        print("  known gap: pass --reparar to close it (see module docstring)")

    print("\nblob: %d B -> %d B  (+%d)" % (len(b), len(n), len(n) - len(b)))
    if a.salida and igual:
        pathlib.Path(a.salida).write_bytes(n)
        print("wrote %s" % a.salida)
    elif a.salida:
        print("nothing written: the null relocation was not identical")
    return 0 if igual else 1


if __name__ == "__main__":
    raise SystemExit(main())
