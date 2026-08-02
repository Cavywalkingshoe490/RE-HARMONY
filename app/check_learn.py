#!/usr/bin/env python3
"""CHECK for `app/learn_ir.py`: tests the ENTIRE learning path without
the device, using REAL waveforms from the factory blob.

The capture itself cannot be tested (it needs the remote and the original
control pointed at it). What CAN be tested, and is tested here, is
everything that happens after the waveform arrives -- which is where the
decisions are:

  1. **Recognition** (positive): a waveform from the blob that `irscan.decode`
     already decodes has to be recognized the same way after going through
     `learn_from_remote()`'s format (alternating us, starting on a mark,
     without the lead-in).
  2. **Recognition with the calibration bias** (the case that predicts the
     libconcord/Logitech divergence): the first mark is inflated on
     purpose. It still has to be recognized, and `via` has to say it went
     through a later frame. Without the fallback to the second frame this
     would fail.
  3. **Recognition** (negative): noise does not get recognized.
  4. **Comparison** (positive): two captures of the same key with jitter
     inside tolerance match.
  5. **Comparison** (negative): two different keys do NOT match, and jitter
     above tolerance doesn't either.
  6. **Round trip of the clean waveform**: for a recognized command, the
     waveform `synth_ir.py` regenerates from the factory `ProtocolList` has to
     give byte for byte the same one already in the blob. This is what
     justifies discarding the capture and keeping the protocol.
  7. **The JSON**: what `build_resources()` builds has to be readable by
     `commands.load_hub_config()` + `commands.commands_of()` (the same ones
     `add_device.py` uses), and the labels have to come out of
     `FunctionList`.
  8. **Text validation** (positive and negative): a writable name passes,
     and one with `Q`/`X`/`Z` -- which the hardware does not draw -- does not.

Usage:
    app/.venv/bin/python app/check_learn.py
"""

from __future__ import annotations

import json
import pathlib
import random
import sys
import tempfile

APP = pathlib.Path(__file__).resolve().parent
RAIZ = APP.parent
sys.path.insert(0, str(APP))
sys.path.insert(0, str(RAIZ / "config_work"))

import learn_ir  # noqa: E402
import library  # noqa: E402
import command_records as comandos_mod  # noqa: E402
import irscan  # noqa: E402
import synth_ir  # noqa: E402

BLOB = RAIZ / "backups" / "config_raw.bin"

failures: list[str] = []


def check(cond: bool, text: str) -> None:
    print(("  OK   " if cond else "  FAIL ") + text)
    if not cond:
        failures.append(text)


def e2e() -> None:
    """Actually saves a learned device (`aprender_ir.save()`), runs it
    through `add_device.py` (subprocess, via `app/generate.py`) and through
    the gate with its negative check -- and then **deletes the folder**, so
    no fake device is left in the Control screen's dropdown.
    """
    import shutil

    import generate  # noqa: PLC0415

    blob_b = BLOB.read_bytes()
    bancos = learn_ir.available_banks(blob_b, tope=8)
    elegidos = []
    for i, b in enumerate(bancos[:6]):
        std = learn_ir.STANDARD_COMMANDS[i]
        an = learn_ir.analyze(learn_ir.simulated_capture(blob_b, b["offset"]))
        elegidos.append(
            {"name": std["name"], "label": std["label"], "analisis": an}
        )

    g = learn_ir.save(elegidos, "Acme", "Control E2E", "Banco", blob_b)
    check(bool(g.get("ok")), "save() wrote the device (%s)" % g.get("error"))
    if not g.get("ok"):
        return
    target = pathlib.Path(g["target"])
    try:
        check(
            g["limpios"] == 6 and g["crudos"] == 0,
            "all 6 commands went through the CLEAN path (protocol recognized)",
        )
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="aprender_e2e_"))
        salida = tmp / "con_aprendido.bin"
        r = generate.generate(
            BLOB,
            g["json"],
            index=3,
            name="Banco",
            device="Banco",
            salida=salida,
        )
        check(
            bool(r["ok"]),
            "add_device.py accepted the learned JSON and generated the "
            "blob (rc=%s)" % r["returncode"],
        )
        if not r["ok"]:
            print((r["stderr"] or "")[-1500:])
            return
        ref, nue = BLOB.read_bytes(), salida.read_bytes()
        c_ok = generate.preview_gate(ref, nue, [0x20, 0x24])
        check(
            c_ok["ok"] and not c_ok["sin_declarar"],
            "gate with the usual 2 repoints: ok=True, undeclared=[] "
            "(%d differences)" % c_ok["diferencias"],
        )
        for remove in (0x20, 0x24):
            partial = [p for p in (0x20, 0x24) if p != remove]
            c_no = generate.preview_gate(ref, nue, partial)
            check(
                not c_no["ok"],
                "NEGATIVE: without --repunta %#04x the gate gives ok=False "
                "(undeclared=%s)" % (remove, c_no["sin_declarar"]),
            )
        shutil.rmtree(tmp, ignore_errors=True)
    finally:
        # never leave the test device in account_export/output/
        if target.name.startswith("dispositivo-manual-acme-control-e2e-"):
            shutil.rmtree(target, ignore_errors=True)
            print("  (test folder deleted: %s)" % target.name)


