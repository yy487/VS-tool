#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, json
from pathlib import Path
from scb_common import iter_scb_files

ADDR_SUFFIXES = ('ちゃん', 'さん', 'くん', '君', '先生')

def iter_json_files(p: Path):
    if p.is_file():
        return [p]
    return sorted(p.glob('*.json'))

def load(p: Path):
    return json.loads(p.read_text(encoding='utf-8'))

def main() -> int:
    ap = argparse.ArgumentParser(description='Lint extracted SCB JSON for suspicious speaker/message pairs.')
    ap.add_argument('json', type=Path, help='json file or directory')
    args = ap.parse_args()
    total = 0
    for jp in iter_json_files(args.json):
        try:
            data = load(jp)
        except Exception as e:
            print(f'{jp}: cannot read: {e}')
            continue
        for e in data:
            name = e.get('name')
            msg = e.get('message') or ''
            if not name or not isinstance(msg, str):
                continue
            # common symptom of wrong name carry: speaker name appears as addressee in its own line
            n = str(name).replace('#N1','').replace('#N2','').strip('・ ')
            if n and any((n+suf) in msg for suf in ADDR_SUFFIXES):
                print(f'{jp.name}: id={e.get("id")} suspicious name={name!r} message={msg!r}')
                total += 1
    print(f'done: suspicious={total}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
