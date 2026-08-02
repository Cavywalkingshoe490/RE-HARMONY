#!/usr/bin/env python3
"""Classifies the config stretches that `coverage.py` leaves unexplained.

`coverage.py` claims 97.91% and leaves 451 loose stretches. Eyeballing them
you can see they are not noise: there are u24 pointer tables, tables of
consecutive identifiers and arrays of fixed-size records. This recognises
them by shape, assuming nothing about the content.

The three recognisers, and why each one is defensible:

    u24 pointers    the values fall inside the blob and are ordered. A
                    stretch of N increasing, in-range triplets does not
                    happen by chance: the probability that 3 random bytes
                    fall in the useful range is ~0.08, so 10 in a row is
                    1e-11.
    fixed step      if on top of that the difference between consecutive
                    pointers is constant, it points at an array of records
                    of that size, and the step itself is the datum that
                    matters.
    consecutive     <id><padding><value> sequences where id and value
                    advance by one. It is a translation table.

Everything that does not fit is reported as still unidentified, with its
dump. Nothing is counted twice and nothing is taken as explained without a
recogniser behind it.

Usage:
    python3 gaps.py <config.bin>
    python3 gaps.py <config.bin> --detalle 20
"""

from __future__ import annotations

import argparse
import pathlib
from collections import Counter

BASE = 0x040000
MIN_POINTERS = 6  # fewer than this does not tell it apart from chance
# The only two labels that appear in the already-claimed lists, counted
# over records with in-range pointer: 0x7F (892), 0x72 (48). Accepting only
# 0x7F lost whole runs like the one at `0x01565E`. With both, the control in
# zona de pixeles sigue dando 0/20000.
ETIQUETAS = frozenset({0x7F, 0x72})


def cargar_tramos(blob: bytes):
    """The gaps coverage.py leaves, using its own claim.

    Imports the function instead of reimplementing the claim: a copy goes
    out of sync the moment coverage.py learns to explain something new,
    and then this would report as a finding something already counted.
    """
    import coverage

    return coverage.tramos_sin_explicar(coverage.mapa_de_reclamos(blob))


def pointers(blob: bytes, ini: int, fin: int):
    """Consecutive u24 triplets that point inside the blob.

    The shape is `<u16 cuenta><cuenta x ptr24>`, the same one `coverage.py`
    calls "pointer tables" but in stretches its walk does not reach.

    **The count is what makes the recogniser reliable**: there is no
    guessing where the run ends nor picking an alignment by "the longest
    one" -- an earlier attempt did that and accepted reads shifted by one
    byte, which also fall in range and also increase, with garbage values.
    Here the header promises N and the N have to be there: at `0x011133` it
    says 10 and there are 10 (with an exact step of 108), at `0x00FE7E` it
    says 5 and there are 5.
    """
    outside = BASE + len(blob)
    if fin - ini < 2 + 3 * MIN_POINTERS:
        return [], ini
    cuenta = int.from_bytes(blob[ini : ini + 2], "little")
    if not MIN_POINTERS <= cuenta <= (fin - ini - 2) // 3:
        return [], ini
    vals, o = [], ini + 2
    for _ in range(cuenta):
        v = int.from_bytes(blob[o : o + 3], "little")
        if not (BASE <= v < outside):
            return [], ini  # the count promised more than there are: this is not it
        vals.append(v)
        o += 3
    return vals, o


def constant_step(vals):
    if len(vals) < 3:
        return None
    d = {vals[i + 1] - vals[i] for i in range(len(vals) - 1)}
    return d.pop() if len(d) == 1 else None


def correlativos(blob: bytes, ini: int, fin: int):
    """<id><00><00><value> with id and value advancing by one."""
    if fin - ini < 16:
        return 0
    n, o = 0, ini
    prev_id = prev_v = -1
    while o + 4 <= fin:
        if blob[o + 1] or blob[o + 2]:
            break
        if n and (blob[o] != prev_id + 1 or blob[o + 3] != prev_v + 1):
            break
        prev_id, prev_v = blob[o], blob[o + 3]
        n += 1
        o += 4
    return n


