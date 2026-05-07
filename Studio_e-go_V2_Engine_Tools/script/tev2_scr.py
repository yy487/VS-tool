from __future__ import annotations

import hashlib
import json
import struct
from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from script.container import extract_ascii_literals, extract_nonzero_words, preview_u32_words, transform_mode2_words


@dataclass
class ScrOuterDoc:
    format: str
    source_path: str
    raw_header: dict[str, object]
    decoded_payload_preview_hex: str
    decoded_payload_sha256: str
    decoded_u32_preview: list[dict[str, object]]
    decoded_nonzero_words: list[dict[str, object]]
    ascii_literals: list[dict[str, object]]
    known_container_magics: list[dict[str, object]]
    container_summary: dict[str, object]
    decoded_payload_bytes: bytes = field(repr=False)


@dataclass
class ScrTextDoc:
    format: str
    source_path: str
    text_encoding: str
    raw_header: dict[str, object]
    entries: list[dict[str, object]]


@dataclass
class ScrSectionDoc:
    sec1_length_offset: int
    sec1_data_offset: int
    sec2_length_offset: int
    sec2_data_offset: int
    sec3_length_offset: int
    sec3_data_offset: int
    sec4_length_offset: int
    sec4_data_offset: int
    sec5_length_offset: int
    sec5_data_offset: int
    sec1_bytes: bytes
    sec2_bytes: bytes
    sec3_bytes: bytes
    sec4_offsets: list[int]
    sec4_offset_positions: list[int]
    sec5_entries: list[dict[str, object]]
    sec3_u32_offset_hits: list[tuple[int, int]]


@dataclass
class ScrRebuildImpact:
    anchor_offset: int
    original_offset: int
    current_offset: int
    old_length: int
    new_length: int
    delta: int
    outer_decoded_payload_size_field_offset: int
    sec3_length_field_offset: int
    sec4_impacted_indices: list[int]
    sec4_impacted_value_positions: list[int]
    sec5_impacted_indices: list[int]
    sec5_impacted_value_positions: list[int]
    sec3_u32_in_range_count: int
    sec3_impacted_u32_sample_positions: list[int]
    sec3_impacted_u32_sample_values: list[int]
    sec3_high_confidence_impacted_positions: list[int]
    sec3_high_confidence_impacted_values: list[int]


