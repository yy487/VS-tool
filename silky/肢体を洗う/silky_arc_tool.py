#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Silky's ARC archive unpack/pack tool.

Format confirmed against Garbro ArcARC.cs and the supplied script.arc/body.exe.c:
    uint32le count
    repeated count times:
        char name[0x20]   # NUL-terminated, padded with 0x00
        uint32le offset
        uint32le size
    raw file data at offsets above

This archive is flat and does not compress/encrypt entries at the ARC layer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, List

NAME_LEN = 0x20
REC_LEN = NAME_LEN + 8
COUNT_LEN = 4
DEFAULT_NAME_ENCODING = "cp932"
MANIFEST_NAME = "arc_manifest.json"


@dataclass
class ArcEntry:
    index: int
    name: str
    offset: int
    size: int
    file: str
    sha256: str | None = None


def _decode_name(raw: bytes, encoding: str) -> str:
    raw = raw.split(b"\x00", 1)[0]
    if not raw:
        raise ValueError("empty entry name")
    return raw.decode(encoding, errors="strict")


def _encode_name(name: str, encoding: str) -> bytes:
    raw = name.encode(encoding, errors="strict")
    if len(raw) == 0:
        raise ValueError("empty entry name")
    if len(raw) >= NAME_LEN:
        # The original reader uses a fixed 0x20 field and C-string semantics.
        # Keep one byte for NUL to avoid a non-terminated name field.
        raise ValueError(f"entry name too long for 0x20-byte field: {name!r} ({len(raw)} bytes)")
    return raw + b"\x00" * (NAME_LEN - len(raw))


def _safe_filename(name: str) -> str:
    # ARC itself is flat. Keep Windows-hostile/path separator characters out of disk filenames.
    safe = name.replace("/", "_").replace("\\", "_").replace(":", "_")
    safe = re.sub(r"[\x00-\x1f<>\"|?*]", "_", safe)
    safe = safe.strip(" .")
    return safe or "unnamed"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_arc(path: Path, encoding: str = DEFAULT_NAME_ENCODING) -> tuple[list[ArcEntry], bytes]:
    data = path.read_bytes()
    if len(data) < COUNT_LEN:
        raise ValueError("file too small")

    count = struct.unpack_from("<I", data, 0)[0]
    if count == 0:
        raise ValueError("entry count is zero")

    index_size = count * REC_LEN
    data_start = COUNT_LEN + index_size
    if data_start > len(data):
        raise ValueError(f"index exceeds file size: data_start=0x{data_start:X}, file_size=0x{len(data):X}")

    entries: list[ArcEntry] = []
    seen_offsets: set[int] = set()
    seen_names: set[str] = set()

    pos = COUNT_LEN
    for i in range(count):
        raw_name = data[pos:pos + NAME_LEN]
        name = _decode_name(raw_name, encoding)
        offset, size = struct.unpack_from("<II", data, pos + NAME_LEN)

        if offset < data_start:
            raise ValueError(f"entry #{i} {name!r}: offset 0x{offset:X} points inside header/index")
        if offset + size > len(data):
            raise ValueError(f"entry #{i} {name!r}: range exceeds file size")
        if offset in seen_offsets:
            raise ValueError(f"entry #{i} {name!r}: duplicate offset 0x{offset:X}")
        if name in seen_names:
            raise ValueError(f"entry #{i} {name!r}: duplicate name")

        seen_offsets.add(offset)
        seen_names.add(name)
        entries.append(ArcEntry(index=i, name=name, offset=offset, size=size, file=_safe_filename(name)))
        pos += REC_LEN

    return entries, data


