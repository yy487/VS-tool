from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

from archive.tev2_archive import write_probe_manifest
from script.tev2_bttext import compile_bttext, parse_bttext_text, probe_bttext, rebuild_bttext
from script.tev2_check_text_fit import check_text_fit
from script.tev2_fit_report import build_fit_report
from script.tev2_patch_text import patch_text_doc
from script.tev2_scan_text import build_text_scan
from script.tev2_scr import compile_scr_text, parse_scr_sections, parse_scr_text, parse_scr_text_bytes, probe_scr, probe_scr_bytes, rebuild_scr
from script.tev2_text_tables import compile_table, parse_table, parse_table_bytes


def make_temp_dir(title_root: Path, name: str) -> Path:
    temp_root = title_root / "_regression_tmp" / name
    if temp_root.exists():
        shutil.rmtree(temp_root, ignore_errors=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    return temp_root


def cleanup_temp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def run_archive_probe_regression(title_root: Path) -> None:
    game_dir = title_root / "game"
    out_dir = make_temp_dir(title_root, "archive_probe")
    try:
        manifest_path = write_probe_manifest(game_dir, out_dir)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != "TE_V2_ARCHIVE_PROBE":
            raise AssertionError("Unexpected archive probe manifest format")
        if int(manifest.get("archive_count", 0)) < 5:
            raise AssertionError("Expected at least 5 gameXX.dat archives")
    finally:
        cleanup_temp_dir(out_dir)


def run_ti_name_roundtrip_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game00" / "files" / "data" / "tiNameSp.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked tiNameSp.dat at _pak0_game00/files/data/tiNameSp.dat")
    doc = parse_table(source_path, text_encoding="cp932")
    rebuilt = compile_table(
        {
            "format": doc.format,
            "table_name": doc.table_name,
            "source_path": doc.source_path,
            "key_mode": doc.key_mode,
            "key_seed_u32": doc.key_seed_u32,
            "record_size": doc.record_size,
            "entries": doc.entries,
        },
        text_encoding="cp932",
    )
    original = source_path.read_bytes()
    if rebuilt != original:
        raise AssertionError("tiNameSp.dat roundtrip mismatch")


def run_ti_name_patch_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game00" / "files" / "data" / "tiNameSp.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked tiNameSp.dat at _pak0_game00/files/data/tiNameSp.dat")
    doc = parse_table(source_path, text_encoding="cp932")
    target = next((entry for entry in doc.entries if entry["decoded"] and entry["text"]), None)
    if target is None:
        raise AssertionError("No decoded tiNameSp.dat entry found")
    target["text"] = "試験"
    rebuilt = compile_table(
        {
            "format": doc.format,
            "table_name": doc.table_name,
            "source_path": doc.source_path,
            "key_mode": doc.key_mode,
            "key_seed_u32": doc.key_seed_u32,
            "record_size": doc.record_size,
            "entries": doc.entries,
        },
        text_encoding="cp932",
    )
    reparsed = parse_table_bytes(
        rebuilt,
        table_name=doc.table_name,
        source_path=doc.source_path,
        text_encoding="cp932",
    )
    if not any(entry["text"] == "試験" for entry in reparsed.entries):
        raise AssertionError("Patched tiNameSp.dat text not recovered after reparse")


def run_ti_name_target_encoding_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game00" / "files" / "data" / "tiNameSp.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked tiNameSp.dat at _pak0_game00/files/data/tiNameSp.dat")
    doc = parse_table(source_path, text_encoding="cp932")
    target = next((entry for entry in doc.entries if entry["decoded"] and entry["text"]), None)
    if target is None:
        raise AssertionError("No decoded tiNameSp.dat entry found")
    target["text"] = "编码回写GBK测试"
    rebuilt = compile_table(
        {
            "format": doc.format,
            "table_name": doc.table_name,
            "source_path": doc.source_path,
            "key_mode": doc.key_mode,
            "key_seed_u32": doc.key_seed_u32,
            "record_size": doc.record_size,
            "entries": doc.entries,
        },
        text_encoding="gbk",
    )
    reparsed = parse_table_bytes(
        rebuilt,
        table_name=doc.table_name,
        source_path=doc.source_path,
        text_encoding="gbk",
    )
    if not any(entry["text"] == "编码回写GBK测试" for entry in reparsed.entries):
        raise AssertionError("GBK write-back text not recovered after reparse")


def run_ti_name_game01_roundtrip_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game01_verify" / "files" / "data" / "tiName.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked tiName.dat at _pak0_game01_verify/files/data/tiName.dat")
    doc = parse_table(source_path, text_encoding="cp932")
    rebuilt = compile_table(
        {
            "format": doc.format,
            "table_name": doc.table_name,
            "source_path": doc.source_path,
            "key_mode": doc.key_mode,
            "key_seed_u32": doc.key_seed_u32,
            "record_size": doc.record_size,
            "entries": doc.entries,
        },
        text_encoding="cp932",
    )
    if rebuilt != source_path.read_bytes():
        raise AssertionError("tiName.dat roundtrip mismatch")


def run_ti_name_game01_target_encoding_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game01_verify" / "files" / "data" / "tiName.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked tiName.dat at _pak0_game01_verify/files/data/tiName.dat")
    doc = parse_table(source_path, text_encoding="cp932")
    target = next((entry for entry in doc.entries if entry["decoded"] and entry["text"]), None)
    if target is None:
        raise AssertionError("No decoded tiName.dat entry found")
    target["text"] = "编码回写GBK测试"
    rebuilt = compile_table(
        {
            "format": doc.format,
            "table_name": doc.table_name,
            "source_path": doc.source_path,
            "key_mode": doc.key_mode,
            "key_seed_u32": doc.key_seed_u32,
            "record_size": doc.record_size,
            "entries": doc.entries,
        },
        text_encoding="gbk",
    )
    reparsed = parse_table_bytes(
        rebuilt,
        table_name=doc.table_name,
        source_path=doc.source_path,
        text_encoding="gbk",
    )
    if not any(entry["text"] == "编码回写GBK测试" for entry in reparsed.entries):
        raise AssertionError("GBK write-back text not recovered after tiName.dat reparse")


def run_ti_balloon_roundtrip_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game00" / "files" / "data" / "tiBalloonSp.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked tiBalloonSp.dat at _pak0_game00/files/data/tiBalloonSp.dat")
    doc = parse_table(source_path, text_encoding="cp932")
    rebuilt = compile_table(
        {
            "format": doc.format,
            "table_name": doc.table_name,
            "source_path": doc.source_path,
            "key_mode": doc.key_mode,
            "key_seed_u32": doc.key_seed_u32,
            "record_size": doc.record_size,
            "entries": doc.entries,
        },
        text_encoding="cp932",
    )
    if rebuilt != source_path.read_bytes():
        raise AssertionError("tiBalloonSp.dat roundtrip mismatch")


def run_ti_balloon_target_encoding_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game00" / "files" / "data" / "tiBalloonSp.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked tiBalloonSp.dat at _pak0_game00/files/data/tiBalloonSp.dat")
    doc = parse_table(source_path, text_encoding="cp932")
    target = next((entry for entry in doc.entries if entry["decoded"]), None)
    if target is None:
        raise AssertionError("No decoded tiBalloonSp.dat entry found")
    target["text"] = "试验气泡GBK"
    rebuilt = compile_table(
        {
            "format": doc.format,
            "table_name": doc.table_name,
            "source_path": doc.source_path,
            "key_mode": doc.key_mode,
            "key_seed_u32": doc.key_seed_u32,
            "record_size": doc.record_size,
            "entries": doc.entries,
        },
        text_encoding="gbk",
    )
    reparsed = parse_table_bytes(
        rebuilt,
        table_name=doc.table_name,
        source_path=doc.source_path,
        text_encoding="gbk",
    )
    if not any(entry["text"] == "试验气泡GBK" for entry in reparsed.entries):
        raise AssertionError("GBK write-back text not recovered after tiBalloonSp.dat reparse")


def run_ti_balloon_game01_roundtrip_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game01_verify" / "files" / "data" / "tiBalloon.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked tiBalloon.dat at _pak0_game01_verify/files/data/tiBalloon.dat")
    doc = parse_table(source_path, text_encoding="cp932")
    rebuilt = compile_table(
        {
            "format": doc.format,
            "table_name": doc.table_name,
            "source_path": doc.source_path,
            "key_mode": doc.key_mode,
            "key_seed_u32": doc.key_seed_u32,
            "record_size": doc.record_size,
            "entries": doc.entries,
        },
        text_encoding="cp932",
    )
    if rebuilt != source_path.read_bytes():
        raise AssertionError("tiBalloon.dat roundtrip mismatch")


def run_ti_balloon_game01_target_encoding_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game01_verify" / "files" / "data" / "tiBalloon.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked tiBalloon.dat at _pak0_game01_verify/files/data/tiBalloon.dat")
    doc = parse_table(source_path, text_encoding="cp932")
    target = next((entry for entry in doc.entries if entry["decoded"]), None)
    if target is None:
        raise AssertionError("No decoded tiBalloon.dat entry found")
    target["text"] = "试验气泡GBK"
    rebuilt = compile_table(
        {
            "format": doc.format,
            "table_name": doc.table_name,
            "source_path": doc.source_path,
            "key_mode": doc.key_mode,
            "key_seed_u32": doc.key_seed_u32,
            "record_size": doc.record_size,
            "entries": doc.entries,
        },
        text_encoding="gbk",
    )
    reparsed = parse_table_bytes(
        rebuilt,
        table_name=doc.table_name,
        source_path=doc.source_path,
        text_encoding="gbk",
    )
    if not any(entry["text"] == "试验气泡GBK" for entry in reparsed.entries):
        raise AssertionError("GBK write-back text not recovered after tiBalloon.dat reparse")


def run_bttext_outer_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game00" / "files" / "data" / "BtText.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked BtText.dat at _pak0_game00/files/data/BtText.dat")
    doc = probe_bttext(source_path)
    if doc.raw_header["magic_ascii"] != "TSCR":
        raise AssertionError("BtText raw outer magic mismatch")
    if doc.tuta_header["magic_ascii"] != "TUTA":
        raise AssertionError("BtText decoded root magic mismatch")
    if doc.txt0_header["magic_ascii"] != "TXT0":
        raise AssertionError("BtText TXT0 string-pool magic mismatch")
    string_values = {entry["text"] for entry in doc.txt0_strings if entry["decoded"]}
    for expected in {"Difficulty", "体力", "霊力"}:
        if expected not in string_values:
            raise AssertionError(f"BtText TXT0 string pool missing {expected!r}")
    if rebuild_bttext(doc) != source_path.read_bytes():
        raise AssertionError("BtText outer roundtrip mismatch")


def run_bttext_text_roundtrip_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game00" / "files" / "data" / "BtText.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked BtText.dat at _pak0_game00/files/data/BtText.dat")
    doc = parse_bttext_text(source_path, text_encoding="cp932")
    rebuilt = compile_bttext(
        {
            "format": doc.format,
            "source_path": doc.source_path,
            "source_text_encoding": doc.source_text_encoding,
            "raw_header": doc.raw_header,
            "tuta_header": doc.tuta_header,
            "txt0_header": doc.txt0_header,
            "entries": doc.entries,
        },
        text_encoding="cp932",
    )
    if rebuilt != source_path.read_bytes():
        raise AssertionError("BtText text roundtrip mismatch")


def run_bttext_text_patch_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game00" / "files" / "data" / "BtText.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked BtText.dat at _pak0_game00/files/data/BtText.dat")
    doc = parse_bttext_text(source_path, text_encoding="cp932")
    target = next((entry for entry in doc.entries if entry["decoded"] and entry["text"] == "Difficulty"), None)
    if target is None:
        raise AssertionError("No stable BtText text entry found for cp932 patch regression")
    target["text"] = "難易度テスト拡張版"
    rebuilt = compile_bttext(
        {
            "format": doc.format,
            "source_path": doc.source_path,
            "source_text_encoding": doc.source_text_encoding,
            "raw_header": doc.raw_header,
            "tuta_header": doc.tuta_header,
            "txt0_header": doc.txt0_header,
            "entries": doc.entries,
        },
        text_encoding="cp932",
    )
    temp_dir = make_temp_dir(title_root, "bttext_patch")
    try:
        rebuilt_path = temp_dir / "BtText_patched.dat"
        rebuilt_path.write_bytes(rebuilt)
        reparsed = parse_bttext_text(rebuilt_path, text_encoding="cp932")
    finally:
        cleanup_temp_dir(temp_dir)
    if not any(entry["text"] == "難易度テスト拡張版" for entry in reparsed.entries):
        raise AssertionError("Patched BtText text not recovered after reparse")
    if not any(entry["text"] == "体力" for entry in reparsed.entries):
        raise AssertionError("Neighbor BtText text was not preserved after variable-length patch")


def run_bttext_target_encoding_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game00" / "files" / "data" / "BtText.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked BtText.dat at _pak0_game00/files/data/BtText.dat")
    doc = parse_bttext_text(source_path, text_encoding="cp932")
    target = next((entry for entry in doc.entries if entry["decoded"] and entry["text"] == "Difficulty"), None)
    if target is None:
        raise AssertionError("No stable BtText text entry found for gbk patch regression")
    target["text"] = "难度测试扩展文本"
    rebuilt = compile_bttext(
        {
            "format": doc.format,
            "source_path": doc.source_path,
            "text_encoding": "gbk",
            "source_text_encoding": doc.source_text_encoding,
            "raw_header": doc.raw_header,
            "tuta_header": doc.tuta_header,
            "txt0_header": doc.txt0_header,
            "entries": doc.entries,
        },
        text_encoding="gbk",
    )
    temp_dir = make_temp_dir(title_root, "bttext_gbk")
    try:
        rebuilt_path = temp_dir / "BtText_gbk.dat"
        rebuilt_path.write_bytes(rebuilt)
        reparsed = parse_bttext_text(rebuilt_path, text_encoding="gbk")
    finally:
        cleanup_temp_dir(temp_dir)
    if not any(entry["text"] == "难度测试扩展文本" for entry in reparsed.entries):
        raise AssertionError("GBK BtText text not recovered after reparse")


def run_bttext_game01_roundtrip_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game01_verify" / "files" / "data" / "BtText.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked game01 BtText.dat at _pak0_game01_verify/files/data/BtText.dat")
    doc = parse_bttext_text(source_path, text_encoding="cp932")
    rebuilt = compile_bttext(
        {
            "format": doc.format,
            "source_path": doc.source_path,
            "source_text_encoding": doc.source_text_encoding,
            "raw_header": doc.raw_header,
            "tuta_header": doc.tuta_header,
            "txt0_header": doc.txt0_header,
            "entries": doc.entries,
        },
        text_encoding="cp932",
    )
    if rebuilt != source_path.read_bytes():
        raise AssertionError("game01 BtText.dat text roundtrip mismatch")


def run_scr_outer_regression(title_root: Path) -> None:
    files_root = title_root / "_pak0_game00" / "files"
    samples = [
        ("script/global.scr", {"fade"}),
        ("script/Battle.scr", {"fade", "start", "t_01"}),
    ]
    for file_name, expected_literals in samples:
        source_path = files_root / file_name
        if not source_path.is_file():
            raise AssertionError(f"Expected unpacked {file_name} at _pak0_game00/files/{file_name}")
        doc = probe_scr(source_path)
        if doc.raw_header["magic_ascii"] != "SCR ":
            raise AssertionError(f"{file_name} raw outer magic mismatch")
        if int(doc.raw_header["codec_mode_u32"]) != 2:
            raise AssertionError(f"{file_name} codec mode mismatch")
        ascii_literals = {item["text"] for item in doc.ascii_literals}
        if not expected_literals.issubset(ascii_literals):
            raise AssertionError(f"{file_name} decoded payload is missing expected literals")
        if rebuild_scr(doc) != source_path.read_bytes():
            raise AssertionError(f"{file_name} outer roundtrip mismatch")


def run_scr_text_candidate_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game00" / "files" / "script" / "start.scr"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked start.scr at _pak0_game00/files/script/start.scr")
    doc = parse_scr_text(source_path, text_encoding="cp932")
    entry_by_offset = {int(entry["offset"]): entry for entry in doc.entries}
    for expected_offset in {361, 511}:
        if expected_offset not in entry_by_offset:
            raise AssertionError(f"SCR text candidate export missing expected offset 0x{expected_offset:X}")
        entry = entry_by_offset[expected_offset]
        if str(entry.get("patch_mode")) != "section_rebuild_expandable":
            raise AssertionError("Unexpected SCR text patch mode metadata")
        if int(entry.get("capacity_bytes", 0)) != int(entry["length"]):
            raise AssertionError("Unexpected SCR text capacity metadata")
        if int(entry.get("in_place_capacity_bytes", 0)) != int(entry["length"]):
            raise AssertionError("Unexpected SCR text in-place capacity metadata")
        if not bool(entry.get("supports_expansion_rebuild", False)):
            raise AssertionError("SCR text candidate is missing expansion rebuild capability metadata")
        rebuild_impact = entry.get("rebuild_impact")
        if not isinstance(rebuild_impact, dict):
            raise AssertionError("SCR text candidate is missing rebuild impact metadata")
        if "anchor_offset" not in rebuild_impact:
            raise AssertionError("SCR rebuild impact metadata is missing anchor offset")
        if "outer_decoded_payload_size_field_offset" not in rebuild_impact:
            raise AssertionError("SCR rebuild impact metadata is missing outer size field")
        if "sec3_length_field_offset" not in rebuild_impact:
            raise AssertionError("SCR rebuild impact metadata is missing sec3 length field")
        if "sec4_impacted_indices_if_expand" not in rebuild_impact or "sec5_impacted_indices_if_expand" not in rebuild_impact:
            raise AssertionError("SCR rebuild impact metadata is missing impacted index arrays")
        if "sec3_u32_in_range_count_if_expand" not in rebuild_impact:
            raise AssertionError("SCR rebuild impact metadata is missing sec3 in-range u32 count")
        if "sec3_impacted_u32_sample_positions_if_expand" not in rebuild_impact or "sec3_impacted_u32_sample_values_if_expand" not in rebuild_impact:
            raise AssertionError("SCR rebuild impact metadata is missing sec3 impacted u32 sample arrays")


