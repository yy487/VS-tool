# Angel / Silky EVIT SNC 文本提取注入工具

## 文件

- `snc_common.py`：共用结构解析、字符串池重建、`st` 引用重写模板。
- `snc_extract.py`：从已解压的 `EVIT` `.snc` 中提取文本。
- `snc_inject.py`：按 JSON 非等长注入，重建字符串池并更新 header/code 引用。

这些工具处理的是 **IFL 解包并正确解压 CMP_ 后的 `.snc`**，即文件头应为 `EVIT`。

## 提取

单文件：

```bat
python snc_extract.py ANSNC_unpacked_engine\yuz31.snc json\yuz31.json --pretty
```

批量：

```bat
python snc_extract.py ANSNC_unpacked_engine json --pretty
```

输出格式：

```json
{
  "_file": "yuz31.snc",
  "_index": 6,
  "_kind": "message",
  "_ref": 4255,
  "_code_word": 15443,
  "name": "美蔓",
  "scr_msg": "「じゃあふたりとも、ナルちゃんをあまり待たせても悪いし、はい、行ってらっしゃい」",
  "message": "「じゃあふたりとも、ナルちゃんをあまり待たせても悪いし、はい、行ってらっしゃい」"
}
```

旁白没有 `name` 字段。

选项格式：

```json
{
  "_file": "suz13.snc",
  "_index": 10,
  "_kind": "choice",
  "_code_word": 12345,
  "_var": 10,
  "choices": [
    {
      "index": 0,
      "_ref": 2712,
      "scr_msg": "ほんとは一緒に入りたい",
      "message": "ほんとは一緒に入りたい"
    }
  ]
}
```

## 注入

单文件：

```bat
python snc_inject.py ANSNC_unpacked_engine\yuz31.snc json\yuz31.json out_snc\yuz31.snc
```

批量：

```bat
python snc_inject.py ANSNC_unpacked_engine json out_snc
```

默认使用 `cp932` 严格编码。若译文含有 CP932 不可编码字符，工具会报错。这是正确行为：中文最终应先经过字体映射 / CnJpMap 后再注入，或者使用已经能被游戏编码接受的文本。

只做校验不写文件：

```bat
python snc_inject.py ANSNC_unpacked_engine json out_snc --dry-run
```

关闭 `scr_msg` 校验：

```bat
python snc_inject.py ANSNC_unpacked_engine json out_snc --no-strict-scr-msg
```

## 非等长注入原理

1. 读取 header。
2. 解析旧字符串池。
3. 用 JSON 中的 `message` 替换目标 `_ref`。
4. 重建字符串池。
5. 建立 `old_ref -> new_ref` 映射。
6. 扫描 VM code 区所有 `st <ref>`，改成新 ref。
7. 更新 `vl_base / ef_base / code_start / file_size`。

`vl/ef` label 表保存的是相对 code 的跳转信息，因此字符串池变长时不需要修改 label 表内部内容，只要整体平移 header 基址。


## v5 notes

- `name` is now treated as the target speaker name during injection. Translating speaker names such as `ゆず` -> `柚子` is allowed by default.
- Strict validation still checks `scr_msg` against the original message text. This remains the main safety anchor.
- Use `--strict-name` only when you intentionally want to forbid speaker-name translation.
- Fallback extraction is disabled by default because it can over-extract string-pool candidates. Use `--fallback` only for investigation.
- `split_name_msg` now only splits `name\nmessage` when the message starts with `「` or `『`, preventing narration line breaks from being treated as names.
