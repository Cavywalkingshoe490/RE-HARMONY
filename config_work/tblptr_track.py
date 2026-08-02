#!/usr/bin/env python3
"""Adversarial re-scan of TBLRD/TBLWT targets.

Unlike the "strict scan" (which pattern-matches an adjacent
MOVLW/MOVWF TBLPTRU + CLRF TBLPTRH + MOVLW/MOVWF TBLPTRL idiom), this walks the
instruction stream carrying a symbolic TBLPTR (u,h,l) forward, so it also sees:
  - loads in any order, at any distance
  - CLRF / SETF / MOVLW-MOVWF / MOVFF / INCF / DECF / ADDWF writes to TBLPTRx
  - addresses reached only by the auto-inc/dec table ops (TBLRD*+, TBLWT*+, ...)
  - partial reloads (only TBLPTRL changed, upper bytes left over)
It also COUNTS the table ops whose pointer it cannot resolve, which is the
ceiling on any "this address appears nowhere else" claim.
"""

import sys, collections
import pic18dis

TBLOPS = {
    0x0008: ("TBLRD*", "r", 0),
    0x0009: ("TBLRD*+", "r", +1),
    0x000A: ("TBLRD*-", "r", -1),
    0x000B: ("TBLRD+*", "r", "pre"),
    0x000C: ("TBLWT*", "w", 0),
    0x000D: ("TBLWT*+", "w", +1),
    0x000E: ("TBLWT*-", "w", -1),
    0x000F: ("TBLWT+*", "w", "pre"),
}

TU, TH, TL, TA = 0xF8, 0xF7, 0xF6, 0xF5  # low byte of TBLPTRU/H/L, TABLAT


def scan(fw, start, end, base=0, reset_on_ret=True, reset_on_call=False):
    """Yield (site, opname, kind, addr_or_None, tablat_or_None)."""
    ptr = [None, None, None]  # u, h, l
    w = None
    tablat = None
    o = start
    while o < end and o + 1 < len(fw):
        x = fw[o] | (fw[o + 1] << 8)
        txt, size = pic18dis.decode(fw, o)

        if x in TBLOPS:
            name, kind, delta = TBLOPS[x]
            if delta == "pre":
                _bump(ptr, +1)
            addr = None
            if all(v is not None for v in ptr):
                addr = (ptr[0] << 16) | (ptr[1] << 8) | ptr[2]
            yield (base + o, name, kind, addr, tablat)
            if delta in (+1, -1):
                _bump(ptr, delta)
            o += 2 * size
            continue

        acc = not (x & 0x100)
        f = x & 0xFF

        if (x & 0xFF00) == 0x0E00:  # MOVLW k
            w = x & 0xFF
        elif (x & 0xFE00) == 0x6E00 and acc:  # MOVWF f,ACCESS
            _set(ptr, f, w)
            if f == TA:
                tablat = w
        elif (x & 0xFE00) == 0x6A00 and acc:  # CLRF f,ACCESS
            _set(ptr, f, 0)
            if f == TA:
                tablat = 0
        elif (x & 0xFE00) == 0x6800 and acc:  # SETF
            _set(ptr, f, 0xFF)
            if f == TA:
                tablat = 0xFF
        elif (x & 0xF000) == 0xC000:  # MOVFF s,d
            n = fw[o + 2] | (fw[o + 3] << 8)
            d = n & 0xFFF
            if d in (0xFF6, 0xFF7, 0xFF8):
                _set(ptr, d & 0xFF, None)
            if d == 0xFF5:
                tablat = None
        elif (x & 0xFC00) == 0x2800 and acc and (x & 0x200):  # INCF f,f
            _delta(ptr, f, +1)
            if f == TA:
                tablat = None
        elif (x & 0xFC00) == 0x0400 and acc and (x & 0x200):  # DECF f,f
            _delta(ptr, f, -1)
            if f == TA:
                tablat = None
        elif acc and f in (TU, TH, TL, TA) and _writes_f(x):
            # any other ALU op with d=1 onto a TBLPTR byte / TABLAT
            _set(ptr, f, None)
            if f == TA:
                tablat = None
        elif x in (0x0012, 0x0011) or (x & 0xFF00) == 0x0C00:  # RETURN/RETFIE/RETLW
            if reset_on_ret:
                ptr = [None, None, None]
                w = None
                tablat = None
        elif (x & 0xFF00) == 0xEC00 and reset_on_call:  # CALL
            ptr = [None, None, None]
            w = None
            tablat = None

        # W becomes unknown after anything that writes W and is not MOVLW
        if (x & 0xFC00) in (
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
            0x3400,
            0x3000,
            0x4400,
            0x4000,
            0x3800,
            0x1C00,
        ) and not (x & 0x200):
            w = None
        if (x & 0xFF00) in (0x0F00, 0x0800, 0x0900, 0x0B00, 0x0A00):  # ADDLW etc
            w = None
        o += 2 * size


