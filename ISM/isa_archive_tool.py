#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ISM ARCHIVED / .ISA unpack & repack tool

Tested against the ISM engine archive layout inferred from ism.dll:
  header:  b"ISM ARCHIVED" + uint32(count | flags << 16)
  entry:   0x30 bytes
           +0x00 char name[0x24]
           +0x24 uint32 offset
           +0x28 uint32 size
           +0x2C uint32 reserved
  index table may be XOR-obfuscated when flags has 0x8000 style bit.

Usage:
  python isa_archive_tool.py unpack DATA.ISA out_dir
  python isa_archive_tool.py pack out_dir DATA_new.ISA --manifest out_dir/_isa_manifest.json
  python isa_archive_tool.py list DATA.ISA
"""

from __future__ import annotations

import argparse
import json
import os
import struct
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, List, Optional

MAGIC = b"ISM ARCHIVED"
HEADER_SIZE = 0x10
ENTRY_SIZE = 0x30
NAME_SIZE = 0x24
DEFAULT_FLAGS = 0x8001


@dataclass
class IsaEntry:
    index: int
    name: str
    offset: int
    size: int
    reserved: int = 0


def _u32le(data: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _p32le(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


def decode_index_table(buf: bytearray) -> None:
    """Decode/encode ISA index table. XOR is symmetric."""
    table_size = len(buf)
    dword_count = table_size // 4
    for i in range(dword_count):
        pos = i * 4
        v = _u32le(buf, pos)
        key = ~((dword_count - i) + table_size) & 0xFFFFFFFF
        buf[pos:pos + 4] = _p32le(v ^ key)


def should_decode(flags: int) -> bool:
    # DLL condition looked like: (char)(flags >> 8) < 0
    return ((flags >> 8) & 0x80) != 0


def sanitize_archive_name(name: str) -> str:
    name = name.replace("\\", "/")
    parts = []
    for part in name.split("/"):
        if not part or part in (".", ".."):
            continue
        # Avoid Windows-forbidden filename characters in extracted path segments.
        part = "".join("_" if c in '<>:"|?*' else c for c in part)
        parts.append(part)
    return "/".join(parts)


def encode_entry_name(name: str, encoding: str = "cp932") -> bytes:
    raw = name.replace("\\", "/").encode(encoding)
    if len(raw) >= NAME_SIZE:
        raise ValueError(f"file name too long for ISA entry name[0x24]: {name!r} ({len(raw)} bytes)")
    return raw + b"\x00" * (NAME_SIZE - len(raw))


def parse_entries(index: bytes, encoding: str = "cp932") -> List[IsaEntry]:
    entries: List[IsaEntry] = []
    for n, pos in enumerate(range(0, len(index), ENTRY_SIZE)):
        raw = index[pos:pos + ENTRY_SIZE]
        raw_name = raw[:NAME_SIZE].split(b"\x00", 1)[0]
        if not raw_name:
            continue
        name = raw_name.decode(encoding, errors="replace").replace("\\", "/")
        offset = _u32le(raw, 0x24)
        size = _u32le(raw, 0x28)
        reserved = _u32le(raw, 0x2C)
        entries.append(IsaEntry(index=n, name=name, offset=offset, size=size, reserved=reserved))
    return entries


def build_index(entries: List[IsaEntry], encoding: str = "cp932") -> bytearray:
    index = bytearray()
    for e in entries:
        raw = bytearray(ENTRY_SIZE)
        raw[:NAME_SIZE] = encode_entry_name(e.name, encoding)
        raw[0x24:0x28] = _p32le(e.offset)
        raw[0x28:0x2C] = _p32le(e.size)
        raw[0x2C:0x30] = _p32le(e.reserved)
        index += raw
    return index


def read_archive(path: Path, encoding: str = "cp932") -> tuple[int, int, List[IsaEntry], bytes]:
    data = path.read_bytes()
    if len(data) < HEADER_SIZE:
        raise ValueError("file too small")
    if data[:12] != MAGIC:
        raise ValueError(f"bad magic: {data[:12]!r}")

    count_flag = _u32le(data, 0x0C)
    count = count_flag & 0xFFFF
    flags = count_flag >> 16
    index_size = count * ENTRY_SIZE
    index_off = HEADER_SIZE
    index_end = index_off + index_size
    if index_end > len(data):
        raise ValueError(f"bad index range: end=0x{index_end:X}, file_size=0x{len(data):X}")

    index = bytearray(data[index_off:index_end])
    if should_decode(flags):
        decode_index_table(index)

    entries = parse_entries(index, encoding=encoding)
    return count, flags, entries, data


def write_manifest(out_dir: Path, source: Path, count: int, flags: int, entries: List[IsaEntry]) -> Path:
    manifest = {
        "format": "ISM ARCHIVED ISA",
        "source": str(source),
        "count": count,
        "flags": flags,
        "entry_size": ENTRY_SIZE,
        "name_size": NAME_SIZE,
        "entries": [asdict(e) for e in entries],
    }
    path = out_dir / "_isa_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_manifest(path: Path) -> tuple[int, List[IsaEntry]]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    flags = int(obj.get("flags", DEFAULT_FLAGS))
    entries = []
    for i, item in enumerate(obj["entries"]):
        entries.append(IsaEntry(
            index=int(item.get("index", i)),
            name=str(item["name"]).replace("\\", "/"),
            offset=int(item.get("offset", 0)),
            size=int(item.get("size", 0)),
            reserved=int(item.get("reserved", 0)),
        ))
    return flags, entries


def unpack_isa(input_path: Path, output_dir: Path, encoding: str = "cp932", no_manifest: bool = False) -> None:
    count, flags, entries, data = read_archive(input_path, encoding=encoding)
    output_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    bad = 0
    for e in entries:
        if e.offset + e.size > len(data):
            print(f"[BAD] #{e.index:04d} {e.name}: offset=0x{e.offset:X}, size=0x{e.size:X}, file_size=0x{len(data):X}")
            bad += 1
            continue
        rel = sanitize_archive_name(e.name)
        if not rel:
            print(f"[BAD] #{e.index:04d}: empty/suspicious name {e.name!r}")
            bad += 1
            continue
        out_path = output_dir / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data[e.offset:e.offset + e.size])
        print(f"[OK] #{e.index:04d} {e.name}  offset=0x{e.offset:X}  size=0x{e.size:X}")
        ok += 1

    if not no_manifest:
        manifest = write_manifest(output_dir, input_path, count, flags, entries)
        print(f"[MANIFEST] {manifest}")

    print(f"\narchive={input_path}")
    print(f"count={count}")
    print(f"flags=0x{flags:04X}")
    print(f"index_size=0x{count * ENTRY_SIZE:X}")
    print(f"index_decode={should_decode(flags)}")
    print(f"ok={ok}, bad={bad}")


def iter_files_for_pack(input_dir: Path, manifest_entries: Optional[List[IsaEntry]]) -> List[IsaEntry]:
    if manifest_entries is not None:
        # Keep original order and original archive names.
        return [IsaEntry(index=i, name=e.name, offset=0, size=0, reserved=e.reserved) for i, e in enumerate(manifest_entries)]

    files = []
    for p in input_dir.rglob("*"):
        if p.is_file() and p.name != "_isa_manifest.json":
            rel = p.relative_to(input_dir).as_posix()
            files.append(rel)
    files.sort(key=lambda s: s.upper())
    return [IsaEntry(index=i, name=name, offset=0, size=0, reserved=0) for i, name in enumerate(files)]


def pack_isa(input_dir: Path, output_path: Path, manifest_path: Optional[Path], flags: Optional[int], encoding: str = "cp932") -> None:
    manifest_entries: Optional[List[IsaEntry]] = None
    manifest_flags: Optional[int] = None
    if manifest_path is not None:
        manifest_flags, manifest_entries = load_manifest(manifest_path)
    elif (input_dir / "_isa_manifest.json").exists():
        manifest_flags, manifest_entries = load_manifest(input_dir / "_isa_manifest.json")

    use_flags = flags if flags is not None else (manifest_flags if manifest_flags is not None else DEFAULT_FLAGS)
    entries = iter_files_for_pack(input_dir, manifest_entries)

    # Validate files and compute offsets. Keep 0x10 + index first, exactly like original layout.
    count = len(entries)
    data_start = HEADER_SIZE + count * ENTRY_SIZE
    cur = data_start
    file_payloads: List[bytes] = []

    for i, e in enumerate(entries):
        src = input_dir / e.name.replace("/", os.sep)
        if not src.exists():
            # Allow sanitized extraction names if original contains Windows-forbidden chars.
            src = input_dir / sanitize_archive_name(e.name).replace("/", os.sep)
        if not src.is_file():
            raise FileNotFoundError(f"missing file for archive entry: {e.name!r} -> {src}")
        payload = src.read_bytes()
        e.index = i
        e.offset = cur
        e.size = len(payload)
        file_payloads.append(payload)
        cur += len(payload)

    index = build_index(entries, encoding=encoding)
    if should_decode(use_flags):
        decode_index_table(index)  # symmetric: plain -> encoded

    header = bytearray(HEADER_SIZE)
    header[:12] = MAGIC
    header[0x0C:0x10] = _p32le((use_flags << 16) | count)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        f.write(header)
        f.write(index)
        for payload in file_payloads:
            f.write(payload)

    print(f"[PACKED] {output_path}")
    print(f"count={count}")
    print(f"flags=0x{use_flags:04X}")
    print(f"index_decode={should_decode(use_flags)}")
    print(f"size=0x{output_path.stat().st_size:X}")


def list_isa(input_path: Path, encoding: str = "cp932") -> None:
    count, flags, entries, data = read_archive(input_path, encoding=encoding)
    print(f"archive={input_path}")
    print(f"count={count}")
    print(f"flags=0x{flags:04X}")
    print(f"index_decode={should_decode(flags)}")
    print()
    for e in entries:
        status = "OK" if e.offset + e.size <= len(data) else "BAD"
        print(f"[{status}] #{e.index:04d} {e.name:<36} offset=0x{e.offset:08X} size=0x{e.size:08X} reserved=0x{e.reserved:08X}")


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ISM ARCHIVED .ISA unpack/repack tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("unpack", help="unpack ISA archive")
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--encoding", default="cp932")
    p.add_argument("--no-manifest", action="store_true")

    p = sub.add_parser("pack", help="pack directory into ISA archive")
    p.add_argument("input_dir", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--manifest", type=Path, default=None, help="manifest generated by unpack; default: input_dir/_isa_manifest.json if exists")
    p.add_argument("--flags", type=lambda s: int(s, 0), default=None, help="archive flags, e.g. 0x8001")
    p.add_argument("--encoding", default="cp932")

    p = sub.add_parser("list", help="list ISA archive entries")
    p.add_argument("input", type=Path)
    p.add_argument("--encoding", default="cp932")

    args = ap.parse_args(argv)
    if args.cmd == "unpack":
        unpack_isa(args.input, args.output, encoding=args.encoding, no_manifest=args.no_manifest)
    elif args.cmd == "pack":
        pack_isa(args.input_dir, args.output, manifest_path=args.manifest, flags=args.flags, encoding=args.encoding)
    elif args.cmd == "list":
        list_isa(args.input, encoding=args.encoding)
    else:
        ap.error("unknown command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