KNOWN_SEC3_REFERENCE_PATTERNS: set[tuple[str, int]] = {
    ("0c04000000020100", -1),
    ("0c0d000000010100", -1),
    ("0000060a00000000", -1),
    ("060a000000020500", -1),
    ("0000000001000a00", 1),
    ("0000000101000a00", 2),
    ("0301000604000000", 2),
    ("000c040000000101", 2),
    ("000c170000000201", 2),
    ("0000000603000000", 1),
    ("030100060a000000", 1),
    ("00000a6661646500", 1),
    ("0006040000000006", 1),
    ("0400000001010006", 1),
    ("0000000605000000", 1),
    ("00000001000a000c", -1),
    ("0101000604000000", -2),
    ("bd8142000c020000", 2),
    ("488176000c020000", 2),
    ("c70000000c050000", -2),
    ("65000006c7000000", 1),
    ("0401000604000000", 1),
    ("030100060a000000", 0),
    ("00000a6661646500", 0),
    ("0301000604000000", -2),
    ("95e494548d81000c", -1),
    ("0000000201000604", 1),
    ("0000000600000000", -2),
    ("030100060a000000", -2),
    ("00000a6661646500", -2),
    ("0000000603000000", -2),
    ("6700000600000000", -1),
    ("0c04000000020100", 1),
    ("000201000a626700", 2),
    ("000201000a626700", 1),
    ("0a0000000c040000", -2),
    ("000c040000000301", -2),
    ("0c04000000020100", -2),
    ("cb8176000c020000", 2),
    ("6381638176000c02", 1),
    ("0c04000000030100", 2),
    ("0c09000000010100", 2),
    ("000000010100060a", 1),
    ("816381638176000c", -1),
    ("65000006c7000000", 0),
    ("00000a6267000006", -2),
    ("000c170000000201", -2),
    ("b78176000c020000", 2),
    ("0301000604000000", -1),
    ("0a735f736d303900", -2),
    ("000c090000000101", 1),
    ("0101000605000000", -1),
    ("000c090000000101", 2),
    ("6381638176000c02", 0),
    ("0c06000000040100", 0),
    ("0c0d000000010100", -2),
    ("638176000c020000", 2),
    ("000c040000000101", -1),
    ("0c04000000030100", 0),
    ("0100060400000000", 2),
    ("0000000603000000", 2),
    ("00000a6661646500", -1),
    ("01000a6661646500", -1),
    ("c182bd8142000c02", 0),
    ("c181638163817600", 1),
    ("0301000604000000", 1),
    ("c88176000c020000", 2),
    ("498176000c020000", 2),
    ("000001010c0d0000", 2),
    ("a28142000c020000", 2),
    ("0000000600000000", -1),
    ("0a0000000c040000", -1),
    ("0006000000000006", -2),
    ("0900000001010c0d", 1),
    ("0400000003010006", 1),
    ("0604000000000600", 1),
    ("a282bd8142000c02", 1),
    ("0006090000000006", -1),
    ("0201000604000000", -2),
    ("0000000001000a00", 2),
    ("00000a6661646500", 2),
    ("0401000604000000", 2),
    ("00000a6267000006", 2),
    ("0c06000000040100", 1),
    ("0a66616465000006", -2),
    ("0101000604000000", 1),
    ("000201000a626700", -2),
    ("0000000603000000", 0),
    ("000a7368616b6500", -2),
    ("0301000603000000", -2),
    ("0c02000000000100", 1),
    ("a28176000c020000", 2),
    ("be8142000c020000", 2),
    ("a98176000c020000", 2),
    ("000c040000000301", -1),
    ("0000020c0c050000", -2),
    ("0c04000000010100", 2),
    ("0100060a0000000c", -1),
    ("816381638142000c", -1),
    ("0000000301000608", 1),
    ("000c090000000101", -1),
    ("030100060a000000", 2),
    ("000a2a6661646500", -2),
    ("82c182bd8142000c", -2),
    ("0100060000000000", 1),
    ("e98142000c020000", 2),
    ("00000b1000000000", 2),
    ("0a735f686d303200", -1),
    ("0a735f686f303800", -1),
    ("0c04000000030100", -1),
    ("000c170000000201", -1),
    ("000000090000803f", 1),
    ("5f686d303970000c", -2),
    ("000a66616465000c", -1),
    ("5f686d303270000c", -2),
    ("0000000101000604", 1),
    ("0000000101000600", 1),
    ("82a981488176000c", -1),
    ("0006030000000006", 1),
    ("0006000000000006", 1),
    ("c182bd8142000c02", 1),
}


def _is_suspicious_short_fragment(text: str) -> bool:
    if len(text) > 3:
        return False
    allowed_halfwidth = all(
        ("\uff61" <= ch <= "\uff9f") or ch in ";:,.!?/\\-_=+*'\"()[]{}<> "
        for ch in text
    )
    return allowed_halfwidth


