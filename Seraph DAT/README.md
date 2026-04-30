# Seraph DAT

封包/资源格式处理目录，包含解包、打包或格式转换脚本。

本 README 为目录补充说明，便于后续维护、迁移和复用。

## 文件说明

| 文件 | 说明 |
|---|---|
| `cf_decode.py` | 工具脚本；Seraph Engine - CF/CT/CB/CC Image Decoder |
| `seraph_dat_tool_v2.py` | 封包解析/解包/重打包工具；Seraph Engine - ArchPac.dat Unpacker v2 |
| `Seraph_Full_Analysis.docx` | 逆向分析/使用说明文档 |
| `Seraph_WAGAMAJO_Analysis.docx` | 逆向分析/使用说明文档 |

## 常见流程

该目录以封包/资源处理为主。常用流程是先解包、替换资源或文本后再重建：

```bash
python seraph_dat_tool_v2.py --help
```

## 注意事项

- 本仓库脚本大多是特定游戏/特定版本适配，跨作品复用前需要重新核对文件头、索引表、指令格式和编码。
- 处理前保留原始文件备份；注入后建议进行二进制比对、游戏内实机检查和异常文本回查。
- 若脚本会重建封包或重排文本区，务必确认 offset、长度字段、压缩块大小和校验字段是否同步更新。
