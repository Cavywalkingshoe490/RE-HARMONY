#!/usr/bin/env python3
"""FIRST RUN: read your own configuration and save it as the baseline.

## Why it is needed

Almost everything this project does starts from a reference configuration:
the blob the remote has today. The repo ships none -- **somebody else's
configuration is no use to you, and besides, it does not get published**.
Yours is inside your remote, and it can be read.

This is what this script does:

    1. finds libconcord (the library that talks to the remote over USB);
    2. asks the remote who it is;
    3. READS its configuration memory -- reads only, never writes;
    4. trims the dump down to the `PTYY` terminator (the rest is padding);
    5. saves it to `backups/config_raw.bin`, which is the name the rest of
       the project looks for, and also leaves a dated, untouched copy in
       `backups/linea_base_<fecha>.bin`.

That second copy is the safety net: it is the configuration your remote had
BEFORE this project touched anything. `--restaurar` wraps it back up in an
`.EZHex` so it can be written again.

## READ ONLY

The only libconcord primitives this script calls on the device are
`get_identity()` (identification) and `read_flash_at()` (a flash READ). It
does not call, nor declare, `update_configuration`, `erase_config`,
`erase_flash` or any `write_*` symbol. It can be run with the remote plugged
in with no risk of changing anything.

## Usage

    python3 first_run.py              # reads and saves the baseline
    python3 first_run.py --estado     # only says what is there, reads nothing
    python3 first_run.py --rehacer    # reads again even if one already exists
    python3 first_run.py --restaurar  # builds the baseline's .EZHex

## What is NOT verified

This script's live path needs a Harmony One plugged in. It leans entirely on
`config_work/read_flash_baseline.py`, which is the same module the Control
screen already uses for its Refresh button. **Neither that module nor this one
was exercised against hardware in the session that wrote them.** What IS
verified, offline: the trim down to `PTYY`, the rejection of an incoherent
dump, and that the `.EZHex` `--restaurar` builds is accepted by libconcord's
`read_and_parse_file()`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
CONFIG_WORK = RAIZ / "config_work"
BACKUPS = RAIZ / "backups"
BASE = 0x040000

TEXT_WITHOUT_LIBCONCORD = """
I cannot find libconcord.

It is the library that talks to the remote over USB. It does NOT come with this
project: it is free software from another project -- Concordance, GPLv3 -- and
it is built separately.

And the PATCHED one is what is needed. A packaged one is no good: `brew install
concordance` does not exist (there is no such formula), and apt/dnf's is
upstream's, which loads fine and then cannot read ANYTHING off this remote,
because arch 12 has firmware_base = 0 and every read lands at address 0.

    README.md   <- the instructions, with the patch

Short version:

    git clone https://github.com/jaymzh/concordance && cd concordance
    patch -p1 -i <ESTE_REPO>/tools/libconcord/libconcord-re-harmony.patch
    cd libconcord && autoreconf -fi && ./configure && make
    export RE_HARMONY_LIBCONCORD=$PWD/.libs/libconcord.6.dylib

(on macOS `./configure` needs CPPFLAGS/LDFLAGS pointing at Homebrew: the exact
line is in that README.)

