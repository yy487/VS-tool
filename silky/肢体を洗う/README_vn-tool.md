# vn-tool

一个面向 **Silky 系列/同类资源结构** 的本地化辅助工具集，覆盖：

- `.arc` 资源包解包 / 回封
- `.MES` 脚本文本提取 / 注入
- `font.bfd` 字库扫描 / 映射 / 重建
- `body.exe` 中 `font.bfd` 读取上限补丁

本仓库当前是一套以 **Python 命令行工具** 为核心的实用工具链，适合用于中文化、字库扩容、脚本回写与资源重打包。

---

## 1. 仓库内容

当前仓库根目录包含以下脚本：

| 文件 | 作用 |
|---|---|
| `silky_arc_tool.py` | Silky ARC 资源包解包、回封、信息查看、回环校验 |
| `silky_mes_op.py` | MES VM opcode / 解析辅助模块，供提取器与注入器共用 |
| `silky_mes_extract.py` | 从 `.MES` 中提取剧情文本与选项文本 |
| `silky_mes_inject.py` | 将翻译 JSON 注回 `.MES`，支持重定位与字符映射 |
| `silky_bfd_font.py` | `font.bfd` 字库分析、缺字映射、重建、调试 |
| `patch_silky_bfd_exe_read_limit.py` | 修改 `body.exe` 对 `font.bfd` 的临时读取上限 |

---

## 2. 工具链适用范围

这套工具链的假设和确认点，直接来自脚本实现：

### ARC
- 格式为平面表结构：
  - `uint32le count`
  - 重复 `count` 次：`name[0x20] + offset + size`
  - 后接原始文件数据
- ARC 层本身 **不压缩、不加密**
- 条目名默认按 **CP932** 处理

### MES
- 当前脚本针对的是一类 **XOR 0x55 编码的 Silky MES VM 字节码流**
- 提取与注入默认文本编码为 **CP932**
- 文本记录核心格式为：
  - `00 <msg_id:u32le> <cp932 cstring> 00`
- 角色名通常作为单独 MESSAGE 记录存在，例如：`［千草］`
- 选择支定义与文本表可被识别并提取

### BFD
- 当前字库工具针对 **BFD24**：
  - `magic[8] = BFD24-00`
  - `u16 width, u16 height, u32 glyph_count`
  - `code_table`
  - `plane1`
  - `plane2`
- 字体本地化的设计思路是：
  1. 统计翻译中出现的字符
  2. 保留可直接 CP932 编码的字符
  3. 为不可直接编码字符分配“借码”来源字符
  4. 重绘 `font.bfd` 中对应字形
  5. 注入脚本时，把目标字符替换为其 CP932 来源字符

---

## 3. 依赖环境

## 基础环境
- Python 3.10+

## 可选依赖
`silky_bfd_font.py` 的部分功能依赖 Pillow：

```bash
pip install pillow
```

以下功能会用到 Pillow：
- 字形重建 / 渲染
- 预览图导出

---

## 4. 推荐工作流

一个比较完整的中文化流程通常如下：

### 4.1 解包 ARC
```bash
python silky_arc_tool.py unpack script.arc unpacked_script
```

### 4.2 提取 MES 文本
```bash
python silky_mes_extract.py extract unpacked_script json_out
```

### 4.3 翻译 JSON
直接编辑 `json_out/*.json` 中的 `message` 字段。

### 4.4 统计翻译字符
```bash
python silky_bfd_font.py scan-json json_out build/charset.json
```

### 4.5 生成字符映射表
```bash
python silky_bfd_font.py make-map build/charset.json font.bfd build/replace_map.json
```

如有自定义中日映射表，可加：

```bash
python silky_bfd_font.py make-map build/charset.json font.bfd build/replace_map.json --subs subs_cn_jp.json
```

### 4.6 重建 font.bfd
```bash
python silky_bfd_font.py build font.bfd build/replace_map.json simhei.ttf build/font_chs.bfd --preview build/font_preview.png
```

### 4.7 注入翻译脚本
```bash
python silky_mes_inject.py unpacked_script json_out patched_mes --map build/replace_map.json --copy-unmodified
```

### 4.8 如新字库超过 EXE 读取上限，则补丁 body.exe
```bash
python patch_silky_bfd_exe_read_limit.py body.exe body_chs.exe --font build/font_chs.bfd
```

