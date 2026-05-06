#!/usr/bin/env python3
"""AI5WIN v2 MES 文本注入工具 v4 (あしたの雪之丞2)
基于 ai5win_disasm 精确反汇编器的变长注入.

核心原理 (EXE 反汇编权威):
  1. 反汇编整个脚本, 精确定位所有"可替换字节区间":
     - 对 op 0x01 TEXT (双字节全角): TEXT 内容部分 (不含 NUL 终止)
     - 对 CH_POS 后紧跟的 TEXT: 选择支文本
     - 对 slot_list 里的 STR slot (菜单项、按钮): 若可翻译则替换

  2. 精确定位所有 u32 跳转载体 (extract_jump_targets):
     - op 0x09 PW_FLAG IMM4 (条件跳转)
     - op 0x0a PB_FLAG IMM4 (无条件跳转)
     - op 0x0e CH_POS  IMM4 (菜单项跳转)
     旧值都是 rel-to-bytecode-start (= abs - hs)

  3. 按起始位置排序替换区间, 累计 delta, 生成新 bytecode.

  4. 修 msg_offsets[] (header 里 mc 个 u32):
     new_rel[i] = old_rel[i] + cumulative_delta_before_old_msg_abs[i]

  5. 修所有 IMM4:
     new_imm = old_imm + cumulative_delta_before_(old_imm+hs)
     写回 new_bytecode 里 IMM4 的新位置.

  6. 编码: cp932 优先 → GBK 回退 → 特殊字符 SPECIAL_CHAR_MAP.
     半角 ASCII 自动转全角.

用法:
  python ai5win_mes_inject.py <input.mes> <trans.json> [output.mes]
  python ai5win_mes_inject.py <mes_dir>   <json_dir>   [output_dir]   (批量)
"""

import struct, json, sys, os
from ai5win_disasm import (
    lzss_decompress, lzss_compress_fake,
    parse_mes, extract_jump_targets,
    OP_HANDLERS,
)

# 这些开头说明 TEXT 本身是正文/书信/括号内文本，不能按 name 处理。
_NAME_FORBID_PREFIX = ('「', '『', '（', '(', '【', '［', '〔', '〈', '《', '　', ' ')


# ─── 编码 ───
SPECIAL_CHAR_MAP = {
    '♡': b'\xFE\x50', '♪': b'\xFE\x51',
    '俵': b'\xFE\x52', '咫': b'\xFE\x53', '啵': b'\xFE\x54',
    '噙': b'\xFE\x55', '妩': b'\xFE\x56', '嫖': b'\xFE\x57',
    '嬉': b'\xFE\x58', '屐': b'\xFE\x59', '師': b'\xFE\x5A',
    '幹': b'\xFE\x5B', '晧': b'\xFE\x5C', '沢': b'\xFE\x5D',
    '狩': b'\xFE\x5E',
}


def encode_text(s):
    """译文编码: 半角→全角, GBK 优先, cp932 回退, 特殊字符走 SPECIAL_CHAR_MAP.
    (游戏用位图字体+自定义 TBL 渲染, GBK/cp932 字节序列都被一视同仁查表渲染)"""
    out = bytearray()
    for c in s:
        if c == ' ':
            c = '\u3000'
        elif '!' <= c <= '~':
            c = chr(ord(c) - 0x21 + 0xFF01)

        if c in SPECIAL_CHAR_MAP:
            out += SPECIAL_CHAR_MAP[c]
        else:
            try:
                out += c.encode('gbk')
            except:
                try:
                    out += c.encode('cp932')
                except:
                    out += b'?'
    return bytes(out)