Meanwhile the app works all the same: you can browse the library, import .ir
codes and prepare changes. The only thing I cannot do is read or write the
remote.
""".strip()


def _config_from_dump(crudo: Path) -> bytes | None:
    """The CONFIG inside a raw flash dump, or `None`.

    The dump is the whole flash window; the config is the prefix up to and
    including the `PTYY` terminator, and the rest is padding. Handing over
    the whole dump as if it were a config does not give a clear error: it
    goes past the GSPM format's limit and the tools stall without being able
    to explain why. Same trim `app/api.py` uses.

    It also checks that the size declared at +4 matches the cut, so as not to
    accept a run of bytes that happens to contain "PTYY".
    """
    try:
        raw = crudo.read_bytes()
    except OSError:
        return None
    if raw[:4] != b"GSPM":
        return None
    i = raw.find(b"PTYY")
    if i < 0:
        return None
    cfg = raw[: i + 4]
    declarado = int.from_bytes(raw[4:7], "little") - BASE
    return cfg if declarado == len(cfg) - 4 else None


def _last_json_line(text: str) -> dict | None:
    """The last line that parses as JSON. libconcord spits its own
    DEBUG onto stdout before the JSON."""
    last = None
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            last = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
    return last


def _libconcord_disponible() -> tuple[bool, str]:
    sys.path.insert(0, str(CONFIG_WORK))
    try:
        import write
    except Exception as exc:  # noqa: BLE001
        return False, "could not import config_work/write.py: %s" % exc
    path = getattr(write, "LIB", None)
    if not path:
        return False, TEXT_WITHOUT_LIBCONCORD
    return True, str(path)


def state() -> dict:
    """What is on disk today. Does not touch the device."""
    ref = BACKUPS / "config_raw.bin"
    bases = sorted(BACKUPS.glob("linea_base_*.bin"))
    has_lib, detail = _libconcord_disponible()
    return {
        "referencia": str(ref) if ref.is_file() else None,
        "referencia_bytes": ref.stat().st_size if ref.is_file() else 0,
        "baselines": [str(p) for p in bases],
        "libconcord": detail if has_lib else None,
        "libconcord_reason": None if has_lib else detail,
    }


def read_from_remote(*, rehacer: bool, timeout: float) -> int:
    ref = BACKUPS / "config_raw.bin"
    if ref.is_file() and not rehacer:
        print("There is already a baseline: %s (%d B)." % (ref, ref.stat().st_size))
        print("Si queres volver a leer el mando: --rehacer")
        return 0

    has_lib, detail = _libconcord_disponible()
    if not has_lib:
        print(detail, file=sys.stderr)
        return 2

    print("libconcord: %s" % detail)
    print("Reading your remote's configuration. READ ONLY -- nothing is written")
    print("to the device. It can take a few minutes; do not unplug it.")
    print()

    with tempfile.TemporaryDirectory(prefix="re-harmony_base_") as tmp:
        dump_path = Path(tmp) / "volcado.bin"
        argv = [
            sys.executable,
            str(CONFIG_WORK / "read_flash_baseline.py"),
            "--salida",
            str(dump_path),
            "--json",
        ]
        try:
            r = subprocess.run(  # noqa: S603 -- read only, no network
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(CONFIG_WORK),
                check=False,
            )
        except subprocess.TimeoutExpired:
            print(
                "The remote answered but reading its memory took more than %s s. "
                "Try again with a higher --timeout." % timeout,
                file=sys.stderr,
            )
            return 1
        except OSError as exc:
            print("could not run the reader: %s" % exc, file=sys.stderr)
            return 1

        info = _last_json_line(r.stdout)
        if info is None or not info.get("encontrado"):
            print(
                "The remote was not found over USB.\n"
                "  - enchufa el cable\n"
                "  - press any key to wake it up\n"
                "  - on Linux a udev rule may be needed "
                "(viene con concordance)\n",
                file=sys.stderr,
            )
            if (r.stderr or "").strip():
                print("detalle: %s" % r.stderr.strip(), file=sys.stderr)
            return 1

        print(
            "Encontrado: arch %s, skin %s, firmware %s.%s"
            % (
                info.get("arch"),
                info.get("skin"),
                info.get("fw_mayor"),
                info.get("fw_menor"),
            )
        )
        if not info.get("valido"):
            print(
                "\nThe remote answered, but what it returned is not a configuration that is "
                "coherente:\n  %s\n"
                "Nothing is saved: a baseline that does not validate is worse than "
                "none."
                % (info.get("reason") or "; ".join(info.get("problemas") or [])),
                file=sys.stderr,
            )
            return 1

        cfg = _config_from_dump(dump_path)
        if cfg is None:
            print(
                "The dump was read in full but it could not be trimmed down to the "
                "PTYY terminator. Nothing is saved.",
                file=sys.stderr,
            )
            return 1

        BACKUPS.mkdir(parents=True, exist_ok=True)
        marca = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
        copia = BACKUPS / ("linea_base_%s.bin" % marca)
        copia.write_bytes(cfg)
        ref.write_bytes(cfg)

    print()
    print("Listo.")
    print("  referencia de trabajo : %s  (%d B)" % (ref, len(cfg)))
    print("  copia intacta         : %s" % copia)
    print(
        "  dispositivos          : %s        pantallas: %s"
        % (info.get("n_dispositivos_actual"), info.get("n_pantallas_actual"))
    )
    if info.get("parece_de_fabrica") is False:
        print()
        print(
            "NOTICE: this configuration does NOT look like a factory one -- it was\n"
            "edited at some point. It still works as a baseline (it is what your remote\n"
            "has today), but do not read it as 'what came from the factory'."
        )
    print()
    print("To go back to this configuration at any time:")
    print("    python3 first_run.py --restaurar")
    return 0


def _relativo(p: Path) -> str:
    """`p` relative to the project root, or absolute if it falls outside."""
    try:
        return str(p.relative_to(RAIZ))
    except ValueError:
        return str(p)


def restaurar() -> int:
    """Builds the `.EZHex` of the oldest baseline there is. Does NOT write:
    it leaves the file and prints the command, because writing is the only
    irreversible operation in the whole project and it does not fire by itself."""
    bases = sorted(BACKUPS.glob("linea_base_*.bin"))
    if not bases:
        print(
            "There is no baseline saved. Run this first:\n"
            "    python3 first_run.py",
            file=sys.stderr,
        )
        return 1
    origin = bases[0]  # the oldest one: the one from before this project touched anything
    sys.path.insert(0, str(CONFIG_WORK))
    import ezhex_emitir

    datos = origin.read_bytes()
    salida = BACKUPS / (origin.stem + ".EZHex")
    salida.write_bytes(ezhex_emitir.envolver(datos))
    problemas = ezhex_emitir.verificar(salida.read_bytes())
    if problemas:
        print("the .EZHex that was built does not validate: %s" % "; ".join(problemas), file=sys.stderr)
        return 1
    print("Armado: %s (%d B)" % (salida, salida.stat().st_size))
    print()
    print("To write it to the remote (THIS ONE DOES WRITE):")
    # `--referencia` is NOT optional: without it `write.py` balks with
    # "NOT WRITING: --referencia with a known-good blob is missing" y devuelve
    # 1, because it has nothing to check "nothing moved" against. When
    # restoring, the reference is the baseline itself: the gate then proves
    # that the .EZHex carries that exact blob, byte for byte.
    #
    # It is printed relative to the project root, not absolute: it is a line
    # to copy and paste, and a 90-character path is unreadable.
    py = _relativo(Path(sys.executable)) if sys.executable else "python3"
    ez, bin_ = _relativo(salida), _relativo(origin)
    for extra in ("  --verificar-solo   # primero probar", ""):
        print(
            "    %s config_work/write.py %s --referencia %s%s" % (py, ez, bin_, extra)
        )
    print()
    print("(from %s)" % RAIZ)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--state", action="store_true", help="what is there today, without touching anything")
    ap.add_argument("--rehacer", action="store_true", help="volver a leer el mando")
    ap.add_argument(
        "--restaurar", action="store_true", help="build the baseline's .EZHex"
    )
    ap.add_argument("--timeout", type=float, default=600.0)
    a = ap.parse_args()

    if a.state:
        print(json.dumps(state(), ensure_ascii=False, indent=2))
        return 0
    if a.restaurar:
        return restaurar()
    return read_from_remote(rehacer=a.rehacer, timeout=a.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
