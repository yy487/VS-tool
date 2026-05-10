#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI5WIN 图片 MSK 工具（正式版）

已知支持：
1. 普通 MSK
   - Type A：解压后带 4 字节 <HH> 宽高头，body 值域通常为 0x00-0x10。
   - Type B：普通 8bit alpha mask，通常与对应 G24/PNG 尺寸一致，无头。
2. 特殊 MSK：TITLE_PT_M.MSK
   - 解压后整体尺寸为 624x580。
   - 可再纵向切成 3 个 208x580 区域：p0 / p1 / p2。
   - 这不是普通的“与 G24 同尺寸的一张 alpha”。

命令：
  查看信息：
    python img_msk_tool_formal.py info <input.MSK> [--ref ref.G24/ref.png]

  解码：
    python img_msk_tool_formal.py decode <input.MSK> [ref.G24/ref.png] [output.png] [--size WxH] [--split-special]
    python img_msk_tool_formal.py decode <msk_dir> [ref_dir] [png_dir] [--split-special]

  合成（仅普通单张 MSK）：
    python img_msk_tool_formal.py merge <input.G24> <input.MSK> [output.png]

  编码灰度 PNG -> MSK：
    python img_msk_tool_formal.py encode <input.png> [output.MSK]
    python img_msk_tool_formal.py encode <png_dir> [msk_dir]

  从 RGBA PNG 提取 alpha -> MSK：
    python img_msk_tool_formal.py extract_alpha <input_rgba.png> [output.MSK]
    python img_msk_tool_formal.py extract_alpha <png_dir> [msk_dir]

  TITLE_PT_M 特殊辅助：
    python img_msk_tool_formal.py split_special <TITLE_PT_M.MSK> [out_dir]
    python img_msk_tool_formal.py join_special <p0.png> <p1.png> <p2.png> <output.MSK>
