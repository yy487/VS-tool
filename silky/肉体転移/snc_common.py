#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EVIT/SNC CP932 visible text extractor/injector common code.

Target: Arpeggio EVIT .snc scripts.
Policy:
  - JSON is UTF-8.
  - SNC strings are encoded as CP932.
  - Only visible text entries exported in JSON are replaced.
  - VM/code size is kept unchanged; string pool is rebuilt non-equal-length.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
import struct
from typing import Dict, Iterable, List, Optional, Tuple

CP932 = "cp932"
HEADER_SIZE = 0x1C
OP_ST = 0x7473  # bytes "st"
OP_LV = 0x766C  # bytes "lv"
OP_CHOICE_BEGIN = 0x0081
OP_ZERO = 0x0000

_ASCII_TOKEN_RE = re.compile(r"^[A-Za-z0-9_./\\:-]+$")
_RESOURCE_RE_LIST = [
    re.compile(r"^VK[A-Z][0-9A-Za-z_]*$"),
    re.compile(r"^KSE[0-9A-Za-z_]*$"),
    re.compile(r"^KBG[0-9A-Za-z_]*$"),
    re.compile(r"^KA[0-9A-Za-z_]*$"),
    re.compile(r"^bgm[0-9A-Za-z_]*$", re.I),
    re.compile(r"^se[0-9A-Za-z_]*$", re.I),
    re.compile(r"^stand[0-9A-Za-z_]*$", re.I),
    re.compile(r"^face[0-9A-Za-z_]*$", re.I),
    re.compile(r"^black$", re.I),
    re.compile(r"^white$", re.I),
    re.compile(r"^ev[0-9A-Za-z_]*$", re.I),
    re.compile(r"^cg[0-9A-Za-z_]*$", re.I),
    re.compile(r"^[a-z]{3}[0-9]{4}[a-z]?$", re.I),  # next script names, e.g. ken1102
]


