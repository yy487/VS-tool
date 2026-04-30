#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple

from akb_op import (
    DEFAULT_ENCODING,
    OPCODES,
    build_msg_text,
    encode_cstr,
    iter_adb_files,
    load_json,
    make_relocator,
    p16,
    p32,
    parse_adb,
)


def choose_text(item: dict, *, use_message_when_no_translation: bool) -> str:
    tr = item.get("translation", None)
    if tr not in (None, ""):
        return tr
    if use_message_when_no_translation:
        return item.get("message", "")
    return item.get("message", "")


def build_replacement_for_item(item: dict, encoding: str, errors: str, use_message_when_no_translation: bool) -> bytes:
    typ = item.get("type")
    if typ == "msg":
        message = choose_text(item, use_message_when_no_translation=use_message_when_no_translation)
        full = build_msg_text(item["line_id"], item.get("ctrl", "\\I7"), item.get("name", ""), message)
        return p16(0x0000) + encode_cstr(full, encoding, errors)
    if typ == "choice":
        message = choose_text(item, use_message_when_no_translation=use_message_when_no_translation)
        target = int(item.get("target", 0))
        return p16(0x0001) + p32(target) + encode_cstr(message, encoding, errors)
    raise ValueError(f"unsupported item type: {typ!r}")


def inject_file(src: Path, dst: Path, items: List[dict], *,
                encoding: str = DEFAULT_ENCODING,
                errors: str = "strict",
                use_message_when_no_translation: bool = False,
                strict: bool = True) -> Tuple[int, List[str]]:
    data = src.read_bytes()
    ins_list = parse_adb(data, encoding=encoding, strict=strict)
    by_offset = {int(x["offset"]): x for x in items}

    chunks: List[bytes] = []
    old_new_span: List[Tuple[int, int, int]] = []
    patch_targets: List[Tuple[int, int, int]] = []  # new_abs_pos, size, old_target
    warnings: List[str] = []
    changed = 0
    new_pos = 0

    for ins in ins_list:
        original = ins.raw
        out = original

        item = by_offset.get(ins.start)
        if item is not None and ins.opcode in (0x0000, 0x0001):
            new_bytes = build_replacement_for_item(item, encoding, errors, use_message_when_no_translation)
            if new_bytes != original:
                out = new_bytes
                changed += 1

        # Record relocation-relevant replacement span.
        if len(out) != len(original):
            old_new_span.append((ins.start, ins.end, len(out)))

        # Record target fields after replacement. For a replaced choice, target is still at payload offset 0.
        spec = OPCODES.get(ins.opcode)
        if spec is not None:
            for rel_off, size in spec.target_fields:
                old_target = None
                if ins.opcode == 0x0001:
                    old_target = int(item.get("target", ins.target)) if item is not None else ins.target
                else:
                    field_abs = ins.payload_start + rel_off
                    if field_abs + size <= ins.end:
                        old_target = int.from_bytes(data[field_abs:field_abs + size], "little")
                if old_target is not None:
                    new_field_abs = new_pos + 2 + rel_off
                    patch_targets.append((new_field_abs, size, old_target))

        chunks.append(out)
        new_pos += len(out)

    rebuilt = bytearray(b"".join(chunks))
    relocate = make_relocator(old_new_span)

    for new_field_abs, size, old_target in patch_targets:
        if old_target > len(data):
            continue
        new_target = relocate(old_target)
        if size == 4:
            rebuilt[new_field_abs:new_field_abs + 4] = p32(new_target)
        elif size == 2:
            rebuilt[new_field_abs:new_field_abs + 2] = p16(new_target)
        else:
            warnings.append(f"unsupported target field size {size} at new 0x{new_field_abs:X}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(rebuilt)
    return changed, warnings


def main() -> None:
    ap = argparse.ArgumentParser(description="Inject JSON translations into TWO/AKB ADB scripts.")
    ap.add_argument("input", help="source ADB file or directory")
    ap.add_argument("json", help="JSON produced by akb_extract.py")
    ap.add_argument("-o", "--output", required=True, help="output ADB file or directory")
    ap.add_argument("--encoding", default=DEFAULT_ENCODING, help="script string encoding, default cp932")
    ap.add_argument("--errors", default="strict", choices=["strict", "replace", "ignore"], help="encoding error mode")
    ap.add_argument("--use-message", action="store_true",
                    help="inject item.message when translation is empty; useful after overwriting message in JSON")
    ap.add_argument("--loose", action="store_true", help="recover from unknown opcodes; not recommended for final build")
    args = ap.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    root = in_path if in_path.is_dir() else in_path.parent

    items_by_file = defaultdict(list)
    for item in load_json(Path(args.json)):
        items_by_file[item["file"]].append(item)

    total_changed = 0
    failures = []

    for src in iter_adb_files(in_path):
        rel = src.relative_to(root).as_posix() if root.is_dir() else src.name
        if in_path.is_dir():
            dst = out_path / rel
        else:
            dst = out_path

        file_items = items_by_file.get(rel, [])
        if not file_items:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            continue

        try:
            changed, warnings = inject_file(
                src, dst, file_items,
                encoding=args.encoding,
                errors=args.errors,
                use_message_when_no_translation=args.use_message,
                strict=not args.loose,
            )
            total_changed += changed
            for w in warnings:
                print(f"warning {rel}: {w}")
        except Exception as e:
            failures.append(f"{src}: {e}")

    print(f"injected changed entries: {total_changed}")
    print(f"output: {out_path}")

    if failures:
        print("failures:")
        for f in failures:
            print("  " + f)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
