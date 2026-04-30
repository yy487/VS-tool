#!/usr/bin/env python3
"""
akb_tool.py - AI6WIN AKB image format converter
Supports: AKB → PNG (decode) and PNG → AKB (encode)

AKB Format (0x20 header + LZSS compressed delta-encoded pixels):
  +0x00  u32  magic      'AKB ' (0x20424B41) or 'AKB+' (0x2B424B41, incremental)
  +0x04  u16  width
  +0x06  u16  height
  +0x08  u32  flags      bit30=24bpp(1)/32bpp(0), bit31=has_alpha
  +0x0C  4B   bg_color   BGRA background fill
  +0x10  i32  offset_x
  +0x14  i32  offset_y
  +0x18  i32  inner_right   (inner_width = inner_right - offset_x)
  +0x1C  i32  inner_bottom  (inner_height = inner_bottom - offset_y)
  +0x20  ...  LZSS compressed pixel data (bottom-up, delta encoded, BGR/BGRA)

LZSS: 4KB window, init pos 0xFEE, fill 0x00, 12-bit offset + 4-bit length, min match 3
Delta: horizontal accumulation per row, then vertical accumulation between rows
Pixel order: bottom-up rows, BGR(A) channels

Usage:
  python akb_tool.py decode <input.akb> [output.png]
  python akb_tool.py encode <input.png> [output.akb] [--flags 0x80000000] [--bg 00000000]
                     [--ox 0] [--oy 0] [--iw WIDTH] [--ih HEIGHT]
  python akb_tool.py info <input.akb>
  python akb_tool.py batch_decode <input_dir> [output_dir]
  python akb_tool.py batch_encode <input_dir> [output_dir]
"""

import struct
import sys
import os
import argparse
from pathlib import Path

try:
    from PIL import Image
    import numpy as np
except ImportError:
    print("ERROR: pip install Pillow numpy", file=sys.stderr)
    sys.exit(1)

# ─── Try loading C accelerator ──────────────────────────────────────────────

