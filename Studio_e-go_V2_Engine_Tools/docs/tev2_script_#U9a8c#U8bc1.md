# TE_V2 脚本验证

当前脚本验证口径已按“文本优先”调整。  
当前正式验证优先覆盖“文本载体是否稳定进入、是否能自洽回写”，而不是要求完整 `.scr` IR。

当前已覆盖：

- `tiNameSp.dat` 反编译 -> 回编 unchanged roundtrip
- `tiNameSp.dat` 日文文本回写
- `tiNameSp.dat` `gbk` 目标编码写回
- `gbk` 写回后再次反编译恢复文本
- `game01` 的 `tiName.dat` unchanged roundtrip
- `game01` 的 `tiName.dat` `gbk` 目标编码写回
- `tiBalloonSp.dat` 反编译 -> 回编 unchanged roundtrip
- `tiBalloonSp.dat` `gbk` 目标编码写回
- `tiBalloonSp.dat` `gbk` 写回后再次反编译恢复文本
- `game01` 的 `tiBalloon.dat` unchanged roundtrip
- `game01` 的 `tiBalloon.dat` `gbk` 目标编码写回
- `BtText.dat` 外层容器解析
- `BtText.dat` `TXT0` 字符串池导出
- `BtText.dat` unchanged 外层回写
- `BtText.dat` 文本反编译 -> 回编 unchanged roundtrip
- `BtText.dat` 可变长文本回写
- `BtText.dat` `gbk` 目标编码写回
- `BtText.dat` `gbk` 写回后再次反编译恢复文本
- `game01` 的 `BtText.dat` unchanged roundtrip
- `.scr` 外层头区解析
- `.scr` 外层载荷解码
- `.scr` unchanged 外层回写
- `.scr` 文本候选导出
- `.scr` 文本候选导出已确认不依赖正则兜底扫描
- `.scr` 同长度或更短文本的原地回写
- `.scr` 代表样本 `Battle.scr` 的长文本扩容回写
- `.scr` 长文本扩容后相邻文本候选仍可恢复
- `.scr` 在 `Battle.scr` / `start.scr` 中的多条长文本同时扩容回写
- `.scr` 多条长文本同时扩容后相邻文本候选仍可恢复
- `.scr` 在 `start.scr` / `Battle.scr` 中的全候选单条长文本扩容回写
- `.scr` 在 `start.scr` / `Battle.scr` 中的全候选两两组合长文本扩容回写
- `.scr` 在 `script/` 目录下 121 个含文本候选脚本中的首条长文本扩容回写
- `.scr` 在 `script/` 目录下 121 个含文本候选脚本中的末条长文本扩容回写
- `.scr` 文本长度检查入口
- `.scr` 文本长度风险报告
- `.scr` fit/report 工具已区分“原地可写”与“需扩容重建但仍可写”
- `.scr` 每条文本导出结果已正式包含 `rebuild_impact`
- `.scr` `sec3` 局部引用白名单已纳入正式自动重建，并在正式回归下通过
- 混合型脚本 `hh_kraken2.scr` 首正文节点的肇事模式已定位并从白名单中移除，正式总回归恢复通过
- `.scr` 在当前白名单下已完成全脚本正文首条/末条长文本扩容的分段全量实测，共 `242` 个案例，当前 `0` 失败
- `.scr` 当前另有 `run_scr_all_scripts_edge_long_text_regression(..., chunk_index, chunk_count)` 作为重型可重复验证入口
- 根入口命令：`python .\tev2_all_scripts_edge_regression.py --chunk-index <0-based> --chunk-count <N>`
- `.scr` 当前另有混合型脚本正文首/末条长文本回归入口：`python .\tev2_mixed_scripts_edge_regression.py`
- `.scr` 当前已完成全脚本正文分层点位长文本验证的窗口化实测，并提供根入口：`python .\tev2_all_scripts_stratified_regression.py --chunk-index <0-based> --chunk-count <N> [--script-start <i>] [--script-end <j>]`
- `.scr` 当前另有混合型脚本单条正文长文本穷举回归入口：`python .\tev2_all_scripts_single_entry_regression.py --mixed-only`
- `.scr` 当前已完成全脚本单条正文长文本穷举回归的分段全量实测，`chunk 0/1/2` 当前全部通过

当前未覆盖：

- `Script.dat` 正式链
- `.scr` 内层 opcode / operand 语义
- `.scr` 任意条目长文本修改的全面安全边界
