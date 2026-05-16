#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI6WIN ARC 批量解包。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai6win_arc_common import CP932, extract_arc


def main() -> None:
    parser = argparse.ArgumentParser(description="AI6WIN ARC extractor (batch)")
    parser.add_argument("arc", help="输入 .arc 文件，例如 mes.arc")
    parser.add_argument("out_dir", help="输出目录")
    parser.add_argument("--encoding", default=CP932, help="文件名编码，默认 cp932")
    parser.add_argument("--manifest-name", default="ai6win_manifest.json", help="manifest 文件名")
    args = parser.parse_args()

    manifest = extract_arc(Path(args.arc), Path(args.out_dir), encoding=args.encoding,
                           manifest_name=args.manifest_name)
    print(json.dumps({
        "format": manifest["format"],
        "entries": len(manifest["entries"]),
        "out_dir": str(Path(args.out_dir)),
        "manifest": str(Path(args.out_dir) / args.manifest_name),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
