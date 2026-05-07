#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Silky BFD24 bitmap-font localization helper.

Workflow:
  1. scan-json  translated_json_dir  charset.json
  2. make-map   charset.json font.bfd replace_map.json
  3. build      font.bfd replace_map.json chinese.ttf font_chs.bfd
  4. inject MES with silky_mes_inject.py --map replace_map.json

BFD24 format confirmed for this title:
  magic[8] = b"BFD24-00"
  u16 width, u16 height, u32 glyph_count
  code_table: glyph_count * 2 bytes, raw CP932 byte order
  plane1: glyph_count * width * height bytes
  plane2: glyph_count * width * height bytes
"""
from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_ENCODING = "cp932"
DEFAULT_MAX_BFD_SIZE = 0x400000
BFD_MAGIC = b"BFD24-00"

PUNCT_MAP = {
    " ": "　",
    "“": "「", "”": "」",
    "‘": "『", "’": "』",
    "«": "「", "»": "」",
    "—": "―",
    "－": "ー",
    "~": "～",
}

FULLWIDTH_MAP = str.maketrans({
    " ": "　",
    **{chr(0x21 + i): chr(0xFF01 + i) for i in range(0x5E)},
})


def normalize_text(text: str, *, ascii_to_fullwidth: bool = True) -> str:
    """Normalize translated text before scan/build/inject/debug.

