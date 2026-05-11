#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from snc_common import inject_file


def main() -> int:
    ap = argparse.ArgumentParser(description="Inject UTF-8 JSON translations into EVIT .snc scripts, rebuilding CP932 string pool non-equal-length.")
    ap.add_argument("input_snc", type=Path, help="original .snc file or directory")
    ap.add_argument("json", type=Path, help="translation .json file or directory")
    ap.add_argument("output_snc", type=Path, help="output .snc file or directory")
    ap.add_argument("--copy-missing", action="store_true", help="when batch injecting, copy .snc files without matching JSON unchanged")
    args = ap.parse_args()

    if args.input_snc.is_file():
        if not args.json.is_file():
            raise SystemExit("single-file injection requires a JSON file")
        stat = inject_file(args.input_snc, args.json, args.output_snc)
        print(f"[inject] {stat['file']}: {stat['old_size']} -> {stat['new_size']} bytes, translated={stat['translated']}")
        return 0

    files = sorted(args.input_snc.glob("*.snc"))
    if not files:
        raise SystemExit(f"no .snc files found: {args.input_snc}")
    args.output_snc.mkdir(parents=True, exist_ok=True)
    injected = copied = 0
    for src in files:
        js = args.json / (src.stem + ".json")
        dst = args.output_snc / src.name
        if not js.exists():
            if args.copy_missing:
                shutil.copy2(src, dst)
                copied += 1
                print(f"[copy] {src.name}: no json")
            continue
        stat = inject_file(src, js, dst)
        injected += 1
        print(f"[inject] {stat['file']}: {stat['old_size']} -> {stat['new_size']} bytes, translated={stat['translated']}")
    print(f"done: injected={injected} copied={copied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
