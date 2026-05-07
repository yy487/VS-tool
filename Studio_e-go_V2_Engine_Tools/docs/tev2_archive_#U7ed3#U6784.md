# TE_V2 资源包结构

## 当前已确认

- 样本中存在 `game00.dat` ~ `game04.dat`
- 引擎字符串中存在 `Game%02d.dat`
- 当前可合理推定这组 `.dat` 是正式资源包入口
- 当前样本资源包 magic 是 `PAK0`
- 包头前 16 字节当前已确认包含：
  - `magic`
  - `header_end`
  - `group_count`
  - `file_count`
- `group_count` 后紧跟 `group_count * 8` 字节组表
- 组表后紧跟 `file_count * 16` 字节文件表
- 文件表后到 `header_end` 之间是名字区
- 名字区当前已确认由“组名 + 文件名”组成
- `game00.dat` 当前已确认组名至少包括：
  - `bmp`
  - `effect`
  - `script`
  - `title`
  - `visual`
  - `data`
  - `voice`
- `game01.dat` 当前已确认组名至少包括：
  - `data`
  - `script`
- 当前正式 unpack 已经可以恢复组归属：
  - `data/BtText.dat`
  - `data/tiName.dat`
  - `script/Battle.scr`
  - `script/global.scr`

## 当前未确认

- 组表第一个字段的精确语义
- 文件表 4 个 `u32` 字段中 `align_u32` / `checksum_u32` 的完整运行时语义
- 压缩 / 加密规则