def run_scr_text_patch_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game00" / "files" / "script" / "start.scr"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked start.scr at _pak0_game00/files/script/start.scr")

    sections_fn = parse_scr_sections

    doc = parse_scr_text(source_path, text_encoding="cp932")
    target = next((entry for entry in doc.entries if int(entry["offset"]) == 361), None)
    if target is None:
        raise AssertionError("No stable SCR text candidate found for patch regression")
    target["text"] = "終幕"
    rebuilt = compile_scr_text(
        {
            "format": doc.format,
            "source_path": doc.source_path,
            "text_encoding": doc.text_encoding,
            "raw_header": doc.raw_header,
            "entries": doc.entries,
        },
        text_encoding="cp932",
    )
    temp_dir = make_temp_dir(title_root, "scr_patch")
    try:
        rebuilt_path = temp_dir / "start_patched.scr"
        rebuilt_path.write_bytes(rebuilt)
        reparsed = parse_scr_text(rebuilt_path, text_encoding="cp932")
        rebuilt_sec3 = sections_fn(probe_scr(rebuilt_path).decoded_payload_bytes).sec3_bytes
    finally:
        cleanup_temp_dir(temp_dir)
    expected_bytes = "終幕".encode("cp932")
    patched_bytes = rebuilt_sec3[361 : 361 + len(expected_bytes)]
    if patched_bytes != expected_bytes:
        raise AssertionError("Patched SCR in-place bytes not recovered after rebuild")
    if not any(int(entry["offset"]) == 511 for entry in reparsed.entries):
        raise AssertionError("Neighbor SCR text candidate was not preserved after in-place patch")

    battle_source = title_root / "_pak0_game00" / "files" / "script" / "Battle.scr"
    if not battle_source.is_file():
        raise AssertionError("Expected unpacked Battle.scr at _pak0_game00/files/script/Battle.scr")
    long_doc = parse_scr_text(battle_source, text_encoding="cp932")
    long_target = next((entry for entry in long_doc.entries if int(entry["offset"]) == 2728), None)
    if long_target is None:
        raise AssertionError("No stable Battle.scr text candidate found for long patch regression")
    long_text = "解放イベント拡張テキスト"
    long_target["text"] = long_text
    rebuilt_long = compile_scr_text(
        {
            "format": long_doc.format,
            "source_path": long_doc.source_path,
            "text_encoding": long_doc.text_encoding,
            "raw_header": long_doc.raw_header,
            "entries": long_doc.entries,
        },
        text_encoding="cp932",
    )
    temp_dir = make_temp_dir(title_root, "scr_long_patch")
    try:
        rebuilt_path = temp_dir / "Battle_patched.scr"
        rebuilt_path.write_bytes(rebuilt_long)
        reparsed_long = parse_scr_text(rebuilt_path, text_encoding="cp932")
    finally:
        cleanup_temp_dir(temp_dir)
    texts = {entry["text"] for entry in reparsed_long.entries}
    if long_text not in texts:
        raise AssertionError("Patched Battle.scr long text was not recovered after rebuild")
    if "座敷童の解放" not in texts:
        raise AssertionError("Neighbor Battle.scr text candidate was not preserved after long patch")

    multi_doc = parse_scr_text(battle_source, text_encoding="cp932")
    multi_targets = {
        2728: "解放イベント拡張テキストA",
        2806: "座敷童拡張イベントテキストB",
    }
    for entry in multi_doc.entries:
        replacement = multi_targets.get(int(entry["offset"]))
        if replacement is not None:
            entry["text"] = replacement
    rebuilt_multi = compile_scr_text(
        {
            "format": multi_doc.format,
            "source_path": multi_doc.source_path,
            "text_encoding": multi_doc.text_encoding,
            "raw_header": multi_doc.raw_header,
            "entries": multi_doc.entries,
        },
        text_encoding="cp932",
    )
    temp_dir = make_temp_dir(title_root, "scr_multi_long_patch")
    try:
        rebuilt_path = temp_dir / "Battle_multi_patched.scr"
        rebuilt_path.write_bytes(rebuilt_multi)
        reparsed_multi = parse_scr_text(rebuilt_path, text_encoding="cp932")
    finally:
        cleanup_temp_dir(temp_dir)
    texts_multi = {entry["text"] for entry in reparsed_multi.entries}
    for replacement in multi_targets.values():
        if replacement not in texts_multi:
            raise AssertionError("Patched Battle.scr multi-entry long text was not recovered after rebuild")
    for preserved_text in {"海月の解放", "靫蔓の解放"}:
        if preserved_text not in texts_multi:
            raise AssertionError(f"Neighbor Battle.scr text candidate {preserved_text!r} was not preserved after multi-entry long patch")

    start_multi_doc = parse_scr_text(source_path, text_encoding="cp932")
    start_multi_targets = {
        361: "終幕拡張テキストA",
        511: "イージーモード追加メッセージ拡張B",
    }
    for entry in start_multi_doc.entries:
        replacement = start_multi_targets.get(int(entry["offset"]))
        if replacement is not None:
            entry["text"] = replacement
    rebuilt_start_multi = compile_scr_text(
        {
            "format": start_multi_doc.format,
            "source_path": start_multi_doc.source_path,
            "text_encoding": start_multi_doc.text_encoding,
            "raw_header": start_multi_doc.raw_header,
            "entries": start_multi_doc.entries,
        },
        text_encoding="cp932",
    )
    temp_dir = make_temp_dir(title_root, "scr_start_multi_long_patch")
    try:
        rebuilt_path = temp_dir / "start_multi_patched.scr"
        rebuilt_path.write_bytes(rebuilt_start_multi)
        reparsed_start_multi = parse_scr_text(rebuilt_path, text_encoding="cp932")
    finally:
        cleanup_temp_dir(temp_dir)
    texts_start_multi = {entry["text"] for entry in reparsed_start_multi.entries}
    for replacement in start_multi_targets.values():
        if replacement not in texts_start_multi:
            raise AssertionError("Patched start.scr multi-entry long text was not recovered after rebuild")
    for preserved_text in {"スタッフロール", "召喚妖怪に『アネゴラス』が追加されました。"}:
        if preserved_text not in texts_start_multi:
            raise AssertionError(f"Neighbor start.scr text candidate {preserved_text!r} was not preserved after multi-entry long patch")

    t01_source = title_root / "_pak0_game00" / "files" / "script" / "t_01.scr"
    if not t01_source.is_file():
        raise AssertionError("Expected unpacked t_01.scr at _pak0_game00/files/script/t_01.scr")
    t01_doc = parse_scr_text(t01_source, text_encoding="cp932")
    t01_target = next((entry for entry in t01_doc.entries if int(entry["offset"]) == 1032), None)
    if t01_target is None:
        raise AssertionError("No stable t_01.scr text candidate found for sec3 impact regression")
    impact_before = t01_target.get("rebuild_impact", {})
    sec3_positions = list(impact_before.get("sec3_high_confidence_impacted_positions_if_expand", []))
    sec3_values = list(impact_before.get("sec3_high_confidence_impacted_values_if_expand", []))
    if not sec3_positions or not sec3_values:
        raise AssertionError("t_01.scr target is missing sec3 high-confidence impact metadata")
    t01_new_text = "飛天丸長文拡張検証"
    t01_target["text"] = t01_new_text
    rebuilt_t01 = compile_scr_text(
        {
            "format": t01_doc.format,
            "source_path": t01_doc.source_path,
            "text_encoding": t01_doc.text_encoding,
            "raw_header": t01_doc.raw_header,
            "entries": t01_doc.entries,
        },
        text_encoding="cp932",
    )
    delta = len(t01_new_text.encode("cp932")) - int(t01_target["length"])
    temp_dir = make_temp_dir(title_root, "scr_sec3_whitelist_patch")
    try:
        rebuilt_path = temp_dir / "t01_patched.scr"
        rebuilt_path.write_bytes(rebuilt_t01)
        reparsed_t01 = parse_scr_text(rebuilt_path, text_encoding="cp932")
        rebuilt_sections = parse_scr_sections(probe_scr(rebuilt_path).decoded_payload_bytes)
    finally:
        cleanup_temp_dir(temp_dir)
    if t01_new_text not in {entry["text"] for entry in reparsed_t01.entries}:
        raise AssertionError("Patched t_01.scr text was not recovered after rebuild")
    for absolute_pos, original_value in zip(sec3_positions, sec3_values):
        rel_pos = absolute_pos - rebuilt_sections.sec3_data_offset
        rebuilt_value = int.from_bytes(rebuilt_sections.sec3_bytes[rel_pos : rel_pos + 4], "little")
        if rebuilt_value != original_value + delta:
            raise AssertionError("t_01.scr sec3 high-confidence impacted field was not rebuilt with the expected delta")

    pattern_samples = [
        ("t_01.scr", 1032, "飛天丸"),
    ]
    for sample_name, expected_offset, expected_text_prefix in pattern_samples:
        sample_path = title_root / "_pak0_game00" / "files" / "script" / sample_name
        if not sample_path.is_file():
            raise AssertionError(f"Expected unpacked {sample_name} at _pak0_game00/files/script/{sample_name}")
        sample_doc = parse_scr_text(sample_path, text_encoding="cp932")
        sample_target = next(
            (
                entry
                for entry in sample_doc.entries
                if int(entry["offset"]) == expected_offset and str(entry["text"]).startswith(expected_text_prefix)
            ),
            None,
        )
        if sample_target is None:
            raise AssertionError(f"No stable {sample_name} text candidate found for sec3 pattern regression")
        sample_impact = sample_target.get("rebuild_impact", {})
        sample_positions = list(sample_impact.get("sec3_high_confidence_impacted_positions_if_expand", []))
        sample_values = list(sample_impact.get("sec3_high_confidence_impacted_values_if_expand", []))
        if not sample_positions or not sample_values:
            raise AssertionError(f"{sample_name} is missing sec3 high-confidence impact metadata")
        new_text = f"{sample_target['text']}長文検証"
        sample_target["text"] = new_text
        rebuilt_sample = compile_scr_text(
            {
                "format": sample_doc.format,
                "source_path": sample_doc.source_path,
                "text_encoding": sample_doc.text_encoding,
                "raw_header": sample_doc.raw_header,
                "entries": sample_doc.entries,
            },
            text_encoding="cp932",
        )
        sample_delta = len(new_text.encode("cp932")) - int(sample_target["length"])
        temp_dir = make_temp_dir(title_root, f"scr_sec3_pattern_{sample_name}")
        try:
            rebuilt_path = temp_dir / f"{sample_name}.patched.scr"
            rebuilt_path.write_bytes(rebuilt_sample)
            reparsed_sample = parse_scr_text(rebuilt_path, text_encoding="cp932")
            rebuilt_sections = parse_scr_sections(probe_scr(rebuilt_path).decoded_payload_bytes)
        finally:
            cleanup_temp_dir(temp_dir)
        if new_text not in {entry["text"] for entry in reparsed_sample.entries}:
            raise AssertionError(f"Patched {sample_name} text was not recovered after rebuild")
        for absolute_pos, original_value in zip(sample_positions, sample_values):
            rel_pos = absolute_pos - rebuilt_sections.sec3_data_offset
            rebuilt_value = int.from_bytes(rebuilt_sections.sec3_bytes[rel_pos : rel_pos + 4], "little")
            if rebuilt_value != original_value + sample_delta:
                raise AssertionError(f"{sample_name} sec3 high-confidence impacted field was not rebuilt with the expected delta")


