#!/usr/bin/env python3
"""AI5WIN v2 MES 文本提取工具 v4 (あしたの雪之丞2)
基于 ai5win_disasm 精确反汇编器.

抓取策略 (以 id=msg 块为单位):
  1. 双字节 TEXT 指令 (op 0x01) 全角文本: 首条如有 0x11 INTERRUPT 紧跟视为名前
  2. CH_POS (op 0x0e) / MENU_SET (op 0x10) / MENU (op 0x15) 的 slot_list 里
     以 cp932 全角开头的 STR slot 视为可翻译菜单项 (选择支/按钮文本)

输出 JSON (GalTransl 兼容):
  [{id, name, message, menu_items?}]

用法:
  python ai5win_mes_extract.py <input.mes>   [output.json]
  python ai5win_mes_extract.py <mes_dir>     [output_dir]   (批量)
"""

import struct, json, sys, os
from ai5win_disasm import lzss_decompress, parse_mes


_RES_EXTS = (b'.g24', b'.msk', b'.ogg', b'.wav', b'.bmp', b'.png',
             b'.mes', b'.ea6', b'.ea5', b'.eav', b'.ttf', b'.fnt')

# 这些开头说明 TEXT 本身是正文/书信/括号内文本，不能按 name 处理。
_NAME_FORBID_PREFIX = ('「', '『', '（', '(', '【', '［', '〔', '〈', '《', '　', ' ')


def _is_user_text(raw):
    """判断 cp932 字节是否可翻译 (非文件名/非纯ASCII参数)."""
    if len(raw) < 2:
        return False
    raw_lower = raw.lower()
    for ext in _RES_EXTS:
        if ext in raw_lower:
            return False
    if not any(0x81 <= b <= 0x9F or 0xE0 <= b <= 0xEF for b in raw):
        return False
    for b in raw:
        if b < 0x20 and b != 0x0A:
            return False
    try:
        raw.decode('cp932')
        return True
    except:
        return False


def _scan_flag_4035_states(ops_by_id_list, dec):
    """按顺序扫所有块, 追踪 B_FLAG id=4035 (对话框模式) 的当前值.
    返回 [value_at_block_entry, ...] 与 ops_by_id_list 等长.
    value=12 → no_name_label 模式 (独白); 其它/None → 正常显示.
    """
    cur = None
    states = []
    for ops in ops_by_id_list:
        states.append(cur)   # 进入本块时的状态
        # 扫本块内是否重设 4035
        for (off, op, args, _) in ops:
            if op != 0x03:
                continue
            id_val = None
            first_expr_range = None
            for a in args:
                if a[0] == 'ID16':
                    id_val = int.from_bytes(a[3], 'little')
                elif a[0] == 'EXPRS' and a[3]:
                    first_expr_range = a[3][0]
            if id_val == 4035 and first_expr_range:
                es, el = first_expr_range
                expr_bytes = bytes(dec[es:es+el])
                # 格式: 'NN ff' 单字节 push, 或 'f1 LO HI ff' u16 push
                if len(expr_bytes) >= 2 and expr_bytes[-1] == 0xFF:
                    body = expr_bytes[:-1]
                    if len(body) == 1 and body[0] < 0x80:
                        cur = body[0]
                    elif len(body) == 3 and body[0] == 0xF1:
                        cur = int.from_bytes(body[1:3], 'little')
    return states


