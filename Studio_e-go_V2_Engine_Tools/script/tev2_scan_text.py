from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from script.tev2_batch_utils import detect_text_carrier, iter_files
from script.tev2_bttext import parse_bttext_text
from script.tev2_scr import parse_scr_text
from script.tev2_text_tables import parse_table


def _count_entries(path: Path, kind: str, text_encoding: str) -> int:
    if kind == 'bttext':
        return len(parse_bttext_text(path, text_encoding=text_encoding).entries)
    if kind == 'scr':
        return len(parse_scr_text(path, text_encoding=text_encoding, include_impact=False).entries)
    if kind == 'table':
        return len(parse_table(path, text_encoding=text_encoding).entries)
    return 0


def _scan_worker(payload: tuple[str, str, str, str]) -> dict[str, object]:
    path_s, base_s, kind, text_encoding = payload
    path = Path(path_s)
    base = Path(base_s)
    item: dict[str, object] = {
        'path': str(path.relative_to(base) if path.is_relative_to(base) else path),
        'absolute_path': str(path),
        'type': kind,
    }
    item['entry_count'] = _count_entries(path, kind, text_encoding)
    item['status'] = 'ok'
    return item


def build_text_scan(input_root: Path, *, text_encoding: str = 'cp932', skip_errors: bool = True, jobs: int = 1) -> dict[str, object]:
    files = iter_files(input_root, suffixes={'.dat', '.scr'}) if input_root.is_dir() else [input_root]
    base = input_root if input_root.is_dir() else input_root.parent
    tasks: list[tuple[str, str, str, str]] = []
    for path in files:
        kind = detect_text_carrier(path)
        if kind is None:
            continue
        tasks.append((str(path), str(base), kind, text_encoding))

    carriers: list[dict[str, object]] = []
    worker_count = (os.cpu_count() or 1) if jobs == 0 else max(1, jobs)
    if worker_count == 1 or len(tasks) <= 1:
        for task in tasks:
            try:
                carriers.append(_scan_worker(task))
            except Exception as exc:
                path = Path(task[0])
                item: dict[str, object] = {
                    'path': str(path.relative_to(base) if path.is_relative_to(base) else path),
                    'absolute_path': str(path),
                    'type': task[2],
                    'entry_count': 0,
                    'status': 'failed',
                    'error': str(exc),
                }
                carriers.append(item)
                if not skip_errors:
                    raise
    else:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            future_to_task = {executor.submit(_scan_worker, task): task for task in tasks}
            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    carriers.append(future.result())
                except Exception as exc:
                    path = Path(task[0])
                    item = {
                        'path': str(path.relative_to(base) if path.is_relative_to(base) else path),
                        'absolute_path': str(path),
                        'type': task[2],
                        'entry_count': 0,
                        'status': 'failed',
                        'error': str(exc),
                    }
                    carriers.append(item)
                    if not skip_errors:
                        raise
    carriers.sort(key=lambda item: str(item.get('path', '')))
    return {'format': 'TE_V2_TEXT_SCAN', 'input_root': str(input_root), 'text_encoding': text_encoding, 'carrier_count': len(carriers), 'carriers': carriers}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Scan TE_V2 text carriers under a file or directory.')
    parser.add_argument('input', type=Path, help='Input file or root directory')
    parser.add_argument('output', type=Path, help='Output scan JSON')
    parser.add_argument('--batch', action='store_true', help='Accepted for workflow consistency; directory input is scanned recursively either way.')
    parser.add_argument('--text-encoding', default='cp932')
    parser.add_argument('--strict', action='store_true', help='Abort on the first parse error.')
    parser.add_argument('--jobs', type=int, default=1, help='Worker process count for directory scans. Use 0 for CPU count.')
    return parser


def main() -> int:
    args = build_parser().parse_args()
    doc = build_text_scan(args.input, text_encoding=args.text_encoding, skip_errors=not args.strict, jobs=args.jobs)
    output = args.output
    if output.exists() and output.is_dir():
        output = output / 'text_scan.json'
    elif output.suffix.lower() != '.json':
        output.mkdir(parents=True, exist_ok=True)
        output = output / 'text_scan.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"[done] scanned carriers={doc['carrier_count']} -> {output}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
