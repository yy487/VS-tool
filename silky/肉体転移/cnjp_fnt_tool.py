#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CnJpMap + full-glyph redraw workflow v6 for Arpeggio EVIT/SNC Fnt_go/Fnt_gos.

Core workflow:
  - Translated JSON stays UTF-8.
  - cn_jp map is display char -> CP932 carrier char, e.g. 这=這.
  - apply-map-json writes carrier chars into JSON for SNC injection.
  - patch-font redraws carrier glyph slots so the game displays the original display chars.
  - build can combine mapping JSON and redrawing fonts in one command.

This version supports two redraw modes:
  mapped-only : redraw only cn_jp mapped carrier slots.
  used-chars  : redraw mapped carrier slots + every CP932-displayable character actually used in JSON.

Fnt_go  : 24x24, raw 4bpp, 16 columns, 8448 glyphs.
Fnt_gos : 20x20, raw 4bpp, 16 columns, 8448 glyphs.
IGF glyph rows are addressed from the file tail, matching this game's renderer.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception as e:  # pragma: no cover
    raise SystemExit("Pillow is required. Install it with: pip install pillow") from e

# CP932 ranges used by Arpeggio.exe DAT_0042C7E4. Total = 8448.
CP932_RANGES: List[Tuple[int, int]] = [
    (0x0020, 0x00DF),
    (0x8140, 0x81FF),
    (0x8240, 0x82FF),
    (0x8340, 0x83FF),
    (0x8440, 0x84BF),
    (0x8740, 0x879F),
    (0x8890, 0x88FF),
]
CP932_RANGES += [(hi << 8 | 0x40, hi << 8 | 0xFF) for hi in range(0x89, 0xA0)]
CP932_RANGES += [(hi << 8 | 0x40, hi << 8 | 0xFF) for hi in range(0xE0, 0xEA)]
CP932_RANGES += [
    (0xEA40, 0xEAAF),
    (0xED40, 0xEDFF),
    (0xEE40, 0xEEFF),
    (0xFA40, 0xFAFF),
    (0xFB40, 0xFBFF),
    (0xFC40, 0xFCBF),
]
RANGE_BASES: List[int] = []
_acc = 0
for _s, _e in CP932_RANGES:
    RANGE_BASES.append(_acc)
    _acc += _e - _s + 1
EXPECTED_GLYPHS = _acc
assert EXPECTED_GLYPHS == 8448, EXPECTED_GLYPHS

DEFAULT_FIELDS = "name,msg,message,scr_msg"
CONTROL_CHARS = {"\r", "\n", "\t", "\v", "\f"}
# This game uses these box-drawing-looking chars as long-voice markers. Preserve by default.
SPECIAL_BAR_CHARS = set("├┬┤")


@dataclass(frozen=True)
class FontSpec:
    name: str
    glyph_w: int
    glyph_h: int
    expected_size: int

    @property
    def row_bytes(self) -> int:
        return self.glyph_w // 2

    @property
    def cols(self) -> int:
        return 16

    @property
    def row_stride(self) -> int:
        return self.cols * self.row_bytes

    @property
    def block_stride(self) -> int:
        return self.row_stride * self.glyph_h

    @property
    def glyph_rows(self) -> int:
        return EXPECTED_GLYPHS // self.cols

    @property
    def atlas_w(self) -> int:
        return self.cols * self.glyph_w

    @property
    def atlas_h(self) -> int:
        return self.glyph_rows * self.glyph_h


