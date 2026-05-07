from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ArchiveProbe:
    path: Path
    size: int
    head_hex: str
    u32le_preview: list[int]
    ascii_preview: str


@dataclass
class PakEntry:
    index: int
    group_index: int
    group_name: str
    file_name: str
    packed_size: int
    data_offset: int
    align_u32: int
    checksum_u32: int

    @property
    def logical_path(self) -> str:
        if self.group_name:
            return f"{self.group_name}/{self.file_name}"
        return self.file_name


def probe_archive(path: Path) -> ArchiveProbe:
    data = path.read_bytes()
    head = data[:64]
    words = []
    for off in range(0, min(len(head), 32), 4):
        if off + 4 <= len(head):
            words.append(struct.unpack_from("<I", head, off)[0])
    ascii_preview = "".join(chr(b) if 32 <= b < 127 else "." for b in head[:32])
    return ArchiveProbe(
        path=path,
        size=len(data),
        head_hex=head.hex(" "),
        u32le_preview=words,
        ascii_preview=ascii_preview,
    )


def build_probe_manifest(game_dir: Path) -> dict[str, object]:
    archives = sorted(game_dir.glob("game*.dat"))
    items = []
    for archive in archives:
        probe = probe_archive(archive)
        items.append(
            {
                "file": archive.name,
                "size": probe.size,
                "head_hex": probe.head_hex,
                "u32le_preview": probe.u32le_preview,
                "ascii_preview": probe.ascii_preview,
            }
        )
    return {
        "format": "TE_V2_ARCHIVE_PROBE",
        "archive_count": len(items),
        "archives": items,
    }


def write_probe_manifest(game_dir: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = build_probe_manifest(game_dir)
    path = output_dir / "archive_probe.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def parse_pak0(path: Path) -> list[PakEntry]:
    data = path.read_bytes()
    magic, header_end, group_count, file_count = struct.unpack_from("<4I", data, 0)
    if magic != 0x304B4150:
        raise ValueError(f"Unsupported archive magic: {magic:08X}")
    group_rows = [struct.unpack_from("<2I", data, 16 + i * 8) for i in range(group_count)]
    file_table_start = 16 + group_count * 8
    files = [struct.unpack_from("<4I", data, file_table_start + i * 16) for i in range(file_count)]

    names_start = file_table_start + file_count * 16
    pos = names_start
    all_names: list[str] = []
    while pos < header_end:
        name_len = data[pos]
        if name_len == 0 or pos + 1 + name_len > header_end:
            break
        name = data[pos + 1 : pos + 1 + name_len].decode("ascii", errors="replace")
        all_names.append(name)
        pos += 1 + name_len

    # Observed samples: names section is "group names" then "file names".
    # Most likely group count is the number of leading group names with small vocabulary.
    # We keep a pragmatic split here based on the last `file_count` names.
    if len(all_names) < file_count:
        raise ValueError("PAK0 header does not contain enough file names")
    group_names = all_names[: len(all_names) - file_count]
    file_names = all_names[len(all_names) - file_count :]
    group_end_indices = [row[1] for row in group_rows]
    entries: list[PakEntry] = []
    for index, row in enumerate(files):
        data_offset, packed_size, align_u32, checksum_u32 = row
        group_index = 0
        for candidate_group_index, group_end_index in enumerate(group_end_indices):
            if index < group_end_index:
                group_index = candidate_group_index
                break
        if group_index > 0 and group_index - 1 < len(group_names):
            group_name = group_names[group_index - 1]
        else:
            group_name = ""
        file_name = file_names[index] if index < len(file_names) else f"file_{index:04d}.bin"
        entries.append(
            PakEntry(
                index=index,
                group_index=group_index,
                group_name=group_name,
                file_name=file_name,
                packed_size=packed_size,
                data_offset=data_offset,
                align_u32=align_u32,
                checksum_u32=checksum_u32,
            )
        )
    return entries


def unpack_pak0(path: Path, output_dir: Path) -> Path:
    data = path.read_bytes()
    entries = parse_pak0(path)
    output_dir.mkdir(parents=True, exist_ok=True)
    files_root = output_dir / "files"
    manifest_entries = []
    for entry in entries:
        blob = data[entry.data_offset : entry.data_offset + entry.packed_size]
        out_path = files_root / entry.logical_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(blob)
        manifest_entries.append(
            {
                "index": entry.index,
                "group_index": entry.group_index,
                "group_name": entry.group_name,
                "file_name": entry.file_name,
                "logical_path": entry.logical_path,
                "packed_size": entry.packed_size,
                "data_offset": entry.data_offset,
                "align_u32": entry.align_u32,
                "checksum_u32": entry.checksum_u32,
            }
        )
    manifest = {
        "format": "TE_V2_PAK0",
        "archive": path.name,
        "entry_count": len(manifest_entries),
        "entries": manifest_entries,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path
