import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest

from ndr.evaluation.compact import Candidate, CompactTask
from ndr.evaluation.compact_trial import run_trial
from ndr.evaluation.grounded_frames import (
    GROUNDING_POLICY,
    GroundedTurnFrameAdapter,
    compile_grounded_frames,
    grounding_fingerprint,
)
from ndr.evaluation.journal import CallJournal, JournaledAdapter
from ndr.evaluation.turn_frames import compile_turn_frames, turn_frame_fingerprint
from ndr.llm.errors import InvalidModelOutput


def task(text="林舟说道。"):
    return CompactTask(
        ("Q1",),
        {"G1": "gap:0:5", "Q1": "quote:5:10"},
        (
            {"ref": "G1", "text": text, "start_cp": 0, "end_cp": 5},
            {"ref": "Q1", "text": "「请进。」", "start_cp": 5, "end_cp": 10},
        ),
        (Candidate("C1", "person", "林舟"),),
    )


def payload(**kwargs):
    return {
        "labels": [
            {
                "q": "Q1",
                "kind": "speech",
                "character": "C1",
                "basis": "direct",
                "evidence": ["G1"],
                "addressee": None,
                "addressee_evidence": [],
                **kwargs,
            }
        ]
    }


@pytest.mark.parametrize("blank", ["", " ", "\n\t\r", "\u3000\xa0"])
def test_blank_evidence_rejected_without_changing_original_strategy(blank):
    t, raw = task(blank), payload()
    before = deepcopy(raw)
    compile_turn_frames(raw, t)
    with pytest.raises(InvalidModelOutput, match="Q1: evidence cites blank"):
        compile_grounded_frames(raw, t)
    assert raw == before


def test_self_reference_cannot_be_direct_but_is_not_reassigned():
    raw = payload(evidence=["Q1"])
    with pytest.raises(InvalidModelOutput, match="outside the target"):
        compile_grounded_frames(raw, task())
    assert raw["labels"][0]["character"] == "C1"


@pytest.mark.parametrize("basis", ["coreference", "response_link"])
def test_nonblank_links_are_not_automatically_semantically_certified(basis):
    raw = payload(basis=basis, evidence=["Q1"])
    stripped, _ = compile_grounded_frames(raw, task())
    assert stripped["labels"][0]["basis"] == basis


def test_any_blank_citation_rejected_even_with_another_nonblank_ref():
    with pytest.raises(InvalidModelOutput, match="blank"):
        compile_grounded_frames(payload(evidence=["Q1", "G1"]), task(" "))


def test_addressee_blank_evidence_rejected_without_guessing_recipient():
    raw = payload(basis="response_link", evidence=["Q1"], addressee="C1", addressee_evidence=["G1"])
    with pytest.raises(InvalidModelOutput, match="addressee_evidence cites blank"):
        compile_grounded_frames(raw, task(" "))


def test_unknown_and_non_speech_preserved():
    raw = payload(character=None, basis="insufficient", evidence=[])
    assert compile_grounded_frames(raw, task(" "))[0]["labels"][0]["character"] is None
    raw = {"labels": [{"q": "Q1", "kind": "quotation"}]}
    assert compile_grounded_frames(raw, task(" "))[0]["labels"] == raw["labels"]


def test_original_unprovided_evidence_and_full_coverage_still_rejected():
    with pytest.raises(InvalidModelOutput):
        compile_grounded_frames(payload(evidence=["unsent"]), task())
    with pytest.raises(InvalidModelOutput):
        compile_grounded_frames({"labels": []}, task())


def test_initial_reading_context_is_not_extended_and_fingerprint_separate():
    t = replace(task(), reading_mode="initial", visible_horizon_cp=10)
    compile_grounded_frames(payload(), t)
    assert max(r["end_cp"] for r in t.context) == 10
    assert grounding_fingerprint("a") != turn_frame_fingerprint("a")
    assert grounding_fingerprint("a") != grounding_fingerprint("b")
    assert "沙季" not in GROUNDING_POLICY and "浅村" not in GROUNDING_POLICY


class Recording:
    def __init__(self, rows):
        self.rows, self.seen = rows, []

    async def generate_labels(self, request):
        self.seen.append(deepcopy(request))
        return deepcopy(self.rows[len(self.seen) - 1])


def test_bounded_retry_preserves_failed_usage_diagnostic_and_actual_request_cache(tmp_path):
    t = task()
    base = Recording(
        [
            {**payload(evidence=["Q1"]), "_usage": {"total_tokens": 11}},
            {**payload(), "_usage": {"total_tokens": 13}},
        ]
    )
    journal = CallJournal(
        tmp_path / "calls.trial.sqlite3",
        dependency_fingerprint=grounding_fingerprint(t.fingerprint()),
        max_calls=2,
        max_tokens=10000,
    )
    result = asyncio.run(
        run_trial(
            GroundedTurnFrameAdapter(JournaledAdapter(base, journal), t),
            t,
            max_format_retries=1,
            max_tokens=64,
            targeted_retries=False,
        )
    )
    assert result["ok"] and result["known_tokens"] == 24 and len(result["attempts"]) == 2
    assert result["attempts"][0]["usage"]["total_tokens"] == 11
    assert "outside the target" in base.seen[1]["messages"][-1]["content"]
    assert GROUNDING_POLICY in base.seen[0]["messages"][0]["content"]
    assert base.seen[0]["messages"][1] == t.messages()[1]
    assert base.seen[0]["max_tokens"] == 64
    restored = asyncio.run(
        run_trial(
            GroundedTurnFrameAdapter(JournaledAdapter(Recording([]), journal), t),
            t,
            max_format_retries=1,
            max_tokens=64,
            targeted_retries=False,
        )
    )
    assert restored["output"] == result["output"] and journal.stats()["calls"] == 2


def test_unknown_usage_failure_stops_before_retry():
    base = Recording([payload(evidence=["Q1"])])
    result = asyncio.run(
        run_trial(GroundedTurnFrameAdapter(base, task()), task(), max_format_retries=1)
    )
    assert not result["ok"] and result["reconciliation_required"] and len(base.seen) == 1