# ─── 定位可替换的文本字节区间 ───
def find_replaceable_texts(dec, hs, lines, msg_abs):
    """扫描整个脚本, 返回每条可替换文本的位置信息。

    在用户原有逻辑基础上只增加一种兼容：
      同一 block 内若明确出现两组以上 name-like TEXT + 0x11 + body TEXT，
      则拆为多个 text_slot_index，供同 id 的多个 JSON entry 按出现顺序回填。

    普通 block 仍保持原逻辑：
      - 一个 name + 多段 message TEXT 合并为一个 JSON message；
      - 注入时完整译文写入第一段 message，后续 message_tail 清空。
    """
    mc = len(msg_abs)

    # 建立 ops_by_id. 前导区归 id=-1
    prelude_ops = []
    ops_by_id = [[] for _ in range(mc)]
    id_idx = 0
    for item in lines:
        off = item[0]
        if off < msg_abs[0]:
            prelude_ops.append(item)
            continue
        while id_idx + 1 < mc and off >= msg_abs[id_idx + 1]:
            id_idx += 1
        ops_by_id[id_idx].append(item)

    results = []

    def scan_block(block_id, ops):
        if not ops:
            return

        is_choice_text = set()
        for k, (off, op, _, _) in enumerate(ops):
            if op == 0x0e and k + 1 < len(ops) and ops[k + 1][1] == 0x01:
                is_choice_text.add(k + 1)

        text_indices = [k for k, (_, op, _, _) in enumerate(ops)
                        if op == 0x01 and k not in is_choice_text]

        def _text_of_op(k):
            for (typ, ps, sz, val) in ops[k][2]:
                if typ == 'TEXT':
                    try:
                        return val.decode('cp932')
                    except Exception:
                        return ''
            return ''

        def has_name_marker(k):
            if not (k + 1 < len(ops) and ops[k + 1][1] == 0x11):
                return False
            first_text = _text_of_op(k)
            if first_text.startswith(_NAME_FORBID_PREFIX):
                return False
            want = first_text.encode('cp932', errors='ignore') + b'\n'
            j = k + 2
            while j < len(ops):
                opj = ops[j][1]
                if opj == 0x01:
                    return True
                if opj == 0x13:
                    return False
                if opj == 0x10:
                    for (typ, ps, sz, val) in ops[j][2]:
                        if typ != 'SLOTS':
                            continue
                        for sl in val:
                            if sl[0] == 'STR' and sl[3] == want:
                                return True
                j += 1
            return False

        def body_like_text(s):
            return bool(s) and s.startswith(('「', '『', '（', '(', '【', '［', '〔', '〈', '《', '　', ' '))

        # 生成 TEXT op 到 role/slot 的映射。
        # text_slot_index 对应同一个 id 下第几个普通正文 JSON entry。
        name_text_slots = {}        # op_index -> slot
        message_text_slots = {}     # op_index -> (slot, part_idx, combined_old)

        # 候选 name 在 text_indices 中的位置。
        candidate_positions = []
        for n, k in enumerate(text_indices[:-1]):
            next_text = _text_of_op(text_indices[n + 1])
            if has_name_marker(k) and body_like_text(next_text):
                candidate_positions.append(n)

        if len(candidate_positions) >= 2:
            # 多发言兼容：每组 name+body 对应一个 JSON entry。
            for slot, pos in enumerate(candidate_positions):
                next_pos = candidate_positions[slot + 1] if slot + 1 < len(candidate_positions) else len(text_indices)
                name_op = text_indices[pos]
                body_ops = text_indices[pos + 1:next_pos]
                name_text_slots[name_op] = slot
                parts = [_text_of_op(mi) for mi in body_ops]
                combined = ''.join(parts)
                for part_idx, mi in enumerate(body_ops):
                    message_text_slots[mi] = (slot, part_idx, combined)
        elif text_indices:
            # 原有逻辑：一个 block 一个正文 entry。
            first = text_indices[0]
            if has_name_marker(first) and len(text_indices) >= 2:
                name_text_slots[first] = 0
                body_ops = text_indices[1:]
                combined = ''.join(_text_of_op(mi) for mi in body_ops)
                for part_idx, mi in enumerate(body_ops):
                    message_text_slots[mi] = (0, part_idx, combined)
            elif has_name_marker(first):
                name_text_slots[first] = 0
            else:
                body_ops = text_indices
                combined = ''.join(_text_of_op(mi) for mi in body_ops)
                for part_idx, mi in enumerate(body_ops):
                    message_text_slots[mi] = (0, part_idx, combined)

        # 1. TEXT 指令
        choice_counter = 0
        for k, (off, op, args, _) in enumerate(ops):
            if op != 0x01:
                continue
            text_arg = None
            for (typ, ps, sz, val) in args:
                if typ == 'TEXT':
                    text_arg = (ps, sz, val)
                    break
            if text_arg is None:
                continue
            t_start, t_len, t_bytes = text_arg
            rec_extra = {}
            if k in is_choice_text:
                role = 'choice'
                rec_extra['choice_idx'] = choice_counter
                choice_counter += 1
            elif k in name_text_slots:
                role = 'name'
                rec_extra['text_slot_index'] = name_text_slots[k]
            elif k in message_text_slots:
                slot, part_idx, combined = message_text_slots[k]
                role = 'message' if part_idx == 0 else 'message_tail'
                rec_extra['text_slot_index'] = slot
                rec_extra['message_part_idx'] = part_idx
                rec_extra['message_combined_old'] = combined
            else:
                role = 'extra_text'
            try:
                old_s = t_bytes.decode('cp932')
            except Exception:
                old_s = None
            rec = {
                'id': block_id, 'kind': 'TEXT', 'role': role,
                'byte_start': t_start, 'byte_len': t_len,
                'old_text': old_s, 'op_offset': off, 'op': op,
            }
            rec.update(rec_extra)
            results.append(rec)

        # 2. MENU_SET 的 STR slot = 章节标题
        for (off, op, args, _) in ops:
            if op != 0x10:
                continue
            for (typ, ps, sz, val) in args:
                if typ != 'SLOTS':
                    continue
                for sl in val:
                    if sl[0] != 'STR':
                        continue
                    s_start, s_len, s_bytes = sl[1], sl[2], sl[3]
                    try:
                        txt = s_bytes.decode('cp932')
                    except Exception:
                        continue
                    raw_lower = s_bytes.lower()
                    if b'\n' in s_bytes or b'\\n' in s_bytes:
                        continue
                    if any(e in raw_lower for e in _RES_EXTS_FOR_INJ):
                        continue
                    if not any(0x81 <= b <= 0x9F or 0xE0 <= b <= 0xEF for b in s_bytes):
                        continue
                    results.append({
                        'id': block_id, 'kind': 'STR', 'role': 'chapter_title',
                        'byte_start': s_start, 'byte_len': s_len,
                        'old_text': txt, 'op_offset': off, 'op': op,
                    })

    scan_block(-1, prelude_ops)
    for i in range(mc):
        scan_block(i, ops_by_id[i])

    return results


