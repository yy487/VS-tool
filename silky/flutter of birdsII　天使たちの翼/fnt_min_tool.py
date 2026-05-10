#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fnt_min.igf analyzer/viewer for Angel engine minimum font.

This is NOT a ZEUS/IGF picture.  Angel.exe loads Fnt_min as a raw font
bitmap table.  Each glyph is 24x24 pixels, 4bpp nibble data, two pixels per
byte, 12 bytes per scanline, 24 scanlines per glyph.  Sixteen glyphs are
interleaved by scanline in a 0x1200-byte block; blocks are stored backwards
from EOF.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

# Character coverage table copied from Angel.exe DAT_00430de0.
RANGES = [
    (0x0020, 0x00DF),
    (0x8140, 0x81FF),
    (0x8240, 0x82FF),
    (0x8340, 0x83FF),
    (0x8440, 0x84BF),
    (0x8740, 0x879F),
    (0x8890, 0x88FF),
]
RANGES += [(hi << 8 | 0x40, hi << 8 | 0xFF) for hi in range(0x89, 0xA0)]
RANGES += [(hi << 8 | 0x40, hi << 8 | 0xFF) for hi in range(0xE0, 0xEA)]
RANGES += [
    (0xEA40, 0xEAAF),
    (0xED40, 0xEDFF),
    (0xEE40, 0xEEFF),
    (0xFA40, 0xFAFF),
    (0xFB40, 0xFBFF),
    (0xFC40, 0xFCBF),
]

GLYPH_W = 24
GLYPH_H = 24
ROW_BYTES = 12
GROUP = 16
BLOCK_STRIDE = 0x1200        # 16 glyphs * 24 rows * 12 bytes
ROW_STRIDE_IN_BLOCK = 0xC0   # 16 glyphs * 12 bytes


def range_bases():
    bases = []
    acc = 0
    for s, e in RANGES:
        bases.append(acc)
        acc += e - s + 1
    return bases, acc

RANGE_BASES, TOTAL_GLYPHS = range_bases()


def code_to_index(code: int) -> int:
    for (s, e), base in zip(RANGES, RANGE_BASES):
        if s <= code <= e:
            return base + code - s
    raise KeyError(f"code 0x{code:04X} is not covered by fnt_min")


def char_to_code(ch: str) -> int:
    raw = ch.encode('cp932')
    if len(raw) == 1:
        return raw[0]
    if len(raw) == 2:
        return (raw[0] << 8) | raw[1]
    raise ValueError(f"unsupported cp932 sequence for {ch!r}: {raw.hex()}")


def glyph_offset(font_size: int, index: int) -> int:
    if not (0 <= index < TOTAL_GLYPHS):
        raise IndexError(index)
    return font_size - ROW_STRIDE_IN_BLOCK - (index >> 4) * BLOCK_STRIDE + (index & 0xF) * ROW_BYTES


def read_glyph_nibbles(data: bytes, index: int):
    start = glyph_offset(len(data), index)
    pix = [[7 for _ in range(GLYPH_W)] for _ in range(GLYPH_H)]
    for y in range(GLYPH_H):
        off = start - y * ROW_STRIDE_IN_BLOCK
        row = data[off:off + ROW_BYTES]
        for xbyte, b in enumerate(row):
            # Angel.exe draws low nibble first, high nibble second.
            pix[y][xbyte * 2] = b & 0x0F
            pix[y][xbyte * 2 + 1] = b >> 4
    return pix


def write_glyph_nibbles(data: bytearray, index: int, pix) -> None:
    start = glyph_offset(len(data), index)
    for y in range(GLYPH_H):
        off = start - y * ROW_STRIDE_IN_BLOCK
        for xbyte in range(ROW_BYTES):
            lo = int(pix[y][xbyte * 2]) & 0x0F
            hi = int(pix[y][xbyte * 2 + 1]) & 0x0F
            data[off + xbyte] = lo | (hi << 4)


def nibble_to_alpha(v: int) -> int:
    # 0x7 is transparent in the engine color table.  Values farther from 7
    # are stronger.  This is for preview only, not exact engine blending.
    if v == 7:
        return 0
    return min(255, 40 + abs(v - 7) * 36)


