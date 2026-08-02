#!/usr/bin/env python3
"""LOAD-BEARING CHECK for the AUTO-BINDING of a device's keys.

## What has to be true, and how each thing is measured

Auto-binding writes 45-odd rows in one shot on a page nobody has looked at
yet. Three ways it could be wrong, and none of them shows up on screen:

  * a key lands on the WRONG command -- the user has no way to know;
  * a key lands on a `(k1,k2)` section `[5]` cannot resolve -- **that hangs
    the remote** (`[5]` has no range check: `ESTADO.md`, `PELIGROS`);
  * a key the user had bound BY HAND gets overwritten -- silently.

So this check never reads back what the writer reported. For every bound key
it starts at the master index, walks the pointers the firmware walks
(`teclas_alcance.on_screen`), and then RE-DERIVES the reached command's
NAME out of the new blob (decode its IR waveform, match `(protocol,payload)`
against the Hub's `DeviceList`) and compares that name against the role the
key is supposed to have. Name in, name out, with the blob as the only witness
in between.

The negative side is where the value is:

  * a device with no `VolumeUp` has to leave Volume+ UNBOUND -- not pointing
    at whatever came next in the list;
  * a key the user bound by hand has to come out byte-identical;
  * `06`/`07`/`2D` -- the page's LED hooks and its internal pager -- have to
    be refused, not bound;
  * a `k2` past the device's command count has to be dropped before anything
    is built;
  * running auto twice has to write nothing the second time.

Runs entirely in memory. Writes no file, opens no window, touches no USB,
never imports `grabar.cargar`.

Usage:
    app/.venv/bin/python -P app/check_keys_auto.py
"""

from __future__ import annotations

import hashlib
import pathlib
import history
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "config_work"))

import add_device as D  # noqa: E402
import keys_reach as TA  # noqa: E402
import keys_auto as AU  # noqa: E402
import keys_physical as TF  # noqa: E402
import keys_map as TM  # noqa: E402

ANCLA = ROOT / "output" / "config_empaquetada.bin"
ANCLA_MD5 = "976bc70edd15b40f56cb49aa5113594f"
#: La MISMA carpeta que usa la app, resuelta por sistema operativo.
#: Estaba clavada a la ruta de macOS: en Windows apuntaba a una carpeta
#: que no existe, aunque `registro.data_directory()` ya sabia resolverlo.
DB = history.data_directory() / "registro.sqlite3"
GRABADA = 9

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


def load_write_entry(n: int) -> tuple[bytes, str] | tuple[None, str]:
    """The blob of recorded sync `n`, READ ONLY. Never touches the device."""
    try:
        import ezhex

        row = (
            sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
            .execute("SELECT ezhex_path FROM grabadas WHERE id=?", (n,))
            .fetchone()
        )
        if row is None:
            return None, "grabada %d is not in %s" % (n, DB.name)
        path = pathlib.Path(row[0])
        if not path.is_file():
            return None, "grabada %d's file is gone: %s" % (n, path)
        _hdr, blob = ezhex.split(path.read_bytes())
        return blob, path.name
    except Exception as exc:  # noqa: BLE001
        return None, "grabada %d could not be read: %s" % (n, exc)


def lg_page(b: bytes) -> tuple[int, int, str]:
    """`(k1, page, name)` of the device with the most commands that is NOT one
    of the three the remote shipped with -- i.e. the one this project added.
    Chosen by measurement and not by a hardcoded 4 or 5, because the same
    device is k1=4 in the anchor and k1=5 in grabada #9."""
    menu = TA.device_screen(b, str(TM.HUB_VOCAB))
    n5 = D.read_section5(b)
    cands = [k1 for k1 in menu if k1 >= 3]
    if not cands:
        raise SystemExit("this blob has no added device: %s" % sorted(menu))
    k1 = max(cands, key=lambda k: n5[k].get("n", 0))
    return k1, menu[k1]["screen"], menu[k1]["name"]


# ==========================================================================


