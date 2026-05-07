from __future__ import annotations

import argparse
import json
import os
import struct
from pathlib import Path


class PAKPackager:
    def __init__(self):
        self.signature = b'PAK0'

    def build_directory_tree_sorted(self, root_dir):
        root_abs = os.path.abspath(root_dir)
        all_dirs = []
        all_files = []

        def walk(current_rel_path, current_abs_path):
            try:
                entries = sorted(os.listdir(current_abs_path))
            except OSError:
                return
            dirs = []
            files = []
            for name in entries:
                full = os.path.join(current_abs_path, name)
                if os.path.islink(full):
                    continue
                if os.path.isdir(full):
                    dirs.append(name)
                else:
                    files.append(name)
            for d in dirs:
                sub_rel = os.path.join(current_rel_path, d) if current_rel_path else d
                all_dirs.append((current_rel_path, d))
                walk(sub_rel, os.path.join(current_abs_path, d))
            for f in files:
                all_files.append((current_rel_path, f, os.path.join(current_abs_path, f)))

        walk('', root_abs)

        dir_index_map = {'': 0}
        dirs = [(0xFFFFFFFF, "")]
        for parent_rel, dir_name in all_dirs:
            parent_idx = dir_index_map[parent_rel]
            dirs.append((parent_idx, dir_name))
            cur_rel = os.path.join(parent_rel, dir_name) if parent_rel else dir_name
            dir_index_map[cur_rel] = len(dirs) - 1

        files = []
        for parent_rel, file_name, full_path in all_files:
            dir_index = dir_index_map[parent_rel]
            files.append((dir_index, file_name, full_path))

        return dirs, files

    def calculate_directory_file_ranges(self, dirs, files):
        file_counts = [0] * len(dirs)
        for dir_idx, _, _ in files:
            file_counts[dir_idx] += 1

        dir_last_index = [0] * len(dirs)
        cur_idx = 0
        for i in range(len(dirs)):
            cur_idx += file_counts[i]
            dir_last_index[i] = cur_idx
        return dir_last_index

    def pack(self, input_dir, output_file, verbose=True):
        if verbose:
            print(f"Packing directory: {input_dir} -> {output_file}")
        dirs, files = self.build_directory_tree_sorted(input_dir)
        dir_last_index = self.calculate_directory_file_ranges(dirs, files)

        if verbose:
            print(f"Found {len(dirs)} directories, {len(files)} files")
            print("\nDirectory list (index, parent, name, last_file_idx):")
            for i, (parent, name) in enumerate(dirs):
                print(f"  [{i}] parent={parent}, name='{name}', last_file_idx={dir_last_index[i]}")
            print("\nFile list (dir_index, file_name):")
            for i, (dir_idx, fname, _) in enumerate(files):
                print(f"  [{i}] dir={dir_idx}, file='{fname}'")

        name_data = bytearray()
        for parent_idx, dir_name in dirs[1:]:
            encoded = dir_name.encode('utf-8')
            if len(encoded) > 255:
                raise ValueError(f"Directory name too long: {dir_name}")
            name_data.append(len(encoded))
            name_data.extend(encoded)
        for dir_idx, file_name, _ in files:
            encoded = file_name.encode('utf-8')
            if len(encoded) > 255:
                raise ValueError(f"File name too long: {file_name}")
            name_data.append(len(encoded))
            name_data.extend(encoded)
        name_data.append(0x00)

        header_size = 0x10
        dir_section_size = len(dirs) * 8
        file_section_size = len(files) * 0x10
        name_section_size = len(name_data)
        data_offset = header_size + dir_section_size + file_section_size + name_section_size

        file_entries = []
        cur_offset = data_offset
        for dir_idx, fname, path in files:
            size = os.path.getsize(path)
            file_entries.append({'path': path, 'offset': cur_offset, 'size': size, 'dir_idx': dir_idx, 'name': fname})
            cur_offset += size

        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'wb') as f:
            f.write(self.signature)
            f.write(struct.pack('<I', data_offset))
            f.write(struct.pack('<I', len(dirs)))
            f.write(struct.pack('<I', len(files)))

            for i, (parent, _) in enumerate(dirs):
                f.write(struct.pack('<I', parent))
                f.write(struct.pack('<I', dir_last_index[i]))

            for entry in file_entries:
                f.write(struct.pack('<I', entry['offset']))
                f.write(struct.pack('<I', entry['size']))
                f.write(b'\x00' * 8)

            f.write(name_data)

            for entry in file_entries:
                with open(entry['path'], 'rb') as src:
                    f.write(src.read())
                if verbose:
                    print(f"Packed: {entry['path']} (size: {entry['size']} bytes)")

        if verbose:
            print(f"\nPacking completed! Output: {output_file}")
            print(f"Data offset: 0x{data_offset:08X}")
            print(f"Directories: {len(dirs)}, Files: {len(files)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pack Studio_e-go_V2 PAK0 archives.")
    parser.add_argument("input_directory", type=Path, help="Input files directory, or a root containing gameXX/files in batch mode.")
    parser.add_argument("output", type=Path, help="Output .dat file, or output directory in batch mode.")
    parser.add_argument("--batch", action="store_true", help="Pack every child archive directory under input_directory.")
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose file listing.")
    parser.add_argument("--skip-errors", action="store_true", help="Continue batch packing after one archive fails.")
    return parser


def _batch_inputs(root: Path) -> list[tuple[str, Path]]:
    items: list[tuple[str, Path]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        files_dir = child / 'files'
        if files_dir.is_dir():
            items.append((child.name, files_dir))
        else:
            items.append((child.name, child))
    return items


def main() -> int:
    args = build_parser().parse_args()
    if not args.input_directory.is_dir():
        raise SystemExit(f"Error: Directory '{args.input_directory}' does not exist!")
    packer = PAKPackager()
    if not args.batch:
        if args.output.suffix.lower() != '.dat':
            raise SystemExit("Error: Output file must have .dat extension!")
        packer.pack(args.input_directory, args.output, verbose=not args.quiet)
        return 0

    args.output.mkdir(parents=True, exist_ok=True)
    ok = 0
    failed = 0
    items: list[dict[str, object]] = []
    for archive_name, input_dir in _batch_inputs(args.input_directory):
        out_file = args.output / f"{archive_name}.dat"
        try:
            packer.pack(input_dir, out_file, verbose=not args.quiet)
            ok += 1
            items.append({"status": "ok", "input_dir": str(input_dir), "output": str(out_file)})
            print(f"[ok] packed: {input_dir} -> {out_file}")
        except Exception as exc:
            failed += 1
            items.append({"status": "failed", "input_dir": str(input_dir), "output": str(out_file), "error": str(exc)})
            print(f"[failed] {input_dir}: {exc}")
            if not args.skip_errors:
                raise
    (args.output / '_batch_pack_manifest.json').write_text(json.dumps({"format": "TE_V2_BATCH_PACK", "ok": ok, "failed": failed, "items": items}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"[done] packed={ok}, failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
