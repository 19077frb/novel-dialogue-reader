import asyncio
import json
from dataclasses import replace

import pytest

from ndr.evaluation.compact import Candidate, CompactTask
from ndr.evaluation.journal import CallJournal, ReconciliationRequired
from ndr.evaluation.pipeline import PipelinePolicy, execute_pipeline, pipeline_fingerprint
from ndr.evaluation.review import Decision
from ndr.evaluation.scene_plan import comparison_plan
from ndr.evaluation.window_review import expanded_review_task, refine_window, union_review_context
from ndr.llm.schemas import LlmOutput

TEXT = "林舟说：「甲。」\n周遥走近。「乙。」\n以后他们会认识江雨。"


def test_expanded_review_preserves_narrative_reading_order_and_literal_text():
    source = task()
    before = source.fingerprint()
    expanded = expanded_review_task(TEXT, source, source.quote_ids, margin=0)
    positions = [(r["start_cp"], r["end_cp"]) for r in expanded.context]
    assert positions == sorted(positions)
    assert "".join(r["text"] for r in expanded.context) == TEXT[:18]
    assert expanded.quote_ids == source.quote_ids
    for ref in source.quote_ids:
        assert expanded.references[ref] == source.references[ref]
    assert expanded.candidates == source.candidates
    assert expanded.pov_ref == source.pov_ref and expanded.scene_ref == source.scene_ref
    assert source.fingerprint() == before


def test_union_keeps_original_refs_and_orders_new_evidence_without_rewriting():
    source = task()
    expanded = expanded_review_task(TEXT, source, ("Q2",), margin=1)
    united = union_review_context(source, [expanded])
    positions = [(r["start_cp"], r["end_cp"]) for r in united.context]
    assert positions == sorted(positions)
    for ref, stable in source.references.items():
        assert united.references[ref] == stable
    for row in united.context:
        assert row["text"] == TEXT[row["start_cp"]:row["end_cp"]]
    assert united.quote_ids == source.quote_ids and united.candidates == source.candidates


def test_initial_review_chronology_does_not_extend_the_visible_horizon():
    source = replace(task(), reading_mode="initial", visible_horizon_cp=18)
    expanded = expanded_review_task(TEXT, source, source.quote_ids, margin=1000)
    assert expanded.reading_mode == "initial" and expanded.visible_horizon_cp == 18
    assert all(r["end_cp"] <= 18 for r in expanded.context)
    main = [r for r in expanded.context if not r["ref"].startswith("ER_anchor")]
    assert "".join(r["text"] for r in main) == TEXT[:18]


def task():
    return CompactTask(
        ("Q1", "Q2"),
        {"Q1": "q1", "Q2": "q2", "E1": "e1"},
        (
            {"ref": "Q1", "start_cp": 4, "end_cp": 8, "text": TEXT[4:8]},
            {"ref": "Q2", "start_cp": 14, "end_cp": 18, "text": TEXT[14:18]},
            {"ref": "E1", "kind": "overlap", "start_cp": 0, "end_cp": 4, "text": TEXT[:4]},
        ),
        (Candidate("C1", "a", "林舟"), Candidate("C2", "b", "周遥")),
    )


class Adapter:
    def __init__(self, *, review_person="C1", bad_review=False, unknown_review=False):
        self.seen = []
        self.review_person, self.bad_review, self.unknown_review = (
            review_person,
            bad_review,
            unknown_review,
        )

    async def generate_labels(self, request):
        data = json.loads(request["messages"][1]["content"])
        self.seen.append(data)
        review = len(self.seen) > 1
        if review and self.bad_review:
            return {"labels": [], "_usage": {"total_tokens": 20}}
        evidence = next(row["ref"] for row in data["context"] if row["ref"] not in data["targets"])
        return {
            "labels": [
                {
                    "q": q,
                    "kind": "speech",
                    "character": self.review_person if review else "C1",
                    "basis": "direct",
                    "evidence": [evidence],
                }
                for q in data["targets"]
            ],
            "needs_context": list(data["targets"]) if review else [],
            "_usage": {
                "total_tokens": None if review and self.unknown_review else 20,
                "unknown": review and self.unknown_review,
            },
        }


def run(adapter, **kwargs):
    return asyncio.run(refine_window(adapter, task(), text=TEXT, max_format_retries=0, **kwargs))


def identities(output):
    parsed = LlmOutput.model_validate(output)
    speakers = {s.temp_ref: s.character_id for s in parsed.new_speakers}
    return [speakers.get(label.speaker_ref) for label in parsed.labels]


def test_candidate_review_recompiles_complete_output_with_original_expanded_proof():
    adapter = Adapter()
    result = run(adapter)
    assert result["ok"] and result["review_complete"]
    assert identities(result["output"]) == ["a", "a"]
    assert result["known_tokens"] == 40 and len(result["attempts"]) == 2
    assert result["output"]["needs_context"] == ["q1", "q2"]
    assert "previous_turn_candidates" in adapter.seen[1]
    assert len(result["compiled_task"]["context"]) > len(task().context)


