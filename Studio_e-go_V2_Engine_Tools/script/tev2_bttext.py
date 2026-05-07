from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

from script.container import TXT0_XOR_PATTERN, decode_mode5_swapped, encode_mode5_swapped, extract_ascii_literals


@dataclass
class BtTextOuterDoc:
    format: str
    source_path: str
    raw_header: dict[str, object]
    tuta_header: dict[str, object]
    txt0_header: dict[str, object]
    txt0_strings: list[dict[str, object]]
    decoded_root_preview_hex: str
    decoded_root_sha256: str
    known_container_magics: list[dict[str, object]]
    container_summary: dict[str, object]
    decoded_root_bytes: bytes = field(repr=False)


@dataclass
class BtTextTextDoc:
    format: str
    source_path: str
    source_text_encoding: str
    raw_header: dict[str, object]
    tuta_header: dict[str, object]
    txt0_header: dict[str, object]
    entries: list[dict[str, object]]


def _decode_txt0_string(data: bytes, start: int, text_encoding: str = "cp932") -> dict[str, object]:
    text_raw = bytearray()
    encoded = bytearray()
    index = 0
    while start + index < len(data):
        byte = data[start + index]
        pattern_byte = TXT0_XOR_PATTERN[index & 3]
        encoded.append(byte)
        if byte == pattern_byte:
            break
        text_raw.append(byte ^ pattern_byte)
        index += 1
    else:
        raise ValueError(f"Unterminated TXT0 string at decoded offset 0x{start:X}")
    try:
        text = bytes(text_raw).decode(text_encoding)
        decoded = True
    except UnicodeDecodeError:
        text = bytes(text_raw).decode(text_encoding, errors="replace")
        decoded = False
    return {
        "text": text,
        "decoded": decoded,
        "text_raw_hex": bytes(text_raw).hex(" "),
        "encoded_hex": bytes(encoded).hex(" "),
        "encoded_length": len(encoded),
    }


def _probe_bttext_bytes(data: bytes, source_path: str, text_encoding: str = "cp932") -> BtTextOuterDoc:
    if len(data) < 16:
        raise ValueError("BtText.dat too small")
    if data[:4] != b"TSCR":
        raise ValueError(f"Unexpected BtText magic: {data[:4]!r}")

    total_size_u32 = struct.unpack_from("<I", data, 4)[0]
    raw_entry_count_u32 = struct.unpack_from("<I", data, 8)[0]
    key_seed_u32 = struct.unpack_from("<I", data, 12)[0]
    encoded_root = data[16:]
    encoded_root_main_size = len(encoded_root) - (len(encoded_root) & 3)
    encoded_root_main = encoded_root[:encoded_root_main_size]
    decoded_root = decode_mode5_swapped(encoded_root_main, key_seed_u32)
    if decoded_root[:4] != b"TUTA":
        raise ValueError(f"Decoded BtText root is not TUTA: {decoded_root[:4]!r}")

    tuta_size_u32 = struct.unpack_from("<I", decoded_root, 4)[0]
    tuta_field_08_u32 = struct.unpack_from("<I", decoded_root, 8)[0]
    tuta_field_0C_u32 = struct.unpack_from("<I", decoded_root, 12)[0]
    if tuta_size_u32 > len(decoded_root):
        missing = tuta_size_u32 - len(decoded_root)
        decoded_root = decoded_root + (b"\x00" * missing)
    else:
        decoded_root = decoded_root[:tuta_size_u32]

    txt0_offset = decoded_root.find(b"TXT0")
    if txt0_offset < 0:
        raise ValueError("Decoded BtText root does not contain TXT0")

    txt0_size_u32 = struct.unpack_from("<I", decoded_root, txt0_offset + 4)[0]
    txt0_entry_count_u32 = struct.unpack_from("<I", decoded_root, txt0_offset + 8)[0]
    txt0_offsets = [
        struct.unpack_from("<I", decoded_root, txt0_offset + 12 + index * 4)[0]
        for index in range(txt0_entry_count_u32)
    ]
    txt0_base_offset = txt0_offset + 8
    txt0_strings: list[dict[str, object]] = []
    for index, relative_offset_u32 in enumerate(txt0_offsets):
        decoded_offset = txt0_base_offset + relative_offset_u32
        item = _decode_txt0_string(decoded_root, decoded_offset, text_encoding=text_encoding)
        txt0_strings.append(
            {
                "index": index,
                "relative_offset_u32": relative_offset_u32,
                "decoded_offset": decoded_offset,
                **item,
            }
        )

    known_container_magics = [
        {"magic_ascii": "TSCR", "magic_u32": 0x52435354, "known_from": "raw_file_header"},
        {"magic_ascii": "TUTA", "magic_u32": 0x41545554, "known_from": "decoded_root_container"},
        {"magic_ascii": "TXT0", "magic_u32": 0x30545854, "known_from": "decoded_string_pool"},
        {"magic_ascii": "TCRP", "magic_u32": 0x50524354, "known_from": "runtime_parser"},
        {"magic_ascii": "M3H0", "magic_u32": 0x3048334D, "known_from": "runtime_parser"},
        {"magic_ascii": "M3P0", "magic_u32": 0x3050334D, "known_from": "runtime_parser"},
    ]
    container_summary = {
        "raw_outer_magic": "TSCR",
        "decoded_root_magic": "TUTA",
        "decoded_root_codec": "mode5_word_swap_xor",
        "txt0_string_pool_magic": "TXT0",
        "txt0_entry_count_u32": txt0_entry_count_u32,
        "status": "outer_container_decoded_txt0_string_pool_available",
    }

    return BtTextOuterDoc(
        format="TE_V2_BTTEXT_OUTER",
        source_path=source_path,
        raw_header={
            "magic_ascii": "TSCR",
            "total_size_u32": total_size_u32,
            "raw_entry_count_u32": raw_entry_count_u32,
            "key_seed_u32": key_seed_u32,
            "encoded_root_size_u32": len(encoded_root),
            "encoded_root_main_size_u32": len(encoded_root_main),
        },
        tuta_header={
            "magic_ascii": "TUTA",
            "container_size_u32": tuta_size_u32,
            "field_08_u32": tuta_field_08_u32,
            "field_0C_u32": tuta_field_0C_u32,
        },
        txt0_header={
            "magic_ascii": "TXT0",
            "container_offset": txt0_offset,
            "container_size_u32": txt0_size_u32,
            "entry_count_u32": txt0_entry_count_u32,
            "offset_table_base_u32": txt0_base_offset,
        },
        txt0_strings=txt0_strings,
        decoded_root_preview_hex=decoded_root[:512].hex(" "),
        decoded_root_sha256=hashlib.sha256(decoded_root).hexdigest(),
        known_container_magics=known_container_magics,
        container_summary=container_summary,
        decoded_root_bytes=decoded_root,
    )


