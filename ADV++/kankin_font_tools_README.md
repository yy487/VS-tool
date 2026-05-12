# kankin_font_tools

`kankin_font_tools` 是针对本游戏 `font.dat` 字库的辅助处理工具，主要用于视觉小说 / Galgame 本地化流程中的 **CP932 代用字映射、翻译 JSON 转换、字库 glyph 重绘**。

本工具适用于以下工作流：

```text
翻译 JSON 中存在 CP932 不可编码的中文字符
    ↓
通过 subs_cn_jp.json 将简中字符映射到 CP932 可编码的日文/兼容汉字
    ↓
将 JSON 中的 name / message 转换为代用字符版本
    ↓
根据映射关系，把 font.dat 中“代用字符”的字形重绘为“原中文字符”
    ↓
游戏仍按 CP932 显示文本，但实际显示为中文 glyph
```

---

## 1. 文件说明

工具包主要包含：

```text
kankin_font_patch.py   主工具脚本
README.md             使用说明
```

需要你额外准备：

```text
font.dat              原游戏字库文件
subs_cn_jp.json       简中字符到 CP932 代用字符的映射表
翻译 JSON 目录         例如 chs/json_restore
TTF/OTF 字体文件       例如 alyce_humming.ttf、NotoSansCJK-Regular.otf
```

---

## 2. cn_jp 映射格式

`subs_cn_jp.json` 使用如下格式：

```json
{
  "这": "這",
  "说": "説",
  "为": "為",
  "你": "凜",
  "们": "們",
  "时": "時",
  "过": "過",
  "对": "対",
  "么": "麼"
}
```

含义是：

```text
原中文字符 -> CP932 可编码代用字符
```

例如：

```text
你 -> 凜
说 -> 説
```

文本注入时实际写入的是 `凜説`，但字库中 `凜` 的 glyph 会被重绘成 `你`，`説` 的 glyph 会被重绘成 `说`。

注意：一个代用字符只能对应一个原中文字符，否则同一个 glyph 无法同时显示成两个不同的字。

错误示例：

```json
{
  "你": "凜",
  "她": "凜"
}
```

---

## 3. JSON 字段处理范围

工具默认只处理：

```text
name
message
```

不会默认处理：

```text
scr_msg
pre_jp
msg
id
```

这是为了避免破坏原始脚本文本、日文定位字段或其他注入校验字段。

如果确实需要处理其他字段，可以通过 `--fields` 指定，例如：

```bat
python kankin_font_patch.py scan json_restore --cn-jp subs_cn_jp.json --fields name,message,msg
```

一般情况下不建议处理 `scr_msg`。

---

## 4. 基本命令

### 4.1 查看 font.dat 信息

```bat
python kankin_font_patch.py info font.dat
```

用于检查 `font.dat` 外层 YOX archive 信息，包括 FID、offset、size 等。

---

### 4.2 扫描翻译 JSON 中不可 CP932 编码字符

```bat
python kankin_font_patch.py scan E:\BaiduNetdiskDownload\1\2\3\chs\json_restore --cn-jp E:\BaiduNetdiskDownload\1\2\3\subs_cn_jp.json --output scan_report.json
```

输出示例：

```text
[SCAN DONE]
  input                         = E:\BaiduNetdiskDownload\1\2\3\chs\json_restore
  fields                        = message, name
  json_files                    = 7
  strings_scanned               = 23694
  unique_unencodable_chars       = 1123
  total_unencodable_occurrences = 67939
  mapped_ok                     = 1123
  missing_mapping               = 0
  mapped_not_cp932_encodable    = 0
```

重点看这三项：

```text
unique_unencodable_chars
mapped_ok
missing_mapping
```

如果：

```text
unique_unencodable_chars = mapped_ok
missing_mapping = 0
mapped_not_cp932_encodable = 0
```

说明当前 `subs_cn_jp.json` 覆盖了全部不可编码字符，可以进入 JSON 转换与字库重绘阶段。

如果存在 `missing_mapping`，需要补充 `subs_cn_jp.json`。

如果存在 `mapped_not_cp932_encodable`，说明某些代用字符本身不能编码为 CP932，需要更换代用字符。

---

### 4.3 将翻译 JSON 转换为代用字版本

```bat
python kankin_font_patch.py convert-json E:\BaiduNetdiskDownload\1\2\3\chs\json_restore --cn-jp E:\BaiduNetdiskDownload\1\2\3\subs_cn_jp.json --output-dir E:\BaiduNetdiskDownload\1\2\3\chs\json_cn
```