This must stay identical to the injector-side normalization.  The main purpose
for this title is to keep the script as a CP932 double-byte stream:
ASCII -> fullwidth, common Chinese/English quotes -> Japanese fullwidth quotes,
and dash/tilde variants -> glyphs that are already well handled by the engine.
"""
    out = []
    for ch in text:
        if ch == "\n":
            out.append(ch)
            continue
        ch = PUNCT_MAP.get(ch, ch)
        if ascii_to_fullwidth:
            ch = ch.translate(FULLWIDTH_MAP)
        out.append(ch)
    return "".join(out)


def cp932_bytes(ch: str) -> bytes | None:
    try:
        b = ch.encode(DEFAULT_ENCODING)
    except UnicodeEncodeError:
        return None
    if len(b) != 2:
        return None
    return b


def cp932_hex(ch: str) -> str | None:
    b = cp932_bytes(ch)
    return b.hex().upper() if b is not None else None


def cp932_char_from_hex(hexstr: str) -> str:
    return bytes.fromhex(hexstr).decode(DEFAULT_ENCODING)


def iter_valid_cp932_double_chars() -> Iterable[str]:
    """Yield valid two-byte CP932 chars, preserving raw byte order."""
    lead_ranges = list(range(0x81, 0xA0)) + list(range(0xE0, 0xFD))
    trail_ranges = list(range(0x40, 0x7F)) + list(range(0x80, 0xFD))
    seen: set[str] = set()
    for lead in lead_ranges:
        for trail in trail_ranges:
            raw = bytes([lead, trail])
            try:
                ch = raw.decode(DEFAULT_ENCODING)
            except UnicodeDecodeError:
                continue
            if len(ch) != 1:
                continue
            try:
                if ch.encode(DEFAULT_ENCODING) != raw:
                    continue
            except UnicodeEncodeError:
                continue
            if ch not in seen:
                seen.add(ch)
                yield ch


@dataclass
class BfdFont:
    magic: bytes
    width: int
    height: int
    codes: list[bytes]
    plane1: bytearray
    plane2: bytearray

    @classmethod
    def load(cls, path: Path) -> "BfdFont":
        data = path.read_bytes()
        if len(data) < 16:
            raise ValueError(f"{path}: too small")
        magic = data[:8]
        if magic != BFD_MAGIC:
            raise ValueError(f"{path}: unsupported magic {magic!r}, expected {BFD_MAGIC!r}")
        width, height, count = struct.unpack_from("<HHI", data, 8)
        pixel_count = width * height
        code_off = 16
        plane1_off = code_off + count * 2
        plane2_off = plane1_off + count * pixel_count
        expected = plane2_off + count * pixel_count
        if expected != len(data):
            raise ValueError(f"{path}: size mismatch, header expects {expected}, actual {len(data)}")
        codes = [data[code_off + i * 2: code_off + i * 2 + 2] for i in range(count)]
        return cls(
            magic=magic,
            width=width,
            height=height,
            codes=codes,
            plane1=bytearray(data[plane1_off:plane2_off]),
            plane2=bytearray(data[plane2_off:expected]),
        )

    @property
    def glyph_count(self) -> int:
        return len(self.codes)

    @property
    def glyph_size(self) -> int:
        return self.width * self.height

    def index_of_code(self, code: bytes) -> int | None:
        try:
            return self.codes.index(code)
        except ValueError:
            return None

    def code_set(self) -> set[bytes]:
        return set(self.codes)

    def get_plane1_template(self, template_char: str | None = "あ") -> bytes:
        idx = None
        if template_char:
            b = cp932_bytes(template_char)
            if b is not None:
                idx = self.index_of_code(b)
        if idx is None:
            idx = 0
        s = idx * self.glyph_size
        return bytes(self.plane1[s:s + self.glyph_size])

    def set_or_append_glyph(self, code: bytes, p1: bytes, p2: bytes) -> tuple[str, int]:
        if len(code) != 2:
            raise ValueError("BFD code must be exactly 2 bytes")
        if len(p1) != self.glyph_size or len(p2) != self.glyph_size:
            raise ValueError("glyph plane size mismatch")
        idx = self.index_of_code(code)
        if idx is None:
            idx = len(self.codes)
            self.codes.append(code)
            self.plane1.extend(p1)
            self.plane2.extend(p2)
            return "append", idx
        s = idx * self.glyph_size
        self.plane1[s:s + self.glyph_size] = p1
        self.plane2[s:s + self.glyph_size] = p2
        return "overwrite", idx

    def to_bytes(self) -> bytes:
        count = len(self.codes)
        return b"".join([
            self.magic,
            struct.pack("<HHI", self.width, self.height, count),
            b"".join(self.codes),
            bytes(self.plane1),
            bytes(self.plane2),
        ])

    def save(self, path: Path, *, max_size: int = DEFAULT_MAX_BFD_SIZE) -> None:
        blob = self.to_bytes()
        if len(blob) > max_size:
            raise ValueError(f"rebuilt BFD is {len(blob)} bytes, exceeds max {max_size} bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)


def iter_json_items(path: Path) -> Iterable[dict[str, Any]]:
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    for fp in files:
        doc = json.loads(fp.read_text(encoding="utf-8"))
        items = doc.get("items") if isinstance(doc, dict) else doc
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                yield item


def scan_json_chars(json_path: Path, *, include_name: bool = True, ascii_to_fullwidth: bool = True) -> dict[str, Any]:
    chars: dict[str, int] = {}
    files: set[str] = set()
    item_count = 0
    for item in iter_json_items(json_path):
        item_count += 1
        if item.get("_file"):
            files.add(str(item["_file"]))
        fields = [str(item.get("message", item.get("msg", "")))]
        if include_name and "name" in item:
            fields.append(str(item.get("name", "")))
        for text in fields:
            text = normalize_text(text, ascii_to_fullwidth=ascii_to_fullwidth)
            for ch in text:
                if ch in "\r\n\t":
                    continue
                chars[ch] = chars.get(ch, 0) + 1
    ordered = sorted(chars.items(), key=lambda kv: (-kv[1], kv[0]))
    direct = []
    need_map = []
    for ch, count in ordered:
        b = cp932_bytes(ch)
        ent = {"char": ch, "count": count, "cp932_hex": b.hex().upper() if b else None}
        if b is None:
            need_map.append(ent)
        else:
            direct.append(ent)
    return {
        "format": "silky_bfd_charset_v1",
        "encoding": DEFAULT_ENCODING,
        "normalize_ascii_to_fullwidth": ascii_to_fullwidth,
        "include_name": include_name,
        "files": sorted(files),
        "item_count": item_count,
        "char_count": len(ordered),
        "direct_cp932_count": len(direct),
        "need_map_count": len(need_map),
        "chars": [{"char": ch, "count": count, "cp932_hex": cp932_hex(ch)} for ch, count in ordered],
        "direct_cp932_chars": direct,
        "need_map_chars": need_map,
    }


def load_charset(path: Path) -> list[str]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, dict):
        if isinstance(doc.get("chars"), list):
            out = []
            for x in doc["chars"]:
                if isinstance(x, dict) and x.get("char"):
                    out.append(str(x["char"])[0])
                elif isinstance(x, str) and x:
                    out.append(x[0])
            return out
        if isinstance(doc.get("need_map_chars"), list) or isinstance(doc.get("direct_cp932_chars"), list):
            out = []
            for key in ("direct_cp932_chars", "need_map_chars"):
                for x in doc.get(key, []):
                    if isinstance(x, dict) and x.get("char"):
                        out.append(str(x["char"])[0])
            return out
    if isinstance(doc, list):
        return [str(x)[0] if isinstance(x, str) else str(x.get("char", ""))[0] for x in doc if (isinstance(x, str) and x) or (isinstance(x, dict) and x.get("char"))]
    raise ValueError("unsupported charset JSON")


def load_subs_map(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    if isinstance(doc, dict):
        for k, v in doc.items():
            if isinstance(v, str) and k and v:
                out[str(k)[0]] = v[0]
            elif isinstance(v, dict):
                src = v.get("source_char") or v.get("source") or v.get("src")
                if src and k:
                    out[str(k)[0]] = str(src)[0]
        for key in ("chars", "map", "mappings"):
            obj = doc.get(key)
            if isinstance(obj, list):
                for ent in obj:
                    if not isinstance(ent, dict):
                        continue
                    target = ent.get("target_char") or ent.get("target") or ent.get("char")
                    source = ent.get("source_char") or ent.get("source") or ent.get("src")
                    if target and source:
                        out[str(target)[0]] = str(source)[0]
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    if isinstance(v, str) and k and v:
                        out[str(k)[0]] = v[0]
    elif isinstance(doc, list):
        for ent in doc:
            if isinstance(ent, dict):
                target = ent.get("target_char") or ent.get("target") or ent.get("char")
                source = ent.get("source_char") or ent.get("source") or ent.get("src")
                if target and source:
                    out[str(target)[0]] = str(source)[0]
    return out


def make_replace_map(charset_path: Path, bfd_path: Path, out_path: Path, *, subs_path: Path | None = None, allow_overwrite: bool = False, max_size: int = DEFAULT_MAX_BFD_SIZE, strict_subs: bool = True, allow_subs_direct_collision: bool = False, subs_priority: str = "override") -> dict[str, Any]:
    """Create replace_map.json.

    v6 rule: when --subs is supplied, the table has priority over direct CP932
    preservation.  This matches the CnJpMap workflow: if the user table says
    target '你' uses source '凜', then source code EAA3 is reserved for '你'.
    If '凜' also appears directly in untranslated/translated JSON text, it is
    removed from direct_cp932_chars and a warning is emitted instead of silently
    abandoning the user table and auto-allocating another source such as 錬.

    subs_priority:
      override  - reserve subs source codes and drop conflicting direct chars.
      error     - abort on subs/direct collision.
      ignore    - ignore colliding subs entries and auto-allocate instead.

    The old --allow-subs-direct-collision option is retained as an alias for
    subs_priority='override' for compatibility.
    """
    font = BfdFont.load(bfd_path)
    chars = load_charset(charset_path)
    # Preserve scan ordering, drop duplicates.
    ordered: list[str] = []
    seen: set[str] = set()
    for ch in chars:
        if ch not in seen:
            seen.add(ch)
            ordered.append(ch)

    if allow_subs_direct_collision:
        subs_priority = "override"
    if subs_priority not in {"override", "error", "ignore"}:
        raise ValueError("subs_priority must be override/error/ignore")

    subs = load_subs_map(subs_path)
    warnings: list[str] = []
    subs_errors: list[str] = []

    # Validate and collect explicit subs entries that are relevant to current charset.
    # Map by source code to detect impossible table collisions.
    valid_subs: dict[str, tuple[str, bytes]] = {}
    source_owner: dict[bytes, str] = {}
    for ch in ordered:
        src = subs.get(ch)
        if not src or src == ch:
            continue
        b = cp932_bytes(src)
        if b is None:
            msg = f"subs mapping invalid: {ch!r}->{src!r}, source is not CP932 double-byte"
            if strict_subs:
                subs_errors.append(msg)
            else:
                warnings.append(msg)
            continue
        old = source_owner.get(b)
        if old is not None and old != ch:
            subs_errors.append(
                f"subs source collision: {old!r} and {ch!r} both use {src!r} ({b.hex().upper()})"
            )
            continue
        source_owner[b] = ch
        valid_subs[ch] = (src, b)

    if subs_errors:
        raise SystemExit("\n".join(subs_errors[:50]) + (f"\n... total {len(subs_errors)} subs errors" if len(subs_errors) > 50 else ""))

    reserved_subs_codes = {b for _, b in valid_subs.values()}

    direct: list[dict[str, Any]] = []
    direct_codes: set[bytes] = set()
    need_auto_map: list[str] = []
    entries: list[dict[str, Any]] = []

    # First apply explicit CnJp/subs mappings exactly.
    for ch in ordered:
        if ch in valid_subs:
            src, b = valid_subs[ch]
            entries.append({
                "target_char": ch,
                "source_char": src,
                "source_cp932_hex": b.hex().upper(),
                "mode": "mapped_subs",
                "exists_in_bfd": b in font.code_set(),
            })

    # Then classify chars not covered by subs.
    for ch in ordered:
        if ch in valid_subs:
            continue
        b = cp932_bytes(ch)
        if b is None:
            need_auto_map.append(ch)
            continue
        if b in reserved_subs_codes:
            owner = source_owner[b]
            msg = (
                f"subs source overrides direct char: direct {ch!r} ({b.hex().upper()}) "
                f"conflicts with target {owner!r}; direct char is omitted from direct_cp932_chars"
            )
            if subs_priority == "error":
                subs_errors.append(msg)
                continue
            if subs_priority == "ignore":
                warnings.append(msg + "; ignored subs owner will be auto-mapped if needed")
                # In ignore mode we keep direct and later remove the subs entry for owner.
                # This mode is mainly for compatibility; override is the intended workflow.
                direct_codes.add(b)
                direct.append({
                    "target_char": ch,
                    "source_char": ch,
                    "source_cp932_hex": b.hex().upper(),
                    "mode": "direct",
                    "exists_in_bfd": b in font.code_set(),
                })
            else:
                warnings.append(msg)
            continue
        direct_codes.add(b)
        direct.append({
            "target_char": ch,
            "source_char": ch,
            "source_cp932_hex": b.hex().upper(),
            "mode": "direct",
            "exists_in_bfd": b in font.code_set(),
        })

    if subs_errors:
        raise SystemExit("\n".join(subs_errors[:50]) + (f"\n... total {len(subs_errors)} subs errors" if len(subs_errors) > 50 else ""))

    used_source_codes: set[bytes] = set(direct_codes) | reserved_subs_codes

    # Prefer CP932 double-byte codes not already present in BFD; they can be appended
    # without overwriting original glyphs.  Exclude all direct and subs-reserved codes.
    existing = font.code_set()
    candidate_chars = []
    for src in iter_valid_cp932_double_chars():
        b = cp932_bytes(src)
        if b is None or b in existing or b in used_source_codes:
            continue
        candidate_chars.append(src)

    for ch in list(need_auto_map):
        if not candidate_chars:
            break
        src = candidate_chars.pop(0)
        b = cp932_bytes(src)
        assert b is not None
        entries.append({
            "target_char": ch,
            "source_char": src,
            "source_cp932_hex": b.hex().upper(),
            "mode": "mapped_append",
            "exists_in_bfd": False,
        })
        used_source_codes.add(b)
        need_auto_map.remove(ch)

    if need_auto_map and allow_overwrite:
        # Last resort: overwrite existing BFD codes that are not direct/subs-reserved and not already used.
        for code in font.codes:
            if not need_auto_map:
                break
            if code in used_source_codes or code in direct_codes or code in reserved_subs_codes:
                continue
            try:
                src = code.decode(DEFAULT_ENCODING)
            except UnicodeDecodeError:
                continue
            ch = need_auto_map.pop(0)
            entries.append({
                "target_char": ch,
                "source_char": src,
                "source_cp932_hex": code.hex().upper(),
                "mode": "mapped_overwrite_existing",
                "exists_in_bfd": True,
            })
            used_source_codes.add(code)

    added_count = sum(1 for e in entries + direct if not e.get("exists_in_bfd"))
    projected_count = font.glyph_count + added_count
    projected_size = 16 + projected_count * 2 + projected_count * font.glyph_size * 2

    doc = {
        "format": "silky_bfd_replace_map_v2",
        "encoding": DEFAULT_ENCODING,
        "subs_priority": subs_priority,
        "font": {
            "source": str(bfd_path),
            "magic": font.magic.decode("ascii", errors="replace"),
            "width": font.width,
            "height": font.height,
            "original_glyph_count": font.glyph_count,
            "projected_glyph_count": projected_count,
            "projected_size": projected_size,
            "max_size_without_exe_patch": max_size,
            "needs_exe_read_limit_patch": projected_size > max_size,
        },
        "direct_cp932_chars": direct,
        "chars": entries,
        "unmapped_chars": need_auto_map,
        "warnings": warnings,
        "summary": {
            "direct_cp932": len(direct),
            "mapped": len(entries),
            "mapped_subs": sum(1 for e in entries if e.get("mode") == "mapped_subs"),
            "unmapped": len(need_auto_map),
            "append_glyphs": added_count,
            "overwrite_glyphs": sum(1 for e in entries + direct if e.get("exists_in_bfd")),
            "subs_direct_overrides": sum(1 for w in warnings if w.startswith("subs source overrides direct char")),
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    if need_auto_map:
        raise SystemExit(f"unmapped chars remain: {''.join(need_auto_map[:50])} (total {len(need_auto_map)}); use --allow-overwrite or provide --subs")
    if projected_size > max_size:
        raise SystemExit(f"projected BFD size {projected_size} exceeds {max_size}; reduce chars or patch EXE read limit")
    return doc

def load_replace_entries(path: Path) -> list[dict[str, Any]]:
    """Load replace entries while preserving mode/direct metadata.

    Returned entries always contain:
      target_char, source_char, is_direct, mode
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = []

    def add(target: Any, source: Any = None, *, mode: str = "", is_direct: bool | None = None) -> None:
        if target is None:
            return
        t = str(target)
        if not t:
            return
        s = str(source) if source is not None else t
        if not s:
            return
        target_ch = t[0]
        source_ch = s[0]
        direct = (target_ch == source_ch) if is_direct is None else bool(is_direct)
        entries.append({
            "target_char": target_ch,
            "source_char": source_ch,
            "is_direct": direct,
            "mode": mode or ("direct" if direct else "mapped"),
        })

    if isinstance(doc, dict):
        for ent in doc.get("direct_cp932_chars", []):
            if isinstance(ent, dict):
                add(ent.get("target_char") or ent.get("char"), ent.get("source_char") or ent.get("char"), mode=ent.get("mode") or "direct", is_direct=True)
            elif isinstance(ent, str):
                add(ent, ent, mode="direct", is_direct=True)
        for ent in doc.get("chars", []):
            if isinstance(ent, dict):
                add(ent.get("target_char") or ent.get("target") or ent.get("char"), ent.get("source_char") or ent.get("source") or ent.get("src"), mode=ent.get("mode") or "mapped", is_direct=False)
            elif isinstance(ent, str):
                add(ent, ent)
        if not entries:
            for k, v in doc.items():
                if isinstance(v, str) and k and v:
                    add(k, v, is_direct=(k[0] == v[0]))
    elif isinstance(doc, list):
        for ent in doc:
            if isinstance(ent, dict):
                target = ent.get("target_char") or ent.get("target") or ent.get("char")
                source = ent.get("source_char") or ent.get("source") or ent.get("src") or target
                add(target, source, mode=ent.get("mode") or "", is_direct=(str(target or "")[:1] == str(source or "")[:1]))
    # Deduplicate by source CP932 code.  Keep mapped entries stronger than direct.
    # Duplicates with the same target/source are harmless; conflicting duplicates are rejected in build_bfd.
    uniq: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str, bool]] = set()
    for e in entries:
        key = (e["target_char"], e["source_char"], bool(e.get("is_direct")))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        uniq.append(e)
    return uniq


