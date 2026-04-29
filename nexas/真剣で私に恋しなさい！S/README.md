# NeXAS / 真剣演舞 引擎汉化工具链

针对「真剣で私に恋しなさい！S」（みなとそふと）的完整汉化工具，融合开源工具的引擎语义理解 + 自家结构分析。

## 🎯 工具链概览（三步流程）

```
mes/*.bin                          mes_zh/*.bin
    │                                   ▲
    │ nexas_extract.py                  │ nexas_inject.py
    ▼                                   │
text_jp/*.json   ──翻译──►   text_zh/*.json
                (GalTransl / 人工)
```

外加 `nexas_disasm.py` 用于深度逆向分析。

## 📦 工具清单

| 文件 | 作用 |
|---|---|
| `nexas_common.py` | 通用解析模块（被三工具复用，不直接运行）|
| `nexas_disasm.py` | 反汇编器 → `.asm` 文件 |
| `nexas_extract.py` | 文本提取 → `.json` 文件 |
| `nexas_inject.py` | 文本注入 ← `.json` + 原 `.bin` |

四个文件放同一目录，三个工具会自动 `import nexas_common`。

## 🚀 快速开始

```bash
# 步骤1: 提取所有日文文本到 .json
python nexas_extract.py mes\ -o text_jp\

# 步骤2: 翻译 - 修改 .json 里 "strings" 数组的对应槽位
#         (GalTransl 自动翻译 / 人工翻译，得到 text_zh\)

# 步骤3: 注入 (中文用 GBK 编码)
python nexas_inject.py mes\ --json text_zh\ -o mes_zh\ --encoding gbk

# 可选: 深度分析用反汇编
python nexas_disasm.py mes\ -o disasm\
```

## ✅ Round-trip 验证

**258 / 258 文件 MD5 完全匹配**：extract 后不修改直接 inject，输出与原文件 100% 字节相同。

```bash
python nexas_extract.py mes\ -o /tmp/jp\
python nexas_inject.py mes\ --json /tmp/jp\ -o /tmp/rebuilt\
# 比对 mes\ 和 /tmp/rebuilt\ 的 MD5 应全部一致
```

## 🔧 引擎机制

### 文件格式
```
[u32 magic]                  = 0x11D3 (剧本) / 0x11E7 (system.bin)
                                同时也作为 extras 数量
[extras  magic × 8B]         = 含 reserved 8B + 变量声明
[u32 commands_count]
[commands  count × 8B]       = 主体, op=0 是参数前缀累积器
[u32 strings_count]
[strings × cp932 NUL分隔]    = 故事文本/资源名 (注入目标)
[trailer]                    = 变量名表等, 注入时原样保留
```

### Opcode 助记符表（融合开源工具）

| op | 助记符 | 含义 |
|----|--------|------|
| 0x00 | (前缀累积器) | **不是独立指令**，arg 累积到下一个非 0 op 的 prefix 数组 |
| 0x05 + data=1 | **LOAD_STRING** | 加载字符串（prefix=string_id）|
| 0x05 + data≠1 | PUSH | 普通压栈 |
| 0x07 | **FUNC** | 函数调用（data=函数 ID，带命名表）|
| 0x09 | PUSH_CUSTOM_TEXT | 与 op=6 配对 |
| 0x0E + data末字节=0x80 | **SPECIAL_TEXT** | 特殊文本 |
| 0x10/15/17/18/1A | CMPR0/5/7/8/A | 比较系列 |
| 0x1B / 0x1C | INIT / DEINIT | |
| 0x1D | INF1 | 文件内消息计数器 |
| 0x2C | INF2 | 变量名引用 |
| 0x40 | JMP | 无条件跳转 |
| 0x41 | **JNGE** | jump if not greater-equal |
| 0x42 | **JNLE** | jump if not less-equal |

### FUNC 命名表
```
0x4006F → PUSH_MESSAGE        0x501B2 → BGM_PLAY
0x8035  → GOTO_NEXT_SCENE     0x501BF → SE_PLAY
0x18036 → REGISTER_SCENE      0x60165 → TEX_FADE
0x20005 → WAIT                0x90143 → TEX_PUSH
0x2009A → BG_FADE             0x301C2 → VOICE_FADE
0x8803E → BG_PUSH             0x2014F → TEX_CLEAR
                              0x601C0 → SYSTEM_VOICE_PLAY
```

## 💡 核心设计决策

### 1. 注入零跳转修正

NeXAS 跳转目标用 **entry index 而非 byte offset**。字符串变长完全不影响 commands 区，注入只需替换 strings 内容。这跟 AI5WIN/HCB/MGOS 等基于 byte offset 的 bytecode 引擎是相反设计 —— 是 NeXAS 引擎对汉化最友好的特性。

### 2. cp932 双向映射陷阱（已自动处理）

某些日文字符（如 `羽`）在 cp932 有多个编码点（NEC外字 `FB92` / 标准JIS `EE75`）。Python 的 cp932 codec 解码两者都接受，但编码只输出标准的，造成 round-trip 失败。

**解决方案**：`rebuild_script` 对**未修改的字符串保留原始字节**，仅对真正修改了的字符串重新编码。结果：258/258 round-trip MD5 完全匹配。

### 3. extract/inject 工作集设计

提取的 JSON 同时含原文 + consumed 标记：

```json
{
  "_meta": { "source": "...", "magic": "0x11D3", "n_strings": 174, "n_consumed": 169 },
  "strings": [ "", "M", ".png", ..., "@vS016_B1_0002「対話文…」" ],
  "consumed": [ false, false, ..., true, true, ... ]
}
```

- translator 修改 `strings[i]` 即可（**保持数组长度不变！**）
- `consumed[i]=true` 表示该字符串被脚本通过 `LOAD_STRING/CASE4/SET_EFFECT/SPECIAL_TEXT` 主动引用 → 通常需要翻译
- `consumed[i]=false` 表示引擎内部使用，translator 一般不动

## 📊 全游戏统计 (259 个文件)

| 指标 | 值 |
|---|---|
| 总脚本 | 257 剧本 + system + replaymode |
| 成功 | 258 (1 个 `__global.bin` 是 16 字节占位) |
| 总字符串数 | 70,767 |
| 被脚本主动引用的 | 69,786 (98.6%) |
| Round-trip MD5 匹配 | **258 / 258 ✓** |
| LOAD_STRING 总数 | 全游戏几万条对话 |
| 最大脚本 | y1_0000_0.bin (601,002 raw entries) |

## 📁 示例文件

| 文件 | 内容 |
|---|---|
| `_summary_all_259.csv` | 全游戏每个文件的统计概览 |
| `example_b1_1008_0.asm` | 反汇编样本（LOAD_STRING 直接显示对话）|
| `example_b1_1008_0.json` | 文本提取样本（翻译这种 JSON 格式）|