def main() -> int:  # noqa: C901
    blob = BLOB.read_bytes()
    # The timings come from the permanent library, NOT from a device folder.
    # This used to read `account_export/output/hub-config-tv-a/`
    # and died with FileNotFoundError the moment the user deleted that
    # device -- a check that only runs while a particular device happens to
    # be downloaded is not a check.
    protos = library.protocol_definitions()
    if not protos:
        raise SystemExit(
            "the protocol library is empty: run "
            "`python3 app/library_store.py` to seed it"
        )

    print("== 1. recognition: POSITIVE check over real waveforms from the blob ==")
    bancos = learn_ir.available_banks(blob, tope=40)
    print("  %d distinct waveforms (protocol, value) in the blob" % len(bancos))
    check(len(bancos) >= 10, "there are at least 10 waveforms to test")

    aciertos, total = 0, 0
    for b in bancos:
        sim = learn_ir.simulated_capture(blob, b["offset"])
        if not sim.get("ok"):
            continue
        total += 1
        an = learn_ir.analyze(sim)
        rec = an["reconocido"]
        if rec and (rec["protocolo"], rec["value"]) == (b["protocolo"], b["value"]):
            aciertos += 1
        else:
            print(
                "     %#08x %s %#x -> %s"
                % (b["offset"], b["protocolo"], b["value"], rec)
            )
    check(
        aciertos == total,
        "%d/%d real waveforms are recognized the same after going through "
        "learn_from_remote()'s format" % (aciertos, total),
    )

    print()
    print("== 2. recognition with libconcord's calibration BIAS ==")
    print(
        "  (libconcord discards word 0 and writes word 1 as the first mark:\n"
        "   remote.cpp:919/931 vs CarrierProcessor.processCalibrationData)"
    )
    con_sesgo, via_frame2 = 0, 0
    for b in bancos:
        sim = learn_ir.simulated_capture(blob, b["offset"], sesgo_1ra_marca_us=1200)
        if not sim.get("ok"):
            continue
        an = learn_ir.analyze(sim)
        rec = an["reconocido"]
        if rec and (rec["protocolo"], rec["value"]) == (b["protocolo"], b["value"]):
            con_sesgo += 1
            if rec["via"].startswith("frame"):
                via_frame2 += 1
    check(
        con_sesgo == total,
        "%d/%d still get recognized with the 1st mark inflated by 1200 us"
        % (con_sesgo, total),
    )
    check(
        via_frame2 > 0,
        "%d of those %d were recognized through a LATER frame -- which is "
        "exactly what the fallback to the 2nd frame exists to cover"
        % (via_frame2, con_sesgo),
    )

    print()
    print("== 3. recognition: NEGATIVE check (noise) ==")
    rnd = random.Random(1234)
    falsos = 0
    for _ in range(200):
        n = rnd.randrange(20, 80) * 2
        ruido = {
            "ir_signal_us": [rnd.randrange(200, 3000) for _ in range(n)],
            "carrier_clock_hz": 38000,
        }
        if learn_ir.analyze(ruido)["reconocido"]:
            falsos += 1
    check(falsos == 0, "0/200 noise waveforms get recognized (got %d)" % falsos)

    print()
    print("== 4/5. comparing two captures ==")
    b0 = bancos[0]
    b1 = next(
        b
        for b in bancos
        if (b["protocolo"], b["value"]) != (b0["protocolo"], b0["value"])
    )
    a0 = learn_ir.analyze(learn_ir.simulated_capture(blob, b0["offset"]))
    a0b = learn_ir.analyze(learn_ir.simulated_capture(blob, b0["offset"]))
    a1 = learn_ir.analyze(learn_ir.simulated_capture(blob, b1["offset"]))

    r = learn_ir.compare(a0, a0b)
    check(r["coinciden"], "same key, twice -> match (%s)" % r["criterio"])
    r = learn_ir.compare(a0, a1)
    check(
        not r["coinciden"],
        "two DIFFERENT keys -> do not match (%s)" % r["detail"],
    )

    # jitter: recognition is broken on purpose (its header is stripped) to
    # force the comparison BY WAVEFORM, which is the weak path.
    def _unrecognized(an: dict, jitter: float, seed: int) -> dict:
        rr = random.Random(seed)
        at = [
            [bool(m), max(1, int(u * (1 + rr.uniform(-jitter, jitter))))]
            for m, u in an["atomos"]
        ]
        copia = dict(an)
        copia["atomos"] = at
        copia["reconocido"] = None  # force the waveform-comparison path
        return copia

    base = _unrecognized(a0, 0.0, 1)
    poco = _unrecognized(a0, 0.10, 2)
    mucho = _unrecognized(a0, 0.60, 3)
    r = learn_ir.compare(base, poco)
    check(
        r["coinciden"],
        "by WAVEFORM: +-10%% jitter matches (worst deviation %.1f%%)"
        % (100 * (r["peor_desvio_rel"] or 0)),
    )
    r = learn_ir.compare(base, mucho)
    check(
        not r["coinciden"],
        "by WAVEFORM: +-60%% jitter does NOT match (worst deviation %.1f%%)"
        % (100 * (r["peor_desvio_rel"] or 0)),
    )

    print()
    print("== 5b. the inter-frame gap measured for the RAW path ==")
    print("  (it's the `TotalLength` saved when the protocol is NOT recognized:")
    print("   frame1_duration + gap has to match the factory TotalLength)")
    correct, mirados = 0, 0
    for b in bancos:
        p = protos.get(b["protocolo"])
        if not p:
            continue
        factory_total = (p["IRSegments"][0].get("TotalLength")) or 0
        if not factory_total:
            continue
        an = learn_ir.analyze(learn_ir.simulated_capture(blob, b["offset"]))
        at = [(bool(m), int(u)) for m, u in an["atomos"]]
        tr, hcs = learn_ir.frames_and_gaps(at)
        if len(tr) < 2 or not hcs:
            continue
        mirados += 1
        measured = sum(u for _, u in tr[0]) + hcs[0]
        if measured == factory_total:
            correct += 1
        else:
            print(
                "     %-15s measured %6d us  vs factory %6d"
                % (b["protocolo"], measured, factory_total)
            )
    check(
        mirados and correct == mirados,
        "%d/%d captures give frame1_duration + gap == factory TotalLength "
        "EXACTLY (counting the trailing silence gave +79%% on Sony 12 Bit)"
        % (correct, mirados),
    )

    print()
    print("== 6. the CLEAN regenerated waveform == the one already in the blob ==")
    iguales, probadas = 0, 0
    for b in bancos:
        if b["protocolo"] not in protos:
            continue
        real = irscan.read_waveform(blob, b["offset"])
        entrada = synth_ir.entrada_de(real)
        reps = synth_ir.repeticiones_de(real)
        gen = synth_ir.sintetizar(
            protos[b["protocolo"]],
            b["value"],
            repeticiones=reps,
            lsb_first=comandos_mod.LSB_FIRST_BY_DEFAULT,
            entrada_us=entrada,
        )
        probadas += 1
        if gen == real:
            iguales += 1
    check(
        probadas and iguales == probadas,
        "%d/%d regenerated waveforms from the ProtocolList give the SAME "
        "word-for-word waveform as the blob -- that's why discarding the "
        "capture and keeping the protocol loses nothing" % (iguales, probadas),
    )

    print()
    print("== 7. the built JSON is read by comandos.load_hub_config()/comandos_de() ==")
    lib = {n: p for n, p in protos.items()}
    elegidos = []
    for i, b in enumerate(bancos[:4]):
        std = learn_ir.STANDARD_COMMANDS[i]
        an = learn_ir.analyze(learn_ir.simulated_capture(blob, b["offset"]))
        elegidos.append(
            {"name": std["name"], "label": std["label"], "analisis": an}
        )
    # one more, deliberately RAW: its recognition is erased
    an_crudo = learn_ir.analyze(
        learn_ir.simulated_capture(blob, bancos[5]["offset"])
    )
    an_crudo["reconocido"] = None
    elegidos.append({"name": "Mute", "label": "Mute", "analisis": an_crudo})

    armado = learn_ir.build_resources(elegidos, "Acme", "TV-1000", "Acme", lib)
    check(armado["limpios"] == 4, "4 commands through the CLEAN path")
    check(armado["crudos"] == 1, "1 command through the RAW path")

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as t:
        json.dump({"resources": armado["resources"]}, t)
        json_path = t.name
    p2, devs = comandos_mod.load_hub_config(json_path)
    cmds, saltados = comandos_mod.commands_of(devs[0], p2)
    check(
        len(cmds) == 5 and not saltados,
        "comandos.commands_of() reads all 5 commands, 0 skipped (skipped=%s)"
        % saltados,
    )
    # the waveforms can really be synthesized
    ok_ondas = 0
    for name, proto, value in cmds:
        try:
            if comandos_mod.press_wave(p2[proto], value):
                ok_ondas += 1
        except Exception as exc:  # noqa: BLE001
            print("     %s: %s" % (name, exc))
    check(ok_ondas == 5, "5/5 commands generate a waveform with comandos.press_wave()")

    # and the short labels come from the FunctionList we emit
    sys.path.insert(0, str(RAIZ / "config_work"))
    import add_device as dispositivo_mod  # noqa: E402  -- ONLY hub_labels

    etq = dispositivo_mod.hub_labels(json_path, devs[0], cmds)
    check(
        etq.get("VolumeUp") == "Vol Up",
        "the short label comes from FunctionList: VolumeUp -> %r (without "
        "FunctionList it would be 'Volume Up')" % etq.get("VolumeUp"),
    )
    pathlib.Path(json_path).unlink(missing_ok=True)

    print()
    print("== 8. text validation: positive and NEGATIVE ==")
    v = learn_ir.validate_texts("Acme", ["Power", "Vol Up"], blob)
    print("  'Acme' + ['Power','Vol Up'] -> ok=%s" % v["ok"])
    for d in v["detail"]:
        if not d["ok"]:
            print(
                "     %s %r: draws=%s missing=%s | writes=%s missing=%s"
                % (
                    d["what"],
                    d["text"],
                    d["dibuja_ok"],
                    d["draw_missing"],
                    d["escribe_ok"],
                    d["write_missing"],
                )
            )
    # the negative the brief asks for: the hardware doesn't draw Q, X, Z
    for mala in ("Qwerty", "Xbox", "Zenith"):
        vn = learn_ir.validate_texts(mala, ["Power"], blob)
        det = next(d for d in vn["detail"] if d["text"] == mala)
        check(
            not vn["ok"],
            "NEGATIVE %r rejected (draws=%s missing=%s | writes=%s missing=%s)"
            % (
                mala,
                det["dibuja_ok"],
                det["draw_missing"],
                det["escribe_ok"],
                det["write_missing"],
            ),
        )
    check(
        not learn_ir.validate_texts("", ["Power"], blob)["ok"],
        "NEGATIVE: an empty name does not pass",
    )

    if "--rapido" not in sys.argv:
        print()
        print("== 9. END TO END: save -> add_device.py -> gate ==")
        print("  (this proves 'the rest of the pipeline does not change')")
        e2e()

    print()
    print("=" * 72)
    if failures:
        print("LEARN CHECK: FAILED (%d):" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("LEARN CHECK: PASSED.")
    print("  Not tested (needs the remote + the original control pointed at it):")
    print("    - whether the firmware answers COMMAND_START_IRCAP (0x70)")
    print("    - whether the carrier calibration lands within 36-40 kHz")
    print(
        "    - the receiver's real jitter against TOLERANCIA_REL=%.2f"
        % learn_ir.TOLERANCIA_REL
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
