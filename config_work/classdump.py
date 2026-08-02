#!/usr/bin/env python3
"""Read Java .class files without a JVM, to recover the Harmony HID protocol.

The official Logitech Harmony Remote Software is a Java application, and its
hidcommands.jar carries the protocol the desktop client speaks to the remote:
class names like WriteFlashCommand, ReadProgramCommand and Protocol12 -- arch 12
being this remote -- describe in words what months of disassembly recovered in
hex. Reading them settles what the firmware only implies.

The bundled JRE has no bin/ and the system has no Java, so javap is not an
option. It is not needed: a .class file is a constant pool followed by fields
and methods, and the opcode of a command is either an integer in that pool or a
bipush/sipush operand in the constructor. Both are reachable with a parser small
enough to audit.

Usage:
    python3 classdump.py <file.class> [--code]
"""

from __future__ import annotations

import argparse
import pathlib
import struct

# constant pool tags that carry a payload we care about
UTF8, INT, FLOAT, LONG, DOUBLE = 1, 3, 4, 5, 6
CLASS, STRING, FIELDREF, METHODREF, IFACEREF = 7, 8, 9, 10, 11
NAMETYPE, HANDLE, MTYPE, DYNAMIC, INVOKEDYN = 12, 15, 16, 17, 18
MODULE, PACKAGE = 19, 20

SKIP = {
    FIELDREF: 4,
    METHODREF: 4,
    IFACEREF: 4,
    NAMETYPE: 4,
    FLOAT: 4,
    INT: 4,
    DYNAMIC: 4,
    INVOKEDYN: 4,
    LONG: 8,
    DOUBLE: 8,
    CLASS: 2,
    STRING: 2,
    MTYPE: 2,
    MODULE: 2,
    PACKAGE: 2,
    HANDLE: 3,
}


def parse(data: bytes):
    """Return (constants, ints, name) where constants are the UTF-8 entries."""
    if data[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("not a class file")
    n = struct.unpack(">H", data[8:10])[0]
    o, pool, ints = 10, {}, {}
    i = 1
    while i < n:
        tag = data[o]
        o += 1
        if tag == UTF8:
            ln = struct.unpack(">H", data[o : o + 2])[0]
            pool[i] = data[o + 2 : o + 2 + ln].decode("utf-8", "replace")
            o += 2 + ln
        elif tag == INT:
            ints[i] = struct.unpack(">i", data[o : o + 4])[0]
            o += 4
        else:
            o += SKIP.get(tag, 2)
        i += 2 if tag in (LONG, DOUBLE) else 1
    return pool, ints, o


def code_constants(data: bytes, start: int, pool):
    """Yield the small integers a constructor pushes: the opcode lives there.

    javac encodes values under 128 as a bipush operand rather than a constant
    pool entry, so scanning the pool alone misses exactly the byte that names
    the command.
    """
    out = []
    o = start
    o += 6  # access, this, super
    ni = struct.unpack(">H", data[o : o + 2])[0]
    o += 2 + 2 * ni  # interfaces
    for _ in range(2):  # fields, then methods
        cnt = struct.unpack(">H", data[o : o + 2])[0]
        o += 2
        for _ in range(cnt):
            o += 6
            na = struct.unpack(">H", data[o : o + 2])[0]
            o += 2
            for _ in range(na):
                nm = struct.unpack(">H", data[o : o + 2])[0]
                ln = struct.unpack(">I", data[o + 2 : o + 6])[0]
                body = data[o + 6 : o + 6 + ln]
                if pool.get(nm) == "Code" and len(body) > 8:
                    clen = struct.unpack(">I", body[4:8])[0]
                    bc = body[8 : 8 + clen]
                    k = 0
                    while k < len(bc):
                        op = bc[k]
                        if op == 0x10 and k + 1 < len(bc):  # bipush
                            out.append(bc[k + 1])
                            k += 2
                            continue
                        if op == 0x11 and k + 2 < len(bc):  # sipush
                            out.append(struct.unpack(">h", bc[k + 1 : k + 3])[0])
                            k += 3
                            continue
                        k += 1
                o += 6 + ln
    return out


def fields_with_values(data, start, pool, ints):
    """Pair each static field with its ConstantValue.

    The pool alone gives the numbers and the strings but not which is which.
    A field carries its constant in a ConstantValue attribute whose two-byte
    body indexes the pool, so walking the field table is what turns a list of
    magic numbers into a labelled memory map.
    """
    import struct
    o = start + 6
    ni = struct.unpack(">H", data[o:o + 2])[0]
    o += 2 + 2 * ni
    cnt = struct.unpack(">H", data[o:o + 2])[0]
    o += 2
    out = []
    for _ in range(cnt):
        o += 2
        nm = struct.unpack(">H", data[o:o + 2])[0]
        o += 4
        na = struct.unpack(">H", data[o:o + 2])[0]
        o += 2
        val = None
        for _ in range(na):
            an = struct.unpack(">H", data[o:o + 2])[0]
            ln = struct.unpack(">I", data[o + 2:o + 6])[0]
            if pool.get(an) == "ConstantValue" and ln == 2:
                idx = struct.unpack(">H", data[o + 6:o + 8])[0]
                val = ints.get(idx, pool.get(idx))
            o += 6 + ln
        out.append((pool.get(nm), val))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("classfile")
    ap.add_argument("--code", action="store_true")
    a = ap.parse_args()
    data = pathlib.Path(a.classfile).read_bytes()
    pool, ints, after = parse(data)

    print("=== %s ===" % pathlib.Path(a.classfile).name)
    names = [v for v in pool.values() if v and not v.startswith("(")]
    print("cadenas:", " ".join(sorted(set(names))[:40]))
    if ints:
        print(
            "pool integers:",
            " ".join("%s=%d(%#x)" % (k, v, v & 0xFFFFFFFF) for k, v in ints.items()),
        )
    print()
    for nm, val in fields_with_values(data, after, pool, ints):
        if val is None:
            continue
        print("  %-42s %s" % (nm, ("%#x (%d)" % (val, val)) if isinstance(val, int) else val))
    if a.code:
        try:
            print("small pushes in the bytecode:", code_constants(data, after, pool))
        except Exception as e:  # noqa: BLE001
            print("could not walk the bytecode:", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
