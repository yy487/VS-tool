#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScrPlayer SCR text extractor / non-equal-length injector.

Supported script format observed in ScrPlayer:
  header magic: b"SCR:2006"
  0x10: uint32 bytecode_size
  0x14: bytecode
  after bytecode: uint32 encrypted_text_size
  after that: text block encrypted by byte ^ 0x7F

Extracted JSON format:
[
  {"id": 0, "name": "", "message": "..."},
  {"id": 1, "name": "太田　昭次郎", "message": "..."}
]

Only dialogue/choice visible text is extracted:
  - op 0x5E: name_offset, voice_offset, msg_offset; voice is ignored.
  - op 0x64: choice_text_offset.
Resource names such as bg/tachi/voice/bgm/se are preserved but not exported.

Injection rebuilds the whole text block and patches known visible-text offsets in bytecode.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

MAGIC = b"SCR:2006"
NULL_OFF = 0xFFFFFFFF


@dataclass
class ScrFile:
    path: Path
    prefix: bytes          # 0x00..0x13, includes header and code_size
    code: bytearray
    text: bytearray        # decrypted text block
    suffix: bytes = b""    # normally empty, preserved if present


@dataclass
class TextRef:
    kind: str              # "msg" or "choice"
    code_pos: int
    id_in_code: int
    name_off: Optional[int]
    msg_off: int
    # locations of the 4-byte offsets inside bytecode, relative to code start
    name_ptr_pos: Optional[int]
    msg_ptr_pos: int


def decode_bytes(raw: bytes, encoding: str) -> str:
    # CP932 decodes half-width kana cleanly. Replacement keeps damaged/custom bytes visible.
    return raw.decode(encoding, errors="replace")


def encode_text(s: str, encoding: str) -> bytes:
    try:
        return s.encode(encoding)
    except UnicodeEncodeError as e:
        raise SystemExit(
            f"Encoding error: character {s[e.start:e.end]!r} cannot be encoded as {encoding}.\n"
            f"Use CP932-compatible text, or provide an engine-specific mapped byte encoding before injection."
        )


