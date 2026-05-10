#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI5WIN G24 <-> PNG tools.

Format confirmed from Ai5win_bak.exe.c:
    header: 4 x little-endian int16 = x, y, width, height
    body:   LZSS stream
    pixel:  24-bit BGR, rows stored in bottom-up DIB order, row stride 4-byte aligned

Commands:
    python ai5win_g24_tools.py g24topng input.g24 output.png
    python ai5win_g24_tools.py g24topng g24_dir png_dir
    python ai5win_g24_tools.py png2g24 input.png output.g24 [--ref-g24 old.g24]
    python ai5win_g24_tools.py png2g24 png_dir g24_dir [--ref-g24 old_g24_dir]
    python ai5win_g24_tools.py roundtrip input.g24 out_dir
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
from typing import Iterable, Optional, Tuple

from PIL import Image

N = 4096
F = 18
THRESHOLD = 2
INIT_POS = 0xFEE


def lzss_decompress(src: bytes, expected_size: Optional[int] = None) -> bytes:
    """Decode the AI5WIN/Okumura-style LZSS stream used by G24/MES resources."""
    text_buf = bytearray(N)
    r = INIT_POS
    flags = 0
    ip = 0
    out = bytearray()

    while ip < len(src):
        flags >>= 1
        if (flags & 0x100) == 0:
            if ip >= len(src):
                break
            flags = src[ip] | 0xFF00
            ip += 1

        if flags & 1:
            if ip >= len(src):
                break
            c = src[ip]
            ip += 1
            out.append(c)
            text_buf[r] = c
            r = (r + 1) & 0xFFF
        else:
            if ip + 1 >= len(src):
                break
            i = src[ip]
            j = src[ip + 1]
            ip += 2
            pos = i | ((j & 0xF0) << 4)
            length = (j & 0x0F) + 3
            for k in range(length):
                c = text_buf[(pos + k) & 0xFFF]
                out.append(c)
                text_buf[r] = c
                r = (r + 1) & 0xFFF
                if expected_size is not None and len(out) >= expected_size:
                    return bytes(out[:expected_size])

        if expected_size is not None and len(out) >= expected_size:
            return bytes(out[:expected_size])

    if expected_size is not None and len(out) < expected_size:
        raise ValueError(f"LZSS output too short: got {len(out)}, need {expected_size}")
    return bytes(out)



def lzss_compress_literal(data: bytes) -> bytes:
    """Literal-only stream. Fast and valid, but about 12.5% larger than raw data."""
    out = bytearray()
    pos = 0
    n = len(data)
    while pos < n:
        chunk = data[pos:pos + 8]
        out.append((1 << len(chunk)) - 1)
        out.extend(chunk)
        pos += len(chunk)
    return bytes(out)


def lzss_compress(data: bytes, max_candidates: int = 128) -> bytes:
    """Greedy AI5WIN/Okumura LZSS encoder.

    The engine decoder uses a 4096-byte ring buffer, starts at 0xFEE, stores
    literal items when the flag bit is 1, and stores two-byte back-references
    when the flag bit is 0. This encoder keeps the same format and usually
    produces sizes close to the original G24 assets.
    """
    from collections import defaultdict, deque

    data = bytes(data)
    n = len(data)
    out = bytearray()
    pos = 0
    table = defaultdict(deque)

    def key_at(i: int):
        if i + 2 < n:
            return data[i:i + 3]
        return None

    def add_pos(i: int) -> None:
        k = key_at(i)
        if k is None:
            return
        dq = table[k]
        dq.append(i)
        while dq and i - dq[0] > N:
            dq.popleft()
        # Limit pathological chains; recent candidates are normally best.
        while len(dq) > 256:
            dq.popleft()

    while pos < n:
        flag_pos = len(out)
        out.append(0)
        flags = 0

        for bit in range(8):
            if pos >= n:
                break

            best_len = 0
            best_abs = 0
            k = key_at(pos)
            dq = table.get(k) if k is not None else None
            if dq:
                while dq and pos - dq[0] > N:
                    dq.popleft()
                checked = 0
                for cand in reversed(dq):
                    dist = pos - cand
                    if dist <= 0 or dist > N:
                        continue
                    length = 0
                    while (length < F and pos + length < n and
                           data[cand + length] == data[pos + length]):
                        length += 1
                    if length > best_len:
                        best_len = length
                        best_abs = cand
                        if length == F:
                            break
                    checked += 1
                    if checked >= max_candidates:
                        break

            if best_len >= 3:
                ring_pos = (INIT_POS + best_abs) & 0xFFF
                out.append(ring_pos & 0xFF)
                out.append(((ring_pos >> 4) & 0xF0) | (best_len - 3))
                for j in range(best_len):
                    add_pos(pos + j)
                pos += best_len
            else:
                flags |= 1 << bit
                out.append(data[pos])
                add_pos(pos)
                pos += 1

        out[flag_pos] = flags

    return bytes(out)

