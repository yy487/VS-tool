#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
from snc_common import extract_file


def iter_snc_files(path: Path):
    if path.is_file():
        yield path
    else:
        yield from sorted(path.glob("*.snc"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract visible CP932 text from EVIT .snc scripts to UTF-8 JSON.")
    ap.add_argument("input", type=Path, help="input .snc file or directory")
    ap.add_argument("output", type=Path, help="output .json file or directory")
    args = ap.parse_args()

    files = list(iter_snc_files(args.input))
    if not files:
        raise SystemExit(f"no .snc files found: {args.input}")

    total_items = total_choices = 0
    for src in files:
        if args.input.is_file():
            dst = args.output
        else:
            dst = args.output / (src.stem + ".json")
        stat = extract_file(src, dst)
        total_items += stat["items"]
        total_choices += stat["choices"]
        print(f"[extract] {stat['file']}: items={stat['items']} choices={stat['choices']} -> {dst}")
    print(f"done: files={len(files)} items={total_items} choices={total_choices}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