def run_scr_exhaustive_long_text_regression(title_root: Path) -> None:
    samples = [
        (title_root / "_pak0_game00" / "files" / "script" / "start.scr", "長文互換確認"),
        (title_root / "_pak0_game00" / "files" / "script" / "Battle.scr", "長文互換確認"),
    ]
    for source_path, suffix in samples:
        if not source_path.is_file():
            raise AssertionError(f"Expected unpacked sample at {source_path}")
        base_doc = parse_scr_text(source_path, text_encoding="cp932")
        base_count = len(base_doc.entries)
        offsets = [int(entry["offset"]) for entry in base_doc.entries]

        sample_temp_root = make_temp_dir(title_root, f"scr_exhaustive_{source_path.stem}")
        try:
            for offset in offsets:
                work_doc = parse_scr_text(source_path, text_encoding="cp932")
                target = next(entry for entry in work_doc.entries if int(entry["offset"]) == offset)
                target["text"] = f"{target['text']}{suffix}{offset:x}"
                rebuilt = compile_scr_text(
                    {
                        "format": work_doc.format,
                        "source_path": work_doc.source_path,
                        "text_encoding": work_doc.text_encoding,
                        "raw_header": work_doc.raw_header,
                        "entries": work_doc.entries,
                    },
                    text_encoding="cp932",
                )
                rebuilt_path = sample_temp_root / f"single_{offset:x}.scr"
                rebuilt_path.write_bytes(rebuilt)
                reparsed = parse_scr_text(rebuilt_path, text_encoding="cp932")
                texts = {entry["text"] for entry in reparsed.entries}
                if target["text"] not in texts:
                    raise AssertionError(f"Exhaustive SCR single-entry long patch was not recovered for 0x{offset:X}")
                if len(reparsed.entries) != base_count:
                    raise AssertionError("Exhaustive SCR single-entry long patch changed entry count")

            for index_a, offset_a in enumerate(offsets):
                for offset_b in offsets[index_a + 1 :]:
                    work_doc = parse_scr_text(source_path, text_encoding="cp932")
                    expected_texts: list[str] = []
                    for entry in work_doc.entries:
                        if int(entry["offset"]) == offset_a:
                            entry["text"] = f"{entry['text']}{suffix}A{offset_a:x}"
                            expected_texts.append(entry["text"])
                        elif int(entry["offset"]) == offset_b:
                            entry["text"] = f"{entry['text']}{suffix}B{offset_b:x}"
                            expected_texts.append(entry["text"])
                    rebuilt = compile_scr_text(
                        {
                            "format": work_doc.format,
                            "source_path": work_doc.source_path,
                            "text_encoding": work_doc.text_encoding,
                            "raw_header": work_doc.raw_header,
                            "entries": work_doc.entries,
                        },
                        text_encoding="cp932",
                    )
                    rebuilt_path = sample_temp_root / f"pair_{offset_a:x}_{offset_b:x}.scr"
                    rebuilt_path.write_bytes(rebuilt)
                    reparsed = parse_scr_text(rebuilt_path, text_encoding="cp932")
                    texts = {entry["text"] for entry in reparsed.entries}
                    for expected_text in expected_texts:
                        if expected_text not in texts:
                            raise AssertionError(
                                f"Exhaustive SCR multi-entry long patch was not recovered for 0x{offset_a:X}, 0x{offset_b:X}"
                            )
                    if len(reparsed.entries) != base_count:
                        raise AssertionError("Exhaustive SCR multi-entry long patch changed entry count")
        finally:
            cleanup_temp_dir(sample_temp_root)


