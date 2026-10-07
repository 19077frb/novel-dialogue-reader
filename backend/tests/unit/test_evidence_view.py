import asyncio
import json
from copy import deepcopy
from dataclasses import replace

import pytest

from ndr.evaluation.compact import Candidate, CompactTask
from ndr.evaluation.compact_trial import run_trial
from ndr.evaluation.evidence_view import (
    EVIDENCE_VIEW_POLICY,
    NonblankEvidenceViewAdapter,
    evidence_view_fingerprint,
    nonblank_evidence_view,
)
from ndr.evaluation.grounded_frames import GroundedTurnFrameAdapter, grounding_fingerprint
from ndr.evaluation.journal import CallJournal, JournaledAdapter
from ndr.llm.errors import InvalidModelOutput


def task():
    return CompactTask(
        ("Q1", "Q2"),
        {"Q1": "quote:0:4", "G1": "gap:4:7", "E1": "evidence:7:12", "Q2": "quote:12:16"},
        (
            {"ref": "Q1", "kind": "target_quote", "text": "「来吧」", "start_cp": 0, "end_cp": 4},
            {"ref": "G1", "kind": "inner_gap", "text": "\n\n　", "start_cp": 4, "end_cp": 7},
            {"ref": "E1", "kind": "overlap", "text": "林舟说：\n", "start_cp": 7, "end_cp": 12},
            {"ref": "Q2", "kind": "target_quote", "text": "「好啊」", "start_cp": 12, "end_cp": 16},
        ),
        (Candidate("C1", "person", "林舟"),),
        {"G1": "Q2"},
        pov_ref="C1",
    )


def payload():
    return {
        "labels": [
            {
                "q": q,
                "kind": "speech",
                "character": "C1",
                "basis": "direct",
                "evidence": ["E1"],
                "addressee": None,
                "addressee_evidence": [],
            }
            for q in ["Q1", "Q2"]
        ]
    }


def test_full_text_order_positions_candidates_and_targets_are_preserved():
    t = task()
    before = deepcopy(t)
    original = json.loads(t.messages()[1]["content"])
    view, hidden, boundaries = nonblank_evidence_view(t)
    assert t == before and hidden == {"G1"}
    assert "".join(row["text"] for row in view["context"]) == "".join(
        row["text"] for row in t.context
    )
    assert [(r["start_cp"], r["end_cp"]) for r in view["context"]] == [
        (r["start_cp"], r["end_cp"]) for r in t.context
    ]
    assert all(view[key] == original[key] for key in ["targets", "candidates", "pov"])
    assert view["gap_next_quote"] == {"B1": "Q2"} and boundaries == {"B1": "G1"}
    assert view["context"][1] == {k: v for k, v in t.context[1].items() if k != "ref"} | {
        "referenceable": False,
        "boundary_ref": "B1",
    }


@pytest.mark.parametrize("field", ["speaker", "recipient", "break", "anonymous"])
def test_every_hidden_citation_position_is_rejected(field):
    raw = payload()
    if field == "speaker":
        raw["labels"][0]["evidence"] = ["G1"]
    elif field == "recipient":
        raw["labels"][0].update(addressee="C1", addressee_evidence=["G1"])
    elif field == "break":
        raw["breaks"] = ["G1"]
    else:
        raw["new_characters"] = [
            {"ref": "N1", "name": "门卫", "description": "门口工作人员", "evidence": ["G1"]}
        ]
    with pytest.raises(InvalidModelOutput, match="did not provide.*G1"):
        NonblankEvidenceViewAdapter(None, task()).compile_payload(raw)


def test_nonblank_reference_and_original_compile_stay_strict():
    adapter = NonblankEvidenceViewAdapter(None, task())
    assert adapter.compile_payload(payload())[0]["labels"][0]["character"] == "C1"
    raw = payload()
    raw["labels"][0]["evidence"] = ["invented"]
    with pytest.raises(InvalidModelOutput):
        adapter.compile_payload(raw)
    raw = payload()
    raw["labels"].pop()
    with pytest.raises(InvalidModelOutput):
        adapter.compile_payload(raw)


@pytest.mark.parametrize("field", ["evidence_hints", "relay"])
def test_hidden_hint_or_relay_is_rejected_before_network(field):
    value = (
        ({"ref": "G1", "mentioned_candidates": []},)
        if field == "evidence_hints"
        else ({"ref": "G1", "candidate": "C1"},)
    )
    with pytest.raises(ValueError, match="hint or relay"):
        NonblankEvidenceViewAdapter(None, replace(task(), **{field: value}))


def test_target_cannot_be_hidden_or_future_added():
    t = task()
    with pytest.raises(ValueError, match="hide a target"):
        nonblank_evidence_view(replace(t, context=({**t.context[0], "text": " "}, *t.context[1:])))
    t = replace(t, reading_mode="initial", visible_horizon_cp=16)
    view, _, _ = nonblank_evidence_view(t)
    assert max(row["end_cp"] for row in view["context"]) == 16
    with pytest.raises(ValueError, match="Future text"):
        replace(t, visible_horizon_cp=15)


