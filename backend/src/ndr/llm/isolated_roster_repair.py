"""Policy-2 repair: indivisible identity groups, isolated auxiliary diagnostics.

This pure compiler does not dispatch, save candidates, or upgrade legacy plans.
"""

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Literal

from pydantic import Field, StrictInt

from ..domain.common import ApiModel
from .roster_repair import (
    REPAIR_VERSION,
    RosterRepairOutput,
    RosterRepairPlan,
    prepare_roster_repair,
)
from .sourced_roster import (
    IsolatedRosterFailure,
    compile_isolated_sourced_roster,
    compile_sourced_roster,
)

ISOLATED_REPAIR_POLICY = "identity-blocks-2"


class _Group(ApiModel):
    indices: list[StrictInt] = Field(min_length=1, max_length=10000)
    characters: list[dict[str, Any]] = Field(min_length=1, max_length=10000)


class _Envelope(ApiModel):
    schema_version: Literal["roster-repair-1"]
    repairs: list[_Group] = Field(max_length=10000)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


@dataclass(frozen=True)
class IsolatedRepairPlan:
    original_plan: RosterRepairPlan
    retained_blocks_json: str
    seen_sources: tuple[str, ...]

    @property
    def groups(self):
        return self.original_plan.groups

    def fingerprint(self):
        plan = self.original_plan
        binding = {
            "policy": ISOLATED_REPAIR_POLICY,
            "original": [plan.original_version, plan.original_sha256,
                         plan.chapter_start, plan.chapter_end],
            "allowed_ids": sorted(plan.allowed_ids), "targets": plan.targets_json,
            "retained": self.retained_blocks_json, "sources": self.seen_sources,
        }
        if json.loads(plan.known_names_json):
            binding["known_names"] = plan.known_names_json
        return hashlib.sha256(_json(binding).encode()).hexdigest()

    def task_payload(self):
        task = self.original_plan.task_payload()
        task["output_schema"] = RosterRepairOutput.model_json_schema()
        task["repair_policy"] = ISOLATED_REPAIR_POLICY
        task["instruction"] += (
            "已修复的独立组也属于保留人物，不得再次改写。"
            "name/alias/designation不能填叙述者、本章第一人称或代词；"
            "视角身份只用pov_candidate及pov_evidence_refs表达，不必新增代称事实。"
            "有原文依据时给出一条简短description事实，description直接复用其value；无依据才留空。"
        )
        return task


def prepare_isolated_roster_repair(payload, **kwargs):
    plan = prepare_roster_repair(payload, **kwargs)
    retained = json.loads(plan.retained_json)
    indices = json.loads(plan.diagnostics_json)["retained_indices"]
    blocks = [{"index": index, "characters": [person], "source_ref": plan.initial_source_ref}
              for index, person in zip(indices, retained, strict=True)]
    return IsolatedRepairPlan(plan, _json(blocks), (plan.initial_source_ref,))


def compile_isolated_roster_repair(plan, payload, *, original, source_ref):
    """Return full valid output/facts, remaining plan, and explicit partial diagnostics."""
    original_plan = plan.original_plan
    if (original.book_version_id != original_plan.original_version
            or original.canonical_sha256 != original_plan.original_sha256):
        raise ValueError("人物修复不能沿用已改变的原文快照")
    if (not isinstance(source_ref, str) or not source_ref.strip() or len(source_ref) > 160
            or source_ref in plan.seen_sources):
        raise ValueError("人物修复必须有独立的真实调用来源")
    envelope = _Envelope.model_validate(payload)
    if envelope.schema_version != REPAIR_VERSION:
        raise ValueError("人物修复协议版本不匹配")
    supplied = []
    for group in envelope.repairs:
        if len(set(group.indices)) != len(group.indices):
            raise ValueError("人物修复索引重复")
        supplied.append(tuple(sorted(group.indices)))
    if len(set(supplied)) != len(supplied) or set(supplied) != set(original_plan.groups):
        raise ValueError("人物修复必须完整覆盖原有依赖组，不能新增、拆分或遗漏组")
    blocks = json.loads(plan.retained_blocks_json)
    if sum(len(b["characters"]) for b in blocks) + sum(
        len(g.characters) for g in envelope.repairs
    ) > 10000:
        raise ValueError("修复后人物提案超过数量上限")
    arguments = {"original": original, "chapter_start": original_plan.chapter_start,
                 "chapter_end": original_plan.chapter_end,
                 "allowed_character_ids": original_plan.allowed_ids,
                 "known_character_names": json.loads(original_plan.known_names_json)}
    remaining, successful, details = [], [], []
    discarded_facts = discarded_descriptions = 0
    for group, indices in zip(envelope.repairs, supplied, strict=True):
        try:
            output, _, diagnostic = compile_isolated_sourced_roster(
                {"schema_version": "1.1", "characters": group.characters},
                source_ref=source_ref, **arguments,
            )
        except IsolatedRosterFailure as exc:
            output, diagnostic = None, exc.diagnostics
        discarded_facts += diagnostic["discarded_auxiliary_facts"]
        discarded_descriptions += diagnostic["discarded_descriptions"]
        # A duplicate/identity dependency is indivisible, even if one member passed.
        rejected = bool(diagnostic["isolated_indices"])
        if len(details) < 100:
            details.append({"indices": list(indices), "accepted": not rejected,
                            "diagnostics": diagnostic})
        if rejected:
            remaining.append(indices)
            continue
        successful.append(indices)
        blocks.append({"index": min(indices),
                       "characters": [p.model_dump(mode="json") for p in output.characters],
                       "source_ref": source_ref})
    blocks.sort(key=lambda b: b["index"])
    people = [person for block in blocks for person in block["characters"]]
    # Cross-group conflicts are never bypassed with per-character salvage.
    output, _ = compile_sourced_roster(
        {"schema_version": "1.1", "characters": people}, source_ref=source_ref, **arguments,
    )
    facts = {}
    for block in blocks:
        _, individual = compile_sourced_roster(
            {"schema_version": "1.1", "characters": block["characters"]},
            source_ref=block["source_ref"], **arguments,
        )
        facts.update(individual)
    remaining = tuple(sorted(remaining))
    targets = [target for target in json.loads(original_plan.targets_json)
               if tuple(target["indices"]) in remaining]
    next_original = replace(original_plan, groups=remaining, targets_json=_json(targets),
                            retained_json=_json(people))
    next_plan = IsolatedRepairPlan(next_original, _json(blocks), (*plan.seen_sources, source_ref))
    diagnostics = {"repair_policy": ISOLATED_REPAIR_POLICY,
                   "successful_groups": [list(g) for g in successful],
                   "unresolved_groups": [list(g) for g in remaining],
                   "discarded_auxiliary_facts": discarded_facts,
                   "discarded_descriptions": discarded_descriptions, "details": details}
    return output, facts, next_plan, diagnostics