FNT_GO = FontSpec("Fnt_go", 24, 24, 2433024)
FNT_GOS = FontSpec("Fnt_gos", 20, 20, 1689600)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, obj: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(obj, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")


def iter_json_files(path: Path) -> List[Path]:
    if path is None:
        return []
    if path.is_file():
        return [path]
    return sorted(path.rglob("*.json"))


def walk_text_fields_with_path(obj: Any, fields: set[str], prefix: str = "$") -> Iterable[Tuple[str, str]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}"
            if k in fields and isinstance(v, str):
                yield p, v
            else:
                yield from walk_text_fields_with_path(v, fields, p)
    elif isinstance(obj, list):
        for i, it in enumerate(obj):
            yield from walk_text_fields_with_path(it, fields, f"{prefix}[{i}]")


def walk_text_fields(obj: Any, fields: set[str]) -> Iterable[str]:
    for _, s in walk_text_fields_with_path(obj, fields):
        yield s


def replace_text_fields(obj: Any, fields: set[str], table: Dict[str, str]) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in fields and isinstance(v, str):
                out[k] = "".join(table.get(ch, ch) for ch in v)
            else:
                out[k] = replace_text_fields(v, fields, table)
        return out
    if isinstance(obj, list):
        return [replace_text_fields(x, fields, table) for x in obj]
    return obj


def cp932_code(ch: str) -> int:
    if len(ch) != 1:
        raise ValueError(f"character must be exactly one Unicode character: {ch!r}")
    raw = ch.encode("cp932")
    if len(raw) == 1:
        return raw[0]
    if len(raw) == 2:
        return (raw[0] << 8) | raw[1]
    raise ValueError(f"unsupported CP932 sequence for {ch!r}: {raw.hex()}")


def cp932_char_from_code(code: int) -> str:
    if code <= 0xFF:
        return bytes([code]).decode("cp932")
    return bytes([code >> 8, code & 0xFF]).decode("cp932")


def cp932_index_from_code(code: int) -> int:
    for (start, end), base in zip(CP932_RANGES, RANGE_BASES):
        if start <= code <= end:
            return base + code - start
    raise ValueError(f"CP932 code 0x{code:04X} is outside this game's font table")


def cp932_index(ch: str) -> int:
    return cp932_index_from_code(cp932_code(ch))


def is_cp932_covered(ch: str) -> bool:
    try:
        cp932_index(ch)
        return True
    except Exception:
        return False


def covered_chars() -> List[Tuple[str, int, int]]:
    out: List[Tuple[str, int, int]] = []
    for start, end in CP932_RANGES:
        for code in range(start, end + 1):
            try:
                ch = cp932_char_from_code(code)
                idx = cp932_index_from_code(code)
            except Exception:
                continue
            if len(ch) == 1:
                out.append((ch, code, idx))
    return out


def normalize_map_record(src: str, value: Any) -> Tuple[str, int, int]:
    if not isinstance(src, str) or len(src) != 1:
        raise ValueError(f"mapping source key must be one character, got {src!r}")
    if isinstance(value, str):
        carrier = value
    elif isinstance(value, dict):
        carrier = (
            value.get("carrier") or value.get("jp") or value.get("dst") or value.get("to") or
            value.get("target") or value.get("slot") or value.get("replace")
        )
        if carrier is None and "code" in value:
            code = int(str(value["code"]), 16) if isinstance(value["code"], str) else int(value["code"])
            idx = cp932_index_from_code(code)
            carrier = cp932_char_from_code(code)
            if "index" in value and int(value["index"]) != idx:
                raise ValueError(f"mapping {src!r} code/index mismatch")
            return carrier, code, idx
    else:
        raise ValueError(f"bad mapping record for {src!r}: {value!r}")
    if not isinstance(carrier, str) or len(carrier) != 1:
        raise ValueError(f"mapping carrier for {src!r} must be one character, got {carrier!r}")
    code = cp932_code(carrier)
    idx = cp932_index_from_code(code)
    return carrier, code, idx


def parse_text_map(path: Path) -> Dict[str, Any]:
    raw: Dict[str, Any] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("//") or s.startswith(";"):
            continue
        # Remove inline comments only after whitespace to avoid damaging chars.
        s = re.split(r"\s+(?:#|//|;)", s, maxsplit=1)[0].strip()
        left = right = None
        for sep in ("=>", "=", "\t", ","):
            if sep in s:
                a, b = s.split(sep, 1)
                left, right = a.strip(), b.strip()
                break
        if left is None:
            parts = s.split()
            if len(parts) >= 2:
                left, right = parts[0], parts[1]
        if not left or not right:
            raise ValueError(f"bad map line {path}:{lineno}: {line!r}")
        if len(left) != 1 or len(right) != 1:
            raise ValueError(f"map line must contain one source char and one carrier char at {path}:{lineno}: {line!r}")
        raw[left] = right
    return raw


def load_map(path: Path, *, reverse: bool = False) -> Dict[str, Dict[str, Any]]:
    if path.suffix.lower() in {".json", ".jsonc"}:
        raw_obj = read_json(path)
    else:
        raw_obj = parse_text_map(path)

    items: List[Tuple[str, Any]] = []
    if isinstance(raw_obj, dict):
        items = list(raw_obj.items())
    elif isinstance(raw_obj, list):
        for i, rec in enumerate(raw_obj):
            if not isinstance(rec, dict):
                raise ValueError(f"mapping list item #{i} must be object")
            src = rec.get("src") or rec.get("cn") or rec.get("zh") or rec.get("from") or rec.get("source") or rec.get("display") or rec.get("char")
            if not isinstance(src, str):
                raise ValueError(f"mapping list item #{i} has no src/cn/from/source/display field")
            items.append((src, rec))
    else:
        raise ValueError("mapping must be a JSON object/list or a text .map file")

    if reverse:
        rev_items: List[Tuple[str, Any]] = []
        for src, rec in items:
            carrier, _, _ = normalize_map_record(src, rec)
            rev_items.append((carrier, src))
        items = rev_items

    out: Dict[str, Dict[str, Any]] = {}
    used: Dict[str, str] = {}
    for src, rec in items:
        carrier, code, idx = normalize_map_record(src, rec)
        if carrier in used and used[carrier] != src:
            raise ValueError(f"carrier collision: {used[carrier]!r} and {src!r} both map to {carrier!r}")
        used[carrier] = src
        out[src] = {"carrier": carrier, "code": f"0x{code:04X}", "index": idx}
    if not out:
        raise ValueError(f"no valid mapping loaded from {path}")
    return out


def glyph_offset(buf_size: int, index: int, spec: FontSpec) -> int:
    if not (0 <= index < EXPECTED_GLYPHS):
        raise IndexError(index)
    return buf_size - spec.row_stride - (index >> 4) * spec.block_stride + (index & 0x0F) * spec.row_bytes


def validate_font_size(data: bytes | bytearray, spec: FontSpec, path: Path) -> None:
    if len(data) != spec.expected_size:
        raise ValueError(f"{path} size mismatch for {spec.name}: got {len(data)}, expected {spec.expected_size}")


def detect_bg_nibble(data: bytes | bytearray) -> int:
    c = Counter()
    for b in data:
        c[b & 0x0F] += 1
        c[b >> 4] += 1
    return c.most_common(1)[0][0]


def read_glyph(data: bytes | bytearray, index: int, spec: FontSpec) -> List[List[int]]:
    start = glyph_offset(len(data), index, spec)
    pix = [[0 for _ in range(spec.glyph_w)] for _ in range(spec.glyph_h)]
    for y in range(spec.glyph_h):
        off = start - y * spec.row_stride
        for bx in range(spec.row_bytes):
            v = data[off + bx]
            pix[y][bx * 2] = v & 0x0F
            pix[y][bx * 2 + 1] = (v >> 4) & 0x0F
    return pix


def write_glyph(data: bytearray, index: int, spec: FontSpec, pix: List[List[int]]) -> None:
    start = glyph_offset(len(data), index, spec)
    for y in range(spec.glyph_h):
        off = start - y * spec.row_stride
        for bx in range(spec.row_bytes):
            lo = int(pix[y][bx * 2]) & 0x0F
            hi = int(pix[y][bx * 2 + 1]) & 0x0F
            data[off + bx] = lo | (hi << 4)


def load_truetype(font_path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_path), size=size)