def probe_bttext(path: Path, text_encoding: str = "cp932") -> BtTextOuterDoc:
    return _probe_bttext_bytes(path.read_bytes(), str(path), text_encoding=text_encoding)


def parse_bttext_text(path: Path, text_encoding: str = "cp932") -> BtTextTextDoc:
    outer = probe_bttext(path, text_encoding=text_encoding)
    entries: list[dict[str, object]] = []
    for item in outer.txt0_strings:
        entries.append(
            {
                "index": item["index"],
                "relative_offset_u32": item["relative_offset_u32"],
                "decoded_offset": item["decoded_offset"],
                "decoded": item["decoded"],
                "text": item["text"],
                "original_text": item["text"],
                "text_raw_hex": item["text_raw_hex"],
                "encoded_hex": item["encoded_hex"],
                "original_encoded_hex": item["encoded_hex"],
                "encoded_length": item["encoded_length"],
            }
        )
    return BtTextTextDoc(
        format="TE_V2_BTTEXT_TEXT",
        source_path=str(path),
        source_text_encoding=text_encoding,
        raw_header=outer.raw_header,
        tuta_header=outer.tuta_header,
        txt0_header=outer.txt0_header,
        entries=entries,
    )


def _encode_txt0_string(text: str, text_encoding: str) -> bytes:
    encoded_text = text.encode(text_encoding)
    out = bytearray()
    for index, byte in enumerate(encoded_text):
        out.append(byte ^ TXT0_XOR_PATTERN[index & 3])
    out.append(TXT0_XOR_PATTERN[len(encoded_text) & 3])
    return bytes(out)


