#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
from scb_common import parse_scb, iter_scb_files


def main() -> int:
    ap = argparse.ArgumentParser(description="Quick binary compare for original/injected SCB directory.")
    ap.add_argument("original", type=Path)
    ap.add_argument("injected", type=Path)
    args = ap.parse_args()
    files = iter_scb_files(args.original)
    diff = []
    missing = []
    for src in files:
        other = args.injected / src.name if args.injected.is_dir() else args.injected
        if not other.exists():
            missing.append(src.name)
            continue
        a = src.read_bytes()
        b = other.read_bytes()
        if a != b:
            diff.append((src.name, len(a), len(b)))
    print({"files": len(files), "different": len(diff), "missing": len(missing)})
    for x in diff[:20]:
        print("diff", x)
    for x in missing[:20]:
        print("missing", x)
    return 1 if diff or missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
