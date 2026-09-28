"""连接测试提示词（DEVELOPMENT.md 5.4）。

用**微型结构化任务**同时检查鉴权与输出可解析：要求模型原样回显一个固定的空结果对象。
这验证的是协议与 JSON 模式是否可用，**不代表**小说识别效果（效果评测属 T16）。
"""

from __future__ import annotations

import json

CONNECTION_PROMPT_VERSION = "connection-2"

ECHO_OBJECT = {
    "schema_version": "1.0",
    "scene_updates": [],
    "gap_decisions": [],
    "new_speakers": [],
    "labels": [],
    "identity_proposals": [],
    "needs_context": [],
}

SYSTEM_PROMPT = (
    "你是协议自检助手。任务：原样输出给定的 JSON 对象，不要添加解释、注释或额外字段，"
    "不要使用 markdown 代码块，也不要在 JSON 前后写任何文字。"
)

USER_PROMPT = "请原样输出下面这个 JSON 对象：\n" + json.dumps(ECHO_OBJECT, ensure_ascii=False)


def build_connection_messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT},
    ]
