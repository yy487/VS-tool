#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI6WIN ARC 通用读写模块。

结构依据 Garbro ArcAi6Win.cs：
    int32le count
    repeat count:
        byte name[0x104]       # 文件名逐字节加密，明文 CP932，可包含 /
        uint32be size          # 包内存储大小
        uint32be unpacked_size # 解压后大小
        uint32be offset        # 数据偏移
    data blobs...

当 size != unpacked_size 时，数据使用 GameRes.Compression.LzssStream 兼容 LZSS。
本模块同时提供兼容解压器和保守压缩器，便于批量解包/封包。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Optional
import hashlib
import json
import os
import struct

CP932 = "cp932"
AI6_NAME_FIELD_SIZE = 0x104
DEFAULT_COMPRESSED_EXTS = {".mes", ".lib", ".a", ".a6", ".msk", ".x"}


@dataclass
class Ai6ArcEntry:
    """ARC 索引条目。size 是包内存储大小，unpacked_size 是解压后大小。"""
    name: str
    offset: int
    size: int
    unpacked_size: int
    packed: bool
    sha1_stored: str | None = None
    sha1_unpacked: str | None = None

    @classmethod
    def from_json(cls, obj: dict) -> "Ai6ArcEntry":
        return cls(
            name=obj["name"],
            offset=int(obj.get("offset", 0)),
            size=int(obj.get("size", 0)),
            unpacked_size=int(obj.get("unpacked_size", obj.get("unpackedSize", 0))),
            packed=bool(obj.get("packed", obj.get("compressed", False))),
            sha1_stored=obj.get("sha1_stored"),
            sha1_unpacked=obj.get("sha1_unpacked"),
        )

    def to_json(self) -> dict:
        return asdict(self)


def sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def normalize_arc_name(name: str) -> str:
    """包内路径统一为 /，避免 Windows 反斜杠写入索引。"""
    return name.replace("\\", "/").strip("/")


def read_u32be(buf: bytes | bytearray, off: int) -> int:
    return struct.unpack_from(">I", buf, off)[0]


def write_u32be(value: int) -> bytes:
    return struct.pack(">I", value & 0xFFFFFFFF)


def decode_name(raw: bytes, encoding: str = CP932) -> str:
    """解密 0x104 字节文件名字段。"""
    if len(raw) != AI6_NAME_FIELD_SIZE:
        raise ValueError(f"bad name field size: {len(raw)}")
    name_length = raw.find(b"\x00")
    if name_length == 0:
        raise ValueError("empty name field")
    if name_length < 0:
        name_length = AI6_NAME_FIELD_SIZE

    out = bytearray(raw[:name_length])
    key = (name_length + 1) & 0xFF
    for i in range(name_length):
        out[i] = (out[i] - key) & 0xFF
        key = (key - 1) & 0xFF
    return out.decode(encoding)


def encode_name(name: str, encoding: str = CP932) -> bytes:
    """加密文件名字段。保留 0 结尾，兼容原索引读取逻辑。"""
    name = normalize_arc_name(name)
    raw_name = name.encode(encoding)
    if len(raw_name) >= AI6_NAME_FIELD_SIZE:
        raise ValueError(
            f"file name too long for AI6WIN ARC: {name!r} -> {len(raw_name)} bytes; "
            f"need <= {AI6_NAME_FIELD_SIZE - 1}"
        )

    name_buf = bytearray(AI6_NAME_FIELD_SIZE)
    name_buf[:len(raw_name)] = raw_name
    key = (len(raw_name) + 1) & 0xFF
    for i in range(len(raw_name)):
        name_buf[i] = (name_buf[i] + key) & 0xFF
        key = (key - 1) & 0xFF
    return bytes(name_buf)