def load_replace_dict(path: Path) -> dict[str, str]:
    return {e["target_char"]: e["source_char"] for e in load_replace_entries(path)}


def inspect_map(map_path: Path, chars: str) -> list[dict[str, Any]]:
    repl = load_replace_dict(map_path)
    out = []
    for ch in chars:
        src = repl.get(ch)
        src_b = cp932_bytes(src) if src else None
        direct_b = cp932_bytes(ch)
        out.append({
            "target_char": ch,
            "source_char": src,
            "source_cp932_hex": src_b.hex().upper() if src_b else None,
            "direct_cp932_hex": direct_b.hex().upper() if direct_b else None,
            "mapped": bool(src and src != ch),
            "present_in_map": src is not None,
        })
    return out


def map_preview_chars(chars: list[str], map_path: Path | None) -> list[str]:
    if not map_path:
        return chars
    repl = load_replace_dict(map_path)
    return [repl.get(ch, ch) for ch in chars]


def _is_cjk_char(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch[0])
    return (0x3400 <= o <= 0x4DBF) or (0x4E00 <= o <= 0x9FFF) or (0xF900 <= o <= 0xFAFF)


def _offset_from_bbox(canvas: int, bbox: tuple[int, int, int, int], *, x_offset: int, y_offset: int) -> tuple[int, int]:
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (canvas - w) // 2 - bbox[0] + x_offset
    y = (canvas - h) // 2 - bbox[1] + y_offset
    return x, y


