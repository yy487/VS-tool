#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json

ENC_DEFAULT = "cp932"
TEXT_OP = 0x0000
END_TICK = 0xFFFF

# Sweet SCB u16-op grammar. z=NUL-terminated CP932 string, h=u16, r=rel16.
GRAMMAR: dict[int, str] = {
    0x00: "z",
    0x01: "h",
    0x02: "h",
    0x03: "z",
    0x04: "hzh",
    0x05: "h",
    0x08: "z",
    0x09: "",
    0x0B: "",
    0x0C: "r",
    0x0D: "hr",
    0x0E: "hr",
    0x0F: "r",
    0x10: "",
    0x11: "zr",
    0x15: "",
    0x16: "h",
    0x17: "h",
    0x18: "hh",
    0x19: "hh",
    0x1A: "hh",
    0x1B: "hh",
    0x1C: "r",
    0x1D: "r",
    0x1E: "r",
    0x1F: "h",
    0x25: "zhhhh",
    0x26: "zhhhh",
    0x27: "",
    0x28: "z",
    0x29: "",
    0x2A: "z",
    0x2B: "",
    0x2C: "",
    0x2E: "",
    0x2F: "",
    0x30: "z",
    0x31: "",
}

JUMP_OPS = {0x0C, 0x0D, 0x0E, 0x0F, 0x11, 0x1C, 0x1D, 0x1E}


def u16_at(b: bytes | bytearray, off: int) -> int:
    return b[off] | (b[off + 1] << 8)


def put_u16(out: bytearray, v: int) -> None:
    out += bytes((v & 0xFF, (v >> 8) & 0xFF))


def s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def encode_s16(v: int) -> int:
    if not -0x8000 <= v <= 0x7FFF:
        raise OverflowError(f"rel16 overflow: {v} is outside -32768..32767")
    return v & 0xFFFF


@dataclass
class Arg:
    kind: str                 # z/h/r
    value: Any
    raw_start: int
    raw_end: int


@dataclass
class Cmd:
    off: int
    op: int
    end: int
    args: list[Arg] = field(default_factory=list)
    raw: bytes = b""

    @property
    def text(self) -> str | None:
        for a in self.args:
            if a.kind == "z":
                return a.value
        return None

    @text.setter
    def text(self, value: str) -> None:
        for a in self.args:
            if a.kind == "z":
                a.value = value
                return
        raise ValueError(f"opcode 0x{self.op:04X} has no string operand")

    @property
    def jump_arg(self) -> Arg | None:
        for a in self.args:
            if a.kind == "r":
                return a
        return None

    @property
    def jump_field(self) -> int | None:
        a = self.jump_arg
        return None if a is None else a.raw_start

    @property
    def jump_target(self) -> int | None:
        a = self.jump_arg
        if a is None:
            return None
        return a.raw_start + int(a.value)


@dataclass
class ParsedSCB:
    path: Path
    data: bytes
    cmds: list[Cmd]
    tail: bytes

    @property
    def command_starts(self) -> set[int]:
        return {c.off for c in self.cmds}


def _read_z(data: bytes, off: int, encoding: str) -> tuple[str, int]:
    end = data.find(b"\x00", off)
    if end < 0:
        raise ValueError(f"unterminated string at 0x{off:X}")
    raw = data[off:end]
    try:
        return raw.decode(encoding), end + 1
    except UnicodeDecodeError as e:
        raise UnicodeDecodeError(e.encoding, e.object, e.start, e.end, f"{e.reason} at file offset 0x{off + e.start:X}") from e


def parse_scb(path: Path, encoding: str = ENC_DEFAULT) -> ParsedSCB:
    data = path.read_bytes()
    cmds: list[Cmd] = []
    off = 0
    while off < len(data):
        if off + 3 <= len(data) and data[off:off + 3] == b"\xff\x1f\x00":
            return ParsedSCB(path, data, cmds, data[off:])
        if off + 2 > len(data):
            raise ValueError(f"truncated opcode at 0x{off:X} in {path}")
        op = u16_at(data, off)
        pos = off + 2
        args: list[Arg] = []
        if op == END_TICK:
            cmd = Cmd(off, op, pos, args, data[off:pos])
            cmds.append(cmd)
            off = pos
            continue
        if op not in GRAMMAR:
            raise ValueError(f"unknown opcode 0x{op:04X} at 0x{off:X} in {path}")
        for kind in GRAMMAR[op]:
            if kind == "z":
                raw_start = pos
                text, pos = _read_z(data, pos, encoding)
                args.append(Arg("z", text, raw_start, pos))
            elif kind == "h":
                if pos + 2 > len(data):
                    raise ValueError(f"truncated u16 at 0x{pos:X} in {path}")
                args.append(Arg("h", u16_at(data, pos), pos, pos + 2))
                pos += 2
            elif kind == "r":
                if pos + 2 > len(data):
                    raise ValueError(f"truncated rel16 at 0x{pos:X} in {path}")
                args.append(Arg("r", s16(u16_at(data, pos)), pos, pos + 2))
                pos += 2
            else:
                raise AssertionError(kind)
        cmd = Cmd(off, op, pos, args, data[off:pos])
        cmds.append(cmd)
        off = pos
    return ParsedSCB(path, data, cmds, b"")


def encode_z(text: str, encoding: str, *, file: str = "", entry: str = "") -> bytes:
    try:
        return text.encode(encoding) + b"\x00"
    except UnicodeEncodeError as e:
        ch = text[e.start:e.end]
        where = f" in {file}" if file else ""
        ent = f" ({entry})" if entry else ""
        raise UnicodeEncodeError(e.encoding, e.object, e.start, e.end,
                                 f"cannot encode {ch!r}{where}{ent}; use CP932-safe text or a character mapping") from e


def rebuild_scb(parsed: ParsedSCB, encoding: str = ENC_DEFAULT) -> bytes:
    old_to_new: dict[int, int] = {}
    out = bytearray()
    patches: list[tuple[int, int, int, int]] = []  # old_cmd_off, old_field, new_field, old_target

    for cmd in parsed.cmds:
        new_cmd_off = len(out)
        old_to_new[cmd.off] = new_cmd_off
        put_u16(out, cmd.op)
        if cmd.op == END_TICK:
            continue
        for arg in cmd.args:
            if arg.kind == "z":
                out += encode_z(str(arg.value), encoding, file=parsed.path.name, entry=f"0x{cmd.off:04X}")
            elif arg.kind == "h":
                put_u16(out, int(arg.value))
            elif arg.kind == "r":
                old_field = arg.raw_start
                old_target = old_field + int(arg.value)
                new_field = len(out)
                put_u16(out, 0)
                patches.append((cmd.off, old_field, new_field, old_target))
            else:
                raise AssertionError(arg.kind)

    out += parsed.tail

    for old_cmd_off, old_field, new_field, old_target in patches:
        if old_target not in old_to_new:
            raise ValueError(
                f"jump target 0x{old_target:04X} from command 0x{old_cmd_off:04X} is not a command boundary"
            )
        new_target = old_to_new[old_target]
        new_rel = new_target - new_field
        v = encode_s16(new_rel)
        out[new_field:new_field + 2] = bytes((v & 0xFF, (v >> 8) & 0xFF))
    return bytes(out)


def iter_scb_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted([*path.glob("*.SCB"), *path.glob("*.scb")])
    return [path]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