转换示例：

转换前：

```json
{
  "name": "你",
  "message": "你说什么"
}
```

假设映射为：

```json
{
  "你": "凜",
  "说": "説",
  "么": "麼"
}
```

转换后：

```json
{
  "name": "凜",
  "message": "凜説什麼"
}
```

转换后的 `json_cn` 目录用于后续文本注入。

---

### 4.4 重绘 font.dat 字库

```bat
python kankin_font_patch.py patch font.dat font_new.dat --json E:\BaiduNetdiskDownload\1\2\3\chs\json_restore --cn-jp E:\BaiduNetdiskDownload\1\2\3\subs_cn_jp.json --font alyce_humming.ttf --report font_patch_report.json
```

参数含义：

```text
font.dat                    输入原字库
font_new.dat                输出新字库
--json json_restore          用原中文 JSON 扫描需要重绘的字符
--cn-jp subs_cn_jp.json      简中 -> 代用字 映射表
--font alyce_humming.ttf     用于绘制中文字形的 TTF/OTF 字体
--report                    输出重绘报告
```

注意：`--json` 应该使用 **原中文版本 JSON**，也就是 `json_restore`，不是已经转换为代用字的 `json_cn`。因为工具需要知道：

```text
哪个原中文字符 -> 应该重绘到哪个代用字符 glyph 上
```

---

## 5. 字形大小与位置调整

v4 版本默认使用 **bbox 自动贴满槽位**：

```text
不改变 font.dat 原有 glyph slot 尺寸
自动寻找最大 TTF 字号
让实际字形尽量填满当前 slot
```

也就是说，槽位大小保持不变，只调整绘制到槽位里的字形大小。

### 5.1 字体仍然偏小

可以提高自动搜索上限：

```bat
python kankin_font_patch.py patch font.dat font_new.dat --json chs\json_restore --cn-jp subs_cn_jp.json --font alyce_humming.ttf --scale 1.6
```

常用范围：

```text
--scale 1.25   默认
--scale 1.4    略放大
--scale 1.6    明显放大
```

### 5.2 字体太贴边

可以保留 1 像素边距：

```bat
--padding 1
```

例如：

```bat
python kankin_font_patch.py patch font.dat font_new.dat --json chs\json_restore --cn-jp subs_cn_jp.json --font alyce_humming.ttf --padding 1
```

### 5.3 字体位置偏上或偏下

向下移动 1 像素：

```bat
--y-offset 1
```

向上移动 1 像素：

```bat
--y-offset -1
```

向右移动 1 像素：

```bat
--x-offset 1
```

向左移动 1 像素：

```bat
--x-offset -1
```

常用示例：

```bat
python kankin_font_patch.py patch font.dat font_new.dat --json chs\json_restore --cn-jp subs_cn_jp.json --font alyce_humming.ttf --scale 1.6 --y-offset 1
```

### 5.4 固定字号

如果你不想自动贴满，可以指定固定字号：

```bat
--font-size 18
```

例如：

```bat
python kankin_font_patch.py patch font.dat font_new.dat --json chs\json_restore --cn-jp subs_cn_jp.json --font alyce_humming.ttf --font-size 18
```

### 5.5 关闭自动贴满槽位

```bat
--no-fit-cell
```

关闭后会回到较保守的 `height * scale` 绘制方式。一般不建议使用，除非你需要复现旧版效果。

---

## 6. 推荐完整流程

假设目录为：

```text
E:\BaiduNetdiskDownload\1\2\3
  font.dat
  subs_cn_jp.json
  alyce_humming.ttf
  chs\
    json_restore\
```

推荐执行：

```bat
cd /d E:\BaiduNetdiskDownload\1\2\3
```

第一步，扫描映射完整性：

```bat
python kankin_font_patch.py scan chs\json_restore --cn-jp subs_cn_jp.json --output scan_report.json
```

第二步，转换 JSON：

```bat
python kankin_font_patch.py convert-json chs\json_restore --cn-jp subs_cn_jp.json --output-dir chs\json_cn
```

第三步，重绘字库：

```bat
python kankin_font_patch.py patch font.dat font_new.dat --json chs\json_restore --cn-jp subs_cn_jp.json --font alyce_humming.ttf --report font_patch_report.json
```

第四步，备份并替换：

```bat
ren font.dat font_bak.dat
ren font_new.dat font.dat
```

第五步，用 `chs\json_cn` 执行文本注入。

