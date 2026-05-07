from __future__ import annotations

import argparse
import json
from pathlib import Path


def patch_text_doc(doc: dict[str, object], *, entry_index: int | None = None, entry_offset: int | None = None, text: str | None = None, patch_map: dict[str, str] | None = None) -> dict[str, object]:
    entries = doc.get('entries')
    if not isinstance(entries, list):
        raise ValueError('JSON document has no entries list')
    changed = 0
    if patch_map:
        for entry in entries:
            old = str(entry.get('text', ''))
            if old in patch_map:
                entry['text'] = patch_map[old]
                changed += 1
    if text is not None:
        matched = False
        for entry in entries:
            if entry_index is not None and int(entry.get('index', -1)) != entry_index:
                continue
            if entry_offset is not None and int(entry.get('offset', entry.get('record_offset', -1))) != entry_offset:
                continue
            entry['text'] = text
            changed += 1
            matched = True
            break
        if not matched:
            raise ValueError('No matching entry found for patch target')
    doc['_patch_changed_count'] = changed
    return doc


def load_patch_map(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding='utf-8'))
    mapping: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            mapping[str(key)] = str(value)
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            src = item.get('original_text', item.get('scr_msg', item.get('source', item.get('from', item.get('text')))))
            dst = item.get('message', item.get('msg', item.get('target', item.get('to'))))
            if src is not None and dst is not None:
                mapping[str(src)] = str(dst)
    else:
        raise ValueError('Unsupported patch map format')
    return mapping


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Patch TE_V2 text JSON document(s).')
    parser.add_argument('input', type=Path, help='Input JSON file or directory in batch mode')
    parser.add_argument('output', type=Path, help='Output JSON file or directory in batch mode')
    parser.add_argument('--batch', action='store_true', help='Recursively patch all JSON files under input directory.')
    parser.add_argument('--entry-index', type=int, default=None)
    parser.add_argument('--entry-offset', type=int, default=None)
    parser.add_argument('--text', default=None)
    parser.add_argument('--patch-map', type=Path, default=None, help='JSON mapping: original text -> replacement, or list with text/msg fields.')
    parser.add_argument('--skip-errors', action='store_true')
    return parser


def patch_one(input_json: Path, output_json: Path, *, entry_index: int | None, entry_offset: int | None, text: str | None, patch_map: dict[str, str] | None) -> int:
    doc = json.loads(input_json.read_text(encoding='utf-8'))
    before = json.dumps(doc.get('entries', []), ensure_ascii=False, sort_keys=True)
    patch_text_doc(doc, entry_index=entry_index, entry_offset=entry_offset, text=text, patch_map=patch_map)
    after = json.dumps(doc.get('entries', []), ensure_ascii=False, sort_keys=True)
    changed = 1 if before != after else 0
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
    return int(doc.get('_patch_changed_count', changed))


def main() -> int:
    args = build_parser().parse_args()
    patch_map = load_patch_map(args.patch_map) if args.patch_map else None
    if args.text is None and patch_map is None:
        raise SystemExit('Need --text or --patch-map')
    if not args.batch:
        changed = patch_one(args.input, args.output, entry_index=args.entry_index, entry_offset=args.entry_offset, text=args.text, patch_map=patch_map)
        print(f'[ok] patched changed={changed}: {args.input} -> {args.output}')
        return 0
    if not args.input.is_dir():
        raise SystemExit('--batch requires input to be a directory')
    args.output.mkdir(parents=True, exist_ok=True)
    ok = failed = total_changed = 0
    items: list[dict[str, object]] = []
    for input_json in sorted(args.input.rglob('*.json')):
        if input_json.name.startswith('_batch_'):
            continue
        output_json = args.output / input_json.relative_to(args.input)
        try:
            changed = patch_one(input_json, output_json, entry_index=args.entry_index, entry_offset=args.entry_offset, text=args.text, patch_map=patch_map)
            ok += 1
            total_changed += changed
            items.append({'status': 'ok', 'input': str(input_json), 'output': str(output_json), 'changed': changed})
            print(f'[ok] patch changed={changed}: {input_json} -> {output_json}')
        except Exception as exc:
            failed += 1
            items.append({'status': 'failed', 'input': str(input_json), 'error': str(exc)})
            print(f'[failed] {input_json}: {exc}')
            if not args.skip_errors:
                raise
    (args.output / '_batch_patch_manifest.json').write_text(json.dumps({'format': 'TE_V2_BATCH_PATCH_TEXT', 'ok': ok, 'failed': failed, 'changed': total_changed, 'items': items}, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
