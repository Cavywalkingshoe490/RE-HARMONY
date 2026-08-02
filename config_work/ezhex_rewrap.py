#!/usr/bin/env python3
"""
ezhex_rewrap.py -- re-wrap an (edited) Harmony config blob into a valid .EZHex
container that `concordance -C` will accept.

Why this exists
---------------
concordance's write path (main() in concordance/concordance.c) ALWAYS runs the
file through read_and_parse_file() -> OperationFile::ReadAndParseOpFile(), which
requires a plain-text <INFORMATION>...</INFORMATION> XML header followed by the
raw binary blob. The header must carry a correct <BINARYDATASIZE> and <CHECKSUM>
(8-bit XOR, seed 0x69, over the binary only) or find_config_binary() fails and
concordance exits with "ERROR: Cannot read input file".

So: dump once with `concordance -c backup.EZHex` (keeps the XML header that
matches YOUR remote's protocol/skin/flash/board), edit the binary part, then
use this tool to rebuild the container.

Usage
-----
  # split a dump into header + blob
  ./ezhex_rewrap.py split backup.EZHex --xml-out hdr.xml --bin-out blob.bin

  # verify a container (size + checksum + cookie)
  ./ezhex_rewrap.py verify backup.EZHex

  # rebuild a container from the original header + an edited blob
  ./ezhex_rewrap.py wrap --template backup.EZHex --bin edited.bin --out new.EZHex

Refs (concordance-main):
  libconcord/operationfile.cpp:37-89   find_config_binary()  (split + checks)
  libconcord/libconcord.cpp:1140-1191  write_config_to_file() (checksum + header)
  libconcord/remote_info.h:444-461     arch 12 (Harmony One): cookie 0x4D505347
"""

import argparse
import re
import sys

SEP = b"</INFORMATION>"
COOKIE_ARCH12 = 0x4D505347  # 'MPSG' little-endian -> bytes 47 53 50 4D


def xor_checksum(blob: bytes) -> int:
    """Exact replica of operationfile.cpp:73-78 / libconcord.cpp:1162-1166."""
    chk = 0x69
    for b in blob:
        chk ^= b
    return chk


def split_container(data: bytes):
    """Return (xml_bytes, binary_bytes). Mirrors find_config_binary():
    the binary starts 2 bytes after the END of the '</INFORMATION>' tag."""
    idx = data.find(SEP)
    if idx == -1:
        raise SystemExit(
            "ERROR: no </INFORMATION> tag - this is a raw blob, "
            "not an EZHex container (concordance -C will reject it)"
        )
    split_at = idx + len(SEP) + 2  # GetTag returns ptr past the tag; +2 skips \r\n
    return data[:split_at], data[split_at:]


def get_tag(xml: bytes, tag: str):
    m = re.search(rb"<" + tag.encode() + rb">(.*?)</" + tag.encode() + rb">", xml, re.S)
    return None if not m else m.group(1).decode(errors="replace").strip()


def cmd_verify(args):
    data = open(args.file, "rb").read()
    xml, blob = split_container(data)
    declared_size = get_tag(xml, "BINARYDATASIZE")
    declared_chk = get_tag(xml, "CHECKSUM")
    actual_chk = xor_checksum(blob)
    print(f"file            : {args.file}")
    print(f"xml header      : {len(xml)} bytes")
    print(f"binary blob     : {len(blob)} bytes")
    print(
        f"BINARYDATASIZE  : {declared_size}  -> "
        f"{'OK' if declared_size and int(declared_size) == len(blob) else 'MISMATCH'}"
    )
    print(
        f"CHECKSUM        : {declared_chk} (decl) / {actual_chk} (calc) -> "
        f"{'OK' if declared_chk and int(declared_chk) == actual_chk else 'MISMATCH'}"
    )
    for t in ("PROTOCOL", "SKIN", "FLASH", "BOARD", "SOFTWARETYPE"):
        v = get_tag(xml, t)
        if v is not None:
            print(f"{t:<16}: {v}")
    if len(blob) >= 8:
        cookie = int.from_bytes(blob[0:4], "little")
        end = int.from_bytes(blob[4:7], "little")
        print(
            f"cookie          : 0x{cookie:08X} -> "
            f"{'valid (arch 12 / Harmony One)' if cookie == COOKIE_ARCH12 else 'NOT the arch-12 cookie'}"
        )
        print(
            f"end_vector      : 0x{end:06X}  (config_bytes_used should be "
            f"{end - 0x040000 + 4} for config_base=0x040000)"
        )


def cmd_split(args):
    data = open(args.file, "rb").read()
    xml, blob = split_container(data)
    open(args.xml_out, "wb").write(xml)
    open(args.bin_out, "wb").write(blob)
    print(f"wrote {args.xml_out} ({len(xml)} B) and {args.bin_out} ({len(blob)} B)")


def cmd_wrap(args):
    tmpl = open(args.template, "rb").read()
    xml, _old = split_container(tmpl)
    blob = open(args.bin, "rb").read()
    chk = xor_checksum(blob)
    xml = re.sub(
        rb"<BINARYDATASIZE>\d+</BINARYDATASIZE>",
        f"<BINARYDATASIZE>{len(blob)}</BINARYDATASIZE>".encode(),
        xml,
    )
    xml = re.sub(
        rb"<CHECKSUM>\d+</CHECKSUM>", f"<CHECKSUM>{chk}</CHECKSUM>".encode(), xml
    )
    if b"<BINARYDATASIZE>" not in xml or b"<CHECKSUM>" not in xml:
        raise SystemExit("ERROR: template header lacks BINARYDATASIZE/CHECKSUM tags")
    open(args.out, "wb").write(xml + blob)
    print(f"wrote {args.out}: {len(xml)} B xml + {len(blob)} B binary, checksum={chk}")
    if len(blob) < 16:
        print(
            "WARNING: blob < 16 bytes -> ReadAndParseOpFile will NOT classify "
            "this as LC_FILE_TYPE_CONFIGURATION (operationfile.cpp:365)"
        )


def main():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("verify")
    v.add_argument("file")
    v.set_defaults(f=cmd_verify)
    s = sub.add_parser("split")
    s.add_argument("file")
    s.add_argument("--xml-out", default="header.xml")
    s.add_argument("--bin-out", default="config.bin")
    s.set_defaults(f=cmd_split)
    w = sub.add_parser("wrap")
    w.add_argument(
        "--template", required=True, help="original .EZHex dump (header source)"
    )
    w.add_argument("--bin", required=True, help="edited binary blob")
    w.add_argument("--out", required=True)
    w.set_defaults(f=cmd_wrap)

    args = p.parse_args()
    args.f(args)


if __name__ == "__main__":
    sys.exit(main())
