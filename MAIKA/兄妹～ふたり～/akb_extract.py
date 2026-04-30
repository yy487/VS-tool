#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List

from akb_op import (
    DEFAULT_ENCODING,
    dump_json,
    iter_adb_files,
    parse_adb,
    split_msg_text,
)


def extract_file(path: Path, root: Path, *, encoding: str = DEFAULT_ENCODING, strict: bool = True) -> List[dict]:
    data = path.read_bytes()
    rel = path.relative_to(root).as_posix() if root.is_dir() else path.name
    ins_list = parse_adb(data, encoding=encoding, strict=strict)

    items: List[dict] = []
    pending_voice = ""

    for ins in ins_list:
        if ins.opcode == 0x0035:
            pending_voice = ins.text or ""
            continue

        if ins.opcode == 0x0000 and ins.text is not None:
            parsed = split_msg_text(ins.text)
            if not parsed:
                continue
            line_id, ctrl, name, message, body = parsed
            items.append({
                "type": "msg",
                "file": rel,
                "offset": ins.start,
                "size": ins.end - ins.start,
                "line_id": line_id,
                "ctrl": ctrl,
                "voice": "" if pending_voice == " " else pending_voice,
                "name": name,
                "message": message,
                "translation": "",
            })
            pending_voice = ""
            continue

        if ins.opcode == 0x0001 and ins.text is not None:
            # Only entries inside 0x0006/0x0007 should normally appear; the opcode is distinctive enough.
            items.append({
                "type": "choice",
                "file": rel,
                "offset": ins.start,
                "size": ins.end - ins.start,
                "target": ins.target,
                "message": ins.text,
                "translation": "",
            })
            continue

    return items


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract TWO/AKB ADB messages and choices to JSON.")
    ap.add_argument("input", help="ADB file or directory containing ADB files")
    ap.add_argument("-o", "--output", default="akb_text.json", help="output JSON path")
    ap.add_argument("--encoding", default=DEFAULT_ENCODING, help="script string encoding, default cp932")
    ap.add_argument("--strict", action="store_true", help="fail on unknown opcodes instead of resyncing")
    args = ap.parse_args()

    in_path = Path(args.input)
    root = in_path if in_path.is_dir() else in_path.parent
    all_items: List[dict] = []

    failures = []
    for adb in iter_adb_files(in_path):
        try:
            all_items.extend(extract_file(adb, root, encoding=args.encoding, strict=args.strict))
        except Exception as e:
            failures.append(f"{adb}: {e}")

    dump_json(all_items, Path(args.output))

    print(f"extracted: {len(all_items)} entries -> {args.output}")
    msg_count = sum(1 for x in all_items if x.get("type") == "msg")
    choice_count = sum(1 for x in all_items if x.get("type") == "choice")
    print(f"  msg: {msg_count}")
    print(f"  choice: {choice_count}")

    if failures:
        print("failures:")
        for f in failures:
            print("  " + f)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
