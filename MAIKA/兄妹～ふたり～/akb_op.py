#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AKB/ADB script common opcode template and binary helpers.

Target format observed in TWO.EXE scripts:
- little-endian uint16 opcode
- most resource/text operands are CP932 C strings
- normal text:   0x0000 + b"KDKFxxxxx\\I7...\\0"
- voice:         0x0035 + voice_id C string, normally appears immediately before text
- choice block:  0x0006, repeated 0x0001 entries, 0x0007
                 choice entry = uint32 target_offset + C string
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from bisect import bisect_right
import json
import re
import struct
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

DEFAULT_ENCODING = "cp932"

MSG_RE = re.compile(r"^(?P<line_id>KDKF[0-9A-Za-z_]+)(?P<ctrl>\\I[0-9A-Za-z]+)(?P<body>.*)$", re.S)
NAME_RE = re.compile(r"^【(?P<name>[^】]+)】(?P<message>.*)$", re.S)


@dataclass(frozen=True)
class OpSpec:
    name: str
    kind: str = "fixed"       # fixed, cstr, choice, u16_cstr, double_cstr, var_fixed
    size: int = 0             # payload size for fixed
    sizes: Tuple[int, ...] = () # payload alternatives for var_fixed
    target_fields: Tuple[Tuple[int, int], ...] = ()  # (payload_offset, byte_size)


# This table is deliberately conservative and easy to extend.
# Unknown/complex visual-menu opcodes can be added here without touching extractor/injector.
OPCODES: Dict[int, OpSpec] = {
    0x0000: OpSpec("msg", "cstr"),
    0x0001: OpSpec("choice_entry", "choice", target_fields=((0, 4),)),
    0x0002: OpSpec("jump", "fixed", 4, target_fields=((0, 4),)),
    0x0004: OpSpec("set_or_cmp", "fixed", 5),
    0x0006: OpSpec("choice_begin", "fixed", 0),
    0x0007: OpSpec("choice_end", "fixed", 0),
    0x0010: OpSpec("cond_jump", "fixed", 9, target_fields=((5, 4),)),
    0x0011: OpSpec("wait", "fixed", 2),
    0x0014: OpSpec("effect", "fixed", 2),  # often argument 0x001e/0x0000/0x0031
    0x0016: OpSpec("cond_jump_16", "fixed", 9, target_fields=((5, 4),)),
    0x0018: OpSpec("call_script", "cstr"),
    0x0026: OpSpec("bgm", "cstr"),
    0x0028: OpSpec("mode_or_case", "var_fixed", sizes=(7, 10, 0, 5, 2, 4, 6, 8, 12)),
    0x002B: OpSpec("se", "cstr"),
    0x0032: OpSpec("menu_item32", "fixed", 6),
    0x0035: OpSpec("voice", "cstr"),
    0x0036: OpSpec("menu_begin36", "fixed", 0),
    0x0037: OpSpec("menu_pair37", "double_cstr"),
    0x0038: OpSpec("menu_item38", "var_fixed", sizes=(6, 4)),
    0x0040: OpSpec("offset40", "fixed", 4, target_fields=((0, 4),)),
    0x0041: OpSpec("word41", "fixed", 2),
    0x0046: OpSpec("bg", "cstr"),
    0x0062: OpSpec("indexed_cstr62", "u16_cstr"),
    0x0093: OpSpec("noop93", "fixed", 0),
    0x00B9: OpSpec("anim_or_label_b9", "cstr"),
    0x00BA: OpSpec("ending_label_ba", "u16_cstr"),
    0x00BE: OpSpec("marker_be", "fixed", 0),
    0x00C9: OpSpec("tachi_l", "cstr"),
    0x00CA: OpSpec("tachi_r", "cstr"),
    0x00F3: OpSpec("menu_table_f3", "fixed", 24),
    0x0102: OpSpec("dword102", "fixed", 4),
    0x0116: OpSpec("cond_jump_116", "fixed", 9, target_fields=((5, 4),)),
    0x0117: OpSpec("set_var_117", "fixed", 5),
    0x011E: OpSpec("marker_11e", "fixed", 0),
    0x013E: OpSpec("case_13e", "fixed", 8),
    0x0145: OpSpec("tachi", "cstr"),
    0x0147: OpSpec("movie", "cstr"),
    0x0148: OpSpec("movie_end", "fixed", 0),
}


@dataclass
class Instruction:
    start: int
    end: int
    opcode: int
    payload_start: int
    payload_end: int
    raw: bytes
    spec: OpSpec
    payload: bytes = b""
    text: Optional[str] = None
    target: Optional[int] = None
    # For choice/u16_cstr, prefix bytes before string operand.
    prefix: bytes = b""
    warnings: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.spec.name


class ParseError(Exception):
    pass


def u16(b: bytes, off: int) -> int:
    return struct.unpack_from("<H", b, off)[0]


def u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def p16(x: int) -> bytes:
    return struct.pack("<H", x & 0xFFFF)


def p32(x: int) -> bytes:
    return struct.pack("<I", x & 0xFFFFFFFF)


def find_cstr_end(data: bytes, pos: int) -> int:
    end = data.find(b"\x00", pos)
    if end < 0:
        raise ParseError(f"unterminated C string at 0x{pos:X}")
    return end + 1


def decode_cstr(raw: bytes, encoding: str = DEFAULT_ENCODING) -> str:
    return raw.decode(encoding, errors="strict")


