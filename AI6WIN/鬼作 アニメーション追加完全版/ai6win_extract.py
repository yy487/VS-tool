#!/usr/bin/env python3
"""
AI6WIN MES 文本提取工具
输入: LZSS压缩的MES + __arc_index.json (提供uncompressed_size)
输出: GalTransl兼容JSON

用法:
  python ai6win_extract.py <input.mes> <arc_index.json> [output.json]
  python ai6win_extract.py <mes_dir> <arc_index.json> <json_dir>  (批量)
"""
import struct, json, sys, os

# ── LZSS 解压 ──
def lzss_decompress(src, usize):
    out = bytearray(); window = bytearray(b'\x00'*4096); wp = 0xFEE; sp = 0
    while sp < len(src) and len(out) < usize:
        flags = src[sp]; sp += 1
        for bit in range(8):
            if sp >= len(src) or len(out) >= usize: break
            if flags & (1 << bit):
                b = src[sp]; sp += 1; out.append(b); window[wp] = b; wp = (wp+1) & 0xFFF
            else:
                if sp+1 >= len(src): break
                lo = src[sp]; hi = src[sp+1]; sp += 2
                off = lo | ((hi & 0xF0) << 4); ml = (hi & 0x0F) + 3
                for k in range(ml):
                    if len(out) >= usize: break
                    b = window[(off+k) & 0xFFF]; out.append(b); window[wp] = b; wp = (wp+1) & 0xFFF
        #endfor bit
    return bytes(out)

# ── AI6WIN Opcode 表 (version 0) ──
# format字符: '' = 无参数, 'B' = 1字节, '>I' = 4B BE uint, '>i' = 4B BE int, 'S' = \0终止字符串
OPS = {
    0x00:'', 0x01:'', 0x02:'', 0x03:'', 0x04:'', 0x05:'',
    0x06:'', 0x07:'', 0x08:'', 0x09:'',
    0x0A:'S',   # STR_PRIMARY — 主文本(对话/旁白)
    0x0B:'S',   # STR_SUPPLEMENT
    0x0C:'', 0x0D:'', 0x0E:'', 0x0F:'',
    0x10:'',    # STLOC_VAR (v0: 无参数)
    0x11:'', 0x12:'', 0x13:'',
    0x14:'>I',  # JUMP_IF_ZERO (地址)
    0x15:'>I',  # JUMP (地址)
    0x16:'>I',  # LIBREG (地址)
    0x17:'', 0x18:'',
    0x19:'>I',  # MESSAGE (递增index)
    0x1A:'>I',  # CHOICE (地址)
    0x1B:'B',   # ESCAPE
    0x1D:'',
    0x32:'>i',  # PUSH_INT32
    0x33:'S',   # PUSH_STR
    0x34:'', 0x35:'',
    0x36:'',    # MUL (v0: 无参数)
    0x37:'', 0x38:'', 0x39:'',
    0x3A:'', 0x3B:'', 0x3C:'', 0x3D:'', 0x3E:'', 0x3F:'',
    0x40:'', 0x41:'', 0x42:'', 0x43:'',
    0xFA:'', 0xFB:'', 0xFC:'', 0xFD:'', 0xFE:'', 0xFF:'',
}
TEXT_OPS = {0x0A, 0x0B}      # 含文本的opcode
ADDR_OPS = {0x14, 0x15, 0x16, 0x1A}  # 含地址的opcode

def extract_file(mes_path, usize, json_path):
    compressed = open(mes_path, 'rb').read()
    data = lzss_decompress(compressed, usize)
    if len(data) != usize:
        print(f"  [WARN] {os.path.basename(mes_path)}: 解压大小不匹配 {len(data)} != {usize}")

    mc = struct.unpack_from('<I', data, 0)[0]
    hs = 4 + mc * 4

    entries = []; pos = hs; tid = 0
    while pos < len(data):
        b = data[pos]
        if b not in OPS:
            pos += 1; continue
        fmt = OPS[b]; pos += 1
        if fmt == '':
            pass
        elif fmt == 'B':
            pos += 1
        elif fmt in ('>I', '>i'):
            pos += 4
        elif fmt == 'S':
            end = data.find(b'\x00', pos)
            if end == -1: break
            if b in TEXT_OPS:
                tid += 1
                try:
                    text = data[pos:end].decode('cp932')
                except:
                    text = data[pos:end].hex(' ')
                # 拆分 ［名前］：台詞 格式
                name = ""
                msg = text
                if text.startswith('\uff3b'):  # ［
                    idx = text.find('\uff3d')   # ］
                    if idx != -1:
                        name = text[1:idx]
                        msg = text[idx+1:]
                        if msg.startswith('\uff1a'):  # ：
                            msg = msg[1:]
                entries.append({"id": tid, "name": name, "message": msg})
            pos = end + 1

    if not entries:
        print(f"  {os.path.basename(mes_path)}: 无文本, 跳过")
        return 0
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    print(f"  {os.path.basename(mes_path)}: {len(compressed)}->{len(data)} bytes, {len(entries)} texts")
    return len(entries)

def main():
    if len(sys.argv) < 3:
        print(f"用法:")
        print(f"  python {sys.argv[0]} <input.mes> <arc_index.json> [output.json]")
        print(f"  python {sys.argv[0]} <mes_dir>   <arc_index.json> <json_dir>  (批量)")
        sys.exit(1)

    src = sys.argv[1]
    idx_path = sys.argv[2]

    # 读arc index获取uncompressed_size
    with open(idx_path, 'r', encoding='utf-8') as f:
        arc_meta = json.load(f)
    size_map = {m['filename']: m['uncompressed_size'] for m in arc_meta}

    if os.path.isdir(src):
        out = sys.argv[3] if len(sys.argv) > 3 else src + '_json'
        os.makedirs(out, exist_ok=True)
        files = sorted(f for f in os.listdir(src) if f.lower().endswith('.mes') and not f.startswith('_'))
        total = 0
        for fn in files:
            if fn not in size_map:
                print(f"  [SKIP] {fn}: 不在arc_index中")
                continue
            jp = os.path.join(out, os.path.splitext(fn)[0] + '.json')
            total += extract_file(os.path.join(src, fn), size_map[fn], jp)
        print(f"[INFO] {len(files)} 文件, {total} 条文本")
    else:
        fn = os.path.basename(src)
        if fn not in size_map:
            print(f"[ERROR] {fn} 不在 {idx_path} 中"); sys.exit(1)
        jp = sys.argv[3] if len(sys.argv) > 3 else os.path.splitext(src)[0] + '.json'
        extract_file(src, size_map[fn], jp)

if __name__ == '__main__':
    main()
