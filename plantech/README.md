# plantech

引擎/项目集合目录，按具体作品或格式拆分为多个子目录。

本 README 为目录补充说明，便于后续维护、迁移和复用。

## 子目录

| 子目录 | 说明 |
|---|---|
| `Rumble ～バンカラ夜叉姫～/` | 文本提取与注入工具目录，通常用于脚本翻译的导出、翻译回写与回归验证。 关键文件：plantech_msg_extract.py, plantech_msg_inject.py, plantech_pac_tool.py |
| `点心铺/` | 文本提取与注入工具目录，通常用于脚本翻译的导出、翻译回写与回归验证。 关键文件：plantech_msg_extract_v4.py, plantech_msg_inject_v4.py, plantech_pac_tool.py |
| `百花缭乱/` | 文本提取与注入工具目录，通常用于脚本翻译的导出、翻译回写与回归验证。 关键文件：plantech_msg_extract_v3.py, plantech_msg_inject_v3.py |

## 常见流程

当前目录没有可直接执行的 Python 脚本；请优先阅读目录内报告、教程或归档说明。

## 注意事项

- 本仓库脚本大多是特定游戏/特定版本适配，跨作品复用前需要重新核对文件头、索引表、指令格式和编码。
- 处理前保留原始文件备份；注入后建议进行二进制比对、游戏内实机检查和异常文本回查。