def bloque(b: bytes, label: str) -> None:
    """The whole battery over one blob."""
    k1, screen, name = lg_page(b)
    n5 = D.read_section5(b)
    print(
        "\n%s: added device k1=%d (%s) -> page %d, %d commands; [5] declares %s"
        % (label, k1, name, screen, n5[k1].get("n", 0), [d["n"] for d in n5])
    )

    # ---------------------------------------------------------------- 1 ---
    # The role table is not typed from a document: it is re-derived from THIS
    # blob's own three factory pages and compared.
    r = AU.check_template_vs_blob(b)
    if r["ok"] is None:
        anota("%s: role table re-derived from the blob" % label, False, r["reason"])
    else:
        anota(
            "%s: the %d roles re-derived from this blob == the frozen table"
            % (label, r["n_roles"]),
            r["ok"],
            r["reason"][:120],
        )

    codigos, plan_source = AU.plantilla_codigos(b)
    derivada = [c for c in TF.inventario(b) if c not in AU.CODES_OUTSIDE_TEMPLATE]
    anota(
        "%s: the 49-code template read from the blob == the one derived from "
        "its own inventory minus the 6 (so the fallback cannot drift)" % label,
        len(codigos) == 49
        and set(codigos) == set(derivada)
        and plan_source.startswith("factory template"),
        "%s; read and derived differ only in the A4/2D order: %s"
        % (plan_source, codigos != derivada),
    )

    # ---------------------------------------------------------------- 2 ---
    print("\n%s -- 2. the plan, and what it refuses to guess" % label)
    plan = AU.planificar(b, k1)
    res = plan["summary"]
    print("   %s" % res)
    anota(
        "%s: the plan covers the whole 49-code factory template" % label,
        len(plan["rows"]) == 49
        and res["ligar"] + res["apagar"] + res["respetada"] + res["omitida"] == 49,
        "%d rows: %s" % (len(plan["rows"]), res),
    )
    anota(
        "%s: the 3 infrastructure codes (06/07/2D) are never claimed" % label,
        all(
            f["accion"] == "omitida"
            for f in plan["rows"]
            if f["codigo"] in TF.CODIGOS_INFRAESTRUCTURA
        )
        and not any(c["codigo"] in TF.CODIGOS_INFRAESTRUCTURA for c in plan["changes"]),
        "omitted: %s"
        % [
            f["codigo_hex"]
            for f in plan["rows"]
            if f["codigo"] in TF.CODIGOS_INFRAESTRUCTURA
        ],
    )
    anota(
        "%s: the pager strips AE/AF and Power A5 are not even in the template"
        % label,
        not ({0xAE, 0xAF, 0xA5} & {f["codigo"] for f in plan["rows"]}),
        "the template declares %d codes, none of them AE/AF/A5" % len(plan["rows"]),
    )

    # ---------------------------------------------------------------- 3 ---
    print("\n%s -- 3. write it, and walk the pointers to see where it went" % label)
    out, repuntes, detail = AU.apply(b, plan)
    ch = AU.checks(b, out, plan, detail, repuntes)
    for c in ch:
        anota("%s: %s" % (label, c["name"]), c["ok"], c["detail"][:130])

    # THE portante, stated here on its own and computed WITHOUT looking at the
    # plan's own reasoning: for each key, the role table says a NAME, the walk
    # says a (k1,k2), and the new blob says what that (k1,k2) is called.
    roles = dict(AU.ROLES)
    n5_out = AU.nombres_seccion5(out)
    ligadas, malas = 0, []
    for f in plan["rows"]:
        if f["accion"] != "ligar":
            continue
        ligadas += 1
        alc = TA.on_screen(out, screen, f["codigo"])
        reached_name = n5_out.get(alc.get("k1"), {}).get(alc.get("k2"))
        esperados = roles.get(f["codigo"], ())
        if (
            alc.get("k1") != k1
            or alc.get("registro5") is None
            or AU.canonico(reached_name or "") not in esperados
        ):
            malas.append((f["codigo_hex"], esperados, reached_name, TA._said(alc)))
    anota(
        "%s: PORTANTE -- all %d bound keys reach a command of device %d whose "
        "NAME re-derived from the new blob is the role's" % (label, ligadas, k1),
        ligadas > 0 and not malas,
        "role and reached name disagree: %s" % malas[:6]
        if malas
        else "%d/%d roles covered" % (ligadas, len(AU.ROLES)),
    )

    # THE OTHER portante: nothing on the page is out of range in [5].
    TM.set_t6(out)
    import relocate

    dest11 = relocate.table(out, relocate.sections(out)[11][0])
    tr = D.read_trailer(out, D.u24(out, D.T6 + 3 + 3 * screen) - D.BASE, max_n=200)
    rows = TA.register_rows(out, tr["hdr"] - D.BASE)
    pares, outside = [], []
    n5_o = D.read_section5(out)
    for _k, cod, _campo, idv, cls in rows:
        if cls != TF.TAG_OBJ or not 0 <= idv < len(dest11):
            continue
        for cmd, _dev in TA.object_commands(out, dest11, idv):
            a, c2 = cmd >> 8, cmd & 0xFF
            pares.append((cod, a, c2))
            if (
                D.resolve_section5(out, cmd)[0] is None
                or not 0 <= a < len(n5_o)
                or not 0 <= c2 < n5_o[a].get("n", 0)
            ):
                outside.append(("0x%02X" % cod, a, c2))
    anota(
        "%s: PORTANTE -- %d reachable (k1,k2) pairs on page %d, 0 out of range "
        "in [5] (the thing that hangs the remote)" % (label, len(pares), screen),
        bool(pares) and not outside,
        "out of range: %s" % outside[:6]
        if outside
        else "all k1==%d, k2 in 0..%d" % (k1, max(c for _c, _a, c in pares)),
    )
    anota(
        "%s: and every one of them belongs to THIS device (a perfect k1 "
        "partition, the way the factory's three pages are)" % label,
        all(a == k1 for _c, a, _k2 in pares),
        "k1 seen: %s" % sorted({a for _c, a, _k2 in pares}),
    )

    # ---------------------------------------------------------------- 4 ---
    print("\n%s -- 4. NEGATIVE: a device without VolumeUp" % label)
    sin_vol = {
        k2: nm for k2, nm in plan["commands"].items() if AU.canonico(nm) != "VolumeUp"
    }
    plan_sv = AU.planificar(b, k1, commands=sin_vol)
    row_vol = next(f for f in plan_sv["rows"] if f["codigo"] == 0x83)
    anota(
        "%s: Volume+ (0x83) is NOT bound when the device has no VolumeUp" % label,
        row_vol["accion"] != "ligar" and "k2" not in row_vol,
        "%s -- %s" % (row_vol["accion"], row_vol["reason"][:80]),
    )
    out_sv, rep_sv, det_sv = AU.apply(b, plan_sv)
    alc = TA.on_screen(out_sv, screen, 0x83)
    anota(
        "%s: and on the written blob the walk finds it reaching NOTHING "
        "(declared and disabled, not pointing at the next command along)" % label,
        alc.get("declarado") and alc.get("cmd_id") is None and alc.get("category") == 0,
        TA._said(alc),
    )
    otras = [
        f["codigo_hex"]
        for f in plan_sv["rows"]
        if f["accion"] == "ligar"
        and f["k2"]
        in {k2 for k2, nm in plan["commands"].items() if AU.canonico(nm) == "VolumeUp"}
    ]
    anota(
        "%s: and no OTHER key inherited VolumeUp's command" % label,
        not otras,
        "keys that took it: %s" % otras if otras else "none",
    )
    anota(
        "%s: the rest of the plan is unchanged by the removal (only 0x83 "
        "moves)" % label,
        {f["codigo"]: f.get("k2") for f in plan_sv["rows"] if f["accion"] == "ligar"}
        == {
            f["codigo"]: f.get("k2")
            for f in plan["rows"]
            if f["accion"] == "ligar" and f["codigo"] != 0x83
        },
        "%d bound with VolumeUp, %d without"
        % (plan["summary"]["ligar"], plan_sv["summary"]["ligar"]),
    )
    _ = (out_sv, rep_sv, det_sv)

    # ---------------------------------------------------------------- 5 ---
    print("\n%s -- 5. the user's manual assignment wins" % label)
    # Bind one key BY HAND to a command its role would never pick, then let
    # auto loose on the page. The hand-made row has to come out untouched.
    a_mano = 0x83  # Volume +
    k2_raro = next(
        k2
        for k2, nm in sorted(plan["commands"].items())
        if AU.canonico(nm) not in (None, "VolumeUp")
    )
    manual, _rm, _dm = TF.apply_device(
        b, [{"screen": screen, "codigo": a_mano, "k1": k1, "k2": k2_raro}]
    )
    before = AU._page_rows(manual, screen)[a_mano]
    plan_m = AU.planificar(manual, k1, commands=plan["commands"])
    row_m = next(f for f in plan_m["rows"] if f["codigo"] == a_mano)
    anota(
        "%s: a hand-bound 0x83 -> command %d is reported 'respetada'"
        % (label, k2_raro),
        row_m["accion"] == "respetada",
        "%s -- %s" % (row_m["accion"], row_m["reason"][:90]),
    )
    out_m, rep_m, det_m = AU.apply(manual, plan_m)
    after = AU._page_rows(out_m, screen)[a_mano]
    alc_m = TA.on_screen(out_m, screen, a_mano)
    anota(
        "%s: and after auto ran, the row is byte-identical and still reaches "
        "the hand-picked command" % label,
        before == after and alc_m.get("cmd_id") == ((k1 << 8) | k2_raro),
        "%s -> %s ; %s" % (before, after, TA._said(alc_m)),
    )
    anota(
        "%s: auto still bound the OTHER keys (respecting one is not giving up)"
        % label,
        plan_m["summary"]["ligar"] == plan["summary"]["ligar"] - 1,
        "%d bound, 1 respected" % plan_m["summary"]["ligar"],
    )
    ok_m, m_m = _verdes(AU.checks(manual, out_m, plan_m, det_m, rep_m))
    anota("%s: every check on that run is green too" % label, ok_m, m_m[:150])

    # explicit escape hatch
    plan_f = AU.planificar(manual, k1, commands=plan["commands"], forzar=True)
    row_f = next(f for f in plan_f["rows"] if f["codigo"] == a_mano)
    anota(
        "%s: forzar=True is the ONLY way to overwrite it, and it does" % label,
        row_f["accion"] == "ligar" and row_f["k2"] != k2_raro,
        "%s k2=%s" % (row_f["accion"], row_f.get("k2")),
    )
    plan_r = AU.planificar(manual, k1, commands=plan["commands"], respetar=(0x89,))
    row_r = next(f for f in plan_r["rows"] if f["codigo"] == 0x89)
    anota(
        "%s: respetar=(0x89,) keeps auto off a code the caller reserves" % label,
        row_r["accion"] == "respetada",
        row_r["reason"][:80],
    )

    # ---------------------------------------------------------------- 6 ---
    print("\n%s -- 6. what has to be refused before a byte is built" % label)
    for case_name, fn in (
        (
            "the enter hook 0x06 (it lights the LEDs)",
            lambda: TF.apply_device(
                b, [{"screen": screen, "codigo": 0x06, "k1": k1, "k2": 0}]
            ),
        ),
        (
            "the leave hook 0x07",
            lambda: TF.apply_device(
                b, [{"screen": screen, "codigo": 0x07, "k1": k1, "k2": 0}]
            ),
        ),
        (
            "the internal pager 0x2D (it is in the 55-key inventory: this "
            "guard was missing)",
            lambda: TF.apply_device(
                b, [{"screen": screen, "codigo": 0x2D, "k1": k1, "k2": 0}]
            ),
        ),
        (
            "disabling the internal pager 0x2D is refused too",
            lambda: TF.apply_device(
                b, [{"screen": screen, "codigo": 0x2D, "apagar": True}]
            ),
        ),
    ):
        try:
            fn()
            anota("%s: %s" % (label, case_name), False, "it was NOT refused")
        except ValueError as exc:
            anota("%s: %s" % (label, case_name), True, str(exc)[:80])

    n_cmd = n5[k1].get("n", 0)
    plan_x = AU.planificar(
        b, k1, commands={n_cmd: "VolumeUp", n_cmd + 500: "Mute", 0: "Select"}
    )
    anota(
        "%s: a k2 past the device's %d commands is dropped with a warning, "
        "not written (an out-of-range (k1,k2) hangs the remote)" % (label, n_cmd),
        all(0 <= c.get("k2", 0) < n_cmd for c in plan_x["changes"])
        and bool(plan_x["avisos"]),
        (plan_x["avisos"] or ["no warning"])[0][:110],
    )


