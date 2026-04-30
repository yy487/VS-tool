# 雪之丞2error

文本提取与注入工具目录，通常用于脚本翻译的导出、翻译回写与回归验证。

本 README 为目录补充说明，便于后续维护、迁移和复用。

## 文件说明

| 文件 | 说明 |
|---|---|
| `ai5win_arc_tool.py` | 封包解析/解包/重打包工具；AI5WIN v3 ARC 解包/封包工具 (あしたの雪之丞2) |
| `ai5win_mes_extract.py` | 文本或资源提取脚本；AI5WIN v2 MES 文本提取工具 v4 (あしたの雪之丞2) |
| `ai5win_mes_inject.py` | 文本注入脚本；AI5WIN v2 MES 文本注入工具 v4 (あしたの雪之丞2) |
| `AI5WIN_v3_汉化傻瓜教程.docx` | 逆向分析/使用说明文档 |
| `font_gen.py` | 字体/字符集处理工具；AI5WIN v3 字体生成器 (あしたの雪之丞2) |
| `img_msk_tool.py` | 图像或素材格式处理工具；AI5WIN 图片 MSK 工具 (あしたの雪之丞2) |
| `msk_scan.py` | 图像或素材格式处理工具；扫描 ARC 解包目录, 分类所有 MSK 文件为 Type A / Type B |
| `patch_exe.py` | EXE/运行时补丁辅助脚本；patch Ai5win.exe 字体缓冲区 (混合 cp932+GBK, 6189 glyphs) |
| `scan_chars.py` | 扫描/统计辅助脚本；扫描 GalTransl JSON 中所有用到的字符，输出字符集 JSON。 |

## 常见流程

典型文本流程如下，实际参数以脚本内 `argparse` / 文件头注释为准：

```bash
python ai5win_mes_extract.py <原始脚本或目录> <导出json或目录>
# 翻译/修改导出的 JSON
python ai5win_mes_inject.py <原始脚本或目录> <翻译json或目录> <输出脚本或目录>
```

建议先用未修改的 JSON 做一次 round-trip：提取后立刻注入，并比对输出与原文件是否一致。

如需先处理封包/归档文件，可查看这些脚本的参数：`ai5win_arc_tool.py`。

## 注意事项

- 本仓库脚本大多是特定游戏/特定版本适配，跨作品复用前需要重新核对文件头、索引表、指令格式和编码。
- 文本编码通常与原游戏运行时有关，常见为 CP932/SJIS；写入中文前需要确认补丁、Hook、字体或码表映射方案。
- 处理前保留原始文件备份；注入后建议进行二进制比对、游戏内实机检查和异常文本回查。
- 若脚本会重建封包或重排文本区，务必确认 offset、长度字段、压缩块大小和校验字段是否同步更新。