def text_bbox(draw: ImageDraw.ImageDraw, ch: str, font: ImageFont.FreeTypeFont, stroke: int) -> Tuple[int, int, int, int]:
    try:
        return draw.textbbox((0, 0), ch, font=font, stroke_width=stroke)
    except TypeError:
        return draw.textbbox((0, 0), ch, font=font)


def render_char_indexed(
    ch: str,
    spec: FontSpec,
    font_path: Path,
    font_size: Optional[int],
    xoff: int,
    yoff: int,
    bg: int,
    ink: int,
    outline: int,
    stroke_width: int,
    threshold: int,
    fit: bool,
    anchor: str = "center",
) -> List[List[int]]:
    if font_size is None:
        font_size = spec.glyph_h - 1
    tmp_draw = ImageDraw.Draw(Image.new("L", (1, 1), 0))
    chosen_font = load_truetype(font_path, font_size)
    if fit:
        for s in range(font_size, 5, -1):
            f = load_truetype(font_path, s)
            b = text_bbox(tmp_draw, ch, f, stroke_width)
            w, h = b[2] - b[0], b[3] - b[1]
            if w <= spec.glyph_w and h <= spec.glyph_h:
                chosen_font = f
                break

    W, H = spec.glyph_w * 3, spec.glyph_h * 3
    fill_mask = Image.new("L", (W, H), 0)
    stroke_mask = Image.new("L", (W, H), 0)
    df = ImageDraw.Draw(fill_mask)
    ds = ImageDraw.Draw(stroke_mask)
    bbox = text_bbox(df, ch, chosen_font, stroke_width)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (spec.glyph_w - tw) // 2 - bbox[0] + spec.glyph_w + xoff
    if anchor == "center":
        y = (spec.glyph_h - th) // 2 - bbox[1] + spec.glyph_h + yoff
    elif anchor == "top":
        y = -bbox[1] + spec.glyph_h + yoff
    elif anchor == "bottom":
        y = spec.glyph_h - th - bbox[1] + spec.glyph_h + yoff
    elif anchor == "baseline":
        ascent, descent = chosen_font.getmetrics()
        baseline = spec.glyph_h - descent - 1 + yoff
        y = baseline - ascent + spec.glyph_h
    else:
        raise ValueError(f"bad anchor: {anchor!r}; expected center/top/bottom/baseline")
    if stroke_width > 0:
        ds.text((x, y), ch, font=chosen_font, fill=255, stroke_width=stroke_width, stroke_fill=255)
    df.text((x, y), ch, font=chosen_font, fill=255)
    fill = fill_mask.crop((spec.glyph_w, spec.glyph_h, spec.glyph_w * 2, spec.glyph_h * 2))
    stroke = stroke_mask.crop((spec.glyph_w, spec.glyph_h, spec.glyph_w * 2, spec.glyph_h * 2))
    pix = [[bg for _ in range(spec.glyph_w)] for _ in range(spec.glyph_h)]
    for yy in range(spec.glyph_h):
        for xx in range(spec.glyph_w):
            fa = fill.getpixel((xx, yy))
            sa = stroke.getpixel((xx, yy))
            if fa > threshold:
                pix[yy][xx] = ink
            elif sa > threshold:
                pix[yy][xx] = outline
    return pix


