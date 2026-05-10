#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IFLS archive unpack/pack tool for Silky engine archives.

Structure verified against Garbro ArcIFL.cs and Angel.exe:
  00: 'IFLS' little endian
  04: data_offset, i.e. start of file payload area
  08: count (Garbro uses it; Angel.exe mostly derives count from data_offset)
  0C: index entries, 0x18 bytes each
      +00 name field, 0x10 bytes, NUL padded. Engine lookup is 8.3-like and
          effectively hashes/compares the first 0x0C bytes.
      +10 uint32 offset
      +14 uint32 size
  data: raw entries. Some entries may be wrapped as:
      common resources: 'CMP_' + uint32 unpacked_size + uint32 reserved + LZSS payload
      .snc scripts used by Angel.exe: 'CMP_' + uint32 unpacked_size + LZSS payload
      (Angel/Garbro pass input from offset + 0x0C, so this third dword exists.)

Default unpack mode follows Garbro for general resources:
  - entries with CMP_ are LZSS-decompressed, except .grd unless --decompress-grd
  - use --raw to extract exact stored bytes

Default pack mode stores files raw. Use --compress to wrap non-.grd files in CMP_.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

MAGIC = b"IFLS"
CMP_MAGIC = b"CMP_"
ENTRY_SIZE = 0x18
NAME_SIZE = 0x10
FRAME_SIZE = 0x1000
FRAME_INIT_POS = 0xFEE
FRAME_FILL = 0x20


@dataclass
class IflEntry:
    name: str
    offset: int
    size: int
    packed: bool = False
    unpacked_size: int | None = None


def read_c_string(raw: bytes) -> str:
    raw = raw.split(b"\x00", 1)[0]
    return raw.decode("cp932", errors="replace")


def encode_name(name: str) -> bytes:
    base = os.path.basename(name).replace("/", "\\")
    raw = base.encode("cp932")
    if len(raw) > NAME_SIZE:
        raise ValueError(f"IFL name field is max {NAME_SIZE} bytes: {name!r} -> {len(raw)} bytes")
    return raw.ljust(NAME_SIZE, b"\x00")


def parse_ifl(path: Path) -> tuple[list[IflEntry], int, int]:
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != MAGIC:
        raise ValueError(f"not an IFLS archive: {path}")
    data_offset, count = struct.unpack_from("<II", data, 4)
    if data_offset <= 12 or data_offset > len(data):
        raise ValueError(f"bad data_offset: 0x{data_offset:X}")
    if (data_offset - 12) % ENTRY_SIZE != 0:
        raise ValueError(f"index area size is not aligned to 0x18: 0x{data_offset - 12:X}")
    real_count = (data_offset - 12) // ENTRY_SIZE
    if count != real_count:
        # Angel.exe derives the effective count from data_offset; keep parsing by real_count.
        pass
    entries: list[IflEntry] = []
    for i in range(real_count):
        p = 12 + i * ENTRY_SIZE
        name = read_c_string(data[p:p + NAME_SIZE])
        if not name:
            raise ValueError(f"empty entry name at index {i}")
        offset, size = struct.unpack_from("<II", data, p + 0x10)
        if offset < data_offset or offset + size > len(data):
            raise ValueError(f"entry outside archive: {name} off=0x{offset:X} size=0x{size:X}")
        packed = False
        unpacked_size = None
        if size >= 12 and data[offset:offset + 4] == CMP_MAGIC:
            packed = True
            unpacked_size = struct.unpack_from("<I", data, offset + 4)[0]
        entries.append(IflEntry(name, offset, size, packed, unpacked_size))
    return entries, data_offset, count