---

## 7. 输出报告说明

### 7.1 scan_report.json

`scan_report.json` 主要用于检查映射完整性。

常见字段：

```text
json_files                      扫描到的 JSON 文件数量
strings_scanned                 扫描到的 name/message 字段数量
unique_unencodable_chars         不可 CP932 编码的唯一字符数量
total_unencodable_occurrences   不可编码字符总出现次数
mapped_ok                       已成功映射数量
missing_mapping                 缺失映射数量
mapped_not_cp932_encodable      代用字符不可 CP932 编码数量
entries                         逐字符详情
```

### 7.2 font_patch_report.json

`font_patch_report.json` 主要用于检查哪些字符被重绘到了哪些代用字符上。

需要重点关注：

```text
patched                         实际重绘数量
skipped                         跳过数量
missing_mapping                 缺失映射
missing_glyph                   字库中找不到对应代用字符 glyph
```

如果出现 `missing_glyph`，通常说明：

```text
代用字符虽然能被 CP932 编码，但当前 font.dat 对应 FID 没有包含该区段
```

需要更换代用字符，或者只对支持该字符区段的 FID 进行重绘。

---

## 8. 常见问题

### Q1：为什么扫描通过了，游戏里还是显示原来的日文字？

通常有两个原因：

1. JSON 注入时没有使用转换后的 `json_cn`。
2. `font.dat` 没有被替换成重绘后的版本。

正确关系是：

```text
注入用 JSON：chs\json_cn
游戏用字库：font_new.dat 替换后的 font.dat
```

---

### Q2：为什么重绘后字体变小？

旧版脚本会出现这个问题，因为 TTF 字号和实际 CJK 字形 bbox 不一致。

v4 已改成默认自动贴满槽位。重新使用 v4 重绘即可：

```bat
python kankin_font_patch.py patch font.dat font_new.dat --json chs\json_restore --cn-jp subs_cn_jp.json --font alyce_humming.ttf
```

如果仍觉得小，可以加：

```bat
--scale 1.6
```

---

### Q3：为什么不直接把中文字写进脚本？

因为游戏文本编码仍然是 CP932。简中字符如“你、这、们”等通常无法被 CP932 编码，直接注入会报错或显示失败。

所以需要：

```text
中文字符 -> CP932 代用字符
```

再通过重绘 font.dat 的方式让代用字符显示成中文。

---

### Q4：为什么不修改 range_mask 或新增 Unicode 字库？

当前 exe 的字形索引逻辑基于 CP932/SJIS 区段和内部 YOX 字库 index 表。直接扩展 Unicode 字库需要修改 exe 的字符到 glyph index 映射逻辑，风险更高。

本工具采用较稳定的方案：

```text
不改 exe
不改文本编码
只替换 CP932 代用字符对应 glyph
```

---

### Q5：可以只重绘一个字体槽位吗？

可以通过 `--slots` 指定，例如：

```bat
python kankin_font_patch.py patch font.dat font_new.dat --json chs\json_restore --cn-jp subs_cn_jp.json --font alyce_humming.ttf --slots 0,1,2,3,5
```

默认通常会处理支持正文显示的主要 FID。  
如果某些 UI 使用独立 FID，需要根据实际显示情况追加对应槽位。

---

## 9. 注意事项

1. 修改前务必备份原始 `font.dat`。
2. `subs_cn_jp.json` 的代用字符必须能被 CP932 编码。
3. 代用字符不能重复分配给多个中文字符。
4. `patch` 用原中文 JSON，`convert-json` 输出代用字 JSON。
5. 注入时应使用代用字 JSON。
6. 游戏运行时应使用重绘后的 `font.dat`。
7. 一般不要处理 `scr_msg`，否则可能破坏注入定位或校验。

---

## 10. 快速命令汇总

```bat
cd /d E:\BaiduNetdiskDownload\1\2\3
```

扫描：

```bat
python kankin_font_patch.py scan chs\json_restore --cn-jp subs_cn_jp.json --output scan_report.json
```

转换 JSON：

```bat
python kankin_font_patch.py convert-json chs\json_restore --cn-jp subs_cn_jp.json --output-dir chs\json_cn
```

重绘字库：

```bat
python kankin_font_patch.py patch font.dat font_new.dat --json chs\json_restore --cn-jp subs_cn_jp.json --font alyce_humming.ttf --report font_patch_report.json
```

替换字库：

```bat
ren font.dat font_bak.dat
ren font_new.dat font.dat
```