def encode_cstr(text: str, encoding: str = DEFAULT_ENCODING, errors: str = "strict") -> bytes:
    return text.encode(encoding, errors=errors) + b"\x00"


def _valid_opcode_set() -> set[int]:
    return set(OPCODES)


def _next_looks_valid(data: bytes, pos: int) -> bool:
    if pos == len(data):
        return True
    if pos + 2 > len(data):
        return False
    return u16(data, pos) in _valid_opcode_set()


def parse_instruction(data: bytes, pos: int, *, encoding: str = DEFAULT_ENCODING) -> Instruction:
    if pos + 2 > len(data):
        raise ParseError(f"truncated opcode at 0x{pos:X}")

    opcode = u16(data, pos)
    spec = OPCODES.get(opcode)
    if spec is None:
        raise ParseError(f"unknown opcode 0x{opcode:04X} at 0x{pos:X}")

    p = pos + 2

    if spec.kind == "fixed":
        end = p + spec.size
        if end > len(data):
            raise ParseError(f"truncated opcode 0x{opcode:04X} at 0x{pos:X}")
        return Instruction(pos, end, opcode, p, end, data[pos:end], spec, data[p:end])

    if spec.kind == "var_fixed":
        # Pick the first payload size that leaves the next opcode on a valid boundary.
        for size in spec.sizes:
            end = p + size
            if end <= len(data) and _next_looks_valid(data, end):
                return Instruction(pos, end, opcode, p, end, data[pos:end], spec, data[p:end])
        raise ParseError(f"cannot size var opcode 0x{opcode:04X} at 0x{pos:X}")

    if spec.kind == "cstr":
        end = find_cstr_end(data, p)
        raw_s = data[p:end - 1]
        text = decode_cstr(raw_s, encoding)
        return Instruction(pos, end, opcode, p, end, data[pos:end], spec, raw_s, text=text)

    if spec.kind == "choice":
        if p + 4 > len(data):
            raise ParseError(f"truncated choice target at 0x{pos:X}")
        target = u32(data, p)
        s0 = p + 4
        end = find_cstr_end(data, s0)
        raw_s = data[s0:end - 1]
        text = decode_cstr(raw_s, encoding)
        return Instruction(pos, end, opcode, p, end, data[pos:end], spec, data[p:end],
                           text=text, target=target, prefix=data[p:s0])

    if spec.kind == "u16_cstr":
        if p + 2 > len(data):
            raise ParseError(f"truncated u16+cstr at 0x{pos:X}")
        s0 = p + 2
        end = find_cstr_end(data, s0)
        raw_s = data[s0:end - 1]
        text = decode_cstr(raw_s, encoding)
        return Instruction(pos, end, opcode, p, end, data[pos:end], spec, data[p:end],
                           text=text, prefix=data[p:s0])

    if spec.kind == "double_cstr":
        e1 = find_cstr_end(data, p)
        e2 = find_cstr_end(data, e1)
        # menu resources, not translation text
        return Instruction(pos, e2, opcode, p, e2, data[pos:e2], spec, data[p:e2])

    raise ParseError(f"unsupported op kind {spec.kind!r} at 0x{pos:X}")


def parse_adb(data: bytes, *, encoding: str = DEFAULT_ENCODING, strict: bool = True) -> List[Instruction]:
    out: List[Instruction] = []
    pos = 0
    while pos < len(data):
        try:
            ins = parse_instruction(data, pos, encoding=encoding)
            out.append(ins)
            pos = ins.end
        except Exception as e:
            if strict:
                raise
            # Recovery mode: preserve one byte and try to resync.
            # This is intended for analysis/extract fallback only, not safe full relocation.
            end = min(pos + 1, len(data))
            raw = data[pos:end]
            out.append(Instruction(pos, end, -1, pos, end, raw, OpSpec("raw_unknown", "fixed"), raw,
                                   warnings=[str(e)]))
            pos = end
    return out


def split_msg_text(s: str) -> Optional[Tuple[str, str, str, str, str]]:
    """
    Return (line_id, ctrl, name, message, full_body) or None if this C string is not a normal message.
    """
    m = MSG_RE.match(s)
    if not m:
        return None
    line_id = m.group("line_id")
    ctrl = m.group("ctrl")
    body = m.group("body")
    nm = NAME_RE.match(body)
    if nm:
        return line_id, ctrl, nm.group("name"), nm.group("message"), body
    return line_id, ctrl, "", body, body


def build_msg_text(line_id: str, ctrl: str, name: str, message: str) -> str:
    if name:
        return f"{line_id}{ctrl}【{name}】{message}"
    return f"{line_id}{ctrl}{message}"


def load_json(path: Path):
    return json.loads(path.read_text("utf-8"))


def dump_json(obj, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), "utf-8")


def iter_adb_files(path: Path) -> Iterator[Path]:
    if path.is_file():
        yield path
    else:
        yield from sorted(path.rglob("*.ADB"))


def make_relocator(replacements: Sequence[Tuple[int, int, int]]):
    """
    replacements: (old_start, old_end, new_len)
    Returns relocate(old_offset). Deltas apply after each replaced span.
    """
    points: List[int] = []
    deltas: List[int] = []
    acc = 0
    for old_start, old_end, new_len in sorted(replacements):
        acc += new_len - (old_end - old_start)
        points.append(old_end)
        deltas.append(acc)

    def relocate(old: int) -> int:
        idx = bisect_right(points, old) - 1
        if idx < 0:
            return old
        return old + deltas[idx]

    return relocate