def registros4(blob: bytes, ini: int, fin: int):
    """Arrays of 4-byte records with the identifier in byte 0.

    At `0x014024` you see `95 00 00 00 | 96 00 00 00 | 97 00 00 00 | ab 00 00 00`
    with the odd entry that does use the other three bytes. It does not
    require the identifiers to be consecutive -- they are not -- but that
    most of the records have bytes 1..3 at zero, which is what tells this
    shape apart from a pointer array or from dense data.
    """
    n = (fin - ini) // 4
    if n < 6:
        return 0
    vacios = sum(
        1
        for k in range(n)
        if not (blob[ini + 4 * k + 1] or blob[ini + 4 * k + 2] or blob[ini + 4 * k + 3])
    )
    return n if vacios >= 0.6 * n else 0


# Sony SIRC: 2400 us leader, marks of 1200 (bit 1) and 600 (bit 0), spaces
# of 600. Bit 15 of the u16 flags "is a mark". Three values, nothing else:
# that is why a stretch that decodes whole against that set is no accident.
SONY = {600, 1200, 2400}


def sony(blob: bytes, ini: int, fin: int):
    """Pulses of a Sony waveform. Returns (count, bits) or (0, 0).

    `coverage.py` only claims the waveforms it reaches **from** a command
    record it managed to parse. The ones left loose land here: 80 stretches
    of 59 bytes (12 bits) and 65 of 71 (15 bits), and the 12-byte
    difference is exactly the 3 extra bits, at 2 pulses per bit.
    """
    mejor = (0, 0, 0)
    for desp in (0, 1, 2):
        o, pulsos, marcas = ini + desp, 0, []
        while o + 2 <= fin:
            v = int.from_bytes(blob[o : o + 2], "little")
            if (v & 0x7FFF) not in SONY:
                break
            if v & 0x8000:
                marcas.append(v & 0x7FFF)
            pulsos += 1
            o += 2
        if pulsos >= 20 and marcas and marcas[0] == 2400:
            # After the pulses comes the gap between repetitions (on this
            # device 0x6270 = 25,200 us) and an `01 00 00` tail. They were not in
            # the SONY set, so the recogniser cut short and left the final
            # 3 bytes loose -- which showed up as 98 stretches of
            # `00 00 00` that looked like padding and were not.
            extra = 0
            if o + 2 <= fin and not (int.from_bytes(blob[o : o + 2], "little") & 0x8000):
                extra = 2
                if blob[o + 2 : o + 5] == b"\x01\x00\x00":
                    extra = 5
            if pulsos > mejor[0]:
                mejor = (pulsos, len(marcas) - 1, desp + 2 * pulsos + extra)
    return mejor


