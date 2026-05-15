# AGSI SB2 简化工具

这版只保留两个大模块：

1. `agsi_sb_tool.py`：结构级解包 / 封包。
2. `agsi_cstr_codec.py`：`CSTR.bin` 原始二进制解码 / 编码。

已经删除文本 JSON 提取、文本注入相关脚本。

## 一、结构解包

```bat
python agsi_sb_tool.py unpack majo2.sb dump_majo2 --overwrite
```

输出：

```text
dump_majo2/
├─ header.bin
├─ manifest.json
├─ CODE.bin
├─ TTBL.bin
├─ FTBL_0.bin
├─ FTBL_1.bin
├─ VTBL.bin
├─ CSTR.bin
├─ CDBL.bin
├─ DBG_0.bin
└─ DBG_1.bin
```

这里每个 `.bin` 都是不含 4 字节 tag 的段 payload。

## 二、解码 CSTR.bin

```bat
python agsi_cstr_codec.py decode dump_majo2
```

得到：

```text
dump_majo2/CSTR_decode.bin
```

`CSTR_decode.bin` 结构不变：

```text
CSTR_decode.bin
├─ offset / size 表，保持原样
└─ 明文 CP932 字符串池
```

所以可以直接用 WinHex / 010 Editor 看字符串池。

注意：字符串池不是从文件开头开始。以 `majo2.sb` 为例，`CSTR` 条目数是 `75527`，所以前面的 offset/size 表大小是：

```text
75527 * 8 = 604216 = 0x93838
```

明文字符串池从 `CSTR_decode.bin + 0x93838` 开始。

## 三、编码回 CSTR.bin

如果只是检查可逆性，直接执行：

```bat
python agsi_cstr_codec.py encode dump_majo2 --overwrite
```

它会读取：

```text
dump_majo2/CSTR_decode.bin
```

然后重新生成：

```text
dump_majo2/CSTR.bin
```

编码动作只对字符串池做 nibble swap，前面的 offset/size 表保持原样。

## 四、封包回 .sb

```bat
python agsi_sb_tool.py pack dump_majo2 majo2_repack.sb --compare-original majo2.sb
```

如果没有改动，应该得到：

```json
"byte_equal": true
```

## 五、完整可逆测试流程

```bat
python agsi_sb_tool.py unpack majo2.sb dump_majo2 --overwrite
python agsi_cstr_codec.py decode dump_majo2
python agsi_cstr_codec.py encode dump_majo2 --overwrite
python agsi_sb_tool.py pack dump_majo2 majo2_repack.sb --compare-original majo2.sb
```

## 六、CSTR 编码说明

`CSTR.bin` 的字符串池不是明文，每个字节做了高低 4 bit 交换：

```python
decoded = ((b >> 4) | ((b & 0x0F) << 4)) & 0xFF
```

该操作自反，所以 decode 和 encode 用同一个变换。
