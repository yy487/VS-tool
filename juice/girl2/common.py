#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GIRL2_95 XSD common template.

职责：
1. 批量路径遍历
2. XSD XOR FF + 解压 / mode0 输出
3. 0x10 文本块扫描
4. name / scr_msg / msg JSON 模板
5. 固定偏移截断注入基础逻辑
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

DEFAULT_ENCODING = "cp932"


class ToolError(Exception):
    pass


# ============================================================
# path / json
# ============================================================
def iter_xsd_files(input_path: str | Path, ext: str = ".XSD") -> List[Path]:
    """Return script files under input_path.

    ext rules:
      - ".XSD": only XSD files
      - ".dec": only decoded files with .dec suffix
      - "" / "*" / "all": all regular files

    When input_path is a single file, the suffix is not checked.
    """
    root = Path(input_path)
    if root.is_file():
        return [root]
    ext_norm = (ext or "").lower().strip()
    if ext_norm in ("", "*", "all"):
        return sorted(p for p in root.rglob("*") if p.is_file())
    if not ext_norm.startswith("."):
        ext_norm = "." + ext_norm
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ext_norm)


def file_key(path: Path, input_root: str | Path) -> str:
    root = Path(input_root)
    if root.is_file():
        return path.name
    return path.relative_to(root).as_posix()


def output_path(src: Path, input_root: str | Path, output_root: str | Path) -> Path:
    root = Path(input_root)
    out = Path(output_root)
    if root.is_file():
        if out.suffix:
            return out
        return out / src.name
    return out / src.relative_to(root)


def load_json(path: str | Path) -> List[dict]:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(obj, dict) and "entries" in obj:
        obj = obj["entries"]
    if not isinstance(obj, list):
        raise ToolError("JSON 必须是数组，或 {'entries': [...]} 格式")
    return [x for x in obj if isinstance(x, dict)]


def save_json(path: str | Path, entries: Iterable[Mapping]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(list(entries), ensure_ascii=False, indent=2), encoding="utf-8")


def make_entry(
    *,
    scr_msg: str,
    file: str,
    index: int,
    name: Optional[str] = None,
    offset: Optional[int] = None,
    end: Optional[int] = None,
    encoding: str = DEFAULT_ENCODING,
) -> dict:
    item: dict = {}
    if name:
        item["name"] = name
    item["scr_msg"] = scr_msg
    item["msg"] = scr_msg
    item["_file"] = file
    item["_index"] = int(index)
    if offset is not None:
        item["_offset"] = int(offset)
    if end is not None:
        item["_end"] = int(end)
    item["_kind"] = "message"
    item["_encoding"] = encoding
    return item


def build_translation_index(entries: Iterable[Mapping]) -> Dict[Tuple[str, int], Mapping]:
    out: Dict[Tuple[str, int], Mapping] = {}
    for seq, item in enumerate(entries):
        idx = int(item.get("_index", seq))
        f = str(item.get("_file", "") or "")
        out[(f, idx)] = item
    return out


def get_msg(item: Mapping, default: str) -> str:
    if item.get("msg") is not None:
        return str(item.get("msg"))
    # 兼容旧字段，方便临时测试
    if item.get("message") is not None:
        return str(item.get("message"))
    if item.get("translation") is not None:
        return str(item.get("translation"))
    return default


# ============================================================
# XSD codec
# ============================================================
def xor_ff(data: bytes) -> bytes:
    return bytes(b ^ 0xFF for b in data)


