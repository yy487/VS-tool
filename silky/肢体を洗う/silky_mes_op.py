#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Silky MES VM opcode table and binary helpers for the current game sample.

Scope:
  - ARC entry MES bytes are XOR 0x55 encoded.
  - Decoded MES is a naked VM bytecode stream, not the older headered Silky MES.
  - This module is shared by extractor and injector.
  - Non-story scripts are intentionally not classified here; caller decides filtering.

Important formats confirmed from body.exe.c analysis:
  message record: 00 <msg_id:u32le> <cp932 cstring> 00
  arglist:        (F5 <raw bytes> FF | expression)* FF
  choice define:  13 <expr table_id> <table_rel:u32le>
  choice table:   count:u8 + repeated { condition_rel:u32le, text_rel:u32le }
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

XOR_KEY = 0x55
DEFAULT_ENCODING = "cp932"

# FUN_004083f0 generic argument-list users or likely users.
GENERIC_ARGLIST_OPS = {
    0x02, 0x03, 0x04, 0x0E, 0x0F,
    0x12, 0x14, 0x15,
    0x20, 0x21, 0x23, 0x24, 0x25, 0x26, 0x27,
    0x2D, 0x2E, 0x2F, 0x30, 0x31, 0x32, 0x33, 0x34,
}

# 0x22 is fixed-length no-arg in this VM; do not parse arglist for it.
FIXED_NOARG_OPS = {0x22, 0xFF}
SPECIAL_LIST_WITH_U16 = {0x05, 0x0A, 0x0C}
SPECIAL_EXPR_LIST = {0x06, 0x0B, 0x0D}
VALID_MAIN_OPS = set(range(0x00, 0x16)) | set(range(0x20, 0x28)) | set(range(0x2D, 0x35)) | {0xFF}

# File-level filter for story extraction.  Users can override in extractor CLI.
DEFAULT_SKIP_STEMS = {
    "art", "theater", "title", "jump", "def", "startup", "sound", "hint",
}

@dataclass
class ExprParse:
    tokens: list[Any]
    start: int
    end: int

@dataclass
class ArgListParse:
    args: list[Any]
    start: int
    end: int

@dataclass
class MessageRecord:
    offset: int
    end: int
    msg_id: int
    raw: bytes
    text: str

@dataclass
class ChoiceItem:
    index: int
    cond_field: int
    text_field: int
    cond_rel: int
    text_rel: int
    message: Optional[MessageRecord]

@dataclass
class ChoiceTable:
    define_offset: int
    table_field: int
    table_rel: int
    table_abs: int
    table_id_expr: ExprParse
    count: int
    items: list[ChoiceItem]

@dataclass
class Command:
    offset: int
    opcode: int
    end: int
    kind: str
    args: list[Any]
    target_field: Optional[int] = None
    target_rel: Optional[int] = None
    message: Optional[MessageRecord] = None

@dataclass
class Relocation:
    field_pos: int
    target_rel: int
    kind: str

@dataclass(frozen=True)
class OpcodeSpec:
    opcode: int
    name: str
    length: str
    variable: bool
    subopcode: str
    reloc: bool
    note: str

