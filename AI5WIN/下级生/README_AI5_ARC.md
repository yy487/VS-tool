# AI5WIN / Silky's AI5 ARC 解包工具

## 反汇编定位结论

本工具根据 `AI5CHN.EXE.c` 中的 ARC 读取逻辑还原。

关键函数如下：

| 函数 | 作用 |
|---|---|
| `FUN_0040dd60` | 初始化配置，读取 `bARCMES` / `bARCBG` / `bARCBGM` / `bARCDATA` / `bARCVOICE` 和默认包名 `MES.ARC`、`BG.ARC`、`BGM.ARC`、`DATA.ARC`、`EVENT.ARC`、`EVERY.ARC`。 |
| `FUN_0040fa70` | 打开 ARC 并读取目录：先读 4 字节条目数，再读 `count * 0x14` 字节目录。 |
| `FUN_0040e5c0` | 目录项解码函数，对每 20 字节目录项执行异或和字节位置还原。 |
| `FUN_0040e680` | 普通读取路径：按文件名查目录，然后 `SetFilePointer(offset)` + `ReadFile(size)`。 |
| `FUN_0040eb50` | 另一条读取路径：同样按目录 `offset/size` 读取，只是先读到临时缓冲区再复制。 |
| `FUN_004204e0` | 文件名查找前转大写；目录名本身仍按目录中的 12 字节字段保存。 |

## ARC 文件结构

小端序：

```text
0x00  uint32 count
0x04  encrypted_entry[count]
      每个 encrypted_entry 长度 0x14
      解码后：
        +0x00 char name[12]
        +0x0C uint32 size
        +0x10 uint32 offset
0x04 + count*0x14  data...
```

目录项解码逻辑对应 `FUN_0040e5c0`：

```python
perm = [0x11, 0x02, 0x08, 0x13, 0x00,
        0x05, 0x0A, 0x0D, 0x01, 0x0F,
        0x06, 0x04, 0x0B, 0x10, 0x03,
        0x09, 0x12, 0x0C, 0x07, 0x0E]
key = count & 0xFF
for each entry:
    for i in range(20):
        decoded[perm[i]] = encrypted[i] ^ key
        key = (key * 3 + 1) & 0xFF
```

数据区目前确认是直存，不需要解压或二次解密。

对样本 `MESCHN.ARC` 的验证结果：

- 条目数：`993`
- 目录区结束偏移：`0x00004D98`，即 `19864`
- 第一项：`01M.MES`，`size=27097`，`offset=19864`
- 数据区连续排列，最后一项结束偏移等于整个 ARC 文件大小

## 用法

列目录：

```bat
python ai5win_arc_extract.py list MESCHN.ARC
```

解包单个 ARC：

```bat
python ai5win_arc_extract.py extract MESCHN.ARC -o out --overwrite
```

批量解包：

```bat
python ai5win_arc_extract.py extract MESCHN.ARC BG.ARC BGM.ARC DATA.ARC -o arc_out --overwrite
```

批量时会在输出根目录下按 ARC 文件名建立子目录，例如：

```text
arc_out/
  MESCHN/
    01M.MES
    02C.MES
    ...
    _arc_manifest.json
```

## 文件说明

- `ai5win_arc_common.py`：ARC 结构解析、目录解码、边界校验、文件写出等共用逻辑。
- `ai5win_arc_extract.py`：命令行入口，支持 `list` 和 `extract`。
- `README_AI5_ARC.md`：反汇编定位说明和使用方法。