def read_u32(buf: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def write_u32(buf: bytearray, off: int, value: int) -> None:
    struct.pack_into("<I", buf, off, value & 0xFFFFFFFF)


def load_scr(path: Path) -> ScrFile:
    data = path.read_bytes()
    if len(data) < 0x18 or data[:8] != MAGIC:
        raise ValueError(f"{path}: not a supported SCR:2006 file")
    code_size = read_u32(data, 0x10)
    code_start = 0x14
    code_end = code_start + code_size
    if code_end + 4 > len(data):
        raise ValueError(f"{path}: invalid code_size 0x{code_size:X}")
    text_size = read_u32(data, code_end)
    text_start = code_end + 4
    text_end = text_start + text_size
    if text_end > len(data):
        raise ValueError(f"{path}: invalid text_size 0x{text_size:X}")
    enc_text = data[text_start:text_end]
    dec_text = bytearray(b ^ 0x7F for b in enc_text)
    return ScrFile(
        path=path,
        prefix=data[:code_start],
        code=bytearray(data[code_start:code_end]),
        text=dec_text,
        suffix=data[text_end:],
    )


def save_scr(scr: ScrFile, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = bytearray(scr.prefix)
    if len(prefix) < 0x14 or prefix[:8] != MAGIC:
        raise ValueError("bad SCR prefix")
    write_u32(prefix, 0x10, len(scr.code))
    enc_text = bytes(b ^ 0x7F for b in scr.text)
    out = bytes(prefix) + bytes(scr.code) + struct.pack("<I", len(enc_text)) + enc_text + scr.suffix
    out_path.write_bytes(out)


def collect_string_starts(text: bytes | bytearray) -> Dict[int, bytes]:
    """Return {offset: raw_bytes_until_NUL} for every NUL-separated string start."""
    starts: Dict[int, bytes] = {}
    pos = 0
    n = len(text)
    while pos < n:
        end = pos
        while end < n and text[end] != 0:
            end += 1
        starts[pos] = bytes(text[pos:end])
        pos = end + 1
    return starts


def is_valid_string_offset(off: int, starts: Dict[int, bytes]) -> bool:
    return off in starts


def scan_visible_refs(scr: ScrFile) -> List[TextRef]:
    """Scan confirmed visible text instructions only."""
    code = scr.code
    strings = collect_string_starts(scr.text)
    refs: List[TextRef] = []
    n = len(code)
    i = 0
    while i < n:
        op = code[i]
        # Dialogue/message instruction observed as:
        # 5E 14 line_lo line_hi name_off:u32 voice_off:u32 msg_off:u32
        if op == 0x5E and i + 16 <= n and code[i + 1] == 0x14:
            line_id = code[i + 2] | (code[i + 3] << 8)
            name_off = read_u32(code, i + 4)
            msg_off = read_u32(code, i + 12)
            name_ok = (name_off == NULL_OFF) or is_valid_string_offset(name_off, strings)
            msg_ok = is_valid_string_offset(msg_off, strings)
            if name_ok and msg_ok:
                refs.append(TextRef(
                    kind="msg",
                    code_pos=i,
                    id_in_code=line_id,
                    name_off=None if name_off == NULL_OFF else name_off,
                    msg_off=msg_off,
                    name_ptr_pos=None if name_off == NULL_OFF else i + 4,
                    msg_ptr_pos=i + 12,
                ))
                i += 16
                continue
        # Choice item instruction observed as:
        # 64 0C 00 00 choice_id:u32 choice_text_off:u32
        if op == 0x64 and i + 12 <= n and code[i + 1] == 0x0C and code[i + 2] == 0 and code[i + 3] == 0:
            choice_id = read_u32(code, i + 4)
            msg_off = read_u32(code, i + 8)
            if is_valid_string_offset(msg_off, strings):
                refs.append(TextRef(
                    kind="choice",
                    code_pos=i,
                    id_in_code=choice_id,
                    name_off=None,
                    msg_off=msg_off,
                    name_ptr_pos=None,
                    msg_ptr_pos=i + 8,
                ))
                i += 12
                continue
        i += 1
    return refs


def extract_one(scr_path: Path, json_path: Path, encoding: str, include_empty: bool = False) -> int:
    scr = load_scr(scr_path)
    strings = collect_string_starts(scr.text)
    refs = scan_visible_refs(scr)
    rows = []
    next_id = 0
    for ref in refs:
        name = ""
        if ref.name_off is not None:
            name = decode_bytes(strings[ref.name_off], encoding)
        msg = decode_bytes(strings[ref.msg_off], encoding)
        if not include_empty and name == "" and msg == "":
            continue
        rows.append({"id": next_id, "name": name, "message": msg})
        next_id += 1
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(rows)


def build_replacement_map(scr: ScrFile, rows: List[dict], encoding: str) -> Dict[int, bytes]:
    strings = collect_string_starts(scr.text)
    refs = scan_visible_refs(scr)
    if len(rows) > len(refs):
        raise ValueError(f"JSON has {len(rows)} rows, but SCR only has {len(refs)} visible text refs")
    repl: Dict[int, bytes] = {}
    conflicts: List[str] = []
    for idx, row in enumerate(rows):
        ref = refs[idx]
        # name
        if ref.name_off is not None and "name" in row:
            new_name = encode_text(str(row.get("name", "")), encoding)
            old = repl.get(ref.name_off)
            if old is not None and old != new_name:
                conflicts.append(f"row {idx}: name offset 0x{ref.name_off:X} has conflicting replacements")
            else:
                repl[ref.name_off] = new_name
        # message / choice
        if "message" in row:
            new_msg = encode_text(str(row.get("message", "")), encoding)
            old = repl.get(ref.msg_off)
            if old is not None and old != new_msg:
                conflicts.append(f"row {idx}: message offset 0x{ref.msg_off:X} has conflicting replacements")
            else:
                repl[ref.msg_off] = new_msg
    if conflicts:
        raise ValueError("Conflicting replacements:\n" + "\n".join(conflicts[:20]))
    return repl


def rebuild_text_and_patch(scr: ScrFile, replacements: Dict[int, bytes]) -> None:
    old_strings = collect_string_starts(scr.text)
    starts_sorted = sorted(old_strings.keys())
    old_to_new: Dict[int, int] = {}
    new_text = bytearray()
    for old_off in starts_sorted:
        old_to_new[old_off] = len(new_text)
        raw = replacements.get(old_off, old_strings[old_off])
        new_text += raw + b"\x00"
    refs = scan_visible_refs(scr)
    for ref in refs:
        if ref.name_ptr_pos is not None and ref.name_off is not None:
            write_u32(scr.code, ref.name_ptr_pos, old_to_new[ref.name_off])
        write_u32(scr.code, ref.msg_ptr_pos, old_to_new[ref.msg_off])
    scr.text = new_text


def inject_one(scr_path: Path, json_path: Path, out_path: Path, encoding: str, backup: bool = False) -> int:
    scr = load_scr(scr_path)
    rows = json.loads(json_path.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list):
        raise ValueError(f"{json_path}: expected top-level JSON list")
    replacements = build_replacement_map(scr, rows, encoding)
    rebuild_text_and_patch(scr, replacements)
    if backup and out_path.exists():
        shutil.copy2(out_path, out_path.with_suffix(out_path.suffix + ".bak"))
    save_scr(scr, out_path)
    return len(replacements)


def iter_scr_files(path: Path, recursive: bool) -> Iterable[Path]:
    if path.is_file():
        yield path
    elif recursive:
        yield from sorted(path.rglob("*.scr"))
    else:
        yield from sorted(path.glob("*.scr"))


def rel_json_path(scr_file: Path, in_root: Path, out_root: Path) -> Path:
    if in_root.is_file():
        return out_root
    rel = scr_file.relative_to(in_root)
    return (out_root / rel).with_suffix(".json")


def rel_out_scr_path(scr_file: Path, in_root: Path, out_root: Path) -> Path:
    if in_root.is_file():
        return out_root
    return out_root / scr_file.relative_to(in_root)


def cmd_extract(args: argparse.Namespace) -> None:
    src = Path(args.input)
    out = Path(args.output)
    total_files = total_rows = 0
    for scr_file in iter_scr_files(src, args.recursive):
        json_path = rel_json_path(scr_file, src, out)
        try:
            count = extract_one(scr_file, json_path, args.encoding, args.include_empty)
        except Exception as e:
            print(f"[ERR] {scr_file}: {e}", file=sys.stderr)
            if not args.keep_going:
                raise
            continue
        print(f"[OK] extract {scr_file} -> {json_path} ({count} rows)")
        total_files += 1
        total_rows += count
    print(f"Done. files={total_files}, rows={total_rows}")


def cmd_inject(args: argparse.Namespace) -> None:
    src = Path(args.input)
    json_src = Path(args.json)
    out = Path(args.output)
    total_files = total_repls = 0
    for scr_file in iter_scr_files(src, args.recursive):
        if src.is_file():
            json_path = json_src
        else:
            json_path = (json_src / scr_file.relative_to(src)).with_suffix(".json")
        out_path = rel_out_scr_path(scr_file, src, out)
        if not json_path.exists():
            if args.skip_missing:
                print(f"[SKIP] missing json: {json_path}")
                continue
            raise FileNotFoundError(json_path)
        try:
            count = inject_one(scr_file, json_path, out_path, args.encoding, args.backup)
        except Exception as e:
            print(f"[ERR] {scr_file}: {e}", file=sys.stderr)
            if not args.keep_going:
                raise
            continue
        print(f"[OK] inject {json_path} -> {out_path} ({count} text strings changed/reused)")
        total_files += 1
        total_repls += count
    print(f"Done. files={total_files}, replaced_offsets={total_repls}")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ScrPlayer .scr visible text extractor/injector")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ep = sub.add_parser("extract", help="extract dialogue/choice text to JSON")
    ep.add_argument("input", help="input .scr file or directory")
    ep.add_argument("output", help="output .json file or directory")
    ep.add_argument("-r", "--recursive", action="store_true", help="process directory recursively")
    ep.add_argument("--encoding", default="cp932", help="text encoding, default: cp932")
    ep.add_argument("--include-empty", action="store_true", help="include empty visible text rows")
    ep.add_argument("--keep-going", action="store_true", help="continue after errors in batch mode")
    ep.set_defaults(func=cmd_extract)

    ip = sub.add_parser("inject", help="inject edited JSON back into .scr, allowing non-equal length")
    ip.add_argument("input", help="input original .scr file or directory")
    ip.add_argument("json", help="edited .json file or json directory")
    ip.add_argument("output", help="output .scr file or directory")
    ip.add_argument("-r", "--recursive", action="store_true", help="process directory recursively")
    ip.add_argument("--encoding", default="cp932", help="text encoding for injected text, default: cp932")
    ip.add_argument("--skip-missing", action="store_true", help="skip .scr files without matching json")
    ip.add_argument("--backup", action="store_true", help="backup existing output files")
    ip.add_argument("--keep-going", action="store_true", help="continue after errors in batch mode")
    ip.set_defaults(func=cmd_inject)

    args = ap.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
