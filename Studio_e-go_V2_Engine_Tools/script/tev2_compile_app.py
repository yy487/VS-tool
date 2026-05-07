from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from script.tev2_batch_utils import output_path_from_json, resolve_source_path
from script.tev2_bttext import compile_bttext
from script.tev2_scr import compile_scr_text
from script.tev2_text_tables import compile_table


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile TE_V2 text carrier JSON.")
    parser.add_argument("input", type=Path, help="Input JSON file or JSON directory in batch mode.")
    parser.add_argument("output", type=Path, help="Output carrier file or output directory in batch mode.")
    parser.add_argument("--batch", action="store_true", help="Recursively compile all JSON files under input directory. Directory input enables this automatically.")
    parser.add_argument("--single", action="store_true", help="Force single-file mode. Use only when input is a JSON file and output is a file.")
    parser.add_argument("--jobs", type=int, default=1, help="Batch worker process count. Use 0 for CPU count.")
    parser.add_argument("--source-root", type=Path, default=None, help="Original unpacked files root used to rewrite source_path during batch compile.")
    parser.add_argument("--skip-errors", action="store_true", help="Continue batch processing after a file fails.")
    parser.add_argument(
        "--text-encoding",
        default="cp932",
        help="Encoding used for write-back. Supports aliases: win-31j/sjis/cp932.",
    )
    return parser


def compile_one(input_json: Path, output_path: Path, *, text_encoding: str, source_root: Path | None = None, json_root: Path | None = None) -> str:
    doc = json.loads(input_json.read_text(encoding="utf-8"))
    resolve_source_path(doc, input_json, json_root or input_json, source_root)
    fmt = str(doc.get("format"))
    if fmt == "TE_V2_BTTEXT_TEXT":
        data = compile_bttext(doc, text_encoding=text_encoding)
        kind = "bttext"
    elif fmt == "TE_V2_SCR_TEXT_CANDIDATES":
        data = compile_scr_text(doc, text_encoding=text_encoding)
        kind = "scr"
    elif fmt == "TE_V2_TEXT_TABLE":
        data = compile_table(doc, text_encoding=text_encoding)
        kind = "table"
    else:
        raise ValueError(f"Unsupported JSON format: {fmt}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)
    return kind


def _is_text_doc(path: Path) -> bool:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return str(doc.get("format")) in {"TE_V2_BTTEXT_TEXT", "TE_V2_SCR_TEXT_CANDIDATES", "TE_V2_TEXT_TABLE"}


def _compile_worker(payload: tuple[str, str, str, str | None, str]) -> dict[str, object]:
    input_s, output_s, text_encoding, source_root_s, json_root_s = payload
    input_json = Path(input_s)
    output_file = Path(output_s)
    source_root = Path(source_root_s) if source_root_s else None
    json_root = Path(json_root_s)
    kind = compile_one(input_json, output_file, text_encoding=text_encoding, source_root=source_root, json_root=json_root)
    return {"status": "ok", "kind": kind, "input": input_s, "output": output_s}


def main() -> int:
    args = build_parser().parse_args()
    auto_batch = args.batch or args.input.is_dir()
    if args.single:
        auto_batch = False

    if not auto_batch:
        if args.input.is_dir():
            raise SystemExit("single-file mode requires input to be a JSON file; omit --single or add --batch for directory input")
        kind = compile_one(args.input, args.output, text_encoding=args.text_encoding, source_root=args.source_root)
        print(f"[ok] {kind}: {args.input} -> {args.output}")
        return 0

    if not args.input.is_dir():
        raise SystemExit("batch compile requires input to be a directory")
    args.output.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[str, str, str, str | None, str]] = []
    for input_json in sorted(args.input.rglob("*.json")):
        if input_json.name.startswith("_batch_") or input_json.name.startswith("_text_"):
            continue
        try:
            doc = json.loads(input_json.read_text(encoding="utf-8"))
        except Exception as exc:
            if args.skip_errors:
                print(f"[skip] {input_json}: not valid json: {exc}")
                continue
            raise
        fmt = str(doc.get("format"))
        if fmt not in {"TE_V2_BTTEXT_TEXT", "TE_V2_SCR_TEXT_CANDIDATES", "TE_V2_TEXT_TABLE"}:
            continue
        output_file = output_path_from_json(input_json, args.input, args.output, doc)
        tasks.append((str(input_json), str(output_file), args.text_encoding, str(args.source_root) if args.source_root else None, str(args.input)))

    ok = 0
    failed = 0
    summary: list[dict[str, object]] = []
    jobs = (os.cpu_count() or 1) if args.jobs == 0 else max(1, args.jobs)
    if jobs == 1 or len(tasks) <= 1:
        for task in tasks:
            input_json = Path(task[0])
            try:
                item = _compile_worker(task)
                ok += 1
                summary.append(item)
                print(f"[ok] {item['kind']}: {input_json} -> {item['output']}")
            except Exception as exc:
                failed += 1
                summary.append({"status": "failed", "input": str(input_json), "error": str(exc)})
                print(f"[failed] {input_json}: {exc}")
                if not args.skip_errors:
                    raise
    else:
        with ProcessPoolExecutor(max_workers=jobs) as executor:
            future_to_task = {executor.submit(_compile_worker, task): task for task in tasks}
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                input_json = Path(task[0])
                try:
                    item = future.result()
                    ok += 1
                    summary.append(item)
                    print(f"[ok] {item['kind']}: {input_json} -> {item['output']}")
                except Exception as exc:
                    failed += 1
                    summary.append({"status": "failed", "input": str(input_json), "error": str(exc)})
                    print(f"[failed] {input_json}: {exc}")
                    if not args.skip_errors:
                        raise
    manifest = {"format": "TE_V2_BATCH_COMPILE", "input_root": str(args.input), "output_root": str(args.output), "source_root": str(args.source_root) if args.source_root else None, "ok": ok, "failed": failed, "items": summary}
    (args.output / "_batch_compile_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] compiled={ok}, failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
