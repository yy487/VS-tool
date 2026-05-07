#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inject edited story text into current-game Silky MES files.

The injector performs non-equal-length replacement by rebuilding the decoded MES
and relocating confirmed base-relative u32 fields:
  - opcode 01 jump target
  - opcode 10 conditional target
  - opcode 11 call target
  - opcode 13 choice table target
  - choice table condition_rel / text_rel

It does not patch ARC directly. Repack the modified MES directory with the ARC
packer after injection.
"""
from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from silky_mes_op import (
    DEFAULT_ENCODING,
    apply_relocations,
    build_message_record,
    collect_relocations,
    decode_mes,
    encode_mes,
    iter_cfg_commands,
    parse_message_record,
    rebuild_with_replacements,
)


def _load_items(json_path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Load either one translation JSON or a directory of per-MES JSON files."""
    docs: list[dict[str, Any] | list[Any]] = []
    if json_path.is_dir():
        for fp in sorted(json_path.glob("*.json")):
            docs.append(json.loads(fp.read_text(encoding="utf-8")))
    else:
        docs.append(json.loads(json_path.read_text(encoding="utf-8")))

    encoding = DEFAULT_ENCODING
    items: list[dict[str, Any]] = []
    for doc in docs:
        source_file = None
        if isinstance(doc, dict):
            encoding = doc.get("encoding") or encoding
            source_file = doc.get("source_file")
            one_items = doc.get("items")
        else:
            one_items = doc
        if not isinstance(one_items, list):
            raise ValueError("translation JSON must be a list or contain an 'items' list")
        for it in one_items:
            if not isinstance(it, dict):
                continue
            if source_file and not it.get("_file"):
                it = dict(it)
                it["_file"] = source_file
            items.append(it)
    return encoding, items

def _group_items(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        file_name = it.get("_file")
        if not file_name:
            continue
        grouped[str(file_name)].append(it)
    for file_name in grouped:
        grouped[file_name].sort(key=lambda x: int(x.get("_offset", -1)))
    return grouped


def _format_name_label(name: str, left: str, right: str) -> str:
    return f"{left}{name}{right}"


PUNCT_MAP = {
    " ": "　",
    "“": "「", "”": "」",
    "‘": "『", "’": "』",
    "«": "「", "»": "」",
    "—": "―",
    "－": "ー",
    "~": "～",
}

def _normalize_ascii_to_fullwidth(text: str) -> str:
    table = {" ": "　"}
    table.update({chr(0x21 + i): chr(0xFF01 + i) for i in range(0x5E)})
    trans = str.maketrans(table)
    out = []
    for ch in text:
        if ch == "\n":
            out.append(ch)
            continue
        ch = PUNCT_MAP.get(ch, ch)
        out.append(ch.translate(trans))
    return "".join(out)


def _load_replace_map(map_path: str | None) -> dict[str, str]:
    """Load target_char -> source_char replacement map.

    Supported formats:
      - silky_bfd_replace_map_v1: {"chars": [{target_char, source_char}], ...}
      - plain dict: {"中": "亜", ...}
      - list of dicts: [{"target_char": "中", "source_char": "亜"}, ...]

    direct_cp932_chars are intentionally ignored because their source equals target.
    """
    if not map_path:
        return {}
    doc = json.loads(Path(map_path).read_text(encoding="utf-8"))
    out: dict[str, str] = {}

    def add(target: Any, source: Any) -> None:
        if target is None or source is None:
            return
        t = str(target)
        s = str(source)
        if not t or not s:
            return
        if t[0] != s[0]:
            out[t[0]] = s[0]

    if isinstance(doc, dict):
        for key in ("chars", "map", "mappings"):
            obj = doc.get(key)
            if isinstance(obj, list):
                for ent in obj:
                    if isinstance(ent, dict):
                        add(ent.get("target_char") or ent.get("target") or ent.get("char"), ent.get("source_char") or ent.get("source") or ent.get("src"))
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, str):
                        add(k, v)
                    elif isinstance(v, dict):
                        add(k, v.get("source_char") or v.get("source") or v.get("src"))
        # Plain dict fallback.  Skip known metadata keys.
        for k, v in doc.items():
            if k in {"format", "encoding", "font", "summary", "chars", "direct_cp932_chars", "unmapped_chars", "map", "mappings"}:
                continue
            if isinstance(v, str):
                add(k, v)
    elif isinstance(doc, list):
        for ent in doc:
            if isinstance(ent, dict):
                add(ent.get("target_char") or ent.get("target") or ent.get("char"), ent.get("source_char") or ent.get("source") or ent.get("src"))
    return out