def _extract_cp932_text_candidates(data: bytes, text_encoding: str = "cp932") -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    seen: set[tuple[int, int]] = set()

    # Structured path only: real text items are emitted after a formal control header.
    # The current confirmed forms are:
    # - ... 00 0A + cp932-bytes + 00
    # - ... 01 0B + cp932-bytes + 00
    starts: list[tuple[int, int]] = []
    for prefix, marker in ((b"\x00\x0A", 0x0A), (b"\x01\x0B", 0x0B)):
        pos = 0
        while True:
            pos = data.find(prefix, pos)
            if pos < 0:
                break
            starts.append((pos + 2, marker))
            pos += 2

    for start, marker in starts:
        end = start
        while end < len(data) and data[end] != 0:
            end += 1
        if end - start < 1:
            continue
        chunk = data[start:end]
        try:
            text = chunk.decode(text_encoding)
        except UnicodeDecodeError:
            continue
        if not any(ord(ch) >= 0x80 for ch in text):
            continue
        if _is_suspicious_short_fragment(text):
            continue
        if (start, end) in seen:
            continue
        candidates.append(
            {
                "index": len(candidates),
                "record_offset": start - 1,
                "offset": start,
                "length": len(chunk),
                "capacity_bytes": len(chunk),
                "in_place_capacity_bytes": len(chunk),
                "text": text,
                "original_text": text,
                "text_raw_hex": chunk.hex(" "),
                "source_rule": "structured_prefixed_null_terminated",
                "prefix_marker_u8": marker,
                "patch_mode": "section_rebuild_expandable",
                "supports_expansion_rebuild": True,
                "prefix_hex": data[max(0, start - 8) : start].hex(" "),
                "suffix_hex": data[end : min(len(data), end + 8)].hex(" "),
            }
        )
        seen.add((start, end))
    candidates.sort(key=lambda item: int(item["offset"]))
    return candidates


def probe_scr(path: Path) -> ScrOuterDoc:
    return _probe_scr_cached(str(path))


def probe_scr_bytes(data: bytes, *, source_path: str = "<memory>") -> ScrOuterDoc:
    if len(data) < 20:
        raise ValueError("SCR file too small")
    if data[:4] != b"SCR ":
        raise ValueError(f"Unexpected SCR magic: {data[:4]!r}")

    version_u32 = struct.unpack_from("<I", data, 4)[0]
    codec_mode_u32 = struct.unpack_from("<I", data, 8)[0]
    key_seed_u32 = struct.unpack_from("<I", data, 12)[0]
    decoded_payload_size_u32 = struct.unpack_from("<I", data, 16)[0]
    if codec_mode_u32 != 2:
        raise ValueError(f"Unsupported SCR codec mode: {codec_mode_u32}")

    decoded_payload = transform_mode2_words(data[20:], key_seed_u32)
    known_container_magics = [
        {"magic_ascii": "SCR ", "magic_u32": 0x20524353, "known_from": "raw_file_header"},
        {"magic_ascii": "TSCR", "magic_u32": 0x52435354, "known_from": "runtime_parser"},
        {"magic_ascii": "TUTA", "magic_u32": 0x41545554, "known_from": "runtime_parser"},
        {"magic_ascii": "TCRP", "magic_u32": 0x50524354, "known_from": "runtime_parser"},
        {"magic_ascii": "TXT0", "magic_u32": 0x30545854, "known_from": "runtime_parser"},
        {"magic_ascii": "M3H0", "magic_u32": 0x3048334D, "known_from": "runtime_parser"},
        {"magic_ascii": "M3P0", "magic_u32": 0x3050334D, "known_from": "runtime_parser"},
    ]
    container_summary = {
        "raw_outer_magic": "SCR ",
        "version_u32": version_u32,
        "codec_mode_u32": codec_mode_u32,
        "decoded_payload_size_u32": decoded_payload_size_u32,
        "status": "outer_container_decoded_inner_instruction_payload_unfinished",
    }

    return ScrOuterDoc(
        format="TE_V2_SCR_OUTER",
        source_path=source_path,
        raw_header={
            "magic_ascii": "SCR ",
            "version_u32": version_u32,
            "codec_mode_u32": codec_mode_u32,
            "key_seed_u32": key_seed_u32,
            "decoded_payload_size_u32": decoded_payload_size_u32,
            "encoded_payload_size_u32": len(data) - 20,
        },
        decoded_payload_preview_hex=decoded_payload[:512].hex(" "),
        decoded_payload_sha256=hashlib.sha256(decoded_payload).hexdigest(),
        decoded_u32_preview=preview_u32_words(decoded_payload, limit=48),
        decoded_nonzero_words=extract_nonzero_words(decoded_payload, limit=96),
        ascii_literals=extract_ascii_literals(decoded_payload),
        known_container_magics=known_container_magics,
        container_summary=container_summary,
        decoded_payload_bytes=decoded_payload,
    )


