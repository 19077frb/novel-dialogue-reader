"""Opt-in type/evidence semantics ablation; no forced labels or product defaults."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

TYPE_BASIS_VERSION = "type-basis-cues-1"
TYPE_ACTIVITY_VERSION = "speech-activity-cues-1"
TYPE_ACTIVITY_POLICY = """
类型由发声事件判断，不由引号位置或排版判断：
speech是人物实际发声，包含自言自语。独处、第一人称、没人回应或没听清，
不能单独证明thought，也不能单独证明已经发声；thought须有未说出口的心理依据。
叙述明确说出、说着或喊出的台词，即使嵌在叙述内、连列多个引号，也不能仅因
被叙述引用就改成quotation。quotation用于引用措辞、术语或没有实际发声的假想话术；
准备说但没说的内容不能当成已发声。核对前后反应是否真正针对这句话，
省略号可表示沉默。证据不足保留unknown，不强判人物，不改变说话依据或编号。
""".strip()
TYPE_BASIS_POLICY = """
类型与证据类别先按可见原文区分，不增加JSON字段或输出推理过程：
1. speech表示故事里实际发声，自言自语也可以是speech。第一人称叙述、独处、
   没有人回应或对方没有听清，都不能单独证明是thought；也不能据此强判已发声。
   thought需要原文支持未说出口的心理活动。引用措辞、假想话术、准备说但尚未说的
   内容，不能当作已经发生的发声；结合具体语境区分quotation、thought或unknown。
2. 对话后的追问、复述或反应可能证明前句实际出声，但要核对反应确实针对该句，
   不能把无关动作和相邻位置当作证明。省略号也可能表示沉默，不自动分配发声人物。
3. direct必须有目标以外的原文，将这个人物与当前这句明确发声联系起来；仅有
   名字、动作、前一句台词或主题相关，不能冒充直接开口依据。若依据是同人延续、
   人称指代或省略主语，检查原文指代关系并用coreference；若依据是针对谁的提问
   及回答，用response_link并引用关联对白。不机械轮流，不能仅改basis就变可信。
4. 每种证据都必须真正支持该句及该人物，不用作品记忆、未来信息、风格刻板印象。
   无法确定时保留unknown或character=null；非speech仍只有q/kind，不能添加人物、
   basis、evidence或说明字段。程序负责状态和编号，不在输出中自行修补人物目录。
""".strip()


def type_basis_fingerprint(source_fingerprint: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [TYPE_BASIS_VERSION, TYPE_BASIS_POLICY, source_fingerprint], ensure_ascii=False
        ).encode()
    ).hexdigest()


def type_activity_fingerprint(source_fingerprint: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [TYPE_ACTIVITY_VERSION, TYPE_ACTIVITY_POLICY, source_fingerprint], ensure_ascii=False
        ).encode()
    ).hexdigest()


class TypeBasisCueAdapter:
    """Wrap a journaled adapter; actual modified requests are cached/accounted."""

    def __init__(self, adapter):
        self.adapter = adapter

    @property
    def policy(self) -> str:
        return TYPE_BASIS_POLICY

    async def generate_labels(self, request: dict) -> dict:
        messages = request.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("Type/basis cues require explicit text messages")
        if any(
            not isinstance(row, dict) or not isinstance(row.get("content"), str) for row in messages
        ):
            raise ValueError("Type/basis cues only accept text messages")
        systems = [i for i, row in enumerate(messages) if row.get("role") == "system"]
        if systems != [0]:
            raise ValueError("Type/basis cues require exactly one leading system message")
        prepared = deepcopy(request)
        prepared["messages"][0]["content"] += "\n\n" + self.policy
        return await self.adapter.generate_labels(prepared)


class SpeechActivityCueAdapter(TypeBasisCueAdapter):
    """Separate type-only ablation, without the basis rules of the original."""

    @property
    def policy(self) -> str:
        return TYPE_ACTIVITY_POLICY
