# AI5WIN

引擎/项目集合目录，按具体作品或格式拆分为多个子目录。

本 README 为目录补充说明，便于后续维护、迁移和复用。

## 子目录

| 子目录 | 说明 |
|---|---|
| `BE-YOND/` | 文本提取与注入工具目录，通常用于脚本翻译的导出、翻译回写与回归验证。 关键文件：ai5winv1_arc_tool.py, ai5winv1_mes_extract.py, ai5winv1_mes_inject.py |
| `らいむいろ戦奇譚/` | 文本提取与注入工具目录，通常用于脚本翻译的导出、翻译回写与回归验证。 关键文件：lime_arc.py, lime_extract.py, lime_inject.py |
| `らいむいろ流奇譚X cross～恋、教ヘテクダサイ。～/` | 文本提取与注入工具目录，通常用于脚本翻译的导出、翻译回写与回归验证。 关键文件：arc_tool.py, mes_extract.py, mes_inject.py |
| `ドラゴンナイト4 Windows版/` | 文本提取与注入工具目录，通常用于脚本翻译的导出、翻译回写与回归验证。 关键文件：ai5winv4_arc_tool.py, ai5winv4_mes_extract.py, ai5winv4_mes_inject.py |
| `女系家族～淫謀～/` | 文本提取与注入工具目录，通常用于脚本翻译的导出、翻译回写与回归验证。 关键文件：ai5v6_bytecode.py, ai5v6_codec.py, ai5winv6_arc_tool.py ... |
| `愛しの言霊/` | 文本提取与注入工具目录，通常用于脚本翻译的导出、翻译回写与回归验证。 关键文件：ai5v7_bytecode_v2.py, ai5winv7_arc_tool.py, ai5winv7_mes_extract.py ... |
| `百鬼/` | 文本提取与注入工具目录，通常用于脚本翻译的导出、翻译回写与回归验证。 关键文件：ai5win_arc_tool.py, ai5win_hyakki_mes_codec.py, ai5win_hyakki_mes_extract.py ... |

## 常见流程

当前目录没有可直接执行的 Python 脚本；请优先阅读目录内报告、教程或归档说明。

## 注意事项

- 本仓库脚本大多是特定游戏/特定版本适配，跨作品复用前需要重新核对文件头、索引表、指令格式和编码。
- 处理前保留原始文件备份；注入后建议进行二进制比对、游戏内实机检查和异常文本回查。
