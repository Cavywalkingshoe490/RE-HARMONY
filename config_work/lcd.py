#!/usr/bin/env python3
"""Recover the LCD controller programming from a Harmony One firmware image.

The display is not on a serial bus. It is memory mapped, and reached through
TBLPTR like external flash, with the low byte of the pointer selecting between
two ports:

    0x020028   index   -- which controller register to talk to
    0x020029   data    -- the value, written high byte first

So a register write is a run of TBLWT* with TBLPTRL toggling between 0x28 and
0x29, and the value comes from TABLAT, which is loaded by MOVLW, by CLRF, or by
MOVFF from RAM. Reading the register numbers alone already separates the panels;
reading the values is what settles the scan direction, the panel size and the
colour depth, so this walks the code linearly and tracks both registers.

Values that come from RAM cannot be resolved statically. They are reported as
'var' rather than guessed, because a fabricated constant here would silently
become a false claim about the panel geometry.

Usage:
    python3 lcd.py <firmware.bin> [--from 0xADDR] [--to 0xADDR]
"""

from __future__ import annotations

import argparse
import pathlib

INDEX_PORT = 0x28
DATA_PORT = 0x29

# SSD1289 register names, from its datasheet. Only the ones the firmware
# actually writes are listed; the point is to make an init sequence readable,
# not to reproduce the datasheet.
SSD1289 = {
    0x00: "oscillation start",
    0x01: "driver output control",
    0x02: "LCD drive AC control",
    0x03: "power control 1",
    0x04: "compare 1",
    0x05: "compare 2",
    0x06: "display control",
    0x07: "display control",
    0x08: "frame cycle control",
    0x09: "gate scan start",
    0x0B: "frame cycle control",
    0x0C: "power control 3",
    0x0D: "power control 4",
    0x0E: "power control 5",
    0x0F: "gate scan position",
    0x10: "sleep mode",
    0x11: "entry mode",
    0x12: "optimise access speed 1",
    0x16: "horizontal porch",
    0x17: "vertical porch",
    0x1E: "power control 6",
    0x22: "GRAM data write",
    0x23: "RAM write mask 1",
    0x24: "RAM write mask 2",
    0x25: "frame frequency",
    0x26: "optimise access speed 3",
    0x28: "VCOM OTP",
    0x44: "horizontal RAM position",
    0x45: "vertical RAM start",
    0x46: "vertical RAM end",
    0x48: "first screen driving position",
    0x4E: "GDDRAM X counter",
    0x4F: "GDDRAM Y counter",
    0x60: "driver output control 2",
    0x61: "base image display",
}
for g in range(0x30, 0x3C):
    SSD1289.setdefault(g, "gamma control")


def decode_entry_mode(v: int) -> str:
    """R11 of the SSD1289: the bits that decide how the address counter moves."""
    am = (v >> 3) & 1
    idv = (v >> 4) & 3
    tri = (v >> 15) & 1
    dfm = (v >> 13) & 3
    return "AM=%d (%s), I/D=%d (%s), TRI=%d, DFM=%d" % (
        am,
        "vertical, column-major" if am else "horizontal, row-major",
        idv,
        {0: "X-, Y-", 1: "X+, Y-", 2: "X-, Y+", 3: "X+, Y+"}[idv],
        tri,
        dfm,
    )


def value_of(pend):
    """The value written to a register, however many bytes the panel takes.

    The two controllers differ here: the SSD1289 has a 16-bit register
    interface and takes two writes per register, high byte first, while the
    HX8347 has an 8-bit one and takes a single write. Assuming two bytes read
    every HX8347 value as unknown, which is what made a whole init sequence
    look table-driven when it is in fact hardcoded.
    """
    if not pend or None in pend:
        return None
    return (pend[-2] << 8) | pend[-1] if len(pend) >= 2 else pend[-1]


def walk(fw: bytes, lo: int, hi: int):
    """Yield (site, register, value_or_None) for each register write."""
    ptr, tab, reg, pend = None, None, None, []
    o = lo
    while o < min(hi, len(fw) - 1):
        w = fw[o] | (fw[o + 1] << 8)
        if (w & 0xFF00) == 0x0E00:  # MOVLW k
            tab_next = w & 0xFF
            if o + 2 < len(fw) - 1:
                n = fw[o + 2] | (fw[o + 3] << 8)
                if n == 0x6EF6:  # MOVWF TBLPTRL
                    ptr = tab_next
                    o += 4
                    continue
                if n == 0x6EF5:  # MOVWF TABLAT
                    tab = tab_next
                    o += 4
                    continue
            tab = tab_next
        elif w == 0x6AF5:  # CLRF TABLAT
            tab = 0
        elif (w & 0xF000) == 0xC000:  # MOVFF src, dst
            tab = None if (fw[o + 2] | (fw[o + 3] << 8)) & 0xFFF == 0xFF5 else tab
            o += 4
            continue
        elif w == 0x000C:  # TBLWT*
            if ptr == INDEX_PORT:
                if reg is not None:
                    yield reg[0], reg[1], value_of(pend)
                reg, pend = (o, tab), []
            elif ptr == DATA_PORT:
                pend.append(tab)
        o += 2
    if reg is not None:
        yield reg[0], reg[1], value_of(pend)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("firmware")
    ap.add_argument("--from", dest="lo", type=lambda x: int(x, 0), default=0)
    ap.add_argument("--to", dest="hi", type=lambda x: int(x, 0), default=1 << 30)
    a = ap.parse_args()

    fw = pathlib.Path(a.firmware).read_bytes()
    for site, reg, val in walk(fw, a.lo, a.hi):
        if reg is None:
            continue
        print(
            "  %#08x  R%02X = %-6s  %s"
            % (
                site,
                reg,
                "%#06x" % val if val is not None else "var",
                SSD1289.get(reg, ""),
            )
        )
        if reg == 0x11 and val is not None:
            print("            -> %s" % decode_entry_mode(val))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
