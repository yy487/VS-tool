#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Non-equal-length injector for Angel/Silky EVIT .snc scripts.

Injection strategy:
  1. Parse old string pool.
  2. Apply JSON message/choice replacements by _ref with scr_msg verification.
  3. Rebuild the whole string pool.
  4. Rewrite all st references in VM code.
  5. Update vl_base / ef_base / code_start / file_size.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

from snc_common import (
    clean_msg, collect_strings, join_name_msg, load_snc, normalize_extracted_message, read_json,
    rebuild_with_new_strings, split_name_msg,
)


def _iter_json_paths(input_json: Path) -> List[Path]:
    if input_json.is_dir():
        return sorted(input_json.glob("*.json"))
    return [input_json]


def _json_for_snc(json_root: Path, snc_path: Path) -> Path:
    if json_root.is_dir():
        return json_root / f"{snc_path.stem}.json"
    return json_root


def _entry_replacement(entry: dict) -> Tuple[int, str, str, str]:
    """Return ref, normalized expected_scr_msg, new_full_text, kind."""
    ref = entry.get("_ref", entry.get("_string_ref"))
    if ref is None:
        raise KeyError("entry has no _ref")
    ref = int(ref)
    name = entry.get("name")
    # scr_msg 和 message 在新版流程中都不保存正文内部 \n。
    scr_msg = normalize_extracted_message(entry.get("scr_msg", ""))
    message = normalize_extracted_message(entry.get("message", entry.get("msg", scr_msg)))
    # join_name_msg 会自动补回 name 与 message 之间的唯一 \n。
    new_full = join_name_msg(name, message)
    return ref, scr_msg, new_full, entry.get("_kind", "message")


def build_replacements(
    snc_path: Path,
    json_path: Path,
    *,
    encoding: str = "cp932",
    strict_scr_msg: bool = True,
) -> Tuple[Dict[int, str], List[str]]:
    data, h, _words = load_snc(snc_path)
    old_strings = collect_strings(data, h, encoding)
    doc = read_json(json_path)
    if not isinstance(doc, list):
        raise ValueError(f"{json_path}: expected a JSON list")

    replacements: Dict[int, str] = {}
    warnings: List[str] = []

    for ent in doc:
        if not isinstance(ent, dict):
            continue
        kind = ent.get("_kind", "message")
        if kind == "choice":
            for ch in ent.get("choices", []):
                if not isinstance(ch, dict):
                    continue
                ref = ch.get("_ref", ch.get("_string_ref"))
                if ref is None:
                    warnings.append(f"choice at index {ent.get('_index')} missing _ref")
                    continue
                ref = int(ref)
                old = normalize_extracted_message(old_strings.get(ref, ""))
                scr = normalize_extracted_message(ch.get("scr_msg", ""))
                msg = normalize_extracted_message(ch.get("message", ch.get("msg", scr)))
                if strict_scr_msg and old != scr:
                    warnings.append(f"choice ref {ref}: scr_msg mismatch: old={old!r}, json={scr!r}; skipped")
                    continue
                replacements[ref] = msg
            continue

        # Ignore map markers and other non-text metadata entries.
        if kind not in ("message", "message_fallback") and "message" not in ent and "msg" not in ent:
            continue

        try:
            ref, scr, new_full, _kind = _entry_replacement(ent)
        except KeyError:
            warnings.append(f"entry index {ent.get('_index')} missing _ref; skipped")
            continue
        old_full = clean_msg(old_strings.get(ref, ""))
        old_name, old_msg = split_name_msg(old_full, allow_name=(ent.get("name") is not None), known_speakers=None)
        old_scr = normalize_extracted_message(old_msg)
        if strict_scr_msg and old_scr != scr:
            warnings.append(f"ref {ref}: scr_msg mismatch: old={old_scr!r}, json={scr!r}; skipped")
            continue
        # name 是目标译名，不再作为原文校验字段。
        # 原始 name 与 message 之间的 \n 会在 join_name_msg 中自动补回。
        replacements[ref] = new_full

    return replacements, warnings


def inject_one(
    snc_path: Path,
    json_path: Path,
    out_path: Path,
    *,
    encoding: str = "cp932",
    errors: str = "strict",
    strict_scr_msg: bool = True,
    dry_run: bool = False,
) -> dict:
    data, h, _words = load_snc(snc_path)
    replacements, warnings = build_replacements(
        snc_path, json_path, encoding=encoding, strict_scr_msg=strict_scr_msg
    )
    if dry_run:
        return {
            "file": snc_path.name,
            "json": json_path.name,
            "replacements": len(replacements),
            "warnings": warnings,
            "dry_run": True,
        }
    new_data, ref_map = rebuild_with_new_strings(
        data, h, replacements, encoding=encoding, errors=errors
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(new_data)
    return {
        "file": snc_path.name,
        "json": json_path.name,
        "output": str(out_path),
        "replacements": len(replacements),
        "old_size": len(data),
        "new_size": len(new_data),
        "delta": len(new_data) - len(data),
        "warnings": warnings,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Inject translated text into Angel/Silky EVIT .snc scripts")
    ap.add_argument("input", type=Path, help="input .snc file or directory")
    ap.add_argument("json", type=Path, help="translation .json file or directory")
    ap.add_argument("output", type=Path, help="output .snc file or directory")
    ap.add_argument("--encoding", default="cp932")
    ap.add_argument("--errors", default="strict", choices=["strict", "replace", "ignore"], help="encoding error policy")
    ap.add_argument("--no-strict-scr-msg", action="store_true", help="do not require normalized scr_msg to match original text")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    strict = not args.no_strict_scr_msg
    results = []
    if args.input.is_dir():
        args.output.mkdir(parents=True, exist_ok=True)
        files = sorted(args.input.glob("*.snc"))
        for p in files:
            jp = _json_for_snc(args.json, p)
            if not jp.exists():
                results.append({"file": p.name, "skipped": True, "reason": f"missing json: {jp}"})
                continue
            out = args.output / p.name
            try:
                results.append(inject_one(
                    p, jp, out, encoding=args.encoding, errors=args.errors,
                    strict_scr_msg=strict, dry_run=args.dry_run,
                ))
            except Exception as e:
                results.append({"file": p.name, "error": str(e)})
    else:
        out = args.output
        if args.output.is_dir():
            out = args.output / args.input.name
        results.append(inject_one(
            args.input, args.json, out, encoding=args.encoding, errors=args.errors,
            strict_scr_msg=strict, dry_run=args.dry_run,
        ))

    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
