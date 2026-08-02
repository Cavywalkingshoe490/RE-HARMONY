#!/usr/bin/env python3
"""Check for the "upload an `.ir` by hand" path (`app/ir_manual.py`), with its
negative.

Any check that can't fail doesn't check anything, so every positive block has
its negative right next to it. NONE of this touches the device or the
account: it only reads the reference blob, runs `add_device.py` by
SUBPROCESS (which writes a `.bin` to `output/`, never to flash), and
previews the gate.

The six blocks:

1. FRAMING over a REAL Flipper-IRDB `.ir` (`app/tests/example_tv.ir`).
   The waveform coming out of `commands.press_wave()` cannot have TWO
   ADJACENT MARKS: the factory blob has zero, and two marks stuck together
   are one continuous carrier (the device sees a different keypress). This
   is the check that catches the defect of flattening every command into a
   fixed segment with `TotalLength = sum(atoms)` (gap 0) when the capture
   ends on a mark -- which is the COMMON case: the Flipper does not record
   the trailing silence, which is why `data:` almost always carries an ODD
   count of numbers.
2. GAP NOT DUPLICATED. If the `.ir` DOES carry the trailing silence, that
   silence is the gap between frames and is respected as is; adding 40 ms
   on top would double it. The emitted gap is measured and has to be
   exactly the file's.
3. PARSED against the already-validated path. A `type: parsed` command has
   to give the SAME waveform, word for word, as `sintir.sintetizar()` over
   the Hub's REAL `ProtocolList` -- the project's synthesis validated
   234/234.
4. LABELS. The 40 standard names from a Flipper `.ir` (`Vol_up`, `Ch_next`,
   `Fast_Forward`, `Vol+`, ...) have to end up as labels the remote can
   DRAW, WRITE, and fit in the 81 px cell. Negative: a name with `Q` (the
   hardware has no such glyph) and one that's too long both have to end up
   NOT importable, with a reason.
5. Parser/boundary NEGATIVES: a malformed `.ir`, a protocol without a
   verified formula, `address`/`command` outside the protocol's range
   (would truncate silently), and a mark longer than a blob word can hold
   (would get clipped in silence). All four get rejected.
6. E2E: import -> `add_device.py` (subprocess) -> gate
   (`grabar.nothing_moved`) with its negative. The test device gets deleted
   no matter what happens.

Usage:  ./app/.venv/bin/python app/check_ir_manual.py [--rapido]
        (`--rapido` skips block 6, which really runs `add_device.py`)
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys
import tempfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "config_work"))

from app import library  # noqa: E402
from app import generate as generar_mod  # noqa: E402
from app import ir_manual  # noqa: E402

import command_records  # noqa: E402
import synth_ir  # noqa: E402

BLOB = RAIZ / "backups" / "config_raw.bin"
IR_REAL = RAIZ / "app" / "tests" / "example_tv.ir"

#: names exactly as they come from the public IRDB / from the Flipper's
#: `universal/tv.ir`. This is the brief's actual use case, not a hand-made file.
NOMBRES_FLIPPER = [
    "Power",
    "Vol_up",
    "Vol_dn",
    "Ch_next",
    "Ch_prev",
    "Mute",
    "Menu",
    "Ok",
    "Up",
    "Down",
    "Left",
    "Right",
    "Back",
    "Exit",
    "Home",
    "Play",
    "Pause",
    "Stop",
    "Rec",
    "Fast_Forward",
    "Rewind",
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
    "Info",
    "Source",
    "Subtitle",
    "Sleep",
    "Guide",
    "Vol+",
    "Vol-",
    "Ch+",
    "Ch-",
]

OK = 0
FAIL = 0


def check(cond: bool, text: str) -> bool:
    global OK, FAIL
    if cond:
        OK += 1
        print("  OK    %s" % text)
    else:
        FAIL += 1
        print("  FAIL  %s" % text)
    return bool(cond)


def words_of(onda: bytes) -> list[int]:
    return [int.from_bytes(onda[i : i + 2], "little") for i in range(0, len(onda), 2)]


def adjacent_marks(pal: list[int]) -> int:
    return sum(
        1 for k in range(len(pal) - 1) if (pal[k] & 0x8000) and (pal[k + 1] & 0x8000)
    )


def space_runs(pal: list[int]) -> list[int]:
    """Contiguous silences, in us (the blob splits long ones into several
    words, so they have to be added back together)."""
    out, cur = [], 0
    for w in pal:
        if w & 0x8000:
            if cur:
                out.append(cur)
                cur = 0
        else:
            cur += w & 0x7FFF
    if cur:
        out.append(cur)
    return out


def wave_for(supported_commands, which: str) -> tuple[list[int], str, int]:
    res = ir_manual.build_resources(supported_commands, "F", "M", "Dev")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump({"resources": res}, fh)
        path = fh.name
    try:
        protos, devs = command_records.load_hub_config(path)
        _, dev = command_records.choose_device(devs, "Dev")
        cmds, saltados = command_records.commands_of(dev, protos)
        assert not saltados, saltados
        name, proto, value = next(c for c in cmds if c[0] == which)
        return words_of(command_records.press_wave(protos[proto], value)), proto, value
    finally:
        pathlib.Path(path).unlink(missing_ok=True)


def parse_ir(text: str, blob: bytes) -> list[dict]:
    with tempfile.NamedTemporaryFile("w", suffix=".ir", delete=False) as fh:
        fh.write(text)
        path = fh.name
    try:
        r = ir_manual._parse_commands(pathlib.Path(path))
        if not r["ok"]:
            return [{"name": "(file)", "soportado": False, "reason": r["error"]}]
        ir_manual._mark_impossible_labels(r["commands"], blob)
        return r["commands"]
    finally:
        pathlib.Path(path).unlink(missing_ok=True)


def ir_raw(name: str, us: list[int], frecuencia: int = 38000) -> str:
    return (
        "Filetype: IR signals file\nVersion: 1\n#\nname: %s\ntype: raw\n"
        "frequency: %d\nduty_cycle: 0.330000\ndata: %s\n"
        % (name, frecuencia, " ".join(str(x) for x in us))
    )


def ir_parsed(name: str, proto: str, addr: int, cmd: int) -> str:
    def le(v):
        return " ".join("%02X" % ((v >> (8 * i)) & 0xFF) for i in range(4))

    return (
        "Filetype: IR signals file\nVersion: 1\n#\nname: %s\ntype: parsed\n"
        "protocol: %s\naddress: %s\ncommand: %s\n" % (name, proto, le(addr), le(cmd))
    )


# --------------------------------------------------------------------------


def block_1_framing(blob: bytes) -> None:
    print("\n== 1. FRAMING over a REAL Flipper-IRDB .ir (%s) ==" % IR_REAL.name)
    r = ir_manual._parse_commands(IR_REAL)
    if not check(r["ok"], "the real file parses"):
        return
    ir_manual._mark_impossible_labels(r["commands"], blob)
    sop = [c for c in r["commands"] if c["soportado"]]
    check(len(sop) == 7, "the file's 7 commands get imported (%d)" % len(sop))
    impares = [
        c for c in sop if c["_proto_hub"] is None and len(c["_atomos_crudos"]) % 2 == 1
    ]
    check(
        bool(impares),
        "the corpus includes captures that END ON A MARK (odd data:) -- %d of %d"
        % (len(impares), len([c for c in sop if c["_proto_hub"] is None])),
    )
    peor_adj, worst_gap = 0, 10**9
    for c in sop:
        pal, _proto, _value = wave_for(sop, c["name"])
        adj = adjacent_marks(pal)
        tiradas = space_runs(pal)[1:]  # the 1st is the 50 ms lead-in
        peor_adj = max(peor_adj, adj)
        worst_gap = min(worst_gap, max(tiradas) if tiradas else 0)
    check(peor_adj == 0, "ZERO adjacent-mark pairs across the 7 commands")
    check(
        worst_gap >= ir_manual.MIN_GAP_US,
        "the smallest inter-frame gap is %d us (>= %d)"
        % (worst_gap, ir_manual.MIN_GAP_US),
    )


def block_2_gap(blob: bytes) -> None:
    print("\n== 2. the gap the capture already carries does not get duplicated ==")
    gap = 39756
    text = ir_raw("Fin esp", [9000, 4500, 560, 1690, 560, gap])
    cmds = parse_ir(text, blob)
    if not check(cmds[0]["soportado"], "it gets imported"):
        print("      reason:", cmds[0]["reason"])
        return
    pal, _p, _v = wave_for(cmds, "Fin esp")
    tiradas = space_runs(pal)
    check(
        gap in tiradas,
        "the emitted gap is exactly the file's (%d us): runs %s" % (gap, tiradas),
    )
    check(
        (gap + ir_manual.TARGET_GAP_US) not in tiradas,
        "%d us does NOT show up (the duplicated gap the previous version emitted)"
        % (gap + ir_manual.TARGET_GAP_US),
    )
    check(adjacent_marks(pal) == 0, "zero adjacent marks")

    print("  -- NEGATIVE: a capture ending on a mark DOES need the gap --")
    text2 = ir_raw("Fin marca", [9000, 4500, 560])
    cmds2 = parse_ir(text2, blob)
    pal2, _p, _v = wave_for(cmds2, "Fin marca")
    check(adjacent_marks(pal2) == 0, "zero adjacent marks there too")
    check(
        ir_manual.TARGET_GAP_US in space_runs(pal2),
        "the %d us gap was added" % ir_manual.TARGET_GAP_US,
    )


def block_3_parsed(blob: bytes) -> None:
    print("\n== 3. 'parsed' == the already-validated path (Hub's REAL ProtocolList) ==")
    # From the permanent library, not from a device folder: this used to read
    # `account_export/output/hub-config-tv-a/` and died with
    # FileNotFoundError as soon as the user deleted that device.
    protos_hub = library.protocol_definitions()
    casos = [
        ("NEC", "Toshiba 32 Bit", 0x40, 0x12),
        ("SIRC", "Sony 12 Bit", 0x01, 0x15),
        ("SIRC15", "Sony 15 Bit", 0xA4, 0x2E),
    ]
    for alias, hub, addr, cmd in casos:
        cmds = parse_ir(ir_parsed("Boton", alias, addr, cmd), blob)
        if not check(cmds[0]["soportado"], "%s gets imported" % alias):
            print("      reason:", cmds[0]["reason"])
            continue
        pal, proto, value = wave_for(cmds, "Boton")
        esperado = synth_ir.sintetizar(protos_hub[hub], value, lsb_first=False)
        check(
            proto == hub and pal == esperado,
            "%s addr=%#x cmd=%#x -> %r, %d words IDENTICAL to sintir.sintetizar()"
            % (alias, addr, cmd, proto, len(pal)),
        )


def block_4_labels(blob: bytes) -> None:
    print("\n== 4. Flipper's 40 standard names give usable labels ==")
    partes = ["Filetype: IR signals file", "Version: 1"]
    for n in NOMBRES_FLIPPER:
        partes.append(
            "#\nname: %s\ntype: raw\nfrequency: 38000\nduty_cycle: 0.330000\n"
            "data: 9000 4500 560 1690 560" % n
        )
    cmds = parse_ir("\n".join(partes) + "\n", blob)
    malos = [c for c in cmds if not c["soportado"]]
    check(
        not malos,
        "all %d get imported with a drawable label (%s)"
        % (
            len(NOMBRES_FLIPPER),
            ", ".join("%s->%s" % (c["name"], c["rotulo"]) for c in cmds[:4])
            + ", ...",
        ),
    )
    for c in malos:
        print("      %s: %s" % (c["name"], c["reason"]))

    print("  -- NEGATIVE: labels the device CANNOT draw --")
    for name, why in (
        ("Qwerty", "the hardware has no 'Q' glyph"),
        ("Reproduccion Automatica Total", "does not fit in the 81 px cell"),
    ):
        c = parse_ir(ir_raw(name, [9000, 4500, 560]), blob)[0]
        check(
            not c["soportado"],
            "%r ends up NOT importable (%s): %s"
            % (name, why, (c["reason"] or "")[:70]),
        )


def block_5_negatives(blob: bytes) -> None:
    print("\n== 5. parser and boundary NEGATIVES ==")
    casos = [
        (
            "missing Filetype header",
            "name: X\ntype: raw\nfrequency: 38000\ndata: 100 200\n",
        ),
        (
            "protocol without a verified formula",
            ir_parsed("X", "NECext", 0x1234, 0x56),
        ),
        (
            "address outside NEC's 8 bits (would truncate)",
            ir_parsed("X", "NEC", 0x1234, 0x12),
        ),
        (
            "command outside SIRC's 7 bits (would truncate)",
            ir_parsed("X", "SIRC", 0x01, 0x89),
        ),
        (
            "a 60000 us mark (the blob would clip it to 32767)",
            ir_raw("X", [60000, 4500, 560]),
        ),
        (
            "raw block with no data",
            "Filetype: IR signals file\nname: X\ntype: raw\nfrequency: 38000\n",
        ),
    ]
    for label, text in casos:
        cmds = parse_ir(text, blob)
        rechazado = all(not c["soportado"] for c in cmds)
        reason = (cmds[0]["reason"] or "")[:78] if cmds else "?"
        check(rechazado, "%s -> rejected: %s" % (label, reason))


def block_6_e2e(blob_path: pathlib.Path) -> None:
    print("\n== 6. E2E: import -> add_device.py (subprocess) -> gate ==")
    partes = ["Filetype: IR signals file", "Version: 1"]
    for i, n in enumerate(NOMBRES_FLIPPER[:12]):
        # mixed on purpose: parsed NEC (a real Hub protocol) + raw ending on
        # a mark (the case that used to break the framing)
        if i % 3 == 0:
            partes.append(
                "#\nname: %s\ntype: parsed\nprotocol: NEC\naddress: 40 00 00 00\n"
                "command: %02X 00 00 00" % (n, 0x10 + i)
            )
        else:
            partes.append(
                "#\nname: %s\ntype: raw\nfrequency: 38000\nduty_cycle: 0.330000\n"
                "data: 8953 4403 601 417 701 516 602 515 602 411 706 515 603 515 575"
                % n
            )
    with tempfile.NamedTemporaryFile("w", suffix=".ir", delete=False) as fh:
        fh.write("\n".join(partes) + "\n")
        ir_path = pathlib.Path(fh.name)

    target = None
    salida = RAIZ / "output" / "_control_ir_manual.bin"
    try:
        r = ir_manual.import_device(
            ir_path, "Acme", "Control E2E", "Tele", blob=blob_path.read_bytes()
        )
        if not check(r["ok"], "importar() writes the device"):
            print("      error:", r.get("error"))
            return
        target = pathlib.Path(r["target"])
        check(
            r["commands"] == 12 and r["commands_skipped"] == 0,
            "all 12 commands got in (%d, %d skipped)"
            % (r["commands"], r["commands_skipped"]),
        )
        g = generar_mod.generate(
            blob_path,
            r["json"],
            index=3,
            name="Tele",
            salida=str(salida),
            device="Tele",
            timeout=900.0,
        )
        if not check(
            g["ok"] and g["returncode"] == 0,
            "add_device.py exits with 0 (returncode %s)" % g.get("returncode"),
        ):
            print((g.get("stderr") or "")[-1500:])
            return
        import re as _re

        repuntes = sorted(
            {
                int(x, 16)
                for x in _re.findall(r"--repunta (0x[0-9a-fA-F]+)", g["stdout"])
            }
        )
        check(bool(repuntes), "declares repoints: %s" % [hex(p) for p in repuntes])
        c = generar_mod.preview_gate(blob_path, salida, repuntes)
        check(
            c["ok"] and not c["sin_declarar"],
            "gate: nothing moved (%d differences, 0 undeclared)" % c["diferencias"],
        )
        print("  -- NEGATIVE: without declaring a repoint the gate has to say NO --")
        for remove in repuntes:
            partial = [p for p in repuntes if p != remove]
            cn = generar_mod.preview_gate(blob_path, salida, partial)
            check(
                not cn["ok"],
                "without --repunta %#08x gives NO (%d bytes undeclared)"
                % (remove, len(cn["sin_declarar"])),
            )
    finally:
        ir_path.unlink(missing_ok=True)
        salida.unlink(missing_ok=True)
        if target is not None and target.name.startswith(
            "hub-config-manual-acme-control-e2e-"
        ):
            shutil.rmtree(target, ignore_errors=True)
            print("  (test folder deleted: %s)" % target.name)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rapido", action="store_true", help="skip block 6 (E2E)")
    a = ap.parse_args()

    blob = BLOB.read_bytes()
    block_1_framing(blob)
    block_2_gap(blob)
    block_3_parsed(blob)
    block_4_labels(blob)
    block_5_negatives(blob)
    if not a.rapido:
        block_6_e2e(BLOB)

    print("\n%d checks OK, %d FAILED" % (OK, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
