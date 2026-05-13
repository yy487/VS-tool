#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract translatable text from Angel/Silky EVIT .snc scripts.

新版重点：
  - 人名识别由 VM 指令流驱动，不再单靠 ``name\\nmessage`` 文本形态判断；
  - 有 voice 调用包围的显示文本，允许拆成 name/message；
  - 无 voice 的显示文本，只在第一行属于已知角色名表时拆 name；
  - 旁白中的正文换行全部删除，交给游戏自动换行；
  - name 与 message 之间的 ``\\n`` 不写入 JSON，注入时自动补回。
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from snc_common import (
    CHOICE_OP, HN, MAP_OPS, ST,
    clean_msg, collect_strings, is_likely_text, load_snc,
    normalize_extracted_message, split_name_msg, write_json,
)

# voice 资源一般是 VHH0760 / VHC2071 这类形式。
def _is_voice_resource(s: str) -> bool:
    t = s.strip()
    return len(t) >= 2 and t[0].upper() == "V" and any(ch.isdigit() for ch in t)


def _voice_ref_for_display(words: List[int], i: int, strings: Dict[int, str]) -> Optional[int]:
    """Return voice ref if code position i is ``0060 st <voice> 0030 <id> st <text>``.

    目前在样本中，带语音对白的结构稳定表现为：
        0060 st <voice_ref> 0030 <line_id> st <text_ref> 0061

    这里严格检查 text 前 5 个 word，避免把上一句的 voice 错挂到下一句。
    """
    if i < 5:
        return None
    if words[i - 5] != 0x0060 or words[i - 4] != ST:
        return None
    # i - 2 是显示 op，通常为 0030；这里放宽到 < 0x0100 是为了兼容少量显示变体。
    if words[i - 2] >= 0x0100:
        return None
    vref = words[i - 3]
    if _is_voice_resource(strings.get(vref, "")):
        return vref
    return None


def _name_candidate_from_text(text: str) -> Optional[str]:
    """Conservatively get first-line speaker candidate from raw string.

    这一步只用于建立“已知人名表”，不直接决定提取结果。
    """
    text = clean_msg(text)
    if "\\n" not in text:
        return None
    first, rest = text.split("\\n", 1)
    first = first.strip()
    if not first or len(first) > 12:
        return None
    if any(ch in first for ch in "「」『』。、？！….,!?;；：:（）()[]{}"):
        return None
    if not rest.lstrip().startswith(("「", "『")):
        return None
    return first


def _iter_display_st_refs(words: List[int], code_start: int) -> Iterable[Tuple[int, int]]:
    """Yield (code_word, ref) for VM-code ST refs that are followed by a display-like op.

    EVIT 脚本里大量正文形态是：
        st <ref> <display-op> <arg>
    所以不能只找 ``0030 ... st``。
    """
    i = code_start
    while i + 2 < len(words):
        if words[i] == ST and words[i + 2] < 0x0100:
            yield i, words[i + 1]
            i += 2
        else:
            i += 1


def _build_known_speakers(words: List[int], code_start: int, strings: Dict[int, str]) -> Set[str]:
    """Build speaker name table from instruction-flow evidence.

    1. 首先收集带 voice 的对白第一行，这是最高可信度；
    2. 再补充在显示指令中高频出现的短名字，用来覆盖主角等无语音对白。
       这一步仍然要求文本形态是短名字 + 引号对白，且出现至少 2 次。
    """
    voice_speakers: Set[str] = set()
    all_candidates: Counter[str] = Counter()

    for code_word, ref in _iter_display_st_refs(words, code_start):
        text = strings.get(ref)
        if text is None or not is_likely_text(clean_msg(text)):
            continue
        cand = _name_candidate_from_text(text)
        if not cand:
            continue
        all_candidates[cand] += 1
        if _voice_ref_for_display(words, code_word, strings) is not None:
            voice_speakers.add(cand)

    # 主角/无语音角色通常没有 voice，但会以同一短名字反复出现。
    frequent_unvoiced = {name for name, count in all_candidates.items() if count >= 2}
    return voice_speakers | frequent_unvoiced


def _message_entry(
    path: Path,
    order: int,
    ref: int,
    text: str,
    *,
    code_word: Optional[int] = None,
    kind: str = "message",
    allow_name: bool = False,
    known_speakers: Optional[Set[str]] = None,
    voice_ref: Optional[int] = None,
) -> dict:
    """Build one JSON text entry from a raw SNC string."""
    # 有 voice 的文本允许按短名字拆；无 voice 文本只允许已知人名拆。
    if allow_name and voice_ref is not None:
        name, raw_msg = split_name_msg(text, allow_name=True, known_speakers=None)
    elif allow_name:
        name, raw_msg = split_name_msg(text, allow_name=True, known_speakers=known_speakers or set())
    else:
        name, raw_msg = None, clean_msg(text)

    # 正文内部换行全部删除；只保留纯正文作为 scr_msg/message。
    msg = normalize_extracted_message(raw_msg)
    base = {
        "_file": path.name,
        "_index": order,
        "_kind": kind,
        "_ref": ref,
        **({"_code_word": code_word} if code_word is not None else {}),
        **({"_voice_ref": voice_ref} if voice_ref is not None else {}),
    }
    if name:
        return {**base, "name": name, "scr_msg": msg, "message": msg}
    return {**base, "scr_msg": msg, "message": msg}


