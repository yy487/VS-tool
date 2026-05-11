#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
from scb_common import TEXT_OP, END_TICK, parse_scb, iter_scb_files, dump_json

NAME_MACROS = {'#N1', '#N2'}


def split_head_before_l(s: str) -> str:
    return s.split('#L', 1)[0].strip()


def clean_name(s: str) -> str | None:
    """Return speaker name from the current TEXT header only.

    The SCB dialogue structure is command-stream based:
        00 00 <header>#L 00 FF FF 00 00 <body>#W#P 00 FF FF

    Therefore <header> itself is the speaker field.  Do not inherit names
    across choices/branches, and do not hide #N1/#N2: they are displayed
    name placeholders rather than opcodes.

      #N2#L            -> name = #N2
      栄美子#L          -> name = 栄美子
      栄美子・#N2#L     -> name = 栄美子・#N2
      ？？#L            -> name = ？？
      #L               -> no name
    """
    pre = split_head_before_l(s)
    return pre or None


def is_dialogue_head_text(s: str) -> bool:
    """Candidate dialogue header.

    Earlier versions tried to reject headers by punctuation/length, which
    incorrectly dropped valid names such as ？？.  The real guard is the
    following command stream: collect_body_cmds() must find a following body
    TEXT ending in #W#P.
    """
    return '#L' in s and '#W#P' not in s


def strip_final_wait_marker(s: str) -> str:
    if s.endswith('#W#P'):
        return s[:-4]
    return s.replace('#W#P', '')


def collect_body_cmds(cmds, start_index: int):
    """Collect one or more TEXT body commands after a header.

    Normal:     header, FFFF, body#W#P
    Special:    header, FFFF, body_part#L, FFFF, body_note#W#P
    Returns (body_cmds, last_index) or ([], start_index).
    """
    j = start_index + 1
    if j < len(cmds) and cmds[j].op == END_TICK:
        j += 1
    body = []
    while j < len(cmds):
        c = cmds[j]
        if c.op != TEXT_OP or c.text is None:
            break
        body.append(c)
        if '#W#P' in c.text:
            return body, j
        # Body continuation must be separated by END_TICK.
        if j + 1 < len(cmds) and cmds[j + 1].op == END_TICK:
            j += 2
            continue
        break
    return [], start_index


def extract_entries(path: Path, encoding: str = 'cp932', include_choice: bool = True) -> list[dict]:
    parsed = parse_scb(path, encoding)
    cmds = parsed.cmds
    entries: list[dict] = []
    eid = 0
    i = 0
    while i < len(cmds):
        cmd = cmds[i]
        if include_choice and cmd.op == 0x11 and cmd.text is not None:
            entries.append({
                'id': eid,
                'type': 'choice',
                'scr_msg': cmd.text,
                'message': cmd.text,
                '_offset': cmd.off,
            })
            eid += 1
            i += 1
            continue

        if cmd.op == TEXT_OP and cmd.text and is_dialogue_head_text(cmd.text):
            body_cmds, last_i = collect_body_cmds(cmds, i)
            if body_cmds:
                segments = [strip_final_wait_marker(c.text or '') for c in body_cmds]
                item = {
                    'id': eid,
                    'type': 'message',
                    'scr_msg': ''.join(segments),
                    'message': ''.join(segments),
                    '_offset': cmd.off,
                    '_msg_offset': body_cmds[0].off,
                    '_body_offsets': [c.off for c in body_cmds],
                }
                name = clean_name(cmd.text)
                if name:
                    item['name'] = name
                entries.append(item)
                eid += 1
                i = last_i + 1
                continue
        i += 1
    return entries


def main() -> int:
    ap = argparse.ArgumentParser(description='Extract Sweet SCB text to one JSON per SCB file.')
    ap.add_argument('input', type=Path, help='input .SCB file or directory')
    ap.add_argument('output', type=Path, help='output .json file or directory')
    ap.add_argument('--encoding', default='cp932')
    ap.add_argument('--no-choice', action='store_true', help='do not extract choice text')
    args = ap.parse_args()

    files = iter_scb_files(args.input)
    if not files:
        raise SystemExit(f'no SCB files found: {args.input}')

    single = len(files) == 1 and args.output.suffix.lower() == '.json'
    total = 0
    for src in files:
        entries = extract_entries(src, args.encoding, include_choice=not args.no_choice)
        dst = args.output if single else args.output / f'{src.stem}.json'
        dump_json(dst, entries)
        total += len(entries)
        print(f'{src.name}: {len(entries)} -> {dst}')
    print(f'done: files={len(files)}, entries={total}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
