#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI5WIN ARC 通用读写共用模块。

结构依据：
    int32 count
    repeat count:
        char name[NameLength]   # 每字节 XOR NameKey，明文 CP932，通常 0 结尾
        uint32 packed_size ^ SizeKey
        uint32 data_offset ^ OffsetKey
    data blobs...

注意：
- Garbro 的 AI5WIN opener 对 mes/lib/a/a6/msk/x 使用 LZSS 解压。
- 这里实现了兼容解压器和一个兼容压缩器；压缩器输出的流可被原 LZSS 解码器读取。
- 对于已有原包的改包，推荐使用 extract 生成 manifest，再 pack 复用 manifest。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Iterator, Optional
import hashlib
import json
import os
import struct


CP932 = "cp932"
NAME_LENGTH_CANDIDATES = (0x14, 0x1E, 0x20, 0x100)
COMPRESSED_EXTS = {".mes", ".lib", ".a", ".a6", ".msk", ".x"}


@dataclass(frozen=True)
class ArcScheme:
    """AI5WIN ARC 索引加密/字段长度方案。"""
    name_length: int
    name_key: int
    size_key: int
    offset_key: int

    def to_json(self) -> dict:
        return {
            "name_length": self.name_length,
            "name_key": self.name_key,
            "size_key": self.size_key,
            "offset_key": self.offset_key,
        }

    @staticmethod
    def from_json(obj: dict) -> "ArcScheme":
        return ArcScheme(
            name_length=parse_int_auto(obj["name_length"]),
            name_key=parse_int_auto(obj["name_key"]) & 0xFF,
            size_key=parse_int_auto(obj["size_key"]) & 0xFFFFFFFF,
            offset_key=parse_int_auto(obj["offset_key"]) & 0xFFFFFFFF,
        )


@dataclass
class ArcEntry:
    """ARC 文件条目。size 是包内存储大小，不一定是解压后大小。"""
    name: str
    offset: int
    size: int
    compressed: bool = False
    unpacked_size: Optional[int] = None
    sha1: Optional[str] = None


def parse_int_auto(value) -> int:
    """支持 int、十进制字符串、0x 十六进制字符串。"""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise TypeError(f"cannot parse integer: {value!r}")


