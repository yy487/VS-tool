#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI5WIN / Silky's AI5 ARC 解包命令行工具。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from ai5win_arc_common import ArcFormatError, extract_arc, read_entries


def _default_out_dir(arc_path: Path, out_root: Path | None) -> Path:
    if out_root is None:
        return arc_path.with_suffix("")
    # 批量解包时给每个 ARC 单独建目录，避免重名覆盖。
    return out_root / arc_path.stem


def cmd_list(args: argparse.Namespace) -> int:
    for arc_name in args.arc:
        arc_path = Path(arc_name)
        with arc_path.open("rb") as fp:
            entries = read_entries(fp, encoding=args.encoding, validate=not args.no_validate)
        print(f"{arc_path}: {len(entries)} entries")
        for e in entries:
            print(f"[{e.index:04d}] {e.name:<12} size=0x{e.size:08X} off=0x{e.offset:08X}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    arcs = [Path(p) for p in args.arc]
    out_root = Path(args.out) if args.out else None

    if len(arcs) > 1 and out_root is None:
        raise SystemExit("批量解包时建议指定 -o/--out 作为输出根目录")

    total = 0
    for arc_path in arcs:
        out_dir = _default_out_dir(arc_path, out_root)
        entries = extract_arc(
            arc_path=arc_path,
            out_dir=out_dir,
            encoding=args.encoding,
            overwrite=args.overwrite,
            write_manifest=not args.no_manifest,
        )
        total += len(entries)
        print(f"[OK] {arc_path} -> {out_dir} ({len(entries)} files)")

    print(f"done: {len(arcs)} archive(s), {total} file(s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract AI5WIN/Silky's AI5 .ARC archives."
    )
    parser.add_argument(
        "--encoding",
        default="cp932",
        help="目录文件名解码编码，默认 cp932；多数 ARC 名称为 ASCII，可保持默认",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出 ARC 目录")
    p_list.add_argument("arc", nargs="+", help="一个或多个 .ARC 文件")
    p_list.add_argument("--no-validate", action="store_true", help="跳过目录边界校验")
    p_list.set_defaults(func=cmd_list)

    p_extract = sub.add_parser("extract", help="解包 ARC")
    p_extract.add_argument("arc", nargs="+", help="一个或多个 .ARC 文件")
    p_extract.add_argument("-o", "--out", help="输出目录；批量时作为输出根目录")
    p_extract.add_argument("--overwrite", action="store_true", help="允许覆盖已存在文件")
    p_extract.add_argument("--no-manifest", action="store_true", help="不输出 _arc_manifest.json")
    p_extract.set_defaults(func=cmd_extract)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ArcFormatError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
