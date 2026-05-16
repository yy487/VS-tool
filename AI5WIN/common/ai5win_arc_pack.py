#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI5WIN ARC 通用封包器。

推荐工作流：
    1) python ai5win_arc_extract.py mes.arc mes_dir
    2) 修改 mes_dir 里的文件
    3) python ai5win_arc_pack.py mes_dir mes_new.arc --source-arc mes.arc

如果目录中存在 ai5win_manifest.json，会优先复用其中的 scheme 和原始顺序。
如果同时提供 --source-arc，未修改/不存在于目录中的条目会从原包复制原始数据块。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from ai5win_arc_common import (
    CP932,
    ArcScheme,
    build_arc_bytes,
    choose_name_length,
    has_compressed_ext,
    load_manifest,
    maybe_pack_entry_data,
    normalize_arc_name,
    read_index,
    read_stored_blob,
    scheme_from_args_or_manifest,
)


def collect_files(in_dir: Path, keep_path: bool) -> list[str]:
    names: list[str] = []
    for p in sorted(in_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.name == "ai5win_manifest.json":
            continue
        rel = p.relative_to(in_dir).as_posix()
        name = rel if keep_path else p.name
        names.append(normalize_arc_name(name))

    # 非 keep_path 模式下，防止不同子目录同名文件覆盖。
    if not keep_path:
        seen = set()
        dup = set()
        for n in names:
            if n.lower() in seen:
                dup.add(n)
            seen.add(n.lower())
        if dup:
            raise ValueError(f"duplicate basename in flat mode: {sorted(dup)}")
    return names


def find_input_file(in_dir: Path, arc_name: str, keep_path: bool) -> Optional[Path]:
    if keep_path:
        p = in_dir / arc_name
        return p if p.is_file() else None

    # AI5WIN 多数为平铺包；默认按 basename 匹配，兼容手动改文件。
    target = Path(arc_name).name.lower()
    matches = [p for p in in_dir.rglob("*") if p.is_file() and p.name.lower() == target]
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(f"multiple files match {arc_name!r}: {matches}")
    return matches[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="Pack AI5WIN ARC archive")
    ap.add_argument("in_dir", help="input directory")
    ap.add_argument("out_arc", help="output .arc")
    ap.add_argument("--source-arc",
                    help="原始 ARC。用于自动猜 scheme、保持原顺序、复制未替换条目的原始数据块")
    ap.add_argument("--manifest",
                    help="manifest 路径；默认尝试 in_dir/ai5win_manifest.json")
    ap.add_argument("--scheme-json",
                    help="scheme JSON 或包含 scheme 字段的 manifest")
    ap.add_argument("--encoding", default=CP932,
                    help="文件名编码，默认 cp932")
    ap.add_argument("--keep-path", action="store_true",
                    help="保留相对路径；默认按 AI5WIN 常见平铺包处理")
    ap.add_argument("--rebuild-all", action="store_true",
                    help="即使有 source-arc，也强制用目录文件重建全部条目")
    ap.add_argument("--add-new", action="store_true",
                    help="manifest/source-arc 之外的文件也加入包末尾")
    ap.add_argument("--store-all", action="store_true",
                    help="所有条目都不做 LZSS 压缩；一般不建议用于 mes/lib/a/a6/msk/x")
    ap.add_argument("--lzss-mode", choices=("greedy", "literal"), default="greedy",
                    help="LZSS 压缩模式：greedy 体积较小；literal 最保守但体积较大")
    ap.add_argument("--name-length",
                    help="无 manifest/source-arc 时手动指定 NameLength，如 0x14")
    ap.add_argument("--name-key", default="0",
                    help="无 manifest/source-arc 时手动指定 NameKey")
    ap.add_argument("--size-key", default="0",
                    help="无 manifest/source-arc 时手动指定 SizeKey")
    ap.add_argument("--offset-key", default="0",
                    help="无 manifest/source-arc 时手动指定 OffsetKey")
    args = ap.parse_args()

    in_dir = Path(args.in_dir)
    out_arc = Path(args.out_arc)
    if not in_dir.is_dir():
        raise NotADirectoryError(in_dir)

    manifest = None
    manifest_path = Path(args.manifest) if args.manifest else (in_dir / "ai5win_manifest.json")
    if manifest_path.is_file():
        manifest = load_manifest(manifest_path)

    source_entries = None
    source_scheme = None
    if args.source_arc:
        source_scheme, source_entries = read_index(args.source_arc, encoding=args.encoding)

    # scheme 优先级：CLI scheme-json > manifest > source-arc > 手动 keys/自动 name_length。
    if args.scheme_json or manifest:
        scheme = scheme_from_args_or_manifest(args, manifest)
    elif source_scheme:
        scheme = source_scheme
    elif args.name_length:
        scheme = scheme_from_args_or_manifest(args, None)
    else:
        raise ValueError(
            "missing scheme: use an extracted ai5win_manifest.json, --source-arc, "
            "--scheme-json, or explicit --name-length/--name-key/--size-key/--offset-key"
        )

    # 确定条目顺序。
    order: list[str] = []
    if manifest and "entries" in manifest:
        order = [normalize_arc_name(e["name"]) for e in manifest["entries"]]
    elif source_entries:
        order = [normalize_arc_name(e.name) for e in source_entries]
    else:
        order = collect_files(in_dir, keep_path=args.keep_path)

    source_by_name = {}
    if source_entries:
        source_by_name = {normalize_arc_name(e.name).lower(): e for e in source_entries}

    items: list[tuple[str, bytes]] = []
    used_lower = set()

    for name in order:
        used_lower.add(name.lower())
        p = find_input_file(in_dir, name, keep_path=args.keep_path)

        if p is None and args.source_arc and not args.rebuild_all:
            # 目录里没有这个文件：直接复制原包的存储块，避免不必要重压缩。
            src_entry = source_by_name.get(name.lower())
            if src_entry is None:
                raise FileNotFoundError(f"{name}: not found in input dir or source arc")
            blob = read_stored_blob(args.source_arc, src_entry)
            items.append((name, blob))
            continue

        if p is None:
            raise FileNotFoundError(f"{name}: not found in input dir")

        plain = p.read_bytes()
        compress = (not args.store_all) and has_compressed_ext(name)
        blob = maybe_pack_entry_data(name, plain, compress=compress, lzss_mode=args.lzss_mode)
        items.append((name, blob))

    if args.add_new:
        for name in collect_files(in_dir, keep_path=args.keep_path):
            if name.lower() in used_lower:
                continue
            p = find_input_file(in_dir, name, keep_path=args.keep_path)
            if p is None:
                continue
            plain = p.read_bytes()
            compress = (not args.store_all) and has_compressed_ext(name)
            blob = maybe_pack_entry_data(name, plain, compress=compress, lzss_mode=args.lzss_mode)
            items.append((name, blob))

    arc_data = build_arc_bytes(items, scheme, encoding=args.encoding)
    out_arc.parent.mkdir(parents=True, exist_ok=True)
    out_arc.write_bytes(arc_data)

    print(f"[OK] packed: {out_arc}")
    print(f"     entries : {len(items)}")
    print(f"     size    : {len(arc_data)} bytes")
    print(
        "     scheme  : "
        f"NameLength=0x{scheme.name_length:X}, "
        f"NameKey=0x{scheme.name_key:02X}, "
        f"SizeKey=0x{scheme.size_key:08X}, "
        f"OffsetKey=0x{scheme.offset_key:08X}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
