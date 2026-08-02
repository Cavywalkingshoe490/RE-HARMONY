#!/usr/bin/env python3
"""Learns an IR code with the Harmony One's receiver: point the device's
**original** remote at it, press the key, and the remote returns the
captured waveform. **This does NOT write flash** -- `learn_from_remote()`
is read-only, it speaks plain HID (it doesn't touch any memory region of
the remote itself).

## Where this comes from (RE verified, not assumed)

Source: `concordance-main/libconcord/` (paths relative to that folder).

- **Exact signature** of the public function, as exposed by the official
  binding (`bindings/python/libconcord.py:846-864`) and declared by the
  public header (`libconcord.h:495-498`):

      int learn_from_remote(uint32_t *carrier_clock, uint32_t **ir_signal,
                            uint32_t *ir_signal_length,
                            lc_callback cb, void *cb_arg);
      void delete_ir_signal(uint32_t *ir_signal);

  `carrier_clock` is a plain OUTPUT (not an array) despite the name --
  `_out('carrier_clock', c_uint)` in the binding. `ir_signal` is
  `uint32_t **`: the `.dylib` allocates the array with `new[]` and the
  caller **must** free it with `delete_ir_signal()` (comment in
  `libconcord.cpp:1774-1780`).

- **Format of the returned waveform** (`libconcord.h:437-458`):
    - `carrier_clock`: carrier frequency in Hz, typically ~36000-40000.
    - `ir_signal`: **alternating** mark/space durations **in microseconds**
      (`uint32_t`, not the `u16` with bit15 that the Harmony One's own blob
      uses -- these are two different formats, see the note below).
    - `ir_signal` **starts with a mark and ends with a space**, so
      `ir_signal_length` is always even.
  This is assembled by `_handle_ir_response()` + `LearnIRInnerLoop()`
  (`remote.cpp:863-1017`): the frequency is computed from the cycle count
  of the first burst (`freq = cycles * 1e6 / t_on`), and each subsequent
  HID word is translated into a (space, mark) pair in microseconds until
  the receiver goes quiet.

- **How the user is told to press the key**: there is NO message or prompt
  coming out of the `.dylib` -- it is purely a timer. `CRemote::LearnIR()`
  (`remote.cpp:1017-1040`, the class that instantiates the Harmony One:
  see below) sends `COMMAND_START_IRCAP` (0x70, a single 64-byte HID
  report) and then blocks reading HID reports inside `LearnIRInnerLoop()`.
  It is the UI (this app) that has to show "point the original remote and
  press the key" BEFORE calling `learn_from_remote()`, because the call is
  synchronous and blocking.

- **Timeouts** -- hardcoded in `remote.h:44-47`, compiled into the already
  built `.dylib`. **There is no timeout parameter in the signature of
  `learn_from_remote()`**; they cannot be changed from ctypes:

      IR_LEARN_START_TIMEOUT   5000 ms  -- wait before the 1st IR word
                                           (time to aim and press)
      IR_LEARN_DONE_TIMEOUT     500 ms  -- silence that closes the capture
      MAX_IR_SIGNAL_DURATION   5000 ms  -- max total waveform duration
      MAX_IR_SIGNAL_LENGTH     1000     -- max marks+spaces (u32)

  `LC_ERROR_IR_OVERFLOW` (17) comes out **ONLY from duration**, never from
  length (`remote.cpp:997-999`): the comparison is `*ir_signal_length >
  MAX_IR_SIGNAL_LENGTH`, strict, and `_handle_ir_response()` bounds EVERY
  write with `if (ir_count < MAX_IR_SIGNAL_LENGTH)` (`remote.cpp:900` and
  `:904`), so the length never goes past 1000 and that half of the
  condition is **dead code**. Measurable consequence: a waveform longer
  than 1000 durations gets **silently truncated** and `learn_from_remote()`
  still returns 0 (success). That's why `capture_key()` returns
  `truncada=True` when `n >= MAX_IR_SIGNAL_LENGTH` -- the only warning
  there will be. If nothing arrives within `IR_LEARN_START_TIMEOUT`, it
  returns `LC_ERROR_READ` (3).

- **`err != 0` does NOT mean "there is no waveform".** `CRemote::LearnIR()`
  (`remote.cpp:1041-1047`) ALWAYS runs the `RESPONSE_DONE` flush after the
  capture, and if that handshake doesn't arrive within 500 ms it **stomps
  `err` with `LC_ERROR_READ` even though the whole waveform was already
  captured**. Also `LearnIRInnerLoop()` allocates the buffer
  (`remote.cpp:959`, `new uint32_t[MAX_IR_SIGNAL_LENGTH]`) BEFORE anything
  can fail, so in the most common failure (nobody presses the key ->
  `LC_ERROR_READ`) the output pointer is **still valid and must be freed**.
  `capture_key()` ALWAYS frees it and returns the waveform marked
  `partial=True` instead of discarding it.

- **If the Harmony One (arch 12) supports it: the libconcord path REACHES
  the device; whether the firmware answers is [ASSUMED] until the first
  live test.** `init_concord()` decides which `CRemoteBase` subclass to
  instantiate based on the USB PID (`libconcord.cpp:707-756`):
    - PIDs from `is_mh_pid()` (0xC124/0xC125/0xC126/0xC129/0xC12B --
      Harmony 300/200/Link/Hub/Touch-Ultimate) -> `CRemoteMH`.
    - Z-Wave PIDs -> `CRemoteZ_HID`.
    - **any other HID PID falls into the `else`, which instantiates
      `CRemote`** (`libconcord.cpp:744-755`) -- and the comment right in
      that `else` literally says: *"Seems to be required for the Harmony
      One"*, confirming the Harmony One goes through that branch. `CRemote`
      **does** implement `LearnIR()` (`remote.h:266-268`, body in
      `remote.cpp:1017-1043`): it sends `COMMAND_START_IRCAP`, calls
      `LearnIRInnerLoop()` (shared with `CRemoteMH`), and closes with
      `COMMAND_STOP_IRCAP`. There is no `arch == 12` check that excludes it
      anywhere along the path. This device's real PID is **046D:C121**,
      which is not in `is_mh_pid()` (only C124/C125/C126/C129/C12B),
      doesn't fall in the Z-Wave range, and isn't 0xC11F -> it enters the
      `else`. [VERIFIED by reading the code that libconcord ATTEMPTS this
      path; whether the firmware answers 0x70/0x90 is [ASSUMED] -- not run
      against the device.]

- **KNOWN RISK in carrier calibration, flagged BEFORE the first test so it
  doesn't get misdiagnosed.** The first 3 words of the stream are
  calibration. libconcord **discards word 0** (`case 0: // ???`,
  `remote.cpp:919`), takes `t_on = word1` raw (`:922`) and computes
  `freq = word2 * 1e6 / t_on` (`:926-928`). The MANUFACTURER's OWN decoder
  does something else: `CarrierProcessor.processCalibrationData`
  (`config_work/viejo_app/learnir_decompiled/.../LearnIrHidServiceSender.java`)
  stores word0 in `m_LastPulseOnTime`, word1 in `m_FirstPulseTime`, and
  computes `envelopeTime = word1 - word0`, `numClocks - 1`,
  `freq = 1e6 / (envelopeTime / (numClocks-1))`. In other words: if word 0
  is not zero, libconcord returns a shifted `carrier_clock` **and the
  waveform's first mark inflated by word0** (libconcord writes `t_on` =
  word1 as the first atom, `remote.cpp:931`). If in the first real capture
  the carrier doesn't land in ~36-40 kHz, or the first mark doesn't match
  the protocol's header but the SECOND frame does, this is the cause.
  Word 0 is **not accessible** through `learn_from_remote()`: fixing it
  requires patching libconcord or talking to the HID directly. In the
  meantime, `app/learn_ir.py` recognizes the protocol by falling back
  to the second frame, which comes through the data path (which DOES
  agree between the two sources).

- **DO NOT run this inside the UI process.** `_handle_ir_response()` takes
  `len = rsp[63]` (up to 255) and indexes `rsp[u]`/`rsp[1+u]` over a 68 B
  stack buffer (`remote.cpp:864-871`): an unusual HID report produces an
  out-of-range read and can crash the process. That's why `app/api.py`
  invokes this script as a **subprocess**.

- **`encode_for_posting()` / `post_new_code()` exist but are NOT declared
  in this module**: they convert the waveform to Logitech's posting format
  and send it over HTTP to the members.harmonyremote.com service
  (`libconcord.cpp:1817-1839`, via `Post(...)`), which is shut down. This
  task is to learn the waveform LOCALLY; converting it to the Harmony
  One's own blob format (seen elsewhere in this project: `u16` LE,
  bit15=mark, microseconds) is a separate step, not yet written here.
  [PENDING -- not included in this module.]

## Pattern followed

Same pattern as `write.py::cargar()`: **only** the symbols this read-only
flow needs are declared by hand via ctypes. Nothing from
`update_configuration`, `erase_*`, `write_firmware_to_remote`, or any
other destructive primitive -- they aren't even imported.

## FORBIDDEN / usage

This script **is not run against the device** as part of this task. The
check that was run was: (a) that the module imports without touching the
device, and (b) that the symbols used exist in the already-built `.dylib`
(`nm -gU libconcord.6.dylib`). `init_concord()`/`learn_from_remote()` were
never called against the real remote.

Intended usage (when the user runs it):
    python3 learn.py --tecla POWER --json captura_power.json
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import pathlib
import platform
import sys

_DEV_LIB_PATH = (
    "libconcord.6.dylib"  # installed system-wide: the OS looks it up
)
_LIB_NAME_BY_OS = {
    "Darwin": "libconcord.6.dylib",
    "Linux": "libconcord.so.6",
    "Windows": "libconcord.dll",
}


def _default_lib() -> str:
    """Resolves the libconcord path. Same logic as `write.py`, duplicated
    on purpose (`write.py` is not imported: every script in this folder
    is meant to run standalone, and duplicating 15 lines is safer than
    coupling a flash-writing module to an IR-reading one)."""
    from_env = os.environ.get("RE_HARMONY_LIBCONCORD")
    if from_env:
        return from_env

    name = _LIB_NAME_BY_OS.get(platform.system(), _LIB_NAME_BY_OS["Darwin"])

    next_to_script = pathlib.Path(__file__).resolve().parent / name
    if next_to_script.exists():
        return str(next_to_script)

    next_to_executable = pathlib.Path(sys.executable).resolve().parent / name
    if next_to_executable.exists():
        return str(next_to_executable)

    return _DEV_LIB_PATH


LIB = _default_lib()

# void (*lc_callback)(uint32_t stage, uint32_t done, uint32_t total,
#     uint32_t count, uint32_t counter_type, void *cb_arg,
#     const uint32_t *extra);
CB = ctypes.CFUNCTYPE(
    None,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_uint32,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32),
)

# libconcord.h / libconcord.py: error codes relevant to this flow.
LC_ERROR = 1
LC_ERROR_READ = 3
LC_ERROR_WRITE = 4
LC_ERROR_CONNECT = 11
LC_ERROR_IR_OVERFLOW = 17

# libconcord.py:96 -- stage that learn_from_remote() reports to the callback.
LC_CB_STAGE_LEARN = 21

# remote.h:44-47 -- informational: hardcoded in the .dylib, not configurable
# from ctypes. Listed here only so the UI knows how long to wait.
IR_LEARN_START_TIMEOUT_MS = 5000
IR_LEARN_DONE_TIMEOUT_MS = 500
MAX_IR_SIGNAL_DURATION_MS = 5000
MAX_IR_SIGNAL_LENGTH = 1000

# Prefix of the JSON line that `--json-stdout` prints. It exists so the
# reader (`app/learn_ir.py`) doesn't have to guess which stdout line is
# the machine's and which is meant for a human.
#
# NOTE ON NAMING: kept as the string "APRENDER_JSON:" because
# `app/learn_ir.py` declares its own independent copy of this same
# literal (not imported from here) and matches on it -- renaming the
# *value* would break that subprocess contract without a synchronized
# edit over there. The Python identifier itself is local to this module
# and safe to rename.
JSON_LINE_PREFIX = "APRENDER_JSON:"


def load_library():
    """Declares ONLY the ctypes symbols needed to learn an IR code through
    the remote's receiver. Deliberately without any flash write/erase
    primitive."""
    try:
        lib = ctypes.CDLL(LIB)
    except OSError as exc:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import write

        raise write.LibconcordAusente(
            "%s\n\n(%s)" % (write.TEXT_WITHOUT_LIBCONCORD, exc)
        ) from exc

    lib.init_concord.restype = ctypes.c_int
    lib.deinit_concord.restype = ctypes.c_int

    lib.get_identity.argtypes = [CB, ctypes.c_void_p]
    lib.get_identity.restype = ctypes.c_int

    for f in ("get_arch", "get_skin", "get_fw_ver_maj", "get_fw_ver_min"):
        getattr(lib, f).restype = ctypes.c_int

    # int learn_from_remote(uint32_t *carrier_clock, uint32_t **ir_signal,
    #     uint32_t *ir_signal_length, lc_callback cb, void *cb_arg);
    lib.learn_from_remote.argtypes = [
        ctypes.POINTER(ctypes.c_uint32),  # carrier_clock  (out, plain)
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint32)),  # ir_signal (out, array)
        ctypes.POINTER(ctypes.c_uint32),  # ir_signal_length (out, plain)
        CB,
        ctypes.c_void_p,
    ]
    lib.learn_from_remote.restype = ctypes.c_int

    # void delete_ir_signal(uint32_t *ir_signal);
    lib.delete_ir_signal.argtypes = [ctypes.POINTER(ctypes.c_uint32)]
    lib.delete_ir_signal.restype = None

    lib.lc_strerror.argtypes = [ctypes.c_int]
    lib.lc_strerror.restype = ctypes.c_char_p

    return lib


def capture_key(lib, cb=None) -> dict:
    """Calls `learn_from_remote()` once and returns the captured waveform
    as a Python dict, with the C++-side memory already freed.

    Blocks up to `IR_LEARN_START_TIMEOUT_MS` waiting for the first IR word
    and up to `IR_LEARN_DONE_TIMEOUT_MS` of silence to close. The caller is
    responsible for showing the user "aim and press" BEFORE calling this
    function -- the .dylib doesn't emit any prompt.

    Returns:
        {"carrier_clock_hz": int,
         "ir_signal_us": [int, ...],   # alternates mark, space, ...; starts
                                        # with a mark, ends with a space (even)
         "ir_signal_length": int,
         "err": int,        # the code learn_from_remote() returned
         "err_texto": str,  # lc_strerror(), "" if err == 0
         "parcial": bool,   # there was an err BUT a waveform still came in -- see below
         "truncada": bool}  # n >= MAX_IR_SIGNAL_LENGTH: the waveform was cut off

    NOTE ON NAMING: the four keys above (`err_text`, `partial`,
    `truncada`, and `key`/`ok`/`error` in `main()`'s output) cross a
    subprocess JSON boundary that `app/learn_ir.py` reads by name (see
    `capturar()` there) -- left in Spanish deliberately, same reasoning as
    `JSON_LINE_PREFIX` above.

    Only raises RuntimeError when there was an error AND **no waveform
    came in at all**. If a waveform came in despite the error it is still
    returned, with `partial=True`: the `RESPONSE_DONE` flush in
    `CRemote::LearnIR()` (`remote.cpp:1041-1047`) stomps `err` with
    `LC_ERROR_READ` even with the whole capture in hand, and discarding it
    there is what would lead to the false conclusion "the One can't learn
    IR".

    The C++-side memory is ALWAYS freed (`finally`): `LearnIRInnerLoop()`
    allocates the buffer (`remote.cpp:959`) before anything can fail, so
    the pointer is valid even when `err != 0`.
    """
    carrier_clock = ctypes.c_uint32(0)
    ir_signal_ptr = ctypes.POINTER(ctypes.c_uint32)()
    ir_signal_length = ctypes.c_uint32(0)

    err = lib.learn_from_remote(
        ctypes.byref(carrier_clock),
        ctypes.byref(ir_signal_ptr),
        ctypes.byref(ir_signal_length),
        cb or CB(),
        None,
    )

    # Read and free BEFORE deciding anything: the buffer exists no matter what.
    try:
        n = ir_signal_length.value
        data = list(ir_signal_ptr[:n]) if (ir_signal_ptr and n) else []
    finally:
        if ir_signal_ptr:
            lib.delete_ir_signal(ir_signal_ptr)

    text = ""
    if err:
        try:
            text = lib.lc_strerror(err).decode(errors="replace")
        except Exception:  # noqa: BLE001
            text = "(no text)"
        if not data:
            raise RuntimeError("learn_from_remote failed (%d): %s" % (err, text))

    return {
        "carrier_clock_hz": carrier_clock.value,
        "ir_signal_us": data,
        "ir_signal_length": n,
        "err": int(err),
        "err_text": text,
        "partial": bool(err),
        "truncada": n >= MAX_IR_SIGNAL_LENGTH,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--key",
        default=None,
        help="name of the key being learned (label only, not posted anywhere)",
    )
    ap.add_argument(
        "--json",
        metavar="FILE",
        default=None,
        help="if given, writes the capture as JSON to this file",
    )
    ap.add_argument(
        "--json-stdout",
        action="store_true",
        help="also prints a '%s<json>' line to stdout -- this is what "
        "`app/learn_ir.py` reads when it runs this script as a "
        "subprocess (the UI doesn't import libconcord: see the "
        "docstring, remote.cpp:864-871)" % JSON_LINE_PREFIX,
    )
    a = ap.parse_args()

    lib = load_library()
    if lib.init_concord():
        print("remote not found", file=sys.stderr)
        if a.json_stdout:
            print(
                JSON_LINE_PREFIX
                + json.dumps({"ok": False, "error": "remote not found"})
            )
        return 1
    try:
        cb_ident = CB(lambda *r: None)
        if lib.get_identity(cb_ident, None):
            print("could not identify the remote", file=sys.stderr)
            return 1

        arch = lib.get_arch()
        print(
            "remote: arch %d, skin %d, firmware %d.%d"
            % (arch, lib.get_skin(), lib.get_fw_ver_maj(), lib.get_fw_ver_min())
        )
        if arch != 12:
            print(
                "warning: arch %d != 12 -- this path is only verified "
                "(by reading the code) for the Harmony One" % arch,
                file=sys.stderr,
            )

        label = a.key or "(unnamed)"
        print(
            "\npoint the device's ORIGINAL remote at the Harmony One's IR "
            "receiver and press the '%s' key now." % label
        )
        print(
            "  waiting up to %.1fs for the first signal, then up to "
            "%.1fs of silence to close the capture..."
            % (IR_LEARN_START_TIMEOUT_MS / 1000, IR_LEARN_DONE_TIMEOUT_MS / 1000)
        )

        capture = capture_key(lib)
        print(
            "\ncaptured: %d Hz, %d marks/spaces (%d bytes of data)"
            % (
                capture["carrier_clock_hz"],
                capture["ir_signal_length"],
                capture["ir_signal_length"] * 4,
            )
        )
        if capture["partial"]:
            print(
                "WARNING: learn_from_remote returned %d (%s) but a waveform "
                "still came in. Most likely the closing handshake didn't "
                "arrive within 500 ms (remote.cpp:1041-1047); the waveform "
                "is kept." % (capture["err"], capture["err_text"]),
                file=sys.stderr,
            )
        if capture["truncada"]:
            print(
                "WARNING: waveform TRUNCATED to %d durations (libconcord's "
                "cap). Press the key ONCE and briefly, don't hold it."
                % MAX_IR_SIGNAL_LENGTH,
                file=sys.stderr,
            )
        print(capture["ir_signal_us"])

        output = {"ok": True, "key": a.key, **capture}
        if a.json:
            pathlib.Path(a.json).write_text(json.dumps(output, indent=2))
            print("\nwritten: %s" % a.json)
        if a.json_stdout:
            print(JSON_LINE_PREFIX + json.dumps(output))

        return 0
    except RuntimeError as e:
        print("\nerror: %s" % e, file=sys.stderr)
        if a.json_stdout:
            print(JSON_LINE_PREFIX + json.dumps({"ok": False, "error": str(e)}))
        return 1
    finally:
        try:
            lib.deinit_concord()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