OPCODE_TABLE: dict[int, OpcodeSpec] = {
    0x00: OpcodeSpec(0x00, "MESSAGE", "1 + 4 + cstring", True, "renderer text/control", False, "剧情文本记录 00 <u32 id> <text> 00"),
    0x01: OpcodeSpec(0x01, "JUMP", "1 + u32", False, "no", True, "无条件跳转，u32 为 base-relative target"),
    0x02: OpcodeSpec(0x02, "CALLBACK_60", "unknown/arglist-like", True, "unknown", False, "剧情注入不生成"),
    0x03: OpcodeSpec(0x03, "SCRIPT_RETURN_SET", "1 + arglist", True, "arglist", False, "脚本返回/切换状态"),
    0x04: OpcodeSpec(0x04, "SCRIPT_EXEC", "1 + arglist", True, "arglist", False, "执行/切换 MES；参数通常含脚本名"),
    0x05: OpcodeSpec(0x05, "SET_NIBBLE_SEQ_U16", "1 + u16 + expr* + FF", True, "expr", False, "连续写 4-bit flag"),
    0x06: OpcodeSpec(0x06, "SET_NIBBLE_SEQ_EXPR", "1 + expr + expr* + FF", True, "expr", False, "连续写 4-bit flag，起点来自表达式"),
    0x07: OpcodeSpec(0x07, "SET_DWORD_VAR", "1 + u8 + expr", True, "expr", False, "写 dword 变量"),
    0x08: OpcodeSpec(0x08, "SET_BYTE_ARRAY", "1 + u8 + expr index + expr* + FF", True, "expr", False, "写 byte 数组"),
    0x09: OpcodeSpec(0x09, "SET_WORD_ARRAY", "1 + u8 + expr index + expr* + FF", True, "expr", False, "写 word 数组"),
    0x0A: OpcodeSpec(0x0A, "SET_WORD_SEQ_U16", "1 + u16 + expr* + FF", True, "expr", False, "连续写 16-bit 系统变量"),
    0x0B: OpcodeSpec(0x0B, "SET_WORD_SEQ_EXPR", "1 + expr + expr* + FF", True, "expr", False, "连续写 16-bit 系统变量，起点来自表达式"),
    0x0C: OpcodeSpec(0x0C, "SET_DWORD_SEQ_U16", "1 + u16 + expr* + FF", True, "expr", False, "连续写 32-bit 变量"),
    0x0D: OpcodeSpec(0x0D, "SET_DWORD_SEQ_EXPR", "1 + expr + expr* + FF", True, "expr", False, "连续写 32-bit 变量，起点来自表达式"),
    0x0E: OpcodeSpec(0x0E, "SYSTEM_CONTROL", "1 + arglist", True, "arglist", False, "系统/流程控制"),
    0x0F: OpcodeSpec(0x0F, "AUDIO_CONTROL", "1 + arglist", True, "subcommand", False, "音量/音频通道控制"),
    0x10: OpcodeSpec(0x10, "JZ", "1 + expr + u32", True, "expr", True, "表达式为 0 时跳转"),
    0x11: OpcodeSpec(0x11, "CALL", "1 + expr + u32", True, "expr", True, "call/subroutine，保存返回帧后跳转"),
    0x12: OpcodeSpec(0x12, "CALL_FRAME_EXEC", "1 + arglist", True, "arglist", False, "执行已保存 call frame"),
    0x13: OpcodeSpec(0x13, "CHOICE_TABLE_DEFINE", "1 + expr + u32", True, "expr + table", True, "定义选择支表，u32 指向 choice table"),
    0x14: OpcodeSpec(0x14, "CHOICE_SHOW", "1 + arglist", True, "arglist", False, "显示/执行选择支表"),
    0x15: OpcodeSpec(0x15, "FORMAT_VALUE", "1 + arglist", True, "arglist", False, "数值格式化/显示"),
    0x20: OpcodeSpec(0x20, "WAIT_INPUT", "1 + arglist", True, "arglist", False, "等待/输入推进"),
    0x21: OpcodeSpec(0x21, "SYSTEM_WAIT", "1 + arglist", True, "arglist", False, "系统等待/流程"),
    0x22: OpcodeSpec(0x22, "DISPLAY_FORCE_END", "1", False, "no", False, "强制结束当前显示/等待状态"),
    0x23: OpcodeSpec(0x23, "RESOURCE_LOAD_A", "1 + arglist", True, "arglist", False, "资源名设置/载入，常见 g24/wav"),
    0x24: OpcodeSpec(0x24, "LAYER_OP_24", "1 + arglist", True, "arglist", False, "画面/图层操作"),
    0x25: OpcodeSpec(0x25, "LAYER_OP_25", "1 + arglist", True, "arglist", False, "画面/图层操作"),
    0x26: OpcodeSpec(0x26, "LAYER_OP_26", "1 + arglist", True, "arglist", False, "画面/图层操作"),
    0x27: OpcodeSpec(0x27, "LAYER_OP_27", "1 + arglist", True, "arglist", False, "画面/图层操作"),
    0x2D: OpcodeSpec(0x2D, "MOUSE_CONTROL", "1 + arglist", True, "subcommand", False, "鼠标 show/hide/坐标"),
    0x2E: OpcodeSpec(0x2E, "GRAPHIC_CONTROL", "1 + arglist", True, "subcommand", False, "图像/立绘/动画/遮罩"),
    0x2F: OpcodeSpec(0x2F, "BGM", "1 + arglist", True, "arglist", False, "BGM 资源"),
    0x30: OpcodeSpec(0x30, "SOUND", "1 + arglist", True, "arglist", False, "音效/voice 资源"),
    0x31: OpcodeSpec(0x31, "VOICE_EFFECT", "1 + arglist", True, "arglist", False, "voice/effect 播放"),
    0x32: OpcodeSpec(0x32, "AUDIO_WAIT_CONTROL", "1 + arglist", True, "subcommand", False, "音频/通道/等待控制"),
    0x33: OpcodeSpec(0x33, "PARSE_ARGS_ONLY", "1 + arglist", True, "arglist", False, "只解析参数槽"),
    0x34: OpcodeSpec(0x34, "LAYER_OP_34", "1 + arglist", True, "arglist", False, "画面/图层操作"),
    0xFF: OpcodeSpec(0xFF, "END", "1", False, "no", False, "VM 结束"),
}