def decode_xsd(raw: bytes, *, strict_size: bool = True) -> bytes:
    data = xor_ff(raw)
    if len(data) < 5:
        raise ToolError("XSD 太短，缺少 5 字节头")

    mode = data[0] & 0x7F
    expected = int.from_bytes(data[1:5], "little")
    pos = 5

    if mode == 0:
        out = data[pos:pos + expected]
        if strict_size and len(out) != expected:
            raise ToolError(f"mode0 大小不匹配: got {len(out)}, expected {expected}")
        return out

    if mode == 1:
        bases = (0x140, 0x280, 0x500, 0xA00)
    elif mode == 2:
        bases = (0x50, 0xA0, 0x140, 0x280)
    else:
        raise ToolError(f"不支持的 XSD 压缩模式: {mode}")

    out = bytearray()
    while pos < len(data):
        tag = data[pos]
        pos += 1
        if tag == 0:
            break

        if tag < 0x40:
            out += data[pos:pos + tag]
            pos += tag
        elif tag < 0x7F:
            if pos >= len(data):
                raise ToolError("byte RLE 读越界")
            out += bytes([data[pos]]) * (tag - 0x3D)
            pos += 1
        elif tag == 0x7F:
            if pos + 3 > len(data):
                raise ToolError("long byte RLE 读越界")
            count = data[pos] | (data[pos + 1] << 8)
            value = data[pos + 2]
            pos += 3
            out += bytes([value]) * count
        elif tag < 0xBF:
            if pos + 2 > len(data):
                raise ToolError("word RLE 读越界")
            pair = data[pos:pos + 2]
            pos += 2
            out += pair * (tag - 0x7E)
        elif tag == 0xBF:
            if pos + 4 > len(data):
                raise ToolError("long word RLE 读越界")
            count = data[pos] | (data[pos + 1] << 8)
            pair = data[pos + 2:pos + 4]
            pos += 4
            out += pair * count
        else:
            if pos >= len(data):
                raise ToolError("LZ 引用读越界")
            ref = data[pos]
            pos += 1
            base = bases[(ref >> 6) & 3]
            src = len(out) + ((ref & 0x3F) - base)
            count = tag - 0xBD
            if src < 0:
                raise ToolError(f"非法 LZ 引用: src={src}")
            for i in range(count):
                out.append(out[src + i])

    if strict_size and len(out) != expected:
        raise ToolError(f"解压大小不匹配: got {len(out)}, expected {expected}")
    return bytes(out)


def encode_xsd_mode0(decoded: bytes) -> bytes:
    packed = bytearray()
    packed.append(0x00)
    packed += len(decoded).to_bytes(4, "little")
    packed += decoded
    return xor_ff(bytes(packed))


# ============================================================
# text block scan / inject
# ============================================================
def u16(data: bytes | bytearray, pos: int) -> int:
    if pos + 2 > len(data):
        raise ToolError(f"u16 读越界: 0x{pos:X}")
    return data[pos] | (data[pos + 1] << 8)


@dataclass
class TextSpan:
    op: int
    start: int
    end: int
    raw: bytes
    text: str


@dataclass
class TextBlock:
    index: int
    file: str
    start: int
    end: int
    spans: List[TextSpan]
    msg: str
    name: Optional[str] = None


def plausible_cp932_string(raw: bytes, *, encoding: str = DEFAULT_ENCODING) -> bool:
    if not raw or len(raw) > 4096:
        return False
    for b in raw:
        if b < 0x20 and b not in (9, 10, 13):
            return False
    try:
        s = raw.decode(encoding)
    except UnicodeDecodeError:
        return False
    if not s.strip():
        return False
    # 避免把纯 ASCII 资源名误当正文。GIRL2 正文基本含 SJIS 高位字节。
    return any(ord(ch) >= 0x80 for ch in s)


def find_text_spans(data: bytes, *, encoding: str = DEFAULT_ENCODING) -> List[TextSpan]:
    spans: List[TextSpan] = []
    i = 0
    n = len(data)
    while i < n - 2:
        if data[i] != 0x10:
            i += 1
            continue
        start = i + 1
        # 0x10 后如果是 FFxx 变量引用，不当内联文本。
        if start + 2 <= n and u16(data, start) >= 0xFF00:
            i += 3
            continue
        end = data.find(b"\x00", start)
        if end < 0:
            break
        raw = data[start:end]
        if plausible_cp932_string(raw, encoding=encoding):
            spans.append(TextSpan(i, start, end, raw, raw.decode(encoding)))
            i = end + 1
        else:
            i += 1
    return spans


def detect_inline_name(msg: str) -> Tuple[Optional[str], str]:
    # 仅处理非常明确的内联 name。没有 name 时不输出 name 字段。
    if msg.startswith("【"):
        close = msg.find("】")
        if 0 < close <= 16 and msg[close + 1:].startswith("\n"):
            return msg[1:close], msg[close + 2:]
    for sep in ("：", ":"):
        pos = msg.find(sep)
        if 0 < pos <= 12:
            name = msg[:pos].strip()
            body = msg[pos + 1:].lstrip()
            if name and body and all(ord(ch) >= 0x80 for ch in name):
                return name, body
    return None, msg