def read_g24_header(path: Path) -> Tuple[int, int, int, int]:
    data = path.read_bytes()[:8]
    if len(data) < 8:
        raise ValueError(f"Invalid G24: file too small: {path}")
    x, y, w, h = struct.unpack("<hhhh", data)
    if w <= 0 or h <= 0:
        raise ValueError(f"Invalid G24 size: {w}x{h}: {path}")
    return int(x), int(y), int(w), int(h)


def g24_to_png(g24_path: Path, png_path: Path) -> dict:
    data = g24_path.read_bytes()
    if len(data) < 8:
        raise ValueError(f"Invalid G24: file too small: {g24_path}")
    x, y, w, h = struct.unpack_from("<hhhh", data, 0)
    if w <= 0 or h <= 0:
        raise ValueError(f"Invalid G24 size: {w}x{h}: {g24_path}")

    stride = (w * 3 + 3) & ~3
    raw_size = stride * h
    raw = lzss_decompress(data[8:], raw_size)

    rgb = bytearray(w * h * 3)
    for row in range(h):
        src_row = h - 1 - row
        src = src_row * stride
        dst = row * w * 3
        for col in range(w):
            sp = src + col * 3
            dp = dst + col * 3
            b, g, r = raw[sp], raw[sp + 1], raw[sp + 2]
            rgb[dp], rgb[dp + 1], rgb[dp + 2] = r, g, b

    png_path.parent.mkdir(parents=True, exist_ok=True)
    Image.frombytes("RGB", (w, h), bytes(rgb)).save(png_path)
    return {
        "input": str(g24_path),
        "output": str(png_path),
        "x": int(x),
        "y": int(y),
        "width": int(w),
        "height": int(h),
        "stride": stride,
        "raw_size": raw_size,
        "compressed_size": len(data) - 8,
    }


def png_to_g24(png_path: Path, g24_path: Path, x: int = 0, y: int = 0, compress_mode: str = "greedy") -> dict:
    with Image.open(png_path) as img:
        img = img.convert("RGB")
        w, h = img.size
        rgb = img.tobytes()

    if not (-32768 <= x <= 32767 and -32768 <= y <= 32767):
        raise ValueError(f"x/y must fit int16: x={x}, y={y}")
    if not (0 < w <= 32767 and 0 < h <= 32767):
        raise ValueError(f"width/height must fit positive int16: {w}x{h}")

    stride = (w * 3 + 3) & ~3
    raw = bytearray(stride * h)
    for row in range(h):
        src = row * w * 3
        dst_row = h - 1 - row
        dst = dst_row * stride
        for col in range(w):
            sp = src + col * 3
            dp = dst + col * 3
            r, g, b = rgb[sp], rgb[sp + 1], rgb[sp + 2]
            raw[dp], raw[dp + 1], raw[dp + 2] = b, g, r

    if compress_mode == "literal":
        body = lzss_compress_literal(bytes(raw))
    elif compress_mode == "greedy":
        body = lzss_compress(bytes(raw))
    else:
        raise ValueError(f"unknown compress mode: {compress_mode}")
    g24_path.parent.mkdir(parents=True, exist_ok=True)
    g24_path.write_bytes(struct.pack("<hhhh", x, y, w, h) + body)
    return {
        "input": str(png_path),
        "output": str(g24_path),
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "stride": stride,
        "raw_size": len(raw),
        "compressed_size": len(body),
        "compress_mode": compress_mode,
    }


def g24_raw(g24_path: Path) -> bytes:
    data = g24_path.read_bytes()
    x, y, w, h = struct.unpack_from("<hhhh", data, 0)
    stride = (w * 3 + 3) & ~3
    return lzss_decompress(data[8:], stride * h)


