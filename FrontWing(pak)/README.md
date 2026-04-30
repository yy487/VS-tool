# FrontWing(pak)

文本提取与注入工具目录，通常用于脚本翻译的导出、翻译回写与回归验证。

本 README 为目录补充说明，便于后续维护、迁移和复用。

## 子目录

| 子目录 | 说明 |
|---|---|
| `pak tool仅封包/` | 占位目录，当前未包含可直接执行的脚本或文档。 关键文件：无 Python 脚本 |

## 文件说明

| 文件 | 说明 |
|---|---|
| `csb_extract.py` | 文本或资源提取脚本；csb_extract.py - Frontwing ADV CSB 文本提取 |
| `csb_inject.py` | 文本注入脚本；csb_inject.py - Frontwing ADV CSB 文本注入 |
| `pak_tool.py` | 封包解析/解包/重打包工具；pak_tool.py - Frontwing ADV Engine PAK Archive Tool |

## 常见流程

典型文本流程如下，实际参数以脚本内 `argparse` / 文件头注释为准：

```bash
python csb_extract.py <原始脚本或目录> <导出json或目录>
# 翻译/修改导出的 JSON
python csb_inject.py <原始脚本或目录> <翻译json或目录> <输出脚本或目录>
```

建议先用未修改的 JSON 做一次 round-trip：提取后立刻注入，并比对输出与原文件是否一致。

如需先处理封包/归档文件，可查看这些脚本的参数：`pak_tool.py`。

## 注意事项

- 本仓库脚本大多是特定游戏/特定版本适配，跨作品复用前需要重新核对文件头、索引表、指令格式和编码。
- 处理前保留原始文件备份；注入后建议进行二进制比对、游戏内实机检查和异常文本回查。
- 若脚本会重建封包或重排文本区，务必确认 offset、长度字段、压缩块大小和校验字段是否同步更新。
