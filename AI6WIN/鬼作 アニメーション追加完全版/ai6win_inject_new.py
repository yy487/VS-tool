#!/usr/bin/env python3
"""AI6WIN MES 文本注入工具 (GalTransl 格式)
适配 GalTransl 翻译后的 JSON 格式: {name, message, src_msg}
按顺序逐条匹配注入, 以 cp932 编码写回。

用法:
  单文件: python ai6win_inject_new.py <input.mes> <trans.json> <arc_index.json> [output.mes]
  批  量: python ai6win_inject_new.py <mes_dir>   <json_dir>   <arc_index.json> <output_dir>
"""
import sys, os, struct, json

# ── LZSS (窗口填充 0x00) ──
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
    return bytes(out)

def lzss_compress_fake(data):
    """伪压缩: 全 literal, 引擎解压100%还原"""
    out = bytearray()
    i = 0; n = len(data)
    while i < n:
        chunk = data[i:i+8]
        pad = 8 - len(chunk)
        out.append(0xFF)  # 8个literal位
        out += chunk + b'\x00' * pad
        i += 8
    return bytes(out)

# ── Opcode 表 (version 0) ──
OPS = {
    0x00:'', 0x01:'', 0x02:'', 0x03:'', 0x04:'', 0x05:'',
    0x06:'', 0x07:'', 0x08:'', 0x09:'',
    0x0A:'S', 0x0B:'S',
    0x0C:'', 0x0D:'', 0x0E:'', 0x0F:'', 0x10:'',
    0x11:'', 0x12:'', 0x13:'',
    0x14:'>I', 0x15:'>I', 0x16:'>I', 0x17:'', 0x18:'',
    0x19:'>I', 0x1A:'>I', 0x1B:'B', 0x1D:'',
    0x32:'>i', 0x33:'S', 0x34:'', 0x35:'', 0x36:'',
    0x37:'', 0x38:'', 0x39:'',
    0x3A:'', 0x3B:'', 0x3C:'', 0x3D:'', 0x3E:'', 0x3F:'',
    0x40:'', 0x41:'', 0x42:'', 0x43:'',
    0xFA:'', 0xFB:'', 0xFC:'', 0xFD:'', 0xFE:'', 0xFF:'',
}
TEXT_OPS = {0x0A, 0x0B}
ADDR_OPS = {0x14, 0x15, 0x16, 0x1A}

# ── 解析 MES 为切片列表 ──
def parse_mes(data):
    mc = struct.unpack_from('<I', data, 0)[0]
    hs = 4 + mc * 4
    hdr_offsets = [struct.unpack_from('<I', data, 4+i*4)[0] for i in range(mc)]

    slices = []; pos = hs; tid = 0
    while pos < len(data):
        b = data[pos]
        if b not in OPS:
            slices.append({'r': pos - hs, 'b': bytes([b]), 'k': 'raw'})
            pos += 1; continue

        fmt = OPS[b]; start = pos; pos += 1

        if b in TEXT_OPS:
            slices.append({'r': start - hs, 'b': bytes([b]), 'k': 'raw'})
            end = data.find(b'\x00', pos)
            if end == -1: end = len(data)
            slices.append({'r': pos - hs, 'b': data[pos:end+1], 'k': 'text', 'i': tid})
            tid += 1; pos = end + 1
        elif b in ADDR_OPS:
            val = struct.unpack_from('>I', data, pos)[0]
            slices.append({'r': start - hs, 'b': bytes([b]), 'k': 'raw'})
            slices.append({'r': pos - hs, 'b': data[pos:pos+4], 'k': 'addr', 't': val})
            pos += 4
        elif b == 0x19:
            slices.append({'r': start - hs, 'b': data[start:pos+4], 'k': 'raw'})
            pos += 4
        elif fmt == '':
            slices.append({'r': start - hs, 'b': bytes([b]), 'k': 'raw'})
        elif fmt == 'B':
            slices.append({'r': start - hs, 'b': data[start:pos+1], 'k': 'raw'})
            pos += 1
        elif fmt in ('>I', '>i'):
            slices.append({'r': start - hs, 'b': data[start:pos+4], 'k': 'raw'})
            pos += 4
        elif fmt == 'S':
            end = data.find(b'\x00', pos)
            if end == -1: end = len(data)
            slices.append({'r': start - hs, 'b': data[start:end+1], 'k': 'raw'})
            pos = end + 1
        else:
            slices.append({'r': start - hs, 'b': bytes([b]), 'k': 'raw'})

    return mc, hs, hdr_offsets, slices, tid