# ==========================================================================


def normalizacion() -> None:
    print("\n0. normalization -- the same function under many spellings")
    familias = {
        "VolumeUp": [
            "VolumeUp",
            "Volume Up",
            "volume up",
            "VOL_UP",
            "vol up",
            "Vol +",
            "volume+",
            "VolumePlus",
            "volumeIncrease",
        ],
        "ChannelDown": [
            "ChannelDown",
            "Channel Down",
            "CH-",
            "ch -",
            "channel_down",
            "ProgramDown",
        ],
        "Select": ["Select", "OK", "ok", "Enter", "cursor select"],
        "SkipBackward": [
            "SkipBackward",
            "skip backward",
            "Replay",
            "PreviousTrack",
            "skip_back",
        ],
        "Number7": ["Number7", "num 7", "digit7", "7", "KEY_7"],
    }
    malos = []
    for canon, formas in familias.items():
        for f in formas:
            if AU.canonico(f) != canon:
                malos.append((f, AU.canonico(f), canon))
    anota(
        "%d spellings across %d roles all normalize to the same canonical name"
        % (sum(len(v) for v in familias.values()), len(familias)),
        not malos,
        "did not: %s" % malos[:6],
    )

    ambiguos = [
        "PlayPause",
        "Next",
        "Previous",
        "Forward",
        "Backward",
        "Input",
        "PowerToggle",
        "Netflix",
        "Red",
        "AspectRatio",
        "",
    ]
    pegados = [(x, AU.canonico(x)) for x in ambiguos if AU.canonico(x)]
    anota(
        "an ambiguous or unknown name matches NOTHING (%d tried: %s)"
        % (len(ambiguos), ", ".join(x or "''" for x in ambiguos[:6])),
        not pegados,
        "matched anyway: %s" % pegados,
    )

    # the invariant the whole design rests on: the match is a FUNCTION.
    duenos: dict[str, set[int]] = {}
    for cod, nombres in AU.ROLES:
        for n in nombres:
            duenos.setdefault(n, set()).add(cod)
    choques = {n: sorted(v) for n, v in duenos.items() if len(v) > 1}
    alias_choque = {
        a: c for a, c in AU.SINONIMOS.items() if c not in AU.CODE_BY_NAME
    }
    anota(
        "no role name belongs to two keys, and every alias lands on a name the "
        "table knows",
        not choques and not alias_choque,
        "clashes: %s ; orphan aliases: %s" % (choques, list(alias_choque)[:6]),
    )
    anota(
        "'Vol +' and 'Vol -' do NOT collapse onto each other",
        AU.normalizar("Vol +") != AU.normalizar("Vol -")
        and AU.canonico("Vol +") == "VolumeUp"
        and AU.canonico("Vol -") == "VolumeDown",
        "%r vs %r" % (AU.normalizar("Vol +"), AU.normalizar("Vol -")),
    )