def unpack_arc(arc_path: Path, out_dir: Path, encoding: str = DEFAULT_NAME_ENCODING, overwrite: bool = False) -> None:
    entries, data = read_arc(arc_path, encoding)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict = {
        "format": "Silky ARC",
        "source": str(arc_path),
        "name_encoding": encoding,
        "count": len(entries),
        "entry_record_size": REC_LEN,
        "name_size": NAME_LEN,
        "entries": [],
    }

    used_files: set[str] = set()
    for ent in entries:
        chunk = data[ent.offset:ent.offset + ent.size]
        filename = ent.file
        if filename in used_files:
            stem = Path(filename).stem
            suffix = Path(filename).suffix
            filename = f"{stem}_{ent.index:04d}{suffix}"
        used_files.add(filename)
        ent.file = filename
        ent.sha256 = _sha256(chunk)

        dst = out_dir / filename
        if dst.exists() and not overwrite:
            raise FileExistsError(f"refusing to overwrite existing file: {dst}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(chunk)
        manifest["entries"].append(asdict(ent))

    manifest_path = out_dir / MANIFEST_NAME
    if manifest_path.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing manifest: {manifest_path}")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"unpacked {len(entries)} entries -> {out_dir}")
    print(f"manifest: {manifest_path}")


def _load_manifest(input_dir: Path) -> dict:
    manifest_path = input_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def pack_arc(input_dir: Path, out_arc: Path, encoding: str | None = None, overwrite: bool = False) -> None:
    manifest = _load_manifest(input_dir)
    entries_meta = manifest.get("entries")
    if not isinstance(entries_meta, list) or not entries_meta:
        raise ValueError("manifest has no entries")

    encoding = encoding or manifest.get("name_encoding") or DEFAULT_NAME_ENCODING
    entries_meta = sorted(entries_meta, key=lambda x: int(x["index"]))

    count = len(entries_meta)
    data_start = COUNT_LEN + count * REC_LEN
    offset = data_start
    index_records: list[bytes] = []
    payloads: list[bytes] = []

    names_seen: set[str] = set()
    for meta in entries_meta:
        name = str(meta["name"])
        rel_file = str(meta.get("file") or _safe_filename(name))
        src = input_dir / rel_file
        if not src.exists():
            raise FileNotFoundError(f"missing entry file for {name!r}: {src}")
        if name in names_seen:
            raise ValueError(f"duplicate entry name in manifest: {name!r}")
        names_seen.add(name)

        blob = src.read_bytes()
        index_records.append(_encode_name(name, encoding) + struct.pack("<II", offset, len(blob)))
        payloads.append(blob)
        offset += len(blob)

    if out_arc.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing archive: {out_arc}")
    out_arc.parent.mkdir(parents=True, exist_ok=True)
    with out_arc.open("wb") as f:
        f.write(struct.pack("<I", count))
        for rec in index_records:
            f.write(rec)
        for blob in payloads:
            f.write(blob)

    print(f"packed {count} entries -> {out_arc}")
    print(f"size: {out_arc.stat().st_size} bytes")


def info_arc(arc_path: Path, encoding: str = DEFAULT_NAME_ENCODING) -> None:
    entries, data = read_arc(arc_path, encoding)
    data_start = COUNT_LEN + len(entries) * REC_LEN
    print(f"archive: {arc_path}")
    print(f"size: {len(data)} bytes")
    print(f"count: {len(entries)}")
    print(f"index size: {len(entries) * REC_LEN} bytes")
    print(f"data start: 0x{data_start:X}")
    print()
    print(f"{'idx':>4}  {'offset':>10}  {'size':>10}  name")
    for ent in entries:
        print(f"{ent.index:4d}  0x{ent.offset:08X}  {ent.size:10d}  {ent.name}")


def verify_roundtrip(arc_path: Path, encoding: str = DEFAULT_NAME_ENCODING) -> None:
    import tempfile
    original = arc_path.read_bytes()
    with tempfile.TemporaryDirectory(prefix="silky_arc_") as td:
        tmp = Path(td)
        unpack_arc(arc_path, tmp / "unpack", encoding=encoding, overwrite=True)
        repacked = tmp / "repacked.arc"
        pack_arc(tmp / "unpack", repacked, encoding=encoding, overwrite=True)
        new = repacked.read_bytes()
    if original == new:
        print("roundtrip OK: repacked archive is byte-identical")
    else:
        print("roundtrip differs: structure is valid, but bytes are not identical", file=sys.stderr)
        print(f"original sha256: {_sha256(original)}", file=sys.stderr)
        print(f"repacked sha256: {_sha256(new)}", file=sys.stderr)
        raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Unpack/pack Silky's .arc archives")
    p.add_argument("--name-encoding", default=DEFAULT_NAME_ENCODING, help="entry name encoding, default: cp932")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_info = sub.add_parser("info", help="show archive index")
    p_info.add_argument("arc", type=Path)

    p_unpack = sub.add_parser("unpack", help="unpack archive")
    p_unpack.add_argument("arc", type=Path)
    p_unpack.add_argument("out_dir", type=Path)
    p_unpack.add_argument("--overwrite", action="store_true")

    p_pack = sub.add_parser("pack", help="pack directory using arc_manifest.json")
    p_pack.add_argument("input_dir", type=Path)
    p_pack.add_argument("out_arc", type=Path)
    p_pack.add_argument("--overwrite", action="store_true")

    p_verify = sub.add_parser("verify", help="unpack then repack and compare bytes")
    p_verify.add_argument("arc", type=Path)
    return p


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "info":
        info_arc(args.arc, args.name_encoding)
    elif args.cmd == "unpack":
        unpack_arc(args.arc, args.out_dir, args.name_encoding, args.overwrite)
    elif args.cmd == "pack":
        pack_arc(args.input_dir, args.out_arc, args.name_encoding, args.overwrite)
    elif args.cmd == "verify":
        verify_roundtrip(args.arc, args.name_encoding)
    else:
        raise AssertionError(args.cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