def xor55(data: bytes) -> bytes:
    return bytes(b ^ XOR_KEY for b in data)


def decode_mes(data: bytes, already_decoded: bool = False) -> bytes:
    return data if already_decoded else xor55(data)


def encode_mes(decoded: bytes, already_encoded: bool = False) -> bytes:
    return decoded if already_encoded else xor55(decoded)


def u16(data: bytes, pos: int) -> int:
    return struct.unpack_from("<H", data, pos)[0]


def u32(data: bytes, pos: int) -> int:
    return struct.unpack_from("<I", data, pos)[0]


def p32(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def read_cstring(data: bytes, pos: int) -> tuple[bytes, int]:
    end = data.find(b"\x00", pos)
    if end < 0:
        return data[pos:], len(data)
    return data[pos:end], end + 1


def decode_text(raw: bytes, encoding: str = DEFAULT_ENCODING) -> str:
    return raw.decode(encoding, errors="replace")


def encode_text(text: str, encoding: str = DEFAULT_ENCODING, errors: str = "strict") -> bytes:
    return text.encode(encoding, errors=errors)


def is_probably_story_mes(path: Path, skip_stems: Iterable[str] = DEFAULT_SKIP_STEMS) -> bool:
    stem = path.stem.lower()
    skips = {s.lower() for s in skip_stems}
    return not any(stem == s or stem.startswith(s + "-") or stem.startswith(s + "_") for s in skips)


def parse_expr(data: bytes, pos: int) -> ExprParse:
    start = pos
    tokens: list[Any] = []
    n = len(data)
    while pos < n:
        b = data[pos]
        pos += 1
        if b == 0xFF:
            return ExprParse(tokens, start, pos)
        if b < 0x80:
            tokens.append(b)
        elif b < 0xA0:
            tokens.append({"var": b})
        elif b < 0xE0:
            tokens.append({"arr8": b})
        elif b in (0xE5, 0xF1, 0xF3, 0xF6, 0xF8):
            if pos + 2 > n:
                tokens.append({"trunc_op": b})
                return ExprParse(tokens, start, n)
            tokens.append({"op": b, "u16": u16(data, pos)})
            pos += 2
        elif b == 0xF2:
            if pos + 4 > n:
                tokens.append({"trunc_op": b})
                return ExprParse(tokens, start, n)
            tokens.append({"op": b, "u32": u32(data, pos)})
            pos += 4
        else:
            tokens.append({"op": b})
    return ExprParse(tokens, start, pos)


def eval_simple_expr(expr: ExprParse) -> Optional[int]:
    """Evaluate only the simple constants commonly used as table ids.

    Returns None for non-trivial VM expressions.
    """
    if len(expr.tokens) == 1 and isinstance(expr.tokens[0], int):
        return expr.tokens[0]
    if len(expr.tokens) == 1 and isinstance(expr.tokens[0], dict):
        t = expr.tokens[0]
        if t.get("op") == 0xF1:
            return int(t["u16"])
        if t.get("op") == 0xF2:
            return int(t["u32"])
    return None


def parse_arglist(data: bytes, pos: int, encoding: str = DEFAULT_ENCODING) -> ArgListParse:
    start = pos
    args: list[Any] = []
    n = len(data)
    while pos < n:
        b = data[pos]
        if b == 0xFF:
            return ArgListParse(args, start, pos + 1)
        if b == 0xF5:
            s0 = pos + 1
            end = data.find(b"\xFF", s0)
            if end < 0:
                raw = data[s0:]
                args.append({"type": "str", "text": decode_text(raw, encoding), "raw_hex": raw.hex(" ")})
                return ArgListParse(args, start, n)
            raw = data[s0:end]
            args.append({"type": "str", "text": decode_text(raw, encoding), "raw_hex": raw.hex(" ")})
            pos = end + 1
        else:
            expr = parse_expr(data, pos)
            args.append({"type": "expr", "tokens": expr.tokens, "start": expr.start, "end": expr.end})
            pos = expr.end
    return ArgListParse(args, start, pos)


def parse_message_record(data: bytes, pos: int, encoding: str = DEFAULT_ENCODING) -> Optional[MessageRecord]:
    if pos < 0 or pos + 6 > len(data) or data[pos] != 0x00:
        return None
    msg_id = u32(data, pos + 1)
    raw, end = read_cstring(data, pos + 5)
    if end <= pos + 5:
        return None
    try:
        text = raw.decode(encoding, errors="strict")
    except UnicodeDecodeError:
        return None
    return MessageRecord(pos, end, msg_id, raw, text)


def build_message_record(msg_id: int, text: str, encoding: str = DEFAULT_ENCODING, errors: str = "strict") -> bytes:
    return b"\x00" + p32(msg_id) + encode_text(text, encoding, errors) + b"\x00"


def parse_command(data: bytes, pos: int, encoding: str = DEFAULT_ENCODING) -> Command:
    n = len(data)
    if not (0 <= pos < n):
        return Command(pos, -1, pos, "eof", [])
    op = data[pos]

    if op == 0x00:
        msg = parse_message_record(data, pos, encoding)
        if msg is not None:
            return Command(pos, op, msg.end, "message", [{"msg_id": msg.msg_id}], message=msg)
        return Command(pos, op, min(pos + 1, n), "op_00_unknown", [])

    if op == 0x01 and pos + 5 <= n:
        return Command(pos, op, pos + 5, "jump", [], target_field=pos + 1, target_rel=u32(data, pos + 1))

    if op in (0x10, 0x11, 0x13):
        expr = parse_expr(data, pos + 1)
        field = expr.end
        target = u32(data, field) if field + 4 <= n else None
        end = field + 4 if target is not None else field
        kind = {0x10: "jump_if_zero", 0x11: "call", 0x13: "choice_define"}[op]
        return Command(pos, op, min(end, n), kind, [{"expr": expr.tokens, "expr_start": expr.start, "expr_end": expr.end}], target_field=field if target is not None else None, target_rel=target)

    if op == 0x07 and pos + 2 <= n:
        expr = parse_expr(data, pos + 2)
        return Command(pos, op, expr.end, "set_dword_var", [{"index": data[pos + 1]}, {"expr": expr.tokens}])

    if op in (0x08, 0x09) and pos + 2 <= n:
        p = pos + 2
        args: list[Any] = [{"base_slot": data[pos + 1]}]
        while p < n and data[p] != 0xFF:
            expr = parse_expr(data, p)
            args.append({"expr": expr.tokens})
            p = expr.end
        if p < n and data[p] == 0xFF:
            p += 1
        return Command(pos, op, p, f"op_{op:02x}_indexed_expr_list", args)

    if op in SPECIAL_LIST_WITH_U16 and pos + 3 <= n:
        p = pos + 3
        args = [{"u16": u16(data, pos + 1)}]
        while p < n and data[p] != 0xFF:
            expr = parse_expr(data, p)
            args.append({"expr": expr.tokens})
            p = expr.end
        if p < n and data[p] == 0xFF:
            p += 1
        return Command(pos, op, p, f"op_{op:02x}_u16_expr_list", args)

    if op in SPECIAL_EXPR_LIST:
        p = pos + 1
        args = []
        while p < n and data[p] != 0xFF:
            expr = parse_expr(data, p)
            args.append({"expr": expr.tokens})
            p = expr.end
        if p < n and data[p] == 0xFF:
            p += 1
        return Command(pos, op, p, f"op_{op:02x}_expr_list", args)

    if op in GENERIC_ARGLIST_OPS:
        parsed = parse_arglist(data, pos + 1, encoding)
        return Command(pos, op, parsed.end, f"op_{op:02x}_arglist", parsed.args)

    if op in FIXED_NOARG_OPS:
        return Command(pos, op, pos + 1, "end" if op == 0xFF else f"op_{op:02x}", [])

    # Keep unknown valid/suspicious byte as 1 byte so disassembly can continue.
    return Command(pos, op, min(pos + 1, n), f"op_{op:02x}_unknown", [])


def iter_linear_commands(data: bytes, encoding: str = DEFAULT_ENCODING, limit: Optional[int] = None) -> list[Command]:
    out: list[Command] = []
    pos = 0
    steps = 0
    while 0 <= pos < len(data):
        cmd = parse_command(data, pos, encoding)
        out.append(cmd)
        if cmd.end <= pos:
            break
        pos = cmd.end
        steps += 1
        if limit is not None and steps >= limit:
            break
    return out


def parse_choice_table(data: bytes, table_rel: int, define_offset: int = -1, table_field: int = -1, table_id_expr: Optional[ExprParse] = None, encoding: str = DEFAULT_ENCODING) -> Optional[ChoiceTable]:
    pos = table_rel
    if not (0 <= pos < len(data)):
        return None
    count = data[pos]
    if count == 0 or count > 32:
        return None
    end = pos + 1 + count * 8
    if end > len(data):
        return None
    items: list[ChoiceItem] = []
    p = pos + 1
    for i in range(count):
        cond_field = p
        text_field = p + 4
        cond_rel = u32(data, cond_field)
        text_rel = u32(data, text_field)
        msg = parse_message_record(data, text_rel, encoding) if text_rel else None
        items.append(ChoiceItem(i, cond_field, text_field, cond_rel, text_rel, msg))
        p += 8
    return ChoiceTable(define_offset, table_field, table_rel, pos, table_id_expr or ExprParse([], -1, -1), count, items)




def choice_table_end(table: ChoiceTable) -> int:
    """Return the first byte after a parsed choice table data block."""
    return table.table_abs + 1 + table.count * 8


def choice_successor_after_define(data: bytes, cmd: Command, encoding: str = DEFAULT_ENCODING) -> int:
    """Return the executable successor after opcode 0x13.

    Current-game 0x13 is followed by choice text records and the choice table
    data block before execution continues at the 0x14 CHOICE_SHOW command.
    Therefore CFG traversal must not continue at cmd.end; doing so treats
    choice data/message bytes as executable opcodes and corrupts extraction and
    relocation.
    """
    if cmd.opcode != 0x13 or cmd.target_rel is None:
        return cmd.end
    expr = ExprParse(cmd.args[0].get("expr", []), cmd.args[0].get("expr_start", -1), cmd.args[0].get("expr_end", -1)) if cmd.args else ExprParse([], -1, -1)
    table = parse_choice_table(data, cmd.target_rel, cmd.offset, cmd.target_field or -1, expr, encoding)
    if table is None:
        return cmd.end
    end = choice_table_end(table)
    if cmd.end <= end <= len(data):
        return end
    return cmd.end

def find_choice_tables(data: bytes, commands: Optional[list[Command]] = None, encoding: str = DEFAULT_ENCODING) -> list[ChoiceTable]:
    commands = commands if commands is not None else iter_linear_commands(data, encoding)
    tables: list[ChoiceTable] = []
    seen: set[int] = set()
    for cmd in commands:
        if cmd.opcode == 0x13 and cmd.target_field is not None and cmd.target_rel is not None:
            expr = ExprParse(cmd.args[0].get("expr", []), cmd.args[0].get("expr_start", -1), cmd.args[0].get("expr_end", -1)) if cmd.args else ExprParse([], -1, -1)
            table = parse_choice_table(data, cmd.target_rel, cmd.offset, cmd.target_field, expr, encoding)
            if table and table.table_rel not in seen:
                seen.add(table.table_rel)
                tables.append(table)
    return tables


def collect_relocations(data: bytes, commands: Optional[list[Command]] = None, encoding: str = DEFAULT_ENCODING) -> list[Relocation]:
    commands = commands if commands is not None else iter_linear_commands(data, encoding)
    relocs: list[Relocation] = []
    for cmd in commands:
        if cmd.target_field is not None and cmd.target_rel is not None:
            if cmd.opcode in (0x01, 0x10, 0x11, 0x13):
                relocs.append(Relocation(cmd.target_field, cmd.target_rel, cmd.kind))
    for table in find_choice_tables(data, commands, encoding):
        for item in table.items:
            if item.cond_rel:
                relocs.append(Relocation(item.cond_field, item.cond_rel, "choice_cond"))
            if item.text_rel:
                relocs.append(Relocation(item.text_field, item.text_rel, "choice_text"))
    # Deduplicate by field position.
    best: dict[int, Relocation] = {}
    for r in relocs:
        best[r.field_pos] = r
    return [best[k] for k in sorted(best)]


def scan_all_message_records(data: bytes, encoding: str = DEFAULT_ENCODING, min_chars: int = 1) -> list[MessageRecord]:
    """Conservative byte scan for message records.

    Used as a supplement for choice text records that are data-referenced rather
    than reached by linear VM walk.  Requires opcode 00 + valid CP932 cstring.
    """
    out: list[MessageRecord] = []
    i = 0
    while i < len(data) - 6:
        if data[i] != 0x00:
            i += 1
            continue
        msg = parse_message_record(data, i, encoding)
        if msg is None or msg.end <= i:
            i += 1
            continue
        text = msg.text.strip()
        # Keep Japanese full-width / kana / CJK looking strings; avoid empty ids/zero padding.
        if len(text) >= min_chars and any(ord(ch) >= 0x3000 for ch in text):
            out.append(msg)
            i = msg.end
        else:
            i += 1
    return out


def make_offset_mapper(replacements: list[tuple[int, int, int]]):
    """Create old->new offset mapper.

    replacements: list of (old_start, old_end, new_len), sorted and non-overlap.
    """
    reps = sorted(replacements)
    def map_offset(old: int) -> int:
        delta = 0
        for start, end, new_len in reps:
            if old < start:
                break
            old_len = end - start
            if start <= old < end:
                return start + delta
            delta += new_len - old_len
        return old + delta
    return map_offset


def rebuild_with_replacements(data: bytes, replacements: list[tuple[int, int, bytes]]) -> tuple[bytes, Any]:
    reps = sorted(replacements, key=lambda x: x[0])
    prev = 0
    chunks: list[bytes] = []
    map_specs: list[tuple[int, int, int]] = []
    for start, end, new_bytes in reps:
        if start < prev:
            raise ValueError(f"overlapping replacements near 0x{start:X}")
        if not (0 <= start <= end <= len(data)):
            raise ValueError(f"replacement out of range: 0x{start:X}-0x{end:X}")
        chunks.append(data[prev:start])
        chunks.append(new_bytes)
        map_specs.append((start, end, len(new_bytes)))
        prev = end
    chunks.append(data[prev:])
    return b"".join(chunks), make_offset_mapper(map_specs)


def apply_relocations(new_data: bytearray, relocs: list[Relocation], map_offset) -> list[str]:
    warnings: list[str] = []
    for r in relocs:
        new_field = map_offset(r.field_pos)
        new_target = map_offset(r.target_rel)
        if not (0 <= new_field + 4 <= len(new_data)):
            warnings.append(f"skip relocation {r.kind}: field 0x{r.field_pos:X}->0x{new_field:X} out of range")
            continue
        new_data[new_field:new_field + 4] = p32(new_target)
    return warnings


def iter_cfg_commands(data: bytes, encoding: str = DEFAULT_ENCODING, entry: int = 0, max_commands: int = 100000) -> list[Command]:
    """Parse executable VM commands by following control flow from entry.

    This avoids treating choice tables, skipped data blocks and CP932 payload bytes
    as main opcodes.  Choice-table text records must still be collected through
    find_choice_tables().
    """
    work = [entry]
    visited: set[int] = set()
    commands: dict[int, Command] = {}

    def add_target(t: Optional[int]) -> None:
        if t is not None and 0 <= t < len(data) and t not in visited:
            work.append(t)

    while work and len(commands) < max_commands:
        pos = work.pop()
        while 0 <= pos < len(data) and pos not in visited and len(commands) < max_commands:
            op = data[pos]
            if op not in VALID_MAIN_OPS:
                break
            cmd = parse_command(data, pos, encoding)
            if cmd.end <= pos or cmd.end > len(data):
                break
            visited.add(pos)
            commands[pos] = cmd

            if op == 0xFF:
                break
            if op == 0x01:
                add_target(cmd.target_rel)
                break
            if op in (0x10, 0x11):
                add_target(cmd.target_rel)
                pos = cmd.end
                continue
            if op == 0x13:
                # 0x13 defines a choice table.  The bytes immediately after the
                # command are normally choice-text records and the table data;
                # execution resumes at the 0x14 command after that table.
                pos = choice_successor_after_define(data, cmd, encoding)
                continue
            pos = cmd.end

    return [commands[k] for k in sorted(commands)]
