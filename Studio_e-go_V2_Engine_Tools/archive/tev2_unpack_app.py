from __future__ import annotations

import argparse
import json
from pathlib import Path

from archive.tev2_archive import unpack_pak0, write_probe_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe or unpack Studio_e-go_V2 PAK0 archives.")
    parser.add_argument("input", type=Path, help="Input game directory or single .dat archive")
    parser.add_argument("output_dir", type=Path, help="Output directory")
    parser.add_argument("--batch", action="store_true", help="When input is a directory, unpack every game*.dat into separate subdirectories.")
    parser.add_argument("--probe-only", action="store_true", help="Only write archive_probe.json for a directory input.")
    parser.add_argument("--skip-errors", action="store_true", help="Continue batch unpack after a file fails.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.input.is_dir() and not args.batch:
        write_probe_manifest(args.input, args.output_dir)
        return 0
    if args.input.is_dir() and args.probe_only:
        write_probe_manifest(args.input, args.output_dir)
        return 0
    if args.input.is_file():
        manifest = unpack_pak0(args.input, args.output_dir)
        print(f"[ok] unpacked: {args.input} -> {manifest.parent}")
        return 0

    if not args.input.is_dir():
        raise SystemExit(f"Input does not exist: {args.input}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_probe_manifest(args.input, args.output_dir)
    ok = 0
    failed = 0
    items: list[dict[str, object]] = []
    for archive in sorted(args.input.glob("game*.dat")):
        out_dir = args.output_dir / archive.stem
        try:
            manifest = unpack_pak0(archive, out_dir)
            ok += 1
            items.append({"status": "ok", "archive": str(archive), "output_dir": str(out_dir), "manifest": str(manifest)})
            print(f"[ok] unpacked: {archive} -> {out_dir}")
        except Exception as exc:
            failed += 1
            items.append({"status": "failed", "archive": str(archive), "output_dir": str(out_dir), "error": str(exc)})
            print(f"[failed] {archive}: {exc}")
            if not args.skip_errors:
                raise
    batch_manifest = {"format": "TE_V2_BATCH_UNPACK", "input_root": str(args.input), "output_root": str(args.output_dir), "ok": ok, "failed": failed, "items": items}
    (args.output_dir / "_batch_unpack_manifest.json").write_text(json.dumps(batch_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] unpacked={ok}, failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