def collect_text_blocks(data: bytes, file: str, *, encoding: str = DEFAULT_ENCODING) -> List[TextBlock]:
    spans = find_text_spans(data, encoding=encoding)
    blocks: List[TextBlock] = []
    i = 0
    idx = 0
    while i < len(spans):
        group = [spans[i]]
        j = i + 1
        while j < len(spans):
            prev = group[-1]
            cur = spans[j]
            # 多行正文通常是 10 text 00 17 10 text 00。
            if data[prev.end + 1:cur.op] == b"\x17":
                group.append(cur)
                j += 1
            else:
                break
        raw_msg = "\n".join(s.text for s in group)
        name, msg = detect_inline_name(raw_msg)
        blocks.append(TextBlock(idx, file, group[0].op, group[-1].end + 1, group, msg, name))
        idx += 1
        i = j
    return blocks


def block_to_entry(block: TextBlock, *, encoding: str = DEFAULT_ENCODING) -> dict:
    return make_entry(
        name=block.name,
        scr_msg=block.msg,
        file=block.file,
        index=block.index,
        offset=block.start,
        end=block.end,
        encoding=encoding,
    )


def encode_fit(text: str, width: int, *, encoding: str = DEFAULT_ENCODING, errors: str = "strict") -> Tuple[bytes, bool]:
    """Encode text into at most width bytes without cutting a multibyte char.

    Unlike encode_fixed_width(), this does not pad by itself.  Padding is handled
    at block level so unused bytes from short sub-spans can be reassigned to
    longer sub-spans first.
    """
    out = bytearray()
    truncated = False
    for ch in text:
        try:
            raw = ch.encode(encoding, errors=errors)
        except UnicodeEncodeError as e:
            raise ToolError(f"文本无法编码为 {encoding}: {e}")
        if len(out) + len(raw) > width:
            truncated = True
            break
        out += raw
    return bytes(out), truncated


def encode_fixed_width(text: str, width: int, *, encoding: str = DEFAULT_ENCODING, errors: str = "strict") -> Tuple[bytes, bool]:
    out, truncated = encode_fit(text, width, encoding=encoding, errors=errors)
    if len(out) < width:
        out += b" " * (width - len(out))
    return out, truncated


def split_stream_to_widths(
    text: str,
    widths: List[int],
    *,
    encoding: str = DEFAULT_ENCODING,
    errors: str = "strict",
) -> Tuple[List[bytes], bool, int, int]:
    """现代栈式/流式固定容量写回 + 尾部截断。

    规则：
      1. JSON msg 是连续字符流；实际换行符只用于编辑阅读，注入时不写入脚本。
      2. 从第 1 个 0x10 小块开始依次塞文本，前面的块不会空出来。
      3. 非最后一个小块默认保留 1 字节空格作为分段占位，避免前一块把边界完全吃满。
      4. 每个小块的总字节长度仍保持原始长度；未使用位置用半角空格补齐。
      5. 如果文本超过所有小块可用文本容量，只从整体尾部安全截断。
      6. 不拆 CP932 多字节字符；如果当前块放不下一个完整字符，就进入下一块。

    Returns: (encoded_parts, truncated, wanted_bytes, budget_bytes)
    """
    if not widths:
        return [], False, 0, 0

    # 换行只是 JSON 编辑层的可读分隔，固定偏移注入时不能写入 0x0A。
    stream = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "")

    try:
        wanted_bytes = len(stream.encode(encoding, errors=errors))
    except UnicodeEncodeError as e:
        raise ToolError(f"文本无法编码为 {encoding}: {e}")

    # 每个非最后小块留 1 字节空格占位；最后小块用满。
    # 如果某个小块原始容量为 0，保持 0；正常 GIRL2 文本块容量都 >= 1。
    text_budgets: List[int] = []
    for i, w in enumerate(widths):
        if i + 1 < len(widths):
            text_budgets.append(max(0, w - 1))
        else:
            text_budgets.append(w)

    parts = [bytearray() for _ in widths]
    idx = 0
    truncated = False

    for ch in stream:
        try:
            raw = ch.encode(encoding, errors=errors)
        except UnicodeEncodeError as e:
            raise ToolError(f"文本无法编码为 {encoding}: {e}")

        while idx < len(widths) and len(parts[idx]) + len(raw) > text_budgets[idx]:
            # 当前块的文本预算放不下该完整字符：补齐当前块，进入下一块。
            # 非最后块补齐时自然会留下至少 1 字节空格占位。
            if len(parts[idx]) < widths[idx]:
                parts[idx] += b" " * (widths[idx] - len(parts[idx]))
            idx += 1

        if idx >= len(widths):
            truncated = True
            break

        parts[idx] += raw

        if len(parts[idx]) == text_budgets[idx]:
            # 文本预算刚好用尽，剩余占位空间在最终 padding 阶段补空格。
            idx += 1

    # 所有未填满的块补空格，保证文本块总长度不变，并清掉旧文本残留。
    for i, width in enumerate(widths):
        if len(parts[i]) < width:
            parts[i] += b" " * (width - len(parts[i]))
        elif len(parts[i]) > width:
            raise ToolError(f"内部错误：第 {i + 1} 块超过容量 {len(parts[i])}>{width}")

    return [bytes(p) for p in parts], truncated, wanted_bytes, sum(text_budgets)