def read_index(arc_path: str | Path, encoding: str = CP932) -> list[Ai6ArcEntry]:
    data = Path(arc_path).read_bytes()
    if len(data) < 4:
        raise ValueError("file too small")

    count = struct.unpack_from("<i", data, 0)[0]
    if count <= 0 or count > 1_000_000:
        raise ValueError(f"insane entry count: {count}")

    index_size = count * (AI6_NAME_FIELD_SIZE + 12)
    min_data_offset = 4 + index_size
    if min_data_offset > len(data):
        raise ValueError("index exceeds file size")

    entries: list[Ai6ArcEntry] = []
    pos = 4
    for i in range(count):
        name = decode_name(data[pos:pos + AI6_NAME_FIELD_SIZE], encoding=encoding)
        pos += AI6_NAME_FIELD_SIZE

        size = read_u32be(data, pos)
        unpacked_size = read_u32be(data, pos + 4)
        offset = read_u32be(data, pos + 8)
        pos += 12

        if offset < min_data_offset or offset + size > len(data):
            raise ValueError(
                f"bad placement for entry[{i}] {name!r}: offset=0x{offset:X}, size=0x{size:X}"
            )

        entries.append(Ai6ArcEntry(
            name=name,
            offset=offset,
            size=size,
            unpacked_size=unpacked_size,
            packed=(size != unpacked_size),
        ))
    return entries


def read_stored_blob(arc_path: str | Path, entry: Ai6ArcEntry) -> bytes:
    with open(arc_path, "rb") as f:
        f.seek(entry.offset)
        return f.read(entry.size)


# ---------------------------------------------------------------------------
# LZSS：兼容 GameRes.Compression.LzssStream
# ---------------------------------------------------------------------------

def lzss_decompress(src: bytes) -> bytes:
    """解压 AI6WIN/AI5WIN/Silky 常见 LZSS 流。"""
    frame_size = 0x1000
    frame = bytearray(frame_size)
    frame_pos = 0xFEE
    frame_mask = frame_size - 1
    out = bytearray()
    p = 0

    while p < len(src):
        ctl = src[p]
        p += 1
        bit = 1
        while bit != 0x100 and p < len(src):
            if ctl & bit:
                b = src[p]
                p += 1
                frame[frame_pos & frame_mask] = b
                frame_pos = (frame_pos + 1) & frame_mask
                out.append(b)
            else:
                if p + 2 > len(src):
                    break
                lo = src[p]
                hi = src[p + 1]
                p += 2
                offset = ((hi & 0xF0) << 4) | lo
                count = 3 + (hi & 0x0F)
                for _ in range(count):
                    b = frame[offset & frame_mask]
                    offset = (offset + 1) & frame_mask
                    frame[frame_pos & frame_mask] = b
                    frame_pos = (frame_pos + 1) & frame_mask
                    out.append(b)
            bit <<= 1
    return bytes(out)


def lzss_compress_literal(src: bytes) -> bytes:
    """全 literal 编码。体积较大，但逻辑最稳。"""
    out = bytearray()
    p = 0
    while p < len(src):
        chunk = src[p:p + 8]
        out.append((1 << len(chunk)) - 1)
        out.extend(chunk)
        p += len(chunk)
    return bytes(out)


def lzss_compress_greedy(src: bytes) -> bytes:
    """简单贪心 LZSS 压缩器，输出流可被 lzss_decompress / 原引擎读取。"""
    n = len(src)
    if n < 3:
        return lzss_compress_literal(src)

    from collections import defaultdict, deque
    pos_map: dict[bytes, deque[int]] = defaultdict(lambda: deque(maxlen=128))

    def add_pos(pos: int) -> None:
        if pos + 3 <= n:
            pos_map[src[pos:pos + 3]].append(pos)

    out = bytearray()
    p = 0
    while p < n:
        ctl_pos = len(out)
        out.append(0)
        ctl = 0

        for bit_index in range(8):
            if p >= n:
                break

            best_pos = -1
            best_len = 0
            if p + 3 <= n:
                candidates = pos_map.get(src[p:p + 3])
                if candidates:
                    min_pos = max(0, p - 0x1000)
                    for q in reversed(candidates):
                        if q < min_pos:
                            continue
                        # 不做前向自重叠匹配，避免编码器复杂化。
                        max_len = min(18, n - p, p - q)
                        if max_len < 3:
                            continue
                        length = 3
                        while length < max_len and src[q + length] == src[p + length]:
                            length += 1
                        if length > best_len:
                            best_len = length
                            best_pos = q
                            if length == 18:
                                break

            if best_len >= 3:
                offset = (0xFEE + best_pos) & 0xFFF
                out.append(offset & 0xFF)
                out.append(((offset >> 4) & 0xF0) | (best_len - 3))
                for k in range(best_len):
                    add_pos(p + k)
                p += best_len
            else:
                ctl |= (1 << bit_index)
                out.append(src[p])
                add_pos(p)
                p += 1

        out[ctl_pos] = ctl
    return bytes(out)


