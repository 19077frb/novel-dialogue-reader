"""Versioned production roster proposals bound to the exact original request."""

from typing import Literal

from pydantic import Field

from ..characters.facts import CharacterIdentityFact, OriginalIdentitySnapshot
from ..characters.names import valid_display_name
from ..domain.common import ApiModel
from .schemas import RosterCharacter, RosterOutput

SOURCED_ROSTER_VERSION = "sourced-roster-1"
PERSONAL_PRONOUNS = frozenset({"我", "你", "您", "他", "她", "它", "我们", "咱们", "你们",
                              "他们", "她们", "它们", "自己", "本人"})


class RosterFactProposal(ApiModel):
    kind: Literal["name", "alias", "designation", "description", "relation"]
    value: str = Field(min_length=1, max_length=512)
    evidence_refs: list[str] = Field(min_length=1, max_length=64)


class SourcedRosterCharacter(RosterCharacter):
    facts: list[RosterFactProposal] = Field(min_length=1, max_length=256)
    pov_evidence_refs: list[str] = Field(default_factory=list, max_length=64)


class SourcedRosterOutput(RosterOutput):
    schema_version: Literal["1.1"] = "1.1"
    characters: list[SourcedRosterCharacter] = Field(max_length=10000)


def original_lines(text, start, end):
    if type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(text):
        raise ValueError("人物分析原文范围无效")
    lines, position = {}, start
    for index, raw in enumerate(text[start:end].splitlines(keepends=True), 1):
        body = raw.rstrip("\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029")
        lines[f"L{index}"] = (position, position + len(body), body)
        position += len(raw)
    return lines


def compile_sourced_roster(payload, *, original: OriginalIdentitySnapshot, chapter_start,
                          chapter_end, allowed_character_ids, source_ref):
    output = SourcedRosterOutput.model_validate(payload)
    lines = original_lines(original.text, chapter_start, chapter_end)
    refs = [person.temp_ref for person in output.characters]
    ids = [person.character_id for person in output.characters if person.character_id]
    if len(set(refs)) != len(refs) or len(set(ids)) != len(ids):
        raise ValueError("人物引用重复，请将同一身份的事实放在同一人物块中")

    def proof(references):
        if not references or len(set(references)) != len(references):
            raise ValueError("人物证据不能为空或重复")
        if any(ref not in lines or not lines[ref][2].strip() for ref in references):
            raise ValueError("人物证据引用了未发送或空白的原文行")
        return tuple((lines[ref][0], lines[ref][1]) for ref in references)

    compiled = {}
    for person in output.characters:
        if person.character_id and person.character_id not in allowed_character_ids:
            raise ValueError("人物引用了未提供的全书人物 ID")
        association = proof(person.evidence_refs)
        if person.pov_candidate:
            proof(person.pov_evidence_refs)
        elif person.pov_evidence_refs:
            raise ValueError("非视角候选不能填写视角证据")
        names = {f.value for f in person.facts if f.kind in {"name", "designation"}}
        aliases = {f.value for f in person.facts if f.kind in {"name", "alias", "designation"}}
        descriptions = [f.value for f in person.facts if f.kind == "description"]
        if not valid_display_name(person.name) or person.name not in names:
            raise ValueError("人物姓名或代称缺少自己的事实依据")
        if person.real_name and person.real_name not in {
            f.value for f in person.facts if f.kind == "name"
        }:
            raise ValueError("真实姓名缺少自己的原文姓名事实")
        if any(alias not in aliases for alias in person.aliases):
            raise ValueError("人物别名缺少自己的事实依据，关系不能作为别名")
        if (person.description
                and person.description not in [*descriptions, "；".join(descriptions)]):
            raise ValueError("人物说明缺少自己的事实依据")
        keys = [(f.kind, f.value) for f in person.facts]
        if len(set(keys)) != len(keys):
            raise ValueError("人物块包含重复事实")
        records = []
        for fact in person.facts:
            if (fact.kind in {"name", "alias", "designation"}
                    and fact.value.strip() in PERSONAL_PRONOUNS):
                raise ValueError("人称代词不能作为人物姓名、别名或代称")
            own = proof(fact.evidence_refs)
            spans = tuple(dict.fromkeys([*own, *(association if person.character_id else ())]))
            if fact.kind in {"name", "alias"} and not any(
                fact.value in original.text[a:b] for a, b in own
            ):
                raise ValueError("姓名或别名不在其引用的原文中")
            records.append(CharacterIdentityFact(
                kind=fact.kind, value=fact.value, evidence_spans=spans,
                visible_from_cp=max(b for _, b in spans),
                canonical_sha256=original.canonical_sha256,
                source="model", source_ref=source_ref, accepted=False,
            ))
        compiled[person.temp_ref] = tuple(records)
    return output, compiled