def split_msg_for_spans(
    block: TextBlock,
    item: Mapping,
    *,
    encoding: str = DEFAULT_ENCODING,
    errors: str = "strict",
) -> Tuple[List[bytes], bool, int, int]:
    """把 JSON msg 编码成与原始 TextSpan 数量一致的固定宽度 byte parts。

    这里不再做“每行对应一个小块”的固定分段；而是采用栈式/流式填充：
    删除 JSON 层换行后，将文本流按原始 0x10 小块容量依次塞满。
    """
    msg = get_msg(item, block.msg)
    widths = [span.end - span.start for span in block.spans]

    # 如果原块识别出 name，第一小块保留给 name，正文流入后续小块。
    # 这样不会把角色名和正文混进同一容量池，减少误覆盖 name 的风险。
    if block.name:
        name = str(item.get("name", block.name))
        name_line = f"【{name}】"
        if len(widths) <= 1:
            return split_stream_to_widths(name_line + msg, widths, encoding=encoding, errors=errors)

        name_part, name_cut, name_wanted, name_budget = split_stream_to_widths(
            name_line,
            [widths[0]],
            encoding=encoding,
            errors=errors,
        )
        body_parts, body_cut, body_wanted, body_budget = split_stream_to_widths(
            msg,
            widths[1:],
            encoding=encoding,
            errors=errors,
        )
        return name_part + body_parts, (name_cut or body_cut), name_wanted + body_wanted, name_budget + body_budget

    return split_stream_to_widths(msg, widths, encoding=encoding, errors=errors)

def find_item(index: Dict[Tuple[str, int], Mapping], file: str, basename: str, idx: int) -> Optional[Mapping]:
    return (
        index.get((file, idx))
        or index.get((basename, idx))
        or index.get(("", idx))
    )


def inject_truncate(
    decoded: bytes,
    entries_index: Dict[Tuple[str, int], Mapping],
    file: str,
    *,
    encoding: str = DEFAULT_ENCODING,
    errors: str = "strict",
    allow_mismatch: bool = False,
) -> Tuple[bytes, int, int, List[str]]:
    out = bytearray(decoded)
    blocks = collect_text_blocks(decoded, file, encoding=encoding)
    basename = Path(file).name
    changed = 0
    truncated = 0
    warnings: List[str] = []

    for block in blocks:
        item = find_item(entries_index, file, basename, block.index)
        if not item:
            continue

        scr_msg = item.get("scr_msg")
        if scr_msg is not None and str(scr_msg) != block.msg:
            text = f"{file}#{block.index}: scr_msg 不匹配，json={str(scr_msg)[:40]!r}, script={block.msg[:40]!r}"
            if not allow_mismatch:
                raise ToolError(text)
            warnings.append(text)

        new_msg = get_msg(item, block.msg)
        new_name = item.get("name", block.name)
        if str(new_msg) == block.msg and new_name == block.name:
            continue

        parts, cut, wanted_bytes, budget_bytes = split_msg_for_spans(
            block,
            item,
            encoding=encoding,
            errors=errors,
        )

        rebuilt = bytearray()
        for i, (span, part) in enumerate(zip(block.spans, parts)):
            rebuilt.append(0x10)
            rebuilt += part
            rebuilt.append(0x00)
            if i + 1 < len(block.spans):
                # Preserve the original separator bytes between two text opcodes.
                # In GIRL2 normal multiline text this is exactly b"\x17".
                rebuilt += decoded[span.end + 1:block.spans[i + 1].op]

        old_len = block.end - block.start
        if len(rebuilt) != old_len:
            raise ToolError(
                f"{file}#{block.index}: 重建文本块长度不一致: "
                f"new={len(rebuilt)}, old={old_len}"
            )
        out[block.start:block.end] = rebuilt

        if cut:
            truncated += 1
            warnings.append(
                f"{file}#{block.index} @0x{block.start:X}: 尾部截断，"
                f"译文 {wanted_bytes} 字节，固定容量 {budget_bytes} 字节"
            )
        changed += 1

    return bytes(out), changed, truncated, warnings