def render_text(data: bytes, text: str, scale: int = 2):
    from PIL import Image, ImageDraw
    glyphs = []
    for ch in text:
        if ch == '\n':
            glyphs.append(None)
            continue
        try:
            idx = code_to_index(char_to_code(ch))
            glyphs.append(read_glyph_nibbles(data, idx))
        except Exception:
            glyphs.append(None)
    lines = [[]]
    for g in glyphs:
        if g is None:
            lines.append([])
        else:
            lines[-1].append(g)
    width = max(1, max((len(line) for line in lines), default=1) * GLYPH_W)
    height = max(1, len(lines) * GLYPH_H)
    im = Image.new('RGBA', (width * scale, height * scale), (32, 40, 56, 255))
    px = im.load()
    for ly, line in enumerate(lines):
        for gx, g in enumerate(line):
            for y in range(GLYPH_H):
                for x in range(GLYPH_W):
                    a = nibble_to_alpha(g[y][x])
                    if a:
                        for sy in range(scale):
                            for sx in range(scale):
                                px[(gx * GLYPH_W + x) * scale + sx, (ly * GLYPH_H + y) * scale + sy] = (245, 245, 245, a)
    return im


def render_sheet(data: bytes, start: int, count: int, cols: int = 32, scale: int = 1):
    from PIL import Image, ImageDraw
    cell = GLYPH_W + 8
    rows = (count + cols - 1) // cols
    im = Image.new('RGBA', (cols * cell * scale, rows * cell * scale), (32, 40, 56, 255))
    px = im.load()
    for n in range(count):
        idx = start + n
        if idx >= TOTAL_GLYPHS:
            break
        g = read_glyph_nibbles(data, idx)
        ox = (n % cols) * cell + 4
        oy = (n // cols) * cell + 4
        for y in range(GLYPH_H):
            for x in range(GLYPH_W):
                a = nibble_to_alpha(g[y][x])
                if a:
                    for sy in range(scale):
                        for sx in range(scale):
                            px[(ox + x) * scale + sx, (oy + y) * scale + sy] = (245, 245, 245, a)
    return im


def cmd_info(args):
    data = Path(args.font).read_bytes()
    info = {
        'file': str(args.font),
        'size': len(data),
        'size_hex': hex(len(data)),
        'glyph_width': GLYPH_W,
        'glyph_height': GLYPH_H,
        'bits_per_pixel': 4,
        'glyph_count': TOTAL_GLYPHS,
        'expected_size': (TOTAL_GLYPHS // 16) * BLOCK_STRIDE,
        'expected_size_hex': hex((TOTAL_GLYPHS // 16) * BLOCK_STRIDE),
        'ranges': [{'start': hex(s), 'end': hex(e), 'count': e - s + 1} for s, e in RANGES],
    }
    print(json.dumps(info, ensure_ascii=False, indent=2))


def cmd_preview(args):
    data = Path(args.font).read_bytes()
    im = render_text(data, args.text, args.scale)
    im.save(args.output)
    print(args.output)


def cmd_sheet(args):
    data = Path(args.font).read_bytes()
    im = render_sheet(data, args.start, args.count, args.cols, args.scale)
    im.save(args.output)
    print(args.output)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('info')
    p.add_argument('font', type=Path)
    p.set_defaults(func=cmd_info)
    p = sub.add_parser('preview')
    p.add_argument('font', type=Path)
    p.add_argument('output')
    p.add_argument('--text', default='美蔓\nゆず\n楢原診療所\n倉町温泉')
    p.add_argument('--scale', type=int, default=3)
    p.set_defaults(func=cmd_preview)
    p = sub.add_parser('sheet')
    p.add_argument('font', type=Path)
    p.add_argument('output')
    p.add_argument('--start', type=int, default=0)
    p.add_argument('--count', type=int, default=512)
    p.add_argument('--cols', type=int, default=32)
    p.add_argument('--scale', type=int, default=1)
    p.set_defaults(func=cmd_sheet)
    args = ap.parse_args(argv)
    args.func(args)

if __name__ == '__main__':
    main()
