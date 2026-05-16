# AI6WIN Story Tools

AI6WIN 的 ARC 解包/封包与 MES 文本提取/注入工具链。

本工具链按项目常用工作流拆分为：

```text
mes.arc
  -> ai6win_arc_extract.py 解包
mes_dir
  -> v1/extract.py 批量提取
json_dir
  -> v1/inject.py 批量注入
mes_chs_dir
  -> ai6win_arc_pack.py 重新封包
mes_chs.arc
```

编码默认全部使用 `cp932`。本工具不处理中文字符映射、不处理字体重绘、不切换 GBK。CP932 不可编码字符需要在注入前由外部字符映射流程解决。

## 目录结构

```text
ai6win_story_tools/
  ai6win_arc_common.py        # AI6WIN ARC 通用读写、LZSS 解压/压缩
  ai6win_arc_extract.py       # ARC 批量解包
  ai6win_arc_pack.py          # ARC 批量封包
  core/
    ai6win_mes.py             # 原 AI6WINScriptTool 核心，保留作兼容/参考
    story_common.py           # MES 文本提取/注入共用逻辑
    library/silky_mes.py
  v0/
    extract.py
    inject.py
  v1/
    extract.py
    inject.py
```

AI6WINScriptTool 原始代码保留在 `core/` 内，许可证见 `LICENSE.AI6WINScriptTool`。当前文本提取/注入默认使用 `story_common.py` 内的快速直读/重建逻辑，以便批量处理。

## ARC 解包

```bat
python ai6win_arc_extract.py mes.arc mes_dir
```

输出：

```text
mes_dir/
  *.mes
  liblary.lib
  ai6win_manifest.json
```

`ai6win_manifest.json` 会保存原包条目顺序、压缩状态、offset、size、sha1 等信息，后续封包建议保留。

## MES 批量提取

大多数 AI6WIN 游戏使用 v1：

```bat
python v1/extract.py mes_dir json_dir
```

早期版本可尝试 v0：

```bat
python v0/extract.py mes_dir json_dir
```

保存调试用反汇编文本：

```bat
python v1/extract.py mes_dir json_dir --keep-asm-dir asm_dir
```

输出 JSON 格式：

```json
[
  {
    "name": "カイザー",
    "scr_msg": "原文",
    "message": "原文"
  },
  {
    "scr_msg": "选项或旁白",
    "message": "选项或旁白"
  }
]
```

说明：

- `scr_msg` 是原脚本文本，用于定位和校验，不应修改。
- `message` 是实际注入文本。
- 注入时也兼容读取 `msg` 字段；但默认提取仍输出 `message`。
- 角色名前缀如 `［角色］：台词` 会拆成 `name + scr_msg/message`。
- 选择项不会继承上一句角色名。

## MES 批量注入

```bat
python v1/inject.py mes_dir json_dir mes_chs_dir
```

遇到 CP932 不可编码字符时，默认直接报错停止，方便定位未映射字符。若只想跳过错误条目：

```bat
python v1/inject.py mes_dir json_dir mes_chs_dir --skip-encode-error
```

保存注入后的调试 asm：

```bat
python v1/inject.py mes_dir json_dir mes_chs_dir --keep-asm-dir asm_new
```

注入定位策略：

1. 优先使用同文件内 JSON 顺序 index + `scr_msg` 完全匹配；
2. 若顺序不匹配，则 fallback 到同文件内 `scr_msg` 唯一匹配；
3. 若 `scr_msg` 多重匹配或不存在，则跳过并写入 report；
4. 只修改 `message` / `msg` 对应正文，角色名前缀会自动重组回原 STR_PRIMARY。

## ARC 封包

推荐使用原 manifest 和原 source arc，这样未修改文件会直接复用原始 stored blob，只有修改过的文件才会重新 LZSS 压缩：

```bat
python ai6win_arc_pack.py mes_chs_dir mes_chs.arc --manifest mes_dir\ai6win_manifest.json --source-arc mes.arc
```

常用参数：

```text
--compress-policy manifest   # 默认：沿用原包条目的压缩状态
--compress-policy none       # 全部不压缩存储
--compress-policy all        # 全部压缩
--compress-policy auto-ext   # 按扩展名判断是否压缩
--lzss-mode greedy           # 默认贪心压缩
--lzss-mode literal          # 全 literal，兼容但体积大
--no-reuse-stored            # 不复用原始 blob，强制从输入目录重建所有条目
```

## 推荐完整命令

```bat
python ai6win_arc_extract.py mes.arc mes
python v1/extract.py mes json

rem 翻译/字符映射后：
python v1/inject.py mes json mes_chs
python ai6win_arc_pack.py mes_chs mes_chs.arc --manifest mes\ai6win_manifest.json --source-arc mes.arc
```

## 本地测试结果

使用当前提供的 `mes.arc` 测试：

```text
ARC 条目：229
MES 文件：228
提取 JSON 条目：28946
带 name 条目：18208
未修改 JSON 回注：patched=0, skipped_same=28946, mismatch=0
未修改回注后的 MES：与原解包 MES 字节一致
复用 source-arc 重封包：与原 mes.arc 字节一致
修改 END1 第一条文本后：只重建 1 个文件，其余 228 个条目复用原 blob
```
