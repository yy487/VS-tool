# -*- coding: utf-8 -*-
"""AI6WIN v0 共用入口。"""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# 优先使用本版本目录下的 core；若不存在，再使用项目根目录 core。
for candidate in (ROOT / "core", HERE / "core"):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

try:
    from story_common import *  # noqa: F401,F403
except ModuleNotFoundError as e:
    raise ModuleNotFoundError(
        "找不到 story_common.py。请确认目录结构包含 v0/common.py 与 core/story_common.py，"
        "或者 v0/core/story_common.py。"
    ) from e

VERSION = 0
