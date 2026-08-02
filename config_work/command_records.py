#!/usr/bin/env python3
"""Generates the command objects and 25 B IR records of a new device.

Input: the Hub's `resources` JSON (`ProtocolList` + `DeviceList`). Output:
bytes ready to append at the end of the blob (same criterion as `proxy.py` and
`relocate.py`: **nothing is run**, everything is appended to the tail).

Does not reimplement waveform synthesis: **everything waveform-shaped comes
out of `synth_ir.py`**, which is already validated 234/234 against the blob.
This module adds the two pieces still missing for a command to become blob
data:

    command record         25 B: flag, carrier (ns), self-pointer,
                            press waveform ptr, hold waveform ptr
    command object           7 B: 02 | {cmd_id, 0x7D} | {dev_id, 0x7C}

## The record, field by field (offsets relative to the record's start)

    +0   01 00 00 00 00              flag (0x04 in 2 of 236 in the real blob;
                                      preserved when re-emitting, never invented)
    +5   <u24 carrier period, NANOSECONDS>
    +8   <u24 its half>
    +11  01 <ptr24 -> record+4>      self-pointer, ALWAYS -11: not a link,
                                      it is a structural self-reference
    +15  01 <ptr24 -> press waveform>  entry + N full repetitions
    +19  <ptr24 -> hold waveform>    a single "still pressed" frame, or
                                      00 00 00 if the command does not repeat
    +22  00 00 00

## CONTROL -- re-emitting the 236 records already in the blob

`--check` decodes each real record (with `commands.records()` +
`irscan.decode()`), rebuilds its 25 bytes **from its own parts** (protocol +
payload from the Hub, at the real offsets it already has) and compares byte
for byte. Result measured against `backups/config_raw.bin`:

    234 of 236 byte-for-byte identical
      2 differ in a single byte: the flag is 0x04 in the real blob and 0x01
      in the reconstruction (it cannot be derived from anything in the Hub,
      so it is not invented: it is preserved when re-emitting, and for new
      commands 0x01 is used, which is the measured majority)

This number **did not come out on the first try**. Two bugs from an earlier
implementation (`proxy.py: bloque()`) were found while reproducing this
control and are fixed here:

1. **The carrier is truncated, not rounded.** `round(1e9/38000)` gives 26316;
   the real blob stores 26315 (`int(1e9/38000)`, i.e. truncation). With
   `round()` the 62 Toshiba records failed by 1 ns -- small enough to go
   unnoticed and enough for the byte-for-byte comparison to never close.
2. **The "hold" waveform is not "one repetition with entry 0" in general.**
   `sintir.sintetizar(..., repeticiones=1, entrada_us=0)` works for protocols
   with no start segment of their own (Sony, Magnavox), but:
   - it leaves an entry word `0x0000` that is not in the blob (`entrada_us=0`
     via `partir_espacio` is not "no entry", it is "a 0 us wait": see
     `hold_wave()` below, which builds the repeat frame by hand from
     `synth_ir.py`'s low-level pieces instead of going through `sintetizar()`);
   - in protocols with their own `Start` (Toshiba: 32 bits at start-up, short
     frame afterward) `sintetizar()`'s `inicio` loop runs **always**,
     regardless of `repeticiones`, so asking for `repeticiones=1` returns the
     long 32-bit frame, not the short "still pressed" one.
   Verified in the blob: the waveform the third pointer points at is **not**
   a duplicate or a backward reference inside the press waveform (that
   hypothesis was tested with `Sony 12/15 Bit`, where it matches by
   coincidence because the real "hold" and the last "press" frame are
   identical bytes -- and it is **refuted** by Toshiba, where they are
   distinct waveforms: Toshiba's measures 18 bytes and starts 2 bytes after
   the press waveform's close, with its own content).

Usage:
    python3 command_records.py generar <hub-config.json> --dispositivo "Philips TV"
    python3 command_records.py control <blob.bin> <hub-config.json>
    python3 command_records.py control <blob.bin> <hub-config.json> --dispositivo "Sony TV"

Writes nothing to the device. Runs nothing on the blob. It only produces
bytes and, optionally, writes them to a file.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import synth_ir

BASE = 0x040000
# `G:<protocol>:(<Start>)(<Repeat>)(<Finish>)`. Same pattern as `proxy.py`
# because it is the Hub's format, not something specific to this module.
KEYCODE = re.compile(r"G:([^:]+):\(([^)]*)\)\(([^)]*)\)\(([^)]*)\)")
# Bit order validated against the blob's 236 records: 234/236 with `False`
# alone (Sony 12, Sony 15 and Toshiba, the three families present), and the
# other 2 are palindromic values where True and False give the same
# waveform -- not a family that truly needs True. [ASSUMED] for Magnavox,
# which is not in the blob and therefore has nothing to check against: it
# inherits the convention measured in the other three families.
LSB_FIRST_BY_DEFAULT = False


# --------------------------------------------------------------------------
# read the Hub config (same as proxy.py: JSON -> protocols + devices)


def load_hub_config(cfg: str) -> tuple[dict, list]:
    """(protocols by name, list of devices) from a Hub config."""
    d = json.loads(pathlib.Path(cfg).read_text())
    r = d["resources"]
    protos = {p["Name"]: p for p in r["ProtocolList"]["Protocols"]}
    devs = r["DeviceList"]["DevicesWithFeatures"]
    return protos, devs


def device_name(dev: dict) -> str:
    d = dev["Device"]
    return d.get("Name") or "%s %s" % (d.get("Manufacturer"), d.get("Model"))


def choose_device(devs: list, which: str):
    """(index, dev) by position, Name, Model or composed name. None if absent."""
    for i, dv in enumerate(devs):
        d = dv["Device"]
        if which in (str(i), d.get("Name"), d.get("Model"), device_name(dv)):
            return i, dv
    return None, None


def commands_of(dev: dict, protos: dict):
    """[(name, protocol, value)] of the commands with a recognizable KeyCode.

    Unlike `proxy.py`, **it does not skip protocols with their own start
    segment** (Toshiba): `hold_wave()` already handles them fine (see
    CONTROL), so skipping them here would only lose commands needlessly.
    """
    out, saltados = [], []
    for c in dev["Commands"]:
        kc = c.get("KeyCode") or ""
        m = KEYCODE.match(kc)
        if not m:
            saltados.append((c.get("Name"), "KeyCode not recognized: %r" % kc))
            continue
        proto = m.group(1)
        if proto not in protos:
            saltados.append((c.get("Name"), "protocol absent from JSON: %s" % proto))
            continue
        # the value is the first hex literal to appear in (Start, Repeat, Finish)
        value = next(
            (int(v[2:], 16) for v in m.group(2, 3, 4) if v.lower().startswith("0x")),
            None,
        )
        if value is None:
            saltados.append((c.get("Name"), "no hex payload: %r" % kc))
            continue
        out.append((c.get("Name"), proto, value))
    return out, saltados


# --------------------------------------------------------------------------
# carrier


def carrier(hz: int) -> tuple[int, int]:
    """Hz -> (period in ns, half). It is TRUNCATED -- see point 1 of the
    module docstring: `round()` corrupts the control's 62 Toshiba records."""
    periodo = int(1_000_000_000 / hz)
    return periodo, periodo // 2


