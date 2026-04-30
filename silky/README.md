# silky

文本提取与注入工具目录，通常用于脚本翻译的导出、翻译回写与回归验证。

本 README 为目录补充说明，便于后续维护、迁移和复用。

## 文件说明

| 文件 | 说明 |
|---|---|
| `silky_extract.py` | 文本或资源提取脚本；silky_extract.py — Silky MES op.txt -> translate.txt 文本提取。 |
| `silky_inject.py` | 文本注入脚本；silky_inject.py — Silky MES translate.txt + op.txt -> 新 op.txt 译文注入。 |
| `silky_op.py` | 脚本字节码/Opcode 分析模块；silky_op.py — Silky engine MES script <-> opcode txt 双向转换。 |
| `silky_pipeline.py` | 批处理/流程整合脚本；silky_pipeline.py — Silky MES 一站式批量处理（多进程并行）。 |

## 常见流程

典型文本流程如下，实际参数以脚本内 `argparse` / 文件头注释为准：

```bash
python silky_extract.py <原始脚本或目录> <导出json或目录>
# 翻译/修改导出的 JSON
python silky_inject.py <原始脚本或目录> <翻译json或目录> <输出脚本或目录>
```

建议先用未修改的 JSON 做一次 round-trip：提取后立刻注入，并比对输出与原文件是否一致。

## 注意事项

- 本仓库脚本大多是特定游戏/特定版本适配，跨作品复用前需要重新核对文件头、索引表、指令格式和编码。
- 处理前保留原始文件备份；注入后建议进行二进制比对、游戏内实机检查和异常文本回查。
- 若脚本会重建封包或重排文本区，务必确认 offset、长度字段、压缩块大小和校验字段是否同步更新。