def _collect_block(block_ops, dec=None):
    """从块内 ops 抓文本.
    返回 (segments, choices, chapter_title)

    segments: [{'name': str|None, 'message': str}]

    兼容原则：
      - 保留原有 v8 风格：一个 block 默认仍提取为一个 entry。
      - 已有的“同一角色一句话被多个 TEXT 分段显示”继续合并为同一个 message。
      - 仅当同一 block 内明确出现两组以上 name-like TEXT + 0x11 + body TEXT 时，
        才拆成多个发言 segment。
      - 不向无名前条目输出 name 字段。
    """
    choices = []
    chapter_title = None
    ops = list(block_ops)

    # 标记 CH_POS 后紧跟的 TEXT (选择支文本)
    is_choice_text = []
    for i, (off, op, _, _) in enumerate(ops):
        if op == 0x0e and i + 1 < len(ops) and ops[i + 1][1] == 0x01:
            is_choice_text.append(i + 1)
    is_choice_text_set = set(is_choice_text)

    # 1. 选择支 TEXT (按物理顺序 = CH_POS 出现顺序)
    for i in is_choice_text:
        for (typ, ps, sz, val) in ops[i][2]:
            if typ == 'TEXT' and _is_user_text(val):
                try:
                    s = val.decode('cp932')
                    choices.append(s)
                except Exception:
                    pass
                break

    # 2. 收集非选择支 TEXT
    text_entries = []  # [(op_index, text)]
    for i, (off, op, args, _) in enumerate(ops):
        if op != 0x01 or i in is_choice_text_set:
            continue
        for (typ, ps, sz, val) in args:
            if typ == 'TEXT' and _is_user_text(val):
                try:
                    text_entries.append((i, val.decode('cp932')))
                except Exception:
                    pass
                break

    def _has_name_marker(op_idx, text):
        """判断某个 TEXT 是否是名前 TEXT。

        这个判据沿用原有逻辑：TEXT 后紧跟 0x11，并且在同一显示段内还能遇到
        后续 TEXT 或 MENU_SET 的名前标签。正文开头符号直接排除。
        """
        if not (op_idx + 1 < len(ops) and ops[op_idx + 1][1] == 0x11):
            return False
        if text.startswith(_NAME_FORBID_PREFIX):
            return False
        want = text.encode('cp932', errors='ignore') + b'\n'
        j = op_idx + 2
        while j < len(ops):
            opj = ops[j][1]
            if opj == 0x01:
                return True
            if opj == 0x13:   # NEW_LINE: 第一段显示结束，前面的 TEXT 不是名前
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

    def _body_like(text):
        return bool(text) and text.startswith(('「', '『', '（', '(', '【', '［', '〔', '〈', '《', '　', ' '))

    segments = []

    # 2a. 新增兼容：同一 block 内两组以上 name+body 时才拆成多发言。
    #     只有一组 name+body 时仍走原有逻辑，避免影响普通 block。
    name_positions = []  # text_entries 中的下标
    for n, (op_idx, text) in enumerate(text_entries[:-1]):
        if _has_name_marker(op_idx, text) and _body_like(text_entries[n + 1][1]):
            name_positions.append(n)

    if len(name_positions) >= 2:
        for k, pos in enumerate(name_positions):
            next_pos = name_positions[k + 1] if k + 1 < len(name_positions) else len(text_entries)
            name = text_entries[pos][1]
            msg_parts = [t for _, t in text_entries[pos + 1:next_pos]]
            msg = ''.join(msg_parts)
            if name or msg:
                segments.append({'name': name or None, 'message': msg or ''})
    elif text_entries:
        # 2b. 原有 v8 行为：一个 block 作为一个逻辑 entry。
        first_idx, first_text = text_entries[0]
        has_name_marker = _has_name_marker(first_idx, first_text)
        if has_name_marker and len(text_entries) >= 2:
            name = first_text
            message = ''.join(t for _, t in text_entries[1:])
            segments.append({'name': name, 'message': message})
        elif has_name_marker:
            segments.append({'name': first_text, 'message': ''})
        else:
            message = ''.join(t for _, t in text_entries)
            segments.append({'name': None, 'message': message})

    # 3. 章节标题 (过滤含 \n 的名前标签)
    for (off, op, args, _) in ops:
        if op != 0x10:
            continue
        for (typ, ps, sz, val) in args:
            if typ != 'SLOTS':
                continue
            for sl in val:
                if sl[0] != 'STR':
                    continue
                if not _is_user_text(sl[3]):
                    continue
                if b'\n' in sl[3] or b'\\n' in sl[3]:
                    continue   # 过滤名前立绘标签
                try:
                    chapter_title = sl[3].decode('cp932')
                except Exception:
                    pass

    return segments, choices, chapter_title