def ranuras(blob: bytes, ini: int, fin: int):
    """Slot records `<ptr24><label>`.

    The format is `{codigo:u8, u16, label:u8}`, just as `coverage.py`
    said from the start.

    **I got this wrong here and it is worth writing down.** I measured that
    the first three bytes read as a u24 fall in range 81.0% aligned against
    0.0% misaligned, and concluded they were pointers. The test came out
    fine but the reading was false: the u16s of those records are
    consecutive identifiers (`0x090C`, `0x090D`, `0x090E`...) and gluing the
    code byte in front of them forms numbers that **land in the blob's
    range by coincidence**.

    What took it apart was cross-checking with the button map: on page
    `0x0291ED` the record is `b2 0c 09 7f` and the matching entry says
    `button: 0xB2`. The first byte **is** the button, in 173 of 173 entries.

    Lesson: a control that rules out misalignment proves there is structure
    there, **not** that the structure is the one you think.

    That is why the criterion now demands both things -- an in-range pointer
    **and** a valid label -- instead of the label alone. And it tries a
    couple of shifts, because several runs carry a prefix byte: at
    `0x01573A` the records start at `+1`, not at `+0`.
    """
    BASE_ = 0x040000
    tope = BASE_ + len(blob)
    mejor = (0, 0)
    for desp in range(0, 4):
        n, o = 0, ini + desp
        while o + 4 <= fin:
            # STRICT: label 0x7F **and** an in-range pointer, for all the
            # records. An earlier version also accepted label 0x00 without
            # checking the pointer, and that broke it: at 20,000 random
            # positions in the pixel zone, **30%** produced runs of 6 or
            # more records. The threshold saved nothing -- at 8 it still gave
            # 30%. With the strict criterion the same control gives **0.0000%**.
            if blob[o + 3] not in ETIQUETAS:
                break
            v = int.from_bytes(blob[o : o + 3], "little")
            if not (BASE_ <= v < tope):
                break
            n += 1
            o += 4
        if n > mejor[0]:
            mejor = (n, desp)
    # With 0% false positives measured, three records are already evidence.
    return mejor if mejor[0] >= 3 else (0, 0)


def ascendentes(blob: bytes, ini: int, fin: int):
    """Strictly increasing u16 lists closed by the 0x270F sentinel.

    They show up at `0x02D564`, inside section `[14]`'s region:

        3000, 3015, 3627, 3652, 3674, 3704, 3725, 3751, 3772,
        3850, 3875, 3952, 4025, 4051, 9999

    `9999` is `0x270F` and closes the list; right after it another one
    starts with the same values except the last. That they are **strictly**
    increasing and end in a sentinel is what makes them recognisable: the
    control over 20,000 random positions in the pixel zone gives
    **0.0000%**.

    What the values represent is not established and is not claimed.
    """
    vals, o = [], ini
    while o + 2 <= fin:
        v = blob[o] | (blob[o + 1] << 8)
        if v == 0x270F:
            return (o + 2 - ini) if len(vals) >= 5 else 0
        if vals and v <= vals[-1]:
            return 0
        vals.append(v)
        o += 2
    return 0


def relleno(blob: bytes, ini: int, fin: int):
    """Alignment zeros between structures.

    Of the 309 stretches of 1 to 3 bytes that were left, **235 are all
    zeros**. It is not a decoded structure and it is not presented as one:
    it is padding, and claiming it under that name is honest because it is
    verified byte by byte. What would NOT be honest is counting it as an
    understood format.
    """
    n = 0
    while ini + n < fin and blob[ini + n] == 0 and n < 8:
        n += 1
    return n


def ranuras_con_cuenta(blob: bytes, ini: int, fin: int):
    """Chained lists `<u8 cuenta><cuenta x {ptr24, tag}>`.

    It is the shape `coverage.py` already declares -- `<count:u8> + count x 4`
    -- but in runs its walk does not reach. You can see it on its own at
    `0x015759`:

        02 | b2 86 07 7f | b1 87 07 7f | 02 | b2 88 07 7f | ...
        03 | b2 f3 07 7f | b0 f4 07 7f | b1 f5 07 7f      (at 0x015952)

    The count byte is what makes the recogniser reliable: it promises N and
    the N have to be there, each one with an in-range pointer and label
    0x7F. Without that cross-check, a loose label criterion gives 30% false
    positives in the pixel zone (measured).
    """
    BASE_ = 0x040000
    tope = BASE_ + len(blob)
    o, listas = ini, 0
    while o < fin:
        c = blob[o]
        if not 1 <= c <= 32 or o + 1 + 4 * c > fin:
            break
        correct = True
        for k in range(c):
            q = o + 1 + 4 * k
            v = int.from_bytes(blob[q : q + 3], "little")
            if blob[q + 3] not in ETIQUETAS or not (BASE_ <= v < tope):
                correct = False
                break
        if not correct:
            break
        listas += 1
        o += 1 + 4 * c
    # ONE list is enough: the control gives **0/20000** false positives in the
    # pixel zone, because the count has to add up and on top of that every
    # record demands label 0x7F and an in-range pointer at once. Demanding two
    # lost the lists truncated at the stretch edge, like at `0x015119`.
    return (o - ini) if listas >= 1 else 0


