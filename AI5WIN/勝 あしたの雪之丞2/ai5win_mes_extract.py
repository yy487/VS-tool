#!/usr/bin/env python3
"""AI5WIN v2 MES 文本提取工具 v4 (あしたの雪之丞2)
基于 ai5win_disasm 精确反汇编器.

抓取策略 (以 id=msg 块为单位):
  1. 双字节 TEXT 指令 (op 0x01) 全角文本: 首条如有 0x11 INTERRUPT 紧跟视为名前
  2. CH_POS (op 0x0e) / MENU_SET (op 0x10) / MENU (op 0x15) 的 slot_list 里
     以 cp932 全角开头的 STR slot 视为可翻译菜单项 (选择支/按钮文本)

输出 JSON:
  [{id, name?, scr_msg, message, is_choice?/is_chapter_title?}]

用法:
  python ai5win_mes_extract.py <input.mes>   [output.json]
  python ai5win_mes_extract.py <mes_dir>     [output_dir]   (批量)
"""

import struct, json, sys, os
from ai5win_disasm import lzss_decompress, parse_mes


_RES_EXTS = (b'.g24', b'.msk', b'.ogg', b'.wav', b'.bmp', b'.png',
             b'.mes', b'.ea6', b'.ea5', b'.eav', b'.ttf', b'.fnt')

# 这些开头说明首个 TEXT 本身就是正文/书信/括号内文本，不能按 name 处理。
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



def _body_like_text(s):
    return s.startswith(_NAME_FORBID_PREFIX)


def _text_arg_from_op(op_item):
    for (typ, ps, sz, val) in op_item[2]:
        if typ == 'TEXT' and _is_user_text(val):
            try:
                return ps, sz, val, val.decode('cp932')
            except:
                return None
    return None


def _is_name_start(ops, text_entries, pos):
    """判断 text_entries[pos] 是否是一个 name TEXT 的起点。

    只用于新增的同 block 多发言识别。判据保持保守：
      1. 该 TEXT 后紧跟 0x11 INTERRUPT；
      2. 自身不是正文样式开头；
      3. 后一条 TEXT 存在，且后一条看起来像正文。
    """
    op_idx, text = text_entries[pos][0], text_entries[pos][1]
    if not (op_idx + 1 < len(ops) and ops[op_idx + 1][1] == 0x11):
        return False
    if _body_like_text(text):
        return False
    if pos + 1 >= len(text_entries):
        return False
    next_text = text_entries[pos + 1][1]
    if not _body_like_text(next_text):
        return False
    return True


def _collect_speaker_segments(ops, text_entries):
    """返回同一 block 内的多发言段。

    只有出现两个及以上 name+body 起点时才启用。
    普通 name+message、同一角色多 TEXT 分段，仍交给原有逻辑处理。
    返回: [(name, message, name_text_index, msg_text_indices)] 或 []。
    """
    starts = [i for i in range(len(text_entries)) if _is_name_start(ops, text_entries, i)]
    if len(starts) < 2:
        return []
    segments = []
    for si, start_pos in enumerate(starts):
        next_start = starts[si + 1] if si + 1 < len(starts) else len(text_entries)
        name = text_entries[start_pos][1]
        msg_positions = list(range(start_pos + 1, next_start))
        if not msg_positions:
            continue
        msg = ''.join(text_entries[p][1] for p in msg_positions)
        msg_text_indices = [text_entries[p][0] for p in msg_positions]
        segments.append((name, msg, text_entries[start_pos][0], msg_text_indices))
    return segments