_RES_EXTS_FOR_INJ = (b'.g24', b'.msk', b'.ogg', b'.wav', b'.bmp', b'.png',
                      b'.mes', b'.ea6', b'.ea5', b'.eav', b'.ttf', b'.fnt')


def _pick_replacement(rep_info, trans_bucket):
    """按 rep 的 role 到对应 bucket slot 取译文。

    trans_bucket:
      {
        'texts': [entry0, entry1, ...],   # 同 id 下普通正文条目，按 JSON 出现顺序
        'choices': {idx: entry},
        'title': entry
      }
    """
    role = rep_info['role']
    old = rep_info.get('old_text')

    def get_text_entry(slot):
        texts = trans_bucket.get('texts') or []
        if 0 <= slot < len(texts):
            return texts[slot]
        # 兼容极旧结构：只有 text 单项
        return trans_bucket.get('text')

    if role == 'name':
        slot = rep_info.get('text_slot_index', 0)
        te = get_text_entry(slot)
        if te and 'name' in te:
            new = te.get('name')
            if new and new != old:
                return new
        return None

    if role == 'message':
        slot = rep_info.get('text_slot_index', 0)
        te = get_text_entry(slot)
        if te:
            new = te.get('message', te.get('msg'))
            old_full = rep_info.get('message_combined_old') or old
            if new and new != old_full:
                return new
        return None

    if role == 'message_tail':
        slot = rep_info.get('text_slot_index', 0)
        te = get_text_entry(slot)
        if te:
            new = te.get('message', te.get('msg'))
            old_full = rep_info.get('message_combined_old') or old
            # 多段 TEXT 合并为一个 JSON message 翻译时，首段写完整译文，
            # 后续 continuation TEXT 清空，避免原文尾巴残留。
            if new and new != old_full:
                return ''
        return None

    if role == 'choice':
        idx = rep_info.get('choice_idx', 0)
        te = trans_bucket.get('choices', {}).get(idx)
        if te:
            new = te.get('message', te.get('msg'))
            if new and new != old:
                return new
        return None

    if role == 'chapter_title':
        te = trans_bucket.get('title')
        if te:
            new = te.get('message', te.get('msg'))
            if new and new != old:
                return new
        return None

    return None