def run_scr_broad_long_text_regression(title_root: Path) -> None:
    script_root = title_root / "_pak0_game00" / "files" / "script"
    sample_temp_root = make_temp_dir(title_root, "scr_broad_long_patch")
    try:
        def is_resource_like_text(text: str) -> bool:
            prefixes = ("ＣＧ：", "ＣＧ:", "CG:", "CG：")
            return text.startswith(prefixes)

        broad_sample_names = {
            "start.scr",
            "Battle.scr",
            "t_01.scr",
            "t_02.scr",
            "t_05.scr",
            "t_14.scr",
            "ed00.scr",
            "ed01.scr",
            "hc_spider.scr",
            "hc_tengu.scr",
            "hh_ogre2.scr",
            "hh_kraken2.scr",
            "hh_succu2.scr",
            "hs_cure1a.scr",
            "hs_pure1.scr",
            "add_t.scr",
        }
        for source_path in sorted(script_root.glob("*.scr")):
            if source_path.name not in broad_sample_names:
                continue
            base_doc = parse_scr_text(source_path, text_encoding="cp932")
            if not base_doc.entries:
                continue
            if any(int(entry.get("record_offset", -1)) == 0 for entry in base_doc.entries):
                continue
            base_count = len(base_doc.entries)
            content_entries = [
                entry
                for entry in base_doc.entries
                if int(entry.get("record_offset", -1)) != 0 and not is_resource_like_text(str(entry["text"]))
            ]
            if not content_entries:
                continue
            targets: list[tuple[int, str]] = [(int(content_entries[0]["offset"]), "first")]
            if len(content_entries) > 1:
                targets.append((int(content_entries[-1]["offset"]), "last"))
            for target_offset, label in targets:
                work_doc = parse_scr_text(source_path, text_encoding="cp932")
                target = next(entry for entry in work_doc.entries if int(entry["offset"]) == target_offset)
                target["text"] = f"{target['text']}長文回帰{source_path.stem}{label}"
                rebuilt = compile_scr_text(
                    {
                        "format": work_doc.format,
                        "source_path": work_doc.source_path,
                        "text_encoding": work_doc.text_encoding,
                        "raw_header": work_doc.raw_header,
                        "entries": work_doc.entries,
                    },
                    text_encoding="cp932",
                )
                rebuilt_path = sample_temp_root / f"{source_path.stem}_{label}.scr"
                rebuilt_path.write_bytes(rebuilt)
                reparsed = parse_scr_text(rebuilt_path, text_encoding="cp932")
                texts = {entry["text"] for entry in reparsed.entries}
                if target["text"] not in texts:
                    raise AssertionError(
                        f"Broad SCR long patch was not recovered for {source_path.name} {label} entry 0x{target_offset:X}"
                    )
                if len(reparsed.entries) != base_count:
                    raise AssertionError(f"Broad SCR long patch changed entry count for {source_path.name}")
    finally:
        cleanup_temp_dir(sample_temp_root)


