#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Common parser/rebuilder for Angel/Silky EVIT .snc scripts.

This module operates on engine-decompressed .snc files whose magic is EVIT.
String references use the engine's word-addressed st form:

    string_address = (string_base + st_ref) * 2

The non-equal-length injector works by rebuilding the string pool and then
rewriting every st reference in the VM code area.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import struct
from typing import Dict, Iterable, List, Optional, Tuple

MAGIC = b"EVIT"

# Word tags observed in Angel.exe / scripts. Stored little-endian in file.
ST = 0x7473  # 'st'
RN = 0x6E72  # 'rn'
HN = 0x6E68  # 'hn'
VL = 0x766C  # 'vl'
EF = 0x6665  # 'ef'

CHOICE_OP = 0x0081
MAP_OPS = {0x0080, 0x0088}
# 0x30 is the main message display op. Other values are kept because they
# appear to consume display strings in this engine family.
MESSAGE_OPS = {0x0030, 0x0034, 0x0045, 0x0095, 0x0096}

RESOURCE_HINT_PREFIXES = (
    "HBG", "HSE", "HSE", "HCG", "HEV", "HH", "V", "bgm", "BGM",
    "se", "SE", "stand", "face", "gray", "white", "black", "map", "frame",
    "window", "ef", "EV", "sys", "title",
)

@dataclass
class SncHeader:
    magic: int
    string_base: int
    vl_base: int
    ef_base: int
    code_start: int
    file_size: int
    var_count: int

    @classmethod
    def from_bytes(cls, data: bytes) -> "SncHeader":
        if len(data) < 28 or data[:4] != MAGIC:
            raise ValueError("not an engine-decompressed EVIT .snc file")
        return cls(*struct.unpack_from("<7I", data, 0))

    def pack(self) -> bytes:
        return struct.pack(
            "<7I", self.magic, self.string_base, self.vl_base, self.ef_base,
            self.code_start, self.file_size, self.var_count,
        )

    @property
    def string_start_off(self) -> int:
        return self.string_base * 2

    @property
    def string_end_word(self) -> int:
        return min(self.vl_base, self.ef_base, self.code_start)

    @property
    def string_end_off(self) -> int:
        return self.string_end_word * 2

    @property
    def code_start_off(self) -> int:
        return self.code_start * 2


