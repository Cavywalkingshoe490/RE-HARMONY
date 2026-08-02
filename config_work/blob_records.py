#!/usr/bin/env python3
"""Scan a Harmony One config blob for the 0xA7 named-record structure.

The arch-12 config blob is an opaque flash image that concordance never parses.
But it is not compressed or encrypted (measured entropy 4.4-7.2 bits/byte), and
it contains at least one region built from a regular record grammar discovered
by hand at offset 0x1c261:

    A7  len:u16le  type:u16le  id:u16le  name[len-4]

`len` counts the four header bytes after it plus the name, so the name length is
`len - 4`. Observed `type` values: 0 for container nodes (``Root``, ``State``)
and 1 for leaf entries (``TV_Power_2``, ``DVR_Power_2``...). The block
is terminated by the little-endian marker 0xBEEF.

The names match the devices the remote's own Hub configuration declares,
which makes that readable JSON a usable oracle for correlating this binary.
The device names used as examples throughout this file (``TV``, ``DVR``,
``Home``) are GENERIC: on any given remote they are whatever that user's
Hub called each device. Nothing here matches on them.

Usage:
    python3 blob_records.py scan  <blob.bin>          # every record found
    python3 blob_records.py regions <blob.bin>        # where records cluster
"""

from __future__ import annotations

import argparse
import pathlib
import re
import struct
from dataclasses import dataclass

MARKER = 0xA7
TERMINATOR = b"\xef\xbe"
NAME_RE = re.compile(rb"^[A-Za-z][A-Za-z0-9_.\- ]*$")
MAX_NAME = 128


@dataclass(frozen=True)
class Record:
    offset: int
    length: int
    type: int
    ident: int
    name: str


def parse_at(data: bytes, offset: int) -> Record | None:
    """Try to read one record at ``offset``; return None if it does not fit."""
    if offset + 7 > len(data) or data[offset] != MARKER:
        return None
    length, rtype, ident = struct.unpack_from("<HHH", data, offset + 1)
    name_len = length - 4
    if not 1 <= name_len <= MAX_NAME:
        return None
    start = offset + 7
    raw = data[start : start + name_len]
    if len(raw) != name_len or not NAME_RE.match(raw):
        return None
    return Record(offset, length, rtype, ident, raw.decode("ascii"))


def scan(data: bytes) -> list[Record]:
    """Find every plausible record in the blob."""
    out: list[Record] = []
    for offset in range(len(data) - 7):
        if data[offset] != MARKER:
            continue
        record = parse_at(data, offset)
        if record is not None:
            out.append(record)
    return out


def cluster(records: list[Record], gap: int = 512) -> list[list[Record]]:
    """Group records that sit close together into regions."""
    groups: list[list[Record]] = []
    for record in records:
        if groups and record.offset - groups[-1][-1].offset <= gap:
            groups[-1].append(record)
        else:
            groups.append([record])
    return groups


def cmd_scan(args: argparse.Namespace) -> int:
    data = pathlib.Path(args.blob).read_bytes()
    records = scan(data)
    print(f"blob {len(data)} bytes -- {len(records)} records")
    for record in records:
        print(
            f"  {record.offset:#09x}  len={record.length:<4} "
            f"type={record.type:<3} id={record.ident:#06x}  {record.name}"
        )
    return 0


def cmd_regions(args: argparse.Namespace) -> int:
    data = pathlib.Path(args.blob).read_bytes()
    groups = cluster(scan(data))
    print(f"blob {len(data)} bytes -- {len(groups)} regions")
    for group in groups:
        first, last = group[0], group[-1]
        end = last.offset + 7 + len(last.name)
        terminated = data[end : end + 2] == TERMINATOR
        print(
            f"  {first.offset:#09x}-{end:#09x}  {len(group):>4} records"
            f"{'  [0xBEEF]' if terminated else ''}"
        )
        for record in group[:12]:
            print(f"      type={record.type} id={record.ident:#06x}  {record.name}")
        if len(group) > 12:
            print(f"      ... +{len(group) - 12} more")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("scan", cmd_scan), ("regions", cmd_regions)):
        p = sub.add_parser(name)
        p.add_argument("blob")
        p.set_defaults(func=handler)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