def lzss_decompress(src: bytes, expected_size: int | None = None) -> bytes:
    frame = bytearray([FRAME_FILL]) * FRAME_SIZE
    frame_pos = FRAME_INIT_POS
    flags = 0
    out = bytearray()
    i = 0
    n = len(src)
    while i < n:
        flags >>= 1
        if (flags & 0x100) == 0:
            flags = src[i] | 0xFF00
            i += 1
        if flags & 1:
            if i >= n:
                break
            b = src[i]
            i += 1
            out.append(b)
            frame[frame_pos] = b
            frame_pos = (frame_pos + 1) & 0xFFF
        else:
            if i + 1 >= n:
                break
            lo = src[i]
            hi = src[i + 1]
            i += 2
            pos = lo | ((hi & 0xF0) << 4)
            length = (hi & 0x0F) + 3
            for k in range(length):
                b = frame[(pos + k) & 0xFFF]
                out.append(b)
                frame[frame_pos] = b
                frame_pos = (frame_pos + 1) & 0xFFF
                if expected_size is not None and len(out) >= expected_size:
                    return bytes(out[:expected_size])
    if expected_size is not None and len(out) != expected_size:
        raise ValueError(f"LZSS unpack size mismatch: got {len(out)}, expected {expected_size}")
    return bytes(out)



def _lzss_key(frame: bytearray, pos: int) -> int:
    return frame[pos] | (frame[(pos + 1) & 0xFFF] << 8) | (frame[(pos + 2) & 0xFFF] << 16)


class _LzssMatchIndex:
    """Small rolling hash index for the 0x1000-byte LZSS frame.

    The old encoder scanned all 4096 frame positions for every token.  This
    index keeps a bucket of frame positions for each 3-byte prefix and updates
    only the three affected prefixes whenever one frame byte changes.
    """

    __slots__ = ("frame", "pos_key", "buckets")

    def __init__(self, frame: bytearray):
        self.frame = frame
        self.pos_key = [0] * FRAME_SIZE
        self.buckets: dict[int, set[int]] = {}
        for pos in range(FRAME_SIZE):
            key = _lzss_key(frame, pos)
            self.pos_key[pos] = key
            self.buckets.setdefault(key, set()).add(pos)

    def _refresh_prefix_at(self, pos: int) -> None:
        pos &= 0xFFF
        old = self.pos_key[pos]
        bucket = self.buckets.get(old)
        if bucket is not None:
            bucket.discard(pos)
            if not bucket:
                del self.buckets[old]
        new = _lzss_key(self.frame, pos)
        self.pos_key[pos] = new
        self.buckets.setdefault(new, set()).add(pos)

    def put_byte(self, frame_pos: int, value: int) -> None:
        frame_pos &= 0xFFF
        self.frame[frame_pos] = value
        # Only prefixes starting at frame_pos-2, frame_pos-1, frame_pos changed.
        self._refresh_prefix_at(frame_pos - 2)
        self._refresh_prefix_at(frame_pos - 1)
        self._refresh_prefix_at(frame_pos)

    def find_longest(self, frame_pos: int, look: bytes, max_len: int = 18, candidate_cap: int = 384) -> tuple[int, int]:
        limit = min(max_len, len(look))
        if limit < 3:
            return 0, 0
        key = look[0] | (look[1] << 8) | (look[2] << 16)
        candidates = self.buckets.get(key)
        if not candidates:
            return 0, 0

        best_pos = 0
        best_len = 0

        # Prefer nearby/backward positions.  It improves compression for runs and
        # keeps pathological buckets such as b'   ' bounded without hurting
        # decoder compatibility.
        ordered = sorted(candidates, key=lambda p: ((frame_pos - p) & 0xFFF))
        if len(ordered) > candidate_cap:
            ordered = ordered[:candidate_cap]

        frame = self.frame
        for pos in ordered:
            # Exact decoder simulation.  Avoid copying the 4 KiB frame per
            # candidate: at most 18 bytes are written during one match, so a tiny
            # overlay dictionary is enough to model overlapping references.
            overlay: dict[int, int] = {}
            fp = frame_pos
            length = 0
            while length < limit:
                rp = (pos + length) & 0xFFF
                b = overlay.get(rp, frame[rp])
                if b != look[length]:
                    break
                overlay[fp] = b
                fp = (fp + 1) & 0xFFF
                length += 1

            if length > best_len:
                best_len = length
                best_pos = pos
                if best_len == limit:
                    break

        if best_len < 3:
            return 0, 0
        return best_pos, best_len


