#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kankin_font_patch.py

Font.dat patcher for kankin.exe style YOX bitmap fonts.

Correct CnJpMap workflow:
  1. Scan translation JSON files and collect characters in selected text fields
     that cannot be encoded by CP932/SJIS.
  2. Resolve those Chinese characters through cn_jp.json, whose format is:
        {"这": "這", "说": "説", "你": "凜", ...}
     The right side is the surrogate character actually written into scripts.
  3. Convert the surrogate character to its CP932 code.
  4. Find the corresponding glyph_index in each YOX font entry by the original
     range_mask/indexing model.
  5. Redraw that surrogate glyph slot with the left-side Chinese character.

This tool does not allocate E040 slots by itself unless your cn_jp.json maps to
those characters/codes. It patches only mappings that are actually used by the
translation JSON scan, unless --patch-all-map is specified.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception as exc:  # pragma: no cover
    raise SystemExit("Pillow is required: pip install pillow") from exc

MAGIC_YOX = 0x00584F59
ALIGN = 0x800
DEFAULT_FIELDS = ("name", "message")

# The game font uses contiguous integer ranges. Do NOT skip 0x7F here.
CP932_RANGES: List[Tuple[int, int, int, str]] = [
    (0, 0x0020, 0x007E, "ascii"),
    (1, 0x00A1, 0x00DF, "hankaku_kana"),
    (2, 0x8140, 0x84BE, "jis_symbols_kana"),
    (3, 0x889F, 0x9872, "jis_kanji_1a"),
    (4, 0x989F, 0x9FFC, "jis_kanji_1b"),
    (5, 0xE040, 0xEAA4, "ibm_nec_ext"),
]


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def p32(v: int) -> bytes:
    return struct.pack("<I", v)


def align_up(n: int, a: int = ALIGN) -> int:
    return (n + a - 1) // a * a


def is_cp932_encodable(ch: str) -> bool:
    try:
        ch.encode("cp932")
        return True
    except UnicodeEncodeError:
        return False


def char_to_cp932_code(ch: str) -> int:
    raw = ch.encode("cp932")
    if len(raw) == 1:
        return raw[0]
    if len(raw) == 2:
        return (raw[0] << 8) | raw[1]
    raise ValueError(f"character must encode to 1 or 2 CP932 bytes: {ch!r}")


def parse_code_or_char(value: Any) -> Tuple[str, int]:
    """Return display surrogate string and CP932 code from mapping value.

    cn_jp.json normally uses a one-character CP932-encodable surrogate:
      {"这": "這"}

    For debugging/compatibility it also accepts hex string values:
      {"你": "E040"}
    """
    if isinstance(value, int):
        if not (0 <= value <= 0xFFFF):
            raise ValueError(f"code out of range: {value!r}")
        return f"0x{value:04X}", value
    if not isinstance(value, str):
        raise ValueError(f"unsupported mapping value: {value!r}")
    s = value.strip()
    s2 = s[2:] if s.lower().startswith("0x") else s
    if len(s2) == 4 and all(c in "0123456789abcdefABCDEF" for c in s2):
        return s, int(s2, 16)
    if not s:
        raise ValueError("empty mapping value")
    if len(s) != 1:
        raise ValueError(f"mapping value must be one CP932 character or 4-digit hex code: {value!r}")
    return s, char_to_cp932_code(s)


def load_cn_jp(path: Path) -> Dict[str, Tuple[str, int]]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    result: Dict[str, Tuple[str, int]] = {}

    if isinstance(obj, dict):
        for cn, surrogate in obj.items():
            if not isinstance(cn, str) or len(cn) != 1:
                raise ValueError(f"left side must be a single Chinese character: {cn!r}")
            result[cn] = parse_code_or_char(surrogate)
        return result

    if isinstance(obj, list):
        for item in obj:
            if not isinstance(item, dict):
                raise ValueError("list mapping entries must be objects")
            cn = item.get("src", item.get("cn", item.get("char", item.get("source"))))
            dst = item.get("dst", item.get("jp", item.get("code", item.get("target"))))
            if not isinstance(cn, str) or len(cn) != 1 or dst is None:
                raise ValueError(f"bad mapping entry: {item!r}")
            result[cn] = parse_code_or_char(dst)
        return result

    raise ValueError("cn_jp.json must be a dict or a list")


