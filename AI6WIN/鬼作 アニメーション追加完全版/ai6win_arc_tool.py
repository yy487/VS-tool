#!/usr/bin/env python3
"""
AI6WIN Engine ARC Archive Tool (解包/封包)
==========================================
引擎: AI6WIN (ELF / Silky / Silky Plus)
格式: .arc 资源包
功能: unpack (解包) / pack (封包)

注意: 本工具直接操作原始数据，不做 LZSS 解压/压缩。
      解包出的文件保持 ARC 内的原始字节（压缩状态不变）。
      封包时按原样写回，保留 compressed/uncompressed size 信息。

ARC 格式:
  [4B LE]  file_count
  [file_count × 0x110]:
      [0x104B] encrypted_filename (cp932, \\0-padded)
      [4B BE]  compressed_size
      [4B BE]  uncompressed_size
      [4B BE]  data_offset
  [data...]    file data (紧跟索引表)

文件名加密: name[j] -= (name_length + 1 - j)
文件名解密: name[j] += (name_length + 1 - j)  (即本工具的 encrypt)

用法:
  python ai6win_arc_tool.py unpack  <input.arc>  [output_dir]
  python ai6win_arc_tool.py pack    <input_dir>  [output.arc]
  python ai6win_arc_tool.py list    <input.arc>
  python ai6win_arc_tool.py verify  <input.arc>
"""

import struct
import os
import sys
import json
from pathlib import Path

ENTRY_NAME_SIZE = 0x104       # 文件名字段固定长度
ENTRY_META_SIZE = 12          # 3 × uint32 BE
ENTRY_TOTAL_SIZE = 0x110      # 0x104 + 12
INDEX_JSON = "__arc_index.json"  # 元数据文件(解包时生成,封包时读取)


# ============================================================
# 文件名加解密
# ============================================================

def decrypt_filename(raw: bytes) -> str:
    """解密ARC索引中的文件名。
    算法: decrypted[j] = (encrypted[j] - key) & 0xFF, key从(len+1)递减。
    """
    nul = raw.find(b'\x00')
    name_len = nul if nul != -1 else len(raw)
    if name_len == 0:
        raise ValueError("空文件名")

    dec = bytearray(raw[:name_len])
    key = (name_len + 1) & 0xFF
    for j in range(name_len):
        dec[j] = (dec[j] - key) & 0xFF
        key = (key - 1) & 0xFF

    return dec.decode('cp932')


def encrypt_filename(name: str) -> bytes:
    """加密文件名, 返回 0x104 字节(含\\0填充)。
    算法: encrypted[j] = (plain[j] + key) & 0xFF, key从(len+1)递减。
    """
    plain = name.encode('cp932')
    name_len = len(plain)
    if name_len > ENTRY_NAME_SIZE - 1:
        raise ValueError(f"文件名过长: {name_len} > {ENTRY_NAME_SIZE - 1}")

    enc = bytearray(plain)
    key = (name_len + 1) & 0xFF
    for j in range(name_len):
        enc[j] = (enc[j] + key) & 0xFF
        key = (key - 1) & 0xFF

    return bytes(enc).ljust(ENTRY_NAME_SIZE, b'\x00')


# ============================================================
# 解包
# ============================================================

def read_index(f):
    """读取ARC索引表, 返回条目列表。"""
    file_count = struct.unpack('<I', f.read(4))[0]
    if file_count == 0 or file_count > 200000:
        raise ValueError(f"文件数量异常: {file_count}")

    entries = []
    for i in range(file_count):
        raw_name = f.read(ENTRY_NAME_SIZE)
        meta = f.read(ENTRY_META_SIZE)
        if len(meta) < ENTRY_META_SIZE:
            raise ValueError(f"条目 {i}: 索引表截断")

        comp_size, uncomp_size, offset = struct.unpack('>III', meta)

        try:
            filename = decrypt_filename(raw_name)
        except Exception as e:
            filename = f"__unknown_{i:04d}.bin"
            print(f"  [WARN] 条目 {i}: 文件名解密失败 ({e})")

        entries.append({
            'filename': filename,
            'compressed_size': comp_size,
            'uncompressed_size': uncomp_size,
            'offset': offset,
        })

    return entries