_c_lzss = None
try:
    import ctypes
    import platform
    _base_dir = os.path.dirname(os.path.abspath(__file__))
    _lib_name = None
    if sys.platform == 'win32':
        # try architecture-specific dll first
        _arch = platform.architecture()[0]  # '32bit' or '64bit'
        if _arch == '64bit':
            _candidates = ['lzss_fast_x64.dll', 'lzss_fast.dll']
        else:
            _candidates = ['lzss_fast.dll', 'lzss_fast_x64.dll']
    else:
        _candidates = ['lzss_fast.so']
    for _name in _candidates:
        _path = os.path.join(_base_dir, _name)
        if os.path.exists(_path):
            _lib_name = _path
            break
    if _lib_name:
        _c_lzss = ctypes.CDLL(_lib_name)
        _c_lzss.lzss_decompress.argtypes = [
            ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        _c_lzss.lzss_decompress.restype = ctypes.c_int
        _c_lzss.lzss_compress.argtypes = [
            ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        _c_lzss.lzss_compress.restype = ctypes.c_int
        _c_lzss.lzss_compress_literal.argtypes = [
            ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        _c_lzss.lzss_compress_literal.restype = ctypes.c_int
except Exception:
    _c_lzss = None


# ─── LZSS ───────────────────────────────────────────────────────────────────

LZSS_WINDOW = 4096
LZSS_INIT_POS = 0xFEE
LZSS_MIN_MATCH = 3
LZSS_MAX_MATCH = 18  # 4-bit length + 3


def _c_decompress(data: bytes, decomp_size: int) -> bytes:
    """LZSS decompress via C accelerator."""
    import ctypes
    dst = ctypes.create_string_buffer(decomp_size)
    n = _c_lzss.lzss_decompress(data, len(data), dst, decomp_size)
    return dst.raw[:n]


def _c_compress(data: bytes) -> bytes:
    """LZSS compress via C accelerator (hash chain)."""
    import ctypes
    cap = len(data) * 2 + 1024
    dst = ctypes.create_string_buffer(cap)
    n = _c_lzss.lzss_compress(data, len(data), dst, cap)
    return dst.raw[:n]


def _c_compress_literal(data: bytes) -> bytes:
    """LZSS literal compress via C accelerator."""
    import ctypes
    cap = len(data) + len(data) // 8 + 16
    dst = ctypes.create_string_buffer(cap)
    n = _c_lzss.lzss_compress_literal(data, len(data), dst, cap)
    return dst.raw[:n]


def lzss_decompress(data: bytes, decomp_size: int) -> bytes:
    """LZSS decompress - C accelerated with Python fallback."""
    if _c_lzss is not None:
        return _c_decompress(data, decomp_size)
    return _py_lzss_decompress(data, decomp_size)


def lzss_compress(data: bytes) -> bytes:
    """LZSS compress (greedy) - C accelerated with Python fallback."""
    if _c_lzss is not None:
        return _c_compress(data)
    return _py_lzss_compress(data)


def lzss_compress_literal(data: bytes) -> bytes:
    """Pure literal LZSS - C accelerated with Python fallback."""
    if _c_lzss is not None:
        return _c_compress_literal(data)
    return _py_lzss_compress_literal(data)


def _py_lzss_decompress(data: bytes, decomp_size: int) -> bytes:
    """Standard LZSS decompress: 4KB window, init 0xFEE, fill 0x00."""
    window = bytearray(LZSS_WINDOW)
    win_pos = LZSS_INIT_POS
    out = bytearray()
    src = 0
    flags = 0
    bits = 0

    while len(out) < decomp_size and src < len(data):
        if bits == 0:
            flags = data[src]
            src += 1
            bits = 8

        if flags & 1:
            # literal
            if src >= len(data):
                break
            b = data[src]
            src += 1
            out.append(b)
            window[win_pos] = b
            win_pos = (win_pos + 1) & 0xFFF
        else:
            # match
            if src + 1 >= len(data):
                break
            lo = data[src]
            hi = data[src + 1]
            src += 2
            offset = lo | ((hi & 0xF0) << 4)
            length = (hi & 0x0F) + LZSS_MIN_MATCH
            for i in range(length):
                if len(out) >= decomp_size:
                    break
                b = window[(offset + i) & 0xFFF]
                out.append(b)
                window[win_pos] = b
                win_pos = (win_pos + 1) & 0xFFF

        flags >>= 1
        bits -= 1

    return bytes(out[:decomp_size])


def _py_lzss_compress(data: bytes) -> bytes:
    window = bytearray(LZSS_WINDOW)
    win_pos = LZSS_INIT_POS
    src = 0
    out = bytearray()
    data_len = len(data)
    MAX_SCAN = 256  # limit backward scan for performance

    while src < data_len:
        flag_byte = 0
        flag_pos = len(out)
        out.append(0)  # placeholder

        for bit in range(8):
            if src >= data_len:
                flag_byte |= (1 << bit)
                out.append(0)
                continue

            best_len = 0
            best_off = 0
            max_match = min(LZSS_MAX_MATCH, data_len - src)

            if max_match >= LZSS_MIN_MATCH:
                scan_limit = min(MAX_SCAN, LZSS_WINDOW)
                for step in range(1, scan_limit + 1):
                    cand = (win_pos - step) & 0xFFF
                    # Simulate decompressor: it writes window[wp+i] before
                    # reading window[off+i+1], so overlapping ranges matter.
                    ml = 0
                    sim_buf = []
                    while ml < max_match:
                        read_pos = (cand + ml) & 0xFFF
                        # check if read_pos was already written in this match
                        b = None
                        for k in range(ml):
                            if ((win_pos + k) & 0xFFF) == read_pos:
                                b = sim_buf[k]
                                break
                        if b is None:
                            b = window[read_pos]
                        if b != data[src + ml]:
                            break
                        sim_buf.append(b)
                        ml += 1
                    if ml > best_len:
                        best_len = ml
                        best_off = cand
                        if best_len == max_match:
                            break

            if best_len >= LZSS_MIN_MATCH:
                lo = best_off & 0xFF
                hi = ((best_off >> 4) & 0xF0) | ((best_len - LZSS_MIN_MATCH) & 0x0F)
                out.append(lo)
                out.append(hi)
                for i in range(best_len):
                    window[win_pos] = data[src]
                    win_pos = (win_pos + 1) & 0xFFF
                    src += 1
            else:
                flag_byte |= (1 << bit)
                b = data[src]
                out.append(b)
                window[win_pos] = b
                win_pos = (win_pos + 1) & 0xFFF
                src += 1

        out[flag_pos] = flag_byte

    return bytes(out)


def _py_lzss_compress_literal(data: bytes) -> bytes:
    out = bytearray()
    src = 0
    while src < len(data):
        chunk = min(8, len(data) - src)
        out.append((1 << chunk) - 1)  # all literal bits set
        for i in range(chunk):
            out.append(data[src])
            src += 1
    return bytes(out)


# ─── Delta encoding/decoding ────────────────────────────────────────────────

def restore_delta(pixels: bytearray, stride: int, pixel_size: int):
    """Restore delta encoding in-place: horizontal then vertical accumulation."""
    # horizontal: first row, skip first pixel
    for i in range(pixel_size, stride):
        pixels[i] = (pixels[i] + pixels[i - pixel_size]) & 0xFF
    # vertical: subsequent rows
    for i in range(stride, len(pixels)):
        pixels[i] = (pixels[i] + pixels[i - stride]) & 0xFF


def apply_delta(pixels: bytearray, stride: int, pixel_size: int):
    """Apply delta encoding in-place: reverse of restore_delta."""
    # reverse vertical: from bottom to top (skip first row)
    for i in range(len(pixels) - 1, stride - 1, -1):
        pixels[i] = (pixels[i] - pixels[i - stride]) & 0xFF
    # reverse horizontal: from right to left in first row (skip first pixel)
    for i in range(stride - 1, pixel_size - 1, -1):
        pixels[i] = (pixels[i] - pixels[i - pixel_size]) & 0xFF


# ─── AKB Header ─────────────────────────────────────────────────────────────

AKB_HEADER_SIZE = 0x20
AKB_MAGIC = 0x20424B41   # 'AKB '
AKB_MAGIC_INC = 0x2B424B41  # 'AKB+'

FLAG_24BPP = 0x40000000
FLAG_ALPHA = 0x80000000


class AkbHeader:
    def __init__(self):
        self.magic = AKB_MAGIC
        self.width = 0
        self.height = 0
        self.flags = 0
        self.bg_color = b'\x00\x00\x00\x00'
        self.offset_x = 0
        self.offset_y = 0
        self.inner_right = 0
        self.inner_bottom = 0
        self.is_incremental = False

    @property
    def bpp(self):
        return 24 if (self.flags & FLAG_24BPP) else 32

    @property
    def pixel_size(self):
        return self.bpp // 8

    @property
    def inner_width(self):
        return self.inner_right - self.offset_x

    @property
    def inner_height(self):
        return self.inner_bottom - self.offset_y

    @property
    def has_alpha(self):
        return bool(self.flags & FLAG_ALPHA)

    @property
    def data_offset(self):
        base = AKB_HEADER_SIZE
        if self.is_incremental:
            base += 0x20  # base filename field
        return base

    @classmethod
    def from_bytes(cls, data: bytes):
        if len(data) < AKB_HEADER_SIZE:
            raise ValueError(f"Data too short for AKB header: {len(data)} < {AKB_HEADER_SIZE}")
        h = cls()
        h.magic = struct.unpack_from('<I', data, 0)[0]
        if h.magic == AKB_MAGIC_INC:
            h.is_incremental = True
        elif h.magic != AKB_MAGIC:
            raise ValueError(f"Bad AKB magic: 0x{h.magic:08X}")
        h.width = struct.unpack_from('<H', data, 4)[0]
        h.height = struct.unpack_from('<H', data, 6)[0]
        h.flags = struct.unpack_from('<I', data, 8)[0]
        h.bg_color = data[0x0C:0x10]
        h.offset_x = struct.unpack_from('<i', data, 0x10)[0]
        h.offset_y = struct.unpack_from('<i', data, 0x14)[0]
        h.inner_right = struct.unpack_from('<i', data, 0x18)[0]
        h.inner_bottom = struct.unpack_from('<i', data, 0x1C)[0]
        return h

    def to_bytes(self) -> bytes:
        return struct.pack('<I HH I 4s i i i i',
                           self.magic,
                           self.width, self.height,
                           self.flags,
                           self.bg_color,
                           self.offset_x, self.offset_y,
                           self.inner_right, self.inner_bottom)

    def __str__(self):
        magic_str = 'AKB+' if self.is_incremental else 'AKB '
        return (f"Magic: {magic_str}  Size: {self.width}x{self.height}  "
                f"BPP: {self.bpp}  Flags: 0x{self.flags:08X}\n"
                f"BG: {self.bg_color.hex()}  "
                f"Offset: ({self.offset_x}, {self.offset_y})\n"
                f"Inner: ({self.offset_x},{self.offset_y})-"
                f"({self.inner_right},{self.inner_bottom}) = "
                f"{self.inner_width}x{self.inner_height}  "
                f"Alpha: {self.has_alpha}")


# ─── Decode ─────────────────────────────────────────────────────────────────

def decode_akb(data: bytes) -> Image.Image:
    """Decode AKB data to PIL Image (RGBA or RGB)."""
    hdr = AkbHeader.from_bytes(data)
    pixel_size = hdr.pixel_size
    stride = hdr.width * pixel_size

    # handle empty inner region
    if hdr.inner_width <= 0 or hdr.inner_height <= 0:
        # just background
        img = Image.new('RGBA' if hdr.bpp == 32 else 'RGB',
                        (hdr.width, hdr.height))
        bg = hdr.bg_color
        if hdr.bpp == 32:
            fill = (bg[2], bg[1], bg[0], bg[3])  # BGRA → RGBA
        else:
            fill = (bg[2], bg[1], bg[0])
        for y in range(hdr.height):
            for x in range(hdr.width):
                img.putpixel((x, y), fill)
        return img

    # decompress inner pixels
    inner_stride = hdr.inner_width * pixel_size
    decomp_size = hdr.inner_height * inner_stride
    compressed = data[hdr.data_offset:]
    raw = lzss_decompress(compressed, decomp_size)
    pixels = bytearray(raw)

    # bottom-up → top-down: reverse row order
    rows = []
    for y in range(hdr.inner_height):
        row_start = (hdr.inner_height - 1 - y) * inner_stride
        rows.append(pixels[row_start:row_start + inner_stride])
    pixels = bytearray(b''.join(rows))

    # restore delta
    restore_delta(pixels, inner_stride, pixel_size)

    # if inner == full image, directly use pixels
    if (hdr.inner_width == hdr.width and hdr.inner_height == hdr.height and
            hdr.offset_x == 0 and hdr.offset_y == 0):
        pass
    else:
        # composite onto background
        full = bytearray(hdr.height * stride)
        bg32 = struct.unpack_from('<I', hdr.bg_color, 0)[0]
        if bg32 != 0:
            for i in range(0, stride, pixel_size):
                full[i:i + pixel_size] = hdr.bg_color[:pixel_size]
            row0 = bytes(full[:stride])
            for y in range(1, hdr.height):
                full[y * stride:(y + 1) * stride] = row0

        # blit inner onto full
        for y in range(hdr.inner_height):
            src_off = y * inner_stride
            dst_off = (hdr.offset_y + y) * stride + hdr.offset_x * pixel_size
            full[dst_off:dst_off + inner_stride] = pixels[src_off:src_off + inner_stride]
        pixels = full

    # convert BGR(A) → RGB(A)
    arr = np.frombuffer(bytes(pixels), dtype=np.uint8)
    if pixel_size == 4:
        arr = arr.reshape((hdr.height, hdr.width, 4))
        # BGRA → RGBA
        arr = arr[:, :, [2, 1, 0, 3]].copy()
        mode = 'RGBA'
    else:
        arr = arr.reshape((hdr.height, hdr.width, 3))
        # BGR → RGB
        arr = arr[:, :, [2, 1, 0]].copy()
        mode = 'RGB'

    return Image.fromarray(arr, mode)


# ─── Encode ─────────────────────────────────────────────────────────────────

def encode_akb(img: Image.Image, flags=None, bg_color=None,
               offset_x=0, offset_y=0, inner_w=None, inner_h=None,
               use_literal_lzss=False) -> bytes:
    """Encode PIL Image to AKB format.

    Args:
        img: PIL Image (RGBA or RGB)
        flags: AKB flags (auto-detected if None)
        bg_color: 4 bytes BGRA background (default 0x00000000)
        offset_x, offset_y: inner region offset
        inner_w, inner_h: inner region size (default = full image)
        use_literal_lzss: if True, use pure literal LZSS (safe, larger)
    """
    width, height = img.size

    if inner_w is None:
        inner_w = width - offset_x
    if inner_h is None:
        inner_h = height - offset_y

    # determine bpp from image mode
    if img.mode == 'RGBA':
        pixel_size = 4
        arr = np.array(img)
        # RGBA → BGRA
        arr = arr[:, :, [2, 1, 0, 3]].copy()
        auto_flags = FLAG_ALPHA  # 0x80000000
    elif img.mode == 'RGB':
        pixel_size = 3
        arr = np.array(img)
        # RGB → BGR
        arr = arr[:, :, [2, 1, 0]].copy()
        auto_flags = FLAG_24BPP  # 0x40000000
    else:
        img = img.convert('RGBA')
        pixel_size = 4
        arr = np.array(img)
        arr = arr[:, :, [2, 1, 0, 3]].copy()
        auto_flags = FLAG_ALPHA

    if flags is None:
        flags = auto_flags

    if bg_color is None:
        bg_color = b'\x00\x00\x00\x00'

    # extract inner region
    inner_arr = arr[offset_y:offset_y + inner_h, offset_x:offset_x + inner_w]
    inner_stride = inner_w * pixel_size

    # flatten to bytes (top-down order)
    pixels = bytearray(inner_arr.tobytes())

    # apply delta encoding
    apply_delta(pixels, inner_stride, pixel_size)

    # top-down → bottom-up: reverse row order
    rows = []
    for y in range(inner_h):
        row_start = (inner_h - 1 - y) * inner_stride
        rows.append(pixels[row_start:row_start + inner_stride])
    pixels = bytearray(b''.join(rows))

    # LZSS compress
    if use_literal_lzss:
        compressed = lzss_compress_literal(bytes(pixels))
    else:
        compressed = lzss_compress(bytes(pixels))

    # build header
    hdr = AkbHeader()
    hdr.magic = AKB_MAGIC
    hdr.width = width
    hdr.height = height
    hdr.flags = flags
    hdr.bg_color = bg_color
    hdr.offset_x = offset_x
    hdr.offset_y = offset_y
    hdr.inner_right = offset_x + inner_w
    hdr.inner_bottom = offset_y + inner_h

    return hdr.to_bytes() + compressed


# ─── CLI ─────────────────────────────────────────────────────────────────────

def cmd_info(args):
    data = Path(args.input).read_bytes()
    hdr = AkbHeader.from_bytes(data)
    print(hdr)
    comp_size = len(data) - hdr.data_offset
    decomp_size = hdr.inner_width * hdr.inner_height * hdr.pixel_size
    print(f"Compressed: {comp_size} B  Decompressed: {decomp_size} B  "
          f"Ratio: {comp_size / max(decomp_size, 1):.1%}")


def cmd_decode(args):
    data = Path(args.input).read_bytes()
    img = decode_akb(data)
    out = args.output or Path(args.input).with_suffix('.png')
    img.save(str(out))
    print(f"[OK] {args.input} → {out}  ({img.size[0]}x{img.size[1]} {img.mode})")


def cmd_encode(args):
    img = Image.open(args.input)
    flags = int(args.flags, 16) if args.flags else None
    bg = bytes.fromhex(args.bg) if args.bg else None
    data = encode_akb(img, flags=flags, bg_color=bg,
                      offset_x=args.ox, offset_y=args.oy,
                      inner_w=args.iw, inner_h=args.ih,
                      use_literal_lzss=args.literal)
    out = args.output or Path(args.input).with_suffix('.akb')
    Path(out).write_bytes(data)
    print(f"[OK] {args.input} → {out}  ({len(data)} bytes)")


def cmd_batch_decode(args):
    in_dir = Path(args.input_dir)
    out_dir = Path(args.output_dir) if args.output_dir else in_dir / 'png_out'
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(in_dir.glob('*.akb')) + sorted(in_dir.glob('*.AKB'))
    ok = err = 0
    for f in files:
        try:
            data = f.read_bytes()
            img = decode_akb(data)
            out_path = out_dir / f.with_suffix('.png').name
            img.save(str(out_path))
            ok += 1
        except Exception as e:
            print(f"[ERR] {f.name}: {e}", file=sys.stderr)
            err += 1
    print(f"Batch decode: {ok} OK, {err} errors")


def cmd_batch_encode(args):
    in_dir = Path(args.input_dir)
    out_dir = Path(args.output_dir) if args.output_dir else in_dir / 'akb_out'
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(in_dir.glob('*.png')) + sorted(in_dir.glob('*.PNG'))
    ok = err = 0
    for f in files:
        try:
            img = Image.open(f)
            data = encode_akb(img, use_literal_lzss=args.literal if hasattr(args, 'literal') else False)
            out_path = out_dir / f.with_suffix('.akb').name
            out_path.write_bytes(data)
            ok += 1
        except Exception as e:
            print(f"[ERR] {f.name}: {e}", file=sys.stderr)
            err += 1
    print(f"Batch encode: {ok} OK, {err} errors")


def cmd_roundtrip(args):
    """Verify: AKB → decode → encode → decode → compare pixels."""
    data = Path(args.input).read_bytes()
    hdr = AkbHeader.from_bytes(data)
    img1 = decode_akb(data)

    # re-encode with original header params
    data2 = encode_akb(img1, flags=hdr.flags, bg_color=hdr.bg_color,
                       offset_x=hdr.offset_x, offset_y=hdr.offset_y,
                       inner_w=hdr.inner_width, inner_h=hdr.inner_height,
                       use_literal_lzss=True)
    img2 = decode_akb(data2)

    arr1 = np.array(img1)
    arr2 = np.array(img2)
    if np.array_equal(arr1, arr2):
        print(f"[PASS] {args.input} round-trip pixel-perfect")
    else:
        diff = np.count_nonzero(arr1 != arr2)
        total = arr1.size
        print(f"[FAIL] {args.input} {diff}/{total} bytes differ")


def main():
    parser = argparse.ArgumentParser(description='AKB image tool (AI6WIN)')
    sub = parser.add_subparsers(dest='command')

    p = sub.add_parser('info', help='Show AKB header info')
    p.add_argument('input')

    p = sub.add_parser('decode', help='AKB → PNG')
    p.add_argument('input')
    p.add_argument('output', nargs='?')

    p = sub.add_parser('encode', help='PNG → AKB')
    p.add_argument('input')
    p.add_argument('output', nargs='?')
    p.add_argument('--flags', default=None, help='Hex flags (e.g. 80000000)')
    p.add_argument('--bg', default=None, help='Background BGRA hex (e.g. 00000000)')
    p.add_argument('--ox', type=int, default=0, help='Offset X')
    p.add_argument('--oy', type=int, default=0, help='Offset Y')
    p.add_argument('--iw', type=int, default=None, help='Inner width')
    p.add_argument('--ih', type=int, default=None, help='Inner height')
    p.add_argument('--literal', action='store_true', help='Use literal LZSS (safe, larger)')

    p = sub.add_parser('batch_decode', help='Batch AKB → PNG')
    p.add_argument('input_dir')
    p.add_argument('output_dir', nargs='?')

    p = sub.add_parser('batch_encode', help='Batch PNG → AKB')
    p.add_argument('input_dir')
    p.add_argument('output_dir', nargs='?')
    p.add_argument('--literal', action='store_true', help='Use literal LZSS')

    p = sub.add_parser('roundtrip', help='Verify round-trip (AKB→PNG→AKB→PNG pixel compare)')
    p.add_argument('input')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        'info': cmd_info,
        'decode': cmd_decode,
        'encode': cmd_encode,
        'batch_decode': cmd_batch_decode,
        'batch_encode': cmd_batch_encode,
        'roundtrip': cmd_roundtrip,
    }
    cmds[args.command](args)


if __name__ == '__main__':
    main()
