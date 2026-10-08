"""Owner schema guidance and strict parsing; not provider-enforced semantics."""

import hashlib
import json

from .expression_owner import ExplicitOwnerProtocol

VERSION = "explicit-owner-null-field-constraints-3"


class ConstrainedOwnerProtocol(ExplicitOwnerProtocol):
    def __init__(self, task):
        super().__init__(task)
        definition = self.schema["$defs"]["OwnerLabel"]
        definition["allOf"] = [
            {
                "if": {"properties": {"character": {"const": None}}, "required": ["character"]},
                "then": {
                    "properties": {"basis": {"const": "insufficient"}, "evidence": {"maxItems": 0}}
                },
                "else": {"properties": {"character": {"type": "string", "minLength": 1}}},
            }
        ]
        auxiliary = getattr(task, "auxiliary_protocol", None)
        if auxiliary is not None:
            from ..llm.expression_diagnostics import DIAGNOSTICS_VERSION

            if auxiliary != DIAGNOSTICS_VERSION:
                raise ValueError("Unsupported auxiliary isolation version")
            refs = [c.ref for c in task.candidates]
            definition["properties"].update(
                {
                    "addressee": {"anyOf": [{"type": "string", "enum": refs}, {"type": "null"}]}
                    if refs
                    else {"type": "null"},
                    "addressee_evidence": {
                        "type": "array",
                        "maxItems": 64,
                        "uniqueItems": True,
                        "items": {"$ref": "#/$defs/OriginalEvidenceReference"},
                    },
                    "owner_depends_on_addressee": {"type": "boolean"},
                }
            )
            self.system = self.system.replace(
                "无需受话对象或其他辅助字段。",
                "无需额外生成受话对象；仅有必要时可提供声明的可选辅助字段。"
                "主归属确实依赖受话关系时owner_depends_on_addressee=true，否则false。"
                "辅助证据也只能引用实际提供的非空原文，不改变主人物依据。",
            )
        self.system = (
            self.system.rsplit("\n", 1)[0]
            + (
                "人物evidence只表达人物归属依据，不是判定kind的依据。"
                "人物原话来源不明仍可判quotation，character=null时basis=insufficient、evidence=[]；"
                "术语、标题和注释等非人物文本判other，不要求人物。\n"
            )
            + json.dumps(self.schema, ensure_ascii=False)
        )
        if any(p.get("identity_records") for p in getattr(task, "effective_profiles", ())):
            self.system += (
                "\n人物资料identity_records说明当前可见字段的来源；source=model仍是模型候选，"
                "profile_update是资料修订，不是原文引文或说话人证据。"
                "历史evidence_spans仅说明来源位置，未在context发送的原文不得引用。"
                "资料或POV候选不能压倒当前原文；先核对已有C的称呼与具体关系，"
                "确有名单外人物才声明有证据的新N，"
                "不得仅凭同名或泛称合并人物。"
            )

    def fingerprint(self):
        return hashlib.sha256(
            json.dumps(
                [VERSION, self.task.fingerprint(), self.messages()],
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def compile(self, payload):
        result = super().compile(payload)
        result["protocol_version"] = VERSION
        return result