def _apply_replace_map(text: str, repl: dict[str, str], *, normalize_ascii: bool) -> str:
    if normalize_ascii:
        text = _normalize_ascii_to_fullwidth(text)
    if not repl:
        return text
    return "".join(repl.get(ch, ch) for ch in text)


def _append_replacement(replacements: list[tuple[int, int, bytes]], start: int, end: int, blob: bytes) -> bool:
    for a, b, _ in replacements:
        if not (end <= a or start >= b):
            raise ValueError(f"overlapping replacement: 0x{start:X}-0x{end:X} overlaps 0x{a:X}-0x{b:X}")
    replacements.append((start, end, blob))
    return True


def inject_file(src_file: Path, dst_file: Path, items: list[dict[str, Any]], *, encoding: str, decoded: bool, errors: str, on_mismatch: str, dry_run: bool = False, replace_map: dict[str, str] | None = None, normalize_ascii: bool = True) -> dict[str, Any]:
    raw = src_file.read_bytes()
    data = decode_mes(raw, already_decoded=decoded)
    commands = iter_cfg_commands(data, encoding)
    relocs = collect_relocations(data, commands, encoding)

    replacements: list[tuple[int, int, bytes]] = []
    changed = 0
    changed_names = 0
    mapped_chars = 0
    skipped = 0
    mismatches: list[str] = []

    seen_msg_offsets: set[int] = set()
    seen_name_offsets: set[int] = set()
    for it in items:
        try:
            off = int(it["_offset"])
        except Exception:
            skipped += 1
            continue
        if off in seen_msg_offsets:
            skipped += 1
            continue
        seen_msg_offsets.add(off)

        # Optional paired speaker name record.  Extractor v2 stores names as a
        # separate hidden MESSAGE record and exposes only `name` on the dialogue item.
        if "name" in it and "_name_offset" in it:
            try:
                name_off = int(it["_name_offset"])
            except Exception:
                name_off = -1
            if name_off >= 0 and name_off not in seen_name_offsets:
                seen_name_offsets.add(name_off)
                name_msg = parse_message_record(data, name_off, encoding)
                if name_msg is None:
                    text = f"name 0x{name_off:X}: not a message record"
                    if on_mismatch == "error":
                        raise ValueError(f"{src_file.name}: {text}")
                    mismatches.append(text)
                else:
                    expected_name_record = str(it.get("_name_scr_msg") or name_msg.text)
                    if expected_name_record != name_msg.text:
                        text = f"name 0x{name_off:X}: scr_msg mismatch, file={name_msg.text!r}, json={expected_name_record!r}"
                        if on_mismatch == "error":
                            raise ValueError(f"{src_file.name}: {text}")
                        mismatches.append(text)
                    if expected_name_record == name_msg.text or on_mismatch == "patch":
                        left = str(it.get("_name_left") or "［")
                        right = str(it.get("_name_right") or "］")
                        visible_name_record_text = _format_name_label(str(it.get("name", "")), left, right)
                        new_name_record_text = _apply_replace_map(visible_name_record_text, replace_map or {}, normalize_ascii=normalize_ascii)
                        mapped_chars += sum(1 for a, b in zip(visible_name_record_text, new_name_record_text) if a != b)
                        if new_name_record_text != name_msg.text:
                            new_name_record = build_message_record(name_msg.msg_id, new_name_record_text, encoding, errors)
                            _append_replacement(replacements, name_msg.offset, name_msg.end, new_name_record)
                            changed_names += 1

        msg = parse_message_record(data, off, encoding)
        if msg is None:
            text = f"0x{off:X}: not a message record"
            if on_mismatch == "error":
                raise ValueError(f"{src_file.name}: {text}")
            mismatches.append(text)
            skipped += 1
            continue

        scr_msg = it.get("scr_msg", "")
        if scr_msg != msg.text:
            text = f"0x{off:X}: scr_msg mismatch, file={msg.text!r}, json={scr_msg!r}"
            if on_mismatch == "error":
                raise ValueError(f"{src_file.name}: {text}")
            mismatches.append(text)
            if on_mismatch == "skip":
                skipped += 1
                continue

        visible_text = str(it.get("message", it.get("msg", scr_msg)))
        new_text = _apply_replace_map(visible_text, replace_map or {}, normalize_ascii=normalize_ascii)
        mapped_chars += sum(1 for a, b in zip(visible_text, new_text) if a != b)
        if new_text == msg.text:
            continue
        new_record = build_message_record(msg.msg_id, new_text, encoding, errors)
        _append_replacement(replacements, msg.offset, msg.end, new_record)
        changed += 1

    if not replacements:
        if not dry_run:
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            if src_file.resolve() != dst_file.resolve():
                shutil.copy2(src_file, dst_file)
        return {"file": src_file.name, "changed": 0, "changed_names": 0, "mapped_chars": mapped_chars, "skipped": skipped, "relocations": 0, "mismatches": mismatches}

    new_data, map_offset = rebuild_with_replacements(data, replacements)
    new_data_ba = bytearray(new_data)
    warnings = apply_relocations(new_data_ba, relocs, map_offset)
    out_raw = encode_mes(bytes(new_data_ba), already_encoded=decoded)

    if not dry_run:
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        dst_file.write_bytes(out_raw)

    return {
        "file": src_file.name,
        "changed": changed,
        "changed_names": changed_names,
        "mapped_chars": mapped_chars,
        "skipped": skipped,
        "relocations": len(relocs),
        "old_size": len(raw),
        "new_size": len(out_raw),
        "delta": len(out_raw) - len(raw),
        "mismatches": mismatches,
        "warnings": warnings,
    }


