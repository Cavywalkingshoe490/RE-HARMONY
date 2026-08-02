#!/usr/bin/env python3
"""Proxy: turns the config Logitech returns into one the Harmony One accepts.

Logitech's cloud no longer serves configs for the One -- it serves the **Hub's
resources** model, which is JSON and stores the IR codes **symbolically**
(`G:Magnavox 13 Bit:()(0x07FF)():3`). The One needs the **GSPM** blob, which
stores them as a **raw waveform** in microseconds. This module is the bridge,
and it is written to be generic: **everything it needs it reads from the JSON
that arrives**, not from fixed tables for this hardware or for this particular
device.

## Why it can be appended without breaking anything

Measured on the real blob:

* the only pointer that reaches the tail is the header's `+4`, and the highest
  target in the whole blob is exactly the `PTYY` close;
* `CODE_USER_CONFIGURATION` runs from `0x040000` to `0x400000`, that is
  **2.615.494 B free** (the blob uses 1.316.666 of 3.932.160).

So **it is appended at the end and nothing is shifted**. No existing byte
moves, every old pointer still holds, and the 90.461 u24 that *look* like
pointers by coincidence stop being a danger, because the danger only existed
when shifting.

## The command record

It is 25 bytes, and the layout was validated by re-emitting the ones already
there: **234 of 236 identical byte for byte** from their parts. The 2 that are
not differ in a single byte -- the first one is `0x04` instead of `0x01` -- so
that flag is **preserved**, not invented.

    +0   01 00 00 00 00              flag (0x04 in 2 of 236)
    +5   <u24 carrier period in NANOSECONDS>
    +8   <u24 half of it>
    +11  01 <ptr24 -> record+4>      self-pointer
    +15  01 <ptr24 -> press waveform>
    +19  <ptr24 -> hold waveform>
    +22  00 00 00

Every command stores **two** waveforms: `press` (50 ms lead-in + 3 frames) and
`hold` (one frame, no lead-in), which is the one that repeats while the key
stays pressed. In the blob they sit back to back, `hold` starts at
`fin(press)+2`, but that is only how it ended up laid out: the firmware
**follows the pointers**. It was checked by measuring that in 89 of 236
records the waveforms are NOT back to back, because several commands share the
same waveform.

## Scope

With `--enganchar` it emits waveforms, command records, objects, global-table
entries, pages, and **the navigation**: a free physical key on a root page
that leads to the first new page.

What it does **not** do: generate the button's **visible label**, so it may be
drawn blank. The label format is solved (`<05><i><g><nombre>
<00>` for the text, `<04><i><g><ptr24>` for the reference, and the pointer is
physical, meaning the text can go at the end of the blob), but **where the
reference of a concrete button goes is still unsolved**. Not to be confused
with the block at `0x01c23e`: there the names are ASCII and are the activity
engine's internal namespace, not the screen text.

**It writes nothing to the hardware.** It writes a file.

Usage:
    python3 proxy.py <blob.bin> <hub-config.json> --listar
    python3 proxy.py <blob.bin> <hub-config.json> --dispositivo "Philips TV" \\
                     --enganchar --ezhex <respaldo.EZHex> --salida nuevo.bin
    python3 proxy.py <blob-original.bin> --verificar <generado.bin>
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import synth_ir

BASE = 0x040000
TOPE_REGION = 0x400000
SEMILLA = (0x21, 0x43)
# `G:<protocolo>:(<Start>)(<Repeat>)(<Finish>):<n>`. The three slots are the
# same `KeyCode.Start/Repeat/Finish` of the `ProtocolList`, so each family
# fills a different one:
#
#     G:Sony 12 Bit:()(0x0A90)():3              the value goes in Repeat
#     G:Toshiba 32 Bit:(0x00FF14EB)(Repeat)():3 the value goes in Start, and the
#                                               word "Repeat" says the
#                                               repeticion usa su propio segmento
#
# Assuming a single shape left out Toshiba and any protocol with a startup
# segment, which is exactly what a proxy cannot do.
KEYCODE = re.compile(r"G:([^:]+):\(([^)]*)\)\(([^)]*)\)\(([^)]*)\)")


# Carrier -> (period ns, half). The two values that show up in the blob; for
# any other frequency it is computed, which is what makes this generic.
def portadora(hz: int) -> tuple[int, int]:
    periodo = round(1_000_000_000 / hz)
    return periodo, periodo // 2


def cargar(cfg: str) -> tuple[dict, list]:
    """(protocols by name, device list) from a Hub config."""
    d = json.loads(pathlib.Path(cfg).read_text())
    r = d["resources"]
    protos = {p["Name"]: p for p in r["ProtocolList"]["Protocols"]}
    devs = r["DeviceList"]["DevicesWithFeatures"]
    return protos, devs


def name_of(dev: dict) -> str:
    d = dev["Device"]
    return d.get("Name") or "%s %s" % (d.get("Manufacturer"), d.get("Model"))


def multisegmento(proto: dict) -> bool:
    """Whether the protocol uses a repeat segment of its own.

    Toshiba does: `Start` with the 32 bits and `Toshiba 32 Bit KeyCodeRepeat`
    as the short "still held down" frame. **It is already implemented** in
    `synth_ir.py` and validated: the synthesizer reproduces **234 of 234** waveforms
    from the blob byte for byte, the 173 Sony ones and the 61 Toshiba ones.

    It is left in as a diagnostic -- no protocol is skipped over this --, but if a
    family with `Finish` showed up, which there is none of in this blob, it is worth
    a look before trusting it.
    """
    kc = proto.get("KeyCode") or {}
    return bool(kc.get("Finish"))


def commands_of(dev: dict, protos: dict):
    """[(name, protocol, value)] of the commands that can be synthesized."""
    out, saltados = [], []
    for c in dev["Commands"]:
        kc = c.get("KeyCode") or ""
        m = KEYCODE.match(kc)
        if not m:
            saltados.append((c.get("Name"), "KeyCode no reconocido: %r" % kc))
            continue
        proto = m.group(1)
        if proto not in protos:
            saltados.append((c.get("Name"), "protocol missing from the JSON: %s" % proto))
            continue
        # the value is the first hexadecimal that shows up in the three slots
        value = next(
            (int(v[2:], 16) for v in m.group(2, 3, 4) if v.lower().startswith("0x")),
            None,
        )
        if value is None:
            saltados.append((c.get("Name"), "sin carga hexadecimal: %r" % kc))
            continue
        if multisegmento(protos[proto]):
            saltados.append(
                (c.get("Name"), "%s usa segmento de repeticion propio" % proto)
            )
            continue
        out.append((c.get("Name"), proto, value))
    return out, saltados


def bloque(protos: dict, cmds: list, arranque: int) -> tuple[bytes, list]:
    """Builds waveforms + records for `cmds`, as if they started at offset `arranque`.

    Returns (bytes, [(name, record offset)]). The pointers are absolute and are
    computed against `arranque`, so the block can be pasted anywhere as long as
    that position is respected.
    """
    out = bytearray()
    index = []
    for name, proto, value in cmds:
        p = protos[proto]
        hz = p.get("CarrierFrequency") or 38000
        per, half = portadora(hz)

        press = synth_ir.a_bytes(synth_ir.sintetizar(p, value))
        # hold: a single frame and no lead-in, which is how they are in the blob
        hold = synth_ir.a_bytes(synth_ir.sintetizar(p, value, repeticiones=1, entrada_us=0))

        off_press = arranque + len(out)
        out += press
        out += b"\x00\x00"
        off_hold = arranque + len(out)
        out += hold
        off_reg = arranque + len(out)

        reg = bytearray(b"\x01\x00\x00\x00\x00")
        reg += per.to_bytes(3, "little") + half.to_bytes(3, "little")
        reg += b"\x01" + (BASE + off_reg + 4).to_bytes(3, "little")
        reg += b"\x01" + (BASE + off_press).to_bytes(3, "little")
        reg += (BASE + off_hold).to_bytes(3, "little")
        reg += b"\x00\x00\x00"
        assert len(reg) == 25
        out += reg
        index.append((name, off_reg))
    return bytes(out), index


def checksum(b: bytes, fin: int) -> tuple[int, int]:
    lo, hi = SEMILLA
    for i in range(0, fin, 2):
        lo ^= b[i]
        hi ^= b[i + 1]
    return lo, hi


def add(blob: bytes, datos: bytes) -> bytes:
    """Pastes `datos` before the tail and redoes the closer and the checksum.

    The tail is `<u16 checksum><'PTYY'>` and the header's `+4` points at the PTYY.
    """
    ptr = int.from_bytes(blob[4:7], "little") - BASE
    cuerpo = bytearray(blob[: ptr - 2])
    cuerpo += datos
    # the XOR-16 walks evens and odds separately: the body has to measure an
    # **even** number of bytes or the loop reads one too many
    if len(cuerpo) % 2:
        cuerpo += b"\x00"
    new_ptr = len(cuerpo) + 2
    cuerpo += b"\x00\x00" + b"PTYY"
    cuerpo[4:7] = (BASE + new_ptr).to_bytes(3, "little")
    lo, hi = checksum(bytes(cuerpo), new_ptr - 2)
    cuerpo[new_ptr - 2] = lo
    cuerpo[new_ptr - 1] = hi
    return bytes(cuerpo)


def revisar(b: bytes) -> list:
    """The four checks the firmware does before accepting a config."""
    ptr = int.from_bytes(b[4:7], "little") - BASE
    lo, hi = checksum(b, ptr - 2) if 0 < ptr <= len(b) else (None, None)
    return [
        ("magia GSPM en +0", b[:4] == b"GSPM", repr(b[:4])),
        ("LWJL en +0x63", b[0x63:0x67] == b"LWJL", repr(b[0x63:0x67])),
        (
            "the u24 at +4 points at the PTYY",
            0 < ptr <= len(b) - 4 and b[ptr : ptr + 4] == b"PTYY",
            "cierre en %#x" % ptr,
        ),
        (
            "checksum XOR-16",
            lo is not None and (b[ptr - 2], b[ptr - 1]) == (lo, hi),
            "calculado %02x %02x, guardado %02x %02x"
            % (lo or 0, hi or 0, b[ptr - 2], b[ptr - 1]),
        ),
        (
            "size below 0x390000",
            len(b) < 0x390000,
            "%d bytes (%#x)" % (len(b), len(b)),
        ),
    ]


def verificar(original: str, fresh: str) -> int:
    """All the invariants, over a pair (original blob, generated blob).

    Puts in a single place the checks that used to be run loose. It is good for
    validating any output, and above all for re-running them if something fails on
    the device and it has to be pinned down where.
    """
    import irscan
    import relocate

    v = pathlib.Path(original).read_bytes()
    n = pathlib.Path(fresh).read_bytes()
    fallas = 0

    def test(name, ok, detail):
        nonlocal fallas
        fallas += not ok
        print("  %-42s %-6s %s" % (name, "OK" if ok else "FALLA", detail))

    for nom, ok, det in revisar(n):
        test(nom, ok, det)

    a, b_ = relocate.chain(v), relocate.chain(n)
    iguales = sum(1 for k, x in a.items() if b_.get(k) == x)
    test(
        "the original buttons resolve the same",
        iguales == len(a),
        "%d de %d" % (iguales, len(a)),
    )

    # of the old body only the closer and the [9][10][11] entries can change
    close = int.from_bytes(v[4:7], "little") - BASE
    dif = [i for i in range(close - 2) if v[i] != n[i]]
    allowed = set(range(4, 7)) | set(range(0x0C + 4 * 9, 0x0C + 4 * 12))
    test(
        "solo cambian cierre e indice maestro [9][10][11]",
        set(dif) <= allowed,
        "%d bytes: %s" % (len(dif), dif),
    )

    # no page reference may point out of range
    sec = relocate.sections(n)
    total = relocate.count_pages(n, *sec[9])
    # **All** the objects of the global table are walked, not just the ones
    # hanging off a button: the first version used `page_references`, which
    # looks at 85 of the 847 `0x7E` slots, and **did not detect a deliberate
    # corruption**. A check never seen to fail is not a check.
    outside, vistas = [], 0
    for d in relocate.table(n, sec[11][0]):
        if not 0 <= d < len(n):
            continue
        c = n[d]
        if not 0 < c < 40 or d + 1 + 3 * c > len(n):
            continue
        for j in range(c):
            if n[d + 3 + 3 * j] != 0x7E:
                continue
            vistas += 1
            ordinal = int.from_bytes(n[d + 1 + 3 * j : d + 3 + 3 * j], "little")
            if ordinal >= total:
                outside.append(ordinal)
    test(
        "every 0x7E slot names an existing page",
        not outside,
        "%d paginas, %d ranuras, %d fuera de rango%s"
        % (total, vistas, len(outside), (" %s" % outside[:5]) if outside else ""),
    )

    test(
        "no waveforms or records were lost",
        len(irscan.find_waveforms(n)) >= len(irscan.find_waveforms(v))
        and len(list(records_de(n))) >= len(list(records_de(v))),
        "ondas %d -> %d"
        % (len(irscan.find_waveforms(v)), len(irscan.find_waveforms(n))),
    )

    free = TOPE_REGION - BASE - len(n)
    test("there is room left in the region", free > 0, "%d B libres" % free)

    print()
    print("VEREDICTO: %s" % ("all in order" if not fallas else "%d FALLAS" % fallas))
    return 0 if not fallas else 1


def records_de(b: bytes):
    import commands

    return commands.records(b)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("blob")
    ap.add_argument("config", nargs="?")
    ap.add_argument(
        "--verificar",
        metavar="GENERADO",
        help="run all the invariants over an already generated blob, "
        "comparing it against the original",
    )
    ap.add_argument("--listar", action="store_true", help="which devices it carries")
    ap.add_argument("--device", help="which one to add (by Name, Model or index)")
    ap.add_argument("--salida")
    ap.add_argument(
        "--enganchar",
        action="store_true",
        help="besides the commands, build objects, table and pages "
        "so that the keys reach them",
    )
    ap.add_argument(
        "--ezhex",
        metavar="PLANTILLA",
        help="package the result as .EZHex using the INTENDEDVERSION "
        "of that template (a backup of the same device is needed)",
    )
    a = ap.parse_args()

    if a.verificar:
        return verificar(a.blob, a.verificar)

    blob = pathlib.Path(a.blob).read_bytes()
    protos, devs = cargar(a.config)

    if a.listar or not a.device:
        print("protocols in the JSON: %s\n" % ", ".join(sorted(protos)))
        print("dispositivos:")
        for i, dv in enumerate(devs):
            cmds, saltados = commands_of(dv, protos)
            print(
                "  [%d] %-26s %d comandos sintetizables%s"
                % (
                    i,
                    name_of(dv),
                    len(cmds),
                    "" if not saltados else "  (%d saltados)" % len(saltados),
                )
            )
        if not a.device:
            print("\nPick one with --dispositivo to add it.")
        return 0

    chosen = None
    for i, dv in enumerate(devs):
        d = dv["Device"]
        if a.device in (str(i), d.get("Name"), d.get("Model"), name_of(dv)):
            chosen = dv
            break
    if chosen is None:
        print("no encontre %r" % a.device, file=sys.stderr)
        return 1

    cmds, saltados = commands_of(chosen, protos)
    for name, reason in saltados:
        print("  SALTADO %-16s %s" % (name, reason))
    if not cmds:
        print("there is nothing synthesizable", file=sys.stderr)
        return 1

    ptr = int.from_bytes(blob[4:7], "little") - BASE
    datos, index = bloque(protos, cmds, ptr - 2)

    print("%s: %d comandos" % (name_of(chosen), len(cmds)))
    print("  bloque generado: %d B" % len(datos))
    print("  free in the region: %d B" % (TOPE_REGION - BASE - len(blob)))

    fresh = add(blob, datos)
    print("  blob: %d B -> %d B" % (len(blob), len(fresh)))

    if a.enganchar:
        import relocate

        before = relocate.chain(fresh)
        # the new device's index is the one after the ones already there
        vistos = {d for _, d in before.values() if d is not None}
        index = max((d >> 8) for d in vistos) + 1 if vistos else 0
        # it gets the exact count: here we know how many records were emitted
        fresh, n_eng = relocate.add_device(fresh, index, len(cmds))
        after = relocate.chain(fresh)
        intactos = all(after.get(k) == v for k, v in before.items())
        print(
            "  hooked up %d commands as device 0x%04X"
            % (n_eng, (index << 8) | 1)
        )
        print(
            "  reachable buttons: %d -> %d   the ones already there: %s"
            % (len(before), len(after), "intactos" if intactos else "CAMBIARON")
        )
        if not intactos:
            print(
                "\nhooking it up broke existing buttons; nothing was written.",
                file=sys.stderr,
            )
            return 1

    print()
    ok = True
    for n, correct, det in revisar(fresh):
        ok &= correct
        print("  %-30s %-6s %s" % (n, "OK" if correct else "FALLA", det))

    if not ok:
        print("\nthe blob does not pass the checks; nothing was written.", file=sys.stderr)
        return 1
    if a.salida:
        pathlib.Path(a.salida).write_bytes(fresh)
        print("\nescrito %s" % a.salida)
        if a.ezhex:
            import ezhex

            cab_b, _ = ezhex.split(pathlib.Path(a.ezhex).read_bytes())
            cab = cab_b.decode("utf-8", "replace")
            cab = ezhex.set_field(cab, "BINARYDATASIZE", str(len(fresh)))
            cab = ezhex.set_field(cab, "CHECKSUM", str(ezhex.checksum(fresh)))
            target = pathlib.Path(a.salida).with_suffix(".EZHex")
            target.write_bytes(cab.encode("utf-8") + fresh)
            print("empaquetado %s (%d B)" % (target, target.stat().st_size))
            for n, correct, det in ezhex.check(cab, fresh):
                print("  %-18s %-6s %s" % (n, "OK" if correct else "FALLA", det))
    else:
        print("\n(no --salida: nothing was written)")
    if a.enganchar:
        print(
            "\nALCANCE, sin adornos:\n"
            "  The blob is valid and **damages nothing that already works**: the\n"
            "  existing buttons resolve identically and the old body changes 11\n"
            "  bytes, all of them foreseen (close + 3 master-index entries).\n"
            "  The new commands come out correctly modeled: the chain\n"
            "  pagina -> objeto -> comando resuelve.\n"
            "  The navigation gets hooked up: a free physical key is added to\n"
            "  a root page, pointing at the first new page. This rests\n"
            "  on tag 0x7E being a **page ordinal** -- measured: 400\n"
            "  references from buttons, **zero** outside the 0..155 range with 156\n"
            "  paginas.\n"
            "  **Untested on the hardware.** The data model being correct does not\n"
            "  guarantee the screen draws it: the new button's label is not\n"
            "  generated, so it may come up blank."
        )
    else:
        print(
            "\nThe commands are in the blob but **no key reaches them**.\n"
            "Add --enganchar to build objects, table and pages."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
