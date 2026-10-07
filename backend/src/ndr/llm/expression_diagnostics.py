"""Opt-in auxiliary isolation, with strict whole-block expression compilation."""

import json
from copy import deepcopy
from dataclasses import dataclass

from ..storage.cache import fingerprint
from .expression_compiler import ExpressionCompilation
from .expression_compiler import _compile_strict_expression_output as compile_expression_output
from .validation import load_json_object

DIAGNOSTICS_VERSION = "expression-auxiliary-isolation-1"
AUX_FIELDS = frozenset({"addressee", "addressee_evidence", "owner_depends_on_addressee"})
OWNER_KINDS = frozenset({"speech", "thought", "quotation"})


@dataclass(frozen=True)
class DiagnosticCompilation:
    compilation: ExpressionCompilation
    diagnostics_json: str
    quarantined_targets: tuple[str, ...]
    original_fingerprint: str
    primary_json: str

    @property
    def diagnostics(self):
        return json.loads(self.diagnostics_json)

    def fingerprint(self):
        return fingerprint(
            [
                DIAGNOSTICS_VERSION,
                self.compilation.fingerprint(),
                self.diagnostics_json,
                self.quarantined_targets,
                self.original_fingerprint,
            ]
        )


def compile_expression_diagnostics(payload, task, *, owner_approvals=None):
    """Explicit alternative; the old compiler still rejects auxiliary fields.

    No model calls, data writes, or implied semantic dependency validation.
    Raw evidence belongs in the caller's bounded call archive, not this result.
    """
    raw = load_json_object(payload) if isinstance(payload, str) else deepcopy(dict(payload))
    primary = deepcopy(raw)
    if not isinstance(primary.get("labels"), list):
        raise ValueError("Complete expression labels required")
    auxiliary = {}
    for row in primary["labels"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid primary expression")
        if not isinstance(row.get("q"), str):
            raise ValueError("Primary expression requires a string target reference")
        data = {key: row.pop(key) for key in list(row) if key in AUX_FIELDS}
        if type(data.get("owner_depends_on_addressee", False)) is not bool:
            raise ValueError("Explicit boolean auxiliary dependency required")
        auxiliary[row.get("q")] = data
    # Duplicate/missing targets, main identities, evidence and scene defects
    # are not auxiliary defects and cannot be removed to obtain a valid result.
    original = compile_expression_output(primary, task, owner_approvals=owner_approvals)
    nonblank = {r["ref"] for r in task.context if r["text"].strip()}
    known = {c.ref for c in task.candidates}
    diagnostics, failed = [], set()
    for row in primary["labels"]:
        data = auxiliary[row["q"]]
        if not data:
            continue
        recipient, proof = data.get("addressee"), data.get("addressee_evidence", [])
        errors = []
        if not isinstance(proof, list) or len(proof) > 64 or any(type(r) is not str for r in proof):
            errors.append("invalid_auxiliary_evidence_list")
        elif len(proof) != len(set(proof)) or set(proof) - nonblank:
            errors.append("unsent_blank_or_duplicate_auxiliary_evidence")
        if recipient is None:
            if proof:
                errors.append("unknown_recipient_with_evidence")
        elif type(recipient) is not str or recipient not in known or not proof:
            errors.append("unprovided_or_unsupported_recipient")
        if row["kind"] not in OWNER_KINDS:
            errors.append("auxiliary_on_unowned_expression")
        diagnostics.append(
            {
                "q": row["q"],
                "valid": not errors,
                "errors": errors,
                "owner_depends_on_addressee": data.get("owner_depends_on_addressee", False),
            }
        )
        if errors and data.get("owner_depends_on_addressee", False):
            failed.add(row["q"])
    if failed:
        labels = {r["q"]: r for r in primary["labels"]}
        edges = {q: set() for q in labels}
        for q, row in labels.items():
            for other in row.get("evidence", []):
                if other in edges:
                    edges[q].add(other)
                    edges[other].add(q)
        for person in primary.get("new_characters", []):
            members = {q for q, r in labels.items() if r.get("character") == person["ref"]}
            members.update(q for q in person["evidence"] if q in labels)
            for q in members:
                edges[q].update(members)
        pending = list(failed)
        while pending:
            for q in edges[pending.pop()] - failed:
                failed.add(q)
                pending.append(q)
        for row in primary["labels"]:
            if row["q"] in failed and row["kind"] in OWNER_KINDS:
                row.update(character=None, basis="insufficient", evidence=[])
        used = {r.get("character") for r in primary["labels"]}
        primary["new_characters"] = [
            p for p in primary.get("new_characters", []) if p["ref"] in used
        ]
    # Final full compile retains coverage, stable references, original kinds,
    # creation order, and intrinsic acceptance ceilings. No per-row bypass.
    approvals = None if owner_approvals is None else dict(owner_approvals)
    if approvals is not None:
        for q in failed:
            approvals[task.references[q]] = False
    result = (
        compile_expression_output(primary, task, owner_approvals=approvals) if failed else original
    )
    return DiagnosticCompilation(
        result,
        json.dumps(diagnostics, ensure_ascii=False),
        tuple(q for q in task.quote_ids if q in failed),
        fingerprint(raw),
        json.dumps(primary, ensure_ascii=False),
    )