def cmd_inject(args: argparse.Namespace) -> int:
    in_dir = Path(args.input)
    out_dir = Path(args.output)
    json_encoding, items = _load_items(Path(args.json))
    encoding = args.encoding or json_encoding or DEFAULT_ENCODING
    grouped = _group_items(items)
    replace_map = _load_replace_map(args.map)

    report = []
    for file_name, file_items in grouped.items():
        src = in_dir / file_name if in_dir.is_dir() else in_dir
        if not src.exists():
            if args.missing == "error":
                raise FileNotFoundError(src)
            report.append({"file": file_name, "missing": True})
            continue
        dst = out_dir / file_name if out_dir else src
        report.append(inject_file(src, dst, file_items, encoding=encoding, decoded=args.decoded, errors=args.errors, on_mismatch=args.on_mismatch, dry_run=args.dry_run, replace_map=replace_map, normalize_ascii=not args.keep_ascii))

    if args.copy_unmodified and in_dir.is_dir() and not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        patched = set(grouped)
        for src in sorted(in_dir.iterdir()):
            if not src.is_file() or src.name in patched:
                continue
            dst = out_dir / src.name
            if not dst.exists():
                shutil.copy2(src, dst)

    changed_total = sum(r.get("changed", 0) for r in report)
    if args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"patched {changed_total} records in {len(report)} files -> {out_dir if out_dir else in_dir}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Inject story text into current-game Silky MES files")
    p = ap.add_parser if False else ap
    ap.add_argument("input", help="input MES directory, or a single MES when JSON only targets that file")
    ap.add_argument("json", help="translation JSON file or per-file JSON directory produced by silky_mes_extract.py")
    ap.add_argument("output", help="output MES directory or output MES path")
    ap.add_argument("--encoding", default=None, help="text encoding; default from JSON or cp932")
    ap.add_argument("--decoded", action="store_true", help="input/output MES are already XOR-decoded")
    ap.add_argument("--errors", default="strict", choices=["strict", "replace", "ignore"], help="encoding error policy for msg")
    ap.add_argument("--map", help="replace_map.json produced by silky_bfd_font.py; target chars are replaced with CP932 source chars before encoding")
    ap.add_argument("--keep-ascii", action="store_true", help="do not normalize ASCII/basic punctuation to full-width before map/encoding")
    ap.add_argument("--on-mismatch", default="error", choices=["error", "skip", "patch"], help="what to do when scr_msg does not match file content")
    ap.add_argument("--missing", default="warn", choices=["warn", "error"], help="what to do when a JSON file is absent")
    ap.add_argument("--copy-unmodified", action="store_true", help="copy files not mentioned in JSON into output directory")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", help="write detailed injection report JSON")
    args = ap.parse_args(argv)
    return cmd_inject(args)


if __name__ == "__main__":
    raise SystemExit(main())
