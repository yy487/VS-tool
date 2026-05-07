from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from script.tev2_batch_utils import detect_text_carrier, iter_files, json_output_path
from script.tev2_bttext import parse_bttext_text, write_text_doc
from script.tev2_scr import parse_scr_text, write_text_doc as write_scr_text_doc
from script.tev2_text_tables import parse_table, write_doc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Decompile TE_V2 text carrier.")
    parser.add_argument("input", type=Path, help="Input text carrier file or directory in batch mode.")
    parser.add_argument("output", type=Path, help="Output JSON file or output directory in batch mode.")
    parser.add_argument("--batch", action="store_true", help="Recursively decompile all supported text carriers under input directory. Directory input enables this automatically.")
    parser.add_argument("--single", action="store_true", help="Force single-file mode. Use only when input is a file and output is a file.")
    parser.add_argument("--skip-errors", action="store_true", help="Continue batch processing after a file fails.")
    parser.add_argument("--jobs", type=int, default=1, help="Batch worker process count. Use 0 for CPU count.")
    parser.add_argument("--with-impact", action="store_true", help="Include verbose SCR rebuild_impact diagnostics. Slower; not required for normal translation/compile.")
    parser.add_argument(
        "--text-encoding",
        default="cp932",
        help="Encoding used to decode text entries. Supports aliases: win-31j/sjis/cp932.",
    )
    return parser


def decompile_one(input_path: Path, output_path: Path, *, text_encoding: str, include_impact: bool = False) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    kind = detect_text_carrier(input_path)
    if kind == "bttext":
        doc = parse_bttext_text(input_path, text_encoding=text_encoding)
        write_text_doc(output_path, doc)
    elif kind == "scr":
        doc = parse_scr_text(input_path, text_encoding=text_encoding, include_impact=include_impact)
        write_scr_text_doc(output_path, doc)
    elif kind == "table":
        doc = parse_table(input_path, text_encoding=text_encoding)
        write_doc(output_path, doc)
    else:
        raise ValueError(f"Unsupported text carrier: {input_path}")
    return kind


def _decompile_worker(payload: tuple[str, str, str, bool]) -> dict[str, object]:
    input_s, output_s, text_encoding, include_impact = payload
    input_path = Path(input_s)
    output_path = Path(output_s)
    kind = decompile_one(input_path, output_path, text_encoding=text_encoding, include_impact=include_impact)
    return {"status": "ok", "kind": kind, "input": input_s, "output": output_s}


def main() -> int:
    args = build_parser().parse_args()
    auto_batch = args.batch or args.input.is_dir()
    if args.single:
        auto_batch = False

    if not auto_batch:
        if args.input.is_dir():
            raise SystemExit("single-file mode requires input to be a file; omit --single or add --batch for directory input")
        kind = decompile_one(args.input, args.output, text_encoding=args.text_encoding, include_impact=args.with_impact)
        print(f"[ok] {kind}: {args.input} -> {args.output}")
        return 0

    if not args.input.is_dir():
        raise SystemExit("batch decompile requires input to be a directory")
    args.output.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[str, str, str, bool]] = []
    for input_file in iter_files(args.input, suffixes={'.dat', '.scr'}):
        kind = detect_text_carrier(input_file)
        if kind is None:
            continue
        output_file = json_output_path(input_file, args.input, args.output)
        tasks.append((str(input_file), str(output_file), args.text_encoding, args.with_impact))

    summary: list[dict[str, object]] = []
    ok = 0
    failed = 0
    jobs = (os.cpu_count() or 1) if args.jobs == 0 else max(1, args.jobs)
    if jobs == 1 or len(tasks) <= 1:
        for task in tasks:
            input_file = Path(task[0])
            output_file = Path(task[1])
            try:
                item = _decompile_worker(task)
                ok += 1
                summary.append(item)
                print(f"[ok] {item['kind']}: {input_file} -> {output_file}")
            except Exception as exc:
                failed += 1
                summary.append({"status": "failed", "input": str(input_file), "output": str(output_file), "error": str(exc)})
                print(f"[failed] {input_file}: {exc}")
                if not args.skip_errors:
                    raise
    else:
        with ProcessPoolExecutor(max_workers=jobs) as executor:
            future_to_task = {executor.submit(_decompile_worker, task): task for task in tasks}
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                input_file = Path(task[0])
                output_file = Path(task[1])
                try:
                    item = future.result()
                    ok += 1
                    summary.append(item)
                    print(f"[ok] {item['kind']}: {input_file} -> {output_file}")
                except Exception as exc:
                    failed += 1
                    summary.append({"status": "failed", "input": str(input_file), "output": str(output_file), "error": str(exc)})
                    print(f"[failed] {input_file}: {exc}")
                    if not args.skip_errors:
                        raise
    manifest = {"format": "TE_V2_BATCH_DECOMPILE", "input_root": str(args.input), "output_root": str(args.output), "ok": ok, "failed": failed, "items": summary}
    (args.output / "_batch_decompile_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] decompiled={ok}, failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
