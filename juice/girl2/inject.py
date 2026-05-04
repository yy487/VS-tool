#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GIRL2_95 XSD 批量截断注入。

用法：
  python inject.py <XSD文件或目录> --json girl2_extract.json -o out_xsd

说明：
  - 只读取 JSON 里的 msg 字段回注。
  - scr_msg 用于校验错位。
  - 固定偏移截断，不做非等长重定位。
  - 输出 XSD 使用 mode0 封装，未修改文件原样复制。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from common import (
    DEFAULT_ENCODING,
    build_translation_index,
    decode_xsd,
    encode_xsd_mode0,
    file_key,
    inject_truncate,
    iter_xsd_files,
    load_json,
    output_path,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="GIRL2_95 XSD batch truncate injector")
    ap.add_argument("input", help="XSD 文件或目录，支持目录批量递归")
    ap.add_argument("--json", required=True, help="extract.py 输出并修改过 msg 的 JSON")
    ap.add_argument("-o", "--output", required=True, help="输出文件或目录；输入目录时这里是输出目录")
    ap.add_argument("--ext", default=None, help="脚本扩展名。默认：普通模式 .XSD；--decoded 模式为全部文件；可填 .dec")
    ap.add_argument("--encoding", default=DEFAULT_ENCODING, help="注入编码，默认 cp932")
    ap.add_argument("--errors", default="strict", choices=("strict", "replace", "ignore"), help="编码错误处理")
    ap.add_argument("--allow-mismatch", action="store_true", help="scr_msg 不匹配时只警告不中止")
    ap.add_argument("--decoded", action="store_true", help="输入/输出均为已解码 bytecode，不做 XSD 解码/封装")
    args = ap.parse_args()

    root = Path(args.input)
    index = build_translation_index(load_json(args.json))
    ext = args.ext if args.ext is not None else ("" if args.decoded else ".XSD")
    files = iter_xsd_files(root, ext)

    total_changed = 0
    total_truncated = 0
    total_warnings = 0

    for path in files:
        key = file_key(path, root)
        try:
            src = path.read_bytes()
            decoded = src if args.decoded else decode_xsd(src)
            new_decoded, changed, truncated, warnings = inject_truncate(
                decoded,
                index,
                key,
                encoding=args.encoding,
                errors=args.errors,
                allow_mismatch=args.allow_mismatch,
            )
            out_bytes = new_decoded if args.decoded else (encode_xsd_mode0(new_decoded) if changed else src)
        except Exception as e:
            print(f"[失败] {key}: {e}")
            raise

        dst = output_path(path, root, args.output)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(out_bytes)

        total_changed += changed
        total_truncated += truncated
        total_warnings += len(warnings)

        print(f"[注入] {key}: changed={changed}, truncated={truncated}, warnings={len(warnings)} -> {dst}")
        for w in warnings[:20]:
            print(f"  [警告] {w}")
        if len(warnings) > 20:
            print(f"  [警告] ... 还有 {len(warnings) - 20} 条")

    print(f"[完成] 文件 {len(files)} 个，替换 {total_changed} 条，截断 {total_truncated} 段，警告 {total_warnings} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
