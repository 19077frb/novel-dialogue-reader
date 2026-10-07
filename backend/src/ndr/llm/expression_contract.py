"""Explicitly selected expression-owner contract; legacy 1.0 stays unchanged."""

from typing import Any, Literal

from pydantic import Field, model_validator

from ..domain.enums import EXPRESSION_OWNER_KINDS, Assignment, QuoteKind, SpeakerBasis
from .schemas import LlmOutput, QuoteLabel

EXPRESSION_SCHEMA_VERSION = "1.1"
OWNER_KINDS = EXPRESSION_OWNER_KINDS


class ExpressionQuoteLabel(QuoteLabel):
    """An explicit owner is independent of the expression's original kind."""

    @model_validator(mode="after")
    def _check_assignment_consistency(self) -> "ExpressionQuoteLabel":
        if self.kind in OWNER_KINDS:
            if self.assignment is None or self.basis is None:
                raise ValueError("表达归属必须给出 assignment 和 basis")
            if self.assignment is Assignment.UNKNOWN:
                if (
                    self.speaker_ref is not None
                    or self.speaker_name is not None
                    or self.basis is not SpeakerBasis.INSUFFICIENT
                    or self.evidence_refs
                ):
                    raise ValueError("未知人物必须无人物引用、无人物证据且 basis=INSUFFICIENT")
            elif not self.speaker_ref:
                raise ValueError("明确表达归属必须给出 speaker_ref")
            elif self.basis is SpeakerBasis.INSUFFICIENT:
                raise ValueError("证据不足必须使用 UNKNOWN，不能声明已确定人物")
        elif any(
            value is not None
            for value in (self.assignment, self.speaker_ref, self.speaker_name, self.basis)
        ):
            raise ValueError("集体、其他及未知类型不能携带单个人物归属")
        return self


class ExpressionLlmOutput(LlmOutput):
    schema_version: Literal["1.1"] = EXPRESSION_SCHEMA_VERSION
    labels: list[ExpressionQuoteLabel] = Field(default_factory=list)


def has_owner_contract(label: QuoteLabel) -> bool:
    """Legacy speech or a versioned owner label; never upgrade historical labels."""
    return label.kind is QuoteKind.SPEECH or (
        isinstance(label, ExpressionQuoteLabel) and label.kind in OWNER_KINDS
    )


def expression_output_json_schema(*, known_character_ids: tuple[str, ...] = ()) -> dict[str, Any]:
    """Describe the same owner field relationships enforced at runtime."""
    schema = ExpressionLlmOutput.model_json_schema()
    label = schema["$defs"]["ExpressionQuoteLabel"]
    label["allOf"] = [
        {
            "if": {"properties": {"kind": {"enum": sorted(k.value for k in OWNER_KINDS)}}},
            "then": {
                "required": ["assignment", "basis"],
                "properties": {
                    "assignment": {"enum": [a.value for a in Assignment]},
                    "basis": {"enum": [b.value for b in SpeakerBasis]},
                },
                "allOf": [
                    {
                        "if": {"properties": {"assignment": {"const": "UNKNOWN"}}},
                        "then": {
                            "properties": {
                                "speaker_ref": {"type": "null"},
                                "speaker_name": {"type": "null"},
                                "basis": {"const": "INSUFFICIENT"},
                                "evidence_refs": {"maxItems": 0},
                            }
                        },
                        "else": {
                            "required": ["speaker_ref"],
                            "properties": {
                                "speaker_ref": {"type": "string", "minLength": 1},
                                "basis": {
                                    "enum": [
                                        b.value
                                        for b in SpeakerBasis
                                        if b is not SpeakerBasis.INSUFFICIENT
                                    ]
                                },
                            },
                        },
                    }
                ],
            },
            "else": {
                "properties": {
                    field: {"type": "null"}
                    for field in ("assignment", "speaker_ref", "speaker_name", "basis")
                }
            },
        }
    ]
    speaker = schema["$defs"]["NewSpeaker"]
    speaker["required"].append("name")
    named = {"type": "string", "minLength": 1, "maxLength": 32}
    speaker["properties"]["name"] = (
        named if not known_character_ids else {"anyOf": [named, {"type": "null"}]}
    )
    if known_character_ids:
        speaker["allOf"] = [
            {
                "if": {
                    "required": ["character_id"],
                    "properties": {
                        "character_id": {"enum": list(known_character_ids)},
                    },
                },
                "then": {"properties": {"evidence_refs": {"minItems": 1}}},
                "else": {"properties": {"name": named}},
            }
        ]
    return schema