def run_scr_all_scripts_edge_long_text_regression(
    title_root: Path,
    *,
    chunk_index: int = 0,
    chunk_count: int = 1,
) -> None:
    if chunk_count <= 0:
        raise ValueError("chunk_count must be positive")
    if chunk_index < 0 or chunk_index >= chunk_count:
        raise ValueError("chunk_index must be within [0, chunk_count)")

    script_root = title_root / "_pak0_game00" / "files" / "script"
    temp_root = make_temp_dir(title_root, f"scr_all_edge_chunk_{chunk_index + 1}_of_{chunk_count}")
    try:
        script_paths = sorted(script_root.glob("*.scr"))
        for script_idx, source_path in enumerate(script_paths):
            if script_idx % chunk_count != chunk_index:
                continue

            base_doc = parse_scr_text(source_path, text_encoding="cp932")
            if not base_doc.entries:
                continue

            content_entries = [
                entry
                for entry in base_doc.entries
                if int(entry.get("record_offset", -1)) != 0
                and not str(entry["text"]).startswith(("ＣＧ：", "ＣＧ:", "CG:", "CG："))
            ]
            if not content_entries:
                continue

            targets = [(int(content_entries[0]["offset"]), "first")]
            if len(content_entries) > 1:
                targets.append((int(content_entries[-1]["offset"]), "last"))

            base_count = len(base_doc.entries)
            for target_offset, label in targets:
                work_doc = parse_scr_text(source_path, text_encoding="cp932")
                target = next(entry for entry in work_doc.entries if int(entry["offset"]) == target_offset)
                target["text"] = f"{target['text']}長文全脚本{source_path.stem}{label}"
                rebuilt = compile_scr_text(
                    {
                        "format": work_doc.format,
                        "source_path": work_doc.source_path,
                        "text_encoding": work_doc.text_encoding,
                        "raw_header": work_doc.raw_header,
                        "entries": work_doc.entries,
                    },
                    text_encoding="cp932",
                )
                rebuilt_path = temp_root / f"{source_path.stem}_{label}.scr"
                rebuilt_path.write_bytes(rebuilt)
                reparsed = parse_scr_text(rebuilt_path, text_encoding="cp932")
                if target["text"] not in {entry["text"] for entry in reparsed.entries}:
                    raise AssertionError(
                        f"All-scripts edge long patch was not recovered for {source_path.name} {label} entry 0x{target_offset:X}"
                    )
                if len(reparsed.entries) != base_count:
                    raise AssertionError(f"All-scripts edge long patch changed entry count for {source_path.name}")
    finally:
        cleanup_temp_dir(temp_root)