@lru_cache(maxsize=128)
def _probe_scr_cached(path_str: str) -> ScrOuterDoc:
    data = Path(path_str).read_bytes()
    return probe_scr_bytes(data, source_path=path_str)


def parse_scr_text(path: Path, text_encoding: str = "cp932", include_impact: bool = True) -> ScrTextDoc:
    cached = _parse_scr_text_cached(str(path), text_encoding, include_impact)
    entries = json.loads(json.dumps(cached["entries"], ensure_ascii=False))
    return ScrTextDoc(
        format=str(cached["format"]),
        source_path=str(cached["source_path"]),
        text_encoding=str(cached["text_encoding"]),
        raw_header=dict(cached["raw_header"]),
        entries=entries,
    )


@lru_cache(maxsize=128)
def _parse_scr_text_cached(path_str: str, text_encoding: str, include_impact: bool) -> dict[str, object]:
    path = Path(path_str)
    outer = probe_scr(path)
    return _build_scr_text_payload(outer, text_encoding=text_encoding, include_impact=include_impact)


def parse_scr_text_bytes(
    data: bytes,
    *,
    source_path: str = "<memory>",
    text_encoding: str = "cp932",
    include_impact: bool = True,
) -> ScrTextDoc:
    payload = _build_scr_text_payload(
        probe_scr_bytes(data, source_path=source_path),
        text_encoding=text_encoding,
        include_impact=include_impact,
    )
    entries = json.loads(json.dumps(payload["entries"], ensure_ascii=False))
    return ScrTextDoc(
        format=str(payload["format"]),
        source_path=str(payload["source_path"]),
        text_encoding=str(payload["text_encoding"]),
        raw_header=dict(payload["raw_header"]),
        entries=entries,
    )


def _build_scr_text_payload(outer: ScrOuterDoc, *, text_encoding: str, include_impact: bool) -> dict[str, object]:
    section_doc = parse_scr_sections(outer.decoded_payload_bytes)
    entries = _extract_cp932_text_candidates(section_doc.sec3_bytes, text_encoding=text_encoding)
    if include_impact:
        for entry in entries:
            impact = plan_scr_rebuild_impact(
                section_doc,
                anchor_offset=int(entry.get("record_offset", entry["offset"])),
                original_offset=int(entry["offset"]),
                current_offset=int(entry["offset"]),
                old_length=int(entry["length"]),
                new_length=int(entry["length"]),
            )
            entry["rebuild_impact"] = {
                "anchor_offset": impact.anchor_offset,
                "outer_decoded_payload_size_field_offset": impact.outer_decoded_payload_size_field_offset,
                "sec3_length_field_offset": impact.sec3_length_field_offset,
                "sec4_impacted_indices_if_expand": impact.sec4_impacted_indices,
                "sec4_impacted_value_positions_if_expand": impact.sec4_impacted_value_positions,
                "sec5_impacted_indices_if_expand": impact.sec5_impacted_indices,
                "sec5_impacted_value_positions_if_expand": impact.sec5_impacted_value_positions,
                "sec3_u32_in_range_count_if_expand": impact.sec3_u32_in_range_count,
                "sec3_impacted_u32_sample_positions_if_expand": impact.sec3_impacted_u32_sample_positions,
                "sec3_impacted_u32_sample_values_if_expand": impact.sec3_impacted_u32_sample_values,
                "sec3_high_confidence_impacted_positions_if_expand": impact.sec3_high_confidence_impacted_positions,
                "sec3_high_confidence_impacted_values_if_expand": impact.sec3_high_confidence_impacted_values,
            }
    return {
        "format": "TE_V2_SCR_TEXT_CANDIDATES",
        "source_path": outer.source_path,
        "text_encoding": text_encoding,
        "raw_header": dict(outer.raw_header),
        "entries": entries,
    }


