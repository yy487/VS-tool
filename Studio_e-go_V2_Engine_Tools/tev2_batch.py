from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable


def run_script(script: str, args: list[str]) -> int:
    return subprocess.call([PYTHON, str(ROOT / script), *args])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='TE_V2 batch workflow dispatcher.')
    sub = parser.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('unpack', help='Batch unpack game*.dat archives')
    p.add_argument('input_game_dir')
    p.add_argument('output_root')
    p.add_argument('--skip-errors', action='store_true')

    p = sub.add_parser('export-text', help='Batch export text carriers to JSON')
    p.add_argument('input_files_root')
    p.add_argument('json_root')
    p.add_argument('--text-encoding', default='cp932')
    p.add_argument('--skip-errors', action='store_true')
    p.add_argument('--jobs', default='1')
    p.add_argument('--with-impact', action='store_true')

    p = sub.add_parser('import-text', help='Batch compile JSON back to files')
    p.add_argument('json_root')
    p.add_argument('output_files_root')
    p.add_argument('--source-root', required=True)
    p.add_argument('--text-encoding', default='cp932')
    p.add_argument('--skip-errors', action='store_true')
    p.add_argument('--jobs', default='1')

    p = sub.add_parser('scan-text', help='Scan supported text carriers')
    p.add_argument('input_files_root')
    p.add_argument('output_json')
    p.add_argument('--text-encoding', default='cp932')
    p.add_argument('--jobs', default='1')

    p = sub.add_parser('fit-report', help='Batch build fit reports for JSON files')
    p.add_argument('json_root')
    p.add_argument('report_root')
    p.add_argument('--extra-bytes', default='0')
    p.add_argument('--text-encoding', default='cp932')
    p.add_argument('--skip-errors', action='store_true')

    p = sub.add_parser('pack', help='Batch pack child directories to gameXX.dat')
    p.add_argument('patched_unpacked_root')
    p.add_argument('output_game_dir')
    p.add_argument('--quiet', action='store_true')
    p.add_argument('--skip-errors', action='store_true')
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == 'unpack':
        cmd = [args.input_game_dir, args.output_root, '--batch']
        if args.skip_errors: cmd.append('--skip-errors')
        return run_script('tev2_unpack.py', cmd)
    if args.cmd == 'export-text':
        cmd = [args.input_files_root, args.json_root, '--batch', '--text-encoding', args.text_encoding, '--jobs', str(args.jobs)]
        if args.skip_errors: cmd.append('--skip-errors')
        if args.with_impact: cmd.append('--with-impact')
        return run_script('tev2_decompile.py', cmd)
    if args.cmd == 'import-text':
        cmd = [args.json_root, args.output_files_root, '--batch', '--source-root', args.source_root, '--text-encoding', args.text_encoding, '--jobs', str(args.jobs)]
        if args.skip_errors: cmd.append('--skip-errors')
        return run_script('tev2_compile.py', cmd)
    if args.cmd == 'scan-text':
        return run_script('tev2_scan_text.py', [args.input_files_root, args.output_json, '--batch', '--text-encoding', args.text_encoding, '--jobs', str(args.jobs)])
    if args.cmd == 'fit-report':
        cmd = [args.json_root, args.report_root, '--batch', '--extra-bytes', args.extra_bytes, '--text-encoding', args.text_encoding]
        if args.skip_errors: cmd.append('--skip-errors')
        return run_script('tev2_fit_report.py', cmd)
    if args.cmd == 'pack':
        cmd = [args.patched_unpacked_root, args.output_game_dir, '--batch']
        if args.quiet: cmd.append('--quiet')
        if args.skip_errors: cmd.append('--skip-errors')
        return run_script('Studio_e-go_V2_pack.py', cmd)
    raise AssertionError(args.cmd)


if __name__ == '__main__':
    raise SystemExit(main())