def load_snc(path: Path) -> Tuple[bytes, SncHeader, List[int]]:
    data = path.read_bytes()
    h = SncHeader.from_bytes(data)
    usable = min(h.file_size, len(data))
    words = list(struct.unpack("<%dH" % (usable // 2), data[:usable // 2 * 2]))
    return data[:usable], h, words


def decode_text(raw: bytes, encoding: str = "cp932") -> str:
    return raw.decode(encoding, errors="replace")


def encode_text(text: str, encoding: str = "cp932", errors: str = "strict") -> bytes:
    return text.encode(encoding, errors=errors)


def clean_msg(s: str) -> str:
    # The scripts often keep one ASCII trailing blank as display padding.
    return s.rstrip(" ")


def split_name_msg(s: str) -> Tuple[Optional[str], str]:
    """Split engine text into optional speaker name and message.

    The raw SNC string often stores spoken lines as:
        name\n「dialogue」

    Narration may also contain line breaks, so a bare first line must not be
    treated as a name.  We only split when the first line is short and the
    following text starts with a dialogue quote.
    """
    s = clean_msg(s)
    if "\\n" in s:
        first, rest = s.split("\\n", 1)
        rest_l = rest.lstrip()
        if (
            first
            and len(first) <= 24
            and "「" not in first
            and "」" not in first
            and "『" not in first
            and "』" not in first
            and rest_l.startswith(("「", "『"))
        ):
            return first, rest
    return None, s


def join_name_msg(name: Optional[str], msg: str) -> str:
    return f"{name}\\n{msg}" if name else msg


def is_likely_resource(s: str) -> bool:
    t = s.strip()
    if not t:
        return True
    if t.startswith(RESOURCE_HINT_PREFIXES):
        return True
    # Compact ASCII identifiers are usually resource names.
    if t.isascii() and len(t) <= 24:
        if not any(ch in t for ch in " .,!?;:'\"（）()[]{}<>「」『』、。？！…"):
            return True
    return False


def collect_string_records(data: bytes, h: SncHeader, encoding: str = "cp932") -> List[Tuple[int, str, bytes]]:
    """Return ordered (ref, decoded_text, raw_bytes) records from the string pool."""
    start = h.string_start_off
    end = min(h.string_end_off, len(data))
    if start < 28 or end < start:
        raise ValueError(f"bad string range: 0x{start:X}..0x{end:X}")
    out: List[Tuple[int, str, bytes]] = []
    p = start
    while p < end:
        q = data.find(b"\0", p, end)
        if q < 0:
            # Malformed tail. Keep the tail as one last record rather than dropping it.
            q = end
        raw = data[p:q]
        ref = p // 2 - h.string_base
        out.append((ref, decode_text(raw, encoding), raw))
        p = q + 1
        if p & 1:
            p += 1
    return out


def collect_strings(data: bytes, h: SncHeader, encoding: str = "cp932") -> Dict[int, str]:
    return {ref: text for ref, text, _raw in collect_string_records(data, h, encoding)}


def iter_code_st_refs(words: List[int], h: SncHeader) -> Iterable[Tuple[int, int]]:
    """Yield (word_index_of_ST, ref) for st refs in the VM code area."""
    i = h.code_start
    while i + 1 < len(words):
        if words[i] == ST:
            yield i, words[i + 1]
            i += 2
        else:
            i += 1


def rebuild_with_new_strings(
    data: bytes,
    h: SncHeader,
    replacements: Dict[int, str],
    *,
    encoding: str = "cp932",
    errors: str = "strict",
) -> Tuple[bytes, Dict[int, int]]:
    """Rebuild string pool and rewrite all st refs in VM code.

    replacements maps old string ref -> new full string. All other strings are
    preserved as decoded text and re-encoded.
    """
    records = collect_string_records(data, h, encoding)
    old_string_start = h.string_start_off
    old_string_end = h.string_end_off
    old_code_start = h.code_start_off
    file_end = min(h.file_size, len(data))

    prefix = bytearray(data[:old_string_start])
    label_bytes = bytearray(data[old_string_end:old_code_start])
    code_bytes = bytearray(data[old_code_start:file_end])

    new_pool = bytearray()
    ref_map: Dict[int, int] = {}
    for old_ref, old_text, old_raw in records:
        new_ref = len(new_pool) // 2
        ref_map[old_ref] = new_ref
        text = replacements.get(old_ref, old_text)
        raw = encode_text(text, encoding=encoding, errors=errors)
        new_pool += raw + b"\0"
        if len(new_pool) & 1:
            new_pool += b"\0"

    # Rewrite st references in code only. Label tables are relative to code_start
    # and do not contain string refs in observed scripts.
    if len(code_bytes) % 2:
        code_bytes += b"\0"
    code_words = list(struct.unpack("<%dH" % (len(code_bytes) // 2), code_bytes))
    rewritten = 0
    i = 0
    while i + 1 < len(code_words):
        if code_words[i] == ST and code_words[i + 1] in ref_map:
            code_words[i + 1] = ref_map[code_words[i + 1]]
            rewritten += 1
            i += 2
        else:
            i += 1
    code_bytes = bytearray(struct.pack("<%dH" % len(code_words), *code_words))

    old_pool_len = old_string_end - old_string_start
    delta_bytes = len(new_pool) - old_pool_len
    if delta_bytes % 2:
        raise AssertionError("string pool delta must be word-aligned")
    delta_words = delta_bytes // 2

    new_h = SncHeader(
        h.magic,
        h.string_base,
        h.vl_base + delta_words,
        h.ef_base + delta_words,
        h.code_start + delta_words,
        0,
        h.var_count,
    )
    new_data = prefix + new_pool + label_bytes + code_bytes
    new_h.file_size = len(new_data)
    new_data[0:28] = new_h.pack()
    return bytes(new_data), ref_map


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, obj, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2 if pretty else None), encoding="utf-8")
