#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI5WIN ARC 解包器。
默认对 mes/lib/a/a6/msk/x 做 LZSS 解压，并生成 ai5win_manifest.json。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ai5win_arc_common import (
    CP932,
    COMPRESSED_EXTS,
    ArcScheme,
    has_compressed_ext,
    load_manifest,
    lzss_decompress,
    manifest_from_arc,
    read_index,
    read_stored_blob,
    save_manifest,
    sha1_bytes,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract AI5WIN ARC archive")
    ap.add_argument("arc", help="input .arc")
    ap.add_argument("out_dir", help="output directory")
    ap.add_argument("--raw", action="store_true",
                    help="不解压 LZSS，直接导出包内原始数据块")
    ap.add_argument("--encoding", default=CP932,
                    help="文件名编码，默认 cp932")
    ap.add_argument("--scheme-json",
                    help="可选：指定 scheme JSON；不指定则自动猜测")
    args = ap.parse_args()

    arc_path = Path(args.arc)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scheme = None
    if args.scheme_json:
        obj = load_manifest(args.scheme_json)
        if "scheme" in obj:
            obj = obj["scheme"]
        scheme = ArcScheme.from_json(obj)

    scheme, entries = read_index(arc_path, scheme=scheme, encoding=args.encoding)

    manifest = manifest_from_arc(arc_path, scheme, entries, encoding=args.encoding)
    for m in manifest["entries"]:
        m["extracted_raw"] = bool(args.raw)

    for entry in entries:
        stored = read_stored_blob(arc_path, entry)
        if (not args.raw) and has_compressed_ext(entry.name):
            data = lzss_decompress(stored)
        else:
            data = stored

        out_path = out_dir / entry.name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)

        # 补充解压后 hash/size，方便之后校验。
        for m in manifest["entries"]:
            if m["name"] == entry.name:
                m["unpacked_size"] = len(data)
                m["sha1_unpacked"] = sha1_bytes(data)
                break

    save_manifest(out_dir / "ai5win_manifest.json", manifest)

    print(f"[OK] extracted: {arc_path}")
    print(f"     entries : {len(entries)}")
    print(f"     out_dir : {out_dir}")
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