# ── 注入 ──
def inject_file(mes_path, json_path, idx_path_or_map, out_path):
    if isinstance(idx_path_or_map, dict):
        size_map = idx_path_or_map
    else:
        with open(idx_path_or_map, 'r', encoding='utf-8') as f:
            size_map = {m['filename']: m['uncompressed_size'] for m in json.load(f)}

    fn = os.path.basename(mes_path)
    if fn not in size_map:
        print(f"  [ERROR] {fn}: 不在arc_index中"); return None

    compressed = open(mes_path, 'rb').read()
    data = lzss_decompress(compressed, size_map[fn])

    with open(json_path, 'r', encoding='utf-8') as f:
        trans = json.load(f)

    # GalTransl 格式: [{name, message, src_msg}, ...] 按顺序对应
    # 构建按序号的翻译列表
    td = {}
    for i, e in enumerate(trans):
        name = e.get('name', '')
        msg = e.get('message', '')
        if name:
            full = f'\uff3b{name}\uff3d\uff1a{msg}'  # ［名前］：台詞
        else:
            full = msg
        td[i] = full

    mc, hs, hdr_off, slices, ttotal = parse_mes(data)

    if len(trans) != ttotal:
        print(f"  [WARN] {fn}: JSON条目数({len(trans)}) != MES文本数({ttotal})")

    # 重建字节码
    replaced = 0
    new = bytearray(); o2n = {}; fixups = []
    for s in slices:
        nr = len(new); o2n[s['r']] = nr

        if s['k'] == 'text':
            t = td.get(s['i'])
            if t is not None:
                new += t.encode('cp932') + b'\x00'
                replaced += 1
            else:
                new += s['b']
        elif s['k'] == 'addr':
            fixups.append((len(new), s['t']))
            new += b'\x00\x00\x00\x00'
        else:
            new += s['b']

    # 修正地址引用
    for fp, tgt in fixups:
        if tgt in o2n:
            struct.pack_into('>I', new, fp, o2n[tgt])
        else:
            candidates = [k for k in o2n if k <= tgt]
            if candidates:
                cl = max(candidates)
                struct.pack_into('>I', new, fp, o2n[cl] + (tgt - cl))
            else:
                struct.pack_into('>I', new, fp, tgt)

    # 重建 header
    hdr = struct.pack('<I', mc)
    for ov in hdr_off:
        if ov in o2n:
            hdr += struct.pack('<I', o2n[ov])
        else:
            candidates = [k for k in o2n if k <= ov]
            if candidates:
                cl = max(candidates)
                hdr += struct.pack('<I', o2n[cl] + (ov - cl))
            else:
                hdr += struct.pack('<I', ov)

    plain = hdr + new
    result = lzss_compress_fake(plain)
    with open(out_path, 'wb') as f:
        f.write(result)

    d = len(result) - len(compressed)
    print(f"  {fn}: {replaced}/{ttotal} texts, {len(compressed)}->{len(result)} "
          f"({'+' if d >= 0 else ''}{d}), uncomp={len(plain)}")
    return len(plain)

# ── main ──
def main():
    if len(sys.argv) < 4:
        print(__doc__); sys.exit(1)

    src, jsrc, idx_path = sys.argv[1], sys.argv[2], sys.argv[3]

    with open(idx_path, 'r', encoding='utf-8') as f:
        arc_meta = json.load(f)
    size_map = {m['filename']: m['uncompressed_size'] for m in arc_meta}

    if os.path.isdir(src):
        if len(sys.argv) < 5:
            print("批量模式需要 output_dir"); sys.exit(1)
        od = sys.argv[4]; os.makedirs(od, exist_ok=True)
        new_sizes = {}
        for fn in sorted(os.listdir(src)):
            sp = os.path.join(src, fn); op = os.path.join(od, fn)
            if fn.startswith('_') or not fn.lower().endswith('.mes'):
                open(op, 'wb').write(open(sp, 'rb').read()); continue
            jp = os.path.join(jsrc, os.path.splitext(fn)[0] + '.json')
            if not os.path.exists(jp):
                open(op, 'wb').write(open(sp, 'rb').read()); continue
            try:
                new_usize = inject_file(sp, jp, size_map, op)
                if new_usize is not None:
                    new_sizes[fn] = new_usize
            except Exception as e:
                print(f"  [ERROR] {fn}: {e}")
                import traceback; traceback.print_exc()
                open(op, 'wb').write(open(sp, 'rb').read())
        # 更新 __arc_index.json
        for m in arc_meta:
            if m['filename'] in new_sizes:
                m['uncompressed_size'] = new_sizes[m['filename']]
        idx_out = os.path.join(od, '__arc_index.json')
        with open(idx_out, 'w', encoding='utf-8') as f:
            json.dump(arc_meta, f, ensure_ascii=False, indent=2)
        print(f"[INFO] 完成, 已更新 {idx_out}")
    else:
        op = sys.argv[4] if len(sys.argv) > 4 else os.path.splitext(src)[0] + '_patched.mes'
        new_usize = inject_file(src, jsrc, size_map, op)
        if new_usize is not None:
            for m in arc_meta:
                if m['filename'] == os.path.basename(src):
                    m['uncompressed_size'] = new_usize; break
            with open(idx_path, 'w', encoding='utf-8') as f:
                json.dump(arc_meta, f, ensure_ascii=False, indent=2)
            print(f"[INFO] 已更新 {idx_path}")

if __name__ == '__main__':
    main()