# --------------------------------------------------------------------------
# a command's two waveforms


def press_wave(
    proto: dict, value: int, lsb_first: bool = LSB_FIRST_BY_DEFAULT
) -> bytes:
    """Entry + N full repetitions. Delegated entirely to `sintir.sintetizar`."""
    return synth_ir.a_bytes(synth_ir.sintetizar(proto, value, lsb_first=lsb_first))


def hold_wave(
    proto: dict, value: int, lsb_first: bool = LSB_FIRST_BY_DEFAULT
) -> bytes:
    """A single "still pressed" frame, with no entry.

    Does not use `sintir.sintetizar()` -- for why, see point 2 of the module
    docstring. Instead it builds the block by hand from `synth_ir.py`'s
    low-level pieces (`segmentos`, `render`, `fundir`, `a_palabras`,
    `CLOSE_US`): the bit and timing logic still belongs entirely to
    `synth_ir.py`, this only chooses which segment to render and how many
    times.

    `KeyCode.Repeat` names the correct segment in the three families measured
    (Sony, Toshiba and Magnavox declare it explicitly); if it were missing,
    it falls back to the main segment, same as `sintetizar()` does.
    """
    segs = synth_ir.segmentos(proto)
    kc = proto.get("KeyCode") or {}
    repite = [s["SegmentName"] for s in (kc.get("Repeat") or [])] or [
        proto["IRSegments"][0]["Name"]
    ]
    palabras: list[int] = []
    for name in repite:
        seg = segs[name]
        atomos = synth_ir.fundir(synth_ir.render(seg, value, lsb_first))
        largo = sum(us for _, us in atomos)
        gap = max((seg.get("TotalLength") or 0) - largo, 0)
        palabras.extend(synth_ir.a_palabras(atomos))
        if gap:
            palabras.extend(synth_ir.a_palabras([(False, gap)]))
    palabras.extend(synth_ir.a_palabras([(False, synth_ir.CLOSE_US)]))
    return synth_ir.a_bytes(palabras)