def collect_chars_from_json(path: Path, fields: set[str]) -> Counter:
    cnt: Counter[str] = Counter()
    for jp in iter_json_files(path):
        obj = read_json(jp)
        for s in walk_text_fields(obj, fields):
            cnt.update(s)
    return cnt


def validate_mapped_json_cp932(input_path: Path, fields: set[str], limit: int = 50) -> List[Dict[str, Any]]:
    bad: List[Dict[str, Any]] = []
    for jp in iter_json_files(input_path):
        obj = read_json(jp)
        for p, s in walk_text_fields_with_path(obj, fields):
            for pos, ch in enumerate(s):
                if ch in CONTROL_CHARS:
                    continue
                if not is_cp932_covered(ch):
                    bad.append({"file": str(jp), "path": p, "pos": pos, "char": ch, "ord": f"U+{ord(ch):04X}", "text": s[:120]})
                    if len(bad) >= limit:
                        return bad
    return bad


def build_render_plan(
    mapping: Dict[str, Dict[str, Any]],
    json_path: Optional[Path],
    fields: set[str],
    mode: str,
    preserve_special_bars: bool = True,
) -> Tuple[Dict[int, Dict[str, Any]], List[str], Counter]:
    """Return index -> {display, carrier, source}. Mapped entries take priority."""
    plan: Dict[int, Dict[str, Any]] = {}
    warnings: List[str] = []
    used = Counter()
    if json_path is not None:
        used = collect_chars_from_json(json_path, fields)

    for src, rec in mapping.items():
        idx = int(rec["index"])
        carrier = rec["carrier"]
        plan[idx] = {"display": src, "carrier": carrier, "code": rec["code"], "index": idx, "source": "map", "count": int(used.get(src, 0))}

    if mode == "used-chars":
        for ch, n in sorted(used.items(), key=lambda kv: (-kv[1], ord(kv[0]))):
            if ch in CONTROL_CHARS:
                continue
            if preserve_special_bars and ch in SPECIAL_BAR_CHARS:
                continue
            if ch in mapping:
                continue
            if not is_cp932_covered(ch):
                warnings.append(f"UNMAPPED_UNENCODABLE {ch!r} U+{ord(ch):04X} count={n}")
                continue
            code = cp932_code(ch)
            idx = cp932_index_from_code(code)
            if idx in plan:
                prev = plan[idx]
                warnings.append(f"SLOT_CONFLICT literal {ch!r} count={n} shares index={idx} with mapped {prev['display']!r}->{prev['carrier']!r}; mapped glyph wins")
                continue
            plan[idx] = {"display": ch, "carrier": ch, "code": f"0x{code:04X}", "index": idx, "source": "used", "count": int(n)}
    return plan, warnings, used