@lru_cache(maxsize=128)
def _parse_scr_sections_cached(path_str: str) -> ScrSectionDoc:
    outer = _probe_scr_cached(path_str)
    return parse_scr_sections(outer.decoded_payload_bytes)


def parse_scr_sections(decoded_payload_bytes: bytes) -> ScrSectionDoc:
    pos = 0
    sec1_length_offset = pos
    sec1_len = struct.unpack_from("<I", decoded_payload_bytes, pos)[0]
    pos += 4
    sec1_data_offset = pos
    sec1 = decoded_payload_bytes[pos : pos + sec1_len]
    pos += sec1_len

    sec2_length_offset = pos
    sec2_len = struct.unpack_from("<I", decoded_payload_bytes, pos)[0]
    pos += 4
    sec2_data_offset = pos
    sec2 = decoded_payload_bytes[pos : pos + sec2_len]
    pos += sec2_len

    sec3_length_offset = pos
    sec3_len = struct.unpack_from("<I", decoded_payload_bytes, pos)[0]
    pos += 4
    sec3_data_offset = pos
    sec3 = decoded_payload_bytes[pos : pos + sec3_len]
    pos += sec3_len

    sec4_length_offset = pos
    sec4_len = struct.unpack_from("<I", decoded_payload_bytes, pos)[0]
    pos += 4
    sec4_data_offset = pos
    sec4_raw = decoded_payload_bytes[pos : pos + sec4_len]
    pos += sec4_len
    sec4_offsets = [struct.unpack_from("<I", sec4_raw, off)[0] for off in range(0, len(sec4_raw), 4)]
    sec4_offset_positions = [sec4_data_offset + off for off in range(0, len(sec4_raw), 4)]

    sec5_length_offset = pos
    sec5_len = struct.unpack_from("<I", decoded_payload_bytes, pos)[0]
    pos += 4
    sec5_data_offset = pos
    sec5_end = pos + sec5_len
    sec5_entries: list[dict[str, object]] = []
    while pos < sec5_end:
        name_end = decoded_payload_bytes.index(0, pos, sec5_end)
        name = decoded_payload_bytes[pos:name_end].decode("cp932", errors="replace")
        pos = name_end + 1
        offset_pos = pos
        target_offset = struct.unpack_from("<I", decoded_payload_bytes, pos)[0]
        pos += 4
        sec5_entries.append(
            {
                "name": name,
                "offset": target_offset,
                "offset_position": offset_pos,
            }
        )

    sec3_u32_offset_hits: list[tuple[int, int]] = []
    for sec3_pos in range(0, len(sec3) - 3, 4):
        value = struct.unpack_from("<I", sec3, sec3_pos)[0]
        if 0 <= value < len(sec3):
            sec3_u32_offset_hits.append((sec3_data_offset + sec3_pos, value))

    return ScrSectionDoc(
        sec1_length_offset=sec1_length_offset,
        sec1_data_offset=sec1_data_offset,
        sec2_length_offset=sec2_length_offset,
        sec2_data_offset=sec2_data_offset,
        sec3_length_offset=sec3_length_offset,
        sec3_data_offset=sec3_data_offset,
        sec4_length_offset=sec4_length_offset,
        sec4_data_offset=sec4_data_offset,
        sec5_length_offset=sec5_length_offset,
        sec5_data_offset=sec5_data_offset,
        sec1_bytes=sec1,
        sec2_bytes=sec2,
        sec3_bytes=sec3,
        sec4_offsets=sec4_offsets,
        sec4_offset_positions=sec4_offset_positions,
        sec5_entries=sec5_entries,
        sec3_u32_offset_hits=sec3_u32_offset_hits,
    )