def lzss_compress(raw: bytes, level: int = 0) -> bytes:
    """Fast greedy encoder compatible with Angel/Silky CMP_ LZSS.

    level controls candidate search breadth:
      0 = fastest, slightly larger output
      1 = default
      2 = slower, usually a little smaller
    """
    if level <= 0:
        candidate_cap = 32
    elif level == 1:
        candidate_cap = 96
    else:
        candidate_cap = 4096

    frame = bytearray([FRAME_FILL]) * FRAME_SIZE
    index = _LzssMatchIndex(frame)
    frame_pos = FRAME_INIT_POS
    out = bytearray()
    i = 0
    raw_len = len(raw)

    while i < raw_len:
        flag_pos = len(out)
        out.append(0)
        flags = 0
        for bit in range(8):
            if i >= raw_len:
                break
            pos, length = index.find_longest(frame_pos, raw[i:i + 18], candidate_cap=candidate_cap)
            if length >= 3:
                out.append(pos & 0xFF)
                out.append(((pos >> 4) & 0xF0) | ((length - 3) & 0x0F))
                for k in range(length):
                    index.put_byte(frame_pos, raw[i + k])
                    frame_pos = (frame_pos + 1) & 0xFFF
                i += length
            else:
                b = raw[i]
                i += 1
                flags |= (1 << bit)
                out.append(b)
                index.put_byte(frame_pos, b)
                frame_pos = (frame_pos + 1) & 0xFFF
        out[flag_pos] = flags
    return bytes(out)


def cmp_payload_offset(name: str) -> int:
    """Return CMP_ header size for this engine.

    Angel.exe's SNC loader calls FUN_0040ef30(local_c + 2, ..., file_size - 8),
    so .snc entries are CMP_ + u32 unpacked_size + LZSS payload.
    Other Garbro-style resources usually carry a reserved u32 and start at +12.
    """
    return 8 if name.lower().endswith(".snc") else 12

