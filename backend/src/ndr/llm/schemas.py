"""§4.4 的模型输出契约（Pydantic 判别联合 + JSON Schema）。

要点：

- ``extra="forbid"``：模型不能塞进契约以外的字段（例如自报 ``visible_from_cp``）。
- 模型只输出**临时引用**（``new1``、``scene_current``），稳定 ID 由后端映射。
- 产出 JSON Schema 用于提示词与校验；不执行任何来自模型的代码。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from ..domain.common import ApiModel
from ..domain.enums import (
    Assignment,
    GapDecision,
    IdentityOperation,
    QuoteKind,
    SpeakerBasis,
)

OUTPUT_SCHEMA_VERSION = "1.0"


class SceneUpdate(ApiModel):
    """声明新场景：必须以一次 BREAK 决策为前置（见 validation）。"""

    temp_ref: str = Field(min_length=1, description="本次输出内的临时场景引用，如 scene_2")
    after_gap_id: str = Field(min_length=1)
    starts_at_quote_id: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class GapDecisionOut(ApiModel):
    gap_id: str = Field(min_length=1)
    decision: GapDecision
    evidence_refs: list[str] = Field(default_factory=list)


class NewSpeaker(ApiModel):
    character_id: str | None = Field(default=None, min_length=1)
    real_name: str | None = Field(default=None, max_length=32)
    aliases: list[str] = Field(default_factory=list, max_length=64)
    # Nullable only for parsing historical cached outputs. Live calls require a name.
    name: str | None = Field(default=None, min_length=1, max_length=32,
                             description="必填简短姓名或称呼，如浅村悠太、轻浮男客；描述另填")
    temp_ref: str = Field(min_length=1, description="本次输出内的临时人物引用，如 new1")
    scene_ref: str = Field(min_length=1)
    first_quote_id: str = Field(min_length=1)
    description: str = Field(
        min_length=1,
        max_length=512,
        description="人物身份、特征和匹配依据的详细说明，不作为显示名称",
    )
    evidence_refs: list[str] = Field(default_factory=list)


class QuoteLabel(ApiModel):
    quote_id: str = Field(min_length=1)
    scene_ref: str = Field(min_length=1)
    kind: QuoteKind
    assignment: Assignment | None = None
    speaker_ref: str | None = None
    speaker_name: str | None = Field(
        default=None,
        max_length=128,
        description="仅在原文明示真实姓名时填写；不确定时为 null",
    )
    basis: SpeakerBasis | None = None
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_assignment_consistency(self) -> QuoteLabel:
        if self.kind is QuoteKind.SPEECH:
            if self.assignment is None:
                raise ValueError("kind=speech 必须给出 assignment")
            if self.basis is None:
                raise ValueError("kind=speech 必须给出 basis")
            if self.assignment is Assignment.UNKNOWN:
                if self.speaker_ref is not None:
                    raise ValueError("assignment=UNKNOWN 时 speaker_ref 必须为 null")
                if self.speaker_name is not None:
                    raise ValueError("assignment=UNKNOWN 时 speaker_name 必须为 null")
            elif not self.speaker_ref:
                raise ValueError("assignment=EXISTING/NEW 时必须给出 speaker_ref")
        else:
            if self.assignment is not None:
                raise ValueError("非 speech 的 assignment 必须为 null")
            if self.speaker_ref is not None:
                raise ValueError("非 speech 的 speaker_ref 必须为 null")
            if self.speaker_name is not None:
                raise ValueError("非 speech 的 speaker_name 必须为 null")
        return self


class IdentityProposal(ApiModel):
    """merge/split 候选：只提出建议，不直接改数据库。"""

    operation: IdentityOperation
    input_refs: list[str] = Field(min_length=1)
    output_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_arity(self) -> IdentityProposal:
        if self.operation is IdentityOperation.MERGE:
            if len(self.input_refs) < 2:
                raise ValueError("merge 至少需要两个输入分组")
            if len(self.output_refs) > 1:
                raise ValueError("merge 最多产出一个分组")
        else:  # SPLIT
            if len(self.input_refs) != 1:
                raise ValueError("split 只能指定一个输入分组")
            if len(self.output_refs) < 2:
                raise ValueError("split 至少产出两个分组")
        return self


class LlmOutput(ApiModel):
    """一次模型调用的完整逻辑输出。"""

    schema_version: Literal["1.0"] = OUTPUT_SCHEMA_VERSION
    scene_updates: list[SceneUpdate] = Field(default_factory=list)
    gap_decisions: list[GapDecisionOut] = Field(default_factory=list)
    new_speakers: list[NewSpeaker] = Field(default_factory=list)
    labels: list[QuoteLabel] = Field(default_factory=list)
    identity_proposals: list[IdentityProposal] = Field(default_factory=list)
    needs_context: list[str] = Field(default_factory=list)


def output_json_schema() -> dict[str, Any]:
    """导出 JSON Schema（提示词与文档用；不带 ``$defs`` 内联引用问题）。"""

    schema = LlmOutput.model_json_schema(ref_template="#/$defs/{model}")
    speaker = schema["$defs"]["NewSpeaker"]
    speaker["required"].append("name")
    speaker["properties"]["name"] = {
        "type": "string", "minLength": 1, "maxLength": 32,
        "description": "简短姓名或称呼；不能是编号或描述句",
    }
    return schema


class RosterCharacter(ApiModel):
    """A candidate character proposed for one chapter."""

    temp_ref: str = Field(min_length=1, max_length=64)
    character_id: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, max_length=128)
    real_name: str | None = Field(default=None, max_length=32,
                                 description="原文明示的真实姓名；只有代称或身份称呼时为null")
    aliases: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=512)
    evidence_refs: list[str] = Field(default_factory=list)
    pov_candidate: bool = False


class RosterOutput(ApiModel):
    """Structured output for the chapter character-roster analysis pass."""

    schema_version: Literal["1.0"] = OUTPUT_SCHEMA_VERSION
    characters: list[RosterCharacter] = Field(default_factory=list)
