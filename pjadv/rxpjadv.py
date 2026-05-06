#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from rxpjadv_py.filename_dat import read_filename_dat
from rxpjadv_py.pack_v2 import build_pack, extract_pack, read_index
from rxpjadv_py.text_manager import export_text, import_legacy_pair, import_text
from rxpjadv_py.textdata import TextData, xor_file


def cmd_pack_list(args: argparse.Namespace) -> None:
    for e in read_index(args.pack):
        print(f"{e.index:04d}  off=0x{e.offset:08X}  size={e.size:8d}  {e.name}")


def cmd_unpack(args: argparse.Namespace) -> None:
    entries = extract_pack(args.pack, args.out, overwrite=args.overwrite)
    print(f"extracted {len(entries)} files -> {args.out}")


def cmd_pack(args: argparse.Namespace) -> None:
    manifest = None
    if args.manifest:
        manifest = [line.strip() for line in Path(args.manifest).read_text(encoding="utf-8").splitlines() if line.strip()]
    entries = build_pack(args.input, args.out, manifest=manifest)
    print(f"packed {len(entries)} files -> {args.out}")


def cmd_text_export(args: argparse.Namespace) -> None:
    entries = export_text(args.textdata, args.scenario, args.out, encoding=args.encoding)
    print(f"exported {len(entries)} text entries -> {args.out}")


def cmd_text_export_legacy(args: argparse.Namespace) -> None:
    entries = export_text(args.textdata, args.scenario, args.msg_json, encoding=args.encoding, legacy_pair_json=True, out_seq_json_path=args.seq_json)
    print(f"exported {len(entries)} text entries -> {args.msg_json}, {args.seq_json}")


def cmd_text_import(args: argparse.Namespace) -> None:
    stats = import_text(
        args.textdata,
        args.scenario,
        args.json,
        out_textdata_path=args.out_textdata,
        out_scenario_path=args.out_scenario,
        encoding=args.encoding,
        strict=not args.no_strict,
        update_name=args.update_name,
    )
    print(stats)


def cmd_text_import_legacy(args: argparse.Namespace) -> None:
    stats = import_legacy_pair(
        args.textdata,
        args.scenario,
        args.msg_json,
        args.seq_json,
        out_textdata_path=args.out_textdata,
        out_scenario_path=args.out_scenario,
        encoding=args.encoding,
    )
    print(stats)


def cmd_textdata_json(args: argparse.Namespace) -> None:
    TextData(args.textdata).dump_json(args.out, encoding=args.encoding)
    print(f"dumped textdata -> {args.out}")


def cmd_xor(args: argparse.Namespace) -> None:
    key = int(args.key, 0)
    xor_file(args.input, args.out, key)
    print(f"xor done: {args.input} -> {args.out}, key=0x{key & 0xFF:02X}")


def cmd_filename(args: argparse.Namespace) -> None:
    for i, name in enumerate(read_filename_dat(args.input, args.encoding)):
        print(f"{i:04d}  {name}")


def main() -> None:
    p = argparse.ArgumentParser(description="Python RxPJADV/PJADV tool: pack, script text, textdata xor")
    sub = p.add_subparsers(required=True)

    sp = sub.add_parser("pack-list", help="list GAMEDAT PAC2 entries")
    sp.add_argument("pack")
    sp.set_defaults(func=cmd_pack_list)

    sp = sub.add_parser("unpack", help="extract GAMEDAT PAC2 archive")
    sp.add_argument("pack")
    sp.add_argument("out")
    sp.add_argument("--overwrite", action="store_true", default=True)
    sp.set_defaults(func=cmd_unpack)

    sp = sub.add_parser("pack", help="build GAMEDAT PAC2 archive from directory")
    sp.add_argument("input")
    sp.add_argument("out")
    sp.add_argument("--manifest", help="optional UTF-8 file list, one relative path per line")
    sp.set_defaults(func=cmd_pack)

    sp = sub.add_parser("text-export", help="export scenario.dat + textdata to unified JSON")
    sp.add_argument("textdata")
    sp.add_argument("scenario")
    sp.add_argument("out")
    sp.add_argument("--encoding", default="cp932")
    sp.set_defaults(func=cmd_text_export)

    sp = sub.add_parser("text-import", help="import unified JSON, append textdata and patch scenario offsets")
    sp.add_argument("textdata")
    sp.add_argument("scenario")
    sp.add_argument("json")
    sp.add_argument("--out-textdata")
    sp.add_argument("--out-scenario")
    sp.add_argument("--encoding", default="cp932")
    sp.add_argument("--no-strict", action="store_true", help="skip invalid entries instead of raising")
    sp.add_argument("--update-name", action="store_true", help="also inject modified name fields")
    sp.set_defaults(func=cmd_text_import)

    sp = sub.add_parser("text-export-legacy", help="export upstream-compatible msg/seq JSON pair")
    sp.add_argument("textdata")
    sp.add_argument("scenario")
    sp.add_argument("msg_json")
    sp.add_argument("seq_json")
    sp.add_argument("--encoding", default="cp932")
    sp.set_defaults(func=cmd_text_export_legacy)

    sp = sub.add_parser("text-import-legacy", help="import upstream-compatible msg/seq JSON pair")
    sp.add_argument("textdata")
    sp.add_argument("scenario")
    sp.add_argument("msg_json")
    sp.add_argument("seq_json")
    sp.add_argument("--out-textdata")
    sp.add_argument("--out-scenario")
    sp.add_argument("--encoding", default="cp932")
    sp.set_defaults(func=cmd_text_import_legacy)

    sp = sub.add_parser("textdata-json", help="dump textdata.bin/dat to JSON for inspection")
    sp.add_argument("textdata")
    sp.add_argument("out")
    sp.add_argument("--encoding", default="cp932")
    sp.set_defaults(func=cmd_textdata_json)

    sp = sub.add_parser("xor", help="XOR encrypt/decrypt textdata using key and +0x5C step")
    sp.add_argument("input")
    sp.add_argument("out")
    sp.add_argument("key", help="initial key, e.g. 0x12")
    sp.set_defaults(func=cmd_xor)

    sp = sub.add_parser("filename-list", help="list PJADV_FL0001 filename.dat")
    sp.add_argument("input")
    sp.add_argument("--encoding", default="cp932")
    sp.set_defaults(func=cmd_filename)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
