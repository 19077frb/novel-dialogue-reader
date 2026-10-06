"""Evidence-gated expression review; pure proposals, never library writes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Literal

from pydantic import Field

from ..domain.common import ApiModel
from ..evaluation.owner_constraints import ConstrainedOwnerProtocol
from ..storage.cache import fingerprint
from .expression_compiler import compile_expression_output
from .expression_contract import OWNER_KINDS

REVIEW_VERSION = "expression-evidence-review-1"
KINDS = frozenset(k.value for k in OWNER_KINDS)


@dataclass(frozen=True)
class OwnerDecision:
    kind: str
    character_id: str | None
    basis: str | None
    evidence: tuple[str, ...]
    admissible: bool
    anonymous_ref: str | None = None
    source_ref: str = ""

    def identity(self):
        return ("owner" if self.kind in KINDS else self.kind, self.character_id, self.anonymous_ref)

    def supported(self, quote_id):
        return (
            self.kind in KINDS
            and bool(self.character_id or self.anonymous_ref)
            and self.admissible
            and self.basis in {"direct", "coreference", "response_link"}
            and any(ref != quote_id for ref in self.evidence)
        )


def snapshot(payload, task, *, call_ref, owner_approvals=None):
    if not isinstance(call_ref, str) or not call_ref:
        raise ValueError("Explicit call scope required")
    compilation = compile_expression_output(payload, task, owner_approvals=owner_approvals)
    checked = ConstrainedOwnerProtocol(task).compile(payload)
    original = checked["original_payload"]
    rows = {r["quote_id"]: r for r in checked["rows"]}
    approvals = dict(compilation.owner_approvals)
    proposed = {}
    anonymous = {
        f"{call_ref}:{p['ref']}": {
            "name": p["name"],
            "description": p["description"],
            "evidence": [task.references[e] for e in p["evidence"]],
        }
        for p in original["new_characters"]
    }
    for label in original["labels"]:
        q = task.references[label["q"]]
        row = rows[q]
        ref = label.get("character")
        proposed[q] = OwnerDecision(
            label["kind"],
            row["character_id"],
            label.get("basis"),
            tuple(row["evidence_refs"]),
            approvals[q],
            f"{call_ref}:{ref}" if ref and ref.startswith("N") else None,
            call_ref,
        )
    return {
        "decisions": proposed,
        "anonymous": anonymous,
        "breaks": tuple(original["breaks"]),
        "version": REVIEW_VERSION,
    }


def compile_decisions(task, resolved, *, breaks=(), anonymous=None):
    """Compile the complete block and preserve every explicit acceptance ceiling."""
    if set(resolved) != {task.references[q] for q in task.quote_ids}:
        raise ValueError("Complete expression dependency block required")
    short = {stable: ref for ref, stable in task.references.items()}
    candidates = {c.character_id: c.ref for c in task.candidates if c.character_id}
    anonymous = anonymous or {}
    new_refs, discoveries, labels = {}, [], []
    for q in task.quote_ids:
        d = resolved[task.references[q]]
        if not isinstance(d, OwnerDecision) or type(d.admissible) is not bool:
            raise ValueError("Explicit typed decision and approval required")
        if d.kind not in KINDS:
            if d.character_id or d.anonymous_ref or d.evidence or d.basis:
                raise ValueError("Unowned kind cannot carry owner fields")
            labels.append({"q": q, "kind": d.kind})
            continue
        character = candidates.get(d.character_id)
        if d.character_id is not None and character is None:
            raise ValueError("Unprovided stable identity")
        if d.anonymous_ref:
            if d.character_id is not None or d.anonymous_ref not in anonymous:
                raise ValueError("Conflicting or missing source anonymous identity")
            if d.anonymous_ref not in new_refs:
                new_refs[d.anonymous_ref] = f"N{len(new_refs) + 1}"
                p = anonymous[d.anonymous_ref]
                discoveries.append(
                    {
                        "ref": new_refs[d.anonymous_ref],
                        "name": p["name"],
                        "description": p["description"],
                        "evidence": [short[e] for e in p["evidence"]],
                    }
                )
            character = new_refs[d.anonymous_ref]
        labels.append(
            {
                "q": q,
                "kind": d.kind,
                "character": character,
                "basis": d.basis,
                "evidence": [short[e] for e in d.evidence],
            }
        )
    return compile_expression_output(
        {"labels": labels, "breaks": list(breaks), "new_characters": discoveries},
        task,
        owner_approvals={q: d.admissible for q, d in resolved.items()},
    )


class ChallengeItem(ApiModel):
    q: str
    verdict: Literal["support_challenger", "support_consensus", "undecidable"]
    evidence: list[str] = Field(min_length=1, max_length=16)
    contradiction_evidence: list[str] = Field(default_factory=list, max_length=16)
    reason: str = Field(min_length=1, max_length=600)


class ChallengeOutput(ApiModel):
    checks: list[ChallengeItem]


@dataclass(frozen=True)
class VerifiedChallenge:
    decision: OwnerDecision
    verdict: str
    task_fingerprint: str
    proposal_fingerprint: str
    verifier_ref: str
    evidence: tuple[str, ...]
    contradiction_evidence: tuple[str, ...]
    reason: str


def _scope(original, reviewed, third, locked=None):
    if (set(reviewed) | set(third) | set(locked or {})) - set(original):
        raise ValueError("Decision outside complete original block")


def _proposal_fingerprint(original, reviewed, third):
    return fingerprint(
        {
            "version": REVIEW_VERSION,
            "proposals": [
                {q: asdict(d) for q, d in p.items()} for p in (original, reviewed, third)
            ],
        }
    )


def agreed_challenges(original, reviewed, third, *, locked=None):
    _scope(original, reviewed, third, locked)
    return tuple(
        q
        for q, a in original.items()
        if q in reviewed
        and q in third
        and q not in (locked or {})
        and a.identity() == reviewed[q].identity()
        and a.identity() != third[q].identity()
        and third[q].supported(q)
        and (a.character_id or a.anonymous_ref or a.kind not in KINDS | {"unknown"})
    )


def verify_payload(
    payload, task, original, reviewed, third, *, requested, verifier_ref, anonymous=None, breaks=()
):
    """Validate a supplied independent check; citations are not semantic proof."""
    sources = {d.source_ref for p in (original, reviewed, third) for d in p.values()}
    if not isinstance(verifier_ref, str) or not verifier_ref or verifier_ref in sources:
        raise ValueError("Independent verifier scope required")
    wanted = tuple(requested)
    if (
        not wanted
        or len(wanted) != len(set(wanted))
        or set(wanted) - set(agreed_challenges(original, reviewed, third))
    ):
        raise ValueError("Only eligible agreed-but-challenged targets may be checked")
    for proposal in (original, {**original, **reviewed}, {**original, **third}):
        compile_decisions(task, proposal, anonymous=anonymous, breaks=breaks)
    parsed = ChallengeOutput.model_validate(payload)
    actual = {
        row["ref"]
        for row in task.context
        if row["text"].strip() and row.get("kind") not in {"inner_gap", "outer_gap"}
    }
    proof = _proposal_fingerprint(original, reviewed, third)
    task_scope = task.fingerprint()
    records = {}
    for row in parsed.checks:
        q = task.references.get(row.q)
        if q not in wanted or q in records:
            raise ValueError("Unexpected or duplicate challenge target")
        for refs in (row.evidence, row.contradiction_evidence):
            if len(refs) != len(set(refs)) or set(refs) - actual:
                raise ValueError("Unsent, blank, boundary or duplicate challenge evidence")
        a, b, c = original[q], reviewed[q], third[q]
        consensus = a if a.supported(q) and not b.supported(q) else b
        contradiction = tuple(task.references[e] for e in row.contradiction_evidence)
        if row.verdict == "support_challenger":
            if not any(e != q for e in contradiction) or not c.supported(q):
                raise ValueError("Changing agreement requires external contradiction evidence")
            choice = c
        elif row.verdict == "support_consensus":
            choice = consensus
        else:
            choice = replace(consensus, admissible=False)
        records[q] = VerifiedChallenge(
            choice,
            row.verdict,
            task_scope,
            proof,
            verifier_ref,
            tuple(task.references[e] for e in row.evidence),
            contradiction,
            row.reason,
        )
    if set(records) != set(wanted):
        raise ValueError("Missing challenge target")
    return records


def reconcile(
    original, reviewed, *, adjudicated=None, locked=None, verified=None, task_fingerprint=None
):
    """Resolve identity separately from type, preserving locks and abstentions."""
    third, locked, verified = adjudicated or {}, locked or {}, verified or {}
    _scope(original, reviewed, third, locked)
    eligible = set(agreed_challenges(original, reviewed, third, locked=locked))
    if set(verified) - eligible:
        raise ValueError("Verification outside eligible agreement challenges")
    proof = _proposal_fingerprint(original, reviewed, third)
    if verified and any(
        not isinstance(v, VerifiedChallenge)
        or not task_fingerprint
        or v.task_fingerprint != task_fingerprint
        or v.proposal_fingerprint != proof
        for v in verified.values()
    ):
        raise ValueError("Verification does not match task and complete proposals")
    result, reasons = {}, {}
    for q, base in original.items():
        second, last = reviewed.get(q), third.get(q)
        if q in locked:
            choice, reason = locked[q], "user_locked"
        elif second is None:
            choice, reason = base, "review_unavailable"
        elif base.identity() == second.identity():
            choice = base if base.supported(q) and not second.supported(q) else second
            reason = "identity_agreement"
            if last and last.identity() != base.identity() and last.supported(q):
                if not (base.character_id or base.anonymous_ref) and base.kind in KINDS | {
                    "unknown"
                }:
                    choice, reason = last, "agreed_unknown_recovered_from_evidence"
                elif q in eligible:
                    check = verified.get(q)
                    consensus = choice
                    choice = replace(choice, admissible=False)
                    reason = "agreement_challenge_pending"
                    if check is not None:
                        if check.verdict == "support_challenger":
                            expected = last
                        elif check.verdict == "support_consensus":
                            expected = consensus
                        elif check.verdict == "undecidable":
                            expected = replace(consensus, admissible=False)
                        else:
                            raise ValueError("Unknown verification verdict")
                        if check.decision != expected:
                            raise ValueError("Verified decision changed")
                        if check.verdict == "support_challenger" and (
                            not check.decision.supported(q)
                            or not any(e != q for e in check.contradiction_evidence)
                        ):
                            raise ValueError("Verified challenger lacks external contradiction")
                        if check.verdict == "undecidable" and check.decision.admissible:
                            raise ValueError("Undecidable challenge must remain pending")
                        choice, reason = check.decision, "agreement_checked_" + check.verdict
        elif last and last.supported(q):
            choice = last
            reason = (
                "evidence_corroborated"
                if last.identity() in {base.identity(), second.identity()}
                else "evidence_new_answer"
            )
        else:
            kind = base.kind if base.kind == second.kind and base.kind in KINDS else "unknown"
            choice = OwnerDecision(kind, None, "insufficient" if kind in KINDS else None, (), False)
            reason = "unresolved_conflict"
        result[q], reasons[q] = choice, reason
    return result, reasons


def review_effects(expected, base, resolved, *, type_gold=None):
    """Paired identity/type effects; missing/abstained owners never score as right."""
    if set(base) != set(resolved):
        raise ValueError("Aligned full proposals required")
    if any(not isinstance(person, str) or not person for person in expected.values()):
        raise ValueError("Explicit identity gold required")
    counts = dict.fromkeys(
        (
            "identity_corrected",
            "identity_harmed",
            "new_unknown",
            "new_wrong",
            "type_corrected",
            "type_harmed",
        ),
        0,
    )
    for q, gold in expected.items():
        if q not in base:
            raise ValueError("Gold target missing from proposals")
        a, b = base[q], resolved[q]
        before = a.character_id if a.admissible and a.kind in KINDS else None
        after = b.character_id if b.admissible and b.kind in KINDS else None
        counts["identity_corrected"] += before != gold and after == gold
        counts["identity_harmed"] += before == gold and after != gold
        counts["new_unknown"] += before is not None and after is None
        counts["new_wrong"] += before == gold and after is not None and after != gold
    for q, kind in (type_gold or {}).items():
        if q not in base:
            raise ValueError("Type gold target missing from proposals")
        counts["type_corrected"] += base[q].kind != kind and resolved[q].kind == kind
        counts["type_harmed"] += base[q].kind == kind and resolved[q].kind != kind
    return {"review_version": REVIEW_VERSION, **counts}