def unpack_arc(arc_path: str, output_dir: str):
    """解包ARC, 保存原始数据(不做LZSS解压)。"""
    arc_path = Path(arc_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(arc_path, 'rb') as f:
        entries = read_index(f)
        file_count = len(entries)
        index_end = 4 + file_count * ENTRY_TOTAL_SIZE

        print(f"文件数量: {file_count}")
        print(f"索引表结束: 0x{index_end:X}")

        # 生成元数据JSON(封包时需要)
        meta_list = []
        success = 0

        for i, entry in enumerate(entries):
            fname = entry['filename']
            # 用/分隔的路径 → 创建子目录
            safe_name = fname.replace('\\', '/')
            out_path = output_dir / safe_name
            out_path.parent.mkdir(parents=True, exist_ok=True)

            f.seek(entry['offset'])
            data = f.read(entry['compressed_size'])

            if len(data) != entry['compressed_size']:
                print(f"  [ERROR] {fname}: 读取不足 "
                      f"({len(data)}/{entry['compressed_size']})")
                continue

            with open(out_path, 'wb') as out_f:
                out_f.write(data)

            meta_list.append({
                'filename': fname,
                'compressed_size': entry['compressed_size'],
                'uncompressed_size': entry['uncompressed_size'],
            })
            success += 1

        # 写元数据
        json_path = output_dir / INDEX_JSON
        with open(json_path, 'w', encoding='utf-8') as jf:
            json.dump(meta_list, jf, ensure_ascii=False, indent=2)

        print(f"\n解包完成: {success}/{file_count}")
        print(f"输出目录: {output_dir}")
        print(f"元数据: {json_path}")


# ============================================================
# 封包
# ============================================================

def pack_arc(input_dir: str, arc_path: str):
    """封包目录为ARC, 使用 __arc_index.json 还原元数据。"""
    input_dir = Path(input_dir)
    arc_path = Path(arc_path)
    json_path = input_dir / INDEX_JSON

    if not json_path.exists():
        print(f"[ERROR] 找不到 {json_path}")
        print("  封包需要解包时生成的元数据文件。")
        print("  如果要从零创建, 请使用 pack_raw 模式。")
        return False

    with open(json_path, 'r', encoding='utf-8') as jf:
        meta_list = json.load(jf)

    file_count = len(meta_list)
    print(f"待封包文件: {file_count}")

    # 计算索引表大小
    index_size = 4 + file_count * ENTRY_TOTAL_SIZE

    # 第一遍: 收集所有文件数据, 计算偏移
    blobs = []
    entries = []
    current_offset = index_size

    for i, meta in enumerate(meta_list):
        fname = meta['filename']
        file_path = input_dir / fname.replace('\\', '/')

        if not file_path.exists():
            print(f"  [ERROR] 缺失文件: {fname}")
            return False

        with open(file_path, 'rb') as f:
            data = f.read()

        comp_size = len(data)
        uncomp_size = meta['uncompressed_size']

        # 如果文件被修改过(大小变了), 更新 compressed_size
        # 但 uncompressed_size 保持JSON中的值(因为我们不做解压)
        if comp_size != meta['compressed_size']:
            print(f"  [WARN] {fname}: 大小变化 {meta['compressed_size']} -> {comp_size}")

        entries.append({
            'filename': fname,
            'compressed_size': comp_size,
            'uncompressed_size': uncomp_size,
            'offset': current_offset,
        })
        blobs.append(data)
        current_offset += comp_size

    # 第二遍: 写ARC文件
    with open(arc_path, 'wb') as f:
        # 文件数量 (LE)
        f.write(struct.pack('<I', file_count))

        # 索引表
        for entry in entries:
            enc_name = encrypt_filename(entry['filename'])
            f.write(enc_name)
            f.write(struct.pack('>III',
                entry['compressed_size'],
                entry['uncompressed_size'],
                entry['offset'],
            ))

        # 数据区
        for blob in blobs:
            f.write(blob)

    total_size = current_offset
    print(f"\n封包完成: {arc_path}")
    print(f"文件数: {file_count}, 总大小: {total_size:,} bytes")
    return True


# ============================================================
# 从零封包 (无JSON, 全部视为未压缩)
# ============================================================

def pack_raw(input_dir: str, arc_path: str):
    """从零封包目录, 所有文件视为未压缩(comp_size == uncomp_size)。"""
    input_dir = Path(input_dir)
    arc_path = Path(arc_path)

    # 收集文件
    files = []
    for path in sorted(input_dir.rglob('*')):
        if path.is_file() and path.name != INDEX_JSON:
            rel = str(path.relative_to(input_dir)).replace(os.sep, '/')
            files.append((rel, path))

    file_count = len(files)
    if file_count == 0:
        print("[ERROR] 目录为空")
        return False

    print(f"待封包文件: {file_count}")

    index_size = 4 + file_count * ENTRY_TOTAL_SIZE
    blobs = []
    entries = []
    current_offset = index_size

    for name, path in files:
        with open(path, 'rb') as f:
            data = f.read()
        size = len(data)
        entries.append({
            'filename': name,
            'compressed_size': size,
            'uncompressed_size': size,
            'offset': current_offset,
        })
        blobs.append(data)
        current_offset += size

    with open(arc_path, 'wb') as f:
        f.write(struct.pack('<I', file_count))
        for entry in entries:
            f.write(encrypt_filename(entry['filename']))
            f.write(struct.pack('>III',
                entry['compressed_size'],
                entry['uncompressed_size'],
                entry['offset'],
            ))
        for blob in blobs:
            f.write(blob)

    print(f"\n封包完成: {arc_path}")
    print(f"文件数: {file_count}, 总大小: {current_offset:,} bytes")
    return True


# ============================================================
# 列表 / 验证
# ============================================================

def list_arc(arc_path: str):
    """列出ARC中所有文件。"""
    with open(arc_path, 'rb') as f:
        entries = read_index(f)

    print(f"{'#':>4}  {'文件名':<35s}  {'压缩':>10s}  {'原始':>10s}  {'偏移':>10s}  {'LZSS':>4s}")
    print("-" * 85)
    total_comp = 0
    total_uncomp = 0
    for i, e in enumerate(entries):
        is_lzss = "是" if e['compressed_size'] != e['uncompressed_size'] else "否"
        print(f"{i:4d}  {e['filename']:<35s}  {e['compressed_size']:>10,d}  "
              f"{e['uncompressed_size']:>10,d}  0x{e['offset']:08X}  {is_lzss:>4s}")
        total_comp += e['compressed_size']
        total_uncomp += e['uncompressed_size']

    print("-" * 85)
    print(f"总计: {len(entries)} 文件, "
          f"压缩: {total_comp:,} bytes, 原始: {total_uncomp:,} bytes, "
          f"压缩率: {total_comp/total_uncomp*100:.1f}%")


def verify_arc(arc_path: str):
    """验证: 解包→封包→逐字节比对。"""
    import tempfile

    arc_path = Path(arc_path)
    original = arc_path.read_bytes()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        unpack_dir = tmpdir / "unpacked"
        repack_path = tmpdir / "repacked.arc"

        print("=" * 60)
        print("[1/3] 解包...")
        unpack_arc(str(arc_path), str(unpack_dir))

        print("\n" + "=" * 60)
        print("[2/3] 封包...")
        pack_arc(str(unpack_dir), str(repack_path))

        print("\n" + "=" * 60)
        print("[3/3] 逐字节比对...")
        repacked = repack_path.read_bytes()

        if original == repacked:
            print(f"\n✅ PASS — round-trip 完全一致 ({len(original):,} bytes)")
            return True
        else:
            # 找到第一个差异位置
            min_len = min(len(original), len(repacked))
            diff_pos = -1
            for i in range(min_len):
                if original[i] != repacked[i]:
                    diff_pos = i
                    break
            if diff_pos == -1 and len(original) != len(repacked):
                diff_pos = min_len

            print(f"\n❌ FAIL — 文件不一致!")
            print(f"  原始大小: {len(original):,}")
            print(f"  封包大小: {len(repacked):,}")
            if diff_pos >= 0:
                print(f"  首个差异: 偏移 0x{diff_pos:X}")
                ctx = 16
                s = max(0, diff_pos - ctx)
                print(f"  原始: {original[s:s+ctx*2].hex(' ')}")
                print(f"  封包: {repacked[s:s+ctx*2].hex(' ')}")
            return False


# ============================================================
# CLI
# ============================================================

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == 'unpack':
        if len(sys.argv) < 3:
            print("用法: ai6win_arc_tool.py unpack <input.arc> [output_dir]")
            sys.exit(1)
        arc = sys.argv[2]
        out = sys.argv[3] if len(sys.argv) > 3 else Path(arc).stem + "_unpacked"
        unpack_arc(arc, out)

    elif cmd == 'pack':
        if len(sys.argv) < 3:
            print("用法: ai6win_arc_tool.py pack <input_dir> [output.arc]")
            sys.exit(1)
        d = sys.argv[2]
        arc = sys.argv[3] if len(sys.argv) > 3 else Path(d).stem + ".arc"
        pack_arc(d, arc)

    elif cmd == 'pack_raw':
        if len(sys.argv) < 3:
            print("用法: ai6win_arc_tool.py pack_raw <input_dir> [output.arc]")
            sys.exit(1)
        d = sys.argv[2]
        arc = sys.argv[3] if len(sys.argv) > 3 else Path(d).stem + ".arc"
        pack_raw(d, arc)

    elif cmd == 'list':
        if len(sys.argv) < 3:
            print("用法: ai6win_arc_tool.py list <input.arc>")
            sys.exit(1)
        list_arc(sys.argv[2])

    elif cmd == 'verify':
        if len(sys.argv) < 3:
            print("用法: ai6win_arc_tool.py verify <input.arc>")
            sys.exit(1)
        verify_arc(sys.argv[2])

    else:
        print(f"未知命令: {cmd}")
        print("可用命令: unpack, pack, pack_raw, list, verify")
        sys.exit(1)


if __name__ == '__main__':
    main()