def render_glyph_planes(
    ch: str,
    font_path: Path,
    *,
    canvas: int,
    size: int,
    x_offset: int = 0,
    y_offset: int = 0,
    bg_value: int = 12,
    mode: str = "bfd24",
) -> tuple[bytes, bytes]:
    """Render one BFD24 glyph as (plane1, plane2).

    The engine copies plane1 into the RGB surface and plane2 into a mask/coverage
    surface.  Original BFD glyphs are not stored as "plane1 template + white
    mask".  They look like this:

      plane1: dark background, bright glyph strokes
      plane2: bright background, dark/inverted glyph coverage

    The previous implementation reused the plane1 of "あ" for every generated
    glyph and wrote a normal white-on-black mask into plane2.  That makes the
    game draw repeated "あ"/black boxes and looks like an index shift, although
    the table indexes are actually aligned.
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
    except Exception as e:  # pragma: no cover
        raise RuntimeError("Pillow is required: pip install pillow") from e

    scale = 4
    big = canvas * scale
    font = ImageFont.truetype(str(font_path), size * scale)
    mask_big = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(mask_big)
    bbox = draw.textbbox((0, 0), ch, font=font)
    if not bbox:
        return bytes([bg_value]) * (canvas * canvas), bytes([255]) * (canvas * canvas)
    x, y = _offset_from_bbox(big, bbox, x_offset=x_offset * scale, y_offset=y_offset * scale)
    draw.text((x, y), ch, fill=255, font=font)
    mask = mask_big.resize((canvas, canvas), Image.Resampling.LANCZOS)

    # plane1 is visible grayscale.  Keep the same non-zero dark floor as the
    # original BFD so the renderer/mask path behaves like stock glyphs.
    cov = mask.tobytes()
    p1 = bytes(bg_value + ((255 - bg_value) * v // 255) for v in cov)

    # plane2 is inverse coverage.  A slight blur gives anti-aliased edges close
    # to the stock BFD mask, without making the whole 24x24 cell opaque.
    if mode == "hard":
        p2 = bytes(0 if v else 255 for v in cov)
    else:
        inv = Image.eval(mask.filter(ImageFilter.GaussianBlur(radius=0.35)), lambda v: 255 - v)
        p2 = inv.tobytes()
    return p1, p2


def render_glyph_plane2(ch: str, font_path: Path, *, canvas: int, size: int, x_offset: int = 0, y_offset: int = 0) -> bytes:
    # Backward compatible helper for older callers.
    return render_glyph_planes(ch, font_path, canvas=canvas, size=size, x_offset=x_offset, y_offset=y_offset)[1]


def build_bfd(src_bfd: Path, map_path: Path, font_path: Path, out_bfd: Path, *, size: int = 22, template_char: str = "あ", max_size: int = DEFAULT_MAX_BFD_SIZE, x_offset: int = 0, y_offset: int = 0, preview: Path | None = None, render_mode: str = "bfd24") -> dict[str, Any]:
    bfd = BfdFont.load(src_bfd)
    if bfd.width != bfd.height:
        raise ValueError("current renderer assumes square glyph cells")
    entries = load_replace_entries(map_path)

    # Prevent the same CP932 source code from being assigned to two different
    # target glyphs.  If this happens the engine's linear lookup can only show
    # one glyph, which looks like table/index corruption in game.
    code_owner: dict[bytes, str] = {}
    changed = []
    render_cache: dict[str, tuple[bytes, bytes]] = {}
    for ent in entries:
        target = ent["target_char"]
        source = ent["source_char"]
        code = cp932_bytes(source)
        if code is None:
            raise ValueError(f"source char {source!r} is not a CP932 two-byte char")
        if code in code_owner and code_owner[code] != target:
            raise ValueError(
                f"source CP932 code collision: {code.hex().upper()} is used for "
                f"{code_owner[code]!r} and {target!r}. Rebuild replace_map."
            )
        code_owner[code] = target

        is_direct = bool(ent.get("is_direct")) or target == source
        existing_idx = bfd.index_of_code(code)
        # Direct non-CJK punctuation/marks should keep the stock glyph when it exists;
        # overwriting punctuation with a Chinese TTF is what causes bad baseline/spacing.
        if is_direct and existing_idx is not None and not _is_cjk_char(target):
            changed.append({
                "target_char": target,
                "source_char": source,
                "source_cp932_hex": code.hex().upper(),
                "mode": "keep_direct_stock",
                "index": existing_idx,
            })
            continue

        if target not in render_cache:
            render_cache[target] = render_glyph_planes(
                target, font_path, canvas=bfd.width, size=size,
                x_offset=x_offset, y_offset=y_offset, mode=render_mode,
            )
        plane1, plane2 = render_cache[target]
        mode, idx = bfd.set_or_append_glyph(code, plane1, plane2)
        changed.append({
            "target_char": target,
            "source_char": source,
            "source_cp932_hex": code.hex().upper(),
            "mode": ("direct_" if is_direct else "mapped_") + mode,
            "index": idx,
        })
    bfd.save(out_bfd, max_size=max_size)
    report = {
        "source_bfd": str(src_bfd),
        "output_bfd": str(out_bfd),
        "font": str(font_path),
        "font_size": size,
        "render_mode": render_mode,
        "original_count": BfdFont.load(src_bfd).glyph_count,
        "new_count": bfd.glyph_count,
        "output_size": out_bfd.stat().st_size,
        "changed": changed,
    }
    report_path = out_bfd.with_suffix(out_bfd.suffix + ".manifest.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if preview:
        make_preview(out_bfd, preview, [e["source_char"] for e in changed[:120]])
    return report


def dump_table(bfd_path: Path, out_path: Path) -> None:
    bfd = BfdFont.load(bfd_path)
    lines = ["index\tcp932_hex\tchar\n"]
    for i, code in enumerate(bfd.codes):
        try:
            ch = code.decode(DEFAULT_ENCODING)
        except UnicodeDecodeError:
            ch = ""
        lines.append(f"{i}\t{code.hex().upper()}\t{ch}\n")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(lines), encoding="utf-8")


def inspect_chars(bfd_path: Path, chars: str) -> list[dict[str, Any]]:
    bfd = BfdFont.load(bfd_path)
    out = []
    for ch in chars:
        b = cp932_bytes(ch)
        out.append({
            "char": ch,
            "cp932_hex": b.hex().upper() if b else None,
            "index": bfd.index_of_code(b) if b else None,
            "exists": (bfd.index_of_code(b) is not None) if b else False,
        })
    return out


def make_preview(bfd_path: Path, out_path: Path, chars: list[str] | None = None, *, map_path: Path | None = None) -> None:
    try:
        from PIL import Image, ImageDraw
    except Exception as e:
        raise RuntimeError("Pillow is required: pip install pillow") from e
    bfd = BfdFont.load(bfd_path)
    if not chars:
        chars = []
        for code in bfd.codes[:120]:
            try:
                chars.append(code.decode(DEFAULT_ENCODING))
            except UnicodeDecodeError:
                pass
    chars = map_preview_chars(chars, map_path)
    cols = 20
    cell = bfd.width + 8
    rows = max(1, (len(chars) + cols - 1) // cols)
    img = Image.new("L", (cols * cell, rows * cell), 0)
    for n, ch in enumerate(chars):
        b = cp932_bytes(ch)
        if b is None:
            continue
        idx = bfd.index_of_code(b)
        if idx is None:
            continue
        s = idx * bfd.glyph_size
        glyph = Image.frombytes("L", (bfd.width, bfd.height), bytes(bfd.plane2[s:s + bfd.glyph_size]))
        x = (n % cols) * cell + 4
        y = (n // cols) * cell + 4
        img.paste(glyph, (x, y))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)



def debug_encode(map_path: Path, text: str, *, bfd_path: Path | None = None, ascii_to_fullwidth: bool = True) -> dict[str, Any]:
    repl = load_replace_dict(map_path)
    normalized = normalize_text(text, ascii_to_fullwidth=ascii_to_fullwidth)
    source_chars = [repl.get(ch, ch) for ch in normalized]
    source = "".join(source_chars)
    try:
        encoded = source.encode(DEFAULT_ENCODING, errors="strict")
        encode_error = None
    except UnicodeEncodeError as e:
        encoded = b""
        encode_error = str(e)

    bfd = BfdFont.load(bfd_path) if bfd_path else None
    rows = []
    for target, source_ch in zip(normalized, source_chars):
        if target == "\n":
            rows.append({"target_char": "\\n", "source_char": "\\n", "mapped": False})
            continue
        b = cp932_bytes(source_ch)
        idx = bfd.index_of_code(b) if (bfd and b) else None
        rows.append({
            "target_char": target,
            "source_char": source_ch,
            "mapped": target != source_ch,
            "source_cp932_hex": b.hex().upper() if b else None,
            "bfd_index": idx,
            "exists_in_bfd": (idx is not None) if bfd else None,
        })
    return {
        "original": text,
        "normalized": normalized,
        "source": source,
        "bytes_hex": encoded.hex(" ").upper() if not encode_error else None,
        "encode_error": encode_error,
        "chars": rows,
    }


def export_reverse_map(map_path: Path, out_path: Path) -> dict[str, Any]:
    """Export source->target map for CreateFont/TextOut-side hooks.

    BFD path: MES contains source chars; rebuilt font.bfd draws target glyphs.
    GDI path: there is no BFD redraw, so a hook should translate source chars
    back to target chars before TextOutA renders with a Chinese-capable font.
    """
    entries = load_replace_entries(map_path)
    table: dict[str, str] = {}
    collisions: list[dict[str, str]] = []
    for e in entries:
        target = e["target_char"]
        source = e["source_char"]
        if target == source:
            continue
        if source in table and table[source] != target:
            collisions.append({"source_char": source, "old_target": table[source], "new_target": target})
            continue
        table[source] = target
    doc = {
        "format": "silky_bfd_reverse_map_v1",
        "encoding": DEFAULT_ENCODING,
        "direction": "source_char_to_target_char",
        "map": table,
        "collisions": collisions,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def print_debug_encode(report: dict[str, Any]) -> None:
    print("original  :", report["original"])
    print("normalized:", report["normalized"])
    print("source    :", report["source"])
    if report.get("encode_error"):
        print("encode err:", report["encode_error"])
    else:
        print("bytes     :", report["bytes_hex"])
    print("\nper char:")
    for r in report["chars"]:
        if r["target_char"] == "\\n":
            print("\\n")
            continue
        extra = ""
        if r.get("exists_in_bfd") is not None:
            extra = f" index={r.get('bfd_index')} exists={r.get('exists_in_bfd')}"
        print(f"{r['target_char']} -> {r['source_char']} -> {r.get('source_cp932_hex')}{extra}")

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Silky BFD24 font localization helper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan-json", help="scan translated JSON and collect chars")
    p.add_argument("json", help="translation JSON file or directory")
    p.add_argument("output", help="output charset JSON")
    p.add_argument("--no-name", action="store_true", help="do not scan speaker name fields")
    p.add_argument("--keep-ascii", action="store_true", help="do not normalize ASCII to full-width")

    p = sub.add_parser("make-map", help="make CP932 replace_map for BFD")
    p.add_argument("charset")
    p.add_argument("bfd")
    p.add_argument("output")
    p.add_argument("--subs", help="optional target->source CnJp/subs JSON")
    p.add_argument("--no-strict-subs", action="store_true", help="allow invalid/colliding --subs entries to be skipped instead of erroring")
    p.add_argument("--allow-subs-direct-collision", action="store_true", help="dangerous: allow a --subs source char that is also used directly in translation")
    p.add_argument("--subs-priority", default="override", choices=["override", "error", "ignore"], help="how to handle --subs source chars that also appear directly in the translation; default override follows the CnJp table")
    p.add_argument("--allow-overwrite", action="store_true", help="allow overwriting existing BFD glyph slots if append candidates are exhausted")
    p.add_argument("--max-size", type=lambda s: int(s, 0), default=DEFAULT_MAX_BFD_SIZE)

    p = sub.add_parser("build", help="rebuild font.bfd from replace_map and TTF/TTC")
    p.add_argument("bfd")
    p.add_argument("map")
    p.add_argument("font")
    p.add_argument("output")
    p.add_argument("--size", type=int, default=22)
    p.add_argument("--template-char", default="あ")
    p.add_argument("--max-size", type=lambda s: int(s, 0), default=DEFAULT_MAX_BFD_SIZE)
    p.add_argument("--x-offset", type=int, default=0)
    p.add_argument("--y-offset", type=int, default=0)
    p.add_argument("--preview", help="write glyph preview PNG")
    p.add_argument("--render-mode", choices=["bfd24", "hard"], default="bfd24", help="glyph plane generation mode; bfd24 matches original two-plane layout")

    p = sub.add_parser("dump-table")
    p.add_argument("bfd")
    p.add_argument("output")

    p = sub.add_parser("inspect")
    p.add_argument("bfd")
    p.add_argument("chars")

    p = sub.add_parser("inspect-map", help="inspect target->source mappings in replace_map.json")
    p.add_argument("map")
    p.add_argument("chars")

    p = sub.add_parser("debug-encode", help="show normalized text, borrowed CP932 source chars and bytes")
    p.add_argument("map")
    p.add_argument("text")
    p.add_argument("--bfd", help="optional BFD to verify each source code exists")
    p.add_argument("--keep-ascii", action="store_true", help="do not normalize ASCII/basic punctuation to full-width")

    p = sub.add_parser("export-reverse-map", help="export source->target JSON for GDI/TextOut hook")
    p.add_argument("map")
    p.add_argument("output")

    p = sub.add_parser("preview")
    p.add_argument("bfd")
    p.add_argument("output")
    p.add_argument("--chars", default="")
    p.add_argument("--map", help="optional replace_map.json; preview target chars through source CP932 mapping")

    args = ap.parse_args(argv)
    if args.cmd == "scan-json":
        doc = scan_json_chars(Path(args.json), include_name=not args.no_name, ascii_to_fullwidth=not args.keep_ascii)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"scanned {doc['char_count']} chars; need_map={doc['need_map_count']} -> {args.output}")
        return 0
    if args.cmd == "make-map":
        doc = make_replace_map(Path(args.charset), Path(args.bfd), Path(args.output), subs_path=Path(args.subs) if args.subs else None, allow_overwrite=args.allow_overwrite, max_size=args.max_size, strict_subs=not args.no_strict_subs, allow_subs_direct_collision=args.allow_subs_direct_collision, subs_priority=args.subs_priority)
        print(f"mapped={doc['summary']['mapped']} direct={doc['summary']['direct_cp932']} append={doc['summary']['append_glyphs']} -> {args.output}")
        return 0
    if args.cmd == "build":
        report = build_bfd(Path(args.bfd), Path(args.map), Path(args.font), Path(args.output), size=args.size, template_char=args.template_char, max_size=args.max_size, x_offset=args.x_offset, y_offset=args.y_offset, preview=Path(args.preview) if args.preview else None, render_mode=args.render_mode)
        print(f"rebuilt BFD: count {report['original_count']} -> {report['new_count']}, size={report['output_size']} -> {args.output}")
        return 0
    if args.cmd == "dump-table":
        dump_table(Path(args.bfd), Path(args.output))
        print(f"wrote {args.output}")
        return 0
    if args.cmd == "inspect":
        print(json.dumps(inspect_chars(Path(args.bfd), args.chars), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "inspect-map":
        print(json.dumps(inspect_map(Path(args.map), args.chars), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "debug-encode":
        rep = debug_encode(Path(args.map), args.text, bfd_path=Path(args.bfd) if args.bfd else None, ascii_to_fullwidth=not args.keep_ascii)
        print_debug_encode(rep)
        return 0
    if args.cmd == "export-reverse-map":
        doc = export_reverse_map(Path(args.map), Path(args.output))
        print(json.dumps({"output": args.output, "mapped": len(doc["map"]), "collisions": len(doc["collisions"])}, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "preview":
        make_preview(Path(args.bfd), Path(args.output), list(args.chars) if args.chars else None, map_path=Path(args.map) if args.map else None)
        print(f"wrote {args.output}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
