"""Opt-in role-evidence ablation, never enabled by production configuration."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

ROLE_CUE_VERSION = "dialogue-role-cues-1"
ROLE_CUE_POLICY = """
归属前在内部核对对话角色，不增加JSON字段或输出推理过程：
1. 区分正在对话的参与者、旁观者、被观察的人、被谈论的人和被称呼的对象。
   叙述者观看某人的衣着、动作或表情，不表示该人接替原来的说话人。
   延续已有谈话的参与者；只有可见原文表明加入/离开/换人，才调整参与者。
2. 不按ABAB机械轮流。同一人可能连续追问、补充或吐槽，中间另一人只是动作、
   表情或沉默。逐句检查称呼、提问对象和回答内容，不能因相邻而强行换人。
3. 在引用动作旁白作证前，核对它解释的是前一句还是引出后一句。例如对白后
   的“她指着……说道”可能补充刚才那句；不能将该动作自动绑定下一条对白。
   隔句的回复或提问也可能构成证据，结合当前可见的完整问答链判断。
4. 如果随后有人追问“你刚才说什么”，检查前句是否实际出声，而非仅因第一人称
   叙述就判为内心话；同样不能把假想、引述、未说出口的话强行归为发声。
5. 仅使用输入中当前可见的原文和人物引用，不用未来情节、作品记忆或身份刻板印象。
   无充分依据时保留未知；证据必须确实支持该句及该人，不为填满标签伪造证据。
""".strip()


def role_cue_fingerprint(source_fingerprint: str) -> str:
    """Scope cached requests to both policy content and its declared version."""
    return hashlib.sha256(
        json.dumps(
            [ROLE_CUE_VERSION, ROLE_CUE_POLICY, source_fingerprint], ensure_ascii=False
        ).encode()
    ).hexdigest()


class RoleCueAdapter:
    """Wrap a journaled adapter so the *modified* request is accounted/cached."""

    def __init__(self, adapter):
        self.adapter = adapter

    async def generate_labels(self, request: dict) -> dict:
        messages = request.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("Role cues require an explicit system message")
        if any(
            not isinstance(row, dict) or not isinstance(row.get("content"), str) for row in messages
        ):
            raise ValueError("Role cues only accept text messages")
        system = [i for i, row in enumerate(messages) if row.get("role") == "system"]
        if len(system) != 1 or system[0] != 0:
            raise ValueError("Role cues require exactly one leading system message")
        prepared = deepcopy(request)
        prepared["messages"][0]["content"] += "\n\n" + ROLE_CUE_POLICY
        return await self.adapter.generate_labels(prepared)