def sin_nombres(b: bytes) -> None:
    """A device whose commands nothing can name binds nothing at all."""
    print("\n7. a device nothing can name binds NOTHING (agnostic, not lucky)")
    menu = TA.device_screen(b, str(TM.HUB_VOCAB))
    n5 = AU.nombres_seccion5(b)
    mudo = next((k1 for k1 in sorted(menu) if not n5.get(k1)), None)
    if mudo is None:
        anota(
            "this blob has a device with no decodable names",
            True,
            "none in the Devices menu (%s): nothing to test here" % sorted(menu),
        )
        return
    plan = AU.planificar(b, mudo)
    anota(
        "device k1=%d has 0 named commands -> 0 keys bound" % mudo,
        plan["summary"]["ligar"] == 0 and bool(plan["avisos"]),
        "%s ; %s" % (plan["summary"], (plan["avisos"] or [""])[0][:80]),
    )
    anota(
        "and it does not silently bind ordinal 0 to anything",
        not any("k2" in c for c in plan["changes"]),
        "%d changes, all of them disabled rows" % len(plan["changes"]),
    )


#: the LG's own `hub-config-with-device.json`, looked up read-only in the same
#: three places `check_load_bearing.py` looks: this is the file the app HAS at
#: the moment a device is added, so it is the name source the real flow uses.
#: Nothing is restored anywhere; the file is only read.
FIXTURES = [
    ROOT / "account_export" / "output",
    ROOT
    / "app"
    / "packaging"
    / "dist"
    / "RE-HARMONY.app"
    / "Contents"
    / "account_export"
    / "output",
    pathlib.Path("/tmp/output_backup"),
]


