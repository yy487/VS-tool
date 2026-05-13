# Angel/Silky EVIT SNC 文本提取注入工具（指令流人名识别版）

## 这版修正点

本版不再用“第一行 + `\\n` + 后文是否以 `「/『` 开头”这种纯文本规则直接判断人名。

新的规则是：

1. 扫描 VM code 中实际显示用的 `st <ref> <display-op>` 引用。
2. 如果显示文本前方精确匹配：

   ```text
   0060 st <voice_ref> 0030 <line_id> st <text_ref> 0061
   ```

   且 `<voice_ref>` 指向 `Vxx` 语音资源，则该文本允许拆成 `name + message`。

3. 对没有 voice 的文本，只有第一行属于从指令流中收集到的“已知角色名表”时才允许拆 name。
   这样可以覆盖主角 `進矢` 这类无语音对白，同时避免把旁白里的引用句误判成人名。

4. `message` 内部的 `\\n` 全部删除，交给游戏自动换行。
   `name` 和 `message` 之间的 `\\n` 不写入 JSON，注入时自动补回。

## 文件

```text
snc_common.py   共用 EVIT 解析、字符串池重建、文本规范化函数
snc_extract.py  提取工具
snc_inject.py   非等长注入工具
```

## 提取

```bat
python snc_extract.py ANSNC_engine json_out --pretty
```

单文件：

```bat
python snc_extract.py ANSNC_engine\kyo04.snc kyo04.json --pretty
```

## 注入

```bat
python snc_inject.py ANSNC_engine json_out out_snc
```

只检查：

```bat
python snc_inject.py ANSNC_engine json_out out_snc --dry-run
```

## JSON 格式

有角色名：

```json
{
  "name": "進矢",
  "scr_msg": "「おまえ、カフェーの店員がそういうこと言っていいのか？」",
  "message": "「おまえ、カフェーの店員がそういうこと言っていいのか？」"
}
```

旁白：

```json
{
  "scr_msg": "俺がプランタンで働くことを知った美蔓さんは、『茜ちゃんが居候してるなら、あなたもしなさい』と、いきなり言い出したのだ。",
  "message": "俺がプランタンで働くことを知った美蔓さんは、『茜ちゃんが居候してるなら、あなたもしなさい』と、いきなり言い出したのだ。"
}
```

注入时：

- `scr_msg` 用于原文校验；
- `message` 是目标正文；
- `name` 是目标角色名，可以翻译；
- 若存在 `name`，注入器会自动写回 `name\\nmessage`；
- 若没有 `name`，注入器只写回 `message`。
