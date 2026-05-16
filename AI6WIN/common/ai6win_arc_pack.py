#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI6WIN ARC 批量封包。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai6win_arc_common import CP932, pack_arc


def main() -> None:
    parser = argparse.ArgumentParser(description="AI6WIN ARC packer (batch)")
    parser.add_argument("input_dir", help="输入目录，即解包/修改后的文件目录")
    parser.add_argument("out_arc", help="输出 .arc 文件")
    parser.add_argument("--manifest", default="", help="manifest 路径；默认读取 input_dir/ai6win_manifest.json")
    parser.add_argument("--source-arc", default="", help="原始 ARC；用于复用未修改条目的原始压缩 blob")
    parser.add_argument("--encoding", default=CP932, help="文件名编码，默认 cp932")
    parser.add_argument("--compress-policy", choices=["manifest", "auto-ext", "all", "none"], default="manifest",
                        help="压缩策略：默认 manifest=沿用原包 packed 标记；无 manifest 时按扩展名")
    parser.add_argument("--lzss-mode", choices=["greedy", "literal"], default="greedy",
                        help="LZSS 压缩模式，默认 greedy")
    parser.add_argument("--no-reuse-stored", action="store_true",
                        help="不复用原包未修改条目的 stored blob，强制从 input_dir 重建")
    args = parser.parse_args()

    stats = pack_arc(
        Path(args.input_dir),
        Path(args.out_arc),
        manifest_path=Path(args.manifest) if args.manifest else None,
        source_arc=Path(args.source_arc) if args.source_arc else None,
        encoding=args.encoding,
        compress_policy=args.compress_policy,
        lzss_mode=args.lzss_mode,
        reuse_stored=not args.no_reuse_stored,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
