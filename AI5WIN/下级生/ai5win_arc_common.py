#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI5WIN / Silky's AI5 系 ARC 解包共用模块。

根据 AI5CHN.EXE.c 反汇编还原：
- FUN_0040fa70：读取 ARC 目录：uint32 count + count * 0x14
- FUN_0040e5c0：对目录项执行异或 + 字节位置还原
- FUN_0040e680 / FUN_0040eb50：按目录项 offset/size 直接读取文件内容

目录项解码后结构：
    +0x00  char name[12]   # 以 \0 截断，游戏查找前会转大写
    +0x0C  uint32 size
    +0x10  uint32 offset
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
import struct
from typing import BinaryIO, Iterable, List, Optional


ENTRY_SIZE = 0x14
HEADER_SIZE = 4

# FUN_0040e5c0 里的 local_18 表。
# 反汇编逻辑等价于：decoded[PERM[i]] = encrypted[i] ^ key
ENTRY_PERM = [
    0x11, 0x02, 0x08, 0x13, 0x00,
    0x05, 0x0A, 0x0D, 0x01, 0x0F,
    0x06, 0x04, 0x0B, 0x10, 0x03,
    0x09, 0x12, 0x0C, 0x07, 0x0E,
]


@dataclass(frozen=True)
class ArcEntry:
    """解码后的 ARC 目录项。"""

    index: int
    name: str
    raw_name: bytes
    size: int
    offset: int

    @property
    def end_offset(self) -> int:
        return self.offset + self.size

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "raw_name_hex": self.raw_name.hex(),
            "size": self.size,
            "offset": self.offset,
            "end_offset": self.end_offset,
        }


class ArcFormatError(RuntimeError):
    """ARC 文件结构不符合当前已知 AI5WIN ARC 格式。"""


def _decode_entry_table(raw_table: bytes, count: int) -> bytes:
    """还原 FUN_0040e5c0 的目录解码逻辑。"""
    if len(raw_table) != count * ENTRY_SIZE:
        raise ArcFormatError(
            f"目录长度不正确：期望 {count * ENTRY_SIZE} 字节，实际 {len(raw_table)} 字节"
        )

    out = bytearray(len(raw_table))
    key = count & 0xFF

    for entry_index in range(count):
        src_base = entry_index * ENTRY_SIZE
        dec = bytearray(ENTRY_SIZE)
        src = raw_table[src_base : src_base + ENTRY_SIZE]
        for i, value in enumerate(src):
            # 游戏里每处理一个字节，key = key * 3 + 1，并按 byte 截断。
            dec[ENTRY_PERM[i]] = value ^ key
            key = (key * 3 + 1) & 0xFF
        out[src_base : src_base + ENTRY_SIZE] = dec

    return bytes(out)


def _decode_name(raw_name: bytes, encoding: str = "cp932") -> str:
    """目录名字段最多 12 字节，遇到 NUL 截断。"""
    raw = raw_name.split(b"\x00", 1)[0]
    try:
        name = raw.decode(encoding)
    except UnicodeDecodeError:
        # 理论上 AI5WIN 的资源名多为 ASCII/CP932；这里保底防止工具中断。
        name = raw.decode(encoding, errors="replace")
    return name


def read_entries(fp: BinaryIO, encoding: str = "cp932", validate: bool = True) -> List[ArcEntry]:
    """读取并解码 ARC 目录。fp 会被移动到文件开头。"""
    fp.seek(0)
    header = fp.read(HEADER_SIZE)
    if len(header) != HEADER_SIZE:
        raise ArcFormatError("文件过短，无法读取 ARC 条目数")

    (count,) = struct.unpack("<I", header)
    if count <= 0:
        raise ArcFormatError(f"非法条目数：{count}")

    table_size = count * ENTRY_SIZE
    raw_table = fp.read(table_size)
    if len(raw_table) != table_size:
        raise ArcFormatError(
            f"文件过短，无法读取完整目录：count={count}, table_size={table_size}"
        )

    decoded_table = _decode_entry_table(raw_table, count)
    entries: List[ArcEntry] = []

    for index in range(count):
        base = index * ENTRY_SIZE
        rec = decoded_table[base : base + ENTRY_SIZE]
        raw_name = rec[:12]
        name = _decode_name(raw_name, encoding=encoding)
        size, offset = struct.unpack_from("<II", rec, 12)
        entries.append(
            ArcEntry(index=index, name=name, raw_name=raw_name, size=size, offset=offset)
        )

    if validate:
        validate_entries(fp, entries)

    return entries


def validate_entries(fp: BinaryIO, entries: Iterable[ArcEntry]) -> None:
    """基础结构校验，防止误识别或目录损坏。"""
    entries = list(entries)
    fp.seek(0, 2)
    file_size = fp.tell()
    min_data_offset = HEADER_SIZE + len(entries) * ENTRY_SIZE

    seen_names = set()
    for e in entries:
        if not e.name:
            raise ArcFormatError(f"第 {e.index} 项文件名为空")
        if e.name in seen_names:
            # 游戏按名字顺序查找，重复名会导致歧义；直接视为异常。
            raise ArcFormatError(f"重复文件名：{e.name}")
        seen_names.add(e.name)
        if e.offset < min_data_offset:
            raise ArcFormatError(
                f"第 {e.index} 项 {e.name} offset={e.offset} 落在目录区内"
            )
        if e.size < 0 or e.end_offset > file_size:
            raise ArcFormatError(
                f"第 {e.index} 项 {e.name} 越界：offset={e.offset}, size={e.size}, file_size={file_size}"
            )


def read_file_data(fp: BinaryIO, entry: ArcEntry) -> bytes:
    """按目录 offset/size 读取文件内容。数据区目前确认是明文直存。"""
    fp.seek(entry.offset)
    data = fp.read(entry.size)
    if len(data) != entry.size:
        raise ArcFormatError(
            f"读取 {entry.name} 失败：期望 {entry.size} 字节，实际 {len(data)} 字节"
        )
    return data


_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._+\-\u3040-\u30ff\u3400-\u9fff]+")


def safe_output_name(name: str, fallback: str) -> str:
    """把目录名转成安全的本地文件名。ARC 原始结构一般没有子目录。"""
    # 禁止路径穿越与绝对路径；保留常见日文/中文/ASCII 字符。
    base = Path(name.replace("\\", "/")).name.strip()
    base = _SAFE_NAME_RE.sub("_", base)
    base = base.strip(". ")
    return base or fallback


def extract_arc(
    arc_path: Path,
    out_dir: Path,
    encoding: str = "cp932",
    overwrite: bool = False,
    write_manifest: bool = True,
) -> List[ArcEntry]:
    """解包单个 ARC。"""
    arc_path = Path(arc_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with arc_path.open("rb") as fp:
        entries = read_entries(fp, encoding=encoding, validate=True)
        used_names = set()
        for entry in entries:
            fallback = f"entry_{entry.index:04d}.bin"
            filename = safe_output_name(entry.name, fallback=fallback)
            if filename in used_names:
                stem = Path(filename).stem
                suffix = Path(filename).suffix
                filename = f"{stem}_{entry.index:04d}{suffix}"
            used_names.add(filename)

            dst = out_dir / filename
            if dst.exists() and not overwrite:
                raise FileExistsError(f"输出文件已存在：{dst}；如需覆盖请加 --overwrite")
            dst.write_bytes(read_file_data(fp, entry))

    if write_manifest:
        manifest = {
            "archive": str(arc_path),
            "entry_count": len(entries),
            "entries": [e.to_dict() for e in entries],
        }
        (out_dir / "_arc_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return entries
