# -*- coding: utf-8 -*-
"""
AI6WIN 剧本文本提取/注入共用层。

本模块复用 AI6WINScriptTool 的反汇编/重汇编能力，只在反汇编文本层处理
STR_PRIMARY 指令，避免手写整套 MES 指令重定位逻辑。

JSON 字段保持项目常用格式：
    可选 name、scr_msg、message
其中 scr_msg 是原脚本文本，注入时只改 message，并用 scr_msg 做定位/校验。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from ai6win_mes import AI6WINScript

DEFAULT_ENCODING = "cp932"

# 全角/半角两类角色名前缀。常见形态：［キャラ］：台词
NAME_PREFIX_RE = re.compile(r"^(?P<prefix>[［\[](?P<name>[^］\]]{1,64})[］\]][：:])(?P<body>.*)$")
# 动态角色名常见形态：前面代码拼出名字，当前 STR_PRIMARY 只保存闭括号和冒号。
DYNAMIC_NAME_SUFFIX_RE = re.compile(r"^(?P<prefix>[］\]][：:])(?P<body>.+)$")


@dataclass
class AsmBlock:
    """反汇编文本中的一个逻辑块。

    old_raw_offset/opcode/raw 用于快速直读/重建 MES；line 用于兼容原 txt 解析。
    """
    kind: str                  # raw/label/command/blank/comment/plain
    line: str = ""
    command: str = ""
    args: Any = None
    opcode: int = -1
    raw: bytes = b""
    old_raw_offset: int = -1


@dataclass
class TextTarget:
    """一个可翻译 STR_PRIMARY 对应的定位信息。"""
    block_index: int
    text_index: int
    scr_msg: str
    name: Optional[str] = None
    prefix: str = ""           # 注入时保留在 message 前的原脚本前缀
    choice: bool = False

    def rebuild(self, message: str) -> str:
        return f"{self.prefix}{message}"


@dataclass
class InjectReport:
    file: str
    total_targets: int = 0
    json_entries: int = 0
    patched: int = 0
    skipped_same: int = 0
    mismatches: list[str] = None
    encode_errors: list[str] = None
    warnings: list[str] = None

    def __post_init__(self) -> None:
        if self.mismatches is None:
            self.mismatches = []
        if self.encode_errors is None:
            self.encode_errors = []
        if self.warnings is None:
            self.warnings = []


# ---------------------------------------------------------------------------
# 低层：MES <-> 反汇编 txt
# ---------------------------------------------------------------------------

def disassemble_mes_to_txt(mes_path: Path, txt_path: Path, version: int,
                           encoding: str = DEFAULT_ENCODING) -> None:
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    script = AI6WINScript(str(mes_path), str(txt_path), encoding=encoding, verbose=False,
                          debug=False, version=version)
    script.disassemble()


def assemble_txt_to_mes(txt_path: Path, mes_path: Path, version: int,
                        encoding: str = DEFAULT_ENCODING) -> None:
    mes_path.parent.mkdir(parents=True, exist_ok=True)
    script = AI6WINScript(str(mes_path), str(txt_path), encoding=encoding, verbose=False,
                          debug=False, version=version)
    script.assemble()

# ---------------------------------------------------------------------------
# 快速直读/直写：避免批量处理时每个文件都落地原工具 asm。
# ---------------------------------------------------------------------------

def _command_specs(version: int) -> dict[int, tuple[str, str]]:
    """返回 opcode -> (参数格式, 命令名)。"""
    if version < 0 or version >= len(AI6WINScript.command_library):
        raise ValueError(f"unsupported AI6WIN version: {version}")
    specs: dict[int, tuple[str, str]] = {}
    for opcode, fmt, name in AI6WINScript.command_library[version]:
        specs[opcode] = (fmt, name or f"{opcode:02x}")
    return specs

def _offset_arg_index_by_opcode() -> dict[int, int]:
    return {opcode: arg_index for opcode, arg_index in AI6WINScript.offsets_library}

def _read_c_string(data: bytes, pos: int, encoding: str) -> tuple[str, int]:
    end = data.find(b"\x00", pos)
    if end < 0:
        raise ValueError("unterminated string in MES")
    return data[pos:end].decode(encoding), end + 1

def _parse_args_from_bytes(data: bytes, pos: int, fmt: str, encoding: str) -> tuple[list[Any], int]:
    args: list[Any] = []
    endian = ""
    for ch in fmt:
        if ch in "><":
            endian = ch
            continue
        if ch in "Ii":
            args.append(struct.unpack_from(endian + ch, data, pos)[0])
            pos += 4
        elif ch in "Hh":
            args.append(struct.unpack_from(endian + ch, data, pos)[0])
            pos += 2
        elif ch in "Bb":
            args.append(struct.unpack_from(endian + ch, data, pos)[0])
            pos += 1
        elif ch == "S":
            text, pos = _read_c_string(data, pos, encoding)
            args.append(text)
        else:
            raise ValueError(f"unknown argument format char: {ch!r}")
    return args, pos

def _encode_args_to_bytes(args: list[Any], fmt: str, encoding: str) -> bytes:
    out = bytearray()
    endian = ""
    current = 0
    for ch in fmt:
        if ch in "><":
            endian = ch
            continue
        value = args[current]
        if ch in "Ii":
            out.extend(struct.pack(endian + ch, int(value)))
        elif ch in "Hh":
            out.extend(struct.pack(endian + ch, int(value)))
        elif ch in "Bb":
            out.extend(struct.pack(endian + ch, int(value)))
        elif ch == "S":
            out.extend(str(value).encode(encoding))
            out.append(0)
        else:
            raise ValueError(f"unknown argument format char: {ch!r}")
        current += 1
    return bytes(out)

def disassemble_mes_to_blocks(mes_path: Path, version: int,
                              encoding: str = DEFAULT_ENCODING) -> list[AsmBlock]:
    """快速把 MES 解析成 blocks。未知 opcode 按单字节 raw 保留。"""
    data = mes_path.read_bytes()
    if len(data) < 4:
        raise ValueError(f"file too small: {mes_path}")
    message_count = struct.unpack_from("<I", data, 0)[0]
    body_base = 4 + message_count * 4
    if body_base > len(data):
        raise ValueError(f"bad AI6WIN header in {mes_path}: message_count={message_count}")

    specs = _command_specs(version)
    blocks: list[AsmBlock] = []
    pos = body_base
    while pos < len(data):
        old_raw = pos - body_base
        opcode = data[pos]
        pos += 1
        spec = specs.get(opcode)
        if spec is None:
            blocks.append(AsmBlock(kind="raw", raw=bytes([opcode]), old_raw_offset=old_raw))
            continue
        fmt, name = spec
        try:
            args, pos = _parse_args_from_bytes(data, pos, fmt, encoding)
        except Exception as e:
            raise ValueError(f"parse failed at raw offset 0x{old_raw:X} opcode=0x{opcode:02X} in {mes_path}: {e}") from e
        if opcode == 0x19:
            # 和原工具一致，MESSAGE 参数在重建时按顺序重写。
            args = ["*MESSAGE_NUMBER*"]
        blocks.append(AsmBlock(kind="command", command=name, args=args, opcode=opcode, old_raw_offset=old_raw))
    return blocks

def _block_payload_size(block: AsmBlock, version: int, encoding: str) -> int:
    if block.kind == "raw":
        return len(block.raw)
    if block.kind != "command":
        return 0
    specs = _command_specs(version)
    fmt, _name = specs[block.opcode]
    args = list(block.args) if isinstance(block.args, list) else []
    if block.opcode == 0x19:
        args = [0]
    return 1 + len(_encode_args_to_bytes(args, fmt, encoding))

def assemble_blocks_to_mes(blocks: list[AsmBlock], mes_path: Path, version: int,
                           encoding: str = DEFAULT_ENCODING) -> None:
    """根据 blocks 重建 MES，并修正 MESSAGE header 与跳转 offset。"""
    specs = _command_specs(version)
    offset_arg_index = _offset_arg_index_by_opcode()

    old_to_new: dict[int, int] = {}
    new_pos = 0
    old_body_end = 0
    for block in blocks:
        if block.old_raw_offset >= 0:
            old_to_new[block.old_raw_offset] = new_pos
            old_body_end = max(old_body_end, block.old_raw_offset + _block_payload_size(block, version, encoding))
        new_pos += _block_payload_size(block, version, encoding)
    old_to_new[old_body_end] = new_pos

    body = bytearray()
    message_offsets: list[int] = []
    message_count = 0

    for block in blocks:
        if block.kind == "raw":
            body.extend(block.raw)
            continue
        if block.kind != "command":
            continue

        opcode = block.opcode
        fmt, _name = specs[opcode]
        args = list(block.args) if isinstance(block.args, list) else []

        if opcode == 0x19:
            message_offsets.append(len(body))
            args = [message_count]
            message_count += 1
        elif opcode in offset_arg_index:
            arg_index = offset_arg_index[opcode]
            old_target = args[arg_index]
            if old_target not in old_to_new:
                raise ValueError(
                    f"cannot remap jump target old_raw=0x{old_target:X} "
                    f"at command old_raw=0x{block.old_raw_offset:X}"
                )
            args[arg_index] = old_to_new[old_target]

        body.append(opcode)
        body.extend(_encode_args_to_bytes(args, fmt, encoding))

    out = bytearray()
    out.extend(struct.pack("<I", len(message_offsets)))
    for off in message_offsets:
        out.extend(struct.pack("<I", off))
    out.extend(body)

    mes_path.parent.mkdir(parents=True, exist_ok=True)
    mes_path.write_bytes(bytes(out))

def write_debug_asm(blocks: list[AsmBlock], txt_path: Path, encoding: str = DEFAULT_ENCODING) -> None:
    """保存调试用 asm 文本；该文本主要用于人工查看，不作为默认重建入口。"""
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    with txt_path.open("w", encoding=encoding, newline="") as f:
        for block in blocks:
            if block.kind == "raw":
                f.write("#0-" + block.raw.hex(" ") + "\n")
            elif block.kind == "command":
                f.write(f"#1-{block.command}\n")
                json.dump(block.args, f, ensure_ascii=False)
                f.write("\n")


# ---------------------------------------------------------------------------
# 反汇编文本解析/写回
# ---------------------------------------------------------------------------

def _read_json_array(lines: list[str], start: int) -> tuple[Any, int]:
    """从 start 开始读取一个 JSON 参数块，返回 (对象, 下一行下标)。"""
    buf: list[str] = []
    i = start
    while i < len(lines):
        buf.append(lines[i])
        text = "".join(buf)
        try:
            return json.loads(text), i + 1
        except json.JSONDecodeError:
            i += 1
            continue
    raise ValueError("反汇编文本中 command 后缺少完整 JSON 参数块")


def parse_asm_txt(txt_path: Path, encoding: str = DEFAULT_ENCODING) -> list[AsmBlock]:
    lines = txt_path.read_text(encoding=encoding).splitlines(keepends=True)
    blocks: list[AsmBlock] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#1-"):
            command = line[3:].strip().split()[0]
            args, i = _read_json_array(lines, i + 1)
            blocks.append(AsmBlock(kind="command", command=command, args=args))
            continue
        if line.startswith("#0-"):
            blocks.append(AsmBlock(kind="raw", line=line))
        elif line.startswith("#2-"):
            blocks.append(AsmBlock(kind="label", line=line))
        elif line.startswith("#3"):
            blocks.append(AsmBlock(kind="label", line=line))
        elif line.startswith("$"):
            blocks.append(AsmBlock(kind="comment", line=line))
        elif line.strip() == "":
            blocks.append(AsmBlock(kind="blank", line=line))
        else:
            blocks.append(AsmBlock(kind="plain", line=line))
        i += 1
    return blocks


def write_asm_txt(blocks: list[AsmBlock], txt_path: Path,
                  encoding: str = DEFAULT_ENCODING) -> None:
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    with txt_path.open("w", encoding=encoding, newline="") as f:
        for block in blocks:
            if block.kind == "command":
                f.write(f"#1-{block.command}\n")
                json.dump(block.args, f, ensure_ascii=False)
                f.write("\n")
            else:
                f.write(block.line)


def _first_arg_string(block: AsmBlock) -> Optional[str]:
    if block.kind != "command" or not isinstance(block.args, list) or not block.args:
        return None
    return block.args[0] if isinstance(block.args[0], str) else None


def _is_blank_or_control_text(text: str) -> bool:
    """过滤明显不该进入翻译 JSON 的空串/纯括号残片。"""
    if text == "":
        return True
    if text in {"［", "[", "］", "]", "［］：", "[]:"}:
        return True
    return False


def collect_text_targets(blocks: list[AsmBlock], version: int) -> list[TextTarget]:
    """
    收集可翻译 STR_PRIMARY。

    规则：
    - MESSAGE 视为新对话边界，清空上一轮 name。
    - CHOICE 后第一个 STR_PRIMARY 视为选项，不继承 name。
    - STR_PRIMARY["［角色］："] 只记录 pending name，不单独提取。
    - STR_PRIMARY["［角色］：正文"] 提取正文，并保留前缀用于注入重组。
    - STR_PRIMARY["］：正文"] 视为动态角色名后缀，提取正文，注入时保留 "］："。
    - 其他非空 STR_PRIMARY 直接提取；若当前 MESSAGE 内有 pending name，则写入 name 字段。
    """
    targets: list[TextTarget] = []
    pending_name: Optional[str] = None
    choice_pending = False
    text_index = 0

    for idx, block in enumerate(blocks):
        if block.kind != "command":
            continue
        cmd = block.command

        if cmd == "MESSAGE":
            pending_name = None
            choice_pending = False
            continue

        if cmd == "CHOICE":
            pending_name = None
            choice_pending = True
            continue

        if cmd in {"RETURN", "JUMP", "JUMP_IF_ZERO", "LIBCALL"}:
            # 明显控制流边界，防止 name 泄漏到下一段。
            pending_name = None
            if cmd != "JUMP_IF_ZERO":
                choice_pending = False
            continue

        if cmd != "STR_PRIMARY":
            continue

        s = _first_arg_string(block)
        if not isinstance(s, str) or _is_blank_or_control_text(s):
            continue

        m = NAME_PREFIX_RE.match(s)
        if m:
            name = m.group("name")
            prefix = m.group("prefix")
            body = m.group("body")
            pending_name = name
            if _is_blank_or_control_text(body):
                # 独立角色名行，不提取。
                choice_pending = False
                continue
            targets.append(TextTarget(
                block_index=idx,
                text_index=text_index,
                scr_msg=body,
                name=None if choice_pending else name,
                prefix=prefix,
                choice=choice_pending,
            ))
            text_index += 1
            choice_pending = False
            continue

        dm = DYNAMIC_NAME_SUFFIX_RE.match(s)
        if dm:
            body = dm.group("body")
            if _is_blank_or_control_text(body):
                choice_pending = False
                continue
            targets.append(TextTarget(
                block_index=idx,
                text_index=text_index,
                scr_msg=body,
                name=None,
                prefix=dm.group("prefix"),
                choice=choice_pending,
            ))
            text_index += 1
            choice_pending = False
            continue

        targets.append(TextTarget(
            block_index=idx,
            text_index=text_index,
            scr_msg=s,
            name=None if choice_pending else pending_name,
            prefix="",
            choice=choice_pending,
        ))
        text_index += 1
        choice_pending = False

    return targets


def entries_from_targets(targets: list[TextTarget]) -> list[dict[str, str]]:
    """生成项目常用 JSON。字段顺序保持 name/scr_msg/message。"""
    entries: list[dict[str, str]] = []
    for t in targets:
        item: dict[str, str] = {}
        if t.name:
            item["name"] = t.name
        item["scr_msg"] = t.scr_msg
        item["message"] = t.scr_msg
        entries.append(item)
    return entries


# ---------------------------------------------------------------------------
# extract / inject 主逻辑
# ---------------------------------------------------------------------------

def extract_file(mes_path: Path, json_path: Path, version: int,
                 encoding: str = DEFAULT_ENCODING, keep_asm: Optional[Path] = None) -> dict[str, Any]:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    blocks = disassemble_mes_to_blocks(mes_path, version, encoding)
    targets = collect_text_targets(blocks, version)
    entries = entries_from_targets(targets)
    json_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    if keep_asm:
        write_debug_asm(blocks, keep_asm, encoding)
    return {
        "file": str(mes_path),
        "json": str(json_path),
        "entries": len(entries),
        "with_name": sum(1 for e in entries if e.get("name")),
    }


def _load_translation_entries(json_path: Path) -> list[dict[str, Any]]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("messages", "texts", "entries"):
            if isinstance(data.get(key), list):
                return data[key]
    raise ValueError(f"不支持的 JSON 格式: {json_path}")


def _check_encodable(text: str, encoding: str) -> None:
    # AI6WINScript.set_S 直接走 CP932 编码；这里提前检查，报错更容易定位。
    AI6WINScript.set_S(text, encoding)


def inject_file(mes_path: Path, json_path: Path, out_mes_path: Path, version: int,
                encoding: str = DEFAULT_ENCODING, keep_asm: Optional[Path] = None,
                fallback_unique_scr: bool = True,
                skip_encode_error: bool = False) -> InjectReport:
    report = InjectReport(file=str(mes_path))
    entries = _load_translation_entries(json_path)
    report.json_entries = len(entries)

    blocks = disassemble_mes_to_blocks(mes_path, version, encoding)
    targets = collect_text_targets(blocks, version)
    report.total_targets = len(targets)

    used_target_indices: set[int] = set()
    for i, entry in enumerate(entries):
        scr_msg = entry.get("scr_msg")
        message = entry.get("message", entry.get("msg", scr_msg))
        if not isinstance(scr_msg, str):
            report.warnings.append(f"entry[{i}] 缺少 scr_msg，跳过")
            continue
        if not isinstance(message, str):
            report.warnings.append(f"entry[{i}] message 不是字符串，跳过")
            continue

        target_pos: Optional[int] = None
        if i < len(targets) and targets[i].scr_msg == scr_msg and i not in used_target_indices:
            target_pos = i
        elif fallback_unique_scr:
            matches = [
                pos for pos, t in enumerate(targets)
                if pos not in used_target_indices and t.scr_msg == scr_msg
            ]
            if len(matches) == 1:
                target_pos = matches[0]
            elif len(matches) > 1:
                report.mismatches.append(
                    f"entry[{i}] scr_msg 有多个匹配，无法唯一定位: {scr_msg!r}"
                )
                continue

        if target_pos is None:
            got = targets[i].scr_msg if i < len(targets) else "<无对应 STR_PRIMARY>"
            report.mismatches.append(f"entry[{i}] scr_msg mismatch: json={scr_msg!r}, mes={got!r}")
            continue

        target = targets[target_pos]
        new_arg = target.rebuild(message)
        try:
            _check_encodable(new_arg, encoding)
        except UnicodeEncodeError as e:
            msg = f"entry[{i}] CP932 编码失败: {message!r}; {e}"
            report.encode_errors.append(msg)
            if skip_encode_error:
                continue
            raise UnicodeEncodeError(e.encoding, e.object, e.start, e.end, msg)

        block = blocks[target.block_index]
        if block.args[0] == new_arg:
            report.skipped_same += 1
        else:
            block.args[0] = new_arg
            report.patched += 1
        used_target_indices.add(target_pos)

    if keep_asm:
        write_debug_asm(blocks, keep_asm, encoding)
    assemble_blocks_to_mes(blocks, out_mes_path, version, encoding)

    return report


def iter_mes_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        if path.suffix.lower() == ".mes":
            yield path
        return
    for p in sorted(path.rglob("*")):
        if p.is_file() and p.suffix.lower() == ".mes":
            yield p


def extract_path(input_path: Path, output_path: Path, version: int,
                 encoding: str = DEFAULT_ENCODING, keep_asm_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    if input_path.is_file():
        json_path = output_path if output_path.suffix.lower() == ".json" else output_path / (input_path.stem + ".json")
        keep_asm = None if keep_asm_dir is None else keep_asm_dir / (input_path.stem + ".ai6asm.txt")
        reports.append(extract_file(input_path, json_path, version, encoding, keep_asm))
        return reports

    for mes in iter_mes_files(input_path):
        rel = mes.relative_to(input_path)
        json_path = output_path / rel.with_suffix(".json")
        keep_asm = None if keep_asm_dir is None else keep_asm_dir / rel.with_suffix(".ai6asm.txt")
        reports.append(extract_file(mes, json_path, version, encoding, keep_asm))
    return reports


def inject_path(input_path: Path, json_path: Path, output_path: Path, version: int,
                encoding: str = DEFAULT_ENCODING, keep_asm_dir: Optional[Path] = None,
                skip_encode_error: bool = False) -> list[InjectReport]:
    reports: list[InjectReport] = []
    if input_path.is_file():
        out_mes = output_path if output_path.suffix.lower() == input_path.suffix.lower() else output_path / input_path.name
        jpath = json_path if json_path.is_file() else json_path / (input_path.stem + ".json")
        keep_asm = None if keep_asm_dir is None else keep_asm_dir / (input_path.stem + ".new.ai6asm.txt")
        reports.append(inject_file(input_path, jpath, out_mes, version, encoding, keep_asm,
                                   skip_encode_error=skip_encode_error))
        return reports

    for mes in iter_mes_files(input_path):
        rel = mes.relative_to(input_path)
        jpath = json_path / rel.with_suffix(".json")
        if not jpath.exists():
            rep = InjectReport(file=str(mes))
            rep.warnings.append(f"缺少对应 JSON: {jpath}")
            reports.append(rep)
            continue
        out_mes = output_path / rel
        keep_asm = None if keep_asm_dir is None else keep_asm_dir / rel.with_suffix(".new.ai6asm.txt")
        reports.append(inject_file(mes, jpath, out_mes, version, encoding, keep_asm,
                                   skip_encode_error=skip_encode_error))
    return reports


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _loose_two_paths(parts: list[str], role1: str, role2: str) -> tuple[Path, Path]:
    if len(parts) < 2:
        raise SystemExit(f"缺少参数：需要 {role1} 和 {role2}")
    if len(parts) == 2:
        return Path(parts[0]), Path(parts[1])
    return Path(" ".join(parts[:-1])), Path(parts[-1])


def _loose_three_paths(parts: list[str], role1: str, role2: str, role3: str) -> tuple[Path, Path, Path]:
    if len(parts) < 3:
        raise SystemExit(f"缺少参数：需要 {role1}、{role2} 和 {role3}")
    if len(parts) == 3:
        return Path(parts[0]), Path(parts[1]), Path(parts[2])
    return Path(" ".join(parts[:-2])), Path(parts[-2]), Path(parts[-1])


def _warn_path_problem(path: Path, label: str) -> None:
    if not path.exists():
        print(f"[WARN] {label} 不存在: {path}", file=sys.stderr)
        print("       Windows 路径包含空格/括号/日文时，建议整体加英文双引号。", file=sys.stderr)


def run_extract_cli(version: int, description: str) -> None:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("paths", nargs="+", help="输入 MES 文件/目录 与 输出 JSON 文件/目录。路径含空格时建议加引号。")
    parser.add_argument("--encoding", default=DEFAULT_ENCODING, help="脚本编码，默认 cp932")
    parser.add_argument("--keep-asm-dir", default="", help="可选：保存调试用反汇编 txt 的目录")
    args = parser.parse_args()

    input_path, output_path = _loose_two_paths(args.paths, "输入 MES 文件或目录", "输出 JSON 文件或目录")
    _warn_path_problem(input_path, "输入路径")
    reports = extract_path(
        input_path, output_path, version,
        encoding=args.encoding,
        keep_asm_dir=Path(args.keep_asm_dir) if args.keep_asm_dir else None,
    )
    print(json.dumps({"version": version, "files": len(reports), "reports": reports}, ensure_ascii=False, indent=2))


def run_inject_cli(version: int, description: str) -> None:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("paths", nargs="+", help="原始 MES 文件/目录、翻译 JSON 文件/目录、输出 MES 文件/目录。路径含空格时建议加引号。")
    parser.add_argument("--encoding", default=DEFAULT_ENCODING, help="脚本编码，默认 cp932")
    parser.add_argument("--keep-asm-dir", default="", help="可选：保存注入后反汇编 txt 的目录")
    parser.add_argument("--skip-encode-error", action="store_true", help="遇到 CP932 不可编码文本时跳过该条，否则默认报错停止")
    args = parser.parse_args()

    input_path, json_path, output_path = _loose_three_paths(args.paths, "原始 MES", "翻译 JSON", "输出 MES")
    _warn_path_problem(input_path, "输入路径")
    _warn_path_problem(json_path, "JSON 路径")
    reports = inject_path(
        input_path, json_path, output_path, version,
        encoding=args.encoding,
        keep_asm_dir=Path(args.keep_asm_dir) if args.keep_asm_dir else None,
        skip_encode_error=args.skip_encode_error,
    )
    print(json.dumps({
        "version": version,
        "files": len(reports),
        "reports": [r.__dict__ for r in reports],
    }, ensure_ascii=False, indent=2))
