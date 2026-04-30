# Pure My 妹ミルクぷるん♪ 有壳失败告终

文本提取与注入工具目录，通常用于脚本翻译的导出、翻译回写与回归验证。

本 README 为目录补充说明，便于后续维护、迁移和复用。

## 文件说明

| 文件 | 说明 |
|---|---|
| `bcs_extract.py` | 文本或资源提取脚本；bcs_extract.py - Tanuki Soft / Kaeru Soft / Rune .bcs 文本提取器 |
| `bcs_inject.py` | 文本注入脚本；bcs_inject.py - Tanuki Soft / Kaeru Soft / Rune .bcs 文本注入器 |
| `bcs_lzss.py` | 编解码、压缩或加密辅助模块；bcs_lzss.py - Tanuki/Kaeru/Rune .bcs 用的 LZSS 编解码 |
| `g2_pack.py` | 封包重建/打包脚本；g2_pack.py - Glib2 .g2 / .stx 资源包封包器 |
| `g2_unpack.py` | 封包重建/打包脚本；g2_unpack.py - Glib2 .g2 / .stx 资源包解包器 |

## 常见流程

典型文本流程如下，实际参数以脚本内 `argparse` / 文件头注释为准：

```bash
python bcs_extract.py <原始脚本或目录> <导出json或目录>
# 翻译/修改导出的 JSON
python bcs_inject.py <原始脚本或目录> <翻译json或目录> <输出脚本或目录>
```

建议先用未修改的 JSON 做一次 round-trip：提取后立刻注入，并比对输出与原文件是否一致。

如需先处理封包/归档文件，可查看这些脚本的参数：`g2_pack.py, g2_unpack.py`。

## 注意事项

- 本仓库脚本大多是特定游戏/特定版本适配，跨作品复用前需要重新核对文件头、索引表、指令格式和编码。
- 文本编码通常与原游戏运行时有关，常见为 CP932/SJIS；写入中文前需要确认补丁、Hook、字体或码表映射方案。
- 处理前保留原始文件备份；注入后建议进行二进制比对、游戏内实机检查和异常文本回查。
- 若脚本会重建封包或重排文本区，务必确认 offset、长度字段、压缩块大小和校验字段是否同步更新。