def run_scr_single_entry_long_text_regression(
    title_root: Path,
    *,
    chunk_index: int = 0,
    chunk_count: int = 1,
    mixed_only: bool = False,
    script_start: int = 0,
    script_end: int = -1,
    entry_start: int = 0,
    entry_end: int = -1,
) -> None:
    if chunk_count <= 0:
        raise ValueError("chunk_count must be positive")
    if chunk_index < 0 or chunk_index >= chunk_count:
        raise ValueError("chunk_index must be within [0, chunk_count)")
    if script_start < 0:
        raise ValueError("script_start must be non-negative")
    if entry_start < 0:
        raise ValueError("entry_start must be non-negative")

    script_root = title_root / "_pak0_game00" / "files" / "script"
    temp_root = make_temp_dir(title_root, f"scr_single_entry_chunk_{chunk_index + 1}_of_{chunk_count}")
    try:
        script_paths = [path for idx, path in enumerate(sorted(script_root.glob("*.scr"))) if idx % chunk_count == chunk_index]
        end = len(script_paths) if script_end < 0 else script_end
        script_paths = script_paths[script_start:end]
        for source_path in script_paths:

            base_doc = parse_scr_text(source_path, text_encoding="cp932")
            if not base_doc.entries:
                continue

            has_mixed_header = any(int(entry.get("record_offset", -1)) == 0 for entry in base_doc.entries)
            if mixed_only and not has_mixed_header:
                continue

            content_entries = [
                entry
                for entry in base_doc.entries
                if int(entry.get("record_offset", -1)) != 0
                and not str(entry["text"]).startswith(("ＣＧ：", "ＣＧ:", "CG:", "CG："))
            ]
            if not content_entries:
                continue
            local_end = len(content_entries) if entry_end < 0 else min(entry_end, len(content_entries))
            local_entries = content_entries[entry_start:local_end]
            if not local_entries:
                continue

            base_count = len(base_doc.entries)
            base_entries = deepcopy(base_doc.entries)
            for idx, entry in enumerate(local_entries, start=entry_start):
                work_entries = deepcopy(base_entries)
                target = next(item for item in work_entries if int(item["offset"]) == int(entry["offset"]))
                target["text"] = f"{target['text']}長文single{source_path.stem}{idx}"
                rebuilt = compile_scr_text(
                    {
                        "format": base_doc.format,
                        "source_path": base_doc.source_path,
                        "text_encoding": base_doc.text_encoding,
                        "raw_header": base_doc.raw_header,
                        "entries": work_entries,
                    },
                    text_encoding="cp932",
                )
                reparsed = parse_scr_text_bytes(
                    rebuilt,
                    source_path=f"{source_path}:{idx}",
                    text_encoding="cp932",
                    include_impact=False,
                )
                if target["text"] not in {item["text"] for item in reparsed.entries}:
                    raise AssertionError(
                        f"All-scripts single-entry long patch was not recovered for {source_path.name} entry 0x{int(entry['offset']):X}"
                    )
                if len(reparsed.entries) != base_count:
                    raise AssertionError(
                        f"All-scripts single-entry long patch changed entry count for {source_path.name}: {base_count}->{len(reparsed.entries)}"
                    )
    finally:
        cleanup_temp_dir(temp_root)


