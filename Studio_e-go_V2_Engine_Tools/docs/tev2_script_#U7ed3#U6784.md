# TE_V2 脚本结构

## 当前层次

当前脚本链应明确拆成两层：

1. 外层容器层
   - `BtText.dat`
   - `Script.dat`
   - `*.scr`
   - 以及 `TSCR` / `TUTA` / `TCRP` / `TXT0` / `M3H0`
2. 内层指令层
   - 真正的 `op + arg` 脚本主体

当前已经确认和建立正式入口的是外层容器层。  
内层指令层尚未被完整反编译。

## 当前方向

当前 title 已触发“困难脚本转向策略”。

当前阶段主目标不是继续硬顶完整 `.scr` 指令集重构，而是：

- 先围绕文本载体建立正式入口
- 先围绕可变长文本建立可维护方案
- 先围绕自洽回写建立验证链

因此这里的重点是：

- 哪些文本载体已经确认
- 哪些文本载体已经能稳定进入
- 哪些仍只是外层事实
- 哪些暂不作为当前阻塞项

## 当前已确认

- 引擎导出中存在 `Script.dat`
- 引擎导出中存在 `script\%s`
- 当前已确认 `script\%s` 是运行时脚本资源路径模板
- 当前已确认包内存在正式 `script` 组，`*.scr` 来自该组
- 当前仍不能把 `Script.dat` 直接定性为“独立总脚本包”
- `tiNameSp.dat` / `tiPageSp.dat` / `tiBalloonSp.dat` 是固定 64 字节记录表
- 这类记录表当前已确认存在按 32 位词异或的解密逻辑
- 当前 `tiNameSp.dat` 已可以正式反编译 / 回编
- 当前 `tiBalloonSp.dat` 已能进入同一条正式反编译 / 回编链
- `tiPageSp.dat / tiPage.dat` 当前样本只有 8 字节：
  - `u32 0x00000006`
  - `u32 0x00000003`
  - 当前已确认它不是文本载体
- `BtTexture.dat` 当前已确认是：
  - `TSCR` 外层
  - mode-5 解码后根容器是 `TUTA`
  - 其主子块是 `FLD0`
  - 当前不属于正文文本载体
- `BtText.dat` 原始文件头当前已确认是：
  - `TSCR`
  - `total_size_u32`
  - `raw_entry_count_u32`
  - `key_seed_u32`
- `BtText.dat` 文件头后的主载荷当前已确认可按 mode-5 字交换异或规则解码
- `BtText.dat` 解码后的根容器当前已确认是 `TUTA`
- `BtText.dat` 解码后的 `TUTA` 内当前已确认存在 `TXT0` 子容器
- `TXT0` 当前已确认是索引字符串池：
  - `u32 size`
  - `u32 entry_count`
  - `u32 relative_offsets[entry_count]`
  - 字符串基址按运行时规则相对 `TXT0 + 8` 计算
  - 字符串本体按 `FE 01 EF 10` 循环异或并以同模式的空终止结尾
- `*.scr` 原始文件头当前已确认是：
  - `SCR `
  - `version_u32 = 0x00010001`
  - `codec_mode_u32 = 2`
  - `key_seed_u32`
  - `decoded_payload_size_u32`
- `*.scr` 文件头后的主载荷当前已确认可按 mode-2 规则稳定解码
- `*.scr` 解码后当前已能稳定提取 ASCII 字面量，例如：
  - `fade`
  - `start`
  - `t_01`
- `*.scr` 文本候选当前只允许通过结构化前导模式反序列化提取
- 当前已确认的结构化前导至少包括：
  - `0x0A + cp932文本 + 0x00`
  - `0x0B + cp932文本 + 0x00`
- 当前已不再允许使用正则暴力扫描导出 `.scr` 文本候选
- `TSCR` 运行时当前已确认会分发到至少：
  - `TUTA`
  - `TCRP`
  - `TSCR`
  - `TXT0`
  - `M3H0`
- 运行时当前还已确认 `M3P0` 也是容器族中的一个 magic
- `TCRP` 当前已确认对应 256 字节记录数组
- `TSCR` 当前已确认对应 32 字节记录数组
- `BtText.dat` 运行时当前已确认存在“按索引取字符串”的文本池层
- `521660(index)` 会把文本池条目映射成字符串
- `BtText.dat` 当前已能做到：
  - 原始文件头解析
  - `TUTA` 根容器解码
  - `TXT0` 字符串池导出
  - unchanged byte-identical 外层回写
- `*.scr` 当前已能做到：
  - 原始文件头解析
  - 外层载荷解码
  - unchanged byte-identical 外层回写
- `57F250` 当前已确认会对某个 data 类资源名去扩展名后追加 `Script.dat`
- 结合当前样本，`tiItemTitle.dat -> tiItemTitleScript.dat` 已与该 sidecar 规则一致
- 当前仍缺少能把 `Script.dat` 作为独立资源文件直接落到样本中的实证

## 文本优先现状

### 当前优先文本载体

- `tiNameSp.dat`
  - 已正式反编译 / 回编
  - 已支持目标编码写回
- `tiBalloonSp.dat`
  - 已正式反编译 / 回编
  - 已支持目标编码写回
- `tiName.dat / tiBalloon.dat`
  - 已确认能进入和 `Sp` 版同一条固定表文本链
- `BtText.dat`
  - 已能正式反编译 / 回编
  - 已支持可变长文本回写
  - 已支持目标编码写回
  - 是正文文本正式入口
  - `game01` 版样本也已能进入同一条 unchanged roundtrip 文本链
- `.scr` 已解码载荷中的字面量
  - 已能定位
  - 当前已能稳定导出一部分日文文本候选
  - 当前候选仅来自结构化反序列化结果
  - 当前每条候选已附带 `rebuild_impact`
  - `rebuild_impact` 当前至少覆盖：
    - 外层 `decoded_payload_size_u32`
    - `sec3` 长度字段
    - `sec4` 受影响偏移位点
    - `sec5` 受影响偏移位点
    - `sec3` 内部疑似局部偏移字段探针摘要
  - 其中一部分 `sec3` 内部局部引用模式当前已经进入自动重建白名单
  - 白名单模式当前已确认会随文本增量 `delta` 一起重建，而不只是记录为探针
  - 当前白名单已覆盖跨脚本重复出现、正文主链高频出现、且经正式回归确认可安全重建的局部引用模式
  - 白名单会继续按混合型脚本实测结果动态收缩，当前已排除会破坏 `hh_kraken2.scr` 首正文边界的一类模式
  - 当前已支持“同长度或更短”的原地回写
  - 当前已支持代表样本的长文本扩容回写
  - 当前已支持同一文件内多条文本同时扩容回写
  - 当前已支持 `start.scr` / `Battle.scr` 全候选单条扩容与两两组合扩容
  - 当前已支持 `script/` 目录下 121 个含文本候选脚本的首条与末条扩容回写
  - 但任意条目长文本修改的全面安全边界仍未 formalize

### 当前方向要求

- 以文本为主
- 以可变长文本为主
- 以自洽回写为主
- 以最小侵入 patch 方案为主

### 当前暂不作为阻塞项

- 完整 `.scr` opcode / operand 语义
- 完整 `.scr` 编译器
- 完整 CFG / 状态机重构

## 当前未确认

- `Script.dat` 是否真的是独立总脚本包，还是一类 sidecar 派生命名
- `BtText.dat` 中除 `TXT0` 以外的 `TUTA` 子层字段语义
- `*.scr` 解码后内层指令流的 opcode / operand 语义
- `*.scr` 与 `Script.dat` 之间是否存在额外索引层
