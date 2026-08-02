#!/usr/bin/env python3
"""Emit the complete command table of a Harmony One config blob.

Everything needed is now recoverable from the blob alone. A command lives in a
fixed-shape record whose waveform follows inline, which is why nothing points at
the records -- the firmware walks them:

    xx 01 00 00 00 00 <u24 period> <u24 half>    carrier, in NANOSECONDS
    01 <ptr24>                                   self-reference
    01 <ptr24 -> waveform>                       the waveform
    <ptr24> 00 00 00
    ff 7f ...                                    the waveform starts here, at +10

The two u24 are the carrier period and its half. 25000 ns is 40 kHz, which is
Sony's carrier; 26315 ns is 38 kHz, which is NEC/Toshiba's. That single field
separates the devices without decoding anything, and it agrees with the split
derived independently from the high byte of the 0x7D command id (84 + 90 Sony,
62 Toshiba).

A Hub config supplies the names: it lists the same commands in clear, so
matching on (protocol, payload) turns a decoded waveform into "PowerToggle"
or "ChannelUp". Pass your own with `--hub`; without one the name and device
columns simply stay empty.

Usage:
    python3 commands.py <blob.bin> [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from collections import Counter

import glyphs
import irscan

BASE = 0x040000
CARRIER = {25000: "40.0 kHz (Sony)", 26315: "38.0 kHz (NEC/Toshiba)"}

# Which device a command belongs to, decided by the address inside its
# payload. NOT a table written here: which addresses belong to which device
# is a property of whoever's equipment the blob was built for, so it is
# DERIVED from the hub-config passed in (`hub_devices()` below). Without one
# the column simply comes back empty -- which is honest -- instead of
# labelling your commands with somebody else's device names.
#
# What makes the grouping trustworthy is that it reproduces the split
# derived independently from the high byte of the 0x7D command id, with no
# hub-config involved at all.


def u24(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


#: high byte of the ptr24 that a command record holds. The low bound is the
#: config base (0x040000); the high bound used to be 0x18, which caps the
#: waveform at file offset 0x14FFFF -- fine for the factory blob (0x14173A) but
#: NOT for a blob that has grown: adding a second device pushes the IR block to
#: 0x14D055..0x1500C3 and every record past 0x150000 was silently DROPPED, so
#: the census came back 62 of 63 and looked like a generation bug. The real
#: ceiling is the config region itself (configcheck: size < 0x390000), so the
#: bound is 0x38. Control: widening 0x18 -> 0x38 leaves the census EXACTLY
#: unchanged on the three blobs that exist -- config_raw 236/236,
#: philips_softkeys 268/268, lg_solo 299/299 -- i.e. it adds no false positive,
#: it only stops truncating.
PTR_HI_MIN, PTR_HI_MAX = 0x04, 0x38


def records(b: bytes):
    """Yield every command record, found by its inline waveform pointer."""
    for o in range(11, len(b) - 3):
        if b[o - 1] != 1 or not PTR_HI_MIN <= b[o + 2] <= PTR_HI_MAX:
            continue
        p = (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)) - BASE
        if not (0 <= p < len(b) - 1) or (b[p] | (b[p + 1] << 8)) != irscan.LEAD_IN:
            continue
        yield o - 1, p, u24(b, o - 11), u24(b, o - 8)


def hub_devices(path: str):
    """Map (protocol, address) -> device name, from a Hub config.

    The Hub groups its commands by device, so every `KeyCode` under a
    device gives one (protocol, address) that belongs to it. Built at run
    time from the file you pass; nothing is hardcoded, and an unreadable or
    absent file gives `{}` (the `device` column then stays empty).
    """
    out: dict[tuple[str, int], str] = {}
    try:
        dl = json.loads(pathlib.Path(path).read_text())
    except OSError:
        return out
    stack = [dl]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            dev = node.get("Device")
            if isinstance(dev, dict) and isinstance(node.get("Commands"), list):
                name = dev.get("Name") or "%s %s" % (
                    dev.get("Manufacturer"),
                    dev.get("Model"),
                )
                for c in node["Commands"]:
                    kc = (c or {}).get("KeyCode") or ""
                    partes = kc.split(":")
                    m = re.search(r"0x([0-9A-Fa-f]+)", kc)
                    if len(partes) > 1 and m:
                        proto = partes[1]
                        out[(proto, address(proto, int(m.group(1), 16)))] = name
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


def hub_names(path: str):
    """Map (protocol, payload) -> command names, from the Hub's own config."""
    out = {}
    try:
        dl = json.loads(pathlib.Path(path).read_text())
    except OSError:
        return out
    stack = [dl]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            kc = node.get("KeyCode")
            if kc:
                m = re.search(r"0x([0-9A-Fa-f]+)", kc)
                if m:
                    key = (kc.split(":")[1], int(m.group(1), 16))
                    out.setdefault(key, set()).add(node.get("CommandTypeId") or "?")
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


def address(proto: str, value: int):
    """The device address carried inside the payload, by protocol family."""
    if proto.startswith("Sony 12"):
        return value & 0x1F
    if proto.startswith("Sony 15"):
        return value & 0xFF
    return (value >> 16) & 0xFFFF


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("blob")
    ap.add_argument("--json")
    ap.add_argument(
        "--hub",
        default=str(glyphs.devicelist_path()),
        help="a Hub DeviceList.json (per-user data, not shipped with this "
        "repo); resolved by glifos.ruta_devicelist() when not given",
    )
    a = ap.parse_args()
    b = pathlib.Path(a.blob).read_bytes()
    names = hub_names(a.hub)
    devices = hub_devices(a.hub)

    rows, undecoded = [], 0
    for off, wf, period, half in records(b):
        r = irscan.decode(irscan.read_waveform(b, wf))
        if not r:
            undecoded += 1
            continue
        proto, bits, value = r
        rows.append(
            {
                "record": "0x%06X" % off,
                "waveform": "0x%06X" % wf,
                "carrier_ns": period,
                "carrier": CARRIER.get(period, "%d ns" % period),
                "protocol": proto,
                "bits": bits,
                "payload": "0x%X" % value,
                "address": address(proto, value),
                "keycode": "G:%s:()(0x%X)():3" % (proto, value),
                "device": devices.get((proto, address(proto, value))),
                "command": sorted(names.get((proto, value), [])) or None,
            }
        )

    named = sum(1 for r in rows if r["command"])
    print("%d command records, %d undecoded" % (len(rows), undecoded))
    print("by carrier :", dict(Counter(r["carrier"] for r in rows)))
    print("by protocol:", dict(Counter(r["protocol"] for r in rows)))
    print("named from the Hub: %d of %d" % (named, len(rows)))
    print("by device  :", dict(Counter(r["device"] for r in rows)))
    print("\naddresses per protocol:")
    for proto in sorted({r["protocol"] for r in rows}):
        addrs = Counter(r["address"] for r in rows if r["protocol"] == proto)
        print("  %-16s %s" % (proto, addrs.most_common()))

    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(rows, indent=1))
        print("\nwrote %s" % a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
