#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GIRL2_95 XSD 批量提取。

用法：
  python extract.py <XSD文件或目录> -o girl2_extract.json

输出统一 JSON：
  name    可选；没有角色名不输出
  scr_msg 原始脚本文本，不改
  msg     初始等于 scr_msg，只改这个字段
"""
from __future__ import annotations

import argparse
from pathlib import Path

from common import DEFAULT_ENCODING, block_to_entry, collect_text_blocks, decode_xsd, file_key, iter_xsd_files, save_json


def extract_one(path: Path, root: Path, *, encoding: str, decoded: bool) -> list[dict]:
    key = file_key(path, root)
    data = path.read_bytes() if decoded else decode_xsd(path.read_bytes())
    blocks = collect_text_blocks(data, key, encoding=encoding)
    return [block_to_entry(b, encoding=encoding) for b in blocks]


def main() -> int:
    ap = argparse.ArgumentParser(description="GIRL2_95 XSD batch extractor")
    ap.add_argument("input", help="XSD 文件或目录，支持目录批量递归")
    ap.add_argument("-o", "--output", default="girl2_extract.json", help="输出 JSON，默认 girl2_extract.json")
    ap.add_argument("--ext", default=None, help="脚本扩展名。默认：普通模式 .XSD；--decoded 模式为全部文件；可填 .dec")
    ap.add_argument("--encoding", default=DEFAULT_ENCODING, help="文本编码，默认 cp932")
    ap.add_argument("--decoded", action="store_true", help="输入已经是解码后的 bytecode，不再做 XSD 解码")
    args = ap.parse_args()

    root = Path(args.input)
    ext = args.ext if args.ext is not None else ("" if args.decoded else ".XSD")
    files = iter_xsd_files(root, ext)
    entries: list[dict] = []

    for path in files:
        try:
            one = extract_one(path, root, encoding=args.encoding, decoded=args.decoded)
        except Exception as e:
            print(f"[跳过] {path}: {e}")
            continue
        entries.extend(one)
        print(f"[提取] {file_key(path, root)}: {len(one)} 条")

    save_json(args.output, entries)
    print(f"[完成] 文件 {len(files)} 个，文本 {len(entries)} 条 -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
