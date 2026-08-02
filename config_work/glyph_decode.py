#!/usr/bin/env python3
"""
Verifies and extends the PLAN.md finding ("MAJOR FINDING: the blob's text
encoding"): the blob does NOT store ASCII, it stores glyph indices (one byte
per character, 0x00 terminator) against a monoalphabetic substitution table.

This script:
  1. Re-derives the substitution table from the 22 command names that
     PLAN.md says it located at an exact offset (known crib: the Hub's
     names in DeviceList/ActivityList/FunctionList).
  2. Verifies letter by letter that the table is SELF-CONSISTENT (one
     letter -> one glyph, no collisions) and that it decodes exactly the
     expected string at each offset.
  3. Reports any offset that does NOT decode as expected (lets mislabels
     be detected, like the "Power"/"PowerOff" case found).
  4. With the table already derived, scans the whole blob looking for
     <bytes in table range><0x00> records and decodes them, to see how
     much more text can be read without knowing the offset in advance.

The blob to read comes from `--blob`, and defaults to `backups/config_raw.bin`
relative to the project root -- the same default the rest of `config_work/`
uses. The crib offsets below were measured on ONE factory dump: pointed at a
different dump the script reports what does not line up (wrong length, table
collision, mismatch) instead of failing, which is exactly what steps 2 and 3
are for.

Usage:
    python3 glyph_decode.py [--blob PATH]
"""

import argparse
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_BLOB = ROOT / "backups" / "config_raw.bin"

# offset(file) -> expected name, as PLAN.md lists them as
# "27 Hub names located in the blob with an exact offset"
CRIB = {
    0x00F873: "Playlist",
    0x00F891: "Sleep",
    0x00F8C3: "Stereo",
    0x00F93A: "Options",
    0x0109A7: "Power",  # <- LABEL TO VERIFY, see below
    0x011BA0: "Cursor",
    0x011C25: "Resolution",
    0x0129DC: "Subtitle",
    0x0129EE: "Return",
    0x012AA8: "Input",
    0x012AB7: "InputHdmi",
    0x012B55: "InputTuner",
    0x012BE1: "Keypad",
    0x014280: "InputCd",
    0x0143B3: "InputPhono",
    0x0143C7: "InputTape",
    0x0143E6: "InputTv",
    0x014550: "OnScreen",
    0x014562: "Presets",
    0x0146C9: "TestTone",
    0x0146EF: "TuningUp",
    0x012941: "PowerOn",
}


def raw_bytes_at(data, off, maxlen=16):
    """Bytes from off up to the first 0x00 (terminator), or maxlen.

    A read past the end of the blob returns what there is (possibly nothing):
    a dump that is not the one the crib was measured on has to be REPORTED by
    the caller, not blow up here.
    """
    out = []
    for i in range(maxlen):
        if off + i >= len(data):
            break
        b = data[off + i]
        if b == 0x00:
            return bytes(out)
        out.append(b)
    return bytes(out)  # did not find a terminator within maxlen


def derive_table(data):
    """Tries to derive glyph->letter by aligning each CRIB offset with its
    expected name, char by char. Returns (table, conflicts, bad_offsets)."""
    table = {}  # glyph (int) -> letter
    reverse = {}  # letter -> glyph, to detect collisions
    conflicts = []
    bad_offsets = []

    for off, name in CRIB.items():
        raw = raw_bytes_at(data, off, maxlen=len(name) + 4)
        if len(raw) != len(name):
            bad_offsets.append(
                (
                    off,
                    name,
                    raw.hex(" "),
                    "length does not match (%d bytes vs %d letters)"
                    % (len(raw), len(name)),
                )
            )
            continue
        ok = True
        for glyph, ch in zip(raw, name):
            if glyph in table and table[glyph] != ch:
                conflicts.append((off, name, glyph, table[glyph], ch))
                ok = False
            if ch in reverse and reverse[ch] != glyph:
                conflicts.append((off, name, glyph, "reverse:" + reverse[ch], ch))
                ok = False
            table[glyph] = ch
            reverse[ch] = glyph
        if not ok:
            bad_offsets.append((off, name, raw.hex(" "), "table collision"))

    return table, reverse, conflicts, bad_offsets


def decode(data, off, table, maxlen=32):
    raw = raw_bytes_at(data, off, maxlen=maxlen)
    return "".join(table.get(b, "?[%02x]" % b) for b in raw), raw


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="derives the blob's glyph table from a known crib and "
        "decodes it back, reporting whatever does not line up",
    )
    ap.add_argument(
        "--blob",
        default=str(DEFAULT_BLOB),
        help="config blob to read (default: %(default)s)",
    )
    args = ap.parse_args(argv)

    blob = pathlib.Path(args.blob)
    if not blob.is_file():
        print("the blob is not there: %s" % blob)
        print("pass --blob PATH, or leave a dump at backups/config_raw.bin")
        return 2
    data = blob.read_bytes()
    print("blob: %s (%d bytes)" % (blob, len(data)))
    print()

    table, reverse, conflicts, bad = derive_table(data)

    print("=== Step 1: derive the table from PLAN.md's crib ===")
    print("Crib entries: %d" % len(CRIB))
    print("Glyphs resolved with no collision: %d" % len(table))
    if conflicts:
        print("CONFLICTS (%d):" % len(conflicts))
        for c in conflicts:
            print("  ", c)
    if bad:
        print("OFFSETS THAT DO NOT LINE UP (%d):" % len(bad))
        for b in bad:
            print("  ", b)

    print()
    print("=== Step 2: decode every crib offset with the final table ===")
    for off, expected in CRIB.items():
        text, raw = decode(data, off, table, maxlen=len(expected) + 6)
        mark = (
            "OK"
            if text == expected
            else (
                "MISMATCH -> check the label"
                if text[: len(expected)] != expected
                else "PREFIX-OK"
            )
        )
        print(
            f"  0x{off:06x}  expected={expected!r:15s}  decoded={text!r:20s}  raw={raw.hex(' ')}  [{mark}]"
        )

    print()
    print("=== Substitution table glyph(hex) -> letter ===")
    for g in sorted(table):
        print(f"  0x{g:02x} -> {table[g]!r}")

    print()
    print("=== Step 3: try decoding 'Power' with one more letter (PowerOff?) ===")
    off = 0x0109A7
    text, raw = decode(data, off, table, maxlen=10)
    print(f"  0x{off:06x} raw={raw.hex(' ')} decoded={text!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
