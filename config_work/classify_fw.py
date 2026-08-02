#!/usr/bin/env python3
"""Classify Harmony One firmware routines by the peripherals they touch.

Seeding Ghidra turns 454 call targets into 484 functions with boundaries, but
they all come out named FUN_CODE_xxxxxx, which is a map with no legend. On a
microcontroller the legend is cheap: what a routine is for is largely decided by
which special function registers it reads and writes. A routine that writes PR2
and toggles T2CON bit 2 is driving the IR carrier whatever it is called.

So this walks each routine from its entry to its first RETURN, records every
access to a named SFR, and buckets it. Routines that touch no SFR at all are
reported separately -- those are the pure logic, and they are the ones that
actually need reading.

Usage:
    python3 classify_fw.py <firmware.bin> <seeds.txt>
    python3 classify_fw.py <firmware.bin> <seeds.txt> --group ir
"""

from __future__ import annotations

import argparse
import pathlib
from collections import defaultdict


# Which peripheral each access-bank SFR belongs to. Only the ones that
# discriminate: everything writes WREG and BSR, so those say nothing.
GROUPS = {
    "ir": {
        0xBA: "CCP2CON",
        0xBB: "CCPR2L",
        0xBC: "CCPR2H",
        0xBD: "CCP1CON",
        0xBE: "CCPR1L",
        0xBF: "CCPR1H",
        0xCA: "T2CON",
        0xCB: "PR2",
        0xCC: "TMR2",
        0xD5: "T0CON",
        0xD6: "TMR0L",
        0xD7: "TMR0H",
    },
    "flash": {0xF6: "TBLPTRL", 0xF7: "TBLPTRH", 0xF8: "TBLPTRU", 0xF5: "TABLAT"},
    "port": {
        0x81: "PORTB",
        0x82: "PORTC",
        0x8A: "LATB",
        0x8B: "LATC",
        0x93: "TRISB",
        0x94: "TRISC",
    },
    "irq": {0xF2: "INTCON", 0xF1: "INTCON2", 0xF0: "INTCON3"},
    "ptr": {
        0xE9: "FSR0L",
        0xEA: "FSR0H",
        0xE1: "FSR1L",
        0xE2: "FSR1H",
        0xD9: "FSR2L",
        0xDA: "FSR2H",
    },
}
OWNER = {reg: g for g, regs in GROUPS.items() for reg in regs}

KNOWN = {
    0x02CD48: "flash_unlock_jedec",
    0x02CD6E: "flash_send_cmd",
    0x02CD82: "flash_reset_read",
    0x02CD94: "flash_poll_dq6",
    0x02CDEC: "flash_erase_sector",
    0x02CE10: "flash_read_byte",
    0x02CE30: "flash_program_byte",
    0x02CE56: "flash_program_buffer",
    0x02CF56: "flash_helper",
    0x02E74A: "far_table_read",
    0x02D9E0: "ir_set_carrier",
    0x02DA6A: "ir_carrier_off",
    0x02DA78: "ir_play_seq",
    0x020636: "ir_engine2",
    0x02C0E8: "ir_engine3_interp",
    0x02D382: "ir_engine4",
    0x0203D4: "divide",
    0x026ED2: "ir_sequence_end",
    0x02B8F8: "cfg_getbyte",
    0x02B90A: "cfg_getbyte_to_buf",
    0x02BA08: "cfg_ptr_inc",
    0x02BA14: "cfg_ptr_add",
    0x02DE6C: "cfg_read_at_ptr",
    0x029A1A: "cfg_stream_banked",
    0x02B8AC: "cfg_follow_ptr",
    0x02B88A: "cfg_ptr_to_logical",
    0x02B9FA: "cfg_ptr_inc2",
    0x02BA06: "nop_hook",
    0x029768: "cmd_setup_ir",
    0x02E70A: "cfg_read_u16",
    0x020380: "ui_getbyte",
    0x020394: "ui_putbyte",
    0x020D00: "delay_nop_slide",
    0x02DD0E: "poll_with_timeout",
    0x0263E6: "ui_parse_taglist",
    0x02692C: "ui_widget_renderer",
    0x0267FE: "sprite_draw_internal",
    0x0295AC: "record_field_parser",
    0x02DCCC: "i2c_start",
    0x02DCDA: "i2c_restart",
    0x02DCE6: "i2c_stop",
    0x02DCF2: "i2c_send_ack",
    0x02DD00: "i2c_send_nack",
    0x02DD0E: "i2c_xfer_byte",
    0x02DD80: "i2c_wait_idle",
    0x02AB80: "i2c_w_dev48",
    0x02ABA2: "i2c_rw_dev48",
    0x02BBF0: "latch_write_0x020020",
    0x02DCA8: "i2c_w_dev48_c",
    0x02D30C: "i2c_w_dev60",
    0x02D32E: "i2c_w_dev60_b",
    0x02D804: "i2c_w_dev1C",
    0x02D826: "i2c_w_dev1C_b",
    0x02DEEA: "i2c_w_dev20",
    0x025926: "cmd_dispatch_dev20",
    0x025FD0: "dev20_pack12_and_read",
    0x025EDE: "dev20_session_step",
    0x0258BA: "dev20_send_1arg",
    0x0258D8: "dev20_send_1arg_b",
    0x0258F6: "dev20_send_3arg",
    0x02DF46: "dev20_read_reply",
    0x02DD68: "i2c_read_byte",
    0x02DF4C: "i2c_r_dev20",
    0x02BA76: "cfg_index_x4",
    0x02BAD2: "cfg_index_x3",
    0x02BBCE: "cfg_ptr_restore",
    0x0224FC: "?nvram_a",
    0x02CCFC: "delay_clrwdt",
}


