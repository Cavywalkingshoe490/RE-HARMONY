#!/usr/bin/env python3
"""Learn IR codes with the Harmony One's RECEIVER and register a device that
is not in Logitech's catalog.

This module is the app-side counterpart of `config_work/learn.py`. **It
reimplements nothing**: libconcord does the capture (`learn_from_remote`,
read-only), `config_work/irscan.py:decode` does the protocol recognition,
`config_work/synth_ir.py` regenerates the clean waveform, `app/library.py:escribir()`
writes the final file -- the SAME one the catalog path already uses -- and
from there it follows the usual flow (generate, gate, apply) with no special
case at all.

## Why the capture runs in a SUBPROCESS

libconcord's `_handle_ir_response()` (`remote.cpp:864-871`) takes
`len = rsp[63]` -- up to 255 -- and indexes `rsp[u]`/`rsp[1+u]` over a
**68-byte stack buffer**. An unusual HID report produces an out-of-range
read and can crash the process. The pywebview window cannot afford that, so
`capture()` launches `config_work/learn.py --json-stdout` as a
subprocess: if it crashes, only the subprocess dies and the app reports it.

## What libconcord returns, and how it's read (VERIFIED by reading the source)

`learn_from_remote()` delivers `ir_signal` as durations **alternating in
microseconds, starting on a MARK and ending on a space** (always an even
length) -- `libconcord.h:437-458`, and assembled in `remote.cpp:863-1013`.
That is exactly the same convention already used by the `.ir` importer
(`app/ir_manual.py:_atomos_de`), so the captured waveform enters the
pipeline without any new conversion.

**libconcord's DATA path matches the manufacturer's decoder.** The two
sources were compared line by line:

    libconcord  remote.cpp:888-895   odd word: `t == on + off`, off = t - on
    Logitech    CarrierProcessor.processData  space = word/1e6 - lastEnvelope

They're the same arithmetic: the second word of every pair is measured from
the SAME origin as the first, and both implementations subtract it the same
way. That's why the marks and spaces in the body of the waveform are
trustworthy.

**CALIBRATION does NOT match, and this has already bitten before.** The
first 3 words of the stream are carrier calibration, and that's where the
two sources diverge:

    libconcord   remote.cpp:919-931
        word0 -> DISCARDED  (`case 0: // ???`, the author didn't know what it was)
        word1 -> raw t_on, and it gets WRITTEN as the waveform's first atom
        word2 -> freq = word2 * 1e6 / t_on

    Logitech     LearnIrHidServiceSender$CarrierProcessor.processCalibrationData
        word0 -> m_LastPulseOnTime
        word1 -> m_FirstPulseTime
        envelopeTime      = word1 - word0
        numClocksEnvelope = word2 - 1
        freq = 1e6 / (envelopeTime / numClocksEnvelope)

In other words: if word 0 isn't zero, libconcord returns the `carrier_clock`
shifted **and the waveform's first mark inflated by word0**. Word 0 is not
reachable through `learn_from_remote()`, so it cannot be corrected from
Python. Practical consequences, already anticipated in the code below:

  1. `recognize()` first tries the whole capture and, if it doesn't
     recognize anything, **falls back to the second frame** -- which comes
     through the data path, the one that DOES match between the two
     sources. Reported in `via`.
  2. The carrier the device returns is SHOWN but **not used to decide
     anything**, nor to compare two captures. When the protocol is
     recognized, the carrier that gets recorded comes from the protocol's
     definition (`CarrierFrequency` from the factory `ProtocolList`), not
     from the capture.
  3. If the carrier comes back outside 30-45 kHz, the most likely cause is
     this divergence and not a problem with the original remote. The UI
     warns about it.

## The two thresholds, MEASURED against the factory blob (not invented)

`GAP_THRESHOLD_US = 10_000` -- separates "space inside a frame" from "gap
between frames". Measured over the 234 waveforms in
`backups/config_raw.bin` (407 gaps):

    max INTRA-frame space :  4,490 us   (NEC's header space)
    min gap BETWEEN frames: 18,600 us

10,000 sits in the middle with a 2.2x margin below and 1.86x above.

`TOLERANCIA_REL = 0.25` -- how much a duration can differ between two
captures of the same key. The hard bound comes from the same measurement:
the worst pair of bit symbols in the blob is Sony's 600 us (zero) vs
1200 us (one), ratio 2.00. For the tolerance bands of a zero and a one to
NEVER touch, `t < (2.00-1)/(2.00+1) = 0.333` is required. 0.25 stays below
that with margin. (Toshiba, the other real case, gives 552 vs 1662 -> ratio
3.01, even more slack.) **[ASSUMED]**: whether 0.25 is also WIDE enough to
absorb the receiver's real jitter is not measured -- there's no real capture
yet. If two legitimate captures keep coming out "different", this is the
number to raise, and it can go up to 0.33 with no risk of confusing a bit.

## What gets saved: the CLEAN waveform if recognized, the raw one if not

If `irscan.decode()` recognizes (protocol, value) and the library
(`app/library.py:available_protocols()`) has that protocol's
definition, the command is saved as a normal reference
`G:<protocol>:(0x<value>)()()` -- exactly like a command downloaded from the
catalog. `synth_ir.py` regenerates the waveform from the factory timing
table: the capture's noise is discarded and what's left is a waveform
validated 234/234 against the blob. If it is NOT recognized, the FIRST RAW
FRAME is saved as a fixed segment (`Atoms` with no `Payload`), the same as
`app/ir_manual.py` does with an `.ir`.

## What this module does NOT do

It does not write flash, does not call any libconcord write primitive, and
does not touch `account_export/`. `config_work/learn.py` only declares, via
ctypes, `learn_from_remote`/`delete_ir_signal` plus identification; no
destructive primitive is declared at all.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
RAIZ = APP.parent
CONFIG_WORK = RAIZ / "config_work"
APRENDER_PY = CONFIG_WORK / "learn.py"

for _r in (str(APP), str(CONFIG_WORK)):
    if _r not in sys.path:
        sys.path.insert(0, _r)

import _runtime  # noqa: E402
import fonts  # noqa: E402  -- read-only (what letters the hardware draws)
import glyphs  # noqa: E402  -- read-only (what letters the blob knows how to write)
import irscan  # noqa: E402  -- decode(): waveform -> (protocol, bits, value)
import synth_ir  # noqa: E402  -- a_palabras/fundir/sintetizar, validated 234/234

# --------------------------------------------------------------------------
# measured constants (see the module's docstring)
# --------------------------------------------------------------------------

#: Space above which it counts as a "gap between frames".
#: Measured: intra-frame max 4,490 us, inter-frame min 18,600 us.
GAP_THRESHOLD_US = 10_000

#: Relative tolerance when comparing two captures of the same key.
#: Hard bound measured: 0.333 (Sony 600 vs 1200). Uses 0.25.
TOLERANCIA_REL = 0.25

#: Absolute floor, so a short duration doesn't fail from rounding.
#: 100 us sits below 25% of the blob's shortest symbol (552 us), so it never
#: widens the band beyond the bound above.
TOLERANCIA_ABS_US = 100

#: Range in which a carrier makes sense. Only for warning, not for deciding.
CARRIER_MIN_HZ, CARRIER_MAX_HZ = 30_000, 45_000

#: libconcord's cap (`remote.h:47`). Reaching this means a TRUNCATED waveform.
MAX_IR_SIGNAL_LENGTH = 1000

#: How long the capture subprocess waits. libconcord blocks for up to 5 s
#: waiting for the first signal (`IR_LEARN_START_TIMEOUT`) plus the
#: waveform's duration plus the closing handshake; 45 s leaves plenty of
#: room to plug in/aim.
TIMEOUT_CAPTURA_S = 45.0

MARCA_JSON = "APRENDER_JSON:"

#: The literal is repeated, `add_device.py` is not imported -- same rule
#: already followed by `app/generate.py` and `app/ir_manual.py`. Every new
#: device has to be able to write this fixed label
#: (`add_device.py:ETIQUETA_VOLVER`).
ROTULO_VOLVER = "Devices"


# --------------------------------------------------------------------------
# the standard command list
# --------------------------------------------------------------------------


def _std(name: str, label: str, grupo: str) -> dict:
    return {"name": name, "label": label, "grupo": grupo}


#: `name` is what goes into `Commands[].Name` -- they're Logitech's
#: canonical names, the same 12 that `config_work/learn_keys.xml` brings
#: (the old client's learning tutorial), plus the digits and a few
#: navigation ones. `label` is what gets DRAWN on the remote's screen: a
#: `FunctionList` is emitted with that `Label`, which is where
#: `add_device.py:hub_labels()` pulls it from. Kept short on
#: purpose: every extra letter is one more letter the blob has to be able to
#: write.
STANDARD_COMMANDS: list[dict] = [
    _std("Power", "Power", "Power"),
    _std("PowerOn", "On", "Power"),
    _std("PowerOff", "Off", "Power"),
    _std("VolumeUp", "Vol Up", "Volume"),
    _std("VolumeDown", "Vol Dn", "Volume"),
    _std("Mute", "Mute", "Volume"),
    _std("ChannelUp", "Ch Up", "Channels"),
    _std("ChannelDown", "Ch Dn", "Channels"),
    _std("Play", "Play", "Playback"),
    _std("Pause", "Pause", "Playback"),
    _std("Stop", "Stop", "Playback"),
    _std("InputHdmi1", "HDMI 1", "Inputs"),
    _std("Input", "Input", "Inputs"),
    _std("Menu", "Menu", "Navigation"),
    _std("Exit", "Exit", "Navigation"),
    _std("DirectionUp", "Up", "Navigation"),
    _std("DirectionDown", "Down", "Navigation"),
    _std("DirectionLeft", "Left", "Navigation"),
    _std("DirectionRight", "Right", "Navigation"),
    _std("Select", "OK", "Navigation"),
    *[_std("Number%d" % d, str(d), "Digits") for d in range(10)],
]


# --------------------------------------------------------------------------
# the waveform: atoms, frames, recognition
# --------------------------------------------------------------------------


def atoms_of(ir_signal_us: list[int]) -> list[tuple[bool, int]]:
    """`[mark, space, mark, ...]` (us) -> `[(is_mark, us), ...]`.

    libconcord guarantees `ir_signal` starts on a mark and alternates
    (`libconcord.h:443-446`), so the type comes from POSITION, same as in a
    Flipper `.ir` (`app/ir_manual.py:_atomos_de`).
    """
    return [(i % 2 == 0, int(us)) for i, us in enumerate(ir_signal_us) if us]


def frames_and_gaps(
    atomos: list[tuple[bool, int]],
) -> tuple[list[list[tuple[bool, int]]], list[int]]:
    """Splits the capture into frames and also returns the gaps BETWEEN them.

    Cuts on every space >= `GAP_THRESHOLD_US` (threshold MEASURED against the
    factory blob, see the module's docstring). `gaps[i]` is the silence
    between `frames[i]` and `frames[i+1]`, so there is always one gap fewer
    than frames.

    **The TRAILING silence is not a gap, and that's why it isn't returned.**
    It's the difference that matters: a real capture ends with the silence
    that closed the capture (up to `IR_LEARN_DONE_TIMEOUT`, 500 ms) and a
    simulated one with the final padding. Averaging that into the gaps
    inflated the command's period -- measured: for a Sony 12 Bit from the
    blob it gave 45,200 us when the factory `TotalLength` requires 25,200 us
    of gap (45,000 of period minus 19,800 of frame). That number gets
    written into the `TotalLength` of commands saved RAW, so it was
    stretching the repeat interval almost by double.
    """
    out: list[list[tuple[bool, int]]] = []
    gaps: list[int] = []
    actual: list[tuple[bool, int]] = []
    outstanding = 0  # the gap just seen, still without a frame following it
    for es_marca, us in atomos:
        if not es_marca and us >= GAP_THRESHOLD_US:
            if actual:
                out.append(actual)
                actual = []
                outstanding = us
            elif outstanding:
                outstanding += us  # two long spaces in a row: it's one single gap
            continue
        if not actual and not es_marca:
            continue  # loose space before the first mark: not a frame
        if not actual and outstanding:
            gaps.append(outstanding)  # only now is it known that a gap just closed
            outstanding = 0
        actual.append((es_marca, us))
    if actual:
        out.append(actual)
    return out, gaps


def frames(atomos: list[tuple[bool, int]]) -> list[list[tuple[bool, int]]]:
    """Only the frames. See `frames_and_gaps()`."""
    return frames_and_gaps(atomos)[0]


def words_of(atomos: list[tuple[bool, int]]) -> list[int]:
    """Atoms -> blob u16 words (bit15=mark). All `synth_ir.py`."""
    return synth_ir.a_palabras(synth_ir.fundir(atomos))


def recognize(atomos: list[tuple[bool, int]]) -> dict | None:
    """`irscan.decode()` on the capture. `None` if nothing is recognized.

    Two attempts, in this order:

      1. The WHOLE capture. This works when libconcord's calibration returns
         a correct first mark.
      2. Each frame starting from the SECOND one. The capture's first mark
         is the only one that goes through the calibration path
         (`remote.cpp:931`), which is where libconcord and Logitech's
         decoder diverge (see the docstring). The following frames arrive
         whole through the data path, where the two sources agree -- so if
         the protocol is there, it shows up.

    This is not a blind patch: it is exactly the symptom predicted by
    reading the source, and `via` records which of the two paths matched so
    the first live test can confirm or refute it.
    """
    r = irscan.decode(words_of(atomos))
    if r:
        return {
            "protocolo": r[0],
            "bits": r[1],
            "value": r[2],
            "via": "full capture",
        }
    for k, tr in enumerate(frames(atomos)):
        if k == 0:
            continue
        r = irscan.decode(words_of(tr))
        if r:
            return {
                "protocolo": r[0],
                "bits": r[1],
                "value": r[2],
                "via": "frame %d (the capture's 1st mark did not match -- see "
                "the calibration divergence in the docstring)" % (k + 1),
            }
    return None


def wave_summary(atomos: list[tuple[bool, int]]) -> dict:
    """Numbers for the UI. Decides nothing."""
    tr = frames(atomos)
    return {
        "atomos": len(atomos),
        "duracion_us": sum(us for _, us in atomos),
        "tramas": len(tr),
        "atomos_trama1": len(tr[0]) if tr else 0,
        "marca_min": min((us for m, us in atomos if m), default=0),
        "marca_max": max((us for m, us in atomos if m), default=0),
        "espacio_max": max((us for m, us in atomos if not m), default=0),
    }


def analyze(captura: dict) -> dict:
    """Raw capture (what `config_work/learn.py` returns) -> everything the
    screen needs: waveform, summary, recognized protocol, and warnings."""
    at = atoms_of(captura.get("ir_signal_us") or [])
    rec = recognize(at) if at else None
    hz = int(captura.get("carrier_clock_hz") or 0)

    avisos: list[str] = []
    if captura.get("truncada") or len(at) >= MAX_IR_SIGNAL_LENGTH:
        avisos.append(
            "waveform TRUNCATED at %d durations: that's libconcord's cap and "
            "it does NOT return an error, the waveform just cuts off in "
            "silence. Press the key once and let go, don't hold it down."
            % MAX_IR_SIGNAL_LENGTH
        )
    if captura.get("partial"):
        avisos.append(
            "libconcord returned error %s (%s) but a waveform came through "
            "anyway. The known case is the closing handshake not arriving "
            "within 500 ms (remote.cpp:1041-1047): the waveform is kept as "
            "is, not discarded." % (captura.get("err"), captura.get("err_text") or "?")
        )
    if hz and not (CARRIER_MIN_HZ <= hz <= CARRIER_MAX_HZ):
        avisos.append(
            "the returned carrier (%d Hz) is outside 30-45 kHz. The most "
            "likely cause is NOT the original remote but libconcord's "
            "calibration, which discards word 0 (remote.cpp:919) while "
            "Logitech's decoder uses word1-word0. It isn't used for "
            "anything: if the protocol is recognized, the carrier comes "
            "from the factory ProtocolList." % hz
        )
    if not at:
        avisos.append("no duration came through at all: the receiver captured nothing.")

    return {
        "atomos": [[bool(m), int(us)] for m, us in at],
        "summary": wave_summary(at),
        "carrier_hz": hz,
        "reconocido": rec,
        "avisos": avisos,
        "err": captura.get("err", 0),
        "truncada": bool(captura.get("truncada")),
    }


# --------------------------------------------------------------------------
# comparing two captures (point 3 of the brief)
# --------------------------------------------------------------------------


def _similar(a: int, b: int) -> bool:
    return abs(a - b) <= max(TOLERANCIA_ABS_US, TOLERANCIA_REL * max(a, b))


def compare(analisis_a: dict, analisis_b: dict) -> dict:
    """Decides whether two captures are of the SAME key. Without this, noise
    gets saved.

    Two paths, in order of strength:

      1. If both recognize a protocol and give the SAME (protocol, value),
         it's the same key. This is the strong criterion: immune to jitter,
         because it compares meaning, not microseconds.
      2. If not, the FIRST FRAMES are compared atom by atom with the
         measured tolerance. The first frame is compared, not the whole
         capture, because the user doesn't hold the key down for the same
         amount of time twice: the number of repeated frames changes even
         though the key doesn't.

    The carrier does NOT enter the comparison: it comes from libconcord's
    calibration path, which is the one that diverges from the manufacturer's
    decoder (see the module's docstring). Comparing it would fail good
    captures.
    """
    ra, rb = analisis_a.get("reconocido"), analisis_b.get("reconocido")
    if ra and rb:
        if (ra["protocolo"], ra["value"]) == (rb["protocolo"], rb["value"]):
            return {
                "coinciden": True,
                "criterio": "protocolo",
                "detail": "both captures decode to %s value %#x"
                % (ra["protocolo"], ra["value"]),
                "peor_desvio_us": None,
                "peor_desvio_rel": None,
            }
        return {
            "coinciden": False,
            "criterio": "protocolo",
            "detail": "they decode differently: %s %#x vs %s %#x -- these "
            "are two different keys, not two captures of the same one"
            % (ra["protocolo"], ra["value"], rb["protocolo"], rb["value"]),
            "peor_desvio_us": None,
            "peor_desvio_rel": None,
        }

    ta = frames([(bool(m), int(u)) for m, u in analisis_a.get("atomos") or []])
    tb = frames([(bool(m), int(u)) for m, u in analisis_b.get("atomos") or []])
    if not ta or not tb:
        return {
            "coinciden": False,
            "criterio": "onda",
            "detail": "one of the two captures has no frame at all",
            "peor_desvio_us": None,
            "peor_desvio_rel": None,
        }
    fa, fb = ta[0], tb[0]
    if len(fa) != len(fb):
        return {
            "coinciden": False,
            "criterio": "onda",
            "detail": "the first frame has %d durations in one capture and "
            "%d in the other" % (len(fa), len(fb)),
            "peor_desvio_us": None,
            "peor_desvio_rel": None,
        }
    peor_us, peor_rel, malos = 0, 0.0, 0
    for (ma, ua), (mb, ub) in zip(fa, fb):
        if ma != mb:
            return {
                "coinciden": False,
                "criterio": "onda",
                "detail": "the mark/space alternation does not match",
                "peor_desvio_us": None,
                "peor_desvio_rel": None,
            }
        d = abs(ua - ub)
        peor_us = max(peor_us, d)
        peor_rel = max(peor_rel, d / max(ua, ub, 1))
        if not _similar(ua, ub):
            malos += 1
    return {
        "coinciden": malos == 0,
        "criterio": "onda",
        "detail": (
            "%d of %d durations match within tolerance (±%d%% or ±%d us)"
            % (len(fa) - malos, len(fa), int(TOLERANCIA_REL * 100), TOLERANCIA_ABS_US)
        ),
        "peor_desvio_us": peor_us,
        "peor_desvio_rel": round(peor_rel, 4),
    }


# --------------------------------------------------------------------------
# capturing (subprocess -- see the module's docstring)
# --------------------------------------------------------------------------


def capture(key: str | None = None, timeout: float = TIMEOUT_CAPTURA_S) -> dict:
    """Runs `config_work/learn.py --json-stdout` and returns the capture.

    NEVER imports libconcord in this process: see "Why the capture runs in
    a SUBPROCESS" in the module's docstring.
    """
    if not APRENDER_PY.exists():
        return {"ok": False, "error": "%s does not exist" % APRENDER_PY}
    argv = [*_runtime.interprete(), str(APRENDER_PY), "--json-stdout"]
    if key:
        argv += ["--key", str(key)]
    try:
        proc = subprocess.run(
            argv,
            cwd=str(CONFIG_WORK),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": "the capture did not finish within %.0f s. libconcord "
            "blocks for up to 5 s waiting for the signal; if it takes longer "
            "than that, it usually means the remote is busy with another "
            "operation." % timeout,
            "argv": argv,
        }
    except OSError as exc:
        return {"ok": False, "error": str(exc), "argv": argv}

    crudo = None
    for line in (proc.stdout or "").splitlines():
        if line.startswith(MARCA_JSON):
            try:
                crudo = json.loads(line[len(MARCA_JSON) :])
            except json.JSONDecodeError:
                crudo = None
    if crudo is None:
        return {
            "ok": False,
            "error": "the capture helper answered nothing (exit code %s). The "
            "usual cause is that the remote is not plugged in: connect it by USB "
            "and try again." % proc.returncode,
            "argv": argv,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    if not crudo.get("ok"):
        return {
            "ok": False,
            "error": crudo.get("error") or "the capture failed",
            "argv": argv,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    return {
        "ok": True,
        "captura": crudo,
        "analisis": analyze(crudo),
        "argv": argv,
        "stderr": proc.stderr,
    }


# --------------------------------------------------------------------------
# test bank: a SIMULATED capture from the factory blob
# --------------------------------------------------------------------------


def available_banks(blob: bytes, tope: int = 24) -> list[dict]:
    """REAL waveforms from the factory blob, to test the screen without the
    remote.

    It is not a capture: it is a waveform already recorded on the device,
    converted to the shape `learn_from_remote()` would return. It serves as
    a positive check of everything downstream (recognition, comparison,
    JSON, generation).
    """
    out, vistos = [], set()
    for p in irscan.find_waveforms(blob):
        w = irscan.read_waveform(blob, p)
        r = irscan.decode(w)
        if not r:
            continue
        clave = (r[0], r[2])
        if clave in vistos:
            continue
        vistos.add(clave)
        out.append({"offset": p, "protocolo": r[0], "bits": r[1], "value": r[2]})
        if len(out) >= tope:
            break
    return out


def simulated_capture(blob: bytes, offset: int, sesgo_1ra_marca_us: int = 0) -> dict:
    """Converts the waveform at `offset` in the blob into `learn_from_remote`'s
    shape.

    `sesgo_1ra_marca_us` simulates the calibration divergence described in
    the docstring: libconcord inflates the first mark because of the word 0
    it discards. With a nonzero bias this capture still has to be
    recognized, but through the SECOND frame -- it's the check that
    `recognize()` does what it claims.
    """
    w = irscan.read_waveform(blob, offset)
    at: list[tuple[bool, int]] = []
    for x in w:
        at.append((bool(x & synth_ir.MARCA), x & synth_ir.MAX_PALABRA))
    at = synth_ir.fundir(at)
    while at and not at[0][0]:
        at.pop(0)  # libconcord starts on a mark: the lead-in is not captured
    if not at:
        return {"ok": False, "error": "no waveform at %#x" % offset}
    if sesgo_1ra_marca_us:
        at[0] = (True, at[0][1] + sesgo_1ra_marca_us)
    if not at[-1][0]:
        at.pop()
    at.append((False, 40_000))  # the trailing silence that closes the capture
    return {
        "ok": True,
        "carrier_clock_hz": 38_000,
        "ir_signal_us": [us for _, us in at],
        "ir_signal_length": len(at),
        "err": 0,
        "err_text": "",
        "partial": False,
        "truncada": False,
        "simulada": True,
        "origin": "factory blob, waveform at %#08x" % offset,
    }


# --------------------------------------------------------------------------
# name and label validation (point 5 of the brief)
# --------------------------------------------------------------------------


def _glyph_table(blob: bytes, textos: list[str]) -> dict:
    """The table `add_device.py` is going to have: `glyphs.extender()`
    against the blob, with the vocabulary of the REAL configs on disk plus
    the texts about to be written. Same input `app/library.py` freezes
    inside the file (`vocabulario_heredado_de_catalogo`)."""
    vocab: set[str] = set()
    try:
        import library

        vocab |= library.vocabulary()
    except Exception:  # noqa: BLE001
        pass
    for t in textos:
        if t:
            vocab.add(t)
            vocab.update(p for p in t.split() if p)
    table, _aprendidos = glyphs.extender(blob, vocab)
    return table


def _missing(text: str, table: dict) -> list[str]:
    inv = {v: k for k, v in table.items()}
    return sorted({c for c in text if c not in inv})


def validate_texts(name: str, labels: list[str], blob: bytes) -> dict:
    """The TWO checks that really decide whether this can be written, both
    for the device's name AND for every chosen command label:

      1. **Draw**: `fonts.choose()` / `elegir_detalle()` -- is there any
         font in the blob with all those strokes. This is what the brief
         asks for.
      2. **Write**: `glyphs` -- the blob does not store ASCII but a glyph
         index, and that table is learned from the vocabulary. A letter can
         have a stroke and no index.

    It also validates the fixed label `Devices`, which `add_device.py`
    writes for ANY new device: if the vocabulary isn't enough for it, no
    name can save the generation.
    """
    name = (name or "").strip()
    labels = [e for e in (labels or []) if e]
    problemas: list[dict] = []

    table = _glyph_table(blob, [name, *labels, ROTULO_VOLVER])

    def _check_one(text: str, what: str) -> dict:
        det = fonts.choose_detail(text, blob)
        missing_font = det.get("atributo") is None
        warning = det.get("warning") or ""
        cambia_paleta = warning.startswith("PALETTE CHANGES")
        missing_g = _missing(text, table)
        r = {
            "text": text,
            "what": what,
            "dibuja_ok": not missing_font and not cambia_paleta,
            "draw_missing": sorted(det.get("missing") or []),
            "draw_warning": det.get("warning"),
            "escribe_ok": not missing_g,
            "write_missing": missing_g,
        }
        r["ok"] = r["dibuja_ok"] and r["escribe_ok"]
        if not r["ok"]:
            problemas.append(r)
        return r

    detail = []
    if name:
        detail.append(_check_one(name, "device name"))
    detail.extend(_check_one(e, "command label") for e in labels)
    detail.append(_check_one(ROTULO_VOLVER, "fixed back label"))

    return {
        "ok": bool(name) and not problemas,
        "name": name,
        "detail": detail,
        "problemas": problemas,
        "no_name": not name,
    }


# --------------------------------------------------------------------------
# building the JSON that add_device.py consumes (point 4 of the brief)
# --------------------------------------------------------------------------


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "x"


def _raw_protocol(idx: int, cmd_name: str, hz: int, atomos, gap_us: int) -> dict:
    """A NOT-recognized command: the first frame as is, as a fixed segment.

    `sintir.render()` returns `Atoms` untouched when the segment has no
    `Payload` -- it's the same mechanism the factory blob's short Toshiba
    repeat frame uses, not a new path.
    """
    largo = sum(us for _, us in atomos)
    seg = {
        "Name": "Main",
        "Atoms": [{"Type": 1 if m else 0, "Value": us} for m, us in atomos],
        "TotalLength": largo + gap_us,
    }
    return {
        "Name": "Aprendido-%03d-%s" % (idx, _slug(cmd_name)[:24]),
        "CarrierFrequency": hz or 38_000,
        "IRSegments": [seg],
        "KeyCode": {
            "Start": [{"SegmentName": "Main"}],
            "Repeat": [{"SegmentName": "Main"}],
        },
    }


def build_resources(
    commands: list[dict],
    fabricante: str,
    modelo: str,
    device_name: str,
    protocolos_lib: dict | None = None,
) -> dict:
    """`commands` = [{nombre, etiqueta, analisis}] accepted -> `resources`.

    Each command takes one of two paths:

      * **clean**: `analisis.reconocido` carries (protocol, value) and the
        library has that protocol's definition -> saved as
        `G:<protocol>:(0x<value>)()()`, just like a catalog command. The
        waveform gets regenerated by `synth_ir.py` from the factory timings:
        the capture's noise is discarded.
      * **raw**: not recognized -> the FIRST FRAME captured as a fixed
        segment, and the gap between frames MEASURED from the capture
        itself (not invented) when there are two frames or more.

    `FunctionList` is not left empty like in the `.ir` importer: it is
    emitted with each command's short `Label`, which is where
    `add_device.py:hub_labels()` pulls the button's text from.
    Without this the label falls back to `split_camel(Name)` ("Volume Up"
    instead of "Vol Up"), three more letters the blob has to be able to
    write.
    """
    if protocolos_lib is None:
        try:
            import library

            protocolos_lib = {
                n: p.definition for n, p in library.available_protocols().items()
            }
        except Exception:  # noqa: BLE001
            protocolos_lib = {}

    dev_id = (abs(hash((fabricante, modelo, device_name))) % 0x7FFFFFFF) or 1
    protocolos: list[dict] = []
    nombres_proto: list[str] = []
    cmds: list[dict] = []
    funciones: list[dict] = []
    detail: list[dict] = []

    for i, c in enumerate(commands):
        an = c["analisis"]
        at = [(bool(m), int(u)) for m, u in an.get("atomos") or []]
        rec = an.get("reconocido")
        fid = i + 1
        limpio = bool(rec) and rec["protocolo"] in protocolos_lib

        if limpio:
            proto_name = rec["protocolo"]
            if proto_name not in nombres_proto:
                nombres_proto.append(proto_name)
                protocolos.append(protocolos_lib[proto_name])
            keycode = "G:%s:(0x%X)()()" % (proto_name, rec["value"])
            detail.append(
                {
                    "name": c["name"],
                    "modo": "limpio",
                    "protocolo": proto_name,
                    "value": rec["value"],
                    "via": rec.get("via"),
                }
            )
        else:
            tr, hcs = frames_and_gaps(at)
            trama1 = tr[0] if tr else at
            # The FIRST gap between frames, measured from the capture
            # itself: it's what sets the period the original remote uses to
            # repeat the code. Not averaged across all of them, and the
            # trailing silence is not touched either (see `frames_and_gaps`),
            # since that one closes the capture and has nothing to do with
            # the repeat rate.
            gap = hcs[0] if hcs else 0
            proto = _raw_protocol(
                i, c["name"], an.get("carrier_hz") or 0, trama1, gap or 40_000
            )
            protocolos.append(proto)
            nombres_proto.append(proto["Name"])
            keycode = "G:%s:(0x0)()()" % proto["Name"]
            detail.append(
                {
                    "name": c["name"],
                    "modo": "crudo",
                    "protocolo": proto["Name"],
                    "atomos": len(trama1),
                    "gap_us": gap or 40_000,
                    "gap_measured": bool(gap),
                }
            )

        cmds.append({"Name": c["name"], "KeyCode": keycode, "FunctionId-": fid})
        funciones.append({"FunctionId-": fid, "Label": c["label"]})

    device = {
        "Device": {
            "Manufacturer": fabricante,
            "Model": modelo,
            "Name": device_name,
            "Id-": dev_id,
            "DeviceType": 1,
            "DeviceTypeDisplayName": "Learned",
        },
        "Commands": cmds,
        "DeviceFeatures": [],
    }
    resources = {
        "ProtocolList": {"Protocols": protocolos},
        "DeviceList": {"DevicesWithFeatures": [device]},
        "FunctionList": {
            "FunctionMaps": [
                {
                    "DeviceId-": dev_id,
                    "FunctionGroups": [{"Name": "Learned", "Functions": funciones}],
                }
            ]
        },
    }
    return {
        "resources": resources,
        "detail": detail,
        "limpios": sum(1 for d in detail if d["modo"] == "limpio"),
        "crudos": sum(1 for d in detail if d["modo"] == "crudo"),
        "protocolos": nombres_proto,
    }


def save(
    commands: list[dict],
    fabricante: str,
    modelo: str,
    device_name: str,
    blob: bytes,
) -> dict:
    """Validates, builds the `resources`, and writes it with
    `biblioteca.write()`.

    Uses the SAME writer as the catalog path -- not a copy -- so the file
    comes out with the same shape, in the same folder, with the
    `vocabulario_heredado_de_catalogo` frozen inside. From there on the
    Control screen treats it like any other device: generate, gate, apply.
    """
    fabricante = (fabricante or "").strip()
    modelo = (modelo or "").strip()
    device_name = (device_name or "").strip()
    if not fabricante or not modelo:
        return {"ok": False, "error": "manufacturer and model are needed"}
    if not commands:
        return {"ok": False, "error": "no command has been accepted yet"}

    val = validate_texts(device_name, [c["label"] for c in commands], blob)
    if not val["ok"]:
        return {
            "ok": False,
            "etapa": "label",
            "error": "there are texts the remote cannot display -- aborting "
            "BEFORE writing anything",
            "validacion": val,
        }

    try:
        import library
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "library.py does not import: %s" % exc}

    armado = build_resources(commands, fabricante, modelo, device_name)
    mat = {
        "ok": True,
        "resources": armado["resources"],
        "vocabulario": library.vocabulary_block(),
        "fabricante": fabricante,
        "modelo": modelo,
        "name": device_name,
        "commands": len(commands),
        "protocolos": armado["protocolos"],
        "protocol_origins": {},
        "missing": [],
    }
    target = library.write(
        mat,
        source_kind="manual",
        source="learned with the Harmony One's IR receiver (app/learn_ir.py)",
    )

    # The audit trail: the raw waveforms exactly as they came off the
    # device, both captures of each key, and the comparison's verdict. Read
    # by no part of the pipeline; it exists so the analysis can be redone
    # tomorrow.
    (target / "capturas.json").write_text(
        json.dumps(
            {
                "generado": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "gap_threshold_us": GAP_THRESHOLD_US,
                "tolerancia_rel": TOLERANCIA_REL,
                "tolerancia_abs_us": TOLERANCIA_ABS_US,
                "commands": commands,
                "detail": armado["detail"],
            },
            ensure_ascii=False,
            indent=1,
        )
    )

    return {
        "ok": True,
        "target": str(target),
        "json": str(target / library.CONFIG_NAME),
        "name": device_name,
        "device": device_name,
        "fabricante": fabricante,
        "modelo": modelo,
        "commands": len(commands),
        "limpios": armado["limpios"],
        "crudos": armado["crudos"],
        "protocolos": armado["protocolos"],
        "detail": armado["detail"],
        "validacion": val,
        "raw_warning": (
            "%d command(s) got saved with the RAW waveform (the protocol "
            "was not recognized): they get grabbed exactly as captured, "
            "with whatever noise they have. Test them one by one." % armado["crudos"]
            if armado["crudos"]
            else None
        ),
    }