### 4.9 回封 ARC
```bash
python silky_arc_tool.py pack unpacked_script script_chs.arc
```

---

## 5. 文本 JSON 格式

`silky_mes_extract.py` 生成的每文件 JSON 文档格式为：

```json
{
  "format": "silky_mes_story_text_v3_per_file",
  "encoding": "cp932",
  "decoded_input": false,
  "source_file": "s001-001.MES",
  "items": [
    {
      "_file": "s001-001.MES",
      "_offset": 1234,
      "_index": 56,
      "_kind": "message",
      "_msg_id": 100,
      "name": "千草",
      "scr_msg": "「おはよう」",
      "message": "「早上好」"
    }
  ]
}
```

### 字段说明

#### 可编辑字段
- `name`：角色名，可选
- `scr_msg`：原文，用于定位和校验
- `message`：当前文本内容；初始通常等于 `scr_msg`，翻译时主要修改这个字段

#### 定位字段
- `_file`：来源文件名
- `_offset`：消息记录偏移
- `_index`：提取时对应命令序号
- `_kind`：类型，常见为 `message` / `choice` / `message_scan`
- `_msg_id`：原始消息 ID

#### 角色名关联字段
当一条对白前面存在独立名字记录时，提取器还会写入内部字段，例如：
- `_name_offset`
- `_name_msg_id`
- `_name_scr_msg`
- `_name_left`
- `_name_right`

这些字段主要由注入器使用，一般不需要手改。

---

## 6. 各工具用法

## 6.1 `silky_arc_tool.py`

Silky ARC 资源包工具。

### 查看索引信息
```bash
python silky_arc_tool.py info script.arc
```

### 解包
```bash
python silky_arc_tool.py unpack script.arc unpacked_script
```

### 回封
```bash
python silky_arc_tool.py pack unpacked_script script_new.arc
```

### 回环校验
```bash
python silky_arc_tool.py verify script.arc
```

### 参数说明
- `--name-encoding`：条目名编码，默认 `cp932`
- `unpack --overwrite`：允许覆盖目标目录中已有文件
- `pack --overwrite`：允许覆盖已存在的输出 ARC

### 解包输出
解包后会额外生成：

```text
arc_manifest.json
```

该清单记录：
- 原始条目顺序
- 条目名
- 偏移与大小
- 解包文件名
- SHA-256

回封时默认依赖这个 `arc_manifest.json` 来恢复结构和顺序。

---

## 6.2 `silky_mes_extract.py`

从 `.MES` 文件中提取剧情对白、名字、选项文本。

### 批量提取目录
```bash
python silky_mes_extract.py extract mes_dir json_out
```

### 提取单个 MES
```bash
python silky_mes_extract.py extract s001-001.MES s001-001.json
```

### 输出选择支结构调试信息
```bash
python silky_mes_extract.py choices s001-001.MES
```

### 常用参数
- `--encoding ENCODING`：文本编码，默认 `cp932`
- `--decoded`：输入 MES 已经是 XOR 解码后的版本
- `--include-non-story`：不跳过 `art/theater/title/jump/def/startup` 等非剧情文件
- `--skip-stem xxx`：额外指定要跳过的文件名前缀，可重复使用
- `--scan-all`：做补充性全文件 message record 扫描，可能抓到更多文本，但也可能提高误判概率
- `--write-empty`：即使某个剧情 MES 没提到文本，也写出空 JSON 文件

### 提取逻辑特点
- 会自动识别并配对独立名字记录与后续对白记录
- 选择支文本会作为 `choice` 类型导出
- 会按文件名过滤明显非剧情脚本，除非显式开启 `--include-non-story`
- 会对疑似误扫文本做一部分过滤，例如无效控制字符、异常消息 ID、纯括号标记等

---

## 6.3 `silky_mes_inject.py`

将翻译 JSON 注回 `.MES`。

### 批量注入
```bash
python silky_mes_inject.py mes_dir json_out patched_mes
```

### 配合字库映射注入
```bash
python silky_mes_inject.py mes_dir json_out patched_mes --map build/replace_map.json --copy-unmodified
```

