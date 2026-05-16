# -*- coding: utf-8 -*-
"""
AI5WIN story extraction/injection common layer.

本模块不直接绑定 v0/v1/v2，而是提供共享的：
1. MES -> 反汇编中间块；
2. 中间块 -> 可翻译 JSON 条目；
3. JSON 条目 -> 修改中间块；
4. 中间块 -> MES 重建。

默认编码为 cp932。真正的 AI5WIN opcode/参数解析仍复用原项目的
AI5WINScript，避免重复实现复杂的 C/V/G 结构解析。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

# wrapper 脚本会把 core 目录加入 sys.path。
from ai5win_mes import AI5WINScript

DEFAULT_ENCODING = "cp932"


@dataclass
class AsmBlock:
    """反汇编文本中的一个逻辑块。"""
    kind: str                  # plain/raw/label/command/blank/comment
    line: str = ""             # 非 command 块原样保存
    command: str = ""          # command 块的命令名或十六进制名
    args: Any = None           # command 参数 JSON


@dataclass
class TextTarget:
    """一个可翻译 TEXT 指令对应的定位信息。"""
    block_index: int
    text_index: int
    scr_msg: str
    name: Optional[str] = None


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
# 低层：反汇编/重汇编封装
# ---------------------------------------------------------------------------

def disassemble_mes_to_txt(mes_path: Path, txt_path: Path, version: int,
                           encoding: str = DEFAULT_ENCODING) -> None:
    """调用原 AI5WINScript，把 MES 反汇编成 txt。"""
    script = AI5WINScript(str(mes_path), str(txt_path), version, encoding=encoding)
    script.disassemble()


def assemble_txt_to_mes(txt_path: Path, mes_path: Path, version: int,
                        encoding: str = DEFAULT_ENCODING) -> None:
    """调用原 AI5WINScript，把 txt 重汇编成 MES。"""
    mes_path.parent.mkdir(parents=True, exist_ok=True)
    script = AI5WINScript(str(mes_path), str(txt_path), version, encoding=encoding)
    script.assemble()


# ---------------------------------------------------------------------------
# 中间文本块解析/写回
# ---------------------------------------------------------------------------

def _read_json_array(lines: list[str], start: int) -> tuple[Any, int]:
    """从 start 开始读取一个 JSON 数组，返回 (对象, 下一行下标)。"""
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
    """解析原工具生成的反汇编 txt。"""
    lines = txt_path.read_text(encoding=encoding).splitlines(keepends=True)
    blocks: list[AsmBlock] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#1-"):
            # 原工具 debug 模式会在命令后追加 offset；这里兼容去掉。
            command = line[3:].strip().split()[0]
            args, i = _read_json_array(lines, i + 1)
            blocks.append(AsmBlock(kind="command", command=command, args=args))
            continue
        if line.startswith("#0-"):
            blocks.append(AsmBlock(kind="raw", line=line))
        elif line.startswith("#2-"):
            blocks.append(AsmBlock(kind="label", line=line))
        elif line.startswith("$"):
            blocks.append(AsmBlock(kind="comment", line=line))
        elif line.strip() == "":
            blocks.append(AsmBlock(kind="blank", line=line))
        else:
            # v0 文件头字符串会以普通行保存，必须原样保留。
            blocks.append(AsmBlock(kind="plain", line=line))
        i += 1
    return blocks


def write_asm_txt(blocks: list[AsmBlock], txt_path: Path,
                  encoding: str = DEFAULT_ENCODING) -> None:
    """把中间块重新写成原工具可 assemble 的 txt。"""
    with txt_path.open("w", encoding=encoding, newline="") as f:
        for block in blocks:
            if block.kind == "command":
                f.write(f"#1-{block.command}\n")
                json.dump(block.args, f, ensure_ascii=False, indent=4)
                f.write("\n")
            else:
                f.write(block.line)


# ---------------------------------------------------------------------------
# 可翻译文本识别
# ---------------------------------------------------------------------------

def _first_arg_string(block: AsmBlock) -> Optional[str]:
    if block.kind != "command":
        return None
    if not isinstance(block.args, list) or not block.args:
        return None
    return block.args[0] if isinstance(block.args[0], str) else None


def is_probable_name(s: str) -> bool:
    """
    粗略判断 SYSTEM_TEXT 是否像角色名。

    目前规则保守：空串、过长、明显含句末标点的字符串不当作 name。
    这不是最终语义判断，只用于避免把系统提示误挂到后续 TEXT。
    """
    if not s:
        return False
    stripped = s.strip()
    if not stripped:
        return False
    if len(stripped) > 24:
        return False
    bad_marks = "。！？!?\n\r「」『』、，,.…"
    return not any(ch in stripped for ch in bad_marks)


def collect_text_targets(blocks: list[AsmBlock], version: int) -> list[TextTarget]:
    """
    根据 AI5WIN 指令流收集可翻译 TEXT。

    name 判定规则：
    - v2: MESSAGE 作为对话块边界；块内 TEXT 前最近的 SYSTEM_TEXT 作为 name。
    - v1: 没有 MESSAGE header，TEXT 前最近的 SYSTEM_TEXT 作为 name；遇到 RETURN/JUMP/MENU_SET 后清理。
    - v0: 标准表里没有 SYSTEM_TEXT，默认不判定 name。
    - MENU_SET 后紧邻的 TEXT 视为选项文本，不继承 name。
    """
    targets: list[TextTarget] = []
    pending_name: Optional[str] = None
    menu_pending = False
    text_index = 0

    for idx, block in enumerate(blocks):
        if block.kind != "command":
            continue
        cmd = block.command

        if version == 2 and cmd == "MESSAGE":
            # 新的 MESSAGE 块开始，清空上一块的角色名。
            pending_name = None
            menu_pending = False
            continue

        if cmd == "SYSTEM_TEXT":
            s = _first_arg_string(block)
            pending_name = s if isinstance(s, str) and is_probable_name(s) else None
            menu_pending = False
            continue

        if cmd == "MENU_SET":
            # 选项分支设置。后面紧邻/后续第一个 TEXT 按选项处理，不继承 name。
            pending_name = None
            menu_pending = True
            continue

        if cmd in {"RETURN", "JUMP", "JUMP_IF", "INTERRUPT", "INTERRUPT_IF", "CALL"}:
            # v1 没有 MESSAGE 边界，遇到明显控制流时主动结束 name 作用域。
            if version < 2:
                pending_name = None
            if cmd != "JUMP_IF":
                menu_pending = False
            continue

        if cmd == "TEXT":
            s = _first_arg_string(block)
            if isinstance(s, str):
                targets.append(TextTarget(
                    block_index=idx,
                    text_index=text_index,
                    scr_msg=s,
                    name=None if menu_pending else pending_name,
                ))
                text_index += 1
            # MENU_SET 后通常只对应一个选项 TEXT。
            menu_pending = False
            # v0/v1 没有可靠 MESSAGE 边界，默认不做跨 TEXT 继承。
            if version < 2:
                pending_name = None
            continue

    return targets


def entries_from_targets(targets: list[TextTarget]) -> list[dict[str, str]]:
    """生成翻译 JSON。字段顺序保持 name/scr_msg/message。"""
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
    """提取单个 MES 到 JSON。"""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ai5win_extract_") as td:
        txt_path = Path(td) / (mes_path.stem + ".ai5asm.txt")
        disassemble_mes_to_txt(mes_path, txt_path, version, encoding)
        blocks = parse_asm_txt(txt_path, encoding)
        targets = collect_text_targets(blocks, version)
        entries = entries_from_targets(targets)
        json_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        if keep_asm:
            keep_asm.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(txt_path, keep_asm)
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
    # set_S 会处理 AI5WIN 特殊符号替换，这里直接调用它来保持一致。
    AI5WINScript.set_S(text, encoding)


def inject_file(mes_path: Path, json_path: Path, out_mes_path: Path, version: int,
                encoding: str = DEFAULT_ENCODING, keep_asm: Optional[Path] = None,
                fallback_unique_scr: bool = True, skip_encode_error: bool = False) -> InjectReport:
    """把 JSON 的 message 注入到单个 MES，并整文件重建。"""
    report = InjectReport(file=str(mes_path))
    entries = _load_translation_entries(json_path)
    report.json_entries = len(entries)

    with tempfile.TemporaryDirectory(prefix="ai5win_inject_") as td:
        td_path = Path(td)
        src_txt = td_path / (mes_path.stem + ".src.ai5asm.txt")
        new_txt = td_path / (mes_path.stem + ".new.ai5asm.txt")
        disassemble_mes_to_txt(mes_path, src_txt, version, encoding)
        blocks = parse_asm_txt(src_txt, encoding)
        targets = collect_text_targets(blocks, version)
        report.total_targets = len(targets)

        used_target_indices: set[int] = set()

        for i, entry in enumerate(entries):
            scr_msg = entry.get("scr_msg")
            message = entry.get("message", scr_msg)
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
                got = targets[i].scr_msg if i < len(targets) else "<无对应 TEXT>"
                report.mismatches.append(
                    f"entry[{i}] scr_msg mismatch: json={scr_msg!r}, mes={got!r}"
                )
                continue

            try:
                _check_encodable(message, encoding)
            except UnicodeEncodeError as e:
                msg = f"entry[{i}] CP932 编码失败: {message!r}; {e}"
                report.encode_errors.append(msg)
                if skip_encode_error:
                    continue
                raise UnicodeEncodeError(e.encoding, e.object, e.start, e.end, msg)

            target = targets[target_pos]
            block = blocks[target.block_index]
            if block.args[0] == message:
                report.skipped_same += 1
            else:
                block.args[0] = message
                report.patched += 1
            used_target_indices.add(target_pos)

        write_asm_txt(blocks, new_txt, encoding)
        if keep_asm:
            keep_asm.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(new_txt, keep_asm)
        assemble_txt_to_mes(new_txt, out_mes_path, version, encoding)

    return report


def iter_mes_files(path: Path) -> Iterable[Path]:
    if path.is_file():
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
        keep_asm = None if keep_asm_dir is None else keep_asm_dir / (input_path.stem + ".ai5asm.txt")
        reports.append(extract_file(input_path, json_path, version, encoding, keep_asm))
        return reports

    for mes in iter_mes_files(input_path):
        rel = mes.relative_to(input_path)
        json_path = output_path / rel.with_suffix(".json")
        keep_asm = None if keep_asm_dir is None else keep_asm_dir / rel.with_suffix(".ai5asm.txt")
        reports.append(extract_file(mes, json_path, version, encoding, keep_asm))
    return reports


def inject_path(input_path: Path, json_path: Path, output_path: Path, version: int,
                encoding: str = DEFAULT_ENCODING, keep_asm_dir: Optional[Path] = None,
                skip_encode_error: bool = False) -> list[InjectReport]:
    reports: list[InjectReport] = []
    if input_path.is_file():
        out_mes = output_path if output_path.suffix.lower() == input_path.suffix.lower() else output_path / input_path.name
        jpath = json_path if json_path.is_file() else json_path / (input_path.stem + ".json")
        keep_asm = None if keep_asm_dir is None else keep_asm_dir / (input_path.stem + ".new.ai5asm.txt")
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
        keep_asm = None if keep_asm_dir is None else keep_asm_dir / rel.with_suffix(".new.ai5asm.txt")
        reports.append(inject_file(mes, jpath, out_mes, version, encoding, keep_asm,
                                   skip_encode_error=skip_encode_error))
    return reports


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _loose_two_paths(parts: list[str], role1: str, role2: str) -> tuple[Path, Path]:
    """
    兼容 Windows 路径未加引号导致被空格拆开的情况。

    正常情况下：
        input output
    会得到两个参数。

    如果用户把带空格的输入目录拆成多个参数，约定最后一个参数是输出路径，
    前面所有碎片用空格拼回输入路径。

    注意：如果输出路径本身也包含空格，仍然必须加引号。
    """
    if len(parts) < 2:
        raise SystemExit(f"缺少参数：需要 {role1} 和 {role2}")
    if len(parts) == 2:
        return Path(parts[0]), Path(parts[1])
    return Path(" ".join(parts[:-1])), Path(parts[-1])


def _loose_three_paths(parts: list[str], role1: str, role2: str, role3: str) -> tuple[Path, Path, Path]:
    r"""
    inject 需要三个路径。为了避免误判，只在参数正好为 3 个时直接接受。
    如果原始 MES 路径含空格但 JSON 和输出路径不含空格，则把前面的碎片拼成 input。

    更复杂的情况，例如三个路径都含空格，请使用引号。
    """
    if len(parts) < 3:
        raise SystemExit(f"缺少参数：需要 {role1}、{role2} 和 {role3}")
    if len(parts) == 3:
        return Path(parts[0]), Path(parts[1]), Path(parts[2])

    # 启发式：最后两个参数通常是 json_dir 和 out_dir；前面全部拼成 input。
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
