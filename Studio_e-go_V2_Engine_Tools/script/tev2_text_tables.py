from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


TABLE_RECORD_SIZE = 64


@dataclass
class TextTableDoc:
    format: str
    table_name: str
    source_path: str
    key_mode: int
    key_seed_u32: int
    record_size: int
    entries: list[dict[str, object]]


def decrypt_words(data: bytes, mode: int, seed_u32: int) -> bytes:
    if mode not in {0, 1, 2}:
        raise ValueError(f"Unsupported key mode: {mode}")
    if mode == 0:
        return data
    out = bytearray(data)
    state = seed_u32 & 0xFFFFFFFF
    for index in range(0, len(out), 4):
        if index + 4 > len(out):
            break
        if ((index // 4) & 0xFF) == 0:
            state = state ^ 0xFFFFFFFF if mode == 2 else (1 if state == 0 else 0)
        state = (state + 124076833) & 0xFFFFFFFF
        word = int.from_bytes(out[index : index + 4], "little") ^ state
        out[index : index + 4] = word.to_bytes(4, "little")
    return bytes(out)


def detect_table_mode(data: bytes) -> tuple[int, int]:
    # Current TE_V2 text tables observed in 月神楽 use mode=2 and seed=0.
    return 2, 0


def parse_table(path: Path, text_encoding: str = "cp932") -> TextTableDoc:
    raw = path.read_bytes()
    return parse_table_bytes(raw, table_name=path.name, source_path=str(path), text_encoding=text_encoding)


def parse_table_bytes(data: bytes, table_name: str, source_path: str, text_encoding: str = "cp932") -> TextTableDoc:
    mode, seed = detect_table_mode(data)
    plain = decrypt_words(data, mode, seed)
    entries: list[dict[str, object]] = []
    for index in range(0, len(plain), TABLE_RECORD_SIZE):
        chunk = plain[index : index + TABLE_RECORD_SIZE]
        if len(chunk) < TABLE_RECORD_SIZE:
            break
        text_raw = chunk.split(b"\x00", 1)[0]
        try:
            text = text_raw.decode(text_encoding)
            decoded = True
        except UnicodeDecodeError:
            text = ""
            decoded = False
        entries.append(
            {
                "index": index // TABLE_RECORD_SIZE,
                "decoded": decoded,
                "text": text,
                "original_text": text,
                "raw_hex": chunk.hex(),
            }
        )
    return TextTableDoc(
        format="TE_V2_TEXT_TABLE",
        table_name=table_name,
        source_path=source_path,
        key_mode=mode,
        key_seed_u32=seed,
        record_size=TABLE_RECORD_SIZE,
        entries=entries,
    )


def compile_table(doc: dict[str, object], text_encoding: str = "cp932") -> bytes:
    record_size = int(doc.get("record_size", TABLE_RECORD_SIZE))
    mode = int(doc.get("key_mode", 0))
    seed = int(doc.get("key_seed_u32", 0))
    out = bytearray()
    for entry in doc["entries"]:
        original = bytes.fromhex(str(entry["raw_hex"]))
        if len(original) != record_size:
            raise ValueError("Invalid raw record size")
        current_text = str(entry.get("text", ""))
        original_text = str(entry.get("original_text", current_text))
        if current_text == original_text:
            record = original
        else:
            encoded = current_text.encode(text_encoding)
            if len(encoded) + 1 > record_size:
                raise ValueError(f"Text too long for fixed table record: {current_text!r}")
            record = encoded + b"\x00" + (b"\x00" * (record_size - len(encoded) - 1))
        out.extend(record)
    return decrypt_words(bytes(out), mode, seed)


def write_doc(path: Path, doc: TextTableDoc) -> None:
    payload = {
        "format": doc.format,
        "table_name": doc.table_name,
        "source_path": doc.source_path,
        "key_mode": doc.key_mode,
        "key_seed_u32": doc.key_seed_u32,
        "record_size": doc.record_size,
        "entries": doc.entries,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
