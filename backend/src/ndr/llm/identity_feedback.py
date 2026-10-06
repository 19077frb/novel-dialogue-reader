"""Bounded, whole-block identity feedback proposals; no dispatch or library writes.

Production stages may use this boundary to obtain candidates for independent
adjudication, never authorization to overwrite facts or apply proposals alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from ..domain.common import ApiModel
from ..evaluation.owner_constraints import ConstrainedOwnerProtocol
from ..storage.cache import fingerprint
from .expression_compiler import compile_expression_output

FEEDBACK_VERSION = "identity-feedback-1"


class IdentityIssue(ApiModel):
    kind: Literal["omitted_identity", "incorrect_association", "incorrect_pov"]
    targets: list[str] = Field(min_length=1, max_length=10000)
    character: str = Field(min_length=1, max_length=64)
    evidence: list[str] = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=600)


class IdentityFeedback(ApiModel):
    schema_version: Literal["identity-feedback-1"]
    issues: list[IdentityIssue] = Field(max_length=10000)
    proposal: dict


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


@dataclass(frozen=True)
class IdentityFeedbackPlan:
    task_fingerprint: str
    original_json: str
    primary_source: str
    locked_targets: frozenset[str]
    version: str = FEEDBACK_VERSION

    def fingerprint(self):
        return fingerprint(
            [
                self.version,
                self.task_fingerprint,
                self.original_json,
                self.primary_source,
                sorted(self.locked_targets),
            ]
        )


@dataclass(frozen=True)
class CompiledIdentityFeedback:
    proposal_json: str
    issues_json: str
    plan_fingerprint: str
    source_ref: str
    # This is only a suggestion; never apply it to a chapter roster here.
    pov_candidate: str | None

    def fingerprint(self):
        return fingerprint(
            [
                FEEDBACK_VERSION,
                self.proposal_json,
                self.issues_json,
                self.plan_fingerprint,
                self.source_ref,
                self.pov_candidate,
            ]
        )


def prepare_identity_feedback(task, primary, *, primary_source, locked_targets=(), generation=0):
    if not task.quote_ids:
        raise ValueError("Identity feedback requires expression targets")
    if type(generation) is not int or generation != 0:
        raise ValueError("Identity feedback permits one generation only")
    if not isinstance(primary_source, str) or not primary_source.strip():
        raise ValueError("Primary identity proposal requires an explicit source")
    compilation = compile_expression_output(primary, task)
    normalized = (
        json.loads(compilation.normalized_payload_json)
        if (compilation.normalized_payload_json is not None)
        else json.loads(_json(primary))
    )
    locked = frozenset(locked_targets)
    if locked - {task.references[q] for q in task.quote_ids}:
        raise ValueError("Locked identity targets outside task")
    return IdentityFeedbackPlan(task.fingerprint(), _json(normalized), primary_source, locked)


def build_identity_feedback_messages(plan, task):
    if plan.task_fingerprint != task.fingerprint() or plan.version != FEEDBACK_VERSION:
        raise ValueError("Identity feedback task changed")
    messages = ConstrainedOwnerProtocol(task).messages()
    schema = IdentityFeedback.model_json_schema()
    issue = schema["$defs"]["IdentityIssue"]["properties"]
    issue["targets"]["items"]["enum"] = list(task.quote_ids)
    issue["evidence"]["items"]["enum"] = [
        row["ref"]
        for row in task.context
        if row["text"].strip() and row.get("kind") not in {"inner_gap", "outer_gap"}
    ]
    issue["targets"]["uniqueItems"] = issue["evidence"]["uniqueItems"] = True
    messages[0]["content"] += (
        "\n本阶段返回identity-feedback-1 JSON，不直接返回labels。proposal使用上述完整归属协议。"
        "人物目录不是不可质疑的真值。只反馈有外部原文依据的遗漏、错误关联或POV候选冲突。"
        "问题targets须覆盖共享新身份的完整依赖块，不改其他对白、类型或场景。"
        "omitted_identity必须声明新的N；incorrect_association可指向已有C或有证据的新N。"
        "incorrect_pov只提出已有C候选，不修改目录或对白归属。"
        "没有有依据的身份问题时issues为空，proposal保持原提案不变。"
        "证据只填已提供编号，不能将姓名、坐标、理由或候选本身当原文引用。\n" + _json(schema)
    )
    short = {stable: ref for ref, stable in task.references.items()}
    messages.append(
        {
            "role": "user",
            "content": _json(
                {
                    "primary_proposal": json.loads(plan.original_json),
                    "locked_targets": [short[q] for q in sorted(plan.locked_targets)],
                    "feedback_generation": 0,
                }
            ),
        }
    )
    return messages


def compile_identity_feedback(plan, payload, task, *, source_ref):
    """Check the whole dependency block, retaining semantic uncertainty outside it."""
    if plan.version != FEEDBACK_VERSION or plan.task_fingerprint != task.fingerprint():
        raise ValueError("Identity feedback task changed")
    if (
        not isinstance(source_ref, str)
        or not source_ref.strip()
        or source_ref == plan.primary_source
    ):
        raise ValueError("Identity feedback requires an independent source")
    parsed = IdentityFeedback.model_validate(payload)
    original = json.loads(plan.original_json)
    # Never accept a damaged primary, or salvage isolated labels without a full compile.
    compile_expression_output(original, task)
    compilation = compile_expression_output(parsed.proposal, task)
    proposal = json.loads(compilation.normalized_payload_json or _json(parsed.proposal))
    before = {r["q"]: r for r in original["labels"]}
    after = {r["q"]: r for r in proposal["labels"]}
    if proposal.get("breaks", []) != original.get("breaks", []):
        raise ValueError("Identity feedback cannot change scene boundaries")
    if any(before[q]["kind"] != after[q]["kind"] for q in before):
        raise ValueError("Identity feedback cannot change expression types")
    actual = {
        r["ref"]
        for r in task.context
        if r["text"].strip() and r.get("kind") not in {"inner_gap", "outer_gap"}
    }
    candidates = {c.ref for c in task.candidates}
    discoveries = {p["ref"] for p in proposal.get("new_characters", [])}
    claimed, changed, pov = set(), set(), None
    for issue in parsed.issues:
        targets = set(issue.targets)
        if len(targets) != len(issue.targets) or targets - before.keys():
            raise ValueError("Identity feedback requires distinct provided targets")
        evidence = set(issue.evidence)
        if len(evidence) != len(issue.evidence) or evidence - actual or not evidence - targets:
            raise ValueError("Identity feedback requires actual external evidence")
        if issue.kind == "incorrect_pov":
            if (
                pov is not None
                or issue.character not in candidates
                or issue.character == task.pov_ref
            ):
                raise ValueError("Identity feedback requires a distinct single POV candidate")
            pov = issue.character
            continue
        if claimed & targets:
            raise ValueError("Overlapping identity feedback dependencies")
        if any(task.references[q] in plan.locked_targets for q in targets):
            raise ValueError("Identity feedback cannot alter locked targets")
        if issue.character not in candidates | discoveries:
            raise ValueError("Identity feedback cites an unprovided identity")
        if issue.kind == "omitted_identity" and (
            issue.character not in discoveries
            or issue.character in {p["ref"] for p in original.get("new_characters", [])}
        ):
            raise ValueError("Omitted identity requires a newly declared identity")
        if any(after[q].get("character") != issue.character for q in targets):
            raise ValueError("Feedback identity differs from its complete proposal")
        if not any(before[q].get("character") != issue.character for q in targets):
            raise ValueError("Identity feedback must change a proposed association")
        for rows in (before, after):
            identities = {rows[q].get("character") for q in targets}
            dependencies = {
                q
                for q, row in rows.items()
                if row.get("character") in identities
                and isinstance(row.get("character"), str)
                and row["character"].startswith("N")
            }
            if dependencies - targets:
                raise ValueError("Identity feedback must include the whole anonymous dependency")
        claimed.update(targets)
        changed.update(targets)
    if any(before[q] != after[q] for q in before.keys() - changed):
        raise ValueError("Identity feedback changed an unrelated expression")
    before_people = {p["ref"]: p for p in original.get("new_characters", [])}
    after_people = {p["ref"]: p for p in proposal.get("new_characters", [])}
    for q in before.keys() - changed:
        ref = before[q].get("character")
        if ref in before_people and before_people[ref] != after_people.get(ref):
            raise ValueError("Identity feedback changed an unrelated identity declaration")
    if proposal.get("needs_context", []) != original.get("needs_context", []):
        outside = (
            set(proposal.get("needs_context", [])) ^ set(original.get("needs_context", []))
        ) - changed
        if outside:
            raise ValueError("Identity feedback changed unrelated context requests")
    return CompiledIdentityFeedback(
        _json(proposal),
        _json([i.model_dump(mode="json") for i in parsed.issues]),
        plan.fingerprint(),
        source_ref,
        pov,
    )
