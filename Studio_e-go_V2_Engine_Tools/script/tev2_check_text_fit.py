from __future__ import annotations

import argparse
import json
from pathlib import Path


def _entry_capacity(entry: dict[str, object]) -> int:
    return int(entry.get('capacity_bytes', entry.get('in_place_capacity_bytes', entry.get('length', 0))))


def check_text_fit(doc: dict[str, object], *, entry_index: int | None = None, entry_offset: int | None = None, text: str | None = None, text_encoding: str = 'cp932') -> dict[str, object]:
    entries = doc.get('entries')
    if not isinstance(entries, list):
        raise ValueError('JSON document has no entries list')
    results = []
    for entry in entries:
        if entry_index is not None and int(entry.get('index', -1)) != entry_index:
            continue
        if entry_offset is not None and int(entry.get('offset', entry.get('record_offset', -1))) != entry_offset:
            continue
        candidate_text = str(text if text is not None else entry.get('text', ''))
        encoded_len = len(candidate_text.encode(text_encoding))
        capacity = _entry_capacity(entry)
        results.append({'index': entry.get('index'), 'offset': entry.get('offset'), 'capacity_bytes': capacity, 'encoded_bytes': encoded_len, 'fits_in_place': encoded_len <= capacity, 'supports_expansion_rebuild': bool(entry.get('supports_expansion_rebuild', False))})
    if not results:
        raise ValueError('No matching entries')
    return {'format': 'TE_V2_TEXT_FIT_CHECK', 'source_path': doc.get('source_path'), 'text_encoding': text_encoding, 'results': results}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Check whether text fits JSON entry capacity.')
    parser.add_argument('input', type=Path, help='Input JSON file or directory in batch mode')
    parser.add_argument('output', nargs='?', type=Path, default=None, help='Optional output JSON file or directory in batch mode')
    parser.add_argument('--batch', action='store_true')
    parser.add_argument('--entry-index', type=int, default=None)
    parser.add_argument('--entry-offset', type=int, default=None)
    parser.add_argument('--text', default=None)
    parser.add_argument('--text-encoding', default='cp932')
    parser.add_argument('--skip-errors', action='store_true')
    return parser


def check_one(path: Path, *, entry_index: int | None, entry_offset: int | None, text: str | None, text_encoding: str) -> dict[str, object]:
    doc = json.loads(path.read_text(encoding='utf-8'))
    return check_text_fit(doc, entry_index=entry_index, entry_offset=entry_offset, text=text, text_encoding=text_encoding)


def main() -> int:
    args = build_parser().parse_args()
    if not args.batch:
        result = check_one(args.input, entry_index=args.entry_index, entry_offset=args.entry_offset, text=args.text, text_encoding=args.text_encoding)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if not args.input.is_dir():
        raise SystemExit('--batch requires input to be a directory')
    output_root = args.output or (args.input / '_fit_check')
    output_root.mkdir(parents=True, exist_ok=True)
    ok = failed = 0
    items = []
    for path in sorted(args.input.rglob('*.json')):
        if path.name.startswith('_batch_'):
            continue
        try:
            result = check_one(path, entry_index=args.entry_index, entry_offset=args.entry_offset, text=args.text, text_encoding=args.text_encoding)
            out = output_root / path.relative_to(args.input)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            ok += 1
            items.append({'status': 'ok', 'input': str(path), 'output': str(out)})
        except Exception as exc:
            failed += 1
            items.append({'status': 'failed', 'input': str(path), 'error': str(exc)})
            if not args.skip_errors:
                raise
    (output_root / '_batch_check_text_fit_manifest.json').write_text(json.dumps({'format': 'TE_V2_BATCH_TEXT_FIT_CHECK', 'ok': ok, 'failed': failed, 'items': items}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[done] fit-check ok={ok}, failed={failed}')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