def plan_to_mapping(plan: Dict[int, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    # patch_font_file expects display src as key.
    out: Dict[str, Dict[str, Any]] = {}
    for idx, rec in sorted(plan.items()):
        key = rec["display"]
        if key in out:
            key = f"{key}\0{idx}"  # impossible visible char key, keeps uniqueness internally
        out[key] = {"carrier": rec["carrier"], "code": rec["code"], "index": idx, "display": rec["display"], "source": rec.get("source"), "count": rec.get("count", 0)}
    return out


def patch_font_file(
    input_font: Path,
    output_font: Path,
    spec: FontSpec,
    mapping_or_plan: Dict[str, Dict[str, Any]],
    draw_font: Path,
    font_size: Optional[int],
    xoff: int,
    yoff: int,
    bg_nibble: str,
    ink_nibble: int,
    outline_nibble: int,
    stroke_width: int,
    threshold: int,
    fit: bool,
    dry_run: bool,
    anchor: str = "center",
) -> Tuple[int, List[str], int]:
    data = bytearray(input_font.read_bytes())
    validate_font_size(data, spec, input_font)
    bg = detect_bg_nibble(data) if bg_nibble == "auto" else int(bg_nibble, 0)
    logs: List[str] = [f"[{spec.name}] bg_nibble=0x{bg:X} input={input_font} output={output_font}"]
    patched = 0
    used_slots: Dict[int, str] = {}
    for src_key, rec in mapping_or_plan.items():
        src = rec.get("display", src_key.split("\0", 1)[0])
        carrier = rec["carrier"]
        idx = int(rec["index"])
        code = rec["code"]
        if idx in used_slots:
            logs.append(f"COLLISION index={idx}: {used_slots[idx]!r} and {src!r}; skip {src!r}->{carrier!r}")
            continue
        used_slots[idx] = src
        if not dry_run:
            pix = render_char_indexed(src, spec, draw_font, font_size, xoff, yoff, bg, ink_nibble, outline_nibble, stroke_width, threshold, fit, anchor=anchor)
            write_glyph(data, idx, spec, pix)
        logs.append(f"PATCH {src!r}->{carrier!r} {code} index={idx} source={rec.get('source','map')} count={rec.get('count','')}")
        patched += 1
    if not dry_run:
        output_font.parent.mkdir(parents=True, exist_ok=True)
        output_font.write_bytes(data)
    return patched, logs, bg


def apply_map_json(input_path: Path, output_path: Path, mapping: Dict[str, Dict[str, Any]], fields: set[str], compact: bool) -> int:
    table = {src: rec["carrier"] for src, rec in mapping.items()}
    files = iter_json_files(input_path)
    for jp in files:
        obj = read_json(jp)
        out = replace_text_fields(obj, fields, table)
        if input_path.is_file():
            outp = output_path
        else:
            outp = output_path / jp.relative_to(input_path)
        write_json(outp, out, compact=compact)
    return len(files)


def _pick_arg(args: argparse.Namespace, specific: str, common: str) -> Any:
    val = getattr(args, specific, None)
    if val is not None:
        return val
    return getattr(args, common)


def _fit_enabled(args: argparse.Namespace, prefix: str) -> bool:
    specific_no_fit = getattr(args, f"{prefix}_no_fit", False)
    return not (args.no_fit or specific_no_fit)


def patch_fonts_from_args(args: argparse.Namespace, render_mapping: Dict[str, Dict[str, Any]]) -> Tuple[int, List[str]]:
    draw_font = Path(args.font)
    if not draw_font.exists():
        raise FileNotFoundError(draw_font)
    total = 0
    logs: List[str] = []
    if args.go:
        p, l, _ = patch_font_file(
            Path(args.go[0]), Path(args.go[1]), FNT_GO, render_mapping, draw_font,
            args.go_font_size, args.go_x, args.go_y,
            _pick_arg(args, "go_bg_nibble", "bg_nibble"),
            _pick_arg(args, "go_ink_nibble", "ink_nibble"),
            _pick_arg(args, "go_outline_nibble", "outline_nibble"),
            _pick_arg(args, "go_stroke_width", "stroke_width"),
            _pick_arg(args, "go_threshold", "threshold"),
            _fit_enabled(args, "go"), args.dry_run,
            anchor=args.go_anchor,
        )
        total += p; logs.extend(l)
    if args.gos:
        p, l, _ = patch_font_file(
            Path(args.gos[0]), Path(args.gos[1]), FNT_GOS, render_mapping, draw_font,
            args.gos_font_size, args.gos_x, args.gos_y,
            _pick_arg(args, "gos_bg_nibble", "bg_nibble"),
            _pick_arg(args, "gos_ink_nibble", "ink_nibble"),
            _pick_arg(args, "gos_outline_nibble", "outline_nibble"),
            _pick_arg(args, "gos_stroke_width", "stroke_width"),
            _pick_arg(args, "gos_threshold", "threshold"),
            _fit_enabled(args, "gos"), args.dry_run,
            anchor=args.gos_anchor,
        )
        total += p; logs.extend(l)
    if not args.go and not args.gos:
        raise SystemExit("nothing to patch: pass --go IN OUT and/or --gos IN OUT")
    return total, logs


def cmd_collect_chars(args: argparse.Namespace) -> int:
    fields = set(args.fields.split(","))
    cnt = collect_chars_from_json(args.json, fields)
    items = []
    for ch, n in sorted(cnt.items(), key=lambda kv: (-kv[1], ord(kv[0]))):
        enc = is_cp932_covered(ch) if ch not in CONTROL_CHARS else True
        rec: Dict[str, Any] = {"char": ch, "count": n, "cp932": enc}
        if ch not in CONTROL_CHARS and enc:
            code = cp932_code(ch)
            rec.update({"code": f"0x{code:04X}", "index": cp932_index_from_code(code)})
        items.append(rec)
    out = {"fields": sorted(fields), "total_unique": len(items), "unencodable_count": sum(1 for x in items if not x["cp932"]), "chars": items}
    write_json(args.output, out)
    print(json.dumps({"unique": len(items), "unencodable": out["unencodable_count"], "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


def default_protected_carrier(ch: str) -> bool:
    if ch.isspace():
        return True
    o = ord(ch)
    # Preserve ASCII/kana/common punctuation by default when auto-making carrier maps.
    if 0x20 <= o <= 0x7E:
        return True
    if 0x3040 <= o <= 0x30FF:
        return True
    if 0xFF00 <= o <= 0xFFEF:
        return True
    if ch in "　、。，．・：；？！ー―‐…‥‘’“”（）「」『』【】《》〈〉？！♪♥♡☆★○●◎◇◆□■△▲▽▼※":
        return True
    return False


def cmd_make_map(args: argparse.Namespace) -> int:
    fields = set(args.fields.split(","))
    cnt = collect_chars_from_json(args.json, fields)
    need = [ch for ch, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], ord(kv[0]))) if ch not in CONTROL_CHARS and not is_cp932_covered(ch)]
    protected = {ch for ch in cnt if ch not in CONTROL_CHARS and is_cp932_covered(ch)}
    if args.protect_json:
        pc = collect_chars_from_json(args.protect_json, fields)
        protected.update(ch for ch in pc if ch not in CONTROL_CHARS and is_cp932_covered(ch))
    if args.protect:
        protected.update(args.protect)
    carriers = []
    for ch, code, idx in covered_chars():
        if ch in protected:
            continue
        if default_protected_carrier(ch) and not args.allow_symbols:
            continue
        carriers.append((ch, code, idx))
    if len(carriers) < len(need):
        raise SystemExit(f"not enough carrier slots: need {len(need)}, available {len(carriers)}")
    out: Dict[str, Dict[str, Any]] = {}
    for src, (carrier, code, idx) in zip(need, carriers):
        out[src] = {"carrier": carrier, "code": f"0x{code:04X}", "index": idx, "count": cnt[src]}
    if args.format == "map" or (args.format == "auto" and args.output.suffix.lower() == ".map"):
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text("\n".join(f"{k}={v['carrier']}" for k, v in out.items()) + "\n", encoding="utf-8")
    else:
        write_json(args.output, out)
    print(json.dumps({"need": len(need), "available": len(carriers), "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


def cmd_apply_map_json(args: argparse.Namespace) -> int:
    mapping = load_map(args.map, reverse=args.reverse_map)
    fields = set(args.fields.split(","))
    n = apply_map_json(args.input, args.output, mapping, fields, args.compact)
    bad = validate_mapped_json_cp932(args.output, fields) if args.check_cp932 else []
    if bad:
        print(json.dumps({"files": n, "output": str(args.output), "cp932_errors": bad}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"files": n, "output": str(args.output), "cp932_ok": not bad}, ensure_ascii=False, indent=2))
    return 0


def make_plan_from_args(args: argparse.Namespace) -> Tuple[Dict[int, Dict[str, Any]], List[str], Counter, Dict[str, Dict[str, Any]]]:
    mapping = load_map(args.map, reverse=args.reverse_map)
    fields = set(args.fields.split(","))
    mode = args.redraw_mode
    if mode == "auto":
        mode = "used-chars" if getattr(args, "json", None) else "mapped-only"
    plan, warnings, used = build_render_plan(mapping, getattr(args, "json", None), fields, mode, preserve_special_bars=not args.redraw_special_bars)
    return plan, warnings, used, mapping


def cmd_build_render_plan(args: argparse.Namespace) -> int:
    plan, warnings, used, _ = make_plan_from_args(args)
    entries = [plan[i] for i in sorted(plan)]
    out = {"mode": args.redraw_mode, "entries": entries, "entry_count": len(entries), "warnings": warnings, "used_unique": len(used)}
    write_json(args.output, out)
    print(json.dumps({"entry_count": len(entries), "warnings": len(warnings), "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


def cmd_patch_font(args: argparse.Namespace) -> int:
    plan, warnings, _, _ = make_plan_from_args(args)
    render_mapping = plan_to_mapping(plan)
    total, logs = patch_fonts_from_args(args, render_mapping)
    logs = warnings + logs
    if args.log:
        Path(args.log).write_text("\n".join(logs) + "\n", encoding="utf-8")
    else:
        for line in logs[:120]:
            print(line)
        if len(logs) > 120:
            print(f"... {len(logs)-120} more; use --log patch.log")
    print(json.dumps({"patched_slots_total": total, "render_entries": len(plan), "warnings": len(warnings), "dry_run": args.dry_run}, ensure_ascii=False, indent=2))
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    plan, warnings, _, mapping = make_plan_from_args(args)
    render_mapping = plan_to_mapping(plan)
    total, logs = patch_fonts_from_args(args, render_mapping)
    fields = set(args.fields.split(","))
    files = 0
    cp932_errors: List[Dict[str, Any]] = []
    if args.json and args.mapped_json:
        files = apply_map_json(Path(args.json), Path(args.mapped_json), mapping, fields, args.compact)
        logs.append(f"[apply-map-json] files={files} input={args.json} output={args.mapped_json}")
        cp932_errors = validate_mapped_json_cp932(Path(args.mapped_json), fields) if args.check_cp932 else []
        if cp932_errors:
            logs.append(f"[cp932-check] errors={len(cp932_errors)}; first={cp932_errors[0]}")
    logs = warnings + logs
    if args.plan_out:
        write_json(args.plan_out, {"entries": [plan[i] for i in sorted(plan)], "warnings": warnings})
    if args.log:
        Path(args.log).write_text("\n".join(logs) + "\n", encoding="utf-8")
    result = {"patched_slots_total": total, "render_entries": len(plan), "mapped_json_files": files, "warnings": len(warnings), "cp932_errors": cp932_errors[:20], "dry_run": args.dry_run}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if cp932_errors else 0


def cmd_check_map(args: argparse.Namespace) -> int:
    m = load_map(args.map, reverse=args.reverse_map)
    sample = []
    for src, rec in list(m.items())[:args.limit]:
        sample.append({"src": src, "carrier": rec["carrier"], "code": rec["code"], "index": rec["index"]})
    print(json.dumps({"entries": len(m), "sample": sample}, ensure_ascii=False, indent=2))
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    for ch in args.chars:
        code = cp932_code(ch)
        idx = cp932_index_from_code(code)
        print(f"{ch}\tcp932=0x{code:04X}\tindex={idx}")
    return 0


def cmd_dump_png(args: argparse.Namespace) -> int:
    spec = FNT_GO if args.kind == "go" else FNT_GOS
    data = bytearray(Path(args.input).read_bytes())
    validate_font_size(data, spec, Path(args.input))
    bg = detect_bg_nibble(data) if args.bg_nibble == "auto" else int(args.bg_nibble, 0)
    img = Image.new("L", (spec.atlas_w, spec.atlas_h), 0)
    px = img.load()
    for idx in range(EXPECTED_GLYPHS):
        gx, gy = idx % 16, idx // 16
        glyph = read_glyph(data, idx, spec)
        for y in range(spec.glyph_h):
            for x in range(spec.glyph_w):
                v = glyph[y][x]
                px[gx * spec.glyph_w + x, gy * spec.glyph_h + y] = 0 if v == bg else 255
    img.save(args.output)
    print(f"wrote {args.output}, bg_nibble=0x{bg:X}")
    return 0


def add_font_patch_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--map", required=True, type=Path, help="cn_jp map: display/source char -> CP932 carrier char; JSON or text .map")
    p.add_argument("--reverse-map", action="store_true", help="interpret map as carrier -> display and invert it")
    p.add_argument("--json", type=Path, help="translated JSON dir/file used by used-chars full redraw")
    p.add_argument("--fields", default=DEFAULT_FIELDS)
    p.add_argument("--redraw-mode", choices=["auto", "mapped-only", "used-chars"], default="auto", help="auto=used-chars when --json is provided, else mapped-only")
    p.add_argument("--redraw-special-bars", action="store_true", help="also redraw ├┬┤; default preserves their original long-voice glyphs")
    p.add_argument("--font", required=True, help="TTF/OTF/TTC used to draw display chars")
    p.add_argument("--go", nargs=2, metavar=("IN", "OUT"), help="patch Fnt_go.igf 24x24")
    p.add_argument("--gos", nargs=2, metavar=("IN", "OUT"), help="patch Fnt_gos.igf 20x20")
    p.add_argument("--go-font-size", type=int, default=22, help="Fnt_go draw font size; main text usually uses this")
    p.add_argument("--gos-font-size", type=int, default=18, help="Fnt_gos draw font size; small UI text usually uses this")
    p.add_argument("--go-x", type=int, default=0)
    p.add_argument("--go-y", type=int, default=1)
    p.add_argument("--gos-x", type=int, default=0)
    p.add_argument("--gos-y", type=int, default=1)
    p.add_argument("--go-anchor", choices=["center", "top", "bottom", "baseline"], default="center")
    p.add_argument("--gos-anchor", choices=["center", "top", "bottom", "baseline"], default="center")
    p.add_argument("--bg-nibble", default="auto", help="shared blank/background nibble; default auto detects 0xA for this game")
    p.add_argument("--go-bg-nibble", default=None, help="override --bg-nibble for Fnt_go")
    p.add_argument("--gos-bg-nibble", default=None, help="override --bg-nibble for Fnt_gos")
    p.add_argument("--ink-nibble", type=lambda s: int(s, 0), default=0xF, help="shared fill nibble")
    p.add_argument("--go-ink-nibble", type=lambda s: int(s, 0), default=None, help="override fill nibble for Fnt_go")
    p.add_argument("--gos-ink-nibble", type=lambda s: int(s, 0), default=None, help="override fill nibble for Fnt_gos")
    p.add_argument("--outline-nibble", type=lambda s: int(s, 0), default=0x1, help="shared outline nibble")
    p.add_argument("--go-outline-nibble", type=lambda s: int(s, 0), default=None, help="override outline nibble for Fnt_go")
    p.add_argument("--gos-outline-nibble", type=lambda s: int(s, 0), default=None, help="override outline nibble for Fnt_gos")
    p.add_argument("--stroke-width", type=int, default=1, help="shared outline stroke width")
    p.add_argument("--go-stroke-width", type=int, default=None, help="override stroke width for Fnt_go")
    p.add_argument("--gos-stroke-width", type=int, default=None, help="override stroke width for Fnt_gos")
    p.add_argument("--threshold", type=int, default=8, help="shared antialias mask threshold, lower means thicker")
    p.add_argument("--go-threshold", type=int, default=None, help="override threshold for Fnt_go")
    p.add_argument("--gos-threshold", type=int, default=None, help="override threshold for Fnt_gos")
    p.add_argument("--no-fit", action="store_true", help="disable auto shrink-to-fit for both fonts")
    p.add_argument("--go-no-fit", action="store_true", help="disable auto shrink-to-fit for Fnt_go only")
    p.add_argument("--gos-no-fit", action="store_true", help="disable auto shrink-to-fit for Fnt_gos only")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--log")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="CnJpMap workflow and full-glyph Fnt_go/Fnt_gos redraw tool for Arpeggio EVIT/SNC.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect-chars")
    p.add_argument("json", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--fields", default=DEFAULT_FIELDS)
    p.set_defaults(func=cmd_collect_chars)

    p = sub.add_parser("make-map")
    p.add_argument("json", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--fields", default=DEFAULT_FIELDS)
    p.add_argument("--protect-json", type=Path)
    p.add_argument("--protect", default="")
    p.add_argument("--allow-symbols", action="store_true")
    p.add_argument("--format", choices=["auto", "json", "map"], default="auto")
    p.set_defaults(func=cmd_make_map)

    p = sub.add_parser("apply-map-json")
    p.add_argument("input", type=Path)
    p.add_argument("map", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--reverse-map", action="store_true")
    p.add_argument("--fields", default=DEFAULT_FIELDS)
    p.add_argument("--compact", action="store_true")
    p.add_argument("--no-check-cp932", dest="check_cp932", action="store_false")
    p.set_defaults(func=cmd_apply_map_json, check_cp932=True)

    p = sub.add_parser("build-render-plan")
    add_font_patch_args(p)
    p.add_argument("output", type=Path)
    p.set_defaults(func=cmd_build_render_plan)

    p = sub.add_parser("patch-font")
    add_font_patch_args(p)
    p.set_defaults(func=cmd_patch_font)

    p = sub.add_parser("build")
    add_font_patch_args(p)
    p.add_argument("--mapped-json", type=Path, help="output JSON dir/file after carrier replacement")
    p.add_argument("--compact", action="store_true")
    p.add_argument("--plan-out", type=Path)
    p.add_argument("--no-check-cp932", dest="check_cp932", action="store_false")
    p.set_defaults(func=cmd_build, check_cp932=True)

    p = sub.add_parser("check-map")
    p.add_argument("map", type=Path)
    p.add_argument("--reverse-map", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_check_map)

    p = sub.add_parser("index")
    p.add_argument("chars", nargs="+")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("dump-png")
    p.add_argument("--kind", choices=["go", "gos"], required=True)
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--bg-nibble", default="auto")
    p.set_defaults(func=cmd_dump_png)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    # Backward compatible: no subcommand means patch-font.
    if argv and argv[0].startswith("-"):
        argv = ["patch-font"] + argv
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
