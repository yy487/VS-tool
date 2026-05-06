from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .common import PathLike, require_signature, write_bytes

PACK_SIGNATURE = b"GAMEDAT PAC2"
PACK_HDR_SIZE = 16
PACK_NAME_SIZE = 32
PACK_INFO_SIZE = 8


@dataclass(frozen=True)
class PackEntry:
    index: int
    name: str
    offset: int
    size: int


def _read_name(raw: bytes | bytearray) -> str:
    return bytes(raw).split(b"\x00", 1)[0].decode("ascii", errors="strict")


def read_index(pack_path: PathLike) -> list[PackEntry]:
    data = Path(pack_path).read_bytes()
    require_signature(data, PACK_SIGNATURE, "PackV2")
    if len(data) < PACK_HDR_SIZE:
        raise ValueError("PackV2: file too small")
    file_count = struct.unpack_from("<I", data, 12)[0]
    names_off = PACK_HDR_SIZE
    infos_off = names_off + file_count * PACK_NAME_SIZE
    data_off = infos_off + file_count * PACK_INFO_SIZE
    if data_off > len(data):
        raise ValueError("PackV2: broken header/table size")

    entries: list[PackEntry] = []
    for i in range(file_count):
        name_raw = data[names_off + i * PACK_NAME_SIZE:names_off + (i + 1) * PACK_NAME_SIZE]
        name = _read_name(name_raw)
        offset, size = struct.unpack_from("<II", data, infos_off + i * PACK_INFO_SIZE)
        if data_off + offset + size > len(data):
            raise ValueError(f"PackV2: entry out of range: {name!r}")
        entries.append(PackEntry(i, name, offset, size))
    return entries


def extract_pack(pack_path: PathLike, out_dir: PathLike, *, overwrite: bool = True) -> list[PackEntry]:
    pack_path = Path(pack_path)
    out_dir = Path(out_dir)
    data = pack_path.read_bytes()
    entries = read_index(pack_path)
    file_count = len(entries)
    data_off = PACK_HDR_SIZE + file_count * PACK_NAME_SIZE + file_count * PACK_INFO_SIZE
    out_dir.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        out_path = out_dir / entry.name
        if out_path.exists() and not overwrite:
            raise FileExistsError(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_bytes(out_path, data[data_off + entry.offset:data_off + entry.offset + entry.size])
    return entries


def _iter_files(in_dir: PathLike, manifest: Iterable[str] | None = None) -> list[Path]:
    root = Path(in_dir)
    if manifest is not None:
        return [root / name for name in manifest]
    return sorted([p for p in root.rglob("*") if p.is_file()], key=lambda p: p.relative_to(root).as_posix())


def build_pack(in_dir: PathLike, out_path: PathLike, *, manifest: Iterable[str] | None = None) -> list[PackEntry]:
    root = Path(in_dir)
    files = _iter_files(root, manifest)
    if not files:
        raise ValueError("PackV2: no files to pack")

    entries: list[PackEntry] = []
    payload = bytearray()
    for idx, path in enumerate(files):
        if not path.is_file():
            raise FileNotFoundError(path)
        rel_name = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        name_bytes = rel_name.encode("ascii", errors="strict")
        # Original C++ code uses char[32] and writes a NUL terminator, so max usable length is 31.
        if len(name_bytes) >= PACK_NAME_SIZE:
            raise ValueError(f"PackV2: file name too long for 32-byte table entry: {rel_name!r}")
        raw = path.read_bytes()
        entries.append(PackEntry(idx, rel_name, len(payload), len(raw)))
        payload.extend(raw)

    header = bytearray()
    header.extend(PACK_SIGNATURE)
    header.extend(struct.pack("<I", len(entries)))
    for entry in entries:
        name = entry.name.encode("ascii", errors="strict")
        header.extend(name + b"\x00" * (PACK_NAME_SIZE - len(name)))
    for entry in entries:
        header.extend(struct.pack("<II", entry.offset, entry.size))
    write_bytes(out_path, header + payload)
    return entries