def build_scr_sections(section_doc: ScrSectionDoc) -> bytes:
    sec4_raw = b"".join(offset.to_bytes(4, "little") for offset in section_doc.sec4_offsets)
    sec5_raw = bytearray()
    for entry in section_doc.sec5_entries:
        sec5_raw.extend(str(entry["name"]).encode("cp932"))
        sec5_raw.append(0)
        sec5_raw.extend(int(entry["offset"]).to_bytes(4, "little"))
    out = bytearray()
    out.extend(len(section_doc.sec1_bytes).to_bytes(4, "little"))
    out.extend(section_doc.sec1_bytes)
    out.extend(len(section_doc.sec2_bytes).to_bytes(4, "little"))
    out.extend(section_doc.sec2_bytes)
    out.extend(len(section_doc.sec3_bytes).to_bytes(4, "little"))
    out.extend(section_doc.sec3_bytes)
    out.extend(len(sec4_raw).to_bytes(4, "little"))
    out.extend(sec4_raw)
    out.extend(len(sec5_raw).to_bytes(4, "little"))
    out.extend(sec5_raw)
    while len(out) & 3:
        out.append(0)
    return bytes(out)


def plan_scr_rebuild_impact(section_doc: ScrSectionDoc, *, anchor_offset: int, original_offset: int, current_offset: int, old_length: int, new_length: int) -> ScrRebuildImpact:
    delta = new_length - old_length
    sec4_impacted_indices = [idx for idx, target_offset in enumerate(section_doc.sec4_offsets) if target_offset > anchor_offset]
    sec5_impacted_indices = [
        idx for idx, sec5_entry in enumerate(section_doc.sec5_entries) if int(sec5_entry["offset"]) > anchor_offset
    ]
    sec3_impacted_u32_positions_if_expand: list[int] = []
    sec3_impacted_u32_values_if_expand: list[int] = []
    sec3_high_confidence_impacted_positions: list[int] = []
    sec3_high_confidence_impacted_values: list[int] = []
    for absolute_pos, value in section_doc.sec3_u32_offset_hits:
        rel_pos = absolute_pos - section_doc.sec3_data_offset
        if anchor_offset < value < len(section_doc.sec3_bytes):
            sec3_impacted_u32_positions_if_expand.append(absolute_pos)
            sec3_impacted_u32_values_if_expand.append(value)
        tail8 = section_doc.sec3_bytes[max(0, rel_pos - 8) : rel_pos].hex()
        for pattern_tail8, delta_to_anchor in KNOWN_SEC3_REFERENCE_PATTERNS:
            if tail8 == pattern_tail8 and value == anchor_offset + delta_to_anchor:
                sec3_high_confidence_impacted_positions.append(absolute_pos)
                sec3_high_confidence_impacted_values.append(value)
                break
    return ScrRebuildImpact(
        anchor_offset=anchor_offset,
        original_offset=original_offset,
        current_offset=current_offset,
        old_length=old_length,
        new_length=new_length,
        delta=delta,
        outer_decoded_payload_size_field_offset=16,
        sec3_length_field_offset=section_doc.sec3_length_offset,
        sec4_impacted_indices=sec4_impacted_indices,
        sec4_impacted_value_positions=[section_doc.sec4_offset_positions[idx] for idx in sec4_impacted_indices],
        sec5_impacted_indices=sec5_impacted_indices,
        sec5_impacted_value_positions=[int(section_doc.sec5_entries[idx]["offset_position"]) for idx in sec5_impacted_indices],
        sec3_u32_in_range_count=len(sec3_impacted_u32_positions_if_expand),
        sec3_impacted_u32_sample_positions=sec3_impacted_u32_positions_if_expand[:32],
        sec3_impacted_u32_sample_values=sec3_impacted_u32_values_if_expand[:32],
        sec3_high_confidence_impacted_positions=sec3_high_confidence_impacted_positions,
        sec3_high_confidence_impacted_values=sec3_high_confidence_impacted_values,
    )


