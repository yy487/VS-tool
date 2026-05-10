#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fnt_min.igf glyph replacement helper for Angel / flutter of birds engine.

Pipeline:
  1) collect-chars   scan translated JSON name/message fields
  2) make-map        assign CP932 carrier slots for CP932-unencodable chars
  3) patch-font      draw original Chinese glyphs into carrier slots of fnt_min.igf
  4) apply-map-json  replace JSON name/message chars with carrier chars before SNC injection

Mapping JSON formats accepted by all map-based commands:
1) cn_jp style, recommended:
{
  "过": "過",
  "这": "這"
}

2) expanded style produced by make-map:
{
  "过": {"carrier": "侽", "code": "0x8890", "index": 1234},
  ...
}

In both formats, the key is the source/Chinese character to render, and the
value/carrier is the CP932 character actually written into scripts.

The patcher keeps file size/layout unchanged. It only overwrites selected 24x24 glyphs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

# Angel.exe DAT_00430de0 coverage table.
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
BLOCK_STRIDE = 0x1200
ROW_STRIDE_IN_BLOCK = 0xC0
EXPECTED_GLYPHS = 0x2100
EXPECTED_SIZE = EXPECTED_GLYPHS // 16 * BLOCK_STRIDE

ASCII_PROTECT = set(chr(i) for i in range(0x20, 0x7F))
JIS_PUNCT_PROTECT = set("　、。，．・：；？！゛゜´｀¨＾￣＿ヽヾゝゞ〃仝々〆〇ー―‐／＼～∥｜…‥‘’“”（）〔〕［］｛｝〈〉《》「」『』【】＋－±×÷＝≠＜＞≦≧∞∴♂♀°′″℃￥＄￠￡％＃＆＊＠§☆★○●◎◇◆□■△▲▽▼※〒→←↑↓")
KANA_PROTECT_RE = re.compile(r"[ぁ-ゖァ-ヺー]|")


def range_bases() -> Tuple[List[int], int]:
    bases: List[int] = []
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


def code_to_char(code: int) -> str:
    if code <= 0xFF:
        return bytes([code]).decode('cp932')
    return bytes([code >> 8, code & 0xFF]).decode('cp932')


def char_to_code(ch: str) -> int:
    raw = ch.encode('cp932')
    if len(raw) == 1:
        return raw[0]
    if len(raw) == 2:
        return (raw[0] << 8) | raw[1]
    raise ValueError(f"unsupported cp932 sequence for {ch!r}: {raw.hex()}")


def char_to_index(ch: str) -> int:
    return code_to_index(char_to_code(ch))


def is_cp932_encodable(ch: str) -> bool:
    try:
        char_to_index(ch)
        return True
    except Exception:
        return False


def glyph_offset(font_size: int, index: int) -> int:
    if not (0 <= index < TOTAL_GLYPHS):
        raise IndexError(index)
    return font_size - ROW_STRIDE_IN_BLOCK - (index >> 4) * BLOCK_STRIDE + (index & 0xF) * ROW_BYTES


