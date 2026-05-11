#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
from snc_common import SncFile, is_probably_resource_or_label


def main() -> int:
    ap = argparse.ArgumentParser(description="Scan EVIT .snc files and print structure statistics.")
    ap.add_argument("input", type=Path, help="input .snc file or directory")
    args = ap.parse_args()
    files = [args.input] if args.input.is_file() else sorted(args.input.glob("*.snc"))
    total_labels = total_st = total_choice = total_visible = total_bad_lv = 0
    for p in files:
        snc = SncFile.from_path(p)
        bad_lv = 0
        for ch in snc.choices:
            for opt in ch.options:
                if opt.label_index is not None and not (0 <= opt.label_index < len(snc.labels)):
                    bad_lv += 1
        visible = 0
        ref_indices = {r.string_index for r in snc.st_refs}
        for idx in ref_indices:
            if not is_probably_resource_or_label(snc.strings[idx].text):
                visible += 1
        print(f"{p.name}: labels={len(snc.labels)} strings={len(snc.strings)} st={len(snc.st_refs)} choices={len(snc.choices)} visible_refs={visible} bad_lv={bad_lv}")
        total_labels += len(snc.labels)
        total_st += len(snc.st_refs)
        total_choice += len(snc.choices)
        total_visible += visible
        total_bad_lv += bad_lv
    print(f"TOTAL files={len(files)} labels={total_labels} st={total_st} choices={total_choice} visible_refs={total_visible} bad_lv={total_bad_lv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