def scan_snc(path: Path, *, encoding: str = "cp932", fallback: bool = False, include_map: bool = True) -> List[dict]:
    data, h, words = load_snc(path)
    strings = collect_strings(data, h, encoding)
    known_speakers = _build_known_speakers(words, h.code_start, strings)

    entries: List[dict] = []
    used_refs: Set[int] = set()
    order = 0
    i = h.code_start

    def add_message(ref: int, code_word: int, kind: str = "message") -> None:
        """Add a code-referenced text string if it passes text filtering."""
        nonlocal order
        if ref in used_refs:
            return
        text = strings.get(ref)
        if text is None:
            return
        text = clean_msg(text)
        if not is_likely_text(text):
            return
        voice_ref = _voice_ref_for_display(words, code_word, strings)
        # 只有“带 voice”或“第一行在已知人名表”时，才允许拆 name。
        cand = _name_candidate_from_text(text)
        allow_name = voice_ref is not None or (cand is not None and cand in known_speakers)
        entries.append(_message_entry(
            path, order, ref, text,
            code_word=code_word,
            kind=kind,
            allow_name=allow_name,
            known_speakers=known_speakers,
            voice_ref=voice_ref,
        ))
        used_refs.add(ref)
        order += 1

    while i < len(words):
        op = words[i]

        # 普通选项菜单：81 hn <var> st <choice0> st <choice1> ... 0000
        if op == CHOICE_OP:
            j = i + 1
            if not (j + 1 < len(words) and words[j] == HN):
                i += 1
                continue
            var_id = words[j + 1]
            j += 2
            choices = []
            while j < len(words) and words[j] != 0:
                if words[j] == ST and j + 1 < len(words):
                    ref = words[j + 1]
                    text = strings.get(ref)
                    if text is not None and is_likely_text(text):
                        msg = normalize_extracted_message(text)
                        choices.append({
                            "index": len(choices),
                            "_ref": ref,
                            "scr_msg": msg,
                            "message": msg,
                        })
                        used_refs.add(ref)
                    j += 2
                else:
                    j += 1
            if choices:
                entries.append({
                    "_file": path.name,
                    "_index": order,
                    "_kind": "choice",
                    "_code_word": i,
                    "_var": var_id,
                    "choices": choices,
                })
                order += 1
            i = max(j + 1, i + 1)
            continue

        if include_map and op in MAP_OPS:
            entries.append({
                "_file": path.name,
                "_index": order,
                "_kind": "map_jump_mode",
                "_code_word": i,
                "_opcode": f"0x{op:02X}",
            })
            order += 1
            i += 1
            continue

        # EVIT 脚本里大量正文是 st <ref> <display-op> <arg>。
        # 只扫 VM code 里实际出现的 st 引用，避免直接扫字符串池造成误提取。
        if op == ST and i + 2 < len(words):
            ref = words[i + 1]
            next_op = words[i + 2]
            if next_op < 0x0100:
                add_message(ref, i, "message")
            i += 2
            continue

        i += 1

    # fallback 只用于逆向查漏，不建议正常本地化使用。
    if fallback:
        for ref, text in sorted(strings.items()):
            if ref in used_refs:
                continue
            t = clean_msg(text)
            if is_likely_text(t):
                entries.append(_message_entry(
                    path, order, ref, t,
                    code_word=None,
                    kind="message_fallback",
                    allow_name=False,
                ))
                order += 1
    return entries


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract text from Angel/Silky EVIT .snc scripts")
    ap.add_argument("input", type=Path, help="input .snc file or directory")
    ap.add_argument("output", type=Path, help="output .json file or directory")
    ap.add_argument("--encoding", default="cp932")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--fallback", action="store_true", help="also scan unreferenced string-pool text; for analysis only")
    ap.add_argument("--no-map-markers", action="store_true")
    args = ap.parse_args()

    include_map = not args.no_map_markers
    if args.input.is_dir():
        args.output.mkdir(parents=True, exist_ok=True)
        files = sorted(args.input.glob("*.snc"))
        total = 0
        for p in files:
            ents = scan_snc(p, encoding=args.encoding, fallback=args.fallback, include_map=include_map)
            total += len(ents)
            write_json(args.output / f"{p.stem}.json", ents, pretty=args.pretty)
        print({"files": len(files), "entries": total})
    else:
        ents = scan_snc(args.input, encoding=args.encoding, fallback=args.fallback, include_map=include_map)
        write_json(args.output, ents, pretty=args.pretty)
        print({"file": args.input.name, "entries": len(ents)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