def test_independent_arm_has_no_previous_answers_and_disagreement_remains_unknown():
    adapter = Adapter(review_person="C2")
    result = run(adapter, mode="independent")
    assert "previous_turn_candidates" not in adapter.seen[1]
    assert result["ok"] and identities(result["output"]) == [None, None]
    assert set(result["resolution_reasons"].values()) == {"unresolved_conflict"}


def test_known_review_failure_retains_valid_base_and_all_failed_usage():
    result = run(Adapter(bad_review=True))
    assert result["ok"] and not result["review_complete"]
    assert result["review_failures"] == 1
    assert identities(result["output"]) == ["a", "a"]
    assert result["known_tokens"] == 40
    assert result["stages"][1]["result"]["ok"] is False


def test_unknown_review_cost_never_submits_the_valid_base_as_complete():
    adapter = Adapter(unknown_review=True)
    result = run(adapter)
    assert result["ok"] is False and result["reconciliation_required"]
    assert len(adapter.seen) == 2


def test_explicit_user_lock_survives_semantic_disagreement():
    locked = {"q1": Decision("speech", "a", "direct", ("e1",))}
    result = run(Adapter(review_person="C2"), locked=locked)
    assert identities(result["output"]) == ["a", None]
    assert result["resolution_reasons"]["q1"] == "user_locked"


def test_initial_review_expansion_cannot_read_future_text_or_change_candidate_facts():
    initial = replace(task(), reading_mode="initial", visible_horizon_cp=18)
    expanded = expanded_review_task(TEXT, initial, ("Q2",))
    assert all(row["end_cp"] <= 18 for row in expanded.context)
    assert "江雨" not in json.dumps(expanded.messages(), ensure_ascii=False)
    assert expanded.candidates == initial.candidates
    with pytest.raises(ValueError, match="distinct ordered"):
        expanded_review_task(TEXT, initial, ("Q2", "Q1"))


def fixture(tmp_path):
    tasks = {"W1": task()}
    plan = comparison_plan(tuple(tasks), continuous=True)
    policy = PipelinePolicy(
        "mock:disabled", processor_version="linked-candidate-test-1", max_format_retries=0
    )
    ledger = CallJournal(
        tmp_path / "review.trial.sqlite3",
        dependency_fingerprint=pipeline_fingerprint(TEXT, tasks, plan, policy),
        max_calls=10,
        max_tokens=100000,
    )
    return tasks, plan, policy, ledger


def test_complete_refinement_pipeline_restores_without_base_or_review_calls(tmp_path):
    tasks, plan, policy, ledger = fixture(tmp_path)

    async def processor(adapter, prepared):
        return await refine_window(adapter, prepared, text=TEXT, max_format_retries=0)

    async def execute(adapter):
        return await execute_pipeline(
            adapter,
            ledger,
            text=TEXT,
            tasks=tasks,
            plan=plan,
            policy=policy,
            process_window=processor,
        )

    first = asyncio.run(execute(Adapter()))
    assert first["complete"] and first["paid_calls_this_invocation"] == 2
    adapter = Adapter()
    restored = asyncio.run(execute(adapter))
    assert restored["complete"] and restored["paid_calls_this_invocation"] == 0
    assert restored["ledger"]["known_tokens"] == 40 and not adapter.seen


def test_refinement_pipeline_unknown_review_is_kept_for_reconciliation_not_checkpoint(tmp_path):
    tasks, plan, policy, ledger = fixture(tmp_path)

    async def processor(adapter, prepared):
        return await refine_window(adapter, prepared, text=TEXT, max_format_retries=0)

    with pytest.raises(ReconciliationRequired):
        asyncio.run(
            execute_pipeline(
                Adapter(unknown_review=True),
                ledger,
                text=TEXT,
                tasks=tasks,
                plan=plan,
                policy=policy,
                process_window=processor,
            )
        )
    assert ledger.stats()["known_tokens"] == 20 and ledger.stats()["unresolved_calls"] == 1
    assert ledger.load_checkpoint("window:W1") is None


def test_processor_cannot_silently_increase_output_cap(tmp_path):
    tasks, plan, policy, ledger = fixture(tmp_path)

    async def processor(adapter, prepared):
        return await refine_window(adapter, prepared, text=TEXT, max_tokens=16000)

    with pytest.raises(ValueError, match="output budget"):
        asyncio.run(
            execute_pipeline(
                Adapter(),
                ledger,
                text=TEXT,
                tasks=tasks,
                plan=plan,
                policy=policy,
                process_window=processor,
            )
        )
    assert ledger.stats()["calls"] == 0