def _writes_f(x):
    two_op = (x & 0xFC00) in (
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
        0x3400,
        0x3000,
        0x4400,
        0x4000,
        0x3800,
        0x1C00,
    )
    if two_op:
        return bool(x & 0x200)
    return (x & 0xFE00) in (0x6C00,) or (x & 0xF000) in (0x9000, 0x8000, 0x7000)


def _set(ptr, f, val):
    if f == TU:
        ptr[0] = val
    elif f == TH:
        ptr[1] = val
    elif f == TL:
        ptr[2] = val


def _delta(ptr, f, d):
    i = {TU: 0, TH: 1, TL: 2}.get(f)
    if i is None:
        return
    ptr[i] = None if ptr[i] is None else (ptr[i] + d) & 0xFF


def _bump(ptr, d):
    if ptr[2] is None:
        ptr[0] = ptr[1] = ptr[2] = None
        return
    v = ptr[2] + d
    ptr[2] = v & 0xFF
    if v > 0xFF or v < 0:
        if ptr[1] is None:
            ptr[0] = ptr[1] = None
            return
        v2 = ptr[1] + (1 if v > 0xFF else -1)
        ptr[1] = v2 & 0xFF
        if v2 > 0xFF or v2 < 0:
            ptr[0] = (
                None if ptr[0] is None else (ptr[0] + (1 if v2 > 0xFF else -1)) & 0xFF
            )


if __name__ == "__main__":
    path = sys.argv[1]
    start = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0
    end = int(sys.argv[3], 0) if len(sys.argv) > 3 else None
    base = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0
    fw = open(path, "rb").read()
    end = len(fw) if end is None else end
    hits = collections.defaultdict(list)
    unknown = 0
    total = 0
    for site, name, kind, addr, tablat in scan(fw, start, end, base):
        total += 1
        if addr is None:
            unknown += 1
        else:
            hits[addr].append((site, name, kind, tablat))
    print(
        "table ops seen: %d   resolved: %d   UNRESOLVED: %d (%.1f%%)"
        % (total, total - unknown, unknown, 100.0 * unknown / max(total, 1))
    )
    print()
    print("--- accesses landing in the I/O window 0x020000-0x02003F ---")
    for a in sorted(k for k in hits if 0x020000 <= k <= 0x02003F):
        rd = sum(1 for h in hits[a] if h[2] == "r")
        wr = sum(1 for h in hits[a] if h[2] == "w")
        vals = sorted(
            {h[3] for h in hits[a] if h[2] == "w"}, key=lambda v: (v is None, v)
        )
        print(
            "0x%06X  reads=%-4d writes=%-4d  written values=%s"
            % (a, rd, wr, ["?" if v is None else "0x%02X" % v for v in vals])
        )
        for site, name, kind, tablat in hits[a]:
            print(
                "        %06X  %-8s %s%s"
                % (
                    site,
                    name,
                    kind,
                    "" if tablat is None else "  TABLAT=0x%02X" % tablat,
                )
            )