def read_u16_words(data: bytes, start: int) -> List[int]:
    if (len(data) - start) % 2:
        # The code area should be word aligned. Ignore a dangling byte defensively.
        end = len(data) - 1
    else:
        end = len(data)
    return list(struct.unpack_from("<" + "H" * ((end - start) // 2), data, start))


def write_u16_word(buf: bytearray, byte_offset: int, value: int) -> None:
    if not (0 <= value <= 0xFFFF):
        raise ValueError(f"u16 overflow: {value:#x}")
    struct.pack_into("<H", buf, byte_offset, value)


def decode_cp932(raw: bytes, path: str = "") -> str:
    try:
        return raw.decode(CP932)
    except UnicodeDecodeError as e:
        raise ValueError(f"CP932 decode failed at {path}: {e}") from e


def encode_cp932(text: str, where: str = "") -> bytes:
    try:
        return text.encode(CP932)
    except UnicodeEncodeError as e:
        raise ValueError(f"CP932 encode failed at {where}: {text!r}; {e}") from e


@dataclass
class Header:
    h0_label_end_word: int
    h1_label_table_word: int
    h2_aux_table_word: int
    h3_code_start_word: int
    h4_file_size: int
    h5_const: int

    @property
    def label_end(self) -> int:
        return self.h0_label_end_word * 2

    @property
    def label_table_start(self) -> int:
        return self.h1_label_table_word * 2

    @property
    def aux_table_start(self) -> int:
        return self.h2_aux_table_word * 2

    @property
    def code_start(self) -> int:
        return self.h3_code_start_word * 2


@dataclass
class StringEntry:
    index: int
    offset: int
    raw: bytes
    text: str


@dataclass
class StRef:
    code_word_index: int
    param_word: int
    string_offset: int
    string_index: int


@dataclass
class ChoiceOption:
    st_word_index: int
    param_word: int
    string_offset: int
    string_index: int
    label_index: Optional[int]
    label: Optional[str]


@dataclass
class ChoiceBlock:
    choice_id: int
    word_index: int
    options: List[ChoiceOption]


class SncFile:
    def __init__(self, path: Path, data: bytes):
        self.path = Path(path)
        self.data = data
        self.header = self._parse_header(data)
        self.labels = self._parse_labels()
        self.strings = self._parse_string_pool()
        self.offset_to_string_index = {s.offset: s.index for s in self.strings}
        self.st_refs = self._scan_st_refs()
        self.choices = self._scan_choices()
        self.choice_string_index_to_info: Dict[int, Tuple[int, int, Optional[str], Optional[int]]] = {}
        for ch in self.choices:
            for opt_i, opt in enumerate(ch.options):
                self.choice_string_index_to_info[opt.string_index] = (ch.choice_id, opt_i, opt.label, opt.label_index)

    @staticmethod
    def _parse_header(data: bytes) -> Header:
        if len(data) < HEADER_SIZE:
            raise ValueError("file too small")
        magic = data[:4]
        if magic != b"EVIT":
            raise ValueError(f"bad magic: {magic!r}")
        vals = struct.unpack_from("<6I", data, 4)
        h = Header(*vals)
        if h.h4_file_size != len(data):
            # Some rebuilt files may be checked strictly by engine, so input mismatch is suspicious.
            raise ValueError(f"header file size mismatch: header={h.h4_file_size}, actual={len(data)}")
        if h.label_end < HEADER_SIZE or h.label_table_start < h.label_end or h.code_start < h.aux_table_start:
            raise ValueError(f"invalid EVIT offsets: {h}")
        return h

    @classmethod
    def from_path(cls, path: Path) -> "SncFile":
        return cls(path, path.read_bytes())

    def _iter_aligned_cstrings(self, start: int, end: int) -> Iterable[Tuple[int, bytes]]:
        pos = start
        while pos < end:
            nul = self.data.find(b"\x00", pos, end)
            if nul < 0:
                break
            raw = self.data[pos:nul]
            yield pos, raw
            pos = nul + 1
            if pos & 1:
                pos += 1

    def _parse_labels(self) -> List[str]:
        labels = []
        for _off, raw in self._iter_aligned_cstrings(HEADER_SIZE, self.header.label_end):
            if raw:
                labels.append(decode_cp932(raw, f"{self.path}:label"))
        return labels

    def _parse_string_pool(self) -> List[StringEntry]:
        out = []
        for off, raw in self._iter_aligned_cstrings(self.header.label_end, self.header.label_table_start):
            if not raw:
                continue
            out.append(StringEntry(len(out), off, raw, decode_cp932(raw, f"{self.path}:{off:#x}")))
        return out

    def _param_to_string_offset(self, param: int) -> int:
        return (param + self.header.h0_label_end_word) * 2

    def _string_offset_to_param(self, offset: int) -> int:
        if offset & 1:
            raise ValueError(f"string offset not word aligned: {offset:#x}")
        return offset // 2 - self.header.h0_label_end_word

    def _scan_st_refs(self) -> List[StRef]:
        words = read_u16_words(self.data, self.header.code_start)
        refs: List[StRef] = []
        for i in range(len(words) - 1):
            if words[i] != OP_ST:
                continue
            param = words[i + 1]
            off = self._param_to_string_offset(param)
            idx = self.offset_to_string_index.get(off)
            if idx is not None:
                refs.append(StRef(i, param, off, idx))
        return refs

    def _scan_choices(self) -> List[ChoiceBlock]:
        words = read_u16_words(self.data, self.header.code_start)
        choices: List[ChoiceBlock] = []
        i = 0
        while i < len(words):
            if words[i] != OP_CHOICE_BEGIN:
                i += 1
                continue
            j = i + 1
            opts: List[ChoiceOption] = []
            ok = False
            while j < len(words):
                if words[j] == OP_ZERO:
                    ok = len(opts) >= 2
                    j += 1
                    break
                if j + 1 >= len(words) or words[j] != OP_ST:
                    break
                st_word_index = j
                param = words[j + 1]
                off = self._param_to_string_offset(param)
                str_idx = self.offset_to_string_index.get(off)
                if str_idx is None:
                    break
                j += 2
                label_idx = None
                label = None
                if j + 1 < len(words) and words[j] == OP_LV:
                    label_idx = words[j + 1]
                    if 0 <= label_idx < len(self.labels):
                        label = self.labels[label_idx]
                    j += 2
                opts.append(ChoiceOption(st_word_index, param, off, str_idx, label_idx, label))
            if ok:
                choices.append(ChoiceBlock(len(choices), i, opts))
                i = j
            else:
                i += 1
        return choices


def is_probably_resource_or_label(text: str) -> bool:
    s = text.strip()
    if not s:
        return True
    for r in _RESOURCE_RE_LIST:
        if r.match(s):
            return True
    # Pure ASCII tokens are almost always resource names/labels in these SNC files.
    if _ASCII_TOKEN_RE.match(s):
        return True
    return False



ENGINE_NL = r"\n"


def _split_engine_lines(text: str) -> Tuple[str, List[str]]:
    """Return the newline marker used by the string and logical line chunks.

    Arpeggio SNC stores display newlines as literal backslash+n in the tested
    files. A real LF fallback is kept for safety.
    """
    if ENGINE_NL in text:
        return ENGINE_NL, text.split(ENGINE_NL)
    if "\n" in text:
        return "\n", text.split("\n")
    return ENGINE_NL, [text]


def _join_without_empty(chunks: List[str]) -> str:
    return "".join(ch for ch in chunks if ch != "")


_SPEAKER_BAD_CHARS = set("「」『』（）()［］[]｛｝{}【】<>〈〉《》。、，,.！!；;：:\"'…‥～〜—―／/\\")
_UNKNOWN_SPEAKER_NAMES = {"？", "?", "？？？", "???"}
_SPEAKER_BAD_SUFFIXES = (
    "ながら", "けれど", "けど", "ので", "から", "まで", "より",
    "は", "が", "を", "に", "へ", "で", "と", "も", "の",
    "だ", "です", "ます", "て",
)


def _looks_like_dialogue_message(s: str) -> bool:
    t = s.lstrip()
    return t.startswith("「") or t.startswith("『") or t.startswith("（")


def _basic_speaker_name_shape(s: str) -> bool:
    """Return True only for short name-like fields."""
    t = s.strip()
    if not t:
        return False
    if t in _UNKNOWN_SPEAKER_NAMES:
        return True
    if len(t) > 16:
        return False
    if any(ch in _SPEAKER_BAD_CHARS for ch in t):
        return False
    if any(t.endswith(suf) for suf in _SPEAKER_BAD_SUFFIXES):
        return False
    if is_probably_resource_or_label(t):
        return False
    if _looks_like_dialogue_message(t):
        return False
    return True


def _collect_speaker_names_from_strings(strings: Iterable[StringEntry]) -> set[str]:
    names: set[str] = set()
    for se in strings:
        _nl, parts = _split_engine_lines(se.text)
        if parts and parts[0] == "":
            parts = parts[1:]
        if len(parts) >= 2 and _looks_like_dialogue_message(parts[1]):
            cand = parts[0].strip()
            if _basic_speaker_name_shape(cand):
                names.add(cand)
    return names


def _looks_like_speaker_name(s: str, known_speakers: Optional[set[str]] = None) -> bool:
    t = s.strip()
    if t in _UNKNOWN_SPEAKER_NAMES:
        return True
    if known_speakers is not None:
        return t in known_speakers
    return _basic_speaker_name_shape(t)


def _is_vk_resource(text: str) -> bool:
    return re.match(r"^VK[A-Z0-9_]*$", text.strip()) is not None


def _compute_speaker_context_by_string_index(snc: "SncFile") -> Dict[int, bool]:
    """Return str_index -> whether this string is in a speaker-call context.

    In Arpeggio SNC, voiced/named dialogue is not reliably identifiable by
    text shape alone. The VM normally emits one or more V* resource strings
    immediately before the visible line; the visible line then stores
    name\nmessage. Wrapped narration can also contain A\nB where B starts with
    a quote, so name splitting must be gated by this VM context.
    """
    ctx: Dict[int, bool] = {}
    for order, ref in enumerate(snc.st_refs):
        has_vk = False
        # Look backwards across a short run of resource calls. Stop as soon as
        # another visible/non-resource text is encountered.
        for j in range(order - 1, max(-1, order - 6), -1):
            prev_text = snc.strings[snc.st_refs[j].string_index].text
            if _is_vk_resource(prev_text):
                has_vk = True
                break
            if is_probably_resource_or_label(prev_text):
                continue
            break
        if has_vk:
            ctx[ref.string_index] = True
    return ctx


def visible_payload_from_string(text: str, known_speakers: Optional[set[str]] = None, speaker_context: bool = False) -> Tuple[Optional[str], str, str, str, str]:
    """Return (name, scr_msg, prefix, suffix, nl_marker).

    Extraction policy for this engine:
      - Do not expose the engine newline marker in JSON.
      - Treat only `name + newline + dialogue-like message` as name/message.
      - A leading newline marker is a display/control prefix and is restored
        during injection, but is not exported.
      - Newline markers inside message text are removed on export/injection.
    """
    prefix = ""
    suffix = ""
    body = text
    while body.endswith(" "):
        suffix = " " + suffix
        body = body[:-1]

    nl_marker, parts = _split_engine_lines(body)
    if len(parts) == 1:
        return None, parts[0], prefix, suffix, nl_marker

    # Leading display marker, e.g. "\\n地の文" or "\\n名前\\n「台詞」".
    if parts and parts[0] == "":
        prefix = nl_marker
        parts = parts[1:]

    # name\n「message」, also supports \nname\n「message」.
    # The first field must look like a short speaker name; otherwise this is
    # wrapped narration followed by a quote and must stay nameless.
    if (
        speaker_context
        and len(parts) >= 2
        and _looks_like_speaker_name(parts[0], known_speakers)
        and _looks_like_dialogue_message(parts[1])
    ):
        name = parts[0].strip()
        msg = _join_without_empty(parts[1:])
        return name, msg, prefix, suffix, nl_marker

    # No speaker name: remove all script newline markers from exported text.
    return None, _join_without_empty(parts), prefix, suffix, nl_marker


def reconstruct_visible_text(original_text: str, msg: str, name_override: Optional[str] = None, known_speakers: Optional[set[str]] = None, speaker_context: bool = False) -> str:
    name, _old_msg, prefix, suffix, nl_marker = visible_payload_from_string(original_text, known_speakers, speaker_context)
    clean_msg = msg.replace(ENGINE_NL, "").replace("\n", "")
    if name is not None:
        return prefix + (name_override if name_override is not None else name) + nl_marker + clean_msg + suffix
    return prefix + clean_msg + suffix

def extract_entries(snc: SncFile) -> List[dict]:
    known_speakers = _collect_speaker_names_from_strings(snc.strings)
    speaker_context_by_index = _compute_speaker_context_by_string_index(snc)
    # Export by first code-reference order; de-duplicate same string-pool entry.
    first_ref_order: Dict[int, int] = {}
    for order, ref in enumerate(snc.st_refs):
        first_ref_order.setdefault(ref.string_index, order)

    visible_indices = []
    for idx, _order in sorted(first_ref_order.items(), key=lambda kv: kv[1]):
        s = snc.strings[idx].text
        if is_probably_resource_or_label(s):
            continue
        visible_indices.append(idx)

    # Group choice options into one JSON entry.
    choice_option_indices = set(snc.choice_string_index_to_info)
    emitted_choice_ids = set()
    out: List[dict] = []
    eid = 0
    for idx in visible_indices:
        if idx in choice_option_indices:
            choice_id, _opt_i, _label, _label_idx = snc.choice_string_index_to_info[idx]
            if choice_id in emitted_choice_ids:
                continue
            ch = snc.choices[choice_id]
            choices = []
            for opt in ch.options:
                se = snc.strings[opt.string_index]
                _name, scr_msg, prefix, suffix, nl_marker = visible_payload_from_string(se.text, known_speakers, speaker_context_by_index.get(se.index, False))
                choices.append({
                    "label": opt.label,
                    "label_index": opt.label_index,
                    "scr_msg": scr_msg,
                    "msg": scr_msg,
                    "_str_index": opt.string_index,
                    "_offset": se.offset,
                    "_prefix": prefix,
                    "_suffix": suffix,
                    "_nl_marker": nl_marker,
                })
            out.append({
                "id": eid,
                "type": "choice",
                "choices": choices,
                "_file": snc.path.name,
                "_choice_id": choice_id,
                "_code_word_index": ch.word_index,
            })
            eid += 1
            emitted_choice_ids.add(choice_id)
            continue

        se = snc.strings[idx]
        name, scr_msg, prefix, suffix, nl_marker = visible_payload_from_string(se.text, known_speakers, speaker_context_by_index.get(se.index, False))
        item = {
            "id": eid,
            "scr_msg": scr_msg,
            "msg": scr_msg,
            "_file": snc.path.name,
            "_str_index": idx,
            "_offset": se.offset,
            "_prefix": prefix,
            "_suffix": suffix,
            "_nl_marker": nl_marker,
        }
        if name is not None:
            item["name"] = name
        out.append(item)
        eid += 1
    return out


def load_translation_map(json_path: Path) -> Dict[int, Tuple[str, Optional[str]]]:
    """Return str_index -> (msg, optional name)."""
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    if isinstance(doc, dict) and "items" in doc:
        items = doc["items"]
    elif isinstance(doc, list):
        items = doc
    else:
        raise ValueError(f"unsupported json root in {json_path}")
    mapping: Dict[int, Tuple[str, Optional[str]]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "choice":
            for ch in item.get("choices", []):
                if "_str_index" not in ch:
                    continue
                mapping[int(ch["_str_index"])] = (str(ch.get("msg", ch.get("scr_msg", ""))), None)
        else:
            if "_str_index" not in item:
                continue
            mapping[int(item["_str_index"])] = (str(item.get("msg", item.get("scr_msg", ""))), item.get("name"))
    return mapping


def rebuild_snc_with_translations(snc: SncFile, trans: Dict[int, Tuple[str, Optional[str]]]) -> bytes:
    h = snc.header
    old = snc.data

    # Prepare new text for every pool entry.
    known_speakers = _collect_speaker_names_from_strings(snc.strings)
    speaker_context_by_index = _compute_speaker_context_by_string_index(snc)
    new_texts: List[str] = []
    for se in snc.strings:
        if se.index in trans:
            msg, name_override = trans[se.index]
            nt = reconstruct_visible_text(se.text, msg, name_override, known_speakers, speaker_context_by_index.get(se.index, False))
            encode_cp932(nt, f"{snc.path.name}:str_index={se.index}")
            new_texts.append(nt)
        else:
            new_texts.append(se.text)

    new_pool = bytearray()
    new_offsets_by_index: Dict[int, int] = {}
    pool_start = h.label_end
    for se, nt in zip(snc.strings, new_texts):
        abs_off = pool_start + len(new_pool)
        if abs_off & 1:
            new_pool.append(0)
            abs_off += 1
        new_offsets_by_index[se.index] = abs_off
        raw = encode_cp932(nt, f"{snc.path.name}:str_index={se.index}")
        new_pool += raw + b"\x00"
        if (pool_start + len(new_pool)) & 1:
            new_pool.append(0)

    new_label_table_start = pool_start + len(new_pool)
    if new_label_table_start & 1:
        new_pool.append(0)
        new_label_table_start += 1

    table1_len = h.aux_table_start - h.label_table_start
    table2_len = h.code_start - h.aux_table_start
    old_tables = old[h.label_table_start:h.code_start]
    old_code = old[h.code_start:]
    new_code = bytearray(old_code)

    # Rewrite every valid ST reference to the rebuilt string-pool offset.
    for ref in snc.st_refs:
        new_off = new_offsets_by_index[ref.string_index]
        new_param = new_off // 2 - h.h0_label_end_word
        if not (0 <= new_param <= 0xFFFF):
            raise ValueError(f"ST param overflow in {snc.path.name}: string index {ref.string_index}, offset {new_off:#x}")
        byte_off = ref.code_word_index * 2 + 2  # word after OP_ST within code area
        write_u16_word(new_code, byte_off, new_param)

    new_aux_table_start = new_label_table_start + table1_len
    new_code_start = new_aux_table_start + table2_len

    out = bytearray()
    out += old[:HEADER_SIZE]
    out += old[HEADER_SIZE:h.label_end]
    out += new_pool
    out += old_tables
    out += new_code

    # Patch header fields.
    struct.pack_into("<6I", out, 4,
                     h.h0_label_end_word,
                     new_label_table_start // 2,
                     new_aux_table_start // 2,
                     new_code_start // 2,
                     len(out),
                     h.h5_const)
    return bytes(out)


def extract_file(src: Path, dst_json: Path) -> dict:
    snc = SncFile.from_path(src)
    items = extract_entries(snc)
    doc = {
        "format": "EVIT_SNC_CP932_VISIBLE_TEXT_V5",
        "file": src.name,
        "encoding": CP932,
        "labels": snc.labels,
        "choice_count": len(snc.choices),
        "items": items,
    }
    dst_json.parent.mkdir(parents=True, exist_ok=True)
    dst_json.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"file": src.name, "items": len(items), "choices": len(snc.choices)}


def inject_file(src_snc: Path, src_json: Path, dst_snc: Path) -> dict:
    snc = SncFile.from_path(src_snc)
    trans = load_translation_map(src_json)
    out = rebuild_snc_with_translations(snc, trans)
    # Smoke-parse rebuilt file to catch header/string/code reference errors.
    tmp = SncFile(dst_snc, out)
    if len(tmp.st_refs) != len(snc.st_refs):
        raise ValueError(f"ST ref count changed unexpectedly in {src_snc.name}: {len(snc.st_refs)} -> {len(tmp.st_refs)}")
    dst_snc.parent.mkdir(parents=True, exist_ok=True)
    dst_snc.write_bytes(out)
    return {"file": src_snc.name, "old_size": len(snc.data), "new_size": len(out), "translated": len(trans)}