def _build_replacements(reps, td):
    """td: {id: bucket}. bucket = {'text', 'choices', 'title'}.
    返回 [(byte_start, byte_end, new_bytes)]"""
    out = []
    for r in reps:
        bucket = td.get(r['id'])
        if not bucket:
            continue
        new_text = _pick_replacement(r, bucket)
        if new_text is None:
            continue
        old_text = r.get('old_text') or ''
        if new_text == old_text:
            continue
        new_bytes = b'' if new_text == '' else encode_text(new_text)
        out.append((r['byte_start'], r['byte_start'] + r['byte_len'], new_bytes))
    out.sort(key=lambda x: x[0])
    # 去重: 同位置不同替换只保留第一个
    dedup = []
    last_end = -1
    for (s, e, nb) in out:
        if s < last_end:
            continue  # overlap
        dedup.append((s, e, nb))
        last_end = e
    return dedup


def inject_file(mes_path, json_path, out_path, verbose=True):
    compressed = open(mes_path, 'rb').read()
    dec = lzss_decompress(compressed)

    with open(json_path, 'r', encoding='utf-8') as f:
        trans = json.load(f)

    mc, hs, msg_rel, msg_abs, lines = parse_mes(dec)

    # 按 id 聚合 trans entries. 同一 id 可以有多条:
    #   - 正文 (无 is_choice/is_chapter_title 标记)
    #   - 多个 is_choice (按 choice_idx 对应原文选择支顺序)
    #   - 一个 is_chapter_title
    td = {}   # {id: {'texts': [entry...], 'choices': {idx: entry}, 'title': entry}}
    for e in trans:
        bid = e['id']
        bucket = td.setdefault(bid, {'texts': [], 'choices': {}, 'title': None})
        if e.get('is_choice'):
            idx = e.get('choice_idx', 0)
            bucket['choices'][idx] = e
        elif e.get('is_chapter_title'):
            bucket['title'] = e
        else:
            # 同一个 id 可能有多个普通正文 entry，例如同 block 多发言。
            # 不依赖隐藏字段，直接按 JSON 出现顺序对应脚本中的 segment 顺序。
            bucket['texts'].append(e)

    # 定位可替换文本
    reps = find_replaceable_texts(dec, hs, lines, msg_abs)
    replacements = _build_replacements(reps, td)

    if not replacements:
        open(out_path, 'wb').write(compressed)
        if verbose:
            print(f"  {os.path.basename(mes_path)}: 无修改")
        return

    # 收集所有 IMM4 跳转载体
    jumps = extract_jump_targets(lines)  # [(bc_abs_pos, rel_val, op)]

    # 构建新 bytecode (bytecode 部分: dec[hs:])
    # 所有 replacements 的 byte_start/byte_end 都是 dec 的 abs 位置
    new_dec = bytearray()
    # 先拷贝 header (4+mc*4 字节), 后面会重写 msg_offsets
    new_dec += dec[:hs]

    # 在 bytecode 区间 [hs, len(dec)) 上应用替换, 并记录 delta 累计点
    delta_points = []   # [(old_abs_pos_threshold, cumulative_delta_after_this_threshold)]
    cur = hs
    cum_delta = 0
    for (s, e, nb) in replacements:
        if s < cur:
            # 不应发生 (build 时已排序去重)
            continue
        # 拷贝 [cur, s)
        new_dec += dec[cur:s]
        # 写新字节
        new_dec += nb
        # 旧区间长度
        old_len = e - s
        new_len = len(nb)
        cum_delta += (new_len - old_len)
        # "旧 abs >= e 的位置, 累计 delta 变为 cum_delta"
        delta_points.append((e, cum_delta))
        cur = e
    new_dec += dec[cur:]

    def remap_abs(old_abs):
        """把旧绝对偏移映射到新绝对偏移 (累计 delta)"""
        d = 0
        for (threshold, cd) in delta_points:
            if old_abs >= threshold:
                d = cd
            else:
                break
        return old_abs + d

    def remap_rel(old_rel):
        """把旧 rel (=abs-hs) 映射到新 rel"""
        old_abs = old_rel + hs
        new_abs = remap_abs(old_abs)
        return new_abs - hs

    # 1. 修 header msg_offsets
    for i in range(mc):
        old_r = msg_rel[i]
        new_r = remap_rel(old_r)
        struct.pack_into('<I', new_dec, 4 + i * 4, new_r)

    # 2. 修所有 IMM4
    fixed_jumps = 0
    for (old_imm_pos, old_rel_val, op) in jumps:
        new_imm_pos = remap_abs(old_imm_pos)
        new_rel_val = remap_rel(old_rel_val)
        if 0 <= new_imm_pos + 4 <= len(new_dec):
            struct.pack_into('<I', new_dec, new_imm_pos, new_rel_val)
            if new_rel_val != old_rel_val:
                fixed_jumps += 1

    # 压缩回 LZSS (假压缩)
    result = lzss_compress_fake(bytes(new_dec))
    with open(out_path, 'wb') as f:
        f.write(result)

    if verbose:
        diff = len(result) - len(compressed)
        print(f"  {os.path.basename(mes_path)}: "
              f"{len(replacements)} texts, {fixed_jumps}/{len(jumps)} jumps fixed, "
              f"{len(compressed)}→{len(result)} ({'+' if diff >= 0 else ''}{diff})")


def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    src, jsrc = sys.argv[1], sys.argv[2]
    if os.path.isdir(src):
        od = sys.argv[3] if len(sys.argv) > 3 else src + '_patched'
        os.makedirs(od, exist_ok=True)
        for fn in sorted(os.listdir(src)):
            sp = os.path.join(src, fn); op = os.path.join(od, fn)
            if fn.startswith('_') or not fn.upper().endswith('.MES'):
                open(op, 'wb').write(open(sp, 'rb').read()); continue
            jp = os.path.join(jsrc, os.path.splitext(fn)[0] + '.json')
            if not os.path.exists(jp):
                open(op, 'wb').write(open(sp, 'rb').read()); continue
            try:
                inject_file(sp, jp, op)
            except Exception as e:
                print(f"  [ERROR] {fn}: {e}")
                import traceback; traceback.print_exc()
                open(op, 'wb').write(open(sp, 'rb').read())
        print("[完成]")
    else:
        op = sys.argv[3] if len(sys.argv) > 3 else os.path.splitext(src)[0] + '_patched.mes'
        inject_file(src, jsrc, op)


if __name__ == '__main__':
    main()
