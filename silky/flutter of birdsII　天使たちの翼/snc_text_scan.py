#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Angel/Silky SNC text scanner.

Reads engine-decompressed .snc files whose header starts with EVIT.
Outputs JSON entries with optional name, scr_msg, msg and markers for choices/map calls.
"""
from __future__ import annotations

import argparse, json, struct
from pathlib import Path

ST = 0x7473  # 'st'
RN = 0x6E72  # 'rn'
HN = 0x6E68  # 'hn'
VL = 0x766C  # 'vl'
EF = 0x6665  # 'ef'
MAGIC = b"EVIT"

# opcodes that commonly consume one or more string arguments and display/use text.
# 0x30/0x34 are message display ops; 0x81 is choice menu; 0x80/0x88 enter map mode.
MESSAGE_OPS = {0x30, 0x34, 0x45, 0x95, 0x96}
CHOICE_OP = 0x81
MAP_OPS = {0x80, 0x88}
RESOURCE_HINT_PREFIXES = (
    "HBG", "HH", "HSE", "V", "stand", "face", "bgm", "gray", "white", "map", "frame"
)


def read_cstr(data: bytes, off: int, limit: int) -> bytes:
    end = data.find(b"\0", off, limit)
    if end < 0:
        end = limit
    return data[off:end]


def decode_sjis(b: bytes) -> str:
    return b.decode("cp932", errors="replace")


def parse_snc(path: Path):
    data = path.read_bytes()
    if data[:4] != MAGIC:
        raise ValueError(f"{path}: not an engine-decompressed SNC/EVIT file")
    if len(data) < 28:
        raise ValueError(f"{path}: too small")
    h = struct.unpack_from("<7I", data, 0)
    str_base, label_a, label_b, code_start, file_size, var_count = h[1:7]
    usable = min(file_size, len(data))
    words = list(struct.unpack("<%dH" % (usable // 2), data[:usable // 2 * 2]))
    return data, h, words


def collect_strings(data: bytes, h) -> dict[int, str]:
    str_base, code_start = h[1], h[4]
    start, end = str_base * 2, min(code_start * 2, len(data))
    out: dict[int, str] = {}
    p = start
    while p < end:
        q = data.find(b"\0", p, end)
        if q < 0:
            break
        if q > p:
            rel = p // 2 - str_base
            out[rel] = decode_sjis(data[p:q])
        p = q + 1
        # Engine addresses strings by word index: (rel + str_base) * 2.
        # Compiler normally pads strings to even address; resync if needed.
        if p & 1:
            p += 1
    return out


def clean_msg(s: str) -> str:
    # Script strings often end with one ASCII space as display padding.
    return s.rstrip(" ")


def split_name_msg(s: str):
    s = clean_msg(s)
    if "\\n" in s:
        first, rest = s.split("\\n", 1)
        if first and len(first) <= 16 and "「" not in first and "」" not in first:
            return first, rest
    return None, s


def is_likely_resource(s: str) -> bool:
    t = s.strip()
    if not t:
        return True
    if t.startswith(RESOURCE_HINT_PREFIXES):
        return True
    # voice/image/resource IDs are usually compact ASCII+digits, e.g. VHB0641, face0218.
    if t.isascii() and len(t) <= 16 and not any(ch in t for ch in " .,!?;:'\"-"):
        return True
    return False


def get_st_string(words, i, strings):
    if i + 1 < len(words) and words[i] == ST:
        return words[i + 1], strings.get(words[i + 1])
    return None, None


def scan(path: Path):
    data, h, words = parse_snc(path)
    strings = collect_strings(data, h)
    code_start = h[4]
    entries = []
    used_text_refs = set()
    i = code_start
    order = 0
    while i < len(words):
        op = words[i]
        # Choice: 0x81 [hn var] st choice0 st choice1 ... 0x0000, then branch code.
        if op == CHOICE_OP:
            j = i + 1
            var_id = None
            # Real selectable menus observed in this engine are:
            #   0x81 hn <result_var> st <choice0> st <choice1> ... 0
            # There are unrelated 0x81-like byte patterns later in branch bodies,
            # so require the hn marker to avoid false choice blocks.
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
                    if text is not None:
                        choices.append({"index": len(choices), "scr_msg": clean_msg(text), "msg": clean_msg(text), "_string_ref": ref})
                        used_text_refs.add(ref)
                    j += 2
                else:
                    j += 1
            if choices:
                entries.append({"_file": path.name, "_index": order, "_kind": "choice", "_offset_word": i, "_var": var_id, "choices": choices})
                order += 1
            i = max(j + 1, i + 1)
            continue

        # Map mode marker.
        if op in MAP_OPS:
            entries.append({"_file": path.name, "_index": order, "_kind": "map_jump_mode", "_offset_word": i, "_opcode": f"0x{op:02X}"})
            order += 1
            i += 1
            continue

        # Message-like ops: collect st arguments immediately following the opcode sequence.
        if op in MESSAGE_OPS:
            j = i + 1
            local_strings = []
            # skip common rn/hn parameters before strings
            guard = 0
            while j < len(words) and guard < 8:
                guard += 1
                if words[j] in (RN, HN, VL, EF) and j + 1 < len(words):
                    j += 2
                    continue
                if words[j] == ST and j + 1 < len(words):
                    ref = words[j + 1]
                    text = strings.get(ref)
                    if text is not None:
                        local_strings.append((ref, text))
                    j += 2
                    continue
                break
            for ref, text in local_strings:
                if ref in used_text_refs or is_likely_resource(text):
                    continue
                name, msg = split_name_msg(text)
                ent = {"_file": path.name, "_index": order, "scr_msg": msg, "msg": msg, "_offset_word": i, "_string_ref": ref}
                if name:
                    ent = {"_file": path.name, "_index": order, "name": name, "scr_msg": msg, "msg": msg, "_offset_word": i, "_string_ref": ref}
                entries.append(ent)
                used_text_refs.add(ref)
                order += 1
            i = max(j, i + 1)
            continue
        i += 1

    # Fallback: include unreferenced narrative/dialogue-looking strings, useful while opcode coverage is incomplete.
    for ref, text in sorted(strings.items()):
        if ref in used_text_refs or is_likely_resource(text):
            continue
        t = clean_msg(text)
        if not t:
            continue
        if any(ch in t for ch in "。「」？！……") or "\\n" in t:
            name, msg = split_name_msg(t)
            ent = {"_file": path.name, "_index": order, "scr_msg": msg, "msg": msg, "_string_ref": ref, "_kind": "string_pool_fallback"}
            if name:
                ent = {"_file": path.name, "_index": order, "name": name, "scr_msg": msg, "msg": msg, "_string_ref": ref, "_kind": "string_pool_fallback"}
            entries.append(ent)
            order += 1
    return entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path, help="EVIT .snc file or directory")
    ap.add_argument("output", type=Path, help="output JSON file or directory")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()
    if args.input.is_dir():
        args.output.mkdir(parents=True, exist_ok=True)
        total = 0
        for p in sorted(args.input.glob("*.snc")):
            ents = scan(p)
            total += len(ents)
            (args.output / (p.stem + ".json")).write_text(json.dumps(ents, ensure_ascii=False, indent=2 if args.pretty else None), encoding="utf-8")
        print(json.dumps({"files": len(list(args.input.glob('*.snc'))), "entries": total}, ensure_ascii=False))
    else:
        ents = scan(args.input)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(ents, ensure_ascii=False, indent=2 if args.pretty else None), encoding="utf-8")
        print(json.dumps({"file": args.input.name, "entries": len(ents)}, ensure_ascii=False))

if __name__ == "__main__":
    main()
