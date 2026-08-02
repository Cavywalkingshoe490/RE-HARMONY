#!/usr/bin/env python3
"""Synthesizes IR waveforms for the Harmony One from a protocol definition.

The One's blob stores codes as **raw waveform**: little-endian u16 words
where bit 15 is the carrier gate and bits 0..14 the duration in microseconds
(see `irscan.py`). The Hub's config, on the other hand, stores the
**symbolic** code -- `G:Magnavox 13 Bit:()(0x07FF)():3` -- plus a separate
list of protocols with the timings. This module is the bridge: definition +
payload -> waveform.

The timings come from `ProtocolList.Protocols[*]` in the Hub's config, and
that structure's format is:

    IRSegments[0].Header      atoms before the bits
    IRSegments[0].Payload     Encodings[BitType].Atoms, NumberOfBits, EncodingType
    IRSegments[0].Trailer     atoms after
    IRSegments[0].TotalLength **repeat period**, not frame length
    CarrierFrequency          Hz

Each atom's `Type` is 1 = mark (carrier on), 0 = space. Verified against the
blob: Sony carries `Header Type 1 Value 2400`, and in the blob the frame's
first word is `0x8960` = a 2400 us mark. It matches.

`EncodingType` distinguishes the two families that appear:

    0   pulse distance   the bit is in the length (Sony, NEC/Toshiba)
    1   biphase / Manchester   the bit is in the order of the two halves (RC5)

**The gap between frames is derived, not stored**: `TotalLength - frame
length`. The proof is arithmetic and closes to the microsecond: a Sony 12
Bit frame measures 19,800 us, the gap present in the blob is 25,200, and
`19,800 + 25,200 = 45,000`, which is exactly Sony's `TotalLength` in the
JSON.

The entry is a **300 ms** silence split into 15-bit words, because a word
cannot exceed 32,767 once bit 15 is the gate.

## How reliable this is

This is not taken on faith, it is measured: `--validar` regenerates **every**
Sony and Toshiba waveform already in the blob from its decoded payload and
compares it byte for byte with the real ones. If the synthesizer were wrong,
that test would say so. Only once that is green does it make sense to
generate Magnavox, the protocol with nothing to check it against.

Usage:
    python3 synth_ir.py --validar <blob.bin> <hub-config.json>
    python3 synth_ir.py --generar <hub-config.json> "Magnavox 13 Bit" 0x07FF
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

MARCA = 0x8000
MAX_PALABRA = 0x7FFF
ENTRADA_US = 50_000  # the one used by 195 of the blob's 234 waveforms
CLOSE_US = 1


def cargar_protocolos(path: str) -> dict:
    """Returns {name: definition} from a Hub config."""
    d = json.loads(pathlib.Path(path).read_text())
    protos = d["resources"]["ProtocolList"]["Protocols"]
    return {p["Name"]: p for p in protos}


def _atomos(lista):
    """[(is_mark, us)] from a list of atoms in the JSON."""
    return [(a["Type"] == 1, a["Value"]) for a in (lista or [])]


def partir_espacio(us: int) -> list[int]:
    """A long silence, in 15-bit words.

    A word cannot exceed 32,767 once bit 15 is the carrier gate, so long
    silences get split. **The rule is not simply greedy**, and finding it
    took three attempts: first "greedy", then "fill n-2 and split the rest
    in two", then two separate functions for entry and for gap. All three
    reproduced part of the cases and failed on others.

    The rule that reproduces **the six cases measured in the blob** is a
    single one: fill with words up to the cap and, **if the remainder falls
    below half a word, average the last two** so as not to emit a tiny tail.

        remainder 30,543 (93%)  ->  32767 32767 30543          (NEC tail of 96,077)
        remainder 17,233 (53%)  ->  32767 17233                (50 ms entry)
        remainder  8,495 (26%)  ->  32767 x14 20631 20631      (500 ms entry)
        remainder  7,455 (23%)  ->  20111 20111                (Toshiba gap 40,222)
        remainder  5,097 (16%)  ->  32767 x8  18932 18932      (300 ms entry)
        remainder  1,699 ( 5%)  ->  32767 32767 17233 17233    (100 ms entry)

    The threshold sits between 26% and 53%, and `MAX_PALABRA // 2` falls
    right in there.
    """
    if us <= MAX_PALABRA:
        return [us]
    llenas, resto = divmod(us, MAX_PALABRA)
    if resto == 0:
        return [MAX_PALABRA] * llenas
    if resto >= MAX_PALABRA // 2:
        return [MAX_PALABRA] * llenas + [resto]
    # short tail: split between the last two to even them out
    junto = MAX_PALABRA + resto
    return [MAX_PALABRA] * (llenas - 1) + [junto // 2, junto - junto // 2]


def bits_de(value: int, n: int, lsb_first: bool = True) -> list[int]:
    b = [(value >> i) & 1 for i in range(n)]
    return b if lsb_first else b[::-1]


def segmentos(proto: dict) -> dict:
    """{name: definition} combining `IRSegments` and `CodeSegments`."""
    out = {}
    for s in (proto.get("IRSegments") or []) + (proto.get("CodeSegments") or []):
        out[s["Name"]] = s
    return out


def render(seg: dict, value: int, lsb_first: bool = True) -> list[tuple[bool, int]]:
    """A segment as [(is_mark, us)].

    Two shapes, and the difference matters: a segment with `Payload` carries
    the payload bits between header and trailer; one without `Payload` is
    **fixed** and its whole shape lives in `Atoms`. NEC's short repeat frame
    is of the second kind: it carries no data, it just says "still pressed".
    """
    pl = seg.get("Payload")
    if not pl:
        return _atomos(seg.get("Atoms"))
    out = list(_atomos(seg.get("Header")))
    codif = {e["BitType"]: _atomos(e["Atoms"]) for e in pl["Encodings"]}
    for bit in bits_de(value, pl["NumberOfBits"], lsb_first):
        out.extend(codif[bit])
    out.extend(_atomos(seg.get("Trailer")))
    return out


def trama(proto: dict, value: int, lsb_first: bool = True) -> list[tuple[bool, int]]:
    """The main frame, without entry or gap. Compatibility."""
    return render(proto["IRSegments"][0], value, lsb_first)


def fundir(atomos: list[tuple[bool, int]]) -> list[tuple[bool, int]]:
    """Merges consecutive atoms of the same kind.

    Needed for biphase: two consecutive half-bits of the same level are a
    single pulse of double the length, which is how RC5 looks on the air and
    how the firmware has to play it back.
    """
    out: list[list] = []
    for es_marca, us in atomos:
        if out and out[-1][0] == es_marca:
            out[-1][1] += us
        else:
            out.append([es_marca, us])
    return [(a, b) for a, b in out]


def a_palabras(atomos: list[tuple[bool, int]]) -> list[int]:
    out = []
    for es_marca, us in atomos:
        if es_marca:
            out.append(MARCA | min(us, MAX_PALABRA))
        else:
            out.extend(partir_espacio(us))
    return out


def sintetizar(
    proto: dict,
    value: int,
    repeticiones: int = 3,
    lsb_first: bool = True,
    entrada_us: int = ENTRADA_US,
):
    """The complete waveform, in u16 words ready for the blob.

    `entrada_us` and `repeticiones` **do not come from the protocol**: they
    are per-command parameters. In the blob the entry takes four values (50,
    100, 300 and 500 ms, with 50 in 195 of 234 waveforms) and frames are 3 or
    2. The only thing `ProtocolList` decides is the frame and the gap, and
    that is the only thing that gets validated.
    """
    segs = segmentos(proto)
    kc = proto.get("KeyCode") or {}

    def bloque(name):
        """Segment `name` plus the silence that brings it to its period."""
        seg = segs[name]
        at = fundir(render(seg, value, lsb_first))
        largo = sum(us for _, us in at)
        total = seg.get("TotalLength") or 0
        gap = max(total - largo, 0)
        return at, gap

    palabras = a_palabras([(False, entrada_us)])

    # `KeyCode.Start` is the frame that carries the payload; `KeyCode.Repeat`
    # is what gets sent while the key stays pressed. Sony and Magnavox have
    # no `Start` and repeat the same frame; NEC/Toshiba **switches segment**:
    # one long frame with the 32 bits and then short "still pressed" frames.
    # Treating both families the same is what made the 61 Toshiba waveforms
    # fail.
    inicio = [s["SegmentName"] for s in (kc.get("Start") or [])]
    repite = [s["SegmentName"] for s in (kc.get("Repeat") or [])] or [
        proto["IRSegments"][0]["Name"]
    ]

    for name in inicio:
        at, gap = bloque(name)
        palabras.extend(a_palabras(at))
        if gap:
            palabras.extend(a_palabras([(False, gap)]))

    missing = repeticiones - len(inicio)
    for _ in range(max(missing, 0)):
        for name in repite:
            at, gap = bloque(name)
            palabras.extend(a_palabras(at))
            if gap:
                palabras.extend(a_palabras([(False, gap)]))

    palabras.extend(a_palabras([(False, CLOSE_US)]))
    return palabras


def a_bytes(palabras: list[int]) -> bytes:
    return b"".join(w.to_bytes(2, "little") for w in palabras)


def entrada_de(real: list[int]) -> int:
    """The entry (initial silence) of a REAL waveform, in microseconds.

    Sums the space words before the first mark. It exists so that whoever
    compares a factory waveform against `sintetizar()` does not have to
    guess the `entrada_us` parameter: it is measured, not assumed. See
    `partir_espacio` -- its inverse, which splits it back into words.
    """
    i = 0
    while i < len(real) and not real[i] & MARCA:
        i += 1
    return sum(x & MAX_PALABRA for x in real[:i])


def repeticiones_de(real: list[int]) -> int:
    """Counts the frames of a REAL waveform: one mark >= 2000 us per frame.

    2000 deliberately separates us from Toshiba's header mark (8990 us) and
    from bit marks (568 us): none of the frames that appear in the blob has
    a mark in the middle.
    """
    return sum(1 for x in real if x & MARCA and (x & MAX_PALABRA) >= 2000)


# --------------------------------------------------------------------------
# validation against the blob


def validar(blob_path: str, cfg_path: str) -> int:
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    import irscan

    b = pathlib.Path(blob_path).read_bytes()
    protos = cargar_protocolos(cfg_path)

    ok = fallo = sin_proto = sin_decodificar = 0
    detalles = []
    for at in irscan.find_waveforms(b):
        real = irscan.read_waveform(b, at)
        r = irscan.decode(real)
        if not r:
            sin_decodificar += 1
            continue
        name, _bits, value = r
        if name not in protos:
            sin_proto += 1
            continue
        # entry and repetitions are parameters of the command, not of the
        # protocol: they are read from the real waveform so the test only
        # measures what is actually derived
        entrada = entrada_de(real)
        reps = repeticiones_de(real)
        mio = None
        for lsb in (True, False):
            cand = sintetizar(protos[name], value, max(reps, 1), lsb, entrada)
            if cand == real:
                mio = cand
                break
        if mio is not None:
            ok += 1
        else:
            fallo += 1
            if len(detalles) < 3:
                cand = sintetizar(protos[name], value, max(reps, 1), True, entrada)
                k = next(
                    (
                        j
                        for j in range(max(len(cand), len(real)))
                        if j >= len(cand) or j >= len(real) or cand[j] != real[j]
                    ),
                    0,
                )
                detalles.append(
                    "  %#08x %-16s differs at word %d of %d/%d\n"
                    "      real %s\n      mine %s"
                    % (
                        at,
                        name,
                        k,
                        len(real),
                        len(cand),
                        " ".join("%04x" % x for x in real[max(0, k - 2) : k + 4]),
                        " ".join("%04x" % x for x in cand[max(0, k - 2) : k + 4]),
                    )
                )

    tot = ok + fallo
    print("blob waveforms regenerated from the Hub's definition:")
    print("  byte-for-byte identical  %d of %d" % (ok, tot))
    print("  differ                   %d" % fallo)
    print("  no protocol in the JSON  %d" % sin_proto)
    print("  not decoded              %d" % sin_decodificar)
    for d in detalles:
        print(d)
    if tot and ok == tot:
        print("\nThe synthesizer reproduces the device's format. Magnavox can be")
        print("generated with the same confidence as Sony and Toshiba.")
    return 0 if tot and ok == tot else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--validar", nargs=2, metavar=("BLOB", "CFG"))
    ap.add_argument("--generar", nargs=3, metavar=("CFG", "PROTOCOLO", "VALOR"))
    a = ap.parse_args()

    if a.validar:
        return validar(*a.validar)
    if a.generar:
        cfg, name, value = a.generar
        protos = cargar_protocolos(cfg)
        if name not in protos:
            print("unknown protocol: %r" % name, file=sys.stderr)
            print("available: %s" % ", ".join(sorted(protos)), file=sys.stderr)
            return 1
        w = sintetizar(protos[name], int(value, 0))
        print(
            "%s  payload %s  ->  %d words, %d B" % (name, value, len(w), 2 * len(w))
        )
        for i, x in enumerate(w):
            print(
                "  [%2d] %04x  %-5s %6d us"
                % (i, x, "MARK" if x & MARCA else "space", x & MAX_PALABRA)
            )
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
