# 月神楽（Studio_e-go_V2）

来源于用户吉光片羽
当前 title 按逆向项目处理，当前正式目标是：

- 导出可编辑文本
- 支持文本回写
- 支持长文本扩容与短文本缩短
- 用正式验证确认这些链路可重复通过

当前正式文本模型只包含**结构化反序列化确认过的可汉化文本**。  
不再把正则扫到的伪候选当成正式文本。

## 当前能做什么

- `tiNameSp.dat` / `tiBalloonSp.dat` 这类固定表可反编译、回编、`gbk` 写回
- `BtText.dat` 可反编译、回编、可变长回写、`gbk` 写回
- `*.scr` 可结构化导出文本候选、回写、扩容、缩短

## 文本最短路径

如果你只是要做汉化文本处理，最短路径是：

1. 解出资源
2. 导出文本
3. 修改 JSON
4. 回编
5. 跑验证

## 正式入口

### 1. 解出资源

```powershell
python .\tev2_unpack.py .\game\game00.dat .\pak0_game00
```

输入：
- `.\game\game00.dat`

输出：
- `.\pak0_game00\manifest.json`
- `.\pak0_game00\files\`

意义：
- 提供正式文本链所需的 `data/` 和 `script/` 资源树

### 2. 导出固定表文本

```powershell
python .\tev2_decompile.py .\pak0_game00\files\data\tiNameSp.dat .\table_dump\tiNameSp.json --text-encoding cp932
```

### 3. 回编固定表文本

```powershell
python .\tev2_compile.py .\table_dump\tiNameSp.json .\table_rebuild\tiNameSp.dat --text-encoding cp932
```

### 4. 导出 `BtText.dat`

```powershell
python .\tev2_decompile.py .\pak0_game00\files\data\BtText.dat .\bttext_probe\BtText.json --text-encoding cp932
```

### 5. 回编 `BtText.dat`

```powershell
python .\tev2_compile.py .\bttext_probe\BtText.json .\bttext_probe\BtText_rebuild.dat --text-encoding cp932
```

### 6. 导出 `.scr` 文本候选

```powershell
python .\tev2_decompile.py .\pak0_game00\files\script\start.scr .\scr_probe\start.json --text-encoding cp932
```

输出意义：
- 当前只导出结构化反序列化确认过的 `.scr` 文本节点

### 7. 回编 `.scr`

```powershell
python .\tev2_compile.py .\scr_probe\start.json .\scr_probe\start_patched.scr --text-encoding cp932
```

### 8. 修改单条文本

```powershell
python .\tev2_patch_text.py .\scr_probe\start.json .\scr_probe\start_patched.json --entry-index 0 --text "TEST OVER"
```

### 9. 长度检查

```powershell
python .\tev2_check_text_fit.py .\scr_probe\start.json --entry-offset 361 --text "終幕" --text-encoding cp932
python .\tev2_fit_report.py .\scr_probe\start.json .\scr_probe\start_fit_report.json --extra-bytes 4 --text-encoding cp932
```

## 正式验证

### 默认总回归

```powershell
python .\regression_test.py
```

覆盖：
- 固定表 roundtrip
- `BtText.dat` roundtrip / 可变长 / `gbk`
- `.scr` 文本导出 / 回写 / 长文本扩容

### 重型验证入口

全脚本正文首/末条长文本：

```powershell
python .\tev2_all_scripts_edge_regression.py --chunk-index 0 --chunk-count 3
python .\tev2_all_scripts_edge_regression.py --chunk-index 1 --chunk-count 3
python .\tev2_all_scripts_edge_regression.py --chunk-index 2 --chunk-count 3
```

混合型脚本正文首/末条长文本：

```powershell
python .\tev2_mixed_scripts_edge_regression.py
```

全脚本正文分层点位长文本：

```powershell
python .\tev2_all_scripts_stratified_regression.py --chunk-index <0-based> --chunk-count <N> [--script-start <i>] [--script-end <j>]
```

全脚本正文单条长文本：

```powershell
python .\tev2_all_scripts_single_entry_regression.py --chunk-index <0-based> --chunk-count <N> [--script-start <i>] [--script-end <j>] [--entry-start <a>] [--entry-end <b>]
```

全脚本正文单条短文本：

```powershell
python .\tev2_all_scripts_single_entry_short_regression.py --chunk-index <0-based> --chunk-count <N> [--script-start <i>] [--script-end <j>] [--entry-start <a>] [--entry-end <b>]
```

## 当前边界

- 当前目标是“文本汉化链可用”，不是完整 `.scr` opcode / operand 语义恢复
- `Script.dat` 仍未正式收敛成独立总脚本包结论
- 当前正式文本模型只包含结构化确认过的文本节点

## 文档

- [docs/README.md](D:/Code/VN_Reverse/titles/月神楽/docs/README.md)
- [docs/tev2_script_结构.md](D:/Code/VN_Reverse/titles/月神楽/docs/tev2_script_结构.md)
- [docs/tev2_script_用法.md](D:/Code/VN_Reverse/titles/月神楽/docs/tev2_script_用法.md)
- [docs/tev2_script_验证.md](D:/Code/VN_Reverse/titles/月神楽/docs/tev2_script_验证.md)

## 批量工作流入口

本版已给所有正式流程补上批量模式，并保留原来的单文件调用方式。

### 1. 批量解包

```powershell
python .\tev2_unpack.py .\game .\work\unpacked --batch
```

目录输入会匹配 `game*.dat`，输出为：

```text
work/unpacked/game00/files/...
work/unpacked/game01/files/...
```

仍然可以只做探测：

```powershell
python .\tev2_unpack.py .\game .\work\unpacked --probe-only
```

### 2. 批量扫描文本载体

```powershell
python .\tev2_scan_text.py .\work\unpacked\game00\files .\work\text_scan.json --batch --text-encoding cp932
```

会递归识别：

- `TSCR` / `BtText.dat`
- `SCR ` / `*.scr`
- 普通 `.dat` 固定表

### 3. 批量导出文本 JSON

```powershell
python .\tev2_decompile.py .\work\unpacked\game00\files .\work\json --batch --text-encoding cp932 --skip-errors
```

输出会保持相对路径：

```text
work/json/data/tiNameSp.json
work/json/data/BtText.json
work/json/script/start.json
```

### 4. 批量 Patch 文本 JSON

单条替换：

```powershell
python .\tev2_patch_text.py .\work\json\script\start.json .\work\json_patched\script\start.json --entry-index 0 --text "TEST"
```

批量按映射表替换：

```powershell
python .\tev2_patch_text.py .\work\json .\work\json_patched --batch --patch-map .\translation_map.json --skip-errors
```

`translation_map.json` 支持两种形式：

```json
{
  "原文": "译文"
}
```

或：

```json
[
  {"scr_msg": "原文", "msg": "译文"},
  {"original_text": "原文", "message": "译文"}
]
```

### 5. 批量长度检查 / 报告

```powershell
python .\tev2_check_text_fit.py .\work\json_patched .\work\fit_check --batch --text-encoding gbk --skip-errors
python .\tev2_fit_report.py .\work\json_patched .\work\fit_report --batch --extra-bytes 4 --text-encoding gbk --skip-errors
```

### 6. 批量回编文本

```powershell
python .\tev2_compile.py .\work\json_patched .\work\patched_files --batch --source-root .\work\unpacked\game00\files --text-encoding gbk --skip-errors
```

`--source-root` 用于把批量 JSON 的 `source_path` 重定向回原始解包文件。这样移动 JSON 目录后也能正常回编。

### 7. 批量 Probe

```powershell
python .\tev2_scr_probe.py .\work\unpacked\game00\files\script .\work\probe\scr --batch --skip-errors
python .\tev2_bttext_probe.py .\work\unpacked\game00\files .\work\probe\bttext --batch --skip-errors
```

### 8. 批量封包

```powershell
python .\Studio_e-go_V2_pack.py .\work\patched_unpacked .\build --batch --quiet --skip-errors
```

输入目录支持两种结构：

```text
work/patched_unpacked/game00/files/...
work/patched_unpacked/game01/files/...
```

或：

```text
work/patched_unpacked/game00/...
work/patched_unpacked/game01/...
```

输出：

```text
build/game00.dat
build/game01.dat
```

### 9. 总批量分发入口

也可以使用统一入口：

```powershell
python .\tev2_batch.py unpack .\game .\work\unpacked
python .\tev2_batch.py scan-text .\work\unpacked\game00\files .\work\text_scan.json
python .\tev2_batch.py export-text .\work\unpacked\game00\files .\work\json --text-encoding cp932 --skip-errors
python .\tev2_batch.py import-text .\work\json_patched .\work\patched_files --source-root .\work\unpacked\game00\files --text-encoding gbk --skip-errors
python .\tev2_batch.py fit-report .\work\json_patched .\work\fit_report --text-encoding gbk --skip-errors
python .\tev2_batch.py pack .\work\patched_unpacked .\build --quiet --skip-errors
```

## 反汇编/扫描加速说明

本版对 SCR 文本反汇编做了轻量加速：

1. `SCR` mode-2 解码热路径改为预分配 `bytearray` + `struct.unpack_from/pack_into`，减少逐 word 切片和临时 bytes 分配。
2. `tev2_decompile.py` 默认不再输出每条文本的 `rebuild_impact` 诊断字段。该字段主要用于逆向调试，正常导出/翻译/回编不需要；需要完整诊断时加 `--with-impact`。
3. `tev2_decompile.py --batch` 和 `tev2_scan_text.py` 新增 `--jobs`，可多进程处理目录。`--jobs 0` 表示使用 CPU 核心数。
4. `tev2_scan_text.py` 统计 SCR 条目数时固定走无 impact 的快速路径。

推荐批量导出命令：

```powershell
python .\tev2_decompile.py .\scr .\json --batch --text-encoding cp932 --jobs 0 --skip-errors
```

如果要输出旧版那种详细 `rebuild_impact` 诊断：

```powershell
python .\tev2_decompile.py .\scr .\json_debug --batch --text-encoding cp932 --with-impact --jobs 0 --skip-errors
```

推荐扫描命令：

```powershell
python .\tev2_scan_text.py .\scr .\json\text_scan.json --text-encoding cp932 --jobs 0
```

## 批量反编译 / 批量重新编译

现在 `tev2_decompile.py` 和 `tev2_compile.py` 遇到目录输入时会自动进入批量模式，不再必须手动写 `--batch`。

批量反编译每个脚本/文本文件：

```powershell
python .\tev2_decompile.py .\scr .\json --text-encoding cp932 --jobs 0 --skip-errors
```

输出会按原始目录结构生成每个文件对应的 JSON，例如：

```text
json\xxx.json
json\subdir\yyy.json
```

批量重新编译每个 JSON：

```powershell
python .\tev2_compile.py .\json .\patched_scr --source-root .\scr --text-encoding cp932 --jobs 0 --skip-errors
```

输出也会按 JSON 目录结构还原为原始扩展名，例如 `.scr` / `.dat`。

仍然保留单文件模式：

```powershell
python .\tev2_decompile.py .\scr\a.scr .\json\a.json --single
python .\tev2_compile.py .\json\a.json .\patched_scr\a.scr --source-root .\scr --single
```