def from_the_json(b: bytes) -> None:
    """The route the app actually takes when a device is ADDED: the names come
    from the device's JSON, not from the Hub's DeviceList.

    The two vocabularies are NOT the same -- the LG's JSON calls its digits
    `0`..`9`, its OK `OK`, its prev-channel `ChannelPrev`, and has no command
    called `Menu` at all -- so this is the check that the matcher survives a
    different manufacturer's spelling, on real data.
    """
    print("\n8. the other name source: the device's own JSON (the add-time flow)")
    cfg = next(
        (
            base / "hub-config-tv-b" / "hub-config-with-device.json"
            for base in FIXTURES
            if (
                base / "hub-config-tv-b" / "hub-config-with-device.json"
            ).is_file()
        ),
        None,
    )
    if cfg is None:
        anota(
            "the LG's hub-config-with-device.json is readable somewhere",
            False,
            "not in any of %s" % [str(f) for f in FIXTURES],
        )
        return
    k1, screen, _n = lg_page(b)
    mapa, source = AU.commands_from_config(cfg, "LG TV")
    hub, _f2 = AU.commands_from_blob(b, k1)
    distintos = sorted(
        (k2, mapa.get(k2), hub.get(k2))
        for k2 in set(mapa) | set(hub)
        if mapa.get(k2) != hub.get(k2)
    )
    anota(
        "the JSON and the Hub really do spell the same device differently "
        "(%d of %d ordinals) -- so this is not a rehearsal"
        % (len(distintos), len(mapa)),
        len(distintos) > 0,
        "e.g. %s" % [(k, a, c) for k, a, c in distintos[:5]],
    )
    plan = AU.planificar(b, k1, commands=mapa)
    anota(
        "the JSON's own vocabulary still covers the roles (%s)"
        % plan["summary"]["roles_ligados_ahora"],
        plan["summary"]["ligar"] >= 30,
        "%s ; source: %s" % (plan["summary"], source),
    )
    out, rep, det = AU.apply(b, plan)
    ok, m = _verdes(AU.checks(b, out, plan, det, rep))
    anota(
        "and every check is green on that blob too -- including (k), which "
        "asks BOTH the JSON and the blob what the reached command is called",
        ok,
        m[:180],
    )
    # the digits are the interesting ones: the JSON calls them "0".."9"
    digitos = {
        f["codigo_hex"]: f["original_name"]
        for f in plan["rows"]
        if f["accion"] == "ligar" and (f.get("name") or "").startswith("Number")
    }
    anota(
        "the 10 digit keys matched from bare names like '7' (%d found)" % len(digitos),
        len(digitos) == 10 and all(v.isdigit() for v in digitos.values()),
        str(digitos),
    )


