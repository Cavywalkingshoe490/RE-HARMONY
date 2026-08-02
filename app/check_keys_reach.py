#!/usr/bin/env python3
"""LOAD-BEARING CHECK for key reassignment: the record the FIRMWARE reaches.

## The bug this exists to make impossible to repeat

Grabada #7 -- `Key D-pad up -> DirectionUp (LG, in PC)` -- passed the gate,
was written, the remote booted fine, and the key did nothing. Every check of
that day looked at the record that had been EDITED, and every one of them was
right: the bytes were there, they resolved, the IR waveform was valid. What
nobody checked was whether that record is the one the remote ARRIVES AT when
the key is pressed. This project had already paid for the same confusion once
(212 of 226 key records pointing at a dead copy of section `[9]`).

So this control never trusts an offset the writer hands it. For every change
it starts at the master index, walks the pointers the firmware walks, and
reads what is at the end of the walk (`teclas_alcance`). And -- the part that
makes it a check and not a decoration -- it PROVES the walk is load-bearing
by breaking things on purpose:

  * repoint a rebuilt header back to the old one (the dead-copy bug, exactly)
    -> the walk has to go RED while the bytes we wrote are all still there;
  * corrupt the two bytes the walk says it read -> RED;
  * ask for a `(k1,k2)` outside section `[5]` -> refused before building
    anything (that one hangs the remote);
  * ask for a side strip (`0xAE`/`0xAF`) -> refused (it is the pager);
  * ask for a row whose class is not `0x7F` -> refused (its id is not an
    object, so repointing it writes something the firmware doesn't read as a
    command).

It also pins the FACT that explains #7, so that a future change to the model
cannot quietly erase it: a key bound only in an Activity context is NOT
declared on the device's own page, which is why it does nothing while you are
standing on that device in Devices.

Runs entirely in memory. Writes no file, opens no window, touches no USB,
never imports `grabar.cargar`.

Usage:
    app/.venv/bin/python -P app/check_keys_reach.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "config_work"))

import add_device as D  # noqa: E402
import keys_reach as TA  # noqa: E402
import keys_physical as TF  # noqa: E402
import keys_map as TM  # noqa: E402

ANCLA = ROOT / "output" / "config_empaquetada.bin"
ANCLA_MD5 = "976bc70edd15b40f56cb49aa5113594f"

RESULTADOS: list[tuple[str, bool, str]] = []


def anota(name: str, ok: bool, detail: str = "") -> bool:
    RESULTADOS.append((name, bool(ok), detail))
    print(
        "  %-4s %s%s"
        % ("OK" if ok else "FAIL", name, "  -- " + detail if detail else "")
    )
    return bool(ok)


def _verdes(chequeos) -> tuple[bool, str]:
    malos = [c for c in chequeos if not c["ok"]]
    return not malos, "; ".join("%s: %s" % (c["name"], c["detail"]) for c in malos)


def main() -> int:
    if not ANCLA.exists():
        print("the anchor does not exist: %s" % ANCLA)
        return 2
    b = ANCLA.read_bytes()
    md5 = hashlib.md5(b).hexdigest()
    print("anchor %s  md5 %s" % (ANCLA.name, md5))
    anota(
        "the anchor is the blob written and verified on the remote",
        md5 == ANCLA_MD5,
        md5,
    )

    pages = {d["k1"]: d for d in TF.map_devices(b, str(TM.HUB_VOCAB))}
    print("\ndevice pages read from the blob:")
    for k1, d in sorted(pages.items()):
        print(
            "   k1=%d  %-14s page %3d  %2d rows  %2d editable  %d LCD pages"
            % (
                k1,
                d["name"],
                d["screen"],
                d["n_rows"],
                d["n_editables"],
                d["pages"],
            )
        )
    anota(
        "every device in the Devices menu has a page and editable keys",
        bool(pages) and all(d["n_editables"] > 0 for d in pages.values()),
        "%d devices" % len(pages),
    )

    # ------------------------------------------------------------------
    # 1. POSITIVE, both shapes: a factory page (the row already exists, 3
    #    bytes overwritten) and a page this project added (the row does not
    #    exist, the header is rebuilt and the trailer repointed).
    # ------------------------------------------------------------------
    print("\n1. the change REACHES, on every device page")
    codigo = 0x9B  # d-pad up
    for k1, d in sorted(pages.items()):
        screen = d["screen"]
        state = d["codigos"]["0x%02X" % codigo]["state"]
        try:
            out, rep, det = TF.apply_device(
                b, [{"screen": screen, "codigo": codigo, "k1": k1, "k2": 0}]
            )
        except ValueError as exc:
            anota("k1=%d page %d (%s)" % (k1, screen, state), False, str(exc))
            continue
        det = [dict(x, kind="device") for x in det]
        ok1, m1 = _verdes(TF.device_checks(b, out, det, rep))
        ok2, m2 = _verdes(TA.checks(b, out, det))
        r = TA.on_screen(out, screen, codigo)
        anota(
            "k1=%d page %d (%s): walk lands on the new command"
            % (k1, screen, state),
            ok1 and ok2 and r.get("cmd_id") == (k1 << 8),
            (m1 + " " + m2).strip() or TA._said(r),
        )

    # ------------------------------------------------------------------
    # 2. NEGATIVE -- the dead copy, reproduced on purpose.
    #    Take a good result whose header was REBUILT, and point the trailer
    #    back at the old header. Not a byte of what the writer wrote is
    #    touched: the new header, the new object and the new [5] record are
    #    all still in the file. Only the pointer changed. A check that reads
    #    "the record we edited" stays green here; this one must not.
    # ------------------------------------------------------------------
    print("\n2. the same bytes, reached by nobody: has to go RED")
    lg = max(pages.values(), key=lambda d: d["screen"])
    out, _rep, det = TF.apply_device(
        b, [{"screen": lg["screen"], "codigo": codigo, "k1": lg["k1"], "k2": 0}]
    )
    det = [dict(x, kind="device") for x in det]
    anota("the honest version is green", _verdes(TA.checks(b, out, det))[0])

    TM.set_t6(b)
    original = D.read_trailer(
        b, D.u24(b, D.T6 + 3 + 3 * lg["screen"]) - D.BASE, max_n=200
    )
    TM.set_t6(out)
    tr = D.read_trailer(
        out, D.u24(out, D.T6 + 3 + 3 * lg["screen"]) - D.BASE, max_n=200
    )
    huerfano = bytearray(out)
    huerfano[tr["off"] + 1 : tr["off"] + 4] = original["hdr"].to_bytes(3, "little")
    new_hdr = det[0]["new_header"]
    intactos = (
        bytes(huerfano[new_hdr : new_hdr + 40]) == out[new_hdr : new_hdr + 40]
    )
    ok, _m = _verdes(TA.checks(b, bytes(huerfano), det))
    anota("every byte the writer wrote is still in the file", intactos)
    anota("and the check goes RED anyway (nothing points at them)", not ok)

    # ------------------------------------------------------------------
    # 2b. THE OTHER DIRECTION: a check that goes red on a CORRECT write is
    #     just as expensive, because a red one aborts the whole Sync (see
    #     `changes._step_keys`). One batch, one screen, one key that
    #     already has a row and one that does not: the writer has to grow
    #     the register, so it rebuilds it whole and repoints the trailer --
    #     and the pre-rebuild address of the existing row becomes the dead
    #     copy BY DESIGN. Check (i) used to compare against that address and
    #     called a good write "the edit landed on a copy".
    #
    #     The last one is what keeps (i) honest while it is being relaxed: a
    #     byte-identical clone of the new register, placed 8 bytes past it,
    #     reached instead of it. It carries the right command, so (h) is
    #     green -- and it sits well inside the window an "is it near the new
    #     register" test would have accepted.
    # ------------------------------------------------------------------
    print("\n2b. a CORRECT write must not go red (a red one aborts the Sync)")
    fac = min(pages.values(), key=lambda d: d["screen"])
    with_row = next(
        c
        for c, v in fac["codigos"].items()
        if v.get("editable") and v.get("state") != "sin fila"
    )
    without_row = next(
        c
        for c, v in fac["codigos"].items()
        if v.get("editable") and v.get("state") == "sin fila"
    )
    mixto = [
        {"screen": fac["screen"], "codigo": int(c, 16), "k1": fac["k1"], "k2": k2}
        for c, k2 in ((with_row, 0), (without_row, 1))
    ]
    out, rep, det = TF.apply_device(b, mixto)
    det = [dict(x, kind="device") for x in det]
    llega = [TA.on_screen(out, fac["screen"], c["codigo"]) for c in mixto]
    anota(
        "page %d, %s (has a row) + %s (does not): both reach their command"
        % (fac["screen"], with_row, without_row),
        all(r.get("cmd_id") == (fac["k1"] << 8 | k) for r, k in zip(llega, (0, 1))),
        "; ".join(TA._said(r) for r in llega),
    )
    anota(
        "and every check is green (it used to say 'landed on a copy')",
        _verdes(TA.checks(b, out, det))[0]
        and _verdes(TF.device_checks(b, out, det, rep))[0],
        _verdes(TA.checks(b, out, det))[1],
    )

    rebuilt = det[0]["new_header"]
    largo = 1 + 4 * len(TA.register_rows(out, rebuilt))
    clon = bytearray(out)
    target = rebuilt + largo + 8
    clon[target : target + largo] = out[rebuilt : rebuilt + largo]
    TM.set_t6(out)
    tr = D.read_trailer(
        out, D.u24(out, D.T6 + 3 + 3 * fac["screen"]) - D.BASE, max_n=200
    )
    clon[tr["off"] + 1 : tr["off"] + 4] = (target + D.BASE).to_bytes(3, "little")
    ch = TA.checks(b, bytes(clon), det)
    inside = (
        rebuilt
        < TA.on_screen(bytes(clon), fac["screen"], mixto[0]["codigo"])["campo"]
        < rebuilt + 4096
    )
    anota(
        "a byte-identical clone of the new register still carries the command",
        next(c["ok"] for c in ch if c["name"].startswith("(h)")),
    )
    anota(
        "it lands inside the +4096 window a near-miss test would accept",
        inside,
    )
    anota(
        "and (i) goes RED anyway: reached register != written register",
        not next(c["ok"] for c in ch if c["name"].startswith("(i)")),
    )

    # ------------------------------------------------------------------
    # 3. THE FACT BEHIND #7, pinned. A key bound only in an Activity
    #    context is not declared on the device's own page: on that page the
    #    press never reaches the context, which is why the reassignment was
    #    invisible.
    # ------------------------------------------------------------------
    print("\n3. why #7 was invisible: the context is not the device page")
    ctx = next(
        (
            c["contexto"]
            for c in TF.mapear(b)["contextos"]
            if c["name"].startswith("Activity") and c["n_editables"]
        ),
        None,
    )
    if ctx is None:
        anota("this blob has an Activity with keys bound", False, "none found")
    else:
        out2, _rep2, det2 = TF.apply_physical(
            b, [{"contexto": ctx, "codigo": codigo, "k1": lg["k1"], "k2": 0}]
        )
        det2 = [dict(x, kind="fisica") for x in det2]
        ok_ctx, m_ctx = _verdes(TA.checks(b, out2, det2))
        anota("the context row itself is reached (the write is correct)", ok_ctx, m_ctx)
        en_pag = TA.on_screen(out2, lg["screen"], codigo)
        anota(
            "and on that device's own page the key is still NOT declared "
            "-- that is the whole bug",
            not en_pag.get("declarado"),
            en_pag.get("reason", ""),
        )
        sombra = TA.sombras(out2, codigo)
        anota(
            "the screens that consume the key before the context are counted",
            len(sombra) > 0,
            "%d screens: %s" % (len(sombra), sombra[:10]),
        )

    # ------------------------------------------------------------------
    # 4. The refusals. Each one is a way of writing something inert or
    #    dangerous, and each one has to abort BEFORE building anything.
    # ------------------------------------------------------------------
    print("\n4. what has to be refused, and is")
    pruebas = [
        (
            "a (k1,k2) outside section [5] (this one hangs the remote)",
            lambda: TF.apply_device(
                b, [{"screen": lg["screen"], "codigo": codigo, "k1": 9, "k2": 0}]
            ),
        ),
        (
            "a side strip 0xAE (it is the pager)",
            lambda: TF.apply_device(
                b,
                [{"screen": lg["screen"], "codigo": 0xAE, "k1": lg["k1"], "k2": 0}],
            ),
        ),
        (
            "a touchscreen zone on the page header (it lives per LCD page)",
            lambda: TF.apply_device(
                b,
                [{"screen": lg["screen"], "codigo": 0xB0, "k1": lg["k1"], "k2": 0}],
            ),
        ),
        (
            "a code the hardware does not emit",
            lambda: TF.apply_device(
                b,
                [{"screen": lg["screen"], "codigo": 0x55, "k1": lg["k1"], "k2": 0}],
            ),
        ),
        (
            "a touch row whose class is not 0x7F (its id is not an object)",
            lambda: TM.apply(
                b,
                [
                    {
                        "screen": lg["screen"],
                        "slot": 0,
                        "codigo": 0xAB,
                        "k1": lg["k1"],
                        "k2": 0,
                    }
                ],
            ),
        ),
    ]
    for name, fn in pruebas:
        try:
            fn()
            anota(name, False, "it was NOT refused")
        except ValueError as exc:
            anota(name, True, str(exc)[:90])

    # ------------------------------------------------------------------
    # 5. Round trip: assign and put it back. The page's key register has to
    #    end up saying exactly what it said at the start.
    # ------------------------------------------------------------------
    print("\n5. round trip on a factory page")
    tv = pages[min(pages)]
    before = TA.on_screen(b, tv["screen"], codigo)
    ida, _r1, _d1 = TF.apply_device(
        b, [{"screen": tv["screen"], "codigo": codigo, "k1": lg["k1"], "k2": 0}]
    )
    vuelta, _r2, _d2 = TF.apply_device(
        ida,
        [
            {
                "screen": tv["screen"],
                "codigo": codigo,
                "k1": before["k1"],
                "k2": before["k2"],
            }
        ],
    )
    after = TA.on_screen(vuelta, tv["screen"], codigo)
    anota(
        "the key comes back to the command it had",
        before.get("cmd_id") == after.get("cmd_id")
        and before.get("dev_id") == after.get("dev_id"),
        "%s -> %s" % (TA._said(before), TA._said(after)),
    )

    fallos = [n for n, ok, _d in RESULTADOS if not ok]
    print("\n" + "=" * 72)
    print(
        "LOAD-BEARING CHECK: %s   (%d checks, %d failed)"
        % ("PASSED" if not fallos else "FAILED", len(RESULTADOS), len(fallos))
    )
    for n in fallos:
        print("   FAILED: %s" % n)
    return 0 if not fallos else 1


if __name__ == "__main__":
    sys.exit(main())