def run_scr_single_entry_short_text_regression(
    title_root: Path,
    *,
    chunk_index: int = 0,
    chunk_count: int = 1,
    mixed_only: bool = False,
    script_start: int = 0,
    script_end: int = -1,
    entry_start: int = 0,
    entry_end: int = -1,
) -> None:
    if chunk_count <= 0:
        raise ValueError("chunk_count must be positive")
    if chunk_index < 0 or chunk_index >= chunk_count:
        raise ValueError("chunk_index must be within [0, chunk_count)")
    if script_start < 0:
        raise ValueError("script_start must be non-negative")
    if entry_start < 0:
        raise ValueError("entry_start must be non-negative")

    script_root = title_root / "_pak0_game00" / "files" / "script"
    temp_root = make_temp_dir(title_root, f"scr_single_short_chunk_{chunk_index + 1}_of_{chunk_count}")
    try:
        script_paths = [path for idx, path in enumerate(sorted(script_root.glob("*.scr"))) if idx % chunk_count == chunk_index]
        end = len(script_paths) if script_end < 0 else script_end
        script_paths = script_paths[script_start:end]
        for source_path in script_paths:

            base_doc = parse_scr_text(source_path, text_encoding="cp932")
            if not base_doc.entries:
                continue

            has_mixed_header = any(int(entry.get("record_offset", -1)) == 0 for entry in base_doc.entries)
            if mixed_only and not has_mixed_header:
                continue

            content_entries = [
                entry
                for entry in base_doc.entries
                if int(entry.get("record_offset", -1)) != 0
                and not str(entry["text"]).startswith(("ＣＧ：", "ＣＧ:", "CG:", "CG："))
            ]
            if not content_entries:
                continue
            local_end = len(content_entries) if entry_end < 0 else min(entry_end, len(content_entries))
            local_entries = content_entries[entry_start:local_end]
            if not local_entries:
                continue

            base_count = len(base_doc.entries)
            base_entries = deepcopy(base_doc.entries)
            for idx, entry in enumerate(local_entries, start=entry_start):
                original_text = str(entry["text"])
                if len(original_text) <= 1:
                    continue
                replacement = original_text[: max(1, len(original_text) // 2)]
                # Keep replacement inside cp932-safe BMP text and avoid host-shell encoding ambiguity.
                replacement = replacement.encode("cp932", errors="ignore").decode("cp932", errors="ignore")
                if not replacement:
                    continue
                replacement_bytes = replacement.encode("cp932")
                original_length = int(entry["length"])
                target_offset = int(entry["offset"])
                neighbor_offsets = {
                    int(item["offset"])
                    for item in base_doc.entries
                    if int(item["offset"]) != target_offset
                    and abs(int(item["offset"]) - target_offset) <= 512
                }
                work_entries = deepcopy(base_entries)
                target = next(item for item in work_entries if int(item["offset"]) == target_offset)
                target["text"] = replacement
                rebuilt = compile_scr_text(
                    {
                        "format": base_doc.format,
                        "source_path": base_doc.source_path,
                        "text_encoding": base_doc.text_encoding,
                        "raw_header": base_doc.raw_header,
                        "entries": work_entries,
                    },
                    text_encoding="cp932",
                )
                reparsed = parse_scr_text_bytes(
                    rebuilt,
                    source_path=f"{source_path}:short:{idx}",
                    text_encoding="cp932",
                    include_impact=False,
                )
                rebuilt_sections = parse_scr_sections(probe_scr_bytes(rebuilt, source_path=f"{source_path}:short:{idx}").decoded_payload_bytes)
                target_slice = rebuilt_sections.sec3_bytes[target_offset : target_offset + original_length]
                expected_slice = replacement_bytes + (b"\x00" * (original_length - len(replacement_bytes)))
                if target_slice != expected_slice:
                    raise AssertionError(
                        f"All-scripts single-entry short patch bytes were not rebuilt correctly for {source_path.name} entry 0x{target_offset:X}"
                    )
                if len(reparsed.entries) != base_count:
                    raise AssertionError(
                        f"All-scripts single-entry short patch changed entry count for {source_path.name}: {base_count}->{len(reparsed.entries)}"
                    )
                reparsed_offsets = {int(item["offset"]) for item in reparsed.entries}
                if not neighbor_offsets.issubset(reparsed_offsets):
                    raise AssertionError(
                        f"All-scripts single-entry short patch dropped neighboring entries for {source_path.name} entry 0x{target_offset:X}"
                    )
    finally:
        cleanup_temp_dir(temp_root)


def run_text_patch_helper_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game00" / "files" / "data" / "BtText.dat"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked BtText.dat at _pak0_game00/files/data/BtText.dat")
    temp_root = make_temp_dir(title_root, "patch_helper")
    try:
        json_path = temp_root / "BtText.json"
        patched_json_path = temp_root / "BtText_patched.json"
        rebuilt_path = temp_root / "BtText_patched.dat"

        doc = parse_bttext_text(source_path, text_encoding="cp932")
        from script.tev2_bttext import write_text_doc

        write_text_doc(json_path, doc)
        patch_text_doc(
            json_path,
            patched_json_path,
            entry_index=12,
            text="TEST PATCH",
        )
        patched_doc = json.loads(patched_json_path.read_text(encoding="utf-8"))
        rebuilt = compile_bttext(patched_doc, text_encoding="cp932")
        rebuilt_path.write_bytes(rebuilt)
        reparsed = parse_bttext_text(rebuilt_path, text_encoding="cp932")
        if not any(entry["text"] == "TEST PATCH" for entry in reparsed.entries):
            raise AssertionError("Patch helper did not patch BtText entry as expected")
    finally:
        cleanup_temp_dir(temp_root)


def run_text_fit_helper_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game00" / "files" / "script" / "start.scr"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked start.scr at _pak0_game00/files/script/start.scr")
    temp_root = make_temp_dir(title_root, "fit_helper")
    try:
        from script.tev2_scr import write_text_doc

        text_doc = parse_scr_text(source_path, text_encoding="cp932")
        json_path = temp_root / "start.json"
        write_text_doc(json_path, text_doc)
        fit = check_text_fit(json_path, entry_offset=361, text="終幕", text_encoding="cp932")
        if not fit["fits"]:
            raise AssertionError("Text fit helper rejected a known fitting SCR replacement")
        if not fit["fits_in_place"]:
            raise AssertionError("Text fit helper unexpectedly rejected a known in-place SCR replacement")
        if fit["requires_expansion_rebuild"]:
            raise AssertionError("Text fit helper incorrectly marked a fitting SCR replacement as expansion-only")
        overflow = check_text_fit(
            json_path,
            entry_offset=361,
            text="これは長すぎるテキストです",
            text_encoding="cp932",
        )
        if overflow["fits"]:
            raise AssertionError("Text fit helper accepted a known overflowing SCR replacement as in-place")
        if overflow["fits_in_place"]:
            raise AssertionError("Text fit helper accepted a known overflowing SCR replacement as in-place")
        if not overflow["requires_expansion_rebuild"]:
            raise AssertionError("Text fit helper did not mark an overflowing SCR replacement as expansion rebuild")
        if not overflow["can_rebuild_with_expansion"]:
            raise AssertionError("Text fit helper did not expose expansion rebuild for overflowing SCR replacement")
    finally:
        cleanup_temp_dir(temp_root)


