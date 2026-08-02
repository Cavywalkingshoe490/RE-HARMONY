#!/usr/bin/env python3
"""LOAD-BEARING CHECK for `app/generate.py`: the only one that proves the app
does the same thing that is grabbed and running on the remote today.

Regenerates, in a temporary directory, the full chain that is today in
`output/config_empaquetada.bin`:

    backups/config_raw.bin
      -> add_device.py --indice 3 --nombre Philips   (via generar.generate())
      -> add_device.py --indice 4 --nombre LG        (via generar.generate())

and -- when this tree HAS that anchor, see below -- requires the final `.bin`
to have md5 `976bc70edd15b40f56cb49aa5113594f`, the one that is already
grabbed and verified on the device. If it doesn't match, this script says so
as-is, with the md5 that came out, without dressing anything up.

Afterwards it runs the negative check of `preview_gate()`: with
the two declared `--repoint` it has to give `ok=True`; removing either of the
two, `ok=False`.

Never touches USB at any point: `add_device.py` runs by subprocess (it does
not import libconcord) and `preview_gate()` only calls
`grabar.nothing_moved()`.

## Without the anchor it SKIPS, it does not fail

The anchor is four files, and every one of them is a dump of somebody's own
remote or an export of their own account: the base blob
(`backups/config_raw.bin`), the two device configs that get chained onto it,
and the resulting `.bin` whose md5 is `ANCLA_MD5`. None of the four is
redistributable, so a tree that does not have all four does not have THIS
anchor -- and requiring a particular md5 that a tree cannot possibly produce
turns a load-bearing check into a check that fails forever, which is worse
than no check.

So the md5 is a requirement only WHEN the anchor is present, exactly the way
`app/api.py` and `app/library.py` already treat it (`ANCLA_BIN.exists()
and _md5(ANCLA_BIN) == ANCLA_MD5`). When it is not, this prints what is
missing and exits `2` -- SKIPPED, the same contract
`check_keys_reach.py` and `check_keys_auto.py` use. Nothing is
weakened where the anchor exists: there the md5 is still hard-required and
still the thing that gets reverted, not patched around.

To anchor a different tree: put your own dump in `backups/config_raw.bin`,
your two device configs in the `FIXTURES` folders, and set `ANCLA_MD5` (and
`ANCLA_BIN`) to the chain you have already written and verified on YOUR
remote.

Usage:
    python3 app/check_load_bearing.py
    # rc 0 = passed, 1 = failed, 2 = skipped (no anchor in this tree)
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BACKUPS = ROOT / "backups"
OUTPUT = ROOT / "account_export" / "output"

#: The base blob the chain starts from: a dump of a real remote, read only.
BASE = BACKUPS / "config_raw.bin"

#: The anchor. `ANCLA_BIN` is NOT read to do the check -- the chain is
#: regenerated from `BASE` + the fixtures and compared against `ANCLA_MD5` --
#: it is what answers the other question: *does this tree have the anchor at
#: all?* Same two names and same test as `app/api.py` and
#: `app/library.py`, and the md5 is part of the test on purpose: a file
#: with this name can perfectly well exist and be somebody else's chain.
ANCLA_BIN = ROOT / "output" / "config_empaquetada.bin"
ANCLA_MD5 = "976bc70edd15b40f56cb49aa5113594f"

REPOINTS = [0x20, 0x24]  # MASTER_S5 and MASTER_T6, measured in add_device.py


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


#: The two device files that, chained, reproduce `ANCLA_MD5`. They are the
#: anchor's FIXTURE, not the user's devices, and they are looked up in three
#: places because the user deleted his downloaded devices on purpose (with
#: the delete button, which works) and this check went red with a
#: FileNotFoundError -- a regression anchor that only runs while a particular
#: device happens to be downloaded is not an anchor.
#:
#: NOTHING IS RESTORED. The fallbacks are read-only copies that already exist
#: inside the repo (the ones that shipped in the built .app) and in the
#: read-only backup; reading them puts no device back in the user's list.
#: Both copies are byte-identical -- measured, md5 6d54e0b4... (Philips) and
#: d4dbd216... (LG).
FIXTURE_DIRS = [
    ROOT / "account_export" / "output",
    ROOT
    / "app"
    / "packaging"
    / "dist"
    / "RE-HARMONY.app"
    / "Contents"
    / "account_export"
    / "output",
    Path("/tmp/output_backup"),
]

#: The two fixture folders of THIS anchor, in chain order. They are named
#: here and not inline so that pointing the check at another pair of devices
#: is one edit and not a hunt through `main()`.
FIXTURES = ("hub-config-tv-a", "hub-config-tv-b")


def _fixture(folder: str, avisar: bool = True) -> Path:
    """The anchor's config file for `folder`, from the first source that has
    it. Returns the LIVE path when nothing has it, so the error message names
    the place the user would expect.

    `avisar=False` is for the availability probe: it asks the same question
    and must not print the same line twice."""
    for base in FIXTURE_DIRS:
        cand = base / folder / "hub-config-with-device.json"
        if cand.is_file():
            if avisar and base is not FIXTURE_DIRS[0]:
                print("   (fixture %s read from %s)" % (folder, base))
            return cand
    return FIXTURE_DIRS[0] / folder / "hub-config-with-device.json"


def _missing_anchor_pieces() -> list[str]:
    """Everything this anchor needs and this tree does not have, named one by
    one. Empty list = the anchor is here and the md5 is required.

    It is a list and not a bool so the skip message says WHICH piece is
    missing: "no anchor" with no name is the kind of message that sends the
    next person to read the source to find out what to put where."""
    missing: list[str] = []

    if not BASE.is_file():
        missing.append("missing, the base blob: %s" % BASE)

    where = ", ".join(str(b) for b in FIXTURE_DIRS)
    for folder in FIXTURES:
        if not _fixture(folder, avisar=False).is_file():
            missing.append(
                "missing, the device config %s/hub-config-with-device.json "
                "(looked up in: %s)" % (folder, where)
            )

    if not ANCLA_BIN.is_file():
        missing.append(
            "missing, the reference blob: %s (md5 %s)" % (ANCLA_BIN, ANCLA_MD5)
        )
    else:
        suyo = _md5(ANCLA_BIN)
        if suyo != ANCLA_MD5:
            missing.append(
                "not this anchor: %s exists, but its md5 is %s and the "
                "anchor's is %s" % (ANCLA_BIN, suyo, ANCLA_MD5)
            )

    return missing


def _show_stdout_on_failure(result: dict, step: str) -> None:
    if not result["ok"]:
        print(f"\n--- {step}: FAILED (stage={result['etapa']}) ---")
        print("command:", result["command"])
        print("returncode:", result["returncode"])
        if result["stdout"]:
            print("--- stdout ---")
            print(result["stdout"][-4000:])
        if result["stderr"]:
            print("--- stderr ---")
            print(result["stderr"][-4000:])


def main() -> int:
    missing = _missing_anchor_pieces()
    if missing:
        print("this tree does not have the anchor, so there is nothing to")
        print("check the chain against:")
        for f in missing:
            print("   - %s" % f)
        print(
            "\nSKIPPED (rc=2). The anchor is a dump of somebody's own remote "
            "plus two\ndevice configs exported from their own account -- none "
            "of that is\nredistributed, so a fresh clone never has it. To "
            "anchor this check on\nyour own remote: put your dump in %s, your "
            "two device configs in the\nFIXTURES folders, and set ANCLA_MD5 to "
            "the chain you already wrote and\nverified on it." % BASE
        )
        return 2

    failures = []

    with tempfile.TemporaryDirectory(prefix="harmony_control_portante_") as tmp:
        tmp = Path(tmp)
        config_raw = tmp / "config_raw.bin"
        shutil.copy(BASE, config_raw)

        philips_json = _fixture(FIXTURES[0])
        lg_json = _fixture(FIXTURES[1])
        philips_bin = tmp / "philips_empaquetado.bin"
        final_bin = tmp / "config_empaquetada.bin"

        print("== step 1/2: Philips (--indice 3) ==")
        r1 = generate.generate(
            config_raw,
            philips_json,
            index=3,
            name="Philips",
            salida=philips_bin,
        )
        _show_stdout_on_failure(r1, "Philips")
        print("ok=%s  returncode=%s" % (r1["ok"], r1["returncode"]))
        if not r1["ok"]:
            failures.append("Philips: generate() did not finish ok")
            print("\nLOAD-BEARING CHECK: COULD NOT REGENERATE THE CHAIN.")
            return 1

        print("\n== step 2/2: LG (--indice 4) ==")
        r2 = generate.generate(
            philips_bin,
            lg_json,
            index=4,
            name="LG",
            device="LG TV",
            salida=final_bin,
        )
        _show_stdout_on_failure(r2, "LG")
        print("ok=%s  returncode=%s" % (r2["ok"], r2["returncode"]))
        if not r2["ok"]:
            failures.append("LG: generate() did not finish ok")
            print("\nLOAD-BEARING CHECK: COULD NOT REGENERATE THE CHAIN.")
            return 1

        real_md5 = _md5(final_bin)
        print("\n== md5 ==")
        print("expected:  %s" % ANCLA_MD5)
        print("got:       %s" % real_md5)
        if real_md5 != ANCLA_MD5:
            failures.append(
                "MD5 DOES NOT MATCH: expected %s, got %s -- the app does NOT "
                "reproduce what is grabbed on the remote today" % (ANCLA_MD5, real_md5)
            )
        else:
            print(
                "md5 IDENTICAL -- the chain generated by the app reproduces, "
                "byte for byte, what is grabbed and verified on the device today."
            )

        # -- gate: positive -------------------------------------------------
        print("\n== gate -- preview_gate() ==")
        ref = config_raw.read_bytes()
        philips_b = philips_bin.read_bytes()
        final_b = final_bin.read_bytes()

        pre1 = generate.preview_gate(ref, philips_b, REPOINTS)
        print("Philips  repoints=%s -> %s" % (REPOINTS, pre1))
        if not pre1["ok"]:
            failures.append(
                "gate Philips with the 2 repoints gave ok=False (expected True)"
            )

        pre2 = generate.preview_gate(philips_b, final_b, REPOINTS)
        print("LG       repoints=%s -> %s" % (REPOINTS, pre2))
        if not pre2["ok"]:
            failures.append("gate LG with the 2 repoints gave ok=False (expected True)")

        # -- gate: NEGATIVE check ---------------------------------------------
        print("\n== NEGATIVE check -- removing a --repunta has to give ok=False ==")
        for remove in REPOINTS:
            partial = [p for p in REPOINTS if p != remove]
            neg = generate.preview_gate(ref, philips_b, partial)
            print(
                "Philips  repoints=%s (without %#04x) -> ok=%s  undeclared=%s"
                % (partial, remove, neg["ok"], neg["sin_declarar"])
            )
            if neg["ok"]:
                failures.append(
                    "NEGATIVE FAILED: removing --repunta %#04x still gave ok=True"
                    % remove
                )

        without_any = generate.preview_gate(ref, philips_b, [])
        print(
            "Philips  repoints=[] -> ok=%s  undeclared=%s"
            % (without_any["ok"], without_any["sin_declarar"])
        )
        if without_any["ok"]:
            failures.append(
                "NEGATIVE FAILED: declaring no repoint at all still gave ok=True"
            )

    print("\n" + "=" * 72)
    if failures:
        print("LOAD-BEARING CHECK: FAILED (%d problem(s)):" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1

    print("LOAD-BEARING CHECK: PASSED.")
    print("  - md5 of the regenerated chain == %s" % ANCLA_MD5)
    print("  - gate with the 2 repoints: ok=True, undeclared=[] (Philips and LG)")
    print("  - negative check: removing any --repunta (or both) gives ok=False")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
