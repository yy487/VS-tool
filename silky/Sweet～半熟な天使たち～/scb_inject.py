#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
from scb_common import TEXT_OP, parse_scb, rebuild_scb, iter_scb_files, load_json


def strip_final_wait_marker(s: str) -> str:
    if s.endswith('#W#P'):
        return s[:-4]
    return s.replace('#W#P', '')


def split_message_to_segments(message: str, n: int) -> tuple[list[str], list[str]]:
    """Split editable message back to original body TEXT command count.

    For multi-body messages the extracted scr_msg preserves internal #L, e.g.
      quote#Lnote
    which becomes [quote#L, note#W#P].
    """
    warnings: list[str] = []
    if n <= 1:
        return [message], warnings
    parts: list[str] = []
    rest = message
    for _ in range(n - 1):
        pos = rest.find('#L')
        if pos < 0:
            warnings.append('message has fewer #L separators than original body segments; kept text in first segment')
            parts.append(rest + '#L')
            rest = ''
        else:
            parts.append(rest[:pos + 2])
            rest = rest[pos + 2:]
    parts.append(rest)
    return parts, warnings


def make_entry_maps(entries: list[dict]) -> tuple[dict[int, dict], dict[int, dict], list[str]]:
    msg_by_first_off: dict[int, dict] = {}
    choice_by_off: dict[int, dict] = {}
    warnings: list[str] = []
    for e in entries:
        typ = e.get('type')
        if typ == 'message' or '_msg_offset' in e or '_body_offsets' in e:
            offsets = e.get('_body_offsets')
            if isinstance(offsets, list) and offsets:
                msg_by_first_off[int(offsets[0])] = e
            elif '_msg_offset' in e:
                msg_by_first_off[int(e['_msg_offset'])] = e
            else:
                warnings.append(f"message id={e.get('id')} has no _msg_offset/_body_offsets; skipped")
        elif typ == 'choice':
            if '_offset' not in e:
                warnings.append(f"choice id={e.get('id')} has no _offset; skipped")
                continue
            choice_by_off[int(e['_offset'])] = e
    return msg_by_first_off, choice_by_off, warnings


def inject_one(src: Path, json_path: Path, dst: Path, encoding: str = 'cp932', strict_scr: bool = True) -> dict:
    entries = load_json(json_path)
    if not isinstance(entries, list):
        raise ValueError(f'JSON must be a list: {json_path}')
    parsed = parse_scb(src, encoding)
    msg_by_first_off, choice_by_off, warnings = make_entry_maps(entries)
    cmd_by_off = {c.off: c for c in parsed.cmds}

    replaced_msg = 0
    replaced_choice = 0
    skipped = 0

    for e in list(msg_by_first_off.values()):
        offsets = e.get('_body_offsets')
        if not isinstance(offsets, list) or not offsets:
            offsets = [e.get('_msg_offset')]
        offsets = [int(x) for x in offsets if x is not None]
        body_cmds = [cmd_by_off.get(off) for off in offsets]
        if not body_cmds or any(c is None or c.op != TEXT_OP for c in body_cmds):
            warnings.append(f"message id={e.get('id')} body offset missing; skipped")
            skipped += 1
            continue
        body_cmds = [c for c in body_cmds if c is not None]
        old_scr = ''.join(strip_final_wait_marker(c.text or '') for c in body_cmds)
        if strict_scr and e.get('scr_msg') != old_scr:
            warnings.append(f"message id={e.get('id')} offset=0x{offsets[0]:04X} scr_msg mismatch; skipped")
            skipped += 1
            continue
        message = str(e.get('message', old_scr))
        parts, ws = split_message_to_segments(message, len(body_cmds))
        for w in ws:
            warnings.append(f"message id={e.get('id')}: {w}")
        for idx, c in enumerate(body_cmds):
            if idx == len(body_cmds) - 1:
                c.text = parts[idx] + '#W#P'
            else:
                c.text = parts[idx]
        replaced_msg += 1

    for cmd in parsed.cmds:
        if cmd.op == 0x11 and cmd.off in choice_by_off:
            e = choice_by_off[cmd.off]
            old_choice = cmd.text or ''
            if strict_scr and e.get('scr_msg') != old_choice:
                warnings.append(f"choice id={e.get('id')} offset=0x{cmd.off:04X} scr_msg mismatch; skipped")
                skipped += 1
                continue
            cmd.text = str(e.get('message', old_choice))
            replaced_choice += 1

    out = rebuild_scb(parsed, encoding)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    return {
        'file': src.name,
        'json': str(json_path),
        'out': str(dst),
        'messages': replaced_msg,
        'choices': replaced_choice,
        'skipped': skipped,
        'warnings': warnings,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description='Inject one-JSON-per-SCB translation back into Sweet SCB files with rel16 relocation.')
    ap.add_argument('input', type=Path, help='input .SCB file or directory')
    ap.add_argument('json', type=Path, help='matching .json file or directory')
    ap.add_argument('output', type=Path, help='output .SCB file or directory')
    ap.add_argument('--encoding', default='cp932')
    ap.add_argument('--no-strict-scr', action='store_true', help='do not require scr_msg to match original text')
    args = ap.parse_args()

    files = iter_scb_files(args.input)
    if not files:
        raise SystemExit(f'no SCB files found: {args.input}')
    single = len(files) == 1 and args.output.suffix.lower() == '.scb'

    reports = []
    for src in files:
        js = args.json if args.json.is_file() else args.json / f'{src.stem}.json'
        if not js.exists():
            print(f'{src.name}: missing json {js}; skipped')
            continue
        dst = args.output if single else args.output / src.name
        r = inject_one(src, js, dst, args.encoding, strict_scr=not args.no_strict_scr)
        reports.append(r)
        print(f"{src.name}: messages={r['messages']} choices={r['choices']} skipped={r['skipped']} -> {dst}")
        for w in r['warnings'][:10]:
            print(f'  warning: {w}')
        if len(r['warnings']) > 10:
            print(f"  ... {len(r['warnings']) - 10} more warnings")
    print(f'done: files={len(reports)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
