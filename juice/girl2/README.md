# GIRL2 XSD 简化批量工具：现代栈式流式截断版

结构保持最简：

```text
common.py
extract.py
inject.py
```

## JSON 格式

提取结果采用统一模板：

```json
{
  "name": "角色名，可选",
  "scr_msg": "原始脚本文本",
  "msg": "原始脚本文本",
  "_file": "STORY1.XSD",
  "_index": 0
}
```

没有角色名时不输出 `name`。翻译/修改时只改 `msg`，`scr_msg` 用于回注校验。

## 提取

```bat
python extract.py 文本 -o girl2_extract.json
```

提取已解码 bytecode：

```bat
python extract.py decoded_dir --decoded -o girl2_extract.json
```

## 注入

```bat
python inject.py 文本 --json girl2_extract.json -o out_xsd
```

注入已解码 bytecode：

```bat
python inject.py decoded_dir --decoded --json girl2_extract.json -o out_decoded
```

## 当前截断逻辑

这版是“现代栈式/流式固定容量写回 + 分段占位 + 尾部截断”：

1. 一个 JSON `msg` 对应原脚本中连续的多个 `0x10 text 00` 小块。
2. 注入时把 `msg` 当成连续字符流。
3. JSON 里的实际换行 `\n` 只用于编辑阅读；注入时不写入脚本，也不保留空块。
4. 按原脚本每个小块的字节容量，从第一个小块开始一个字符一个字符往里塞。
5. 非最后一个小块默认保留 1 字节空格作为分段占位；前面的块仍然优先吃文本，不会空出来。
6. 如果字符流超过所有小块的可用文本容量，只在整体尾部安全截断。
7. 不拆 CP932 多字节字符；如果当前块剩余 1 字节而下一个字符是 2 字节，会用空格补满当前块，再把字符放入下一块。
8. 脚本里的 `0x17` 等分隔 opcode 原样保留，不新增、不删除、不移动。

也就是说，原来三块：

```text
10 text1 00 17 10 text2 00 17 10 text3 00
```

即使你把 `msg` 中的两个换行删掉，工具也会按三块原始容量依次填充，而不是把第一块写满后让后两块空掉。