### 常用参数
- `--encoding ENCODING`：文本编码，默认优先取 JSON 里的编码，否则 `cp932`
- `--decoded`：输入/输出 MES 都视作已解码数据，不做 XOR 0x55 编码/解码
- `--errors {strict,replace,ignore}`：编码失败时的处理策略
- `--map MAP`：传入 `replace_map.json`，注入前先把目标字符替换成 CP932 来源字符
- `--keep-ascii`：不将 ASCII 和基础标点归一化为全角
- `--on-mismatch {error,skip,patch}`：当 `scr_msg` 与文件内容不匹配时如何处理
- `--missing {warn,error}`：JSON 对应文件不存在时如何处理
- `--copy-unmodified`：把未修改文件一起复制到输出目录
- `--dry-run`：只检查，不落盘
- `--report report.json`：输出详细注入报告

### 注入逻辑特点
- 基于 `_offset` 精确定位消息记录
- 使用 `scr_msg` 做一致性校验
- 支持对白记录与独立名字记录的联动回写
- 对长度变化进行重建，并修正相关相对跳转 / 表项重定位
- 当没有任何替换时，可直接复制原文件

### 建议
为了避免错注，推荐始终保留：
- `_file`
- `_offset`
- `scr_msg`

不要随意改动这些字段。

---

## 6.4 `silky_bfd_font.py`

BFD24 字库本地化主工具。

### 1）扫描翻译字符
```bash
python silky_bfd_font.py scan-json json_out build/charset.json
```

可选参数：
- `--no-name`：不统计 `name` 字段
- `--keep-ascii`：不把 ASCII 归一化为全角

输出 `charset.json` 中会区分：
- `direct_cp932_chars`：可直接用 CP932 双字节编码的字符
- `need_map_chars`：需要借码映射的字符

### 2）生成映射表
```bash
python silky_bfd_font.py make-map build/charset.json font.bfd build/replace_map.json
```

常用参数：
- `--subs SUBS`：提供自定义 target->source 映射表
- `--no-strict-subs`：允许非法或冲突的 subs 条目被跳过，而不是直接报错
- `--allow-subs-direct-collision`：允许 subs 与直接字符冲突，危险选项
- `--subs-priority {override,error,ignore}`：控制 subs 与直出字符冲突时的策略
- `--allow-overwrite`：追加码位用尽后，允许覆盖已有 BFD glyph
- `--max-size 0x400000`：限制生成字库最大大小

输出的 `replace_map.json` 主要包含：
- `direct_cp932_chars`
- `chars`（需要映射的字符）
- `unmapped_chars`
- `warnings`
- `summary`

如果投影后的字库大小超过 `--max-size`，命令会直接报错，并提示需要压缩字符集或补 EXE。

### 3）重建字库
```bash
python silky_bfd_font.py build font.bfd build/replace_map.json simhei.ttf build/font_chs.bfd
```

常用参数：
- `--size`：绘制字号，默认 `22`
- `--template-char`：模板字符，默认 `あ`
- `--max-size`：最大允许输出大小
- `--x-offset` / `--y-offset`：绘制偏移
- `--preview preview.png`：额外导出预览 PNG
- `--render-mode {bfd24,hard}`：渲染模式，默认 `bfd24`

执行后会额外生成：

```text
font_chs.bfd.manifest.json
```

里面记录了：
- 原始 / 新字形数量
- 输出大小
- 每个目标字符使用的来源字符
- 字形是追加还是覆写

### 4）辅助调试命令

#### 导出码表
```bash
python silky_bfd_font.py dump-table font.bfd build/bfd_table.tsv
```

#### 查看某些字符是否已存在于 BFD
```bash
python silky_bfd_font.py inspect font.bfd "凜錬語"
```

#### 查看某些目标字符会映射到什么来源字符
```bash
python silky_bfd_font.py inspect-map build/replace_map.json "你好世界"
```

#### 调试某段文本的编码结果
```bash
python silky_bfd_font.py debug-encode build/replace_map.json "你好，世界" --bfd font.bfd
```

这个命令非常适合排查：
- 某个字符最终借用了谁的 CP932 编码
- 是否会编码失败
- 对应来源码位在 BFD 中是否存在

#### 导出反向映射表
```bash
python silky_bfd_font.py export-reverse-map build/replace_map.json build/reverse_map.json
```

