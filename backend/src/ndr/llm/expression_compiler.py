"""Compile validated short expressions into the explicitly selected 1.1 contract."""

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace

from ..domain.enums import AnnotationStatus
from ..evaluation.compact import CompactTask, compile_output
from ..evaluation.owner_constraints import ConstrainedOwnerProtocol
from .errors import InvalidModelOutput
from .expression_contract import EXPRESSION_SCHEMA_VERSION, OWNER_KINDS, ExpressionLlmOutput
from .expression_task import ProjectedCompactTask, known_declaration_ids
from .validation import LabelingTargets, load_json_object, parse_and_validate

COMPILER_VERSION = "domain-expression-compiler-1"


@dataclass(frozen=True)
class ExpressionCompilation:
    output_json: str
    protocol_fingerprint: str
    owner_approvals: tuple[tuple[str, bool], ...]
    normalized_payload_json: str | None = None
    auxiliary_diagnostics_json: str | None = None

    @property
    def auxiliary_warnings(self):
        data = json.loads(self.auxiliary_diagnostics_json or "{}")
        invalid = sum(not r["valid"] for r in data.get("records", []))
        if not invalid:
            return []
        isolated = len(data.get("quarantined_targets", []))
        return [
            f"已隔离 {invalid} 条无效受话辅助信息；不依赖它的归属保留，"
            f"{isolated} 条依赖归属保留原类型并转为未知人物。"
        ]

    @property
    def output(self) -> ExpressionLlmOutput:
        return ExpressionLlmOutput.model_validate_json(self.output_json)

    @property
    def acceptance_ceilings(self) -> dict[str, AnnotationStatus]:
        approved = dict(self.owner_approvals)
        return {
            row.quote_id: AnnotationStatus.PROVISIONAL
            for row in self.output.labels
            if row.kind in OWNER_KINDS and row.speaker_ref and not approved[row.quote_id]
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                [
                    COMPILER_VERSION,
                    EXPRESSION_SCHEMA_VERSION,
                    self.protocol_fingerprint,
                    self.output_json,
                    self.owner_approvals,
                    *(
                        [self.normalized_payload_json, self.auxiliary_diagnostics_json]
                        if self.auxiliary_diagnostics_json is not None
                        else []
                    ),
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()


def validate_initial_identity_fields(task: CompactTask) -> None:
    """Validate supplied field visibility, not the semantic truth of an identity."""
    task.__post_init__()
    if isinstance(task, ProjectedCompactTask):
        task.validate_effective_profiles()
        return
    if task.reading_mode != "initial":
        return
    for candidate in task.candidates:
        facts = [f for f in task.identity_facts if f["candidate"] == candidate.ref]
        if candidate.name not in [
            f["value"] for f in facts if f["kind"] in {"name", "designation"}
        ]:
            raise ValueError("Initial candidate name lacks a visible identity fact")
        aliases = tuple(
            dict.fromkeys(
                f["value"]
                for f in facts
                if f["kind"] in {"name", "alias", "designation"} and f["value"] != candidate.name
            )
        )
        description = "；".join(f["value"] for f in facts if f["kind"] == "description")
        if candidate.aliases != aliases or candidate.description != description:
            raise ValueError("Initial candidate fields differ from visible identity facts")


def compile_expression_output(
    payload: str | Mapping,
    task: CompactTask,
    *,
    owner_approvals: Mapping[str, bool] | None = None,
) -> ExpressionCompilation:
    version = getattr(task, "auxiliary_protocol", None)
    if version is not None:
        from .expression_diagnostics import DIAGNOSTICS_VERSION, compile_expression_diagnostics

        if version != DIAGNOSTICS_VERSION:
            raise ValueError("Unsupported auxiliary isolation version")
        result = compile_expression_diagnostics(payload, task, owner_approvals=owner_approvals)
        return replace(
            result.compilation,
            protocol_fingerprint=result.fingerprint(),
            normalized_payload_json=result.primary_json,
            auxiliary_diagnostics_json=json.dumps(
                {
                    "records": result.diagnostics,
                    "quarantined_targets": list(result.quarantined_targets),
                },
                ensure_ascii=False,
            ),
        )
    return _compile_strict_expression_output(payload, task, owner_approvals=owner_approvals)


def _compile_strict_expression_output(
    payload: str | Mapping,
    task: CompactTask,
    *,
    owner_approvals: Mapping[str, bool] | None = None,
) -> ExpressionCompilation:
    task = deepcopy(task)
    validate_initial_identity_fields(task)
    payload = load_json_object(payload) if isinstance(payload, str) else deepcopy(dict(payload))
    protocol = ConstrainedOwnerProtocol(task)
    checked = protocol.compile(payload)
    intrinsic = {row["quote_id"]: row["admissible"] for row in checked["rows"]}
    approvals = dict(intrinsic) if owner_approvals is None else dict(owner_approvals)
    if set(approvals) != set(intrinsic) or any(type(v) is not bool for v in approvals.values()):
        raise ValueError("Complete explicit owner approvals required")
    if any(approvals[q] and not intrinsic[q] for q in approvals):
        raise ValueError("Owner approval cannot upgrade unsupported evidence")

    # Internal speech is ONLY an atomic validation view. It is never returned
    # or submitted: original kinds are restored, then the entire 1.1 block is
    # revalidated before the caller can obtain the domain output.
    internal = deepcopy(checked["original_payload"])
    kinds = {task.references[row["q"]]: row["kind"] for row in internal["labels"]}
    for row in internal["labels"]:
        if row["kind"] in {"thought", "quotation"}:
            row["kind"] = "speech"
    domain = compile_output(protocol.guard.compile_payload(internal), task).model_dump(mode="json")
    domain["schema_version"] = EXPRESSION_SCHEMA_VERSION
    for row in domain["labels"]:
        row["kind"] = kinds[row["quote_id"]]
    targets = LabelingTargets(
        quote_ids=tuple(task.references[q] for q in task.quote_ids),
        gap_ids=tuple(task.references[g] for g in task.gap_next_quote),
        scene_refs=(task.scene_ref,),
        speaker_refs=tuple(c.existing_ref for c in task.candidates if c.existing_ref),
        character_ids=tuple(c.character_id for c in task.candidates if c.character_id),
        evidence_ids=tuple(task.references.values()),
        require_display_names=True,
        known_declaration_ids=known_declaration_ids(task),
    )
    report = parse_and_validate(domain, targets, expected_schema_version=EXPRESSION_SCHEMA_VERSION)
    if not report.ok:
        raise InvalidModelOutput(
            "Expression compilation rejected",
            details={"codes": report.error_codes},
        )
    return ExpressionCompilation(
        report.output.model_dump_json(), protocol.fingerprint(), tuple(sorted(approvals.items()))
    )