def roundtrip(g24_path: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = g24_path.stem
    png1 = out_dir / f"{stem}.decoded.png"
    g24_2 = out_dir / f"{stem}.repacked.g24"
    png2 = out_dir / f"{stem}.redecoded.png"

    info1 = g24_to_png(g24_path, png1)
    png_to_g24(png1, g24_2, x=info1["x"], y=info1["y"], compress_mode="greedy")
    info2 = g24_to_png(g24_2, png2)

    raw1 = g24_raw(g24_path)
    raw2 = g24_raw(g24_2)
    same_raw = raw1 == raw2

    with Image.open(png1) as a, Image.open(png2) as b:
        same_png = a.tobytes() == b.tobytes() and a.size == b.size and a.mode == b.mode

    return {
        "source": str(g24_path),
        "decoded_png": str(png1),
        "repacked_g24": str(g24_2),
        "redecoded_png": str(png2),
        "source_info": info1,
        "repacked_info": info2,
        "raw_equal_after_roundtrip": same_raw,
        "png_equal_after_roundtrip": same_png,
        "source_file_size": g24_path.stat().st_size,
        "repacked_file_size": g24_2.stat().st_size,
    }


def iter_pngs(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
    else:
        seen = set()
        for p in sorted(path.rglob("*.png")) + sorted(path.rglob("*.PNG")):
            if p not in seen:
                seen.add(p)
                yield p


def iter_g24s(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
    else:
        seen = set()
        for p in sorted(path.rglob("*.g24")) + sorted(path.rglob("*.G24")):
            if p not in seen:
                seen.add(p)
                yield p


def find_ref_g24(ref_root: Optional[Path], src_root: Path, png: Path) -> Optional[Path]:
    """Find the matching original G24 for one PNG in directory conversion."""
    if ref_root is None:
        return None
    if ref_root.is_file():
        return ref_root
    rel = png.relative_to(src_root) if src_root.is_dir() else Path(png.name)
    candidates = [
        ref_root / rel.with_suffix(".g24"),
        ref_root / rel.with_suffix(".G24"),
        ref_root / (rel.stem + ".g24"),
        ref_root / (rel.stem + ".G24"),
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    # Fallback: recursive basename match, useful when PNGs are flattened by the user.
    for ext in (".g24", ".G24"):
        hits = list(ref_root.rglob(png.stem + ext))
        if hits:
            return hits[0]
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AI5WIN G24 <-> PNG converter/tester")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="print G24 header information")
    p.add_argument("g24", type=Path)

    p = sub.add_parser("g24topng", aliases=["g242png"], help="convert G24 file/directory to PNG")
    p.add_argument("g24", type=Path)
    p.add_argument("png", type=Path)

    p = sub.add_parser("png2g24", help="convert PNG file/directory to G24")
    p.add_argument("png", type=Path)
    p.add_argument("g24", type=Path)
    p.add_argument("--x", type=int, default=0)
    p.add_argument("--y", type=int, default=0)
    p.add_argument("--ref-g24", type=Path, help="read x/y from an existing G24")
    p.add_argument("--compress", choices=["greedy", "literal"], default="greedy",
                   help="LZSS compression mode; greedy keeps files near original size")

    p = sub.add_parser("roundtrip", help="test G24 -> PNG -> G24 -> PNG")
    p.add_argument("g24", type=Path)
    p.add_argument("out_dir", type=Path)

    args = parser.parse_args(argv)

    if args.cmd == "info":
        x, y, w, h = read_g24_header(args.g24)
        stride = (w * 3 + 3) & ~3
        print(json.dumps({"x": x, "y": y, "width": w, "height": h, "stride": stride,
                          "raw_size": stride * h, "file_size": args.g24.stat().st_size}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd in ("g24topng", "g242png"):
        if args.g24.is_dir():
            for g24 in iter_g24s(args.g24):
                out = args.png / g24.relative_to(args.g24).with_suffix(".png")
                print(json.dumps(g24_to_png(g24, out), ensure_ascii=False))
        else:
            print(json.dumps(g24_to_png(args.g24, args.png), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "png2g24":
        default_x, default_y = args.x, args.y
        if args.ref_g24 and args.ref_g24.is_file():
            default_x, default_y, _w, _h = read_g24_header(args.ref_g24)
        if args.png.is_dir():
            for png in iter_pngs(args.png):
                out = args.g24 / png.relative_to(args.png).with_suffix(".g24")
                x, y = default_x, default_y
                ref = find_ref_g24(args.ref_g24, args.png, png)
                if ref:
                    x, y, _w, _h = read_g24_header(ref)
                print(json.dumps(png_to_g24(png, out, x=x, y=y, compress_mode=args.compress), ensure_ascii=False))
        else:
            print(json.dumps(png_to_g24(args.png, args.g24, x=default_x, y=default_y, compress_mode=args.compress), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "roundtrip":
        print(json.dumps(roundtrip(args.g24, args.out_dir), ensure_ascii=False, indent=2))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
