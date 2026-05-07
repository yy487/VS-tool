from __future__ import annotations

from pathlib import Path
from typing import Iterable

TEXT_CARRIER_SUFFIXES = {'.dat', '.scr'}


def iter_files(root: Path, *, suffixes: Iterable[str] | None = None) -> list[Path]:
    if root.is_file():
        return [root]
    suffix_set = {s.lower() for s in suffixes} if suffixes else None
    items: list[Path] = []
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        if suffix_set and path.suffix.lower() not in suffix_set:
            continue
        items.append(path)
    return sorted(items)


def detect_text_carrier(path: Path) -> str | None:
    try:
        head = path.read_bytes()[:4]
    except OSError:
        return None
    if head == b'TSCR':
        return 'bttext'
    if head == b'SCR ':
        return 'scr'
    if path.suffix.lower() == '.dat':
        return 'table'
    return None


def json_output_path(input_file: Path, input_root: Path, output_root: Path) -> Path:
    if input_root.is_file():
        return output_root
    rel = input_file.relative_to(input_root)
    return (output_root / rel).with_suffix('.json')


def output_path_from_json(json_file: Path, json_root: Path, output_root: Path, doc: dict[str, object]) -> Path:
    if json_root.is_file():
        return output_root
    rel = json_file.relative_to(json_root)
    source_suffix = Path(str(doc.get('source_path', ''))).suffix
    fmt = str(doc.get('format', ''))
    if source_suffix:
        suffix = source_suffix
    elif fmt == 'TE_V2_SCR_TEXT_CANDIDATES':
        suffix = '.scr'
    else:
        suffix = '.dat'
    return (output_root / rel).with_suffix(suffix)


def resolve_source_path(doc: dict[str, object], json_file: Path, json_root: Path, source_root: Path | None) -> None:
    if source_root is None:
        return
    if json_root.is_file():
        original_name = Path(str(doc.get('source_path', json_file.stem))).name
        doc['source_path'] = str(source_root / original_name)
        return
    rel = json_file.relative_to(json_root)
    source_suffix = Path(str(doc.get('source_path', ''))).suffix
    if not source_suffix:
        fmt = str(doc.get('format', ''))
        source_suffix = '.scr' if fmt == 'TE_V2_SCR_TEXT_CANDIDATES' else '.dat'
    doc['source_path'] = str((source_root / rel).with_suffix(source_suffix))