def _build_scr_bytes(raw_header: dict[str, object], decoded_payload_bytes: bytes) -> bytes:
    encoded_payload = transform_mode2_words(decoded_payload_bytes, int(raw_header["key_seed_u32"]))
    rebuilt = (
        b"SCR "
        + int(raw_header["version_u32"]).to_bytes(4, "little")
        + int(raw_header["codec_mode_u32"]).to_bytes(4, "little")
        + int(raw_header["key_seed_u32"]).to_bytes(4, "little")
        + int(raw_header["decoded_payload_size_u32"]).to_bytes(4, "little")
        + encoded_payload
    )
    if len(decoded_payload_bytes) != int(raw_header["decoded_payload_size_u32"]):
        raise ValueError("Decoded SCR payload size does not match header")
    return rebuilt


def compile_scr_text(doc: dict[str, object], text_encoding: str = "cp932") -> bytes:
    if str(doc.get("format")) != "TE_V2_SCR_TEXT_CANDIDATES":
        raise ValueError("Unsupported SCR text document format")
    source_path = Path(str(doc["source_path"]))
    outer = deepcopy(_probe_scr_cached(str(source_path)))
    sections = deepcopy(_parse_scr_sections_cached(str(source_path)))
    payload = bytearray(sections.sec3_bytes)
    shift = 0
    changed_entries = sorted(
        (
            entry
            for entry in doc["entries"]
            if str(entry.get("text", "")) != str(entry.get("original_text", entry.get("text", "")))
        ),
        key=lambda item: int(item["offset"]),
    )
    for entry in changed_entries:
        current_text = str(entry.get("text", ""))
        original_text = str(entry.get("original_text", current_text))
        original_offset = int(entry["offset"])
        offset = int(entry["offset"]) + shift
        length = int(entry.get("capacity_bytes", entry["length"]))
        encoded = current_text.encode(text_encoding)
        if len(encoded) <= length:
            payload[offset : offset + length] = encoded + (b"\x00" * (length - len(encoded)))
            continue

        impact = plan_scr_rebuild_impact(
            sections,
            anchor_offset=int(entry.get("record_offset", original_offset)),
            original_offset=original_offset,
            current_offset=offset,
            old_length=length,
            new_length=len(encoded),
        )
        delta = impact.delta
        payload[offset : offset + length] = encoded
        for idx in impact.sec4_impacted_indices:
            sections.sec4_offsets[idx] = int(sections.sec4_offsets[idx]) + delta
        for idx in impact.sec5_impacted_indices:
            sections.sec5_entries[idx]["offset"] = int(sections.sec5_entries[idx]["offset"]) + delta
        for absolute_pos, value in zip(impact.sec3_high_confidence_impacted_positions, impact.sec3_high_confidence_impacted_values):
            rel_pos = absolute_pos - sections.sec3_data_offset
            struct.pack_into("<I", payload, rel_pos, int(value) + delta)
        shift += delta

    sections.sec3_bytes = bytes(payload)
    rebuilt_payload = build_scr_sections(sections)
    raw_header = dict(outer.raw_header)
    raw_header["decoded_payload_size_u32"] = len(rebuilt_payload)
    return _build_scr_bytes(raw_header, rebuilt_payload)


def rebuild_scr(doc: ScrOuterDoc) -> bytes:
    return _build_scr_bytes(doc.raw_header, doc.decoded_payload_bytes)


def write_probe(path: Path, output_path: Path) -> Path:
    doc = probe_scr(path)
    payload = {
        "format": doc.format,
        "source_path": doc.source_path,
        "raw_header": doc.raw_header,
        "decoded_payload_preview_hex": doc.decoded_payload_preview_hex,
        "decoded_payload_sha256": doc.decoded_payload_sha256,
        "decoded_u32_preview": doc.decoded_u32_preview,
        "decoded_nonzero_words": doc.decoded_nonzero_words,
        "ascii_literals": doc.ascii_literals,
        "known_container_magics": doc.known_container_magics,
        "container_summary": doc.container_summary,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def write_text_doc(path: Path, doc: ScrTextDoc) -> None:
    payload = {
        "format": doc.format,
        "source_path": doc.source_path,
        "text_encoding": doc.text_encoding,
        "raw_header": doc.raw_header,
        "entries": doc.entries,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
