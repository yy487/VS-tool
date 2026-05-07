#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch current Silky body.exe font.bfd temporary read buffer limit.

The game allocates a temporary buffer with HeapAlloc(0x400000) and then reads
font.bfd with ReadFile(..., 0x400000).  Expanded BFD files above 4 MiB need both
immediates patched to a larger value.

Confirmed sites in body.exe:
  VA 0043027A: push 0x400000  ; allocation size before FUN_00403540
  VA 00430308: push 0x400000  ; read size before FUN_0043cdd0

Usage:
  python patch_silky_bfd_exe_read_limit.py body.exe body_chs.exe --limit 0x800000
  python patch_silky_bfd_exe_read_limit.py body.exe body_chs.exe --font build/font.bfd
  python patch_silky_bfd_exe_read_limit.py body.exe --dry-run --limit 0x800000
"""
from __future__ import annotations

import argparse
import math
import shutil
import struct
from pathlib import Path

IMAGE_BASE_DEFAULT = 0x400000
PATCH_VAS = {
    0x0043027A: "font.bfd temporary HeapAlloc size",
    0x00430308: "font.bfd ReadFile max bytes",
}
OLD_LIMIT = 0x400000


def _read_u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _read_u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _parse_pe_sections(data: bytes):
    if data[:2] != b"MZ":
        raise ValueError("not a PE/MZ executable")
    pe = _read_u32(data, 0x3C)
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("invalid PE header")
    coff = pe + 4
    num_sections = _read_u16(data, coff + 2)
    opt_size = _read_u16(data, coff + 16)
    opt = coff + 20
    magic = _read_u16(data, opt)
    if magic != 0x10B:
        raise ValueError(f"only PE32 is supported, optional-header magic=0x{magic:X}")
    image_base = _read_u32(data, opt + 28)
    sec_off = opt + opt_size
    sections = []
    for i in range(num_sections):
        s = sec_off + i * 40
        name = data[s:s + 8].split(b"\0", 1)[0].decode("ascii", errors="replace")
        virtual_size, virtual_address, raw_size, raw_ptr = struct.unpack_from("<IIII", data, s + 8)
        sections.append({
            "name": name,
            "va": virtual_address,
            "vsize": virtual_size,
            "raw_size": raw_size,
            "raw_ptr": raw_ptr,
        })
    return image_base, sections


def _va_to_file_offset(data: bytes, va: int) -> int:
    image_base, sections = _parse_pe_sections(data)
    rva = va - image_base
    for s in sections:
        span = max(s["vsize"], s["raw_size"])
        if s["va"] <= rva < s["va"] + span:
            off = s["raw_ptr"] + (rva - s["va"])
            if not (0 <= off < len(data)):
                raise ValueError(f"VA 0x{va:08X} maps outside file")
            return off
    raise ValueError(f"VA 0x{va:08X} not in any PE section")


def _round_up_limit(size: int, minimum: int = OLD_LIMIT) -> int:
    need = max(size, minimum)
    # Keep the patch simple and stable: round to whole MiB.
    mib = 0x100000
    return int(math.ceil(need / mib) * mib)


def _parse_int(s: str) -> int:
    return int(s, 0)


def patch_exe(input_exe: Path, output_exe: Path | None, *, limit: int, dry_run: bool) -> list[dict]:
    data = bytearray(input_exe.read_bytes())
    if limit <= 0:
        raise ValueError("limit must be positive")
    if limit > 0x7FFFFFFF:
        raise ValueError("limit is too large for this signed 32-bit push immediate")

    results = []
    for va, desc in PATCH_VAS.items():
        off = _va_to_file_offset(data, va)
        if data[off] != 0x68:
            raise ValueError(f"unexpected opcode at VA 0x{va:08X} / file 0x{off:X}: {data[off]:02X}, expected 68 push imm32")
        old = _read_u32(data, off + 1)
        if old not in (OLD_LIMIT, limit):
            raise ValueError(
                f"unexpected existing value at VA 0x{va:08X} / file 0x{off + 1:X}: "
                f"0x{old:X}, expected 0x{OLD_LIMIT:X} or already 0x{limit:X}"
            )
        results.append({"va": va, "file_offset": off + 1, "description": desc, "old": old, "new": limit})
        if not dry_run:
            struct.pack_into("<I", data, off + 1, limit)

    if not dry_run:
        if output_exe is None:
            raise ValueError("output exe is required unless --dry-run is used")
        output_exe.parent.mkdir(parents=True, exist_ok=True)
        output_exe.write_bytes(data)
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Patch Silky EXE font.bfd read-buffer limit")
    ap.add_argument("input_exe")
    ap.add_argument("output_exe", nargs="?", help="patched EXE path; omitted with --dry-run")
    ap.add_argument("--limit", type=_parse_int, default=None, help="new limit, e.g. 0x800000")
    ap.add_argument("--font", help="expanded font.bfd; limit is rounded up to MiB above this file size")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.limit is None and not args.font:
        limit = 0x800000
    elif args.limit is not None:
        limit = args.limit
    else:
        limit = _round_up_limit(Path(args.font).stat().st_size)

    font_size = Path(args.font).stat().st_size if args.font else None
    if font_size is not None and font_size > limit:
        raise ValueError(f"font size 0x{font_size:X} exceeds requested limit 0x{limit:X}")

    results = patch_exe(Path(args.input_exe), Path(args.output_exe) if args.output_exe else None, limit=limit, dry_run=args.dry_run)
    print(f"new font.bfd read limit: {limit} bytes / 0x{limit:X}")
    if font_size is not None:
        print(f"font.bfd size:          {font_size} bytes / 0x{font_size:X}")
    for r in results:
        status = "already" if r["old"] == r["new"] else "patch"
        print(f"{status}: VA 0x{r['va']:08X}, file+0x{r['file_offset']:X}, {r['description']}: 0x{r['old']:X} -> 0x{r['new']:X}")
    if args.dry_run:
        print("dry-run only; no file written")
    else:
        print(f"written: {args.output_exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
