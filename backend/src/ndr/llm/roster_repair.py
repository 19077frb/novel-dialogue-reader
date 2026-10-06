"""Pure, request-bound identity-block repair; never dispatches a provider call."""

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, StrictInt

from ..domain.common import ApiModel
from .sourced_roster import (
    IsolatedRosterFailure,
    SourcedRosterCharacter,
    SourcedRosterOutput,
    compile_isolated_sourced_roster,
    compile_sourced_roster,
)

REPAIR_VERSION = "roster-repair-1"


class IdentityRepair(ApiModel):
    indices: list[StrictInt] = Field(min_length=1, max_length=10000)
    characters: list[SourcedRosterCharacter] = Field(min_length=1, max_length=10000)


class RosterRepairOutput(ApiModel):
    schema_version: Literal["roster-repair-1"]
    repairs: list[IdentityRepair] = Field(max_length=10000)


@dataclass(frozen=True)
class RosterRepairPlan:
    """JSON snapshots prevent mutable model proposals from changing this plan."""

    original_version: str
    original_sha256: str
    chapter_start: int
    chapter_end: int
    allowed_ids: frozenset[str]
    initial_source_ref: str
    retained_json: str
    targets_json: str
    diagnostics_json: str
    groups: tuple[tuple[int, ...], ...]

    def task_payload(self):
        return {
            "schema_version": REPAIR_VERSION,
            "groups": json.loads(self.targets_json),
            "retained_characters": json.loads(self.retained_json),
            "output_schema": RosterRepairOutput.model_json_schema(),
            "instruction": "仅修复列出的整组身份；保留人物不可改写。整组可合并，不能删除整组。",
        }


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def prepare_roster_repair(payload, *, original, chapter_start, chapter_end,
                          allowed_character_ids, source_ref):
    """Freeze all failed blocks, including transitive duplicate dependencies."""
    if not isinstance(source_ref, str) or not source_ref.strip() or len(source_ref) > 160:
        raise ValueError("人物初次调用来源不能为空")
    allowed_character_ids = frozenset(allowed_character_ids)
    try:
        output, _, diagnostic = compile_isolated_sourced_roster(
            payload, original=original, chapter_start=chapter_start, chapter_end=chapter_end,
            allowed_character_ids=allowed_character_ids, source_ref=source_ref,
        )
    except IsolatedRosterFailure as exc:
        output = SourcedRosterOutput(characters=[])
        diagnostic = exc.diagnostics
    failed = diagnostic["isolated_indices"]
    raw = payload["characters"]
    # Union-find keeps duplicate chains in one indivisible repair target.
    parent = {index: index for index in failed}

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    dependencies = {}
    for index in failed:
        person = raw[index - 1]
        if not isinstance(person, dict):
            continue
        for key in ("temp_ref", "character_id"):
            value = person.get(key)
            if not isinstance(value, str) or not value:
                continue
            dependency = (key, value)
            if dependency in dependencies:
                parent[root(index)] = root(dependencies[dependency])
            else:
                dependencies[dependency] = index
    components = {}
    for index in failed:
        components.setdefault(root(index), []).append(index)
    groups = tuple(sorted(tuple(indices) for indices in components.values()))
    errors = diagnostic["details"]
    targets = [{
        "indices": list(group), "characters": [raw[i - 1] for i in group],
        "errors": [e for e in errors if e["character_index"] in group],
    } for group in groups]
    return RosterRepairPlan(
        original.book_version_id, original.canonical_sha256, chapter_start, chapter_end,
        frozenset(allowed_character_ids), source_ref,
        _json([p.model_dump(mode="json") for p in output.characters]),
        _json(targets), _json(diagnostic), groups,
    )


def compile_roster_repair(plan, payload, *, original, source_ref):
    """Replace failed dependency groups only, then compile the entire roster."""
    if (original.book_version_id != plan.original_version
            or original.canonical_sha256 != plan.original_sha256):
        raise ValueError("人物修复不能沿用已改变的原文快照")
    if (not isinstance(source_ref, str) or not source_ref.strip() or len(source_ref) > 160
            or source_ref == plan.initial_source_ref):
        raise ValueError("人物修复必须有独立的真实调用来源")
    repair = RosterRepairOutput.model_validate(payload)
    if repair.schema_version != REPAIR_VERSION:
        raise ValueError("人物修复协议版本不匹配")
    expected = set(plan.groups)
    supplied = []
    for item in repair.repairs:
        if any(type(index) is not int for index in item.indices):
            raise ValueError("人物修复索引必须为整数")
        if len(set(item.indices)) != len(item.indices):
            raise ValueError("人物修复索引重复")
        supplied.append(tuple(sorted(item.indices)))
    if len(set(supplied)) != len(supplied) or set(supplied) != expected:
        raise ValueError("人物修复必须完整覆盖原有依赖组，不能新增、拆分或遗漏组")
    retained = json.loads(plan.retained_json)
    if len(retained) + sum(len(item.characters) for item in repair.repairs) > 10000:
        raise ValueError("修复后人物提案超过数量上限")
    retained_indices = json.loads(plan.diagnostics_json)["retained_indices"]
    blocks = {index: [person] for index, person in zip(retained_indices, retained, strict=True)}
    for item in repair.repairs:
        blocks[min(item.indices)] = [p.model_dump(mode="json") for p in item.characters]
    people = [person for index in sorted(blocks) for person in blocks[index]]
    output, facts = compile_sourced_roster(
        {"schema_version": "1.1", "characters": people}, original=original,
        chapter_start=plan.chapter_start, chapter_end=plan.chapter_end,
        allowed_character_ids=plan.allowed_ids, source_ref=source_ref,
    )
    # Compile retained blocks with their initial source, not the repair's receipt.
    _, retained_facts = compile_sourced_roster(
        {"schema_version": "1.1", "characters": retained}, original=original,
        chapter_start=plan.chapter_start, chapter_end=plan.chapter_end,
        allowed_character_ids=plan.allowed_ids, source_ref=plan.initial_source_ref,
    )
    facts.update(retained_facts)
    return output, facts