def write_glyph_nibbles(data: bytearray, index: int, pix: List[List[int]]) -> None:
    start = glyph_offset(len(data), index)
    for y in range(GLYPH_H):
        off = start - y * ROW_STRIDE_IN_BLOCK
        for xbyte in range(ROW_BYTES):
            lo = int(pix[y][xbyte * 2]) & 0x0F
            hi = int(pix[y][xbyte * 2 + 1]) & 0x0F
            data[off + xbyte] = lo | (hi << 4)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path: Path, obj: Any, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        path.write_text(json.dumps(obj, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')


def iter_json_files(path: Path) -> List[Path]:
    if path.is_file():
        return [path]
    return sorted(path.rglob('*.json'))


def walk_strings(obj: Any, fields: set[str]) -> Iterable[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in fields and isinstance(v, str):
                yield v
            else:
                yield from walk_strings(v, fields)
    elif isinstance(obj, list):
        for it in obj:
            yield from walk_strings(it, fields)


def collect_chars(json_path: Path, fields: set[str]) -> Counter:
    c = Counter()
    for jp in iter_json_files(json_path):
        try:
            obj = read_json(jp)
        except Exception as e:
            print(f"[WARN] skip invalid json {jp}: {e}")
            continue
        for s in walk_strings(obj, fields):
            c.update(s)
    return c


def build_covered_chars() -> List[Tuple[str, int, int]]:
    out: List[Tuple[str, int, int]] = []
    for s, e in RANGES:
        for code in range(s, e + 1):
            try:
                ch = code_to_char(code)
                idx = code_to_index(code)
            except Exception:
                continue
            if len(ch) == 1:
                out.append((ch, code, idx))
    return out


def is_default_protected(ch: str) -> bool:
    if ch in ASCII_PROTECT or ch in JIS_PUNCT_PROTECT:
        return True
    o = ord(ch)
    # keep kana and fullwidth roman/digits/symbols stable
    if 0x3040 <= o <= 0x30FF:
        return True
    if 0xFF00 <= o <= 0xFFEF:
        return True
    return False


def _normalize_map_record(src: str, v: Any) -> Tuple[str, int, int]:
    """Return (carrier, code, index) for one mapping record.

    Supported forms:
      "中": "日"
      "中": {"carrier": "日"}
      "中": {"to": "日"}
      "中": {"dst": "日"}
      "中": {"jp": "日"}
      "中": {"code": "0x8890", "index": 1234}
    """
    if not isinstance(src, str) or len(src) != 1:
        raise ValueError(f"mapping key must be exactly one character, got {src!r}")

    if isinstance(v, str):
        carrier = v
        if len(carrier) != 1:
            raise ValueError(f"mapping value for {src!r} must be one carrier character, got {v!r}")
        code = char_to_code(carrier)
        idx = code_to_index(code)
        return carrier, code, idx

    if isinstance(v, dict):
        carrier = (
            v.get('carrier') or v.get('to') or v.get('dst') or
            v.get('jp') or v.get('slot') or v.get('replace')
        )
        if carrier is not None:
            if not isinstance(carrier, str) or len(carrier) != 1:
                raise ValueError(f"bad carrier for {src!r}: {carrier!r}")
            code = char_to_code(carrier)
            idx = code_to_index(code)
            # If code/index are present, verify they point to the same slot.
            if 'code' in v:
                given_code = int(str(v['code']), 16) if isinstance(v['code'], str) else int(v['code'])
                if given_code != code:
                    raise ValueError(f"code mismatch for {src!r}: carrier {carrier!r}=0x{code:04X}, json code=0x{given_code:04X}")
            if 'index' in v and int(v['index']) != idx:
                raise ValueError(f"index mismatch for {src!r}: carrier {carrier!r} index={idx}, json index={v['index']}")
            return carrier, code, idx

        # Expanded records may only contain code/index. In that case recover the carrier char from code.
        if 'code' in v:
            code = int(str(v['code']), 16) if isinstance(v['code'], str) else int(v['code'])
            idx = code_to_index(code)
            carrier = code_to_char(code)
            if 'index' in v and int(v['index']) != idx:
                raise ValueError(f"index mismatch for {src!r}: code=0x{code:04X} index={idx}, json index={v['index']}")
            return carrier, code, idx

    raise ValueError(f"bad mapping value for {src!r}: {v!r}")


def load_map(path: Path) -> Dict[str, Dict[str, Any]]:
    raw = read_json(path)
    m: Dict[str, Dict[str, Any]] = {}

    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        # Also accept list-style maps: [{"src":"过", "dst":"過"}, ...]
        norm_items = []
        for i, rec in enumerate(raw):
            if not isinstance(rec, dict):
                raise ValueError(f"mapping list item #{i} must be object, got {rec!r}")
            src = rec.get('src') or rec.get('from') or rec.get('cn') or rec.get('zh') or rec.get('char')
            if not isinstance(src, str):
                raise ValueError(f"mapping list item #{i} has no src/from/cn/zh/char field")
            norm_items.append((src, rec))
        items = norm_items
    else:
        raise ValueError("mapping must be a JSON object or list")

    used_carriers: Dict[str, str] = {}
    for src, v in items:
        carrier, code, idx = _normalize_map_record(src, v)
        if carrier in used_carriers and used_carriers[carrier] != src:
            raise ValueError(f"carrier collision: {used_carriers[carrier]!r} and {src!r} both map to {carrier!r}")
        used_carriers[carrier] = src
        m[src] = {"carrier": carrier, "code": f"0x{code:04X}", "index": idx}
    return m


def replace_strings(obj: Any, fields: set[str], table: Dict[str, str]) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in fields and isinstance(v, str):
                out[k] = ''.join(table.get(ch, ch) for ch in v)
            else:
                out[k] = replace_strings(v, fields, table)
        return out
    if isinstance(obj, list):
        return [replace_strings(x, fields, table) for x in obj]
    return obj


def find_font(user_path: str | None) -> str:
    if user_path:
        return user_path
    candidates = []
    windir = os.environ.get('WINDIR') or os.environ.get('SystemRoot')
    if windir:
        candidates += [
            os.path.join(windir, 'Fonts', 'msyh.ttc'),
            os.path.join(windir, 'Fonts', 'simhei.ttf'),
            os.path.join(windir, 'Fonts', 'simsun.ttc'),
            os.path.join(windir, 'Fonts', 'msgothic.ttc'),
        ]
    candidates += [
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    raise SystemExit("No font found. Please pass --ttf path/to/chinese_font.ttf or .ttc")


def render_char_to_nibbles(ch: str, ttf: str, size: int, xoff: int, yoff: int, threshold: int) -> List[List[int]]:
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(ttf, size=size)
    # render larger temporary canvas to avoid clipping
    canvas = Image.new('L', (GLYPH_W * 3, GLYPH_H * 3), 0)
    draw = ImageDraw.Draw(canvas)
    try:
        bbox = draw.textbbox((0, 0), ch, font=font)
    except Exception:
        bbox = font.getbbox(ch)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (GLYPH_W - tw) // 2 - bbox[0] + xoff + GLYPH_W
    y = (GLYPH_H - th) // 2 - bbox[1] + yoff + GLYPH_H
    draw.text((x, y), ch, fill=255, font=font)
    crop = canvas.crop((GLYPH_W, GLYPH_H, GLYPH_W * 2, GLYPH_H * 2))
    pix: List[List[int]] = [[7 for _ in range(GLYPH_W)] for _ in range(GLYPH_H)]
    for y in range(GLYPH_H):
        for x in range(GLYPH_W):
            a = crop.getpixel((x, y))
            if a <= threshold:
                pix[y][x] = 7
            else:
                # 7 is transparent. Use values above 7 for visible white-like pixels.
                # Keep antialiasing by spreading 8..15.
                pix[y][x] = 8 + min(7, max(0, round((a - threshold) * 7 / max(1, 255 - threshold))))
    return pix


def cmd_collect_chars(args):
    fields = set(args.fields.split(','))
    cnt = collect_chars(args.json_path, fields)
    items = []
    for ch, n in sorted(cnt.items(), key=lambda kv: (-kv[1], ord(kv[0]))):
        encodable = is_cp932_encodable(ch)
        rec = {"char": ch, "count": n, "cp932": encodable}
        if encodable:
            code = char_to_code(ch)
            rec["code"] = f"0x{code:04X}"
            rec["index"] = code_to_index(code)
        items.append(rec)
    out = {"fields": sorted(fields), "total_unique": len(items), "unencodable_count": sum(1 for x in items if not x['cp932']), "chars": items}
    write_json(args.output, out, pretty=True)
    print(json.dumps({"unique": len(items), "unencodable": out['unencodable_count'], "output": str(args.output)}, ensure_ascii=False, indent=2))


def cmd_make_map(args):
    fields = set(args.fields.split(','))
    trans_cnt = collect_chars(args.json_path, fields)
    src_chars = [ch for ch, _ in sorted(trans_cnt.items(), key=lambda kv: (-kv[1], ord(kv[0]))) if not is_cp932_encodable(ch)]
    protected = set(ch for ch in trans_cnt if is_cp932_encodable(ch))
    if args.protect_json:
        protected.update(ch for ch in collect_chars(args.protect_json, fields) if is_cp932_encodable(ch))
    if args.protect:
        protected.update(args.protect)
    carriers: List[Tuple[str, int, int]] = []
    for ch, code, idx in build_covered_chars():
        if ch in protected:
            continue
        if is_default_protected(ch) and not args.allow_symbols:
            continue
        # Avoid using halfwidth control-ish and whitespace slots.
        if ch.isspace():
            continue
        carriers.append((ch, code, idx))
    if len(carriers) < len(src_chars):
        raise SystemExit(f"not enough carrier glyphs: need {len(src_chars)}, available {len(carriers)}")
    m: Dict[str, Dict[str, Any]] = {}
    for src, (carrier, code, idx) in zip(src_chars, carriers):
        m[src] = {"carrier": carrier, "code": f"0x{code:04X}", "index": idx, "count": trans_cnt[src]}
    write_json(args.output, m, pretty=True)
    print(json.dumps({"need": len(src_chars), "available": len(carriers), "output": str(args.output)}, ensure_ascii=False, indent=2))


def cmd_check_map(args):
    m = load_map(args.map_json)
    out = {
        "map": str(args.map_json),
        "entries": len(m),
        "samples": [
            {"src": src, "carrier": rec["carrier"], "code": rec["code"], "index": rec["index"]}
            for src, rec in list(m.items())[:args.limit]
        ],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_apply_map_json(args):
    fields = set(args.fields.split(','))
    m = load_map(args.map_json)
    table = {src: rec['carrier'] for src, rec in m.items()}
    files = iter_json_files(args.input)
    for jp in files:
        obj = read_json(jp)
        out = replace_strings(obj, fields, table)
        rel = jp.name if args.input.is_file() else jp.relative_to(args.input)
        out_path = args.output / rel if args.output.suffix.lower() != '.json' else args.output
        write_json(out_path, out, pretty=not args.compact)
    print(json.dumps({"files": len(files), "output": str(args.output)}, ensure_ascii=False, indent=2))


def cmd_patch_font(args):
    data = bytearray(Path(args.input_font).read_bytes())
    if len(data) != EXPECTED_SIZE and not args.force:
        raise SystemExit(f"unexpected font size {len(data):#x}, expected {EXPECTED_SIZE:#x}; pass --force to override")
    m = load_map(args.map_json)
    ttf = find_font(args.ttf)
    for src, rec in m.items():
        idx = int(rec['index'])
        pix = render_char_to_nibbles(src, ttf, args.size, args.xoff, args.yoff, args.threshold)
        write_glyph_nibbles(data, idx, pix)
    Path(args.output_font).write_bytes(data)
    print(json.dumps({"patched": len(m), "ttf": ttf, "output": str(args.output_font)}, ensure_ascii=False, indent=2))


def cmd_preview_map(args):
    from PIL import Image, ImageDraw, ImageFont
    # reuse original preview style from fnt table
    data = Path(args.font).read_bytes()
    m = load_map(args.map_json)
    samples = list(m.items())[:args.count]
    cell_w, cell_h = 120, 34
    im = Image.new('RGBA', (cell_w * args.cols, cell_h * ((len(samples)+args.cols-1)//args.cols)), (32,40,56,255))
    px = im.load()
    try:
        label_font = ImageFont.truetype(find_font(args.ttf), 12)
    except Exception:
        label_font = None
    draw = ImageDraw.Draw(im)
    for n, (src, rec) in enumerate(samples):
        col, row = n % args.cols, n // args.cols
        ox, oy = col * cell_w, row * cell_h
        idx = int(rec['index'])
        start = glyph_offset(len(data), idx)
        for y in range(GLYPH_H):
            off = start - y * ROW_STRIDE_IN_BLOCK
            for xb, b in enumerate(data[off:off+ROW_BYTES]):
                vals = [b & 0x0F, b >> 4]
                for k, v in enumerate(vals):
                    if v != 7:
                        a = min(255, 40 + abs(v - 7) * 36)
                        im.putpixel((ox + xb*2 + k + 4, oy + y + 4), (245,245,245,a))
        draw.text((ox+34, oy+4), f"{src}->{rec['carrier']}", fill=(245,245,245,255), font=label_font)
        draw.text((ox+34, oy+18), f"{rec['code']} idx={rec['index']}", fill=(180,200,220,255), font=label_font)
    im.save(args.output)
    print(args.output)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Patch Angel fnt_min.igf carrier glyphs for CnJpMap-style localization.')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('collect-chars')
    p.add_argument('json_path', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--fields', default='name,message')
    p.set_defaults(func=cmd_collect_chars)

    p = sub.add_parser('make-map')
    p.add_argument('json_path', type=Path, help='translated JSON directory/file')
    p.add_argument('output', type=Path)
    p.add_argument('--fields', default='name,message')
    p.add_argument('--protect-json', type=Path, help='original/extracted JSON to protect direct CP932 chars that still appear')
    p.add_argument('--protect', default='', help='extra characters that must not be used as carrier slots')
    p.add_argument('--allow-symbols', action='store_true', help='allow punctuation/kana/fullwidth symbols as carriers; not recommended')
    p.set_defaults(func=cmd_make_map)

    p = sub.add_parser('check-map', help='validate cn_jp.json / char_map.json and show resolved carrier slots')
    p.add_argument('map_json', type=Path)
    p.add_argument('--limit', type=int, default=20)
    p.set_defaults(func=cmd_check_map)

    p = sub.add_parser('apply-map-json', help='replace JSON name/message with carrier chars using cn_jp.json / char_map.json')
    p.add_argument('input', type=Path)
    p.add_argument('map_json', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--fields', default='name,message')
    p.add_argument('--compact', action='store_true')
    p.set_defaults(func=cmd_apply_map_json)

    p = sub.add_parser('patch-font', help='draw source chars into carrier glyph slots using cn_jp.json / char_map.json')
    p.add_argument('input_font', type=Path)
    p.add_argument('map_json', type=Path)
    p.add_argument('output_font', type=Path)
    p.add_argument('--ttf', help='Chinese-capable .ttf/.ttc path. Example: C:\\Windows\\Fonts\\msyh.ttc')
    p.add_argument('--size', type=int, default=22)
    p.add_argument('--xoff', type=int, default=0)
    p.add_argument('--yoff', type=int, default=1)
    p.add_argument('--threshold', type=int, default=8)
    p.add_argument('--force', action='store_true')
    p.set_defaults(func=cmd_patch_font)

    p = sub.add_parser('preview-map', help='preview patched carrier glyphs')
    p.add_argument('font', type=Path)
    p.add_argument('map_json', type=Path)
    p.add_argument('output')
    p.add_argument('--ttf')
    p.add_argument('--count', type=int, default=128)
    p.add_argument('--cols', type=int, default=4)
    p.set_defaults(func=cmd_preview_map)

    args = ap.parse_args(argv)
    args.func(args)

if __name__ == '__main__':
    main()