# --------------------------------------------------------------------------
# the two pieces the task calls for: the 25 B record and the 7 B object


def command_record(
    off_registro: int,
    off_press: int,
    off_hold: int | None,
    periodo_ns: int,
    mitad_ns: int,
    bandera: int = 0x01,
) -> bytes:
    """The 25 B record. `off_*` are offsets relative to the blob body (without
    adding `BASE` yet -- this helper adds it)."""
    reg = bytearray([bandera, 0, 0, 0, 0])
    reg += periodo_ns.to_bytes(3, "little") + mitad_ns.to_bytes(3, "little")
    reg += b"\x01" + (BASE + off_registro + 4).to_bytes(3, "little")
    reg += b"\x01" + (BASE + off_press).to_bytes(3, "little")
    reg += (
        (BASE + off_hold).to_bytes(3, "little")
        if off_hold is not None
        else b"\x00\x00\x00"
    )
    reg += b"\x00\x00\x00"
    assert len(reg) == 25
    return bytes(reg)


def _slot(u16: int, tag: int) -> bytes:
    return u16.to_bytes(2, "little") + bytes([tag])


def command_object(cmd_id: int, dev_id: int) -> bytes:
    """7 B: `02 | {cmd_id, 0x7D} | {dev_id, 0x7C}`.

    Shape verified 236/236 against the blob (see `check_objects()`, which
    runs with any `command_records.py check`): every one of the blob's 236 command
    records has, somewhere, an object with this exact shape and `dev_id` in
    {0x0001, 0x0101, 0x0201} -- exactly the three known devices, with no
    collisions and nothing left over.
    """
    return b"\x02" + _slot(cmd_id, 0x7D) + _slot(dev_id, 0x7C)


# --------------------------------------------------------------------------
# a whole device, ready to append