def code_to_glyph_index(code: int, range_mask: int) -> Optional[int]:
    idx = 0
    for bit, start, end, _name in CP932_RANGES:
        if not (range_mask & (1 << bit)):
            continue
        count = end - start + 1
        if start <= code <= end:
            return idx + (code - start)
        idx += count
    return None


def glyph_count_for_mask(range_mask: int) -> int:
    return sum((end - start + 1) for bit, start, end, _ in CP932_RANGES if range_mask & (1 << bit))


def iter_json_files(paths: Sequence[str]) -> List[Path]:
    out: List[Path] = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            out.extend(sorted(x for x in path.rglob("*.json") if x.is_file()))
        elif path.is_file():
            out.append(path)
        else:
            raise FileNotFoundError(p)
    # de-duplicate while preserving order
    seen = set()
    uniq = []
    for x in out:
        key = str(x.resolve())
        if key not in seen:
            uniq.append(x)
            seen.add(key)
    return uniq


def collect_unencodable_from_obj(obj: Any, fields: set[str], stats: Dict[str, Any], source: Path) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in fields and isinstance(v, str):
                for ch in v:
                    if not is_cp932_encodable(ch):
                        stats.setdefault("chars", {}).setdefault(ch, 0)
                        stats["chars"][ch] += 1
                        stats.setdefault("locations", {}).setdefault(ch, [])
                        if len(stats["locations"][ch]) < 20:
                            loc = {"file": str(source), "field": k}
                            if "id" in obj:
                                loc["id"] = obj["id"]
                            if "_index" in obj:
                                loc["_index"] = obj["_index"]
                            stats["locations"][ch].append(loc)
            collect_unencodable_from_obj(v, fields, stats, source)
    elif isinstance(obj, list):
        for v in obj:
            collect_unencodable_from_obj(v, fields, stats, source)


def scan_translation_json(paths: Sequence[str], fields: Sequence[str]) -> Dict[str, Any]:
    files = iter_json_files(paths)
    stats: Dict[str, Any] = {"files": [str(p) for p in files], "chars": {}, "locations": {}}
    field_set = set(fields)
    for path in files:
        obj = json.loads(path.read_text(encoding="utf-8"))
        collect_unencodable_from_obj(obj, field_set, stats, path)
    return stats


def replace_text_fields(obj: Any, fields: set[str], mapping: Dict[str, Tuple[str, int]]) -> Any:
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            if k in fields and isinstance(v, str):
                new[k] = "".join(mapping[ch][0] if ch in mapping else ch for ch in v)
            else:
                new[k] = replace_text_fields(v, fields, mapping)
        return new
    if isinstance(obj, list):
        return [replace_text_fields(v, fields, mapping) for v in obj]
    return obj


@dataclass
class DatEntry:
    offset: int
    size: int
    flag: int
    reserved: int
    data: bytes


