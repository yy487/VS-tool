# ScrPlayer 文本提取与注入工具

一个用于 **ScrPlayer 引擎 `.scr` 脚本文件** 的文本提取与非等长注入工具，适用于 Galgame 本地化翻译项目。

------

## ✨ 功能特性

- ✅ 支持 `.scr` 原始文件直接解析（无需预解密）
- ✅ 自动处理 `XOR 0x7F` 文本加密
- ✅ 提取：
  - 对白（角色名 + 内容）
  - 选项文本
- ❌ 不提取：
  - 语音（voice）
  - 立绘（tachi）
  - 背景 / 音效 / BGM 等资源名
- ✅ 支持 **非等长文本注入**
- ✅ 自动修复：
  - `5E` 指令中的 name / message offset
  - `64` 指令中的选项文本 offset
- ✅ JSON 格式友好，适合翻译流程

------

## 📂 支持的文件结构

ScrPlayer `.scr` 文件结构：

```
SCR:2006
[header]
[bytecode]
[text_size]
[text_block (XOR 0x7F)]
```

文本区：

- 编码：CP932（半角片假名）
- 分隔：`\0`
- 加密：逐字节 `^ 0x7F`

------

## 🧩 提取规则

仅提取以下指令相关文本：

### 1. 对白（op_5E）

```
5E 14 [id] [name_offset] [voice_offset] [msg_offset]
```

提取：

```
{
  "id": 0,
  "name": "角色名",
  "message": "对白"
}
```

特殊情况：

- `name_offset = -1` → 旁白 / 内心独白

------

### 2. 选项（op_64）

```
64 0C 00 00 [choice_id] [text_offset]
```

提取：

```
{
  "id": N,
  "name": "",
  "message": "选项文本"
}
```

------

## 🚀 使用方法

### 1. 提取文本

```
python scr_text_tool.py extract input.scr output.json
```

批量：

```
python scr_text_tool.py extract scr_dir json_dir -r
```

------

### 2. 注入文本（非等长）

```
python scr_text_tool.py inject input.scr input.json output.scr
```

批量：

```
python scr_text_tool.py inject scr_dir json_dir out_dir -r
```

------

## 📝 JSON 格式

示例：

```
[
  {
    "id": 0,
    "name": "",
    "message": "キ～ンコ～ンカ～ンコ～ン～♪"
  },
  {
    "id": 1,
    "name": "勝",
    "message": "「……終わんねー終わんねぇ～！！」"
  }
]
```

说明：

| 字段    | 含义             |
| ------- | ---------------- |
| id      | 文本顺序编号     |
| name    | 角色名（可为空） |
| message | 对白或选项文本   |

------

## ⚠️ 注意事项

### 1. 编码问题（重要）

当前工具使用：

```
CP932 编码
```

因此：

- ❌ 不能直接写入简体中文
- ✅ 可用于：
  - 日文修正
  - 同编码替换
- ⚠️ 若需中文：
  - 需配合字体映射 / 编码映射方案（另行实现）

------

### 2. 非等长注入

工具会：

- 重建 text_block
- 自动修正 offset

但前提是：

```
所有文本引用必须来自：
- op_5E
- op_64
```

目前未处理：

- 其他潜在文本引用指令（如系统字符串）

------