def run_text_fit_report_regression(title_root: Path) -> None:
    source_path = title_root / "_pak0_game00" / "files" / "script" / "start.scr"
    if not source_path.is_file():
        raise AssertionError("Expected unpacked start.scr at _pak0_game00/files/script/start.scr")
    temp_root = make_temp_dir(title_root, "fit_report")
    try:
        from script.tev2_scr import write_text_doc

        text_doc = parse_scr_text(source_path, text_encoding="cp932")
        json_path = temp_root / "start.json"
        report_path = temp_root / "start_fit_report.json"
        write_text_doc(json_path, text_doc)
        build_fit_report(json_path, report_path, extra_bytes=0, text_encoding="cp932")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        entries = report["entries"]
        short = next((entry for entry in entries if int(entry["offset"]) == 361), None)
        long_entry = next((entry for entry in entries if int(entry["offset"]) == 2083), None)
        if short is None or long_entry is None:
            raise AssertionError("Fit report missing expected SCR entries")
        if not short["fits_estimate"]:
            raise AssertionError("Fit report unexpectedly rejected a short SCR entry")
        if not short["fits_in_place_estimate"]:
            raise AssertionError("Fit report unexpectedly rejected a short SCR entry as in-place")
        build_fit_report(json_path, report_path, extra_bytes=4, text_encoding="cp932")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        long_entry = next((entry for entry in report["entries"] if int(entry["offset"]) == 2083), None)
        if long_entry is None:
            raise AssertionError("Fit report missing long SCR entry after growth report")
        if long_entry["fits_estimate"]:
            raise AssertionError("Fit report unexpectedly accepted a long SCR entry after extra byte growth")
        if long_entry["fits_in_place_estimate"]:
            raise AssertionError("Fit report unexpectedly accepted a long SCR entry as in-place after extra byte growth")
        if not long_entry["requires_expansion_rebuild_estimate"]:
            raise AssertionError("Fit report did not mark a long SCR entry as requiring expansion rebuild")
        if not long_entry["can_rebuild_with_expansion_estimate"]:
            raise AssertionError("Fit report did not expose expansion rebuild for a long SCR entry")
    finally:
        cleanup_temp_dir(temp_root)


def run_text_scan_regression(title_root: Path) -> None:
    resource_root = title_root / "_pak0_game00" / "files"
    if not resource_root.is_dir():
        raise AssertionError("Expected unpacked files root at _pak0_game00/files")
    temp_root = make_temp_dir(title_root, "text_scan")
    try:
        output_path = temp_root / "text_scan.json"
        build_text_scan(resource_root, output_path, text_encoding="cp932")
        report = json.loads(output_path.read_text(encoding="utf-8"))
        if report.get("format") != "TE_V2_TEXT_SCAN":
            raise AssertionError("Unexpected text scan report format")
        carriers = report.get("carriers", [])
        carrier_paths = {entry["path"] for entry in carriers}
        for expected_suffix in [
            "_pak0_game00\\files\\data\\tiNameSp.dat",
            "_pak0_game00\\files\\data\\tiBalloonSp.dat",
            "_pak0_game00\\files\\data\\BtText.dat",
            "_pak0_game00\\files\\script\\start.scr",
        ]:
            if not any(path.endswith(expected_suffix) for path in carrier_paths):
                raise AssertionError(f"Text scan is missing expected carrier {expected_suffix}")
    finally:
        cleanup_temp_dir(temp_root)