def device_block(
    protos: dict,
    dev: dict,
    dev_index: int,
    arranque: int,
    lsb_first: bool = LSB_FIRST_BY_DEFAULT,
):
    """Waveforms + records(25 B) + objects(7 B) of an entire device.

    Returns `(bytes, index, skipped)`. `index` is a list of dicts with
    `name`, `protocolo`, `value`, `cmd_id`, `dev_id`, `off_registro` and
    `off_objeto` -- all as blob offsets (`BASE` already added on the ones
    that are pointers, raw offsets on the rest), meant so that whoever later
    builds the navigation table/objects (`relocate.py`) does not have to
    re-derive anything.

    **Order matters**: commands are emitted in the same order they appear in
    `dev["Commands"]`, and `cmd_id = (dev_index << 8) | ordinal` with
    `ordinal` = position in that walk, starting at 0. This is the same
    convention measured in the real blob (`TRAZA_TV.md`: `dev_id =
    (indice<<8)|0x01`, `cmd_id = (indice<<8)|ordinal`) and the one
    `reubicar.add_device` expects, which counts new records by
    address position, not by pointer -- see PLAN.md, section "THE ID ->
    RECORD BRIDGE": a device's records are contiguous and in ordinal order
    because nothing points at them; the firmware reaches them by walking the
    region. Reordering them breaks that count even if every individual
    record is perfect.
    """
    cmds, saltados = commands_of(dev, protos)
    dev_id = (dev_index << 8) | 0x01
    out = bytearray()
    index = []

    for ordinal, (name, proto_name, value) in enumerate(cmds):
        p = protos[proto_name]
        hz = p.get("CarrierFrequency") or 38000
        periodo, mitad = carrier(hz)

        press = press_wave(p, value, lsb_first)
        off_press = arranque + len(out)
        out += press
        out += b"\x00\x00"  # padding: this is how waveforms sit in the real blob

        hold = hold_wave(p, value, lsb_first)
        off_hold = arranque + len(out)
        out += hold

        off_reg = arranque + len(out)
        out += command_record(off_reg, off_press, off_hold, periodo, mitad)

        index.append(
            {
                "name": name,
                "protocolo": proto_name,
                "value": value,
                "cmd_id": (dev_index << 8) | ordinal,
                "dev_id": dev_id,
                "off_registro": BASE + off_reg,
            }
        )

    for entrada in index:
        entrada["off_objeto"] = BASE + arranque + len(out)
        out += command_object(entrada["cmd_id"], entrada["dev_id"])

    return bytes(out), index, saltados


# --------------------------------------------------------------------------
# CONTROL: re-emit what is already in the blob and compare byte for byte

# which device each real record belongs to, by protocol and address inside
# the payload -- same as `commands.py`, which already validated it by
# crossing two independent paths (blocks by address size and by button
# sub-walk).
_DIRECCION = {
    "Sony 12 Bit": lambda v: v & 0x1F,
    "Sony 15 Bit": lambda v: v & 0xFF,
}


def _device_of(proto: str, value: int):
    import commands as C

    d = _DIRECCION.get(proto, lambda v: (v >> 16) & 0xFFFF)(value)
    return C.DEVICE.get((proto, d))