@dataclass
class YoxFont:
    data: bytes
    magic: int
    version: int
    range_mask: int
    bitmap_size: int
    index_off: int
    width: int
    height: int
    glyph_count: int
    width_off: int
    index: List[Tuple[int, int]]
    width_table: Optional[bytes]

    @classmethod
    def parse(cls, data: bytes) -> "YoxFont":
        if len(data) < 0x20 or u32(data, 0) != MAGIC_YOX:
            raise ValueError("not an inner YOX font")
        version = u32(data, 4)
        range_mask = u32(data, 8)
        bitmap_size = u32(data, 12)
        index_off = u32(data, 16)
        wh = u32(data, 20)
        width = wh & 0xFFFF
        height = (wh >> 16) & 0xFFFF
        glyph_count = u32(data, 24)
        width_off = u32(data, 28)
        if index_off + glyph_count * 8 > len(data):
            raise ValueError("inner YOX index table outside entry")
        index = []
        for i in range(glyph_count):
            off = u32(data, index_off + i * 8)
            size = u32(data, index_off + i * 8 + 4)
            index.append((off, size))
        width_table = None
        if width_off:
            end = index_off
            if width_off < len(data):
                width_table = data[width_off:end]
        return cls(data, MAGIC_YOX, version, range_mask, bitmap_size, index_off, width, height, glyph_count, width_off, index, width_table)

    def rebuild_with_replacements(self, replacements: Dict[int, bytes]) -> bytes:
        glyph_blocks: List[bytes] = []
        new_index: List[Tuple[int, int]] = []
        pos = 0
        bitmap_base = 0x20

        for gi, (old_off, old_size) in enumerate(self.index):
            if gi in replacements:
                raw = replacements[gi]
                if len(raw) != self.width * self.height:
                    raise ValueError(f"glyph {gi}: raw size {len(raw)} != {self.width*self.height}")
                payload = zlib.compress(raw, level=9)
                block = b"YOX\0" + p32(2) + p32(len(raw)) + p32(0) + payload
            else:
                start = bitmap_base + old_off
                end = start + old_size
                if end > len(self.data):
                    raise ValueError(f"glyph {gi}: old block outside data")
                block = self.data[start:end]
            glyph_blocks.append(block)
            new_index.append((pos, len(block)))
            pos += len(block)

        bitmap = b"".join(glyph_blocks)
        new_bitmap_size = len(bitmap)
        new_index_off = 0x20 + new_bitmap_size
        index_bytes = bytearray()
        for off, size in new_index:
            index_bytes += p32(off) + p32(size)

        width_table = self.width_table or b""
        new_width_off = 0
        tail = b""
        if width_table:
            new_width_off = new_index_off + len(index_bytes)
            tail = width_table

        header = bytearray(0x20)
        struct.pack_into("<IIIIIIII", header, 0,
                         MAGIC_YOX,
                         self.version,
                         self.range_mask,
                         new_bitmap_size,
                         new_index_off,
                         self.width | (self.height << 16),
                         self.glyph_count,
                         new_width_off)
        return bytes(header) + bitmap + bytes(index_bytes) + tail


class FontDat:
    def __init__(self, data: bytes):
        self.data = data
        if len(data) < 16 or u32(data, 0) != MAGIC_YOX:
            raise ValueError("not an outer YOX archive")
        self.unknown = u32(data, 4)
        self.table_off = u32(data, 8)
        self.count = u32(data, 12)
        if self.table_off + self.count * 16 > len(data):
            raise ValueError("archive table outside file")
        self.entries: List[DatEntry] = []
        for i in range(self.count):
            off = u32(data, self.table_off + i * 16)
            size = u32(data, self.table_off + i * 16 + 4)
            flag = u32(data, self.table_off + i * 16 + 8)
            reserved = u32(data, self.table_off + i * 16 + 12)
            self.entries.append(DatEntry(off, size, flag, reserved, data[off:off+size]))

    def rebuild(self, new_datas: Dict[int, bytes]) -> bytes:
        out = bytearray()
        out += p32(MAGIC_YOX) + p32(self.unknown) + b"\0" * 8
        if len(out) < ALIGN:
            out += b"\0" * (ALIGN - len(out))

        new_entries_meta: List[Tuple[int, int, int, int]] = []
        for i, ent in enumerate(self.entries):
            if len(out) % ALIGN:
                out += b"\0" * (align_up(len(out)) - len(out))
            off = len(out)
            blob = new_datas.get(i, ent.data)
            out += blob
            new_entries_meta.append((off, len(blob), ent.flag, ent.reserved))

        if len(out) % ALIGN:
            out += b"\0" * (align_up(len(out)) - len(out))
        table_off = len(out)
        for off, size, flag, reserved in new_entries_meta:
            out += p32(off) + p32(size) + p32(flag) + p32(reserved)

        extra_start = self.table_off + self.count * 16
        if extra_start < len(self.data):
            out += self.data[extra_start:]

        struct.pack_into("<I", out, 8, table_off)
        struct.pack_into("<I", out, 12, self.count)
        return bytes(out)


