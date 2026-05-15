# AGSI SB2 情况 A 文本提取/注入工具

适用范围：只替换已有正文/选项文本，不修改 CODE.bin，不增删 CSTR index。

## 流程

```bat
python agsi_sb_tool.py unpack majo2.sb dump_majo2 --overwrite
python agsi_cstr_codec.py decode dump_majo2
python agsi_extract.py dump_majo2 majo2_text.json
```

翻译 `message` 字段后：

```bat
python agsi_inject.py dump_majo2 majo2_text.json
python agsi_sb_tool.py pack dump_majo2 majo2_chs.sb
```

如需中文单字映射：

```bat
python agsi_inject.py dump_majo2 majo2_text.json --char-map subs_cn_jp.json
```

## JSON 说明

输出项示例：

```json
{
  "_kind": "choice",
  "_api": "Cmd1$s",
  "_cstr_id": 2660,
  "_code_off": "0x0000ee9b",
  "_push_off": "0x0000ee96",
  "_refs": [
    {
      "_kind": "choice",
      "_api": "Cmd1$s",
      "_select_group": 5,
      "_code_off": "0x0000ee9b",
      "_push_off": "0x0000ee96"
    }
  ],
  "scr_msg": "「ミントと話してると楽しいから、大丈夫」",
  "message": "「ミントと話してると楽しいから、大丈夫」"
}
```

只修改 `message`，不要改 `scr_msg`。

## 关于重复 _cstr_id

同一个 CSTR 字符串可能被 CODE 多处引用。新版 `agsi_extract.py` 默认按 `_cstr_id` 去重，并把所有引用位置放进 `_refs`。

如果使用旧版 JSON，同一个 `_cstr_id` 可能有多条；新版 `agsi_inject.py` 默认在同一 `_cstr_id` 出现不同 `message` 时直接报错，避免旧版的 `last one wins` 静默覆盖。

临时兼容旧 JSON 可使用：

```bat
python agsi_inject.py dump_majo2 old.json --duplicate-policy last
```

但正式翻译不建议这样做。
