"""版本化提示模板（T07）。

提示模板与 schema 一样是**版本化产物**：改动会影响缓存键与评测复现，必须提升版本号。
小说正文一律作为**数据**传入（放在带标记的块里并转义标记本身），不拼进指令。
"""

from __future__ import annotations

from .connection import CONNECTION_PROMPT_VERSION, build_connection_messages
from .labeling import (
    DATA_DELIMITER,
    LABELING_PROMPT_VERSION,
    build_labeling_messages,
    escape_data_markers,
)

__all__ = [
    "CONNECTION_PROMPT_VERSION",
    "DATA_DELIMITER",
    "LABELING_PROMPT_VERSION",
    "build_connection_messages",
    "build_labeling_messages",
    "escape_data_markers",
]
