#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract story text from current-game Silky MES files.

Output JSON item format follows the project convention:
  optional name, scr_msg, message
plus private locator fields used by the injector:
  _file, _offset, _index, _kind, _msg_id

Name handling:
  The engine stores the speaker name as an independent MESSAGE record such as
  "［千草］" before the actual dialogue MESSAGE record.  This extractor pairs
  that name record with the following dialogue record and suppresses the
  standalone name line from the output.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from silky_mes_op import (
    DEFAULT_ENCODING,
    DEFAULT_SKIP_STEMS,
    decode_mes,
    find_choice_tables,
    is_probably_story_mes,
    iter_cfg_commands,
    scan_all_message_records,
)

NAME_BRACKETS = (
    ("［", "］"),
    ("【", "】"),
    ("[", "]"),
)


def _iter_mes_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    files = sorted(path.glob("*.MES")) + sorted(path.glob("*.mes"))
    seen = set()
    out = []
    for f in files:
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def split_name_label(text: str) -> Optional[tuple[str, str, str]]:
    """Return (name, left_bracket, right_bracket) if text is a pure name label."""
    s = text.strip()
    for left, right in NAME_BRACKETS:
        if s.startswith(left) and s.endswith(right) and len(s) > len(left) + len(right):
            name = s[len(left):-len(right)].strip()
            if name:
                return name, left, right
    return None


def is_story_text(text: str) -> bool:
    s = text.strip()
    if not s:
        return False
    if "\ufffd" in s:
        return False
    # Reject C0 controls produced by false byte scans.  Real dialogue may use
    # ordinary whitespace, but not embedded NUL/STX/ACK/etc. in the JSON text.
    if any((ord(ch) < 0x20 and ch not in "\t\r\n") for ch in s):
        return False
    if all(ch in "％%" for ch in s):
        return False
    if s in {"［", "］", "【", "】", "「", "」", "『", "』"}:
        return False
    return True


def is_story_message_id(msg_id: int) -> bool:
    # Valid story records in this title use compact increasing ids.  Huge values
    # are almost always data bytes misread as 00 <u32 id> message records.
    return 0 <= int(msg_id) <= 1_000_000


def _record_key(item: dict[str, Any]) -> tuple[str, int]:
    return item["_file"], int(item["_offset"])


def _attach_name_fields(record: dict[str, Any], pending: Optional[dict[str, Any]]) -> None:
    if not pending:
        return
    record["name"] = pending["name"]
    record["_name_offset"] = pending["offset"]
    record["_name_msg_id"] = pending["msg_id"]
    record["_name_scr_msg"] = pending["scr_msg"]
    record["_name_left"] = pending["left"]
    record["_name_right"] = pending["right"]


def extract_file(path: Path, *, encoding: str = DEFAULT_ENCODING, decoded: bool = False, scan_messages: bool = False) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    data = decode_mes(raw, already_decoded=decoded)
    commands = iter_cfg_commands(data, encoding)
    choice_offsets: set[int] = set()
    hidden_name_offsets: set[int] = set()
    records: list[dict[str, Any]] = []

    # Directly reached message commands.  Speaker names are separate message
    # records in the VM stream; pair the latest pure name-label record with the
    # following non-name message record.
    pending_name: Optional[dict[str, Any]] = None
    for idx, cmd in enumerate(commands):
        if cmd.message is None or not is_story_message_id(cmd.message.msg_id) or not is_story_text(cmd.message.text):
            continue
        name_info = split_name_label(cmd.message.text)
        if name_info is not None:
            name, left, right = name_info
            pending_name = {
                "name": name,
                "left": left,
                "right": right,
                "offset": cmd.message.offset,
                "msg_id": cmd.message.msg_id,
                "scr_msg": cmd.message.text,
            }
            hidden_name_offsets.add(cmd.message.offset)
            continue

        rec = {
            "_file": path.name,
            "_offset": cmd.message.offset,
            "_index": idx,
            "_kind": "message",
            "_msg_id": cmd.message.msg_id,
            "scr_msg": cmd.message.text,
            "message": cmd.message.text,
        }
        _attach_name_fields(rec, pending_name)
        pending_name = None
        records.append(rec)

    # Choice-table referenced texts.  Choice records do not use speaker names.
    for table in find_choice_tables(data, commands, encoding):
        for item in table.items:
            if item.message is None or not is_story_message_id(item.message.msg_id) or not is_story_text(item.message.text):
                continue
            if split_name_label(item.message.text) is not None:
                hidden_name_offsets.add(item.message.offset)
                continue
            choice_offsets.add(item.message.offset)
            records.append({
                "_file": path.name,
                "_offset": item.message.offset,
                "_choice_table_offset": table.table_rel,
                "_choice_index": item.index,
                "_kind": "choice",
                "_msg_id": item.message.msg_id,
                "scr_msg": item.message.text,
                "message": item.message.text,
            })

    # Supplementary scan: catches data-referenced message records not reached by CFG.
    # Do not output pure speaker-name labels as standalone text.
    if scan_messages:
        for msg in scan_all_message_records(data, encoding):
            if (
                msg.offset in choice_offsets
                or msg.offset in hidden_name_offsets
                or not is_story_message_id(msg.msg_id)
                or not is_story_text(msg.text)
            ):
                continue
            if split_name_label(msg.text) is not None:
                continue
            records.append({
                "_file": path.name,
                "_offset": msg.offset,
                "_kind": "message_scan",
                "_msg_id": msg.msg_id,
                "scr_msg": msg.text,
                "message": msg.text,
            })

    # Deduplicate by exact file + offset. Preserve choice classification when present.
    best: dict[tuple[str, int], dict[str, Any]] = {}
    rank = {"choice": 3, "message": 2, "message_scan": 1}
    for r in records:
        key = _record_key(r)
        if key not in best or rank.get(r.get("_kind", ""), 0) > rank.get(best[key].get("_kind", ""), 0):
            best[key] = r
    return sorted(best.values(), key=lambda x: int(x["_offset"]))