def read_u32le(buf: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def write_u32le(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def has_compressed_ext(name: str, compressed_exts: set[str] = COMPRESSED_EXTS) -> bool:
    return Path(name).suffix.lower() in compressed_exts


def sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def normalize_arc_name(path: str) -> str:
    """包内路径统一用 /，避免 Windows 反斜杠进入索引。"""
    return path.replace("\\", "/").strip("/")


def decrypt_name(raw: bytes, scheme: ArcScheme, encoding: str = CP932) -> Optional[str]:
    """解密固定长文件名字段。"""
    out = bytearray()
    # Garbro 读的是 fixed name_length；遇到 XOR 后的 0 结束。
    for i in range(scheme.name_length):
        b = raw[i] ^ scheme.name_key
        if b == 0:
            break
        if b < 0x20:
            return None
        out.append(b)
    if not out:
        return None
    try:
        return out.decode(encoding)
    except UnicodeDecodeError:
        return None


def encrypt_name(name: str, scheme: ArcScheme, encoding: str = CP932) -> bytes:
    """加密文件名字段。默认要求预留 0 结尾，兼容性最好。"""
    name = normalize_arc_name(name)
    raw = name.encode(encoding)
    if len(raw) >= scheme.name_length:
        raise ValueError(
            f"file name too long for NameLength=0x{scheme.name_length:X}: "
            f"{name!r} -> {len(raw)} bytes; need <= {scheme.name_length - 1}"
        )
    plain = bytearray(scheme.name_length)
    plain[:len(raw)] = raw
    return bytes((b ^ scheme.name_key) for b in plain)


def check_entry_placement(offset: int, size: int, file_size: int, min_data_offset: int) -> bool:
    return offset >= min_data_offset and size >= 0 and offset + size <= file_size


def read_index(arc_path: str | Path, scheme: Optional[ArcScheme] = None,
               encoding: str = CP932) -> tuple[ArcScheme, list[ArcEntry]]:
    """读取 ARC 索引。未传 scheme 时自动猜测。"""
    data = Path(arc_path).read_bytes()
    if len(data) < 4:
        raise ValueError("file too small")
    count = struct.unpack_from("<i", data, 0)[0]
    if count <= 0 or count > 1_000_000:
        raise ValueError(f"insane entry count: {count}")

    if scheme is None:
        schemes = list(guess_schemes_from_bytes(data, encoding=encoding))
        if not schemes:
            raise ValueError("cannot guess AI5WIN ARC scheme")
        scheme = schemes[0]

    entries = read_index_from_bytes(data, scheme, encoding=encoding)
    return scheme, entries


def read_index_from_bytes(data: bytes, scheme: ArcScheme,
                          encoding: str = CP932) -> list[ArcEntry]:
    count = struct.unpack_from("<i", data, 0)[0]
    index_size = count * (scheme.name_length + 8)
    min_data_offset = 4 + index_size
    if min_data_offset > len(data):
        raise ValueError("index exceeds file size")

    entries: list[ArcEntry] = []
    pos = 4
    for _ in range(count):
        name = decrypt_name(data[pos:pos + scheme.name_length], scheme, encoding=encoding)
        if not name or not name.strip():
            raise ValueError("bad or empty encrypted name")
        pos += scheme.name_length

        packed_size = read_u32le(data, pos) ^ scheme.size_key
        data_offset = read_u32le(data, pos + 4) ^ scheme.offset_key
        pos += 8

        if not check_entry_placement(data_offset, packed_size, len(data), min_data_offset):
            raise ValueError(
                f"bad placement for {name}: offset=0x{data_offset:X}, size=0x{packed_size:X}"
            )

        entries.append(ArcEntry(
            name=name,
            offset=data_offset,
            size=packed_size,
            compressed=has_compressed_ext(name),
        ))
    return entries


def guess_schemes_from_bytes(data: bytes, encoding: str = CP932) -> Iterator[ArcScheme]:
    """按 Garbro Ai5ArcIndexReader.GuessSchemes 的逻辑猜 scheme。"""
    if len(data) < 4:
        return
    count = struct.unpack_from("<i", data, 0)[0]
    if count < 2:
        return

    for name_length in NAME_LENGTH_CANDIDATES:
        data_offset = (name_length + 8) * count + 4
        if data_offset >= len(data):
            continue

        # 第一个条目的 name 字段末尾一般是加密后的 0，因此可取出 NameKey。
        if 3 + name_length >= len(data):
            continue
        name_key = data[3 + name_length]

        first_size_enc = read_u32le(data, 4 + name_length)
        first_offset_enc = read_u32le(data, 8 + name_length)
        offset_key = (data_offset ^ first_offset_enc) & 0xFFFFFFFF

        # 第二个条目的 offset 字段位置：4 + (name+8) + name + 4 = (name+8)*2
        second_off_pos = (name_length + 8) * 2
        if second_off_pos + 4 > len(data):
            continue
        second_offset = read_u32le(data, second_off_pos) ^ offset_key
        if second_offset < data_offset or second_offset >= len(data):
            continue

        size_key = ((second_offset - data_offset) ^ first_size_enc) & 0xFFFFFFFF
        if offset_key == 0 or size_key == 0:
            continue

        scheme = ArcScheme(name_length, name_key, size_key, offset_key)
        try:
            read_index_from_bytes(data, scheme, encoding=encoding)
        except Exception:
            continue
        yield scheme


def choose_name_length(names: Iterable[str], encoding: str = CP932) -> int:
    """为新建包自动选择能容纳所有文件名的 NameLength。"""
    max_len = 0
    for name in names:
        max_len = max(max_len, len(normalize_arc_name(name).encode(encoding)))
    for n in NAME_LENGTH_CANDIDATES:
        if max_len < n:
            return n
    raise ValueError(f"file name too long: max {max_len} bytes, largest NameLength is 0x100")


def read_stored_blob(arc_path: str | Path, entry: ArcEntry) -> bytes:
    with open(arc_path, "rb") as f:
        f.seek(entry.offset)
        return f.read(entry.size)


def lzss_decompress(src: bytes) -> bytes:
    """兼容 GameRes.Compression.LzssStream 的 AI5WIN LZSS 解压。"""
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
    """
    最保守的 LZSS 编码：全部写 literal。
    优点是逻辑简单、兼容性高；缺点是体积约增加 12.5%。
    """
    out = bytearray()
    p = 0
    while p < len(src):
        chunk = src[p:p + 8]
        out.append((1 << len(chunk)) - 1)  # 低 len(chunk) 位都是 literal
        out.extend(chunk)
        p += len(chunk)
    return bytes(out)


def lzss_compress_greedy(src: bytes) -> bytes:
    """
    兼容 AI5WIN LZSS 的简单贪心压缩器。
    不是追求最优压缩率，但比全 literal 小很多，适合通用封包。
    """
    n = len(src)
    if n < 3:
        return lzss_compress_literal(src)

    # 3 字节 hash -> 近期出现位置列表。为控制内存，每个 key 最多保留若干候选。
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
                key = src[p:p + 3]
                candidates = pos_map.get(key)
                if candidates:
                    min_pos = max(0, p - 0x1000)
                    # 从近到远一般更容易获得较长匹配。
                    for q in reversed(candidates):
                        if q < min_pos:
                            continue
                        # 为避免复杂的自重叠匹配，限制在已有明文范围内。
                        max_len = min(18, n - p, p - q)
                        if max_len < 3:
                            continue
                        l = 3
                        while l < max_len and src[q + l] == src[p + l]:
                            l += 1
                        if l > best_len:
                            best_len = l
                            best_pos = q
                            if l == 18:
                                break

            if best_len >= 3:
                offset = (0xFEE + best_pos) & 0xFFF
                out.append(offset & 0xFF)
                out.append(((offset >> 4) & 0xF0) | (best_len - 3))
                # 注意：即使输出 match，解码端也会把 match 展开的每个字节写入滑窗。
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


def maybe_pack_entry_data(name: str, plain_data: bytes, compress: bool,
                          lzss_mode: str = "greedy") -> bytes:
    """根据扩展名/manifest 决定是否 LZSS 压缩。"""
    if not compress:
        return plain_data
    if lzss_mode == "literal":
        return lzss_compress_literal(plain_data)
    if lzss_mode == "greedy":
        return lzss_compress_greedy(plain_data)
    raise ValueError(f"unknown lzss_mode: {lzss_mode}")


def build_arc_bytes(items: list[tuple[str, bytes]], scheme: ArcScheme,
                    encoding: str = CP932) -> bytes:
    """
    根据已经准备好的包内数据块构建 ARC。
    items: [(archive_name, stored_blob), ...]
    """
    count = len(items)
    index_size = count * (scheme.name_length + 8)
    data_offset = 4 + index_size

    # 先计算每个数据块 offset。
    offsets: list[int] = []
    cur = data_offset
    for _, blob in items:
        offsets.append(cur)
        cur += len(blob)

    out = bytearray()
    out.extend(struct.pack("<i", count))

    for (name, blob), off in zip(items, offsets):
        out.extend(encrypt_name(name, scheme, encoding=encoding))
        out.extend(write_u32le(len(blob) ^ scheme.size_key))
        out.extend(write_u32le(off ^ scheme.offset_key))

    for _, blob in items:
        out.extend(blob)

    return bytes(out)


def manifest_from_arc(arc_path: str | Path, scheme: ArcScheme, entries: list[ArcEntry],
                      encoding: str = CP932) -> dict:
    return {
        "format": "ARC/AI5WIN",
        "encoding": encoding,
        "scheme": scheme.to_json(),
        "compressed_exts": sorted(COMPRESSED_EXTS),
        "entries": [
            {
                "name": e.name,
                "offset": e.offset,
                "size": e.size,
                "compressed": has_compressed_ext(e.name),
                "sha1_stored": sha1_bytes(read_stored_blob(arc_path, e)),
            }
            for e in entries
        ],
    }


def save_manifest(path: str | Path, manifest: dict) -> None:
    Path(path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def load_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def scheme_from_args_or_manifest(args, manifest: Optional[dict] = None) -> ArcScheme:
    if getattr(args, "scheme_json", None):
        obj = json.loads(Path(args.scheme_json).read_text(encoding="utf-8"))
        if "scheme" in obj:
            obj = obj["scheme"]
        return ArcScheme.from_json(obj)

    if manifest and "scheme" in manifest:
        return ArcScheme.from_json(manifest["scheme"])

    if getattr(args, "name_length", None) is not None:
        return ArcScheme(
            name_length=parse_int_auto(args.name_length),
            name_key=parse_int_auto(args.name_key),
            size_key=parse_int_auto(args.size_key),
            offset_key=parse_int_auto(args.offset_key),
        )

    raise ValueError("missing scheme: use --manifest, --source-arc, --scheme-json, or explicit keys")