def par_con_carga(blob: bytes, ini: int, fin: int):
    """10 B records `<ptr24><3 bytes><label><ptr24>`.

    They show up as targets of the enumeration tables. With an exact step of
    10 the shape stands out on its own:

        02 99 04 | ff 1d 0e | 14 | 80 94 05
        02 99 04 | 8e 24 08 | 14 | 80 94 05

    The two pointers are constant within a run and the middle varies -- a
    3-byte payload, not an address (it falls out of range).

    Measured over the 228 targets, demanding **both** pointers in range:

        aligned      64%
        shift 1       0%     <- the discriminant
        shift 2      36%
        random        1%

    64 times the control, and shift 1 kills it. The label at +6 is `0x14` in
    136 of 147, which reinforces it.

    **It is not the same as `dobles()`**, which also measures 10 B but
    splits the fields as `<u16><ptr24><ptr24><u16>`. Under that reading
    these do not fit.
    """
    BASE_ = 0x040000
    tope = BASE_ + len(blob)
    n, o = 0, ini
    while o + 10 <= fin:
        a = int.from_bytes(blob[o : o + 3], "little")
        c = int.from_bytes(blob[o + 7 : o + 10], "little")
        if not (BASE_ <= a < tope and BASE_ <= c < tope):
            break
        n += 1
        o += 10
    return n if n >= 2 else 0


def dobles(blob: bytes, ini: int, fin: int):
    """10-byte records `<u16><ptr24><ptr24><u16>`.

    Found by autocorrelation in the twins `0x0199C8` and `0x019678`, which
    start out identical: step 20 with 93.4% self-similarity and step 10 with
    66.7%, i.e. 10-byte records with alternating pairs.

    Measured, demanding that **both** pointers fall in range:

        aligned         94% and 100%
        shifts 1 and 2   0%
        control in the pixel zone  0.4%

    Around 250 times the control. Demanding both pointers at once is what
    makes it so clean: one alone would give a lot of noise.
    """
    BASE_ = 0x040000
    tope = BASE_ + len(blob)
    n, o = 0, ini
    while o + 10 <= fin:
        a = int.from_bytes(blob[o + 2 : o + 5], "little")
        c = int.from_bytes(blob[o + 5 : o + 8], "little")
        if not (BASE_ <= a < tope and BASE_ <= c < tope):
            break
        n += 1
        o += 10
    return n if n >= 4 else 0


def index_table(blob: bytes, ini: int, fin: int, margen: int = 16):
    """u24 table **behind** the stretch whose entries point **inside** it.

    Found while looking at `0x0128E6`: the pointers aiming at that region
    were spaced exactly 3 bytes apart and started right where the region
    ends. The stretch is an array of variable-length records and the table
    that follows it gives the bounds.

    No separate control is needed: three consecutive u24s falling inside one
    concrete window a couple of thousand bytes wide has a probability of the
    order of 1e-12. The restriction "it points at THIS stretch" is the
    control.
    """
    BASE_ = 0x040000
    mejor = []
    for arranque in range(fin, min(fin + margen, len(blob) - 3)):
        vals, o = [], arranque
        while o + 3 <= len(blob):
            v = int.from_bytes(blob[o : o + 3], "little") - BASE_
            if not (ini <= v < fin):
                break
            vals.append(v)
            o += 3
        if len(vals) > len(mejor):
            mejor = vals
    return mejor


