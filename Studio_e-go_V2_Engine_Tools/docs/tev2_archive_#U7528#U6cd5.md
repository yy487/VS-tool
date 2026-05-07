# TE_V2 资源包用法

当前正式资源包入口：

- `tev2_unpack.py`

## 资源包探针导出

```powershell
python .\tev2_unpack.py .\game .\probe_out
```

- 输入
  - `.\game\`
- 输出
  - `.\probe_out\archive_probe.json`
- 适用场景
  - 建立 `game00.dat` ~ `game04.dat` 的正式探针结果
  - 为后续正式 unpack/pack 继续确认包头、组表、文件表和名字区结构

## 解出单个 `PAK0`

```powershell
python .\tev2_unpack.py .\game\game01.dat .\pak0_game01
```

- 输入
  - `.\game\game01.dat`
- 输出
  - `.\pak0_game01\manifest.json`
  - `.\pak0_game01\files\`
- 适用场景
  - 按包内正式组归属和原始文件名解出资源
  - 当前会直接得到例如：
    - `.\pak0_game01\files\data\BtText.dat`
    - `.\pak0_game01\files\data\tiItemTitleScript.dat`
    - `.\pak0_game01\files\script\Battle.scr`