def _collect_block(block_ops, dec=None):
    """从块内 ops 抓文本.
    返回 (name, message, choices, chapter_title)

    dec: 保留参数仅为兼容旧调用，不参与显示模式推断。

    识别规则:
      - "选择支 TEXT" (choices):
          CH_POS (op 0x0e) 后紧跟的 TEXT (op 0x01). 例: '●水島を刺激する...'
      - "章节标题" (chapter_title):
          独立的 MENU_SET (op 0x10) 里的合法 STR slot (过滤含 \n 的名前标签).
      - "名前 + 台词":
          块首 TEXT 后紧跟 0x11 INTERRUPT: 首 TEXT=name, 下一个非选择支 TEXT=message.
          否则块首 TEXT 就是 message.
    """
    name = None
    message = None
    choices = []
    chapter_title = None
    ops = list(block_ops)

    # 标记 CH_POS 后紧跟的 TEXT (选择支文本)
    is_choice_text = []
    for i, (off, op, _, _) in enumerate(ops):
        if op == 0x0e and i + 1 < len(ops) and ops[i + 1][1] == 0x01:
            is_choice_text.append(i + 1)
    is_choice_text_set = set(is_choice_text)

    # 1. 选择支 TEXT
    for i in is_choice_text:
        text_arg = _text_arg_from_op(ops[i])
        if text_arg:
            choices.append(text_arg[3])

    # 2. 名前 + 台词 / 无名前正文
    text_entries = []
    for i, item in enumerate(ops):
        if item[1] != 0x01 or i in is_choice_text_set:
            continue
        text_arg = _text_arg_from_op(item)
        if text_arg:
            text_entries.append((i, text_arg[3]))

    def _has_name_marker(first_idx, first_text):
        if not (first_idx + 1 < len(ops) and ops[first_idx + 1][1] == 0x11):
            return False
        if first_text.startswith(_NAME_FORBID_PREFIX):
            return False
        want = first_text.encode('cp932', errors='ignore') + b'\\n'
        j = first_idx + 2
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
                        if sl[0] == 'STR' and (sl[3] == want or sl[3] == first_text.encode('cp932', errors='ignore') + b'\n'):
                            return True
            j += 1
        return False

    if text_entries:
        first_idx, first_text = text_entries[0]
        has_name_marker = _has_name_marker(first_idx, first_text)
        if has_name_marker and len(text_entries) >= 2:
            name = first_text
            message = ''.join(t for _, t in text_entries[1:])
        elif has_name_marker:
            name = first_text
        else:
            message = ''.join(t for _, t in text_entries)

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
                    continue
                try:
                    chapter_title = sl[3].decode('cp932')
                except:
                    pass

    return name, message, choices, chapter_title


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

    entries = []

    def emit_block(list_idx, block_id, ops):
        """把一个块的 name/message/choices/chapter_title 全部展开为独立 entry"""
        ops_l = list(ops)

        # 新增兼容：同一个 block 内出现两组以上 name+body 时，拆成多条 entry。
        # 其它块仍完全沿用原有 _collect_block 逻辑，避免改变既有格式和识别结果。
        is_choice_text = []
        for i, (off, op, _, _) in enumerate(ops_l):
            if op == 0x0e and i + 1 < len(ops_l) and ops_l[i + 1][1] == 0x01:
                is_choice_text.append(i + 1)
        is_choice_text_set = set(is_choice_text)
        text_entries = []
        for i, item in enumerate(ops_l):
            if item[1] != 0x01 or i in is_choice_text_set:
                continue
            text_arg = _text_arg_from_op(item)
            if text_arg:
                text_entries.append((i, text_arg[3]))
        segments = _collect_speaker_segments(ops_l, text_entries)

        if segments:
            for nm, msg, _name_idx, _msg_indices in segments:
                entries.append({"id": block_id, "name": nm, "scr_msg": msg or "", "message": msg or ""})
            # 多发言块仍保留选择支/章节标题的旧逻辑，虽然正常不会同时出现。
            _, _, chs, ct = _collect_block(ops, dec)
        else:
            nm, msg, chs, ct = _collect_block(ops, dec)
            # 1. 正文台词：统一输出 scr_msg/message；name 为空时不输出 name 字段
            if nm or msg:
                ent = {"id": block_id, "scr_msg": msg or "", "message": msg or ""}
                if nm:
                    ent = {"id": block_id, "name": nm, "scr_msg": msg or "", "message": msg or ""}
                entries.append(ent)

        # 2. 每个选择支作为独立 entry，保留选项标记符
        for idx, c in enumerate(chs):
            entries.append({
                "id": block_id, "scr_msg": c, "message": c,
                "is_choice": True, "choice_idx": idx,
            })
        # 3. 章节标题作为独立 entry，保留章节标记符
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
