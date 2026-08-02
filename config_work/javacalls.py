#!/usr/bin/env python3
"""List, in order, what each Java method calls. Recovers a procedure, not a table.

classdump.py pulls constants and strings out of a .class, which is enough to
learn *what* names exist -- MEMORY_MODEL_AT49BV322A, "Erasing block at ",
COMMAND_WRITE_FLASH. It is not enough to learn the *order* of anything, and the
order is the whole question when the goal is to reproduce a flashing procedure
safely. That lives in the bytecode.

So this walks each method's Code attribute and prints the invoke* targets in
sequence, plus the string constants pushed with ldc along the way. The result
reads as a procedure:

    write(...)
      -> validateSize
      -> "Erasing block at "
      -> eraseBlock
      -> writeData
      -> verify

No attempt is made to reconstruct control flow -- branches are not followed, so
a loop looks like a straight line and a conditional shows both arms. That is a
real limitation and the output should be read as "these calls happen, in this
textual order", not "this is the execution trace".

Usage:
    python3 javacalls.py <file.class> [--method NAME] [--strings]
"""

from __future__ import annotations

import argparse
import pathlib
import struct

import classdump

# invoke opcodes, all with a two-byte constant pool index
INVOKE = {
    0xB6: "virtual",
    0xB7: "special",
    0xB8: "static",
    0xB9: "interface",
    0xBA: "dynamic",
}
LDC, LDC_W, LDC2_W = 0x12, 0x13, 0x14
# operand widths for everything else we may step over
WIDE1 = {
    0x10,
    0x15,
    0x16,
    0x17,
    0x18,
    0x19,
    0x36,
    0x37,
    0x38,
    0x39,
    0x3A,
    0xA9,
    0xBC,
    LDC,
}
WIDE2 = {
    0x11,
    0x13,
    0x14,
    0x84,
    0x99,
    0x9A,
    0x9B,
    0x9C,
    0x9D,
    0x9E,
    0x9F,
    0xA0,
    0xA1,
    0xA2,
    0xA3,
    0xA4,
    0xA5,
    0xA6,
    0xA7,
    0xA8,
    0xB2,
    0xB3,
    0xB4,
    0xB5,
    0xB6,
    0xB7,
    0xB8,
    0xBB,
    0xBD,
    0xC0,
    0xC1,
    0xC6,
    0xC7,
}


def refname(pool, raw, idx):
    """Resolve a Methodref/Fieldref index to 'Class.member'."""
    ent = raw.get(idx)
    if not ent:
        return "#%d" % idx
    cls, nt = ent
    cname = raw.get(cls)
    cname = pool.get(cname[0]) if isinstance(cname, tuple) else pool.get(cls)
    ntv = raw.get(nt)
    mname = pool.get(ntv[0]) if isinstance(ntv, tuple) else None
    short = (cname or "?").rsplit("/", 1)[-1]
    return "%s.%s" % (short, mname or "?")


def structure(data: bytes):
    """Second pass over the pool keeping the reference entries we skipped."""
    n = struct.unpack(">H", data[8:10])[0]
    o, pool, raw = 10, {}, {}
    i = 1
    while i < n:
        tag = data[o]
        o += 1
        if tag == classdump.UTF8:
            ln = struct.unpack(">H", data[o : o + 2])[0]
            pool[i] = data[o + 2 : o + 2 + ln].decode("utf-8", "replace")
            o += 2 + ln
        elif tag in (
            classdump.FIELDREF,
            classdump.METHODREF,
            classdump.IFACEREF,
            classdump.NAMETYPE,
        ):
            raw[i] = struct.unpack(">HH", data[o : o + 4])
            o += 4
        elif tag in (classdump.CLASS, classdump.STRING):
            raw[i] = (struct.unpack(">H", data[o : o + 2])[0],)
            o += 2
        elif tag == classdump.INT:
            raw[i] = struct.unpack(">i", data[o : o + 4])
            o += 4
        else:
            o += classdump.SKIP.get(tag, 2)
        i += 2 if tag in (classdump.LONG, classdump.DOUBLE) else 1
    return pool, raw, o


def methods(data: bytes, start: int, pool, raw):
    o = start + 6
    ni = struct.unpack(">H", data[o : o + 2])[0]
    o += 2 + 2 * ni
    for pas in range(2):
        cnt = struct.unpack(">H", data[o : o + 2])[0]
        o += 2
        for _ in range(cnt):
            o += 2
            nm = struct.unpack(">H", data[o : o + 2])[0]
            o += 4
            na = struct.unpack(">H", data[o : o + 2])[0]
            o += 2
            code = None
            for _ in range(na):
                an = struct.unpack(">H", data[o : o + 2])[0]
                ln = struct.unpack(">I", data[o + 2 : o + 6])[0]
                if pool.get(an) == "Code":
                    body = data[o + 6 : o + 6 + ln]
                    clen = struct.unpack(">I", body[4:8])[0]
                    code = body[8 : 8 + clen]
                o += 6 + ln
            if pas == 1 and code:
                yield pool.get(nm), code


def walk(code: bytes, pool, raw):
    out, k = [], 0
    while k < len(code):
        op = code[k]
        if op in INVOKE:
            idx = struct.unpack(">H", code[k + 1 : k + 3])[0]
            out.append(("call", refname(pool, raw, idx)))
            k += 5 if op == 0xB9 or op == 0xBA else 3
            continue
        if op in (LDC, LDC_W):
            w = 1 if op == LDC else 2
            idx = code[k + 1] if w == 1 else struct.unpack(">H", code[k + 1 : k + 3])[0]
            ent = raw.get(idx)
            if ent and len(ent) == 1 and pool.get(ent[0]):
                out.append(("str", pool[ent[0]]))
            elif ent and len(ent) == 1:
                out.append(("num", ent[0]))
            k += 1 + w
            continue
        if op == 0x10:  # bipush
            out.append(("num", code[k + 1]))
            k += 2
            continue
        if op == 0x11:  # sipush
            out.append(("num", struct.unpack(">h", code[k + 1 : k + 3])[0]))
            k += 3
            continue
        if op in (0xAA, 0xAB):  # table/lookupswitch
            break
        k += 1 + (2 if op in WIDE2 else 1 if op in WIDE1 else 0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("classfile")
    ap.add_argument("--method")
    ap.add_argument("--min", type=int, default=1)
    a = ap.parse_args()
    data = pathlib.Path(a.classfile).read_bytes()
    pool, raw, after = structure(data)

    print("=== %s ===" % pathlib.Path(a.classfile).name)
    for name, code in methods(data, after, pool, raw):
        if a.method and a.method not in (name or ""):
            continue
        seq = walk(code, pool, raw)
        if len(seq) < a.min:
            continue
        print("\n  %s()" % name)
        for kind, v in seq:
            if kind == "call":
                print("      -> %s" % v)
            elif kind == "str":
                print("         %r" % v[:90])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