def extract_file(mes_path, json_path, verbose=True):
    compressed = open(mes_path, 'rb').read()
    try:
        dec = lzss_decompress(compressed)
    except Exception as e:
        if verbose:
            print(f"  {os.path.basename(mes_path)}: LZSS 解压失败: {e}")
        return 0

    if len(dec) < 4:
        if verbose:
            print(f"  {os.path.basename(mes_path)}: 空文件")
        return 0

    mc, hs, msg_rel, msg_abs, lines = parse_mes(dec)
    if mc == 0:
        if verbose:
            print(f"  {os.path.basename(mes_path)}: mc=0, 跳过")
        return 0

    # 按 id 分组 ops. 前导区 [hs, ma[0]) 归 id=-1 (序章/文件初始化段)
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

    # 全局追踪 B_FLAG 4035 (对话框模式) 状态
    # 顺序: [prelude, id=0, id=1, ..., id=mc-1]
    all_block_ops = [prelude_ops] + ops_by_id
    flag_states = _scan_flag_4035_states(all_block_ops, dec)
    # 块入口状态 -> no_name_label (12 是独白模式)
    # 但 _collect_block 只看本块是否重设, 如果本块重设了就应该用重设后的值
    # 所以真正判据: "本块离开时状态 == 12" → no_name_label
    # 更精确地: "在本块的消息显示时, 4035 是否为 12"?
    # 保守做法: 块入口状态 OR 本块内任何消息显示前的重设值
    # 简单: 块出口状态 (最接近消息实际生效时的值)
    # flag_states 给的是入口状态; 出口状态 = 下一个块的入口状态
    def exit_state(idx):
        if idx + 1 < len(flag_states):
            return flag_states[idx + 1]
        # 最后一块: 再扫一遍本块得最终值
        cur = flag_states[idx]
        for (off, op, args, _) in all_block_ops[idx]:
            if op != 0x03: continue
            id_val = None; er = None
            for a in args:
                if a[0] == 'ID16': id_val = int.from_bytes(a[3], 'little')
                elif a[0] == 'EXPRS' and a[3]: er = a[3][0]
            if id_val == 4035 and er:
                eb = bytes(dec[er[0]:er[0]+er[1]])
                if len(eb) >= 2 and eb[-1] == 0xFF:
                    body = eb[:-1]
                    if len(body) == 1 and body[0] < 0x80: cur = body[0]
                    elif len(body) == 3 and body[0] == 0xF1:
                        cur = int.from_bytes(body[1:3], 'little')
        return cur

    entries = []

    def emit_block(list_idx, block_id, ops):
        """把一个块的 name/message/choices/chapter_title 展开为翻译 JSON entry。

        输出格式保持原有干净格式：
          - 有角色名：id, name, scr_msg, message
          - 无角色名：id, scr_msg, message
          - 选择/章节只额外保留 is_choice/choice_idx 或 is_chapter_title
        """
        segments, chs, ct = _collect_block(ops, dec)

        # 1. 正文台词。多发言 block 会产生多个同 id entry，顺序即脚本物理顺序。
        for seg in segments:
            nm = seg.get('name')
            msg = seg.get('message') or ''
            if not nm and not msg:
                continue
            if nm:
                ent = {"id": block_id, "name": nm, "scr_msg": msg, "message": msg}
            else:
                ent = {"id": block_id, "scr_msg": msg, "message": msg}
            entries.append(ent)

        # 2. 每个选择支作为独立 entry，保留选项标记符，不输出空 name。
        for idx, c in enumerate(chs):
            entries.append({
                "id": block_id, "scr_msg": c, "message": c,
                "is_choice": True, "choice_idx": idx,
            })

        # 3. 章节标题作为独立 entry，保留章节标记符，不输出空 name。
        if ct:
            entries.append({
                "id": block_id, "scr_msg": ct, "message": ct,
                "is_chapter_title": True,
            })

    # 前导区: list_idx=0, block_id=-1
    if prelude_ops:
        emit_block(0, -1, prelude_ops)

    for i in range(mc):
        emit_block(i + 1, i, ops_by_id[i])

    if not entries:
        if verbose:
            print(f"  {os.path.basename(mes_path)}: 无文本")
        return 0

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    nmsg = sum(1 for e in entries if not e.get("is_choice") and not e.get("is_chapter_title"))
    nchoice = sum(1 for e in entries if e.get("is_choice"))
    nchap = sum(1 for e in entries if e.get("is_chapter_title"))
    if verbose:
        print(f"  {os.path.basename(mes_path)}: "
              f"{len(compressed)}→{len(dec)}B, "
              f"{len(entries)} entries ({nmsg} msg, {nchoice} choices, {nchap} titles)")
    return len(entries)


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    src = sys.argv[1]
    if os.path.isdir(src):
        out = sys.argv[2] if len(sys.argv) > 2 else src + '_json'
        os.makedirs(out, exist_ok=True)
        files = sorted(f for f in os.listdir(src)
                       if f.upper().endswith('.MES') and not f.startswith('_'))
        total = 0
        for fn in files:
            jp = os.path.join(out, os.path.splitext(fn)[0] + '.json')
            total += extract_file(os.path.join(src, fn), jp)
        print(f"[完成] {len(files)} 文件, {total} entries")
    else:
        jp = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + '.json'
        extract_file(src, jp)


if __name__ == '__main__':
    main()