def check_records(blob_path: str, cfg_path: str, device: str | None = None):
    """Re-emits every 25 B record already present in the blob and compares it
    byte for byte with the original. Returns (ok, total, details).

    Methodology, for traceability: the offset, press waveform and real
    `holdp` are taken from the record itself (nothing about location is
    invented), the press waveform is decoded to get protocol and payload,
    that protocol is looked up in the Hub's JSON, and with that the full 25
    bytes are rebuilt -- carrier and self-pointer included -- to compare
    against the real ones. The "hold" waveform is compared separately, in a
    slice of `len(mia)` bytes from the real `holdp`: `irscan.read_waveform`
    does not work for that because its cut heuristic (two spaces in a row,
    the last one short) needs more than 8 words to trigger, and several hold
    waveforms measure less -- comparing with its heuristic pulls in bytes
    from the neighboring structure that are not part of the waveform and
    produces a false negative.
    """
    import commands as C
    import irscan

    blob = pathlib.Path(blob_path).read_bytes()
    protos, _ = load_hub_config(cfg_path)

    ok = 0
    detalles = []
    for off, wf, period, half in C.records(blob):
        start = off - 15
        raw = blob[start : start + 25]
        flag = raw[0]
        pressp = int.from_bytes(raw[16:19], "little") - BASE
        holdp_field = int.from_bytes(raw[19:22], "little")

        w_press_real = list(irscan.read_waveform(blob, pressp))
        dec = irscan.decode(w_press_real)
        if not dec:
            detalles.append((start, "press waveform does not decode", None))
            continue
        proto, _bits, value = dec
        if proto not in protos:
            detalles.append((start, "protocol absent from JSON: %s" % proto, proto))
            continue

        if device is not None and _device_of(proto, value) != device:
            continue

        p = protos[proto]

        # entry and repetitions are parameters of the command, not of the
        # protocol (same as in `sintir.validar`): they are read from the
        # real waveform itself.
        i = 0
        while i < len(w_press_real) and not w_press_real[i] & synth_ir.MARCA:
            i += 1
        entrada = sum(x & synth_ir.MAX_PALABRA for x in w_press_real[:i])
        reps = sum(
            1
            for x in w_press_real
            if x & synth_ir.MARCA and (x & synth_ir.MAX_PALABRA) >= 2000
        )

        mia_press = mia_lsb = None
        for lsb in (LSB_FIRST_BY_DEFAULT, not LSB_FIRST_BY_DEFAULT):
            cand = synth_ir.sintetizar(p, value, max(reps, 1), lsb, entrada)
            if cand == w_press_real:
                mia_press, mia_lsb = cand, lsb
                break
        if mia_press is None:
            detalles.append((start, "press waveform not reproduced", proto))
            continue

        per_mio, mitad_mio = carrier(p.get("CarrierFrequency") or 38000)

        ok_hold = True
        if holdp_field:
            mia_hold = hold_wave(p, value, mia_lsb)
            real_slice = blob[holdp_field - BASE : holdp_field - BASE + len(mia_hold)]
            ok_hold = mia_hold == bytes(real_slice)

        # `start` and `pressp` are already local offsets (blob = BASE +
        # offset), same as `off_reg`/`off_press` in `device_block` -- BASE
        # does not need to be subtracted from them again.
        mio_reg = command_record(start, pressp, None, per_mio, mitad_mio)
        # rebuilt with the REAL offsets (nothing about location is
        # invented); the only thing recomputed is the carrier/flag content
        mio_reg = bytearray(mio_reg)
        mio_reg[16:19] = (pressp + BASE).to_bytes(3, "little")
        mio_reg[19:22] = holdp_field.to_bytes(3, "little")

        if bytes(mio_reg) == raw and ok_hold:
            ok += 1
        else:
            d = next((k for k in range(25) if mio_reg[k] != raw[k]), None)
            detalles.append(
                (
                    start,
                    "differs: record byte %s, hold_ok=%s, real flag=%#04x"
                    % (d, ok_hold, flag),
                    proto,
                )
            )

    total = ok + len(detalles)
    return ok, total, detalles


def check_objects(blob_path: str) -> tuple[int, int]:
    """Counts the command objects (`02 | {cmd_id,0x7D} | {dev_id,0x7C}`) that
    are really in the blob, and how many fall on a known device's `dev_id`.
    This is a SHAPE check (the 7 B shape is correct if it appears exactly
    once per command record, with no collisions), not a content one -- the
    content (which `cmd_id` belongs to which command) has nothing to check
    against for a device that is not yet in the blob.
    """
    import commands as C

    blob = pathlib.Path(blob_path).read_bytes()
    total_records = len(list(C.records(blob)))

    conocidos = {0x0001, 0x0101, 0x0201}
    encontrados = 0
    for o in range(len(blob) - 7):
        if blob[o] != 0x02 or blob[o + 3] != 0x7D or blob[o + 6] != 0x7C:
            continue
        dev_id = blob[o + 4] | (blob[o + 5] << 8)
        if dev_id in conocidos:
            encontrados += 1
    return encontrados, total_records