"""

import argparse
import os
import struct
import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("需要 Pillow: pip install pillow")
    raise

# ── LZSS ──────────────────────────────────────────────────────────────────────
def lzss_decompress(src: bytes) -> bytes:
    out = bytearray()
    window = bytearray(b'\x00' * 4096)
    wp = 0xFEE
    sp = 0
    while sp < len(src):
        flags = src[sp]
        sp += 1
        for bit in range(8):
            if sp >= len(src):
                break
            if flags & (1 << bit):
                b = src[sp]
                sp += 1
                out.append(b)
                window[wp] = b
                wp = (wp + 1) & 0xFFF
            else:
                if sp + 1 >= len(src):
                    break
                lo = src[sp]
                hi = src[sp + 1]
                sp += 2
                off = lo | ((hi & 0xF0) << 4)
                ml = (hi & 0x0F) + 3
                for k in range(ml):
                    b = window[(off + k) & 0xFFF]
                    out.append(b)
                    window[wp] = b
                    wp = (wp + 1) & 0xFFF
    return bytes(out)


def lzss_compress(data: bytes) -> bytes:
    """Python LZSS 压缩（无 overlap 版本，兼容优先）"""
    WINDOW = 4096
    MASK = 0xFFF
    MAX_M = 18
    MIN_M = 3
    INIT = 0xFEE
    window = bytearray(b'\x00' * WINDOW)
    wp = INIT
    sp = 0
    n = len(data)
    out = bytearray()
    while sp < n:
        fp = len(out)
        out.append(0)
        flags = 0
        for bit in range(8):
            if sp >= n:
                break
            best_len = 0
            best_off = 0
            for back in range(1, WINDOW):
                off = (wp - back) & MASK
                ml = min(MAX_M, back)  # no overlap
                k = 0
                while k < ml and sp + k < n and window[(off + k) & MASK] == data[sp + k]:
                    k += 1
                if k > best_len:
                    best_len = k
                    best_off = off
                    if k == MAX_M:
                        break
            if best_len >= MIN_M:
                out.append(best_off & 0xFF)
                out.append(((best_off >> 4) & 0xF0) | ((best_len - MIN_M) & 0x0F))
                for _ in range(best_len):
                    window[wp] = data[sp]
                    wp = (wp + 1) & MASK
                    sp += 1
            else:
                flags |= (1 << bit)
                out.append(data[sp])
                window[wp] = data[sp]
                wp = (wp + 1) & MASK
                sp += 1
        out[fp] = flags
    return bytes(out)


def _try_c_compress(data: bytes):
    c_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lzss_comp')
    if not os.path.exists(c_path):
        return None
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix='.raw') as f:
        f.write(data)
        tmp_in = f.name
    tmp_out = tmp_in + '.lzss'
    try:
        r = subprocess.run([c_path, tmp_in, tmp_out], capture_output=True, timeout=120)
        if r.returncode == 0 and os.path.exists(tmp_out):
            return open(tmp_out, 'rb').read()
    except Exception:
        pass
    finally:
        for p in [tmp_in, tmp_out]:
            if os.path.exists(p):
                os.remove(p)
    return None


def compress(data: bytes) -> bytes:
    result = _try_c_compress(data)
    if result is not None:
        return result
    return lzss_compress(data)

# ── G24 helpers ───────────────────────────────────────────────────────────────
def g24_get_size(path):
    with open(path, 'rb') as f:
        header = f.read(8)
    _x, _y, w, h = struct.unpack_from('<hhhh', header, 0)
    return w, h


def g24_to_rgb(path):
    data = Path(path).read_bytes()
    _x, _y, w, h = struct.unpack_from('<hhhh', data, 0)
    stride = (w * 3 + 3) & ~3
    raw = lzss_decompress(data[8:])
    out = bytearray(w * h * 3)
    for row in range(h):
        sr = (h - 1 - row) * stride
        dr = row * w * 3
        for col in range(w):
            out[dr + col * 3 + 0] = raw[sr + col * 3 + 2]
            out[dr + col * 3 + 1] = raw[sr + col * 3 + 1]
            out[dr + col * 3 + 2] = raw[sr + col * 3 + 0]
    return Image.frombytes('RGB', (w, h), bytes(out))

# ── MSK detection / decode ────────────────────────────────────────────────────
def parse_size(size_text):
    w, h = size_text.lower().split('x', 1)
    return int(w), int(h)


def get_ref_size(ref_path):
    if not ref_path or not os.path.exists(ref_path):
        return 0, 0
    if ref_path.upper().endswith('.G24'):
        return g24_get_size(ref_path)
    img = Image.open(ref_path)
    return img.size


def is_type_a(raw: bytes):
    if len(raw) < 5:
        return False, 0, 0, b''
    w, h = struct.unpack_from('<HH', raw, 0)
    body = raw[4:]
    if w > 0 and h > 0 and w <= 4096 and h <= 4096 and w * h == len(body) and body and max(body) <= 0x10:
        return True, w, h, body
    return False, 0, 0, b''


def is_title_pt_m(msk_path: str, raw: bytes, force_special=False):
    name = os.path.basename(msk_path).upper()
    if force_special:
        return len(raw) == 624 * 580
    if name == 'TITLE_PT_M.MSK' and len(raw) == 624 * 580:
        return True
    return False


def guess_dimensions(n: int):
    candidates = []
    for cw in [624, 640, 480, 392, 328, 320, 312, 304, 256, 240, 208, 200, 192, 160]:
        if n % cw == 0:
            ch = n // cw
            if 10 < ch < 4096:
                candidates.append((cw, ch))
    # 去重保持顺序
    res = []
    seen = set()
    for wh in candidates:
        if wh not in seen:
            seen.add(wh)
            res.append(wh)
    return res


def decode_msk_to_image(msk_path, ref_path=None, explicit_size=None, force_special=False):
    raw = lzss_decompress(Path(msk_path).read_bytes())

    ok, tw, th, body = is_type_a(raw)
    if ok:
        expanded = bytes(min(255, b * 16) for b in body)
        return {
            'kind': 'type_a',
            'image': Image.frombytes('L', (tw, th), expanded),
            'size': (tw, th),
            'raw': raw,
        }

    if explicit_size:
        w, h = parse_size(explicit_size)
        if len(raw) != w * h:
            raise ValueError(f'指定尺寸 {w}x{h} 与解压长度不匹配: {len(raw)} != {w*h}')
        return {
            'kind': 'explicit',
            'image': Image.frombytes('L', (w, h), raw),
            'size': (w, h),
            'raw': raw,
        }

    if is_title_pt_m(msk_path, raw, force_special=force_special):
        return {
            'kind': 'title_pt_m',
            'image': Image.frombytes('L', (624, 580), raw),
            'size': (624, 580),
            'raw': raw,
        }

    rw, rh = get_ref_size(ref_path)
    if rw > 0 and rh > 0 and rw * rh == len(raw):
        return {
            'kind': 'type_b_ref',
            'image': Image.frombytes('L', (rw, rh), raw),
            'size': (rw, rh),
            'raw': raw,
        }

    cand = guess_dimensions(len(raw))
    if cand:
        w, h = cand[0]
        return {
            'kind': 'guessed',
            'image': Image.frombytes('L', (w, h), raw[:w * h]),
            'size': (w, h),
            'raw': raw,
            'candidates': cand,
            'ref_size': (rw, rh),
        }

    raise ValueError(f'无法确定尺寸: {msk_path} ({len(raw)} bytes)')


def save_special_slices(img: Image.Image, out_base: str):
    base_path = Path(out_base)
    parent = base_path.parent
    stem = base_path.stem
    suffix = base_path.suffix or '.png'
    for i in range(3):
        part = img.crop((i * 208, 0, (i + 1) * 208, 580))
        part.save(parent / f'{stem}_p{i}{suffix}')

# ── command implementations ───────────────────────────────────────────────────
def cmd_info(args):
    raw = lzss_decompress(Path(args.input).read_bytes())
    print(f'file={args.input}')
    print(f'compressed_size={Path(args.input).stat().st_size}')
    print(f'decompressed_size={len(raw)}')
    print(f'min={min(raw) if raw else 0} max={max(raw) if raw else 0} unique={len(set(raw)) if raw else 0}')

    ok, w, h, _body = is_type_a(raw)
    if ok:
        print(f'class=Type A ({w}x{h}, 4-byte header, body<=0x10)')
        return

    if is_title_pt_m(args.input, raw, force_special=args.force_special):
        print('class=TITLE_PT_M special')
        print('size=624x580')
        print('slices=3 x (208x580), vertical slices by x-range')
        return

    rw, rh = get_ref_size(args.ref)
    if rw and rh:
        print(f'ref_size={rw}x{rh}')
        print(f'ref_match={"yes" if rw * rh == len(raw) else "no"}')
    cand = guess_dimensions(len(raw))
    print('candidates=' + ', '.join(f'{w}x{h}' for w, h in cand))


def decode_one(msk_path, ref_path, out_path, explicit_size=None, split_special=False, force_special=False):
    result = decode_msk_to_image(msk_path, ref_path=ref_path, explicit_size=explicit_size, force_special=force_special)
    img = result['image']
    img.save(out_path)
    kind = result['kind']
    w, h = result['size']
    print(f'  {os.path.basename(msk_path)} -> {os.path.basename(out_path)} ({w}x{h}) [{kind}]')
    if kind == 'title_pt_m' and split_special:
        save_special_slices(img, out_path)
        print(f'    额外输出 3 个切片: {Path(out_path).stem}_p0/p1/p2{Path(out_path).suffix or ".png"}')


def cmd_decode(args):
    src = args.input
    ref = args.ref
    out = args.output

    def _looks_like_ref_file(path):
        ext = os.path.splitext(path)[1].lower()
        return ext in ('.g24', '.png') and os.path.exists(path)

    if os.path.isdir(src):
        # 兼容旧用法：decode <msk_dir> [ref_dir] [png_dir]
        ref_dir = None
        out_dir = None
        if ref:
            if os.path.isdir(ref):
                ref_dir = ref
                out_dir = out or (src + '_png')
            else:
                out_dir = ref
        else:
            out_dir = src + '_png'
        os.makedirs(out_dir, exist_ok=True)
        for fn in sorted(os.listdir(src)):
            if not fn.upper().endswith('.MSK'):
                continue
            msk_path = os.path.join(src, fn)
            base = fn.rsplit('.', 1)[0]
            # 保持旧逻辑：通常 _M 去匹配 G24 基名
            base_ref = fn.rsplit('_', 1)[0] if '_M.' in fn.upper() or '_m.' in fn else base
            rp = None
            if ref_dir:
                for ext in ['.G24', '.g24', '.png', '.PNG']:
                    cand = os.path.join(ref_dir, base_ref + ext)
                    if os.path.exists(cand):
                        rp = cand
                        break
            out_path = os.path.join(out_dir, base + '.png')
            decode_one(msk_path, rp, out_path, explicit_size=args.size, split_special=args.split_special,
                       force_special=args.force_special)
    else:
        # 兼容旧用法：decode <input.MSK> [ref.G24/ref.png] [output.png]
        if ref and out is None and not _looks_like_ref_file(ref):
            out_path = ref
            ref_path = None
        else:
            ref_path = ref
            out_path = out or (os.path.splitext(src)[0] + '.png')
        decode_one(src, ref_path, out_path, explicit_size=args.size, split_special=args.split_special,
                   force_special=args.force_special)


def cmd_merge(args):
    g24_path = args.g24
    msk_path = args.msk
    out_path = args.output or (os.path.splitext(g24_path)[0] + '_rgba.png')

    raw = lzss_decompress(Path(msk_path).read_bytes())
    if is_title_pt_m(msk_path, raw, force_special=args.force_special):
        raise ValueError('TITLE_PT_M 不是普通单张 alpha，不能直接用 merge 与 G24 一对一合成。请先 decode/split_special 分析。')

    rgb = g24_to_rgb(g24_path)
    w, h = rgb.size
    result = decode_msk_to_image(msk_path, ref_path=g24_path, explicit_size=args.size, force_special=args.force_special)
    alpha = result['image']
    if alpha.size != (w, h):
        raise ValueError(f'MSK 尺寸 {alpha.size} 与 G24 尺寸 {(w, h)} 不一致，无法直接 merge')
    rgba = rgb.convert('RGBA')
    rgba.putalpha(alpha)
    rgba.save(out_path)
    print(f'  {os.path.basename(g24_path)} + {os.path.basename(msk_path)} -> {os.path.basename(out_path)}')


def cmd_encode(args):
    src = args.input
    out = args.output
    if os.path.isdir(src):
        out_dir = out or (src + '_msk')
        os.makedirs(out_dir, exist_ok=True)
        for fn in sorted(os.listdir(src)):
            if not fn.lower().endswith('.png'):
                continue
            png_path = os.path.join(src, fn)
            out_path = os.path.join(out_dir, fn.rsplit('.', 1)[0] + '.MSK')
            _encode_gray_png(png_path, out_path)
    else:
        out_path = out or (os.path.splitext(src)[0] + '.MSK')
        _encode_gray_png(src, out_path)


def _encode_gray_png(png_path, out_path):
    img = Image.open(png_path).convert('L')
    raw = img.tobytes()
    comp = compress(raw)
    Path(out_path).write_bytes(comp)
    ratio = len(comp) * 100 // len(raw) if raw else 0
    print(f'  {os.path.basename(png_path)} -> {os.path.basename(out_path)} ({len(raw)}->{len(comp)}, {ratio}%)')


def cmd_extract_alpha(args):
    src = args.input
    out = args.output
    if os.path.isdir(src):
        out_dir = out or (src + '_msk')
        os.makedirs(out_dir, exist_ok=True)
        for fn in sorted(os.listdir(src)):
            if not fn.lower().endswith('.png'):
                continue
            png_path = os.path.join(src, fn)
            out_path = os.path.join(out_dir, fn.rsplit('.', 1)[0] + '_M.MSK')
            _extract_alpha_png(png_path, out_path)
    else:
        out_path = out or (os.path.splitext(src)[0] + '_M.MSK')
        _extract_alpha_png(src, out_path)


def _extract_alpha_png(png_path, out_path):
    img = Image.open(png_path)
    if img.mode != 'RGBA':
        print(f'  警告: {png_path} 不是 RGBA, 生成全不透明 MSK')
        w, h = img.size
        raw = b'\xFF' * (w * h)
    else:
        raw = img.getchannel('A').tobytes()
    comp = compress(raw)
    Path(out_path).write_bytes(comp)
    print(f'  {os.path.basename(png_path)} alpha -> {os.path.basename(out_path)} ({len(raw)}->{len(comp)})')


def cmd_split_special(args):
    result = decode_msk_to_image(args.input, force_special=True)
    if result['size'] != (624, 580):
        raise ValueError('split_special 仅适用于 TITLE_PT_M 风格的 624x580 特殊 MSK')
    out_dir = args.output_dir or (os.path.splitext(args.input)[0] + '_split')
    os.makedirs(out_dir, exist_ok=True)
    full_path = os.path.join(out_dir, Path(args.input).stem + '.png')
    result['image'].save(full_path)
    save_special_slices(result['image'], full_path)
    print(f'  saved: {full_path} + p0/p1/p2')


def cmd_join_special(args):
    imgs = [Image.open(args.p0).convert('L'), Image.open(args.p1).convert('L'), Image.open(args.p2).convert('L')]
    for i, img in enumerate(imgs):
        if img.size != (208, 580):
            raise ValueError(f'p{i} 尺寸必须是 208x580，当前是 {img.size}')
    full = Image.new('L', (624, 580))
    full.paste(imgs[0], (0, 0))
    full.paste(imgs[1], (208, 0))
    full.paste(imgs[2], (416, 0))
    raw = full.tobytes()
    comp = compress(raw)
    Path(args.output).write_bytes(comp)
    print(f'  join_special -> {args.output} ({len(raw)}->{len(comp)})')

# ── argparse ─────────────────────────────────────────────────────────────────
def build_parser():
    p = argparse.ArgumentParser(description='AI5WIN MSK tool (formal)')
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('info', help='显示 MSK 信息')
    s.add_argument('input')
    s.add_argument('--ref', default=None)
    s.add_argument('--force-special', action='store_true', help='强制按 TITLE_PT_M 特殊格式判断')
    s.set_defaults(func=cmd_info)

    s = sub.add_parser('decode', help='MSK -> PNG')
    s.add_argument('input')
    s.add_argument('ref', nargs='?', default=None)
    s.add_argument('output', nargs='?', default=None)
    s.add_argument('--size', default=None, help='显式指定尺寸，如 624x580')
    s.add_argument('--split-special', action='store_true', help='若为 TITLE_PT_M，额外导出 p0/p1/p2')
    s.add_argument('--force-special', action='store_true', help='强制按 TITLE_PT_M 特殊格式解码')
    s.set_defaults(func=cmd_decode)

    s = sub.add_parser('merge', help='G24 + 普通单张 MSK -> RGBA PNG')
    s.add_argument('g24')
    s.add_argument('msk')
    s.add_argument('output', nargs='?', default=None)
    s.add_argument('--size', default=None)
    s.add_argument('--force-special', action='store_true')
    s.set_defaults(func=cmd_merge)

    s = sub.add_parser('encode', help='灰度 PNG -> MSK')
    s.add_argument('input')
    s.add_argument('output', nargs='?', default=None)
    s.set_defaults(func=cmd_encode)

    s = sub.add_parser('extract_alpha', help='RGBA PNG alpha -> MSK')
    s.add_argument('input')
    s.add_argument('output', nargs='?', default=None)
    s.set_defaults(func=cmd_extract_alpha)

    s = sub.add_parser('split_special', help='拆分 TITLE_PT_M 为整图 + p0/p1/p2')
    s.add_argument('input')
    s.add_argument('output_dir', nargs='?', default=None)
    s.set_defaults(func=cmd_split_special)

    s = sub.add_parser('join_special', help='将 3 个 208x580 切片重新拼成 TITLE_PT_M 并压缩')
    s.add_argument('p0')
    s.add_argument('p1')
    s.add_argument('p2')
    s.add_argument('output')
    s.set_defaults(func=cmd_join_special)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()
