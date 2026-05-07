from __future__ import annotations

import argparse
import json
from pathlib import Path

from script.tev2_bttext import write_probe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Studio_e-go_V2 BtText.dat outer-container structure.")
    parser.add_argument("input", type=Path, help="Input BtText.dat file or directory in batch mode")
    parser.add_argument("output", type=Path, help="Output JSON file or directory in batch mode")
    parser.add_argument("--batch", action="store_true", help="Recursively probe BtText.dat files.")
    parser.add_argument("--skip-errors", action="store_true", help="Continue batch processing after a file fails.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.batch:
        write_probe(args.input, args.output)
        print(f"[ok] bttext probe: {args.input} -> {args.output}")
        return 0
    if not args.input.is_dir():
        raise SystemExit("--batch requires input to be a directory")
    args.output.mkdir(parents=True, exist_ok=True)
    ok = 0
    failed = 0
    items: list[dict[str, object]] = []
    for input_file in sorted(args.input.rglob("BtText.dat")):
        output_file = (args.output / input_file.relative_to(args.input)).with_suffix(".json")
        try:
            write_probe(input_file, output_file)
            ok += 1
            items.append({"status": "ok", "input": str(input_file), "output": str(output_file)})
            print(f"[ok] bttext probe: {input_file} -> {output_file}")
        except Exception as exc:
            failed += 1
            items.append({"status": "failed", "input": str(input_file), "error": str(exc)})
            print(f"[failed] {input_file}: {exc}")
            if not args.skip_errors:
                raise
    (args.output / "_batch_bttext_probe_manifest.json").write_text(json.dumps({"format": "TE_V2_BATCH_BTTEXT_PROBE", "ok": ok, "failed": failed, "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