# --------------------------------------------------------------------------
# CLI


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="modo", required=True)

    g = sub.add_parser("generar", help="build the block of a new device")
    g.add_argument("config")
    g.add_argument("--device", required=True)
    g.add_argument("--index", type=int, default=0, help="device index (0-based)")
    g.add_argument("--arranque", type=lambda x: int(x, 0), default=0)
    g.add_argument("--salida", help="where to write the raw bytes")
    g.add_argument("--json", dest="salida_json", help="where to write the index")

    c = sub.add_parser(
        "check", help="re-emit what is already in the blob and compare"
    )
    c.add_argument("blob")
    c.add_argument("config")
    c.add_argument(
        "--device",
        help="restrict to an already-known device (Sony TV, Home, DVR); "
        "without this it runs over the blob's 236 records",
    )

    a = ap.parse_args()

    if a.modo == "check":
        ok, total, detalles = check_records(a.blob, a.config, a.device)
        print("COMMAND RECORDS (25 B) -- re-emitted from their own parts")
        print("  byte-for-byte identical  %d of %d" % (ok, total))
        if detalles:
            print("  differ                   %d" % len(detalles))
            for off, reason, proto in detalles[:10]:
                print("    %#08x  %-10s  %s" % (off, proto or "?", reason))
            if len(detalles) > 10:
                print("    ... +%d more" % (len(detalles) - 10))

        enc, tot = check_objects(a.blob)
        print("\nCOMMAND OBJECTS (7 B) -- shape `02 | {cmd_id,0x7D} | {dev_id,0x7C}`")
        print("  found in the blob with a known dev_id  %d of %d records" % (enc, tot))
        return 0 if ok == total and enc == tot else 1

    # generate
    protos, devs = load_hub_config(a.config)
    i, dev = choose_device(devs, a.device)
    if dev is None:
        print("could not find %r. Available:" % a.device, file=sys.stderr)
        for k, dv in enumerate(devs):
            print("  [%d] %s" % (k, device_name(dv)), file=sys.stderr)
        return 1

    datos, index, saltados = device_block(protos, dev, a.index, a.arranque)

    print(
        "%s (index %d, dev_id %#06x): %d commands"
        % (device_name(dev), a.index, (a.index << 8) | 1, len(index))
    )
    print("  block generated: %d B" % len(datos))
    for e in index[:5]:
        print(
            "    cmd_id %#06x  %-24s record %#08x  object %#08x"
            % (e["cmd_id"], e["name"], e["off_registro"], e["off_objeto"])
        )
    if len(index) > 5:
        print("    ... +%d more" % (len(index) - 5))
    for name, reason in saltados:
        print("  SKIPPED %-24s %s" % (name, reason))

    # cheap self-check: every press waveform just generated has to start with
    # LEAD_IN and, if the protocol is from a family `irscan.decode` knows how
    # to read (Sony SIRC, NEC/Toshiba), decode to the same (protocol,
    # payload) that produced it. `irscan.decode` does NOT know RC5/biphase
    # (Magnavox included): for those only the shape is confirmed, not the
    # content -- Magnavox's content is validated separately, in
    # `TRAZA_TV.md` (against real captures from Flipper's IRDB), not against
    # this blob, which has no Magnavox at all to check against.
    import irscan

    ok_forma = ok_decodificado = sin_decodificador = 0
    for e in index:
        off_local = e["off_registro"] - BASE - a.arranque
        pressp_abs = int.from_bytes(datos[off_local + 16 : off_local + 19], "little")
        pressp_local = pressp_abs - BASE - a.arranque
        w = irscan.read_waveform(datos, pressp_local)
        if not w or w[0] != irscan.LEAD_IN:
            continue
        ok_forma += 1
        dec = irscan.decode(w)
        if dec is None:
            sin_decodificador += 1
        elif dec[0] == e["protocolo"] and dec[2] == e["value"]:
            ok_decodificado += 1
    print(
        "  self-check: %d of %d press waveforms start with LEAD_IN"
        % (ok_forma, len(index))
    )
    print(
        "    of those, %d decode to the same (protocol, payload) with irscan.py"
        % ok_decodificado
    )
    if sin_decodificador:
        print(
            "    %d are from a protocol irscan.decode does not know (biphase/RC5, "
            "e.g. Magnavox) -- not verifiable this way" % sin_decodificador
        )

    if a.salida:
        pathlib.Path(a.salida).write_bytes(datos)
        print("\nwrote %s (%d B)" % (a.salida, len(datos)))
    if a.salida_json:
        pathlib.Path(a.salida_json).write_text(json.dumps(index, indent=1))
        print("wrote %s" % a.salida_json)
    if not a.salida and not a.salida_json:
        print("\n(no --salida or --json: nothing was written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
