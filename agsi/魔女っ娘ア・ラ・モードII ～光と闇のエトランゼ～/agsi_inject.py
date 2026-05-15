# -*- coding: utf-8 -*-
"""AGSI SB2 情况 A 文本注入工具。

只修改已有 CSTR index 的内容，然后重建 CSTR_decode.bin / CSTR.bin。
不修改 CODE.bin，不新增/删除 CSTR index。

同一个 _cstr_id 在 JSON 中重复出现时，默认要求 message 完全一致；
如果不同，直接报错，避免“last one wins”静默覆盖。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agsi_common import (
    DEFAULT_ENCODING,
    encode_text_checked,
    load_char_map,
    read_cstr_decode,
    rebuild_cstr_files,
)


def normalize_text(s: str) -> str:
    return s.rstrip("\x00")


def load_items(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("translation JSON must be a list")
    return data


def inject(
    dump_dir: Path,
    json_path: Path,
    encoding: str = DEFAULT_ENCODING,
    char_map_path: Path | None = None,
    allow_scr_mismatch: bool = False,
    backup: bool = True,
    duplicate_policy: str = "error",
) -> dict:
    cstr_entries = read_cstr_decode(dump_dir, encoding=encoding)
    raw_entries = [e.raw for e in cstr_entries]
    char_map = load_char_map(char_map_path)
    items = load_items(json_path)

    touched: set[int] = set()
    assigned_text: dict[int, str] = {}
    first_json_index: dict[int, int] = {}
    warnings: list[str] = []

    # 第一遍：校验并合并每个 _cstr_id 的最终文本。
    for pos, item in enumerate(items):
        if not isinstance(item, dict):
            warnings.append(f"skip non-object item at json index {pos}")
            continue
        if "_cstr_id" not in item:
            warnings.append(f"skip item without _cstr_id at json index {pos}")
            continue
        sid = int(item["_cstr_id"])
        if sid < 0 or sid >= len(cstr_entries):
            raise IndexError(f"_cstr_id out of range at json index {pos}: {sid}")
        touched.add(sid)

        old_text = cstr_entries[sid].text
        scr_msg = normalize_text(str(item.get("scr_msg", "")))
        if scr_msg != old_text and not allow_scr_mismatch:
            raise ValueError(
                f"scr_msg mismatch for CSTR[{sid}] at json index {pos}: "
                f"json={scr_msg!r}, current={old_text!r}. "
                f"Use --allow-scr-mismatch only if you know what you are doing."
            )
        new_text = normalize_text(str(item.get("message", scr_msg)))

        if sid in assigned_text:
            prev = assigned_text[sid]
            if prev != new_text:
                msg = (
                    f"conflicting duplicate _cstr_id={sid}; "
                    f"first_json_index={first_json_index[sid]}, current_json_index={pos}; "
                    f"previous={prev!r}, current={new_text!r}"
                )
                if duplicate_policy == "error":
                    raise ValueError(msg + "; re-extract with the fixed extractor or make duplicate messages identical")
                if duplicate_policy == "first":
                    warnings.append(msg + "; first one kept")
                    continue
                if duplicate_policy == "last":
                    warnings.append(msg + "; last one wins")
                elif duplicate_policy == "same":
                    raise ValueError(msg + "; --duplicate-policy same requires identical messages")
        else:
            first_json_index[sid] = pos
        assigned_text[sid] = new_text

    changed = 0
    for sid, new_text in assigned_text.items():
        new_raw = encode_text_checked(new_text, sid, encoding, char_map)
        if new_raw != raw_entries[sid]:
            raw_entries[sid] = new_raw
            changed += 1

    report = rebuild_cstr_files(dump_dir, raw_entries, make_backup=backup)
    report.update({
        "dump_dir": str(dump_dir),
        "json": str(json_path),
        "items": len(items),
        "touched_cstr": len(touched),
        "changed": changed,
        "encoding": encoding,
        "char_map": str(char_map_path) if char_map_path else None,
        "duplicate_policy": duplicate_policy,
        "warnings": warnings[:50],
        "warning_count": len(warnings),
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="AGSI SB2 CSTR 文本注入器：只重建 CSTR，不修改 CODE")
    parser.add_argument("dump_dir", help="agsi_sb_tool.py unpack 得到的目录")
    parser.add_argument("json", help="agsi_extract.py 输出并翻译后的 JSON")
    parser.add_argument("--encoding", default=DEFAULT_ENCODING, help="字符串编码，默认 cp932")
    parser.add_argument("--char-map", help="可选：单字映射 JSON，例如 subs_cn_jp.json")
    parser.add_argument("--allow-scr-mismatch", action="store_true", help="允许 scr_msg 与当前 CSTR 不一致")
    parser.add_argument("--no-backup", action="store_true", help="不生成 CSTR.bin.bak / CSTR_decode.bin.bak")
    parser.add_argument(
        "--duplicate-policy",
        choices=["error", "first", "last", "same"],
        default="error",
        help="同一 _cstr_id 出现不同 message 时的处理方式；默认 error，避免静默覆盖",
    )
    args = parser.parse_args()

    report = inject(
        Path(args.dump_dir),
        Path(args.json),
        encoding=args.encoding,
        char_map_path=Path(args.char_map) if args.char_map else None,
        allow_scr_mismatch=args.allow_scr_mismatch,
        backup=not args.no_backup,
        duplicate_policy=args.duplicate_policy,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
