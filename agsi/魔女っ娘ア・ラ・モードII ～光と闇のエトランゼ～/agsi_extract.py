# -*- coding: utf-8 -*-
"""AGSI SB2 情况 A 文本提取工具。

只从 CODE 指令流中提取可翻译项：
- Mess$is / MessC$s：正文
- Cmd1$s ~ Cmd5$s：选项

不提取 Talk$s、Voice$s、Change$s、Map$ii、资源名等。

重要：同一个 CSTR id 可能被 CODE 多处引用。默认按 CSTR id 去重，
把所有引用位置放进 _refs，避免翻译时同一 _cstr_id 出现多份不同译文。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agsi_common import (
    CHOICE_APIS,
    DEFAULT_ENCODING,
    MAP_APIS,
    JUMP_APIS,
    RESOURCE_APIS,
    TALK_APIS,
    TEXT_APIS,
    VOICE_APIS,
    get_cstr_count,
    iter_call_events,
    parse_api_table,
    prev_push_int_before_str,
    prev_push_str,
    read_cstr_decode,
)


def should_keep_text(s: str, keep_empty: bool = False) -> bool:
    if s == "":
        return keep_empty
    return True


def add_or_merge(results_by_sid: dict[int, dict], item: dict, dedupe: bool) -> None:
    """加入一条提取结果。

    dedupe=True 时：同一 _cstr_id 只输出一次，所有引用位置合并进 _refs。
    dedupe=False 时：保留旧行为，每个 CODE 调用点输出一条。
    """
    sid = int(item["_cstr_id"])
    ref_keys = [
        "_kind", "_api", "_select_group", "_code_off", "_push_off",
        "_msg_no", "_voice", "_talk", "_talk_api",
    ]
    ref = {k: item[k] for k in ref_keys if k in item}

    if not dedupe:
        # 用负数偏移避免 dict key 冲突；调用方只取 values。
        results_by_sid[-len(results_by_sid) - 1] = item
        return

    if sid not in results_by_sid:
        base = dict(item)
        base["_refs"] = [ref]
        results_by_sid[sid] = base
        return

    base = results_by_sid[sid]
    base.setdefault("_refs", []).append(ref)

    # 如果同一字符串同时作为正文/选项出现，顶层标记为 mixed，具体位置看 _refs。
    if base.get("_kind") != item.get("_kind"):
        base["_kind"] = "mixed"
    if base.get("_api") != item.get("_api"):
        base["_api"] = "mixed"


def extract(
    dump_dir: Path,
    encoding: str = DEFAULT_ENCODING,
    keep_empty: bool = False,
    dedupe: bool = True,
) -> list[dict]:
    cstr_entries = read_cstr_decode(dump_dir, encoding=encoding)
    cstr_count = get_cstr_count(dump_dir)
    code = (dump_dir / "CODE.bin").read_bytes()
    _apis, api_by_addr = parse_api_table(dump_dir / "FTBL_1.bin", encoding=encoding)

    results_by_sid: dict[int, dict] = {}
    current_talk = None
    current_talk_api = None
    current_voice = None
    select_group = 0

    for ev in iter_call_events(code, api_by_addr):
        api = ev.api
        ps = prev_push_str(code, ev.call_off, cstr_count)

        if api in TALK_APIS:
            if ps:
                _push_off, sid = ps
                current_talk = cstr_entries[sid].text
                current_talk_api = api
            elif api == "TalkC$":
                current_talk = None
                current_talk_api = api
            continue

        if api in VOICE_APIS:
            if ps:
                _push_off, sid = ps
                current_voice = cstr_entries[sid].text
            continue

        if api == "SelectClr$i":
            select_group += 1
            continue

        if api in TEXT_APIS:
            if not ps:
                continue
            push_off, sid = ps
            text = cstr_entries[sid].text
            if not should_keep_text(text, keep_empty=keep_empty):
                continue
            msg_no_info = prev_push_int_before_str(code, push_off)
            item = {
                "_kind": TEXT_APIS[api],
                "_api": api,
                "_cstr_id": sid,
                "_code_off": f"0x{ev.call_off:08x}",
                "_push_off": f"0x{push_off:08x}",
                "scr_msg": text,
                "message": text,
            }
            if msg_no_info is not None:
                item["_msg_no"] = msg_no_info[1]
            if current_voice:
                item["_voice"] = current_voice
            if current_talk:
                item["_talk"] = current_talk
                item["_talk_api"] = current_talk_api
            add_or_merge(results_by_sid, item, dedupe=dedupe)
            continue

        if api in CHOICE_APIS:
            if not ps:
                continue
            push_off, sid = ps
            text = cstr_entries[sid].text
            if not should_keep_text(text, keep_empty=keep_empty):
                continue
            item = {
                "_kind": "choice",
                "_api": api,
                "_select_group": select_group,
                "_cstr_id": sid,
                "_code_off": f"0x{ev.call_off:08x}",
                "_push_off": f"0x{push_off:08x}",
                "scr_msg": text,
                "message": text,
            }
            add_or_merge(results_by_sid, item, dedupe=dedupe)
            continue

        if api in RESOURCE_APIS or api in JUMP_APIS or api in MAP_APIS:
            continue

    # 按 CSTR id / 出现顺序稳定输出。
    if dedupe:
        return [results_by_sid[k] for k in sorted(results_by_sid.keys())]
    return list(results_by_sid.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="AGSI SB2 CODE 驱动文本提取器")
    parser.add_argument("dump_dir", help="agsi_sb_tool.py unpack 得到的目录")
    parser.add_argument("output_json", help="输出 JSON")
    parser.add_argument("--encoding", default=DEFAULT_ENCODING, help="字符串编码，默认 cp932")
    parser.add_argument("--keep-empty", action="store_true", help="保留空字符串项，默认不保留")
    parser.add_argument("--no-dedupe", action="store_true", help="不按 _cstr_id 去重，保留每个调用点一条；通常不建议")
    args = parser.parse_args()

    dump_dir = Path(args.dump_dir)
    items = extract(
        dump_dir,
        encoding=args.encoding,
        keep_empty=args.keep_empty,
        dedupe=not args.no_dedupe,
    )
    Path(args.output_json).write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "dump_dir": str(dump_dir),
        "output_json": args.output_json,
        "entries": len(items),
        "dedupe_by_cstr_id": not args.no_dedupe,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
