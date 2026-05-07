from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_fit_report(doc: dict[str, object], *, extra_bytes: int = 0, text_encoding: str = 'cp932') -> dict[str, object]:
    entries = doc.get('entries')
    if not isinstance(entries, list):
        raise ValueError('JSON document has no entries list')
    rows = []
    in_place_ok = 0
    overflow = 0
    expandable = 0
    for entry in entries:
        text = str(entry.get('text', ''))
        encoded_len = len(text.encode(text_encoding)) + int(extra_bytes)
        capacity = int(entry.get('capacity_bytes', entry.get('in_place_capacity_bytes', entry.get('length', 0))))
        fits = encoded_len <= capacity
        if fits:
            in_place_ok += 1
        else:
            overflow += 1
        if bool(entry.get('supports_expansion_rebuild', False)):
            expandable += 1
        rows.append({'index': entry.get('index'), 'offset': entry.get('offset'), 'capacity_bytes': capacity, 'encoded_bytes_with_extra': encoded_len, 'overflow_bytes': max(0, encoded_len - capacity), 'fits_in_place': fits, 'supports_expansion_rebuild': bool(entry.get('supports_expansion_rebuild', False)), 'text': text})
    return {'format': 'TE_V2_TEXT_FIT_REPORT', 'source_path': doc.get('source_path'), 'text_encoding': text_encoding, 'extra_bytes': extra_bytes, 'entry_count': len(rows), 'in_place_ok': in_place_ok, 'overflow': overflow, 'expandable': expandable, 'entries': rows}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Build capacity/overflow report for TE_V2 text JSON document(s).')
    parser.add_argument('input', type=Path, help='Input JSON file or directory in batch mode')
    parser.add_argument('output', type=Path, help='Output report JSON file or directory in batch mode')
    parser.add_argument('--batch', action='store_true')
    parser.add_argument('--extra-bytes', type=int, default=0)
    parser.add_argument('--text-encoding', default='cp932')
    parser.add_argument('--skip-errors', action='store_true')
    return parser


def report_one(input_json: Path, output_json: Path, *, extra_bytes: int, text_encoding: str) -> dict[str, object]:
    doc = json.loads(input_json.read_text(encoding='utf-8'))
    report = build_fit_report(doc, extra_bytes=extra_bytes, text_encoding=text_encoding)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main() -> int:
    args = build_parser().parse_args()
    if not args.batch:
        report = report_one(args.input, args.output, extra_bytes=args.extra_bytes, text_encoding=args.text_encoding)
        print(f"[done] report entries={report['entry_count']}, overflow={report['overflow']} -> {args.output}")
        return 0
    if not args.input.is_dir():
        raise SystemExit('--batch requires input to be a directory')
    args.output.mkdir(parents=True, exist_ok=True)
    ok = failed = overflow_total = 0
    items = []
    for input_json in sorted(args.input.rglob('*.json')):
        if input_json.name.startswith('_batch_'):
            continue
        out = args.output / input_json.relative_to(args.input)
        try:
            report = report_one(input_json, out, extra_bytes=args.extra_bytes, text_encoding=args.text_encoding)
            ok += 1
            overflow_total += int(report['overflow'])
            items.append({'status': 'ok', 'input': str(input_json), 'output': str(out), 'entry_count': report['entry_count'], 'overflow': report['overflow']})
            print(f"[ok] fit report: {input_json} -> {out}")
        except Exception as exc:
            failed += 1
            items.append({'status': 'failed', 'input': str(input_json), 'error': str(exc)})
            print(f"[failed] {input_json}: {exc}")
            if not args.skip_errors:
                raise
    (args.output / '_batch_fit_report_manifest.json').write_text(json.dumps({'format': 'TE_V2_BATCH_TEXT_FIT_REPORT', 'ok': ok, 'failed': failed, 'overflow_total': overflow_total, 'items': items}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'[done] fit-report ok={ok}, failed={failed}, overflow_total={overflow_total}')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