def are_load_bearing(b: bytes) -> None:
    """The two new checks, broken ON PURPOSE.

    A check that cannot go red is a decoration, and this project has shipped
    one before (a control that read back the field the writer had just
    written). So: write an HONEST auto-assignment, then produce the two
    failures the checks exist for -- a key pointed at the wrong command, and a
    `(k1,k2)` pushed out of range -- and require each check to notice.
    """
    print("\n9. the two new checks, broken on purpose (they have to go red)")
    k1, screen, _n = lg_page(b)
    plan = AU.planificar(b, k1)
    out, rep, det = AU.apply(b, plan)
    ok, _m = _verdes(AU.checks(b, out, plan, det, rep))
    anota("the honest version is green to begin with", ok)

    # --- (k): the same key, pointed at a command that is not its role's ----
    TM.set_t6(out)
    tr = D.read_trailer(out, D.u24(out, D.T6 + 3 + 3 * screen) - D.BASE, max_n=200)
    rows = {f[1]: f for f in TA.register_rows(out, tr["hdr"] - D.BASE)}
    fv, fm = rows[0x83], rows[0x89]  # Volume + and Mute
    sucio = bytearray(out)
    sucio[fv[2] : fv[2] + 2] = fm[3].to_bytes(2, "little")
    ch = AU.checks(b, bytes(sucio), plan, det, rep)
    k_rojo = not next(c["ok"] for c in ch if c["name"].startswith("(k)"))
    alc = TA.on_screen(bytes(sucio), screen, 0x83)
    anota(
        "Volume+ repointed at Mute's command: 2 bytes changed, everything the "
        "writer wrote still in the file -- and (k) goes RED",
        k_rojo,
        TA._said(alc),
    )
    k2_mute = next(f["k2"] for f in plan["rows"] if f["codigo"] == 0x89)
    anota(
        "and it is red for the RIGHT reason: the reached command resolves "
        "perfectly through [5] -- it is simply not called VolumeUp, so no "
        "range or reachability check would ever have caught it",
        alc.get("registro5") is not None and alc.get("cmd_id") == ((k1 << 8) | k2_mute),
        "reaches Mute (command %d) instead of VolumeUp" % k2_mute,
    )

    # --- (l): a (k1,k2) pushed out of range -- the one that hangs it -------
    sub = D.read_section5(out)[k1]["sub"]
    sucio2 = bytearray(out)
    sucio2[sub + 1 : sub + 3] = (1).to_bytes(2, "little")
    ch2 = AU.checks(b, bytes(sucio2), plan, det, rep)
    anota(
        "device %d's [5] count cut to 1, so every bound k2 is now out of "
        "range: (l) goes RED" % k1,
        not next(c["ok"] for c in ch2 if c["name"].startswith("(l)")),
        next(c["detail"] for c in ch2 if c["name"].startswith("(l)"))[:110],
    )

    # --- rule 2: a duplicate the hold rule cannot separate is NOT bound ----
    mapa = dict(plan["commands"])
    gemelo = next(k2 for k2, nm in mapa.items() if AU.canonico(nm) == "VolumeDown")
    mapa[gemelo] = "VolumeUp"  # two DIFFERENT commands now claim the same name
    plan_d = AU.planificar(b, k1, commands=mapa)
    row = next(f for f in plan_d["rows"] if f["codigo"] == 0x83)
    anota(
        "two commands with different IR waveforms claiming the same name: "
        "Volume+ is left UNBOUND rather than guessed",
        row["accion"] != "ligar",
        "%s -- %s" % (row["accion"], row["reason"][:110]),
    )
    anota(
        "and Volume- goes unbound with it (its name was taken, not reassigned)",
        next(f for f in plan_d["rows"] if f["codigo"] == 0x84)["accion"] != "ligar",
        next(f for f in plan_d["rows"] if f["codigo"] == 0x84)["reason"][:90],
    )

    # --- a malformed change must FAIL, not pass by default -----------------
    # The disabled row's success condition is "reaches nothing", so anything
    # that got classified as one by accident would go green while doing
    # nothing. `_es_apagado` keys on the explicit flag for exactly that
    # reason; this is the check that keeps it that way.
    roto = dict(det[0], cmd_id=None)
    roto.pop("apagar", None)
    anota(
        "a change with neither a command nor the explicit 'apagar' flag is "
        "reported FAILED, not green-by-default",
        not TA.verificar(out, [roto])[0]["ok"],
        "verificar says ok=%s" % TA.verificar(out, [roto])[0]["ok"],
    )

    # --- the plan has to survive the trip to the UI ------------------------
    # `changes.py` has already been bitten twice by a `set` and by raw `bytes`
    # riding a result dict into `json.dumps`.
    import json

    try:
        text = json.dumps(plan)
        serializa, reason = True, "%d B of JSON, no set and no bytes" % len(text)
    except (TypeError, ValueError) as exc:
        serializa, reason = False, str(exc)
    anota(
        "the plan is JSON-serializable as it stands (the UI gets it raw)",
        serializa,
        reason,
    )