def unpack_archive(path: Path, out_dir: Path, raw: bool = False, decompress_grd: bool = False) -> None:
    blob = path.read_bytes()
    entries, data_offset, stored_count = parse_ifl(path)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": str(path),
        "data_offset": data_offset,
        "stored_count": stored_count,
        "count": len(entries),
        "entries": [],
    }
    for e in entries:
        stored = blob[e.offset:e.offset + e.size]
        payload = stored
        did_decompress = False
        if (not raw and e.packed and e.unpacked_size is not None
                and (decompress_grd or not e.name.lower().endswith(".grd"))):
            payload = lzss_decompress(stored[cmp_payload_offset(e.name):], e.unpacked_size)
            did_decompress = True
        out_path = out_dir / e.name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(payload)
        item = asdict(e)
        item["extracted_size"] = len(payload)
        item["decompressed"] = did_decompress
        manifest["entries"].append(item)
    (out_dir / "__ifl_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def iter_input_files(in_dir: Path) -> list[Path]:
    files = []
    for p in sorted(in_dir.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.name != "__ifl_manifest.json":
            files.append(p)
    return files


def build_archive(in_dir: Path, out_path: Path, compress: bool = False, compress_grd: bool = False, use_manifest_order: bool = True, compress_level: int = 0) -> None:
    files: list[Path]
    manifest_path = in_dir / "__ifl_manifest.json"
    if use_manifest_order and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = []
        seen = set()
        for ent in manifest.get("entries", []):
            name = ent.get("name")
            if not name:
                continue
            p = in_dir / name
            if p.is_file():
                files.append(p)
                seen.add(p.name.lower())
        for p in iter_input_files(in_dir):
            if p.name.lower() not in seen:
                files.append(p)
    else:
        files = iter_input_files(in_dir)

    if not files:
        raise ValueError(f"no files to pack in {in_dir}")
    if len(files) > 0x7FFFFFFF:
        raise ValueError("too many files")

    data_offset = 12 + len(files) * ENTRY_SIZE
    index = bytearray()
    data = bytearray()
    current = data_offset
    packed_info = []
    for p in files:
        name = p.name
        raw = p.read_bytes()
        stored = raw
        packed = False
        if compress and (compress_grd or not name.lower().endswith(".grd")):
            comp = lzss_compress(raw, level=compress_level)
            if name.lower().endswith(".snc"):
                stored = CMP_MAGIC + struct.pack("<I", len(raw)) + comp
            else:
                stored = (CMP_MAGIC + struct.pack("<I", len(raw)) + comp) if name.lower().endswith(".snc") else (CMP_MAGIC + struct.pack("<II", len(raw), 0) + comp)
            packed = True
        index += encode_name(name)
        index += struct.pack("<II", current, len(stored))
        data += stored
        packed_info.append((name, len(raw), len(stored), packed))
        current += len(stored)

    out = bytearray()
    out += MAGIC
    out += struct.pack("<II", data_offset, len(files))
    out += index
    out += data
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(out)

    info = {
        "output": str(out_path),
        "count": len(files),
        "data_offset": data_offset,
        "entries": [
            {"name": n, "input_size": a, "stored_size": b, "compressed": c}
            for n, a, b, c in packed_info
        ],
    }
    out_path.with_suffix(out_path.suffix + ".pack.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")


def list_archive(path: Path) -> None:
    entries, data_offset, stored_count = parse_ifl(path)
    print(f"IFLS: {path}")
    print(f"data_offset=0x{data_offset:X}, stored_count={stored_count}, effective_count={len(entries)}")
    for i, e in enumerate(entries):
        flag = " CMP" if e.packed else ""
        extra = f" -> {e.unpacked_size}" if e.unpacked_size is not None else ""
        print(f"{i:04d}  off=0x{e.offset:08X} size=0x{e.size:08X}{flag}{extra}  {e.name}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Silky IFLS archive unpack/pack tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="list archive entries")
    p_list.add_argument("archive", type=Path)

    p_unpack = sub.add_parser("unpack", help="unpack archive")
    p_unpack.add_argument("archive", type=Path)
    p_unpack.add_argument("out_dir", type=Path)
    p_unpack.add_argument("--raw", action="store_true", help="extract exact stored bytes, do not decompress CMP_ entries")
    p_unpack.add_argument("--decompress-grd", action="store_true", help="also decompress .grd CMP_ entries")

    p_pack = sub.add_parser("pack", help="pack a folder into IFLS archive")
    p_pack.add_argument("in_dir", type=Path)
    p_pack.add_argument("archive", type=Path)
    p_pack.add_argument("--compress", action="store_true", help="wrap non-.grd files as CMP_ + LZSS")
    p_pack.add_argument("--compress-grd", action="store_true", help="allow .grd compression too")
    p_pack.add_argument("--no-manifest-order", action="store_true", help="ignore __ifl_manifest.json order")
    p_pack.add_argument("--compress-level", type=int, choices=(0, 1, 2), default=0,
                        help="LZSS search level: 0 fastest, 1 balanced, 2 smallest/slower")

    p_test = sub.add_parser("test-lzss", help="self-test LZSS roundtrip on a file")
    p_test.add_argument("file", type=Path)

    args = ap.parse_args(argv)
    if args.cmd == "list":
        list_archive(args.archive)
    elif args.cmd == "unpack":
        unpack_archive(args.archive, args.out_dir, args.raw, args.decompress_grd)
    elif args.cmd == "pack":
        build_archive(args.in_dir, args.archive, args.compress, args.compress_grd, not args.no_manifest_order, args.compress_level)
    elif args.cmd == "test-lzss":
        raw = args.file.read_bytes()
        comp = lzss_compress(raw)
        dec = lzss_decompress(comp, len(raw))
        print(json.dumps({"input": len(raw), "compressed": len(comp), "ok": dec == raw}, ensure_ascii=False))
        return 0 if dec == raw else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
