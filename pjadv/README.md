# rxpjadv_python_tool

这是一个将 **RxPJADV** 核心逻辑改写为 Python 的本地化辅助工具，主要处理 PJADV/RxPJADV 系脚本资源：

- `GAMEDAT PAC2` 封包解包与重打包；
- `scenario.dat + textdata.bin/dat` 文本提取；
- 追加式非等长文本注入；
- `textdata.bin/dat` XOR 加解密；
- `filename.dat` 文件名表查看。

## 源码来源与声明

本工具的结构分析与核心处理逻辑来源于开源仓库：

```text
https://github.com/ZQF-ReVN/RxPJADV
```

原项目为 C++ 实现。本工具在 Python 中复刻并整理了其中的核心思路，包括：

- `GAMEDAT PAC2` 封包格式；
- `PJADV_SF0001` 的 `scenario.dat` 指令扫描方式；
- `PJADV_TF0001` 的 `textdata.bin/dat` 文本池结构；
- 文本 opcode 与 textdata offset 的对应关系；
- 通过追加新文本并回写 scenario offset 实现非等长注入；
- textdata XOR 处理逻辑。

本 Python 版不是原仓库的逐行翻译，而是面向本地化工作流重新整理后的版本。

## 文件结构

```text
rxpjadv_python_tool/
  README.md
  rxpjadv.py                  # 命令行入口
  rxpjadv_py/
    __init__.py
    common.py                 # 通用二进制/编码辅助函数
    pack_v2.py                # GAMEDAT PAC2 解包/封包
    scenario.py               # scenario.dat 指令流扫描与 patch
    textdata.py               # textdata 读取、追加、保存、XOR
    text_manager.py           # 文本导出/导入主逻辑
    filename_dat.py           # filename.dat 文件名表读取
```

## 环境要求

只依赖 Python 标准库。

推荐：

```bash
python 3.10+
```

## 1. 封包处理：GAMEDAT PAC2

### 查看封包文件表

```bash
python rxpjadv.py pack-list archive.dat
```

输出示例：

```text
0000  off=0x00000000  size=   12345  scenario.dat
0001  off=0x00003039  size=   67890  textdata.bin
```

### 解包

```bash
python rxpjadv.py unpack archive.dat archive/
```

说明：

- 封包签名应为 `GAMEDAT PAC2`；
- 文件名表每项 32 字节；
- 文件信息表每项 8 字节，结构为 `offset + size`；
- `offset` 是相对数据区开头的偏移，不是相对整个封包文件开头。

### 重打包

```bash
python rxpjadv.py pack archive/ archive_new.dat
```

默认会按相对路径字典序打包。

如果需要固定文件顺序，可以使用 manifest：

```bash
python rxpjadv.py pack archive/ archive_new.dat --manifest manifest.txt
```

`manifest.txt` 示例：

```text
scenario.dat
textdata.bin
filename.dat
```

注意：

- 文件名表单项长度是 32 字节；
- 为了保留 NUL 结尾，本工具限制文件名编码后最大 31 字节；
- 当前默认按 ASCII 写入封包文件名，如果实际游戏使用非 ASCII 文件名，需要按样本修改编码策略。

## 2. 文本提取

### 标准 JSON 格式导出

```bash
python rxpjadv.py text-export textdata.bin scenario.dat scenario.json
```

默认编码为 `cp932`。也可以显式指定：

```bash
python rxpjadv.py text-export textdata.bin scenario.dat scenario.json --encoding cp932
```

导出的 JSON 使用本地化工作流格式：

```json
{
  "_file": "scenario.dat",
  "_index": 123,
  "_cmd_offset": 4096,
  "_op": "0x80000307",
  "_kind": "message",
  "_name_offset": 4660,
  "_msg_offset": 22136,
  "name": "キャラ名",
  "scr_msg": "原文",
  "msg": "原文"
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `_file` | scenario 文件名 |
| `_index` | scenario 指令序号 |
| `_cmd_offset` | 指令在 scenario.dat 中的文件偏移 |
| `_op` | opcode |
| `_kind` | 文本类型：`message`、`select`、`chapter`、`comment` |
| `_name_offset` | 角色名在 textdata 中的 offset，仅对话文本可能存在 |
| `_msg_offset` | 正文/选项/章节/注释在 textdata 中的 offset |
| `name` | 角色名，可选 |
| `scr_msg` | 原始脚本文本，用于注入校验，不应修改 |
| `msg` | 待翻译/待注入文本，初始等于 `scr_msg` |

### 支持的文本 opcode

| opcode | 类型 | 参数位置 |
|---|---|---|
| `0x80000406` | 对话文本 | `word[2] = name offset`, `word[3] = msg offset` |
| `0x80000307` | 对话文本 | `word[2] = name offset`, `word[3] = msg offset` |
| `0x01010203` | 选项文本 | `word[1] = msg offset` |
| `0x01010804` | 选项文本 | `word[1] = msg offset` |
| `0x01000D02` | 章节文本 | `word[1] = msg offset` |
| `0x03000303` | 存档/读档注释 | `word[2] = msg offset` |

### 兼容原 RxPJADV 的 msg/seq 双 JSON 格式

如果需要导出成原项目风格：

```bash
python rxpjadv.py text-export-legacy textdata.bin scenario.dat scenario_msg.json scenario_seq.json
```

原风格会生成两个文件：

```text
scenario_msg.json    文本内容
scenario_seq.json    对应 scenario 指令序号
```

示例：

```json
{
  "chr_org": "角色名",
  "chr_tra": "角色名",
  "msg_org": "原文",
  "msg_tra": "原文"
}
```

## 3. 文本注入

### 标准 JSON 注入

编辑导出的 `scenario.json`，只修改 `msg` 字段。

然后执行：

```bash
python rxpjadv.py text-import textdata.bin scenario.dat scenario.json \
  --out-textdata textdata.bin.new \
  --out-scenario scenario.dat.new
