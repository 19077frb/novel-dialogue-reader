"""轻小说对话辅助阅读器后端包。

实现规格见仓库根目录的 DEVELOPMENT.md。
"""

from __future__ import annotations

__all__ = ["__version__", "API_VERSION"]

__version__ = "1.0.0"

# 公共 HTTP 契约版本；与 DEVELOPMENT.md 第 5 节一起演进。
API_VERSION = "1"