def referenciados(blob: bytes, tramos):
    """Stretches whose START is the target of a pointer living in an
    already-parsed structure. The two restrictions came out of auditing a
    broad version:

        start-of-stretch target    27%  against  2% random  -> signal
        any-byte target            indistinguishable from random  -> noise

    There are 83,733 in-range u24s inside parsed structures; if they fell at
    random you would expect ~1,754 in the unexplained zone and there are
    1,443, **fewer than chance**. Splitting on interior targets gave 99.92%
    and was false. Real pointers point at the **start** of a record, not at
    its middle.
    """
    import coverage

    BASE_ = 0x040000
    own = coverage.mapa_de_reclamos(blob)
    inicios = {i: (i, f) for i, f in tramos}
    fonts = {}
    for o in range(len(blob) - 2):
        if not own[o]:
            continue
        v = int.from_bytes(blob[o : o + 3], "little")
        if BASE_ <= v < BASE_ + len(blob):
            d = v - BASE_
            if d in inicios and d not in fonts:
                fonts[d] = coverage.LABEL.get(own[o], "?")
    return fonts


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("config")
    ap.add_argument("--detail", type=int, default=12)
    a = ap.parse_args()
    blob = pathlib.Path(a.config).read_bytes()

    tramos = cargar_tramos(blob)
    total = sum(f - i for i, f in tramos)
    print(
        "unexplained runs: %d, %d bytes (%.2f%% of the blob)\n"
        % (len(tramos), total, 100 * total / len(blob))
    )

    clas = Counter()
    bytes_by_kind = Counter()
    hallazgos = []
    resto = []
    fonts = referenciados(blob, tramos)

    # Big stretches are not a single structure: they are several glued together.
    # Testing only the first byte of the stretch recognised 84 bytes of 27,579.
    # You have to walk the stretch: when nothing fits, advance a byte and retry.
    for ini, fin in tramos:
        o = ini
        outstanding = None  # start of the little stretch nobody recognised
        while o < fin:
            reconocido = None

            # Index table behind the stretch: it gives the bounds of its records.
            # Tried at **any** position, not only at the start: a stretch
            # usually starts with another structure (a pointer table, some
            # slots) and only then comes the indexed body. Demanding
            # `o == ini` lost, for example, `0x011160`.
            if True:
                idx = index_table(blob, o, fin)
                if len(idx) >= 3:
                    reconocido = (fin, "records indexed by a later table (%d)"
                                  % len(idx), len(idx), None, [])
                    # the table points inside [o, fin): it is the indexed body

            # Then reachability: if something points here, it is a record and
            # it starts right here. Nothing has to be guessed.
            #
            # And not only at the stretch start: a big stretch is usually several
            # records glued together, and the **interior** targets say where
            # to cut. The record runs to the next target or to the end of the
            # stretch, whichever comes first.
            if o == ini and o in fonts:
                reconocido = (fin, "record pointed at from %s" % fonts[o],
                              fin - o, None, [])

            vals, end = pointers(blob, o, fin)
            if len(vals) >= MIN_POINTERS:
                passed = constant_step(vals)
                category = "punteros u24 paso %d" % passed if passed else "punteros u24"
                reconocido = (end, category, len(vals), passed, vals[:3])
            if not reconocido:
                n = correlativos(blob, o, fin)
                if n >= 4:
                    reconocido = (o + 4 * n, "tabla correlativa", n, None, [])
            if not reconocido:
                n = ascendentes(blob, o, fin)
                if n:
                    reconocido = (o + n, "u16 crecientes con centinela 0x270F",
                                  n, None, [])
            if not reconocido:
                n = relleno(blob, o, fin)
                if n:
                    reconocido = (o + n, "relleno de alineacion (ceros)", n, None, [])
            if not reconocido:
                largo = ranuras_con_cuenta(blob, o, fin)
                if largo:
                    reconocido = (o + largo, "listas <cuenta><codigo,u16,tag>",
                                  largo, None, [])
            if not reconocido:
                n = par_con_carga(blob, o, fin)
                if n:
                    reconocido = (o + 10 * n, "registros <ptr24><carga><tag><ptr24>",
                                  n, None, [])
            if not reconocido:
                n = dobles(blob, o, fin)
                if n:
                    reconocido = (o + 10 * n, "registros <u16><ptr24><ptr24><u16>",
                                  n, None, [])
            if not reconocido:
                n, desp = ranuras(blob, o, fin)
                if n:
                    reconocido = (o + desp + 4 * n, "ranuras <codigo><u16><tag>", n, None, [])
            if not reconocido:
                pulsos, bits, largo = sony(blob, o, fin)
                if pulsos:
                    reconocido = (o + largo, "forma de onda Sony %d bits" % bits,
                                  pulsos, None, [])
            if not reconocido:
                n = registros4(blob, o, min(fin, o + 4 * 512))
                if n >= 6:
                    reconocido = (o + 4 * n, "registros de 4 bytes", n, None, [])

            if reconocido:
                end, category, n, passed, muestra = reconocido
                if outstanding is not None:
                    resto.append((outstanding, o))
                    outstanding = None
                clas[category] += 1
                bytes_by_kind[category] += end - o
                hallazgos.append((o, end, category, n, passed, muestra))
                o = end
            else:
                if outstanding is None:
                    outstanding = o
                o += 1
        if outstanding is not None:
            resto.append((outstanding, fin))

    print("%-26s %7s %10s" % ("category", "tramos", "bytes"))
    for c, n in clas.most_common():
        print("%-26s %7d %10d" % (c, n, bytes_by_kind[c]))
    reconocido = sum(bytes_by_kind.values())
    sin = total - reconocido
    print("%-26s %7s %10d" % ("RECONOCIDO", "", reconocido))
    print("%-26s %7d %10d" % ("sigue sin identificar", len(resto), sin))
    # Two numbers, and the one that matters is the second. The blob is 92.9%
    # bulk data (bitmaps and waveforms), so coverage over the total mostly
    # measures how well padding gets accounted for: 27,579 unexplained bytes
    # are 2.09% of the blob but 29.39% of the structure. Reporting only the
    # first one is keeping the comfortable number.
    import coverage as _cov

    own = _cov.mapa_de_reclamos(blob)
    granel = {k for k, v in _cov.LABEL.items() if v in ("bitmaps", "IR waveforms")}
    n_granel = sum(1 for x in own if x in granel)
    estr = len(blob) - n_granel
    print(
        "\ncobertura sobre el blob entero:  %.2f%% -> %.2f%%"
        % (100 * (len(blob) - total) / len(blob), 100 * (len(blob) - sin) / len(blob))
    )
    print(
        "coverage of the STRUCTURE:       %.2f%% -> %.2f%%   (%d B of %d)"
        % (100 * (estr - total) / estr, 100 * (estr - sin) / estr, sin, estr)
    )
    print("   the blob is %.1f%% bulk data; the rest is what describes the format"
          % (100 * n_granel / len(blob)))

    print("\nhallazgos mas grandes:")
    for ini, fin, category, n, passed, muestra in sorted(
        hallazgos, key=lambda h: h[1] - h[0], reverse=True
    )[: a.detail]:
        extra = (
            "  primeros: %s" % ", ".join("%#08x" % v for v in muestra)
            if muestra
            else ""
        )
        print(
            "  %#08x..%#08x  %6d B  %-24s n=%d%s"
            % (ini, fin, fin - ini, category, n, extra)
        )

    print("\nruns still unidentified, largest:")
    for ini, fin in sorted(resto, key=lambda r: r[1] - r[0], reverse=True)[: a.detail]:
        print(
            "  %#08x..%#08x  %6d B  %s"
            % (ini, fin, fin - ini, " ".join("%02x" % x for x in blob[ini : ini + 14]))
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