def render_glyph(char: str, width: int, height: int, font_path: Path,
                 size: Optional[int], x_offset: int, y_offset: int,
                 stroke_width: int = 0, scale: float = 1.0) -> bytes:
    base_size = size or max(1, int(height * scale))
    font = ImageFont.truetype(str(font_path), base_size)
    img = Image.new("L", (width, height), 0)

    probe_size = max(width, height, base_size) * 5
    probe = Image.new("L", (probe_size, probe_size), 0)
    pdraw = ImageDraw.Draw(probe)
    pdraw.text((probe_size // 2, probe_size // 2), char, font=font, fill=255,
               stroke_width=stroke_width, stroke_fill=255)
    bbox = probe.getbbox()
    if bbox is None:
        return bytes(width * height)
    glyph_w = bbox[2] - bbox[0]
    glyph_h = bbox[3] - bbox[1]
    cropped = probe.crop(bbox)
    paste_x = (width - glyph_w) // 2 + x_offset
    paste_y = (height - glyph_h) // 2 + y_offset
    img.paste(cropped, (paste_x, paste_y))
    return img.tobytes()


def parse_slots(s: str, archive: FontDat) -> List[int]:
    if s == "auto":
        slots = []
        for i, ent in enumerate(archive.entries):
            try:
                YoxFont.parse(ent.data)
            except Exception:
                continue
            slots.append(i)
        return slots
    out = []
    for part in s.split(","):
        part = part.strip()
        if part:
            out.append(int(part, 0))
    return out


def build_used_mapping(args: argparse.Namespace) -> Tuple[Dict[str, Tuple[str, int]], Dict[str, Any]]:
    cn_jp = load_cn_jp(Path(args.cn_jp))
    scan: Dict[str, Any] = {"chars": {}, "locations": {}, "files": []}
    if args.patch_all_map:
        used_chars = set(cn_jp.keys())
    else:
        if not args.json:
            raise SystemExit("patch requires --json unless --patch-all-map is specified")
        scan = scan_translation_json(args.json, args.fields.split(","))
        used_chars = set(scan["chars"].keys())

    used: Dict[str, Tuple[str, int]] = {}
    missing = []
    for ch in sorted(used_chars):
        if ch in cn_jp:
            used[ch] = cn_jp[ch]
        else:
            missing.append(ch)
    meta = {
        "scanned_files": scan.get("files", []),
        "unencodable_count": len(scan.get("chars", {})),
        "used_mapping_count": len(used),
        "missing_in_cn_jp": missing,
        "scan_counts": scan.get("chars", {}),
        "locations": scan.get("locations", {}),
    }
    return used, meta


def cmd_info(args: argparse.Namespace) -> int:
    dat = FontDat(Path(args.input).read_bytes())
    print(f"outer_magic=YOX table_off=0x{dat.table_off:X} count={dat.count}")
    for i, ent in enumerate(dat.entries):
        try:
            f = YoxFont.parse(ent.data)
            expect = glyph_count_for_mask(f.range_mask)
            ok = "OK" if expect == f.glyph_count else f"MISMATCH expected={expect}"
            print(f"FID {i}: off=0x{ent.offset:X} size=0x{ent.size:X} mask=0x{f.range_mask:X} "
                  f"glyphs={f.glyph_count} {ok} wh={f.width}x{f.height} "
                  f"bitmap=0x{f.bitmap_size:X} index_off=0x{f.index_off:X} width_off=0x{f.width_off:X}")
        except Exception as exc:
            print(f"FID {i}: off=0x{ent.offset:X} size=0x{ent.size:X} non-font/error={exc}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    cn_jp = load_cn_jp(Path(args.cn_jp)) if args.cn_jp else {}
    stats = scan_translation_json(args.json, args.fields.split(","))
    rows = []
    missing = []
    for ch, count in sorted(stats["chars"].items(), key=lambda kv: (-kv[1], kv[0])):
        item = {"char": ch, "count": count}
        if cn_jp:
            if ch in cn_jp:
                sur, code = cn_jp[ch]
                item.update({"mapped_to": sur, "cp932_code": f"{code:04X}"})
            else:
                item["mapped_to"] = None
                missing.append(ch)
        rows.append(item)
    result = {
        "files": stats["files"],
        "fields": args.fields.split(","),
        "unencodable_count": len(stats["chars"]),
        "chars": rows,
        "missing_in_cn_jp": missing,
        "locations": stats["locations"],
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


def cmd_patch(args: argparse.Namespace) -> int:
    in_path = Path(args.input)
    out_path = Path(args.output)
    font_path = Path(args.font)
    mapping, meta = build_used_mapping(args)
    if meta["missing_in_cn_jp"] and not args.allow_missing:
        preview = "".join(meta["missing_in_cn_jp"][:80])
        raise SystemExit(f"cn_jp.json missing {len(meta['missing_in_cn_jp'])} unencodable chars: {preview}")
    if not mapping:
        raise SystemExit("no used mapping found")

    dat = FontDat(in_path.read_bytes())
    slots = parse_slots(args.slots, dat)
    if not slots:
        raise SystemExit("no font slots selected")

    new_datas: Dict[int, bytes] = {}
    report = []
    code_collision: Dict[int, List[str]] = {}
    for cn_char, (_sur, code) in mapping.items():
        code_collision.setdefault(code, []).append(cn_char)
    collisions = {f"{code:04X}": chars for code, chars in code_collision.items() if len(chars) > 1}
    if collisions and not args.allow_collision:
        raise SystemExit("multiple Chinese chars map to same surrogate CP932 code: " + json.dumps(collisions, ensure_ascii=False))

    for fid in slots:
        if not (0 <= fid < len(dat.entries)):
            raise SystemExit(f"slot out of range: {fid}")
        yf = YoxFont.parse(dat.entries[fid].data)
        replacements: Dict[int, bytes] = {}
        skipped = []
        patched_items = []
        for cn_char, (surrogate, code) in mapping.items():
            gi = code_to_glyph_index(code, yf.range_mask)
            if gi is None:
                skipped.append((cn_char, surrogate, code, "range_mask_not_supported"))
                continue
            if not (0 <= gi < yf.glyph_count):
                skipped.append((cn_char, surrogate, code, f"glyph_index_out_of_range:{gi}"))
                continue
            raw = render_glyph(cn_char, yf.width, yf.height, font_path,
                               args.font_size, args.x_offset, args.y_offset,
                               args.stroke_width, args.scale)
            replacements[gi] = raw
            patched_items.append({"cn": cn_char, "surrogate": surrogate, "code": f"{code:04X}", "glyph_index": gi})
        if replacements:
            new_datas[fid] = yf.rebuild_with_replacements(replacements)
        report.append({
            "fid": fid,
            "width": yf.width,
            "height": yf.height,
            "range_mask": f"0x{yf.range_mask:X}",
            "patched": len(replacements),
            "skipped": len(skipped),
            "items": patched_items[:50] if args.verbose else None,
        })
        if args.verbose and skipped:
            for ch, surrogate, code, why in skipped[:50]:
                print(f"FID {fid}: skip {ch!r}->{surrogate!r}/0x{code:04X}: {why}")

    out_path.write_bytes(dat.rebuild(new_datas))
    result = {
        "input": str(in_path),
        "output": str(out_path),
        "font": str(font_path),
        "scanned_files": meta["scanned_files"],
        "unencodable_count": meta["unencodable_count"],
        "used_mapping_count": meta["used_mapping_count"],
        "missing_in_cn_jp": meta["missing_in_cn_jp"],
        "collisions": collisions,
        "slots": slots,
        "report": report,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.report:
        Path(args.report).write_text(json.dumps({**result, "locations": meta["locations"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def cmd_convert_json(args: argparse.Namespace) -> int:
    mapping = load_cn_jp(Path(args.cn_jp))
    files = iter_json_files(args.json)
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    fields = set(args.fields.split(","))
    converted = []
    for src in files:
        obj = json.loads(src.read_text(encoding="utf-8"))
        new_obj = replace_text_fields(obj, fields, mapping)
        if args.keep_tree:
            # Preserve source path under output dir as much as possible.
            rel = src
            try:
                common = Path(os.path.commonpath([str(p.parent) for p in files]))
                rel = src.relative_to(common)
            except Exception:
                rel = Path(src.name)
            dst = outdir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
        else:
            dst = outdir / src.name
        dst.write_text(json.dumps(new_obj, ensure_ascii=False, indent=2), encoding="utf-8")
        converted.append({"src": str(src), "dst": str(dst)})
    print(json.dumps({"converted": converted, "fields": sorted(fields)}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Patch kankin font.dat using translation JSON scan + cn_jp.json CnJpMap.")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("info", help="print outer/inner YOX font information")
    q.add_argument("input", help="font.dat or font_bak.dat")
    q.set_defaults(func=cmd_info)

    q = sub.add_parser("scan", help="scan JSON text fields for CP932-unencodable chars")
    q.add_argument("json", nargs="+", help="translation JSON file(s) or dir(s)")
    q.add_argument("--cn-jp", "--map", dest="cn_jp", help="optional cn_jp.json to resolve mapped surrogate chars")
    q.add_argument("--fields", default=",".join(DEFAULT_FIELDS), help="comma-separated fields to scan")
    q.add_argument("--output", help="write scan report JSON")
    q.set_defaults(func=cmd_scan)

    q = sub.add_parser("patch", help="scan JSON and patch font.dat glyphs according to cn_jp.json")
    q.add_argument("input", help="source font.dat/font_bak.dat")
    q.add_argument("output", help="output patched font.dat")
    q.add_argument("--json", nargs="*", help="translation JSON file(s) or dir(s) to scan")
    q.add_argument("--cn-jp", "--map", dest="cn_jp", required=True, help="cn_jp.json, e.g. {'这':'這'}")
    q.add_argument("--font", required=True, help="TTF/OTF font used to draw Chinese glyphs")
    q.add_argument("--slots", default="auto", help="auto=all font FIDs, or comma-separated FIDs, e.g. 0,1,2,3,5")
    q.add_argument("--fields", default=",".join(DEFAULT_FIELDS), help="comma-separated fields to scan")
    q.add_argument("--patch-all-map", action="store_true", help="ignore --json scan and patch every entry in cn_jp.json")
    q.add_argument("--allow-missing", action="store_true", help="do not abort when scanned chars are absent from cn_jp.json")
    q.add_argument("--allow-collision", action="store_true", help="allow multiple Chinese chars mapping to same surrogate slot")
    q.add_argument("--font-size", type=int, default=None, help="override font size for every slot")
    q.add_argument("--scale", type=float, default=1.05, help="auto font-size scale against glyph height")
    q.add_argument("--x-offset", type=int, default=0, help="glyph x offset in pixels")
    q.add_argument("--y-offset", type=int, default=0, help="glyph y offset in pixels")
    q.add_argument("--stroke-width", type=int, default=0, help="optional stroke width")
    q.add_argument("--report", help="write detailed patch report JSON")
    q.add_argument("--verbose", action="store_true")
    q.set_defaults(func=cmd_patch)

    q = sub.add_parser("convert-json", help="write converted JSON files with unencodable chars replaced by cn_jp surrogates")
    q.add_argument("json", nargs="+", help="translation JSON file(s) or dir(s)")
    q.add_argument("--cn-jp", "--map", dest="cn_jp", required=True)
    q.add_argument("--output-dir", required=True)
    q.add_argument("--fields", default=",".join(DEFAULT_FIELDS))
    q.add_argument("--keep-tree", action="store_true")
    q.set_defaults(func=cmd_convert_json)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