def lzss_compress(src: bytes, mode: str = "greedy") -> bytes:
    if mode == "greedy":
        return lzss_compress_greedy(src)
    if mode == "literal":
        return lzss_compress_literal(src)
    raise ValueError(f"unknown lzss mode: {mode}")


def unpack_entry_data(arc_path: str | Path, entry: Ai6ArcEntry) -> bytes:
    blob = read_stored_blob(arc_path, entry)
    if not entry.packed:
        return blob
    data = lzss_decompress(blob)
    if len(data) != entry.unpacked_size:
        raise ValueError(
            f"LZSS size mismatch for {entry.name}: got {len(data)}, expected {entry.unpacked_size}"
        )
    return data


def should_compress_by_ext(name: str, compressed_exts: set[str] = DEFAULT_COMPRESSED_EXTS) -> bool:
    return Path(name).suffix.lower() in compressed_exts


def prepare_stored_blob(plain: bytes, compress: bool, lzss_mode: str = "greedy") -> tuple[bytes, int, bool]:
    """返回 (stored_blob, unpacked_size, packed_flag)。"""
    if not compress:
        return plain, len(plain), False
    packed = lzss_compress(plain, mode=lzss_mode)
    # 极少数情况下压缩后长度可能等于原长。索引靠 size != unpacked_size 判断是否解压，
    # 此时为了避免误判，退回未压缩存储。
    if len(packed) == len(plain):
        return plain, len(plain), False
    return packed, len(plain), True


def build_arc_bytes(items: list[tuple[str, bytes, int]]) -> bytes:
    """
    构建 ARC。
    items: [(archive_name, stored_blob, unpacked_size), ...]
    """
    count = len(items)
    index_size = count * (AI6_NAME_FIELD_SIZE + 12)
    data_offset = 4 + index_size

    offsets: list[int] = []
    cur = data_offset
    for _, blob, _ in items:
        offsets.append(cur)
        cur += len(blob)
        if cur > 0xFFFFFFFF:
            raise ValueError("archive too large for AI6WIN 32-bit offsets")

    out = bytearray()
    out.extend(struct.pack("<i", count))
    for (name, blob, unpacked_size), off in zip(items, offsets):
        out.extend(encode_name(name))
        out.extend(write_u32be(len(blob)))
        out.extend(write_u32be(unpacked_size))
        out.extend(write_u32be(off))
    for _, blob, _ in items:
        out.extend(blob)
    return bytes(out)


def extract_arc(arc_path: str | Path, out_dir: str | Path, encoding: str = CP932,
                manifest_name: str = "ai6win_manifest.json") -> dict:
    """批量解包 ARC，写出 manifest。"""
    arc_path = Path(arc_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    entries = read_index(arc_path, encoding=encoding)
    manifest_entries: list[dict] = []
    for e in entries:
        stored = read_stored_blob(arc_path, e)
        data = lzss_decompress(stored) if e.packed else stored
        if len(data) != e.unpacked_size:
            raise ValueError(f"unpacked size mismatch: {e.name}: got={len(data)} expected={e.unpacked_size}")

        out_path = out_dir / e.name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)

        e.sha1_stored = sha1_bytes(stored)
        e.sha1_unpacked = sha1_bytes(data)
        manifest_entries.append(e.to_json())

    manifest = {
        "format": "ARC/AI6WIN",
        "encoding": encoding,
        "source_arc": str(arc_path),
        "name_field_size": AI6_NAME_FIELD_SIZE,
        "entries": manifest_entries,
    }
    save_manifest(out_dir / manifest_name, manifest)
    return manifest