def _build_file_doc(file_name: str, args: argparse.Namespace, recs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": "silky_mes_story_text_v3_per_file",
        "encoding": args.encoding,
        "decoded_input": bool(args.decoded),
        "source_file": file_name,
        "items": recs,
    }


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _reorder_record_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Keep private locator fields first, then name, scr_msg, message.

    The resulting order is meant for review/editing and matches the project
    convention requested by the user.
    """
    ordered: dict[str, Any] = {}
    for k, v in record.items():
        if k in {"name", "scr_msg", "message", "msg"}:
            continue
        ordered[k] = v
    if "name" in record:
        ordered["name"] = record["name"]
    ordered["scr_msg"] = record.get("scr_msg", "")
    ordered["message"] = record.get("message", record.get("msg", record.get("scr_msg", "")))
    return ordered


def cmd_extract(args: argparse.Namespace) -> int:
    src = Path(args.input)
    out = Path(args.output)
    skip = set(args.skip_stem or DEFAULT_SKIP_STEMS)
    files = _iter_mes_files(src)
    if not args.include_non_story:
        files = [f for f in files if is_probably_story_mes(f, skip)]

    total_items = 0
    written_files = 0

    if src.is_file():
        # Single MES input: output may be either a JSON file path or a directory.
        if out.suffix.lower() == ".json":
            out_json = out
        else:
            out_json = out / f"{src.stem}.json"
        recs = extract_file(src, encoding=args.encoding, decoded=args.decoded, scan_messages=args.scan_all)
        recs = [_reorder_record_fields(r) for r in recs]
        _write_json(out_json, _build_file_doc(src.name, args, recs))
        print(f"wrote {len(recs)} items -> {out_json}")
        return 0

    # Directory input: batch decompile each MES into one JSON file.
    out.mkdir(parents=True, exist_ok=True)
    for f in files:
        recs = extract_file(f, encoding=args.encoding, decoded=args.decoded, scan_messages=args.scan_all)
        recs = [_reorder_record_fields(r) for r in recs]
        if not recs and not args.write_empty:
            continue
        out_json = out / f"{f.stem}.json"
        _write_json(out_json, _build_file_doc(f.name, args, recs))
        total_items += len(recs)
        written_files += 1

    print(f"wrote {total_items} items in {written_files} JSON files -> {out}")
    return 0

def cmd_choices(args: argparse.Namespace) -> int:
    path = Path(args.mes)
    data = decode_mes(path.read_bytes(), already_decoded=args.decoded)
    commands = iter_cfg_commands(data, args.encoding)
    tables = find_choice_tables(data, commands, args.encoding)
    serial = []
    for t in tables:
        serial.append({
            "define_offset": t.define_offset,
            "table_offset": t.table_rel,
            "count": t.count,
            "choices": [{
                "index": c.index,
                "condition_offset": c.cond_rel or None,
                "text_offset": c.text_rel,
                "msg_id": c.message.msg_id if c.message else None,
                "text": c.message.text if c.message else None,
            } for c in t.items],
        })
    print(json.dumps(serial, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Extract story text from current-game Silky MES files")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("extract")
    p.add_argument("input", help="MES file or directory")
    p.add_argument("output", help="output JSON directory for directory input, or JSON file/directory for single MES input")
    p.add_argument("--encoding", default=DEFAULT_ENCODING)
    p.add_argument("--decoded", action="store_true", help="input MES is already XOR-decoded")
    p.add_argument("--include-non-story", action="store_true", help="do not skip art/theater/title/jump/def/startup/etc.")
    p.add_argument("--skip-stem", action="append", help="additional stem to skip; may repeat")
    p.add_argument("--scan-all", action="store_true", help="enable supplementary whole-file message-record byte scan; off by default to avoid false positives")
    p.add_argument("--write-empty", action="store_true", help="write empty per-file JSON for story MES files with no extracted text")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("choices")
    p.add_argument("mes")
    p.add_argument("--encoding", default=DEFAULT_ENCODING)
    p.add_argument("--decoded", action="store_true")
    p.set_defaults(func=cmd_choices)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
