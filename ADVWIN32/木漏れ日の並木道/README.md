# ADVWIN32/木漏れ日の並木道

封包/资源格式处理目录，包含解包、打包或格式转换脚本。

本 README 为目录补充说明，便于后续维护、迁移和复用。

## 文件说明

| 文件 | 说明 |
|---|---|
| `ADVWIN32_Analysis.docx` | 逆向分析/使用说明文档 |
| `ADVWIN32_Analysis_v2.docx` | 逆向分析/使用说明文档 |
| `ADVWIN32_RE_Report.md` | 逆向分析/使用说明文档 |
| `mcg2png.py` | 图像或素材格式处理工具；mcg2png.py - ADVWIN32 MCG image decoder |
| `mrg_unpack.py` | 封包重建/打包脚本；mrg_unpack.py - ADVWIN32 / F&C Co. MRG archive unpacker |

## 常见流程

该目录以封包/资源处理为主。常用流程是先解包、替换资源或文本后再重建：

```bash
python mrg_unpack.py --help
```

## 注意事项

- 本仓库脚本大多是特定游戏/特定版本适配，跨作品复用前需要重新核对文件头、索引表、指令格式和编码。
- 处理前保留原始文件备份；注入后建议进行二进制比对、游戏内实机检查和异常文本回查。
- 若脚本会重建封包或重排文本区，务必确认 offset、长度字段、压缩块大小和校验字段是否同步更新。
