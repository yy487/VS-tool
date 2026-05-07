# TE_V2 脚本用法

当前 title 当前阶段已按“文本优先”处理。  
正式脚本入口先从稳定文本载体开始，而不是从完整 `.scr` 指令集开始：

- `tev2_decompile.py`
- `tev2_compile.py`
- `tev2_bttext_probe.py`
- `tev2_scr_probe.py`

## 反编译 `tiNameSp.dat`

```powershell
python .\tev2_decompile.py .\pak0_game00\files\data\tiNameSp.dat .\table_dump\tiNameSp.json --text-encoding cp932
```

- 输入
  - `.\pak0_game00\files\data\tiNameSp.dat`
- 输出
  - `.\table_dump\tiNameSp.json`
- 适用场景
  - 当前最小正式文本导出链

## 编译 `tiNameSp.dat`

```powershell
python .\tev2_compile.py .\table_dump\tiNameSp.json .\table_rebuild\tiNameSp.dat --text-encoding cp932
```

- 输入
  - `.\table_dump\tiNameSp.json`
- 输出
  - `.\table_rebuild\tiNameSp.dat`
- 适用场景
  - 当前最小正式文本回写链

## 指定回写编码（GBK 示例）

```powershell
python .\tev2_compile.py .\table_dump\tiNameSp.json .\table_rebuild_gbk\tiNameSp.dat --text-encoding gbk
```

- 输入
  - `.\table_dump\tiNameSp.json`
- 输出
  - `.\table_rebuild_gbk\tiNameSp.dat`
- 适用场景
  - 当前最小目标编码写回链

## 按目标编码再次反编译校验

```powershell
python .\tev2_decompile.py .\table_rebuild_gbk\tiNameSp.dat .\table_dump_gbk\tiNameSp.json --text-encoding gbk
```

- 输入
  - `.\table_rebuild_gbk\tiNameSp.dat`
- 输出
  - `.\table_dump_gbk\tiNameSp.json`
- 适用场景
  - 校验 `gbk` 写回结果仍可再次反编译

## 当前编码口径

- 当前已实现文本表读写路径：`cp932`
- 当前已实现目标编码写回：`gbk`
- 当前文本主线以固定记录表优先
- 当前正文文本正式入口是 `BtText.dat`
- 当前 `.scr` 已进入正式文本候选回写链

## 反编译 `BtText.dat`

```powershell
python .\tev2_decompile.py .\_pak0_game00\files\data\BtText.dat .\bttext_probe\BtText.json --text-encoding cp932
```

- 输入
  - `.\_pak0_game00\files\data\BtText.dat`
- 输出
  - `.\bttext_probe\BtText.json`
- 适用场景
  - 导出 `BtText.dat` 的正式文本 JSON
  - 进入正文文本主线

## 编译 `BtText.dat`

```powershell
python .\tev2_compile.py .\_bttext_probe\BtText.json .\_bttext_probe\BtText_rebuild.dat --text-encoding cp932
```

- 输入
  - `.\_bttext_probe\BtText.json`
- 输出
  - `.\_bttext_probe\BtText_rebuild.dat`
- 适用场景
  - `BtText.dat` 正式文本回写
  - `BtText.dat` 可变长文本回写

## 修改文本 JSON 中的单条文本

```powershell
python .\tev2_patch_text.py .\_bttext_probe\BtText.json .\_bttext_probe\BtText_patched.json --entry-index 12 --text "TEST PATCH"
```

- 输入
  - `.\_bttext_probe\BtText.json`
- 输出
  - `.\_bttext_probe\BtText_patched.json`
- 适用场景
  - 不手工编辑 JSON，直接修改指定文本条目
  - 适用于固定表、`BtText.dat`、`.scr` 文本候选 JSON

## `BtText.dat` 指定回写编码（GBK 示例）

```powershell
python .\tev2_compile.py .\_bttext_probe\BtText.json .\_bttext_probe\BtText_rebuild_gbk.dat --text-encoding gbk
```

- 输入
  - `.\_bttext_probe\BtText.json`
- 输出
  - `.\_bttext_probe\BtText_rebuild_gbk.dat`
- 适用场景
  - 正文主载体目标编码写回

## `BtText.dat` 按目标编码再次反编译校验

```powershell
python .\tev2_decompile.py .\_bttext_probe\BtText_rebuild_gbk.dat .\_bttext_probe\BtText_rebuild_gbk.json --text-encoding gbk
```

- 输入
  - `.\_bttext_probe\BtText_rebuild_gbk.dat`