def scan(fw: bytes, start: int, limit: int = 4096):
    """Walk from `start` to its first RETURN; return (size, SFRs touched)."""
    seen = set()
    o = start
    while o + 1 < len(fw) and o - start < limit:
        word = fw[o] | (fw[o + 1] << 8)
        # RETURN, RETFIE, and RETLW k -- leaving RETLW out let routines run into
        # their neighbours, which inflated both the sizes and the SFR sets, and
        # made a plain predicate look like it drove the LCD.
        if word in (0x0012, 0x0011) or (word & 0xFF00) == 0x0C00:
            return o + 2 - start, seen
        # any instruction whose low byte is a file register, with a=0
        top = word >> 8
        is_file = (
            (word & 0xFE00) in (0x6E00, 0x6A00)  # MOVWF, CLRF
            or (word & 0xFC00)
            in (
                0x5000,
                0x2400,
                0x2000,
                0x5C00,
                0x5800,
                0x5400,
                0x1800,
                0x1400,
                0x1000,
                0x2800,
                0x0400,
                0x2C00,
                0x3C00,
            )
            or 0x80 <= top <= 0xBF  # BSF/BCF/BTFSS/BTFSC
        )
        if is_file:
            access = (
                not (word & 0x0100) if (word & 0xFE00) in (0x6E00, 0x6A00) else True
            )
            reg = word & 0xFF
            if access and reg in OWNER:
                seen.add(reg)
        o += 2
    return o - start, seen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("firmware")
    ap.add_argument("seeds")
    ap.add_argument("--group")
    a = ap.parse_args()

    fw = pathlib.Path(a.firmware).read_bytes()
    seeds = []
    for line in pathlib.Path(a.seeds).read_text().splitlines():
        parts = line.split()
        if parts:
            seeds.append((int(parts[0], 16), int(parts[1]) if len(parts) > 1 else 0))

    buckets = defaultdict(list)
    for addr, calls in seeds:
        if addr >= len(fw):
            continue
        size, regs = scan(fw, addr)
        groups = sorted({OWNER[r] for r in regs} - {"ptr"})
        key = "+".join(groups) if groups else ("ptr-only" if regs else "pure logic")
        buckets[key].append((addr, calls, size, regs))

    if a.group:
        rows = sorted(buckets.get(a.group, []), key=lambda r: -r[1])
        print("%d routines in group '%s'" % (len(rows), a.group))
        for addr, calls, size, regs in rows:
            names = ",".join(
                sorted(GROUPS[OWNER[r]][r] for r in regs if OWNER[r] != "ptr")
            )
            print(
                "  %#08x  %-22s calls %3d  %4d B  %s"
                % (addr, KNOWN.get(addr, ""), calls, size, names)
            )
        return 0

    print(
        "%d routines classified by the SFRs they touch\n"
        % sum(map(len, buckets.values()))
    )
    for key, rows in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        named = [KNOWN[a2] for a2, _, _, _ in rows if a2 in KNOWN]
        print(
            "  %-22s %4d routines%s"
            % (key, len(rows), "   e.g. " + ", ".join(named[:3]) if named else "")
        )

    print(
        "\nidentified by hand so far: %d of %d"
        % (len([s for s, _ in seeds if s in KNOWN]), len(seeds))
    )
    print("\nmost-called routines still unnamed:")
    unnamed = [(s, c) for s, c in seeds if s not in KNOWN]
    for addr, calls in sorted(unnamed, key=lambda r: -r[1])[:12]:
        _, regs = scan(fw, addr)
        g = sorted({OWNER[r] for r in regs})
        print(
            "  %#08x  called %3d  touches: %s" % (addr, calls, ",".join(g) or "nothing")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
