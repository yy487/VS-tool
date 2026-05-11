# Sweet SCB 非等长文本提取/注入工具 v5

## 说明

本工具用于 Sweet 引擎 `.SCB` 脚本的文本提取与非等长注入。

v5 修正了 name/message 配对规则：

- `#N1`、`#N2` 是名字占位符，不是控制指令。
- `#L` 前面的字段就是当前对话头的 name。
- 不再继承上一条 name。
- `#N2#L` 会提取为 `"name": "#N2"`。
- `栄美子#L` 会提取为 `"name": "栄美子"`。
- `栄美子・#N2#L` 会提取为 `"name": "栄美子・#N2"`。
- 正文中的续行 `...#L` 仍会合并进同一条 message，不会误判为 name。

SCB 基本对话结构：

```text
00 00 <name-or-name-macro>#L 00 FF FF 00 00 <body>#W#P 00 FF FF
```

其中 `23 4C 00 FF FF 00 00` 即 `#L\0 FF FF 00 00` 后接正文 TEXT 命令。

## 提取

批量：

```bat
python scb_extract.py script json
```

单文件：

```bat
python scb_extract.py script\EM007.SCB json\EM007.json
```

## 注入

批量：

```bat
python scb_inject.py script json out
```

单文件：

```bat
python scb_inject.py script\EM007.SCB json\EM007.json out\EM007.SCB
```

## JSON 格式

每个 `.SCB` 对应一个同名 `.json`。

```json
{
  "id": 0,
  "type": "message",
  "name": "#N2",
  "scr_msg": "「やあ、栄美子ちゃん」",
  "message": "「やあ、栄美子ちゃん」",
  "_offset": 22,
  "_msg_offset": 32,
  "_body_offsets": [32]
}
```

选项：

```json
{
  "id": 1,
  "type": "choice",
  "scr_msg": "▼遊ぶ",
  "message": "▼遊ぶ",
  "_offset": 4567
}
```

注入时以 `scr_msg` 做原文校验，只替换 `message`。

## 注意

默认写回编码为 CP932。若 `message` 含 CP932 不能编码的汉字，需要先接字符替换/字体映射流程。


## v6 name 规则

提取器不做跨选项、跨分支的 name 继承，只按当前指令流中的对话头判断：

```text
00 00 <header>#L 00 FF FF 00 00 <body>#W#P 00 FF FF
```

规则：

```text
栄美子#L          -> name = 栄美子
栄美子・#N2#L     -> name = 栄美子・#N2
#N1#L / #N2#L     -> 不输出 name 字段
```

`#N1/#N2` 仍会被当成合法对话头用于定位正文，但纯宏头不会写入 JSON 的 `name`，避免选项分支后的上下文 name 误配。


## v7 修正

- `#N1/#N2` 不再被隐藏，作为名字占位符正常输出到 `name`。
- 选项 `type=choice` 仍然不做 `name` 继承。
- 去掉对 `name` 的标点过滤，按指令流结构 `00 00 <name>#L 00 FF FF 00 00 <message>#W#P 00 FF FF` 判断，所以 `？？#L` 这类匿名角色名不会再漏提。
- 原样提取再注入测试：453 个 SCB，diff=0。