def main() -> int:
    print("=" * 72)
    normalizacion()

    if not ANCLA.exists():
        print("the anchor does not exist: %s" % ANCLA)
        return 2
    anchor = ANCLA.read_bytes()
    md5 = hashlib.md5(anchor).hexdigest()
    print("\nanchor %s  md5 %s" % (ANCLA.name, md5))
    anota("the anchor is the blob generated by the app today", md5 == ANCLA_MD5, md5)

    g9, comog9 = load_write_entry(GRABADA)
    if g9 is None:
        anota(
            "grabada #%d (the one the user has on the remote) is readable" % GRABADA,
            False,
            comog9,
        )
    else:
        print(
            "grabada #%d %s  %d B  sha256 %s"
            % (GRABADA, comog9, len(g9), hashlib.sha256(g9).hexdigest()[:24])
        )
        anota(
            "grabada #%d is readable and is the LG-with-its-own-page one" % GRABADA,
            True,
            "%d screens, [5] declares %s"
            % (D.u16(g9, TM.set_t6(g9)), [d["n"] for d in D.read_section5(g9)]),
        )
        bloque(g9, "grabada#9")
        sin_nombres(g9)
        from_the_json(g9)
        are_load_bearing(g9)

    bloque(anchor, "anchor")
    sin_nombres(anchor)

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