- 输出
  - `.\_bttext_probe\BtText_rebuild_gbk.json`
- 适用场景
  - 校验 `BtText.dat` 目标编码写回后仍可再次反编译

## 导出 `.scr` 文本候选

```powershell
python .\tev2_decompile.py .\_pak0_game00\files\script\start.scr .\scr_probe\start.json --text-encoding cp932
```

- 输入
  - `.\_pak0_game00\files\script\start.scr`
- 输出
  - `.\scr_probe\start.json`
- 适用场景
  - 导出 `.scr` 已解码载荷中的文本候选
  - 确认正文是否已落在 `.scr` 当前可识别区域
  - 当前只导出结构化反序列化得到的文本节点，不允许正则兜底扫描
  - 当前已实测能导出：
    - `ＧＡＭＥ　ＯＶＥＲ`
    - `『イージーモード』が追加されました。`
  - 当前仍不等于内层 opcode 级反编译

## `.scr` 原地文本回写

```powershell
python .\tev2_compile.py .\scr_probe\start.json .\scr_probe\start_patched.scr --text-encoding cp932
```

- 输入
  - `.\scr_probe\start.json`
- 输出
  - `.\scr_probe\start_patched.scr`
- 适用场景
  - 当前支持对 `.scr` 文本候选做“同长度或更短”的原地回写
  - 适合作为困难脚本文本优先路线下的最小侵入 patch

## 修改 `.scr` 文本候选中的单条文本

```powershell
python .\tev2_patch_text.py .\scr_probe\start.json .\scr_probe\start_patched.json --entry-index 0 --text "TEST OVER"
```

- 输入
  - `.\scr_probe\start.json`
- 输出
  - `.\scr_probe\start_patched.json`
- 适用场景
  - 不手工编辑 JSON，直接修改 `.scr` 某条文本候选
  - 后续可直接接 `tev2_compile.py` 原地回写脚本

## 检查 `.scr` 文本是否还能原地回写

```powershell
python .\tev2_check_text_fit.py .\scr_probe\start.json --entry-offset 361 --text "終幕" --text-encoding cp932
```

- 输入
  - `.\scr_probe\start.json`
- 输出
  - 一份 JSON 检查结果
- 适用场景
  - 在真正回写前先确认容量
  - `fits_in_place = false` 时，不应继续走当前原地回写链
  - `requires_expansion_rebuild = true` 时，应改走扩容重建回写

## 生成 `.scr` 长度风险报告

```powershell
python .\tev2_fit_report.py .\scr_probe\start.json .\scr_probe\start_fit_report.json --extra-bytes 4 --text-encoding cp932
```

- 输入
  - `.\scr_probe\start.json`
- 输出
  - `.\scr_probe\start_fit_report.json`
- 适用场景
  - 批量筛出“只要再长一点就会溢出”的 `.scr` 条目
  - 在进入扩容回写前先做范围判断
  - `can_rebuild_with_expansion_estimate = true` 的条目可进入扩容重建回写链

## `.scr` 代表样本长文本扩容回写

```powershell
python .\tev2_patch_text.py .\scr_probe\Battle.json .\scr_probe\Battle_patched.json --entry-offset 2728 --text "解放イベント拡張テキスト"
python .\tev2_compile.py .\scr_probe\Battle_patched.json .\scr_probe\Battle_patched.scr --text-encoding cp932
```

- 输入
  - `.\scr_probe\Battle.json`
- 输出
  - `.\scr_probe\Battle_patched.json`
  - `.\scr_probe\Battle_patched.scr`
- 适用场景
  - 对代表样本 `.scr` 条目验证长文本扩容回写
  - 当前正式回归已覆盖 `Battle.scr` 偏移 `2728`
  - 当前正式回归也已覆盖 `Battle.scr` / `start.scr` 的多条长文本同时扩容
  - 当前正式总回归还会穷举 `start.scr` / `Battle.scr` 的全候选单条扩容与两两组合扩容
  - 当前正式总回归还会覆盖 `script/` 目录下 121 个含文本候选脚本的首条与末条长文本扩容

## 生成全量文本载体盘点

```powershell
python .\tev2_scan_text.py .\_pak0_game00\files .\_probe_out\text_scan.json --text-encoding cp932
```

- 输入
  - `.\_pak0_game00\files`
- 输出
  - `.\_probe_out\text_scan.json`
- 适用场景
  - 对当前 title 的正式文本载体做一次总盘点
  - 明确固定表、`BtText.dat`、`.scr` 候选分别有哪些入口
