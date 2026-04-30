#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScrPlayer .scr decryptor / text dumper

Usage:
  python scr_decryptor.py 0_2_prologue.scr
  python scr_decryptor.py 0_2_prologue.scr -o out_dir
  python scr_decryptor.py unpacked_dir -o out_dir --recursive

Output per script:
  <name>.meta.txt        header / size info
  <name>.bytecode.bin    raw bytecode block
  <name>.text.dec.bin    decrypted text block bytes
  <name>.strings.txt     NUL-split strings decoded with CP932
  <name>.strings.tsv     index, offset, byte length, decoded string

File format assumed from ScrPlayer SCR:2006:
  0x00  8 bytes  magic, usually b"SCR:2006"
  0x08  8 bytes  script id/name-ish field
  0x10  u32le    bytecode size
  0x14  N bytes  bytecode
  ...   u32le    encrypted text block size
  ...   M bytes  text block, each byte XOR 0x7F
"""

from __future__ import annotations

import argparse
import csv
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


XOR_KEY = 0x7F
DEFAULT_ENCODING = "cp932"


@dataclass
class ScrFile:
    path: Path
    magic: bytes
    ident: bytes
    code_size: int
    bytecode: bytes
    text_size: int
    text_encrypted: bytes
    text_decrypted: bytes


def xor_7f(data: bytes) -> bytes:
    return bytes(b ^ XOR_KEY for b in data)


def parse_scr(path: Path) -> ScrFile:
    raw = path.read_bytes()
    if len(raw) < 0x18:
        raise ValueError(f"{path}: file too small: {len(raw)} bytes")

    magic = raw[0:8]
    if not magic.startswith(b"SCR:"):
        raise ValueError(f"{path}: unexpected magic {magic!r}")

    ident = raw[8:16]
    code_size = struct.unpack_from("<I", raw, 0x10)[0]

    code_off = 0x14
    code_end = code_off + code_size
    if code_end + 4 > len(raw):
        raise ValueError(
            f"{path}: invalid code_size=0x{code_size:X}, file_size=0x{len(raw):X}"
        )

    bytecode = raw[code_off:code_end]
    text_size = struct.unpack_from("<I", raw, code_end)[0]
    text_off = code_end + 4
    text_end = text_off + text_size

    if text_end > len(raw):
        raise ValueError(
            f"{path}: invalid text_size=0x{text_size:X}, "
            f"text_off=0x{text_off:X}, file_size=0x{len(raw):X}"
        )

    text_encrypted = raw[text_off:text_end]
    text_decrypted = xor_7f(text_encrypted)

    return ScrFile(
        path=path,
        magic=magic,
        ident=ident,
        code_size=code_size,
        bytecode=bytecode,
        text_size=text_size,
        text_encrypted=text_encrypted,
        text_decrypted=text_decrypted,
    )


def iter_strings(block: bytes, encoding: str = DEFAULT_ENCODING):
    """
    Yield (index, offset, byte_length, decoded_text, raw_bytes).
    Empty segments are skipped, but their offsets remain accurate.
    """
    pos = 0
    idx = 0
    for part in block.split(b"\x00"):
        off = pos
        pos += len(part) + 1
        if not part:
            continue
        text = part.decode(encoding, errors="replace")
        yield idx, off, len(part), text, part
        idx += 1


def safe_ident_text(ident: bytes) -> str:
    # Ident often contains NUL padding or non-text bytes.
    stripped = ident.rstrip(b"\x00")
    try:
        return stripped.decode(DEFAULT_ENCODING, errors="replace")
    except Exception:
        return repr(stripped)


def dump_scr(scr: ScrFile, out_dir: Path, encoding: str = DEFAULT_ENCODING) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = scr.path.stem

    (out_dir / f"{stem}.bytecode.bin").write_bytes(scr.bytecode)
    (out_dir / f"{stem}.text.dec.bin").write_bytes(scr.text_decrypted)

    meta = []
    meta.append(f"path\t{scr.path}")
    meta.append(f"file_size\t0x{scr.path.stat().st_size:X} ({scr.path.stat().st_size})")
    meta.append(f"magic\t{scr.magic!r}")
    meta.append(f"ident_raw\t{scr.ident.hex(' ')}")
    meta.append(f"ident_text\t{safe_ident_text(scr.ident)}")
    meta.append(f"code_offset\t0x14")
    meta.append(f"code_size\t0x{scr.code_size:X} ({scr.code_size})")
    meta.append(f"text_size_field_offset\t0x{0x14 + scr.code_size:X}")
    meta.append(f"text_offset\t0x{0x14 + scr.code_size + 4:X}")
    meta.append(f"text_size\t0x{scr.text_size:X} ({scr.text_size})")
    meta.append(f"text_xor_key\t0x{XOR_KEY:02X}")
    (out_dir / f"{stem}.meta.txt").write_text("\n".join(meta) + "\n", encoding="utf-8")

    strings = list(iter_strings(scr.text_decrypted, encoding=encoding))

    with (out_dir / f"{stem}.strings.txt").open("w", encoding="utf-8", newline="\n") as f:
        for idx, off, blen, text, raw in strings:
            f.write(f"#{idx:04d} @0x{off:08X} len={blen}\n")
            f.write(text)
            f.write("\n\n")

    with (out_dir / f"{stem}.strings.tsv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["index", "offset_hex", "offset_dec", "byte_len", "text"])
        for idx, off, blen, text, raw in strings:
            w.writerow([idx, f"0x{off:X}", off, blen, text])


def collect_inputs(path: Path, recursive: bool) -> Iterable[Path]:
    if path.is_file():
        yield path
    elif path.is_dir():
        pattern = "**/*.scr" if recursive else "*.scr"
        yield from sorted(path.glob(pattern))
    else:
        raise FileNotFoundError(path)


def main() -> int:
    ap = argparse.ArgumentParser(description="Decrypt and dump ScrPlayer .scr files.")
    ap.add_argument("input", type=Path, help=".scr file or directory containing .scr files")
    ap.add_argument("-o", "--out", type=Path, default=Path("scr_dump"), help="output directory")
    ap.add_argument("-e", "--encoding", default=DEFAULT_ENCODING, help="text encoding, default cp932")
    ap.add_argument("-r", "--recursive", action="store_true", help="recurse when input is a directory")
    args = ap.parse_args()

    paths = list(collect_inputs(args.input, args.recursive))
    if not paths:
        print(f"No .scr files found in {args.input}")
        return 1

    ok = 0
    failed = 0
    for path in paths:
        try:
            scr = parse_scr(path)
            # Preserve relative-like structure only by filename for now.
            dump_scr(scr, args.out, encoding=args.encoding)
            print(
                f"[OK] {path.name}: code=0x{scr.code_size:X}, "
                f"text=0x{scr.text_size:X}, strings={sum(1 for _ in iter_strings(scr.text_decrypted, args.encoding))}"
            )
            ok += 1
        except Exception as e:
            print(f"[FAIL] {path}: {e}")
            failed += 1

    print(f"Done. ok={ok}, failed={failed}, out={args.out}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
