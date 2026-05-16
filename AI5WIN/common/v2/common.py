# -*- coding: utf-8 -*-
"""AI5WIN v2 共用入口。

本目录为自包含版本：即使只复制 v2/ 目录，也能找到 core/story_common.py。
"""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# 优先使用本版本目录下的 core，保证 v0/v1/v2 可以单独复制运行；
# 如果不存在，再回退到项目根目录的 core。
# 注意：使用 insert(0) 时，后插入的路径优先级更高，所以这里先放 ROOT，再放 HERE。
for candidate in (ROOT / "core", HERE / "core"):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

try:
    from story_common import *  # noqa: F401,F403
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "找不到 story_common.py。请确认当前目录结构为：v2/common.py 与 v2/core/story_common.py 同时存在，"
        "或者项目根目录下存在 core/story_common.py。"
    ) from e

VERSION = 2