def compile_bttext(doc: dict[str, object], text_encoding: str = "cp932") -> bytes:
    if str(doc.get("format")) != "TE_V2_BTTEXT_TEXT":
        raise ValueError("Unsupported BtText document format")
    source_path = Path(str(doc["source_path"]))
    source_text_encoding = str(doc.get("source_text_encoding", "cp932"))
    outer_doc = parse_bttext_text(source_path, text_encoding=source_text_encoding)
    outer = probe_bttext(source_path, text_encoding=source_text_encoding)

    encoded_entries: list[bytes] = []
    entry_offsets: list[int] = []
    current_relative_offset = 4 + 4 * len(doc["entries"])
    original_entries_by_index = {entry["index"]: entry for entry in outer_doc.entries}
    for entry in doc["entries"]:
        current_text = str(entry.get("text", ""))
        original_entry = original_entries_by_index[int(entry["index"])]
        original_text = str(original_entry.get("original_text", current_text))
        if current_text == original_text:
            encoded = bytes.fromhex(str(original_entry["original_encoded_hex"]))
        else:
            encoded = _encode_txt0_string(current_text, text_encoding)
        entry_offsets.append(current_relative_offset)
        encoded_entries.append(encoded)
        current_relative_offset += len(encoded)

    txt0_payload = bytearray()
    txt0_payload.extend(len(doc["entries"]).to_bytes(4, "little"))
    for relative_offset_u32 in entry_offsets:
        txt0_payload.extend(relative_offset_u32.to_bytes(4, "little"))
    for encoded in encoded_entries:
        txt0_payload.extend(encoded)
    desired_mod = int(doc["tuta_header"]["container_size_u32"]) & 3
    while (8 + len(txt0_payload)) & 3 != desired_mod:
        txt0_payload.append(0)

    txt0_container_size_u32 = 8 + len(txt0_payload)
    txt0_container = b"TXT0" + txt0_container_size_u32.to_bytes(4, "little") + bytes(txt0_payload)
    txt0_offset = int(doc["txt0_header"]["container_offset"])
    tuta_prefix = bytearray(outer.decoded_root_bytes[:txt0_offset])
    new_tuta_size = len(tuta_prefix) + len(txt0_container)
    struct.pack_into("<I", tuta_prefix, 4, new_tuta_size)
    struct.pack_into("<I", tuta_prefix, 12, txt0_container_size_u32)
    new_decoded_root = bytes(tuta_prefix) + txt0_container

    encoded_root_main_size = len(new_decoded_root) - (len(new_decoded_root) & 3)
    encoded_root = (
        encode_mode5_swapped(new_decoded_root[:encoded_root_main_size], int(doc["raw_header"]["key_seed_u32"]))
        + new_decoded_root[encoded_root_main_size:]
    )
    total_size_u32 = 16 + len(encoded_root)
    rebuilt = (
        b"TSCR"
        + total_size_u32.to_bytes(4, "little")
        + int(doc["raw_header"]["raw_entry_count_u32"]).to_bytes(4, "little")
        + int(doc["raw_header"]["key_seed_u32"]).to_bytes(4, "little")
        + encoded_root
    )
    return rebuilt


def write_text_doc(path: Path, doc: BtTextTextDoc) -> None:
    payload = {
        "format": doc.format,
        "source_path": doc.source_path,
        "source_text_encoding": doc.source_text_encoding,
        "raw_header": doc.raw_header,
        "tuta_header": doc.tuta_header,
        "txt0_header": doc.txt0_header,
        "entries": doc.entries,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def rebuild_bttext(doc: BtTextOuterDoc) -> bytes:
    encoded_root_main_size = len(doc.decoded_root_bytes) - (len(doc.decoded_root_bytes) & 3)
    encoded_root = (
        encode_mode5_swapped(doc.decoded_root_bytes[:encoded_root_main_size], int(doc.raw_header["key_seed_u32"]))
        + doc.decoded_root_bytes[encoded_root_main_size:]
    )
    rebuilt = (
        b"TSCR"
        + int(doc.raw_header["total_size_u32"]).to_bytes(4, "little")
        + int(doc.raw_header["raw_entry_count_u32"]).to_bytes(4, "little")
        + int(doc.raw_header["key_seed_u32"]).to_bytes(4, "little")
        + encoded_root
    )
    if len(rebuilt) != int(doc.raw_header["total_size_u32"]):
        raise ValueError("Rebuilt BtText size does not match raw header")
    return rebuilt


def write_probe(path: Path, output_path: Path) -> Path:
    doc = probe_bttext(path)
    payload = {
        "format": doc.format,
        "source_path": doc.source_path,
        "raw_header": doc.raw_header,
        "tuta_header": doc.tuta_header,
        "txt0_header": doc.txt0_header,
        "txt0_strings": doc.txt0_strings,
        "decoded_root_preview_hex": doc.decoded_root_preview_hex,
        "decoded_root_sha256": doc.decoded_root_sha256,
        "decoded_ascii_literals": extract_ascii_literals(doc.decoded_root_bytes),
        "known_container_magics": doc.known_container_magics,
        "container_summary": doc.container_summary,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
