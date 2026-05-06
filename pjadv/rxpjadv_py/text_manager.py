from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import PathLike
from .scenario import Scenario
from .textdata import TextData

# opcode -> metadata for text-bearing fields
DIALOG_OPS = {0x80000406, 0x80000307}
SELECT_OPS = {0x01010203, 0x01010804}
CHAPTER_OPS = {0x01000D02}
COMMENT_OPS = {0x03000303}


def _hex32(v: int) -> str:
    return f"0x{v:08X}"


def _load_json(path: PathLike) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _save_json(path: PathLike, obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def export_text(
    textdata_path: PathLike,
    scenario_path: PathLike,
    out_json_path: PathLike,
    *,
    encoding: str = "cp932",
    legacy_pair_json: bool = False,
    out_seq_json_path: PathLike | None = None,
) -> list[dict[str, Any]]:
    """Export PJADV texts.

    Default output follows this project's localization-oriented format:
      optional name, scr_msg, msg, plus locating metadata.

    When legacy_pair_json=True, output the upstream-style msg json and seq json pair.
    """
    textdata = TextData(textdata_path)
    scenario = Scenario(scenario_path)

    entries: list[dict[str, Any]] = []
    legacy_msgs: list[dict[str, str]] = []
    legacy_seqs: list[int] = []

    for cmd in scenario.commands:
        words = list(cmd.words)
        op = words[0]
        base: dict[str, Any] = {
            "_file": Path(scenario_path).name,
            "_index": cmd.index,
            "_cmd_offset": cmd.offset,
            "_op": _hex32(op),
        }

        if op in DIALOG_OPS:
            if len(words) <= 3:
                continue
            name_off = words[2]
            msg_off = words[3]
            entry = dict(base)
            entry["_kind"] = "message"
            entry["_name_offset"] = name_off
            entry["_msg_offset"] = msg_off
            if name_off:
                entry["name"] = textdata.get_text(name_off, encoding)
            if msg_off:
                msg = textdata.get_text(msg_off, encoding)
                entry["scr_msg"] = msg
                entry["msg"] = msg
            entries.append(entry)

            legacy: dict[str, str] = {}
            if name_off:
                name = textdata.get_text(name_off, encoding)
                legacy["chr_org"] = name
                legacy["chr_tra"] = name
            if msg_off:
                msg = textdata.get_text(msg_off, encoding)
                legacy["msg_org"] = msg
                legacy["msg_tra"] = msg
            legacy_msgs.append(legacy)
            legacy_seqs.append(cmd.index)

        elif op in SELECT_OPS:
            if len(words) <= 1 or not words[1]:
                continue
            msg = textdata.get_text(words[1], encoding)
            entries.append({**base, "_kind": "select", "_msg_offset": words[1], "scr_msg": msg, "msg": msg})
            legacy_msgs.append({"sel_org": msg, "sel_tra": msg})
            legacy_seqs.append(cmd.index)

        elif op in CHAPTER_OPS:
            if len(words) <= 1 or not words[1]:
                continue
            msg = textdata.get_text(words[1], encoding)
            entries.append({**base, "_kind": "chapter", "_msg_offset": words[1], "scr_msg": msg, "msg": msg})
            legacy_msgs.append({"chp_org": msg, "chp_tra": msg})
            legacy_seqs.append(cmd.index)

        elif op in COMMENT_OPS:
            if len(words) <= 2 or not words[2]:
                continue
            msg = textdata.get_text(words[2], encoding)
            entries.append({**base, "_kind": "comment", "_msg_offset": words[2], "scr_msg": msg, "msg": msg})
            legacy_msgs.append({"com_org": msg, "com_tra": msg})
            legacy_seqs.append(cmd.index)

    if legacy_pair_json:
        if out_seq_json_path is None:
            raise ValueError("out_seq_json_path is required when legacy_pair_json=True")
        _save_json(out_json_path, legacy_msgs)
        _save_json(out_seq_json_path, legacy_seqs)
    else:
        _save_json(out_json_path, entries)
    return entries


def _field_for_kind(kind: str, op: int) -> int:
    if kind == "message" or op in DIALOG_OPS:
        return 3
    if kind in {"select", "chapter"} or op in SELECT_OPS or op in CHAPTER_OPS:
        return 1
    if kind == "comment" or op in COMMENT_OPS:
        return 2
    raise ValueError(f"unsupported text kind/opcode: kind={kind!r}, op=0x{op:08X}")


def _name_field_for_dialog(op: int) -> int | None:
    return 2 if op in DIALOG_OPS else None


def import_text(
    textdata_path: PathLike,
    scenario_path: PathLike,
    json_path: PathLike,
    *,
    out_textdata_path: PathLike | None = None,
    out_scenario_path: PathLike | None = None,
    encoding: str = "cp932",
    strict: bool = True,
    update_name: bool = False,
) -> dict[str, int]:
    """Import localization JSON by appending translated text to textdata and patching scenario offsets.

    The importer uses _index first and validates _op and scr_msg when present.
    If strict=False, entries with failed validation are skipped instead of raising.
    """
    textdata = TextData(textdata_path)
    scenario = Scenario(scenario_path)
    entries = _load_json(json_path)
    if not isinstance(entries, list):
        raise ValueError("import json must be an array")

    stats = {"total": 0, "patched_msg": 0, "patched_name": 0, "skipped": 0}

    for entry in entries:
        stats["total"] += 1
        try:
            idx = int(entry["_index"])
            cmd = scenario.commands[idx]
            op = cmd.opcode
            expected_op = int(str(entry.get("_op", _hex32(op))), 16)
            if op != expected_op:
                raise ValueError(f"opcode mismatch at index {idx}: scenario=0x{op:08X}, json=0x{expected_op:08X}")
            words = scenario.command_words(idx)
            kind = str(entry.get("_kind", "message"))
            msg_field = _field_for_kind(kind, op)
            if msg_field >= len(words):
                raise ValueError(f"command index {idx} has no word[{msg_field}]")

            scr_msg = entry.get("scr_msg")
            old_msg_off = words[msg_field]
            if scr_msg is not None and old_msg_off:
                current = textdata.get_text(old_msg_off, encoding)
                if current != scr_msg:
                    raise ValueError(f"scr_msg validation failed at index {idx}: current={current!r}, expected={scr_msg!r}")

            if "msg" in entry and old_msg_off:
                new_off = textdata.append_text(str(entry["msg"]), encoding)
                scenario.set_word(idx, msg_field, new_off)
                stats["patched_msg"] += 1

            if update_name and op in DIALOG_OPS and "name" in entry:
                name_field = _name_field_for_dialog(op)
                if name_field is not None and name_field < len(words) and words[name_field]:
                    new_name_off = textdata.append_text(str(entry["name"]), encoding)
                    scenario.set_word(idx, name_field, new_name_off)
                    stats["patched_name"] += 1

        except Exception:
            if strict:
                raise
            stats["skipped"] += 1

    textdata.save(out_textdata_path or f"{textdata_path}.new")
    scenario.save(out_scenario_path or f"{scenario_path}.new")
    return stats


def import_legacy_pair(
    textdata_path: PathLike,
    scenario_path: PathLike,
    msg_json_path: PathLike,
    seq_json_path: PathLike,
    *,
    out_textdata_path: PathLike | None = None,
    out_scenario_path: PathLike | None = None,
    encoding: str = "cp932",
) -> dict[str, int]:
    """Import upstream-style chr_tra/msg_tra/sel_tra JSON plus seq JSON."""
    textdata = TextData(textdata_path)
    scenario = Scenario(scenario_path)
    msgs = _load_json(msg_json_path)
    seqs = _load_json(seq_json_path)
    if len(msgs) != len(seqs):
        raise ValueError("legacy msg json and seq json length mismatch")
    stats = {"total": len(msgs), "patched": 0}

    for msg, seq in zip(msgs, seqs):
        idx = int(seq)
        cmd = scenario.commands[idx]
        words = scenario.command_words(idx)
        op = words[0]
        if op in DIALOG_OPS:
            if words[2] and "chr_tra" in msg:
                scenario.set_word(idx, 2, textdata.append_text(str(msg["chr_tra"]), encoding))
                stats["patched"] += 1
            if words[3] and "msg_tra" in msg:
                scenario.set_word(idx, 3, textdata.append_text(str(msg["msg_tra"]), encoding))
                stats["patched"] += 1
        elif op in SELECT_OPS:
            if words[1] and "sel_tra" in msg:
                scenario.set_word(idx, 1, textdata.append_text(str(msg["sel_tra"]), encoding))
                stats["patched"] += 1
        elif op in CHAPTER_OPS:
            if words[1] and "chp_tra" in msg:
                scenario.set_word(idx, 1, textdata.append_text(str(msg["chp_tra"]), encoding))
                stats["patched"] += 1
        elif op in COMMENT_OPS:
            if words[2] and "com_tra" in msg:
                scenario.set_word(idx, 2, textdata.append_text(str(msg["com_tra"]), encoding))
                stats["patched"] += 1

    textdata.save(out_textdata_path or f"{textdata_path}.new")
    scenario.save(out_scenario_path or f"{scenario_path}.new")
    return stats
