#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract translatable text from Angel/Silky EVIT .snc scripts.

Output entry format uses optional name, scr_msg and message:

  {
    "name": "美蔓",
    "scr_msg": "「進矢君、\\n悪いんだけどそろそろ起きてちょうだい」",
    "message": "「進矢君、\\n悪いんだけどそろそろ起きてちょうだい」"
  }
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Set, Tuple

from snc_common import (
    CHOICE_OP, EF, HN, MAP_OPS, MESSAGE_OPS, RN, ST, VL,
    clean_msg, collect_strings, is_likely_resource, load_snc, split_name_msg,
    write_json,
)


def _message_entry(path: Path, order: int, ref: int, text: str, code_word=None, kind="message") -> dict:
    name, msg = split_name_msg(text)
    ent = {
        "_file": path.name,
        "_index": order,
        "_kind": kind,
        "_ref": ref,
        "scr_msg": msg,
        "message": msg,
    }
    if code_word is not None:
        ent["_code_word"] = code_word
    if name:
        # Put name before scr_msg/message for readability.
        ent = {
            "_file": path.name,
            "_index": order,
            "_kind": kind,
            "_ref": ref,
            **({"_code_word": code_word} if code_word is not None else {}),
            "name": name,
            "scr_msg": msg,
            "message": msg,
        }
    return ent


def scan_snc(path: Path, *, encoding: str = "cp932", fallback: bool = False, include_map: bool = True) -> List[dict]:
    data, h, words = load_snc(path)
    strings = collect_strings(data, h, encoding)
    entries: List[dict] = []
    used_refs: Set[int] = set()
    order = 0
    i = h.code_start

    while i < len(words):
        op = words[i]

        # Ordinary choice menu:
        #   81 hn <var> st <choice0> st <choice1> ... 0000
        if op == CHOICE_OP:
            j = i + 1
            if not (j + 1 < len(words) and words[j] == HN):
                i += 1
                continue
            var_id = words[j + 1]
            j += 2
            choices = []
            while j < len(words) and words[j] != 0:
                if words[j] == ST and j + 1 < len(words):
                    ref = words[j + 1]
                    text = strings.get(ref)
                    if text is not None and not is_likely_resource(text):
                        msg = clean_msg(text)
                        choices.append({
                            "index": len(choices),
                            "_ref": ref,
                            "scr_msg": msg,
                            "message": msg,
                        })
                        used_refs.add(ref)
                    j += 2
                else:
                    j += 1
            if choices:
                entries.append({
                    "_file": path.name,
                    "_index": order,
                    "_kind": "choice",
                    "_code_word": i,
                    "_var": var_id,
                    "choices": choices,
                })
                order += 1
            i = max(j + 1, i + 1)
            continue

        if include_map and op in MAP_OPS:
            entries.append({
                "_file": path.name,
                "_index": order,
                "_kind": "map_jump_mode",
                "_code_word": i,
                "_opcode": f"0x{op:02X}",
            })
            order += 1
            i += 1
            continue

        # Message-like ops. They may have rn/hn/vl/ef parameters before st.
        if op in MESSAGE_OPS:
            j = i + 1
            local: List[Tuple[int, str]] = []
            guard = 0
            while j < len(words) and guard < 10:
                guard += 1
                if words[j] in (RN, HN, VL, EF) and j + 1 < len(words):
                    j += 2
                    continue
                if words[j] == ST and j + 1 < len(words):
                    ref = words[j + 1]
                    text = strings.get(ref)
                    if text is not None:
                        local.append((ref, clean_msg(text)))
                    j += 2
                    continue
                break
            for ref, text in local:
                if ref in used_refs or is_likely_resource(text):
                    continue
                entries.append(_message_entry(path, order, ref, text, i, "message"))
                used_refs.add(ref)
                order += 1
            i = max(j, i + 1)
            continue

        i += 1

    if fallback:
        for ref, text in sorted(strings.items()):
            if ref in used_refs:
                continue
            t = clean_msg(text)
            if is_likely_resource(t):
                continue
            # Keep dialogue/narration-looking unreferenced strings to avoid missed text
            # while opcode coverage is still being expanded.
            if "\\n" in t or any(ch in t for ch in "。「」『』？！……"):
                entries.append(_message_entry(path, order, ref, t, None, "message_fallback"))
                order += 1
    return entries


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract text from Angel/Silky EVIT .snc scripts")
    ap.add_argument("input", type=Path, help="input .snc file or directory")
    ap.add_argument("output", type=Path, help="output .json file or directory")
    ap.add_argument("--encoding", default="cp932")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--fallback", action="store_true", help="also scan unreferenced string-pool candidates; may over-extract and is not recommended for localization")
    ap.add_argument("--no-map-markers", action="store_true")
    args = ap.parse_args()

    fallback = args.fallback
    include_map = not args.no_map_markers
    if args.input.is_dir():
        args.output.mkdir(parents=True, exist_ok=True)
        files = sorted(args.input.glob("*.snc"))
        total = 0
        for p in files:
            ents = scan_snc(p, encoding=args.encoding, fallback=fallback, include_map=include_map)
            total += len(ents)
            write_json(args.output / f"{p.stem}.json", ents, pretty=args.pretty)
        print({"files": len(files), "entries": total})
    else:
        ents = scan_snc(args.input, encoding=args.encoding, fallback=fallback, include_map=include_map)
        write_json(args.output, ents, pretty=args.pretty)
        print({"file": args.input.name, "entries": len(ents)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