def save_manifest(path: str | Path, manifest: dict) -> None:
    Path(path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def load_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def collect_files_for_new_arc(input_dir: Path) -> list[str]:
    names: list[str] = []
    for p in sorted(input_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.name.lower() == "ai6win_manifest.json":
            continue
        names.append(normalize_arc_name(str(p.relative_to(input_dir))))
    return names


def _is_same_as_manifest_file(path: Path, entry: Ai6ArcEntry) -> bool:
    if not path.exists() or not entry.sha1_unpacked:
        return False
    return sha1_bytes(path.read_bytes()) == entry.sha1_unpacked


def pack_arc(input_dir: str | Path, out_arc: str | Path, manifest_path: str | Path | None = None,
             source_arc: str | Path | None = None, encoding: str = CP932,
             compress_policy: str = "manifest", lzss_mode: str = "greedy",
             reuse_stored: bool = True) -> dict:
    """
    批量封包。

    compress_policy:
      - manifest：有 manifest 时沿用原条目的 packed 标记；无 manifest 时按扩展名判断。
      - auto-ext：按扩展名判断。
      - all：全部压缩。
      - none：全部不压缩。
    reuse_stored:
      - source_arc + manifest 存在时，未修改或缺失文件直接复用原 blob，最大限度保持原包。
    """
    input_dir = Path(input_dir)
    out_arc = Path(out_arc)
    out_arc.parent.mkdir(parents=True, exist_ok=True)

    manifest: dict | None = None
    entries: list[Ai6ArcEntry]
    if manifest_path:
        manifest = load_manifest(manifest_path)
    else:
        default_manifest = input_dir / "ai6win_manifest.json"
        if default_manifest.exists():
            manifest = load_manifest(default_manifest)

    if source_arc is None and manifest and manifest.get("source_arc"):
        maybe = Path(manifest["source_arc"])
        if maybe.exists():
            source_arc = maybe

    source_entries_by_name: dict[str, Ai6ArcEntry] = {}
    if source_arc:
        for e in read_index(source_arc, encoding=encoding):
            source_entries_by_name[e.name] = e

    if manifest and isinstance(manifest.get("entries"), list):
        entries = [Ai6ArcEntry.from_json(obj) for obj in manifest["entries"]]
    elif source_entries_by_name:
        entries = list(source_entries_by_name.values())
    else:
        entries = [Ai6ArcEntry(name=n, offset=0, size=0, unpacked_size=0,
                               packed=should_compress_by_ext(n))
                   for n in collect_files_for_new_arc(input_dir)]

    items: list[tuple[str, bytes, int]] = []
    stats = {
        "entries": len(entries),
        "from_files": 0,
        "reused_stored": 0,
        "compressed": 0,
        "stored_plain": 0,
        "missing_reused": 0,
        "warnings": [],
    }

    for e in entries:
        arc_name = normalize_arc_name(e.name)
        disk_path = input_dir / arc_name
        source_entry = source_entries_by_name.get(arc_name)

        can_reuse = bool(source_arc and source_entry and reuse_stored)
        if can_reuse and (not disk_path.exists() or _is_same_as_manifest_file(disk_path, e)):
            stored = read_stored_blob(source_arc, source_entry)
            items.append((arc_name, stored, source_entry.unpacked_size))
            stats["reused_stored"] += 1
            if not disk_path.exists():
                stats["missing_reused"] += 1
            continue

        if not disk_path.exists():
            raise FileNotFoundError(f"missing file for arc entry {arc_name!r}: {disk_path}")

        plain = disk_path.read_bytes()
        if compress_policy == "all":
            compress = True
        elif compress_policy == "none":
            compress = False
        elif compress_policy == "auto-ext":
            compress = should_compress_by_ext(arc_name)
        elif compress_policy == "manifest":
            compress = bool(e.packed) if (manifest or source_entry) else should_compress_by_ext(arc_name)
        else:
            raise ValueError(f"unknown compress policy: {compress_policy}")

        stored, unpacked_size, packed = prepare_stored_blob(plain, compress=compress, lzss_mode=lzss_mode)
        items.append((arc_name, stored, unpacked_size))
        stats["from_files"] += 1
        if packed:
            stats["compressed"] += 1
        else:
            stats["stored_plain"] += 1

    out_arc.write_bytes(build_arc_bytes(items))
    stats["output"] = str(out_arc)
    stats["output_size"] = out_arc.stat().st_size
    return stats
