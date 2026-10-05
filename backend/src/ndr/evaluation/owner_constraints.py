"""Opt-in schema guidance; not a provider-enforced or production protocol."""

import hashlib
import json

from .expression_owner import ExplicitOwnerProtocol

VERSION = "explicit-owner-null-field-constraints-2"


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
        self.system = (
            self.system.rsplit("\n", 1)[0]
            + (
                "人物evidence只表达人物归属依据，不是判定kind的依据。"
                "无明确原表达者的引文仍可以判为quotation，但character=null时basis=insufficient、evidence=[]。\n"
            )
            + json.dumps(self.schema, ensure_ascii=False)
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
