#!/usr/bin/env python3
"""Pull Logitech's CLIENT_ID out of YOUR own copy of their Android APK.

    python3 config_work/extract_client_id.py <your.apk>          # pulls it and saves it
    python3 config_work/extract_client_id.py <your.apk> --show   # only prints it

## Why this exists instead of shipping the number

Logging in to a Logitech account needs a `client_id`, and that identifier is
Logitech's: it lives in `R.string.logitech_app_id` inside their Android app.
It is their credential, not this project's, so it **is not redistributed**.

The tool is. Same call as with libconcord: ship the patch and the
instructions, not somebody else's binary. You get the APK wherever you already
have it, and your number comes out of your copy.

## What this is not

It does NOT decompile, does NOT modify the APK and does NOT defeat any
protection: it opens a ZIP and reads one string out of the resource table,
which is plain text. No APK, nothing to pull.

## How it finds it, and why it does not guess

`resources.arsc` is the compiled resource table. Its first block is a
`ResStringPool`: every string in the package, one after another. In 5.7.17
that is 12,077 of them.

Of those 12,077, exactly **ONE** is a UUID and nothing else. The other 24 that
look like UUIDs are file names (`a05ee5ab-...png,17`) and they do not match
because the pattern is anchored to the WHOLE string. Measured, not assumed: on
5.7.17-132 the result is unique.

If a future version had more than one candidate, this lists them and **does
not choose**: better no number than the wrong one.

## Where it goes

It writes it to `account.json` at the project root, without being asked:

    {"client_id": "..."}

That file is in `.gitignore`. It can also come from the environment as
`RE_HARMONY_CLIENT_ID`, which wins over the file.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import struct
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ACCOUNT = ROOT / "account.json"

#: Anchored to the WHOLE string. That is what separates the `client_id`
#: from the 24 image names that are also UUID-shaped.
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-z]{12}$")


def pool_strings(data: bytes, off: int = 12) -> list[str]:
    """The strings of an Android `ResStringPool`.

    The format is documented in AOSP's `ResourceTypes.h`: header, offset
    table, then the strings, in UTF-8 or UTF-16 depending on one bit of
    `flags`. Lengths come in one or two units depending on the high bit, which
    is the only fiddly part.
    """
    kind, header, _size = struct.unpack_from("<HHI", data, off)
    if kind != 0x0001:
        raise ValueError("en el offset %d no hay un ResStringPool" % off)
    n, _styles, flags, start, _ = struct.unpack_from("<IIIII", data, off + 8)
    utf8 = bool(flags & (1 << 8))
    offsets = struct.unpack_from("<%dI" % n, data, off + header)
    base = off + start
    out: list[str] = []
    for o in offsets:
        p = base + o
        if utf8:
            len_u16 = data[p]
            p += 1
            if len_u16 & 0x80:
                p += 1
            length = data[p]
            p += 1
            if length & 0x80:
                length = ((length & 0x7F) << 8) | data[p]
                p += 1
            out.append(data[p : p + length].decode("utf-8", "replace"))
        else:
            length = struct.unpack_from("<H", data, p)[0]
            p += 2
            if length & 0x8000:
                length = ((length & 0x7FFF) << 16) | struct.unpack_from("<H", data, p)[0]
                p += 2
            out.append(data[p : p + length * 2].decode("utf-16-le", "replace"))
    return out


def candidates(apk: pathlib.Path) -> tuple[list[str], int]:
    """`(possible client_ids, how many strings were looked at)`."""
    with zipfile.ZipFile(apk) as z:
        if "resources.arsc" not in z.namelist():
            raise ValueError("this is not an APK: no resources.arsc inside")
        arsc = z.read("resources.arsc")
    strings = pool_strings(arsc)
    return sorted({c for c in strings if UUID.match(c)}), len(strings)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("apk", help="your own copy of the Logitech Harmony APK")
    # SAVING IS THE DEFAULT, and that is the point: reading the number out and
    # then asking you to paste it somewhere is two steps where one will do, and
    # the second one is where people mistype. `--show` is for when you only
    # want to look.
    ap.add_argument(
        "--show",
        action="store_true",
        help="only print it; do not touch account.json",
    )
    a = ap.parse_args()

    apk = pathlib.Path(a.apk).expanduser()
    if not apk.is_file():
        print("does not exist: %s" % apk, file=sys.stderr)
        return 1

    try:
        found, total = candidates(apk)
    except (ValueError, zipfile.BadZipFile, struct.error) as exc:
        print("could not read the APK: %s" % exc, file=sys.stderr)
        return 1

    print("%s: %d strings in the resource table" % (apk.name, total))
    if not found:
        print(
            "\nNo client_id found. This may be an APK version that stores it "
            "some other way.",
            file=sys.stderr,
        )
        return 1
    if len(found) > 1:
        print(
            "\nThere are %d candidates and I will not pick for you:\n  %s\n"
            "Try them with `python3 config_work/myharmony.py --try <id>`."
            % (len(found), "\n  ".join(found)),
            file=sys.stderr,
        )
        return 1

    cid = found[0]
    print("\nclient_id: %s" % cid)
    if a.show:
        return 0

    data = {}
    if ACCOUNT.is_file():
        try:
            data = json.loads(ACCOUNT.read_text())
        except json.JSONDecodeError:
            pass
    if data.get("client_id") == cid:
        print("already in %s, unchanged" % ACCOUNT.name)
        return 0
    previous = data.get("client_id")
    data["client_id"] = cid
    ACCOUNT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    if previous:
        print("replaced in %s (was %s)" % (ACCOUNT.name, previous))
    else:
        print("saved to %s" % ACCOUNT)
    print("The app reads it from there. Nothing else to do.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