class Recording:
    def __init__(self, rows):
        self.rows, self.seen = rows, []

    async def generate_labels(self, request):
        self.seen.append(deepcopy(request))
        return deepcopy(self.rows[len(self.seen) - 1])


def test_actual_view_and_diagnostic_cached_retry_preserves_usage_and_restores(tmp_path):
    t = task()
    invalid = payload()
    invalid["labels"][0]["evidence"] = ["G1"]
    backend = Recording(
        [{**invalid, "_usage": {"total_tokens": 9}}, {**payload(), "_usage": {"total_tokens": 12}}]
    )
    journal = CallJournal(
        tmp_path / "view.trial.sqlite3",
        dependency_fingerprint=evidence_view_fingerprint(t.fingerprint()),
        max_calls=2,
        max_tokens=20000,
    )
    adapter = NonblankEvidenceViewAdapter(JournaledAdapter(backend, journal), t)
    request = {"messages": t.messages(), "max_tokens": 64}
    frozen = deepcopy(request)
    result = asyncio.run(
        run_trial(adapter, t, max_tokens=64, max_format_retries=1, targeted_retries=False)
    )
    assert result["ok"] and result["known_tokens"] == 21 and len(backend.seen) == 2
    actual = json.loads(backend.seen[0]["messages"][1]["content"])
    assert "ref" not in actual["context"][1]
    assert "G1" not in actual["gap_next_quote"] and request == frozen
    direct = Recording([{**payload(), "_usage": {"total_tokens": 4}}])
    asyncio.run(NonblankEvidenceViewAdapter(direct, t).generate_labels(request))
    assert request == frozen
    assert EVIDENCE_VIEW_POLICY in backend.seen[0]["messages"][0]["content"]
    assert "did not provide" in backend.seen[1]["messages"][-1]["content"]
    stats = journal.stats()
    restored = asyncio.run(
        run_trial(
            NonblankEvidenceViewAdapter(JournaledAdapter(Recording([]), journal), t),
            t,
            max_tokens=64,
            max_format_retries=1,
            targeted_retries=False,
        )
    )
    assert result["output"] == restored["output"] and journal.stats() == stats


def test_unknown_usage_does_not_retry_and_original_request_is_not_mutated():
    raw = payload()
    raw["labels"][0]["evidence"] = ["G1"]
    backend = Recording([raw])
    t = task()
    before = deepcopy(t)
    result = asyncio.run(run_trial(NonblankEvidenceViewAdapter(backend, t), t))
    assert result["reconciliation_required"] and len(backend.seen) == 1 and t == before


def test_previous_grounding_input_and_fingerprint_are_not_changed():
    t = task()
    backend = Recording([{**payload(), "_usage": {"total_tokens": 4}}])
    result = asyncio.run(run_trial(GroundedTurnFrameAdapter(backend, t), t, max_format_retries=0))
    assert result["ok"] and backend.seen[0]["messages"][1] == t.messages()[1]
    assert evidence_view_fingerprint("a") != grounding_fingerprint("a")
    assert evidence_view_fingerprint("a") != evidence_view_fingerprint("b")
    assert "沙季" not in EVIDENCE_VIEW_POLICY and "浅村" not in EVIDENCE_VIEW_POLICY


def test_blank_gap_boundary_is_preserved_by_mapping_not_scene_inference():
    raw = payload()
    raw["breaks"] = ["B1"]
    frozen = deepcopy(raw)
    stripped, _ = NonblankEvidenceViewAdapter(None, task()).compile_payload(raw)
    assert stripped["breaks"] == ["G1"] and raw == frozen


@pytest.mark.parametrize("field", ["speaker", "recipient", "anonymous"])
def test_boundary_only_ids_cannot_be_evidence(field):
    raw = payload()
    if field == "speaker":
        raw["labels"][0]["evidence"] = ["B1"]
    elif field == "recipient":
        raw["labels"][0].update(addressee="C1", addressee_evidence=["B1"])
    else:
        raw["new_characters"] = [
            {"ref": "N1", "name": "门卫", "description": "门口工作人员", "evidence": ["B1"]}
        ]
    with pytest.raises(InvalidModelOutput, match="did not provide.*B1"):
        NonblankEvidenceViewAdapter(None, task()).compile_payload(raw)


def test_boundary_alias_avoids_existing_reference_and_terminal_gap_rule_stays():
    t = task()
    refs = {"B1" if key == "E1" else key: value for key, value in t.references.items()}
    context = tuple({**row, "ref": "B1"} if row["ref"] == "E1" else row for row in t.context)
    view, _, boundaries = nonblank_evidence_view(replace(t, references=refs, context=context))
    assert boundaries == {"B1b": "G1"} and view["gap_next_quote"] == {"B1b": "Q2"}
    context = (
        *t.context,
        {"ref": "G2", "kind": "outer_gap", "text": " ", "start_cp": 16, "end_cp": 17},
    )
    t = replace(
        t,
        references={**t.references, "G2": "gap:16:17"},
        context=context,
        gap_next_quote={**t.gap_next_quote, "G2": None},
    )
    raw = payload()
    raw["breaks"] = ["B2"]
    with pytest.raises(InvalidModelOutput):
        NonblankEvidenceViewAdapter(None, t).compile_payload(raw)