```

注入逻辑：

1. 读取原 `textdata.bin/dat`；
2. 读取原 `scenario.dat`；
3. 使用 `_index` 定位 scenario 指令；
4. 校验 `_op` 是否一致；
5. 读取当前 textdata offset 的文本，校验是否等于 `scr_msg`；
6. 将 `msg` 编码为 CP932；
7. 把新文本追加到 textdata 尾部；
8. 回写 scenario 指令中的 textdata offset；
9. 输出新 `textdata` 和新 `scenario`。

这是追加式非等长注入，不会覆盖原文本区域。

### 角色名是否注入

默认只注入 `msg`，不会修改 `name`。

如果确实需要注入角色名：

```bash
python rxpjadv.py text-import textdata.bin scenario.dat scenario.json \
  --out-textdata textdata.bin.new \
  --out-scenario scenario.dat.new \
  --update-name
```

一般不建议翻译角色名，除非你明确确认引擎显示逻辑与文本池引用关系没有副作用。

### 非严格模式

默认严格校验，任何一条 `_op` 或 `scr_msg` 不匹配都会报错中止。

如果希望跳过失败项：

```bash
python rxpjadv.py text-import textdata.bin scenario.dat scenario.json --no-strict
```

### 兼容原 RxPJADV 双 JSON 注入

```bash
python rxpjadv.py text-import-legacy textdata.bin scenario.dat scenario_msg.json scenario_seq.json \
  --out-textdata textdata.bin.new \
  --out-scenario scenario.dat.new
```

## 4. textdata 结构与查看

`textdata.bin/dat` 明文结构：

```text
PJADV_TF0001      12 bytes
text_count        uint32 little-endian
text_0            C string
00 00
text_1            C string
00 00
...
```

导出 textdata 全量 JSON 仅用于检查：

```bash
python rxpjadv.py textdata-json textdata.bin textdata.json
```

## 5. textdata XOR 加解密

原项目里的 XOR 逻辑为：

```text
byte ^= key
key += 0x5C
```

命令：

```bash
python rxpjadv.py xor textdata.bin textdata.dec 0x12
```

同一算法再次执行可以反向处理，前提是初始 key 一致：

```bash
python rxpjadv.py xor textdata.dec textdata.enc 0x12
```

注意：

- key 需要结合具体游戏样本确认；
- `text-export` 和 `text-import` 要求输入的 textdata 是 `PJADV_TF0001` 明文；
- 如果原始文件是加密的，应先 XOR 解密，再提取/注入，最后 XOR 加密回去。

## 6. filename.dat 查看

```bash
python rxpjadv.py filename-list filename.dat
```

默认按 CP932 解码：

```bash
python rxpjadv.py filename-list filename.dat --encoding cp932
```

`filename.dat` 结构：

```text
PJADV_FL0001      12 bytes
name_count        uint32 little-endian
filename[32] * name_count
```

某些 scenario opcode 里的资源参数可能不是直接文件名，而是 `filename.dat` 的 index。

## 7. 推荐完整工作流

如果游戏资源在 `archive.dat` 中：

```bash
# 1. 解包
python rxpjadv.py unpack archive.dat archive/

# 2. 如果 textdata 加密，先解密；key 需要自行确认
python rxpjadv.py xor archive/textdata.bin archive/textdata.dec 0x12

# 3. 提取文本
python rxpjadv.py text-export archive/textdata.dec archive/scenario.dat scenario.json

# 4. 翻译，只修改 scenario.json 里的 msg 字段

# 5. 注入
python rxpjadv.py text-import archive/textdata.dec archive/scenario.dat scenario.json \
  --out-textdata archive/textdata.new \
  --out-scenario archive/scenario.new

# 6. 如果需要，加密回 textdata.bin
python rxpjadv.py xor archive/textdata.new archive/textdata.bin 0x12

# 7. 替换 scenario.dat
cp archive/scenario.new archive/scenario.dat

# 8. 重打包
python rxpjadv.py pack archive/ archive_new.dat
```

如果 textdata 本身就是明文，则跳过 XOR 步骤。

## 8. 与原 C++ 项目的差异

1. Python 版默认输出统一 JSON：`name`、`scr_msg`、`msg`，并附带 `_index`、`_op`、`_offset` 等定位信息。
2. 注入时会校验 `scr_msg`，避免 JSON 与脚本错配导致误注入。
3. 保留了原项目的 legacy msg/seq 双 JSON 导入导出命令。
4. 封包重打包默认按路径排序，必要时可通过 manifest 固定顺序。
5. 文件名长度检查修正为最大 31 字节，以避免 32 字节文件名写 NUL 时越界。

## 9. 注意事项

- 当前工具针对已知 PJADV/RxPJADV 结构编写，不保证适配所有 PJADV 变体。
- 如果 `scenario.dat` 的 opcode 结构和上述表不同，需要先补 opcode 表。
- 如果翻译文本含简体中文，直接 CP932 编码通常会失败；需要先替换为映射字符。
- 如果游戏存在散文件优先读取机制，可以直接替换解包目录中的 `scenario.dat` / `textdata.bin`；否则需要重打包。
- 如果封包内部文件名不是 ASCII，需要修改 `pack_v2.py` 中的文件名编码策略。