这个文件适合给 GDI / TextOutA hook 侧使用，即：
- BFD 方案里，脚本存的是来源字符
- GDI 方案里，没有 BFD 重绘，可能需要在输出前再把来源字符翻回目标字符

#### 导出字形预览图
```bash
python silky_bfd_font.py preview font.bfd build/font_preview.png
```

也可结合映射表预览目标字符：

```bash
python silky_bfd_font.py preview font.bfd build/font_preview.png --map build/replace_map.json --chars "你好世界"
```

---

## 6.5 `patch_silky_bfd_exe_read_limit.py`

当重建后的 `font.bfd` 超过游戏 EXE 原本的读取上限时，使用此工具补丁 `body.exe`。

脚本中已经写明当前确认的两个补丁点：
- `VA 0043027A`：临时 `HeapAlloc` 大小
- `VA 00430308`：`ReadFile` 最大读取字节数

默认旧上限为：

```text
0x400000
```

### 指定新上限直接补丁
```bash
python patch_silky_bfd_exe_read_limit.py body.exe body_chs.exe --limit 0x800000
```

### 根据 font.bfd 大小自动向上取整补丁
```bash
python patch_silky_bfd_exe_read_limit.py body.exe body_chs.exe --font build/font_chs.bfd
```

### 仅检查，不输出文件
```bash
python patch_silky_bfd_exe_read_limit.py body.exe --dry-run --limit 0x800000
```

### 参数说明
- `input_exe`：原始 EXE
- `output_exe`：输出补丁后 EXE；若使用 `--dry-run` 可省略
- `--limit`：显式指定新上限
- `--font`：根据字库大小自动计算所需上限，并按 MiB 向上取整
- `--dry-run`：只打印补丁信息，不写文件

---

## 7. 常见注意事项

### 1）默认编码是 CP932
无论是 ARC 条目名、MES 文本还是 BFD 码位，当前工具链默认都围绕 **CP932** 设计。

### 2）`message` 才是主要翻译字段
提取器输出里：
- `scr_msg`：原文
- `message`：当前可编辑文本

建议只改 `message`，保留 `scr_msg` 作为校验基准。

### 3）字库方案不是“直接写中文编码”
这套方案的核心是：
- 脚本侧仍尽量保持 CP932 可编码流
- 不可直接编码的目标字符，通过 `replace_map.json` 借用来源字符码位
- `font.bfd` 把这些来源字符的字形改绘成目标字符

### 4）BFD 变大后可能需要补 EXE
`make-map` 与 `build` 阶段都要关注输出字库体积。
如果超过原始 EXE 的 4 MiB 读取上限，需要同时补丁 EXE。

### 5）`--scan-all` 很有用，但不是默认安全模式
它会补扫 CFG 没直接走到的消息记录，适合查漏；但也会提高误提取概率，建议按项目需要开启。

---

## 8. 一个最小示例

```bash
python silky_arc_tool.py unpack script.arc work/script
python silky_mes_extract.py extract work/script work/json
python silky_bfd_font.py scan-json work/json work/build/charset.json
python silky_bfd_font.py make-map work/build/charset.json font.bfd work/build/replace_map.json
python silky_bfd_font.py build font.bfd work/build/replace_map.json simhei.ttf work/build/font_chs.bfd --preview work/build/font_preview.png
python silky_mes_inject.py work/script work/json work/script_patched --map work/build/replace_map.json --copy-unmodified
python patch_silky_bfd_exe_read_limit.py body.exe body_chs.exe --font work/build/font_chs.bfd
python silky_arc_tool.py pack work/script_patched script_chs.arc
```

---

## 9. 后续可补充内容建议

如果你准备正式上传 GitHub，建议后续再补：

- 适配过的具体游戏 / 引擎分支说明
- 一份实际样例输入输出目录结构
- `replace_map.json` / `charset.json` / per-file JSON 的样例文件
- 常见报错 FAQ
- 与其他工具（如 Garbro、反汇编分析结果）的关系说明
- License

---

## 10. 免责声明

本仓库 README 当前基于仓库内现有 Python 脚本的实现逻辑整理而成，适配范围以实际目标游戏样本为准。不同 Silky 标题、不同版本 EXE、不同资源布局，可能需要按具体项目再做调整。
