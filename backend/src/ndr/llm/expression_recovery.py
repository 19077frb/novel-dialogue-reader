"""Final-attempt recovery of local defects, never acceptance of invalid evidence."""

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass

from pydantic import ValidationError

from ..characters.names import valid_display_name
from ..evaluation.compact import DiscoveredCharacter
from ..evaluation.evidence_view import nonblank_evidence_view
from ..evaluation.expression_owner import OwnerLabel, UnownedLabel
from .errors import InvalidModelOutput
from .expression_compiler import ExpressionCompilation, compile_expression_output
from .expression_diagnostics import AUX_FIELDS, OWNER_KINDS
from .validation import load_json_object

RECOVERY_VERSION = "expression-local-recovery-1"


@dataclass(frozen=True)
class RecoveredExpression:
    compilation: ExpressionCompilation
    warnings: dict[str, str]


def recover_expression_output(payload, task):
    """Retain provided candidates as untrusted, then strictly compile the block.

    The strict compiler, evaluation and review proposals keep their original
    semantics. Callers opt in only after ordinary format retries are exhausted.
    Original model output stays in the call archive; recovered output is not cached.
    """
    raw = load_json_object(payload) if isinstance(payload, str) else deepcopy(dict(payload))
    if (set(raw) - {"labels", "breaks", "new_characters", "needs_context"}
            or not isinstance(raw.get("labels"), list) or not raw["labels"]):
        raise InvalidModelOutput("Cannot recover an invalid expression envelope")
    targets = set(task.quote_ids)
    by_quote = defaultdict(list)
    for row in raw["labels"]:
        if (not isinstance(row, dict) or not isinstance(row.get("q"), str)
                or row["q"] not in targets):
            raise InvalidModelOutput("Cannot recover unprovided expression targets")
        by_quote[row["q"]].append(row)
    view, _, _ = nonblank_evidence_view(task)
    proofs = {r["ref"] for r in view["context"] if "ref" in r}
    breaks = raw.get("breaks", [])
    allowed_breaks = {ref for ref, q in view["gap_next_quote"].items() if q}
    if (not isinstance(breaks, list) or any(type(ref) is not str for ref in breaks)
            or len(breaks) != len(set(breaks)) or set(breaks) - allowed_breaks):
        raise InvalidModelOutput("Cannot recover invalid scene structure")
    needs = raw.get("needs_context", [])
    if (not isinstance(needs, list) or any(type(q) is not str for q in needs)
            or len(needs) != len(set(needs)) or set(needs) - targets):
        raise InvalidModelOutput("Cannot recover invalid context requests")
    known = {c.ref for c in task.candidates}
    declarations = raw.get("new_characters", [])
    if not isinstance(declarations, list):
        raise InvalidModelOutput("Cannot recover invalid identity declarations")
    discoveries = {}
    seen = set()
    for person in declarations:
        if not isinstance(person, dict) or not isinstance(person.get("ref"), str):
            raise InvalidModelOutput("Cannot locate invalid identity declaration")
        ref = person["ref"]
        if ref in seen:
            raise InvalidModelOutput("Cannot recover duplicate identity declarations")
        seen.add(ref)
        try:
            parsed = DiscoveredCharacter.model_validate(person)
        except ValidationError:
            continue
        if valid_display_name(parsed.name) and not set(parsed.evidence) - proofs:
            discoveries[ref] = parsed.model_dump(mode="json")
    warnings, normalized, dependents = {}, {}, defaultdict(set)
    identities = known | discoveries.keys()
    for q in task.quote_ids:
        rows = by_quote[q]
        if len(rows) != 1:
            warnings[q] = "模型遗漏了这句对白" if not rows else "模型重复返回这句对白，归属未确定"
            normalized[q] = {"q": q, "kind": "unknown"}
            continue
        row = deepcopy(rows[0])
        primary = ({k: v for k, v in row.items() if k not in AUX_FIELDS}
                   if getattr(task, "auxiliary_protocol", None) else row)
        try:
            if ("owner_depends_on_addressee" in row
                    and type(row["owner_depends_on_addressee"]) is not bool):
                raise ValueError("Invalid auxiliary dependency")
            if isinstance(primary.get("kind"), str) and primary["kind"] in OWNER_KINDS:
                label = OwnerLabel.model_validate(primary)
                if (set(label.evidence) - proofs
                        or label.character is not None and label.character not in identities
                        or label.character is None and (
                            label.basis != "insufficient" or label.evidence)
                        or label.character is not None and label.basis == "insufficient"
                        or label.basis == "direct" and not any(ref != q for ref in label.evidence)):
                    raise ValueError("人物或证据未通过校验")
            else:
                UnownedLabel.model_validate(primary)
        except (ValidationError, ValueError):
            warnings[q] = "模型返回的人物或证据未通过校验，请核对归属"
        normalized[q] = row
        refs = primary.get("evidence", [])
        if isinstance(refs, list):
            for ref in refs:
                if isinstance(ref, str) and ref in targets:
                    dependents[ref].add(q)
    for ref, person in discoveries.items():
        members = {q for q, row in normalized.items() if row.get("character") == ref}
        for proof in set(person["evidence"]) & targets:
            dependents[proof].update(members)
    pending = deque(warnings)
    while pending:
        for q in dependents[pending.popleft()]:
            if q not in warnings:
                warnings[q] = "人物依据依赖一条待确认结果，请核对归属"
                pending.append(q)
    for q in warnings:
        row = normalized[q]
        kind = row.get("kind")
        if isinstance(kind, str) and kind in OWNER_KINDS:
            candidate = row.get("character")
            character = candidate if isinstance(candidate, str) and candidate in known else None
            normalized[q] = {
                "q": q, "kind": kind, "character": character,
                "basis": "style_only" if character else "insufficient",
                # A provided quote is only the tentative slot's location anchor,
                # never proof of identity. STYLE_ONLY cannot become accepted.
                "evidence": [q] if character else [],
            }
        else:
            normalized[q] = {"q": q, "kind": "unknown"}
    if not warnings:
        raise InvalidModelOutput("No recoverable local expression defects")
    used = {row.get("character") for row in normalized.values()
            if isinstance(row.get("character"), str)}
    recovered = {
        "labels": [normalized[q] for q in task.quote_ids], "breaks": breaks,
        "new_characters": [person for ref, person in discoveries.items() if ref in used],
        "needs_context": needs,
    }
    compilation = compile_expression_output(recovered, task)
    return RecoveredExpression(
        compilation, {task.references[q]: message for q, message in warnings.items()}
    )
