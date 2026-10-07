import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest
from pydantic import ValidationError

from ndr.evaluation.compact import Candidate, CompactTask, compile_output
from ndr.evaluation.compact_trial import run_trial
from ndr.evaluation.journal import CallJournal, JournaledAdapter
from ndr.evaluation.turn_frames import (
    FRAME_RETRY_POLICY,
    TURN_FRAME_POLICY,
    TurnFrameAdapter,
    compile_turn_frames,
    turn_frame_fingerprint,
)
from ndr.llm.errors import InvalidModelOutput


def task():
    return CompactTask(
        ("Q1",),
        {"Q1": "q1", "G1": "g1"},
        (
            {"ref": "G1", "text": "林舟说道。", "start_cp": 0, "end_cp": 5},
            {"ref": "Q1", "text": "周遥，你来吧。", "start_cp": 5, "end_cp": 13},
        ),
        (Candidate("C1", "a", "林舟"), Candidate("C2", "b", "周遥")),
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
                "addressee": "C2",
                "addressee_evidence": ["Q1"],
                **kwargs,
            }
        ]
    }


def test_recipient_field_does_not_rewrite_speaker_or_original_payload():
    t = task()
    raw = payload()
    before = deepcopy(raw)
    stripped, frames = compile_turn_frames(raw, t)
    assert raw == before
    assert stripped["labels"][0]["character"] == "C1"
    assert frames == [{"q": "Q1", "addressee": "C2", "evidence": ["Q1"]}]
    assert compile_output(stripped, t).new_speakers[0].character_id == "a"


def test_unknown_addressee_is_preserved_without_inventing_a_person():
    stripped, frames = compile_turn_frames(payload(addressee=None, addressee_evidence=[]), task())
    assert frames[0]["addressee"] is None
    assert len(compile_output(stripped, task()).new_speakers) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"addressee": "C999"},
        {"addressee_evidence": []},
        {"addressee": None},
        {"addressee_evidence": ["not-sent"]},
        {"addressee_evidence": ["Q1", "Q1"]},
        {"addressee": "N1"},
    ],
)
def test_invalid_recipient_or_evidence_is_not_repaired(changes):
    with pytest.raises(InvalidModelOutput):
        compile_turn_frames(payload(**changes), task())


def test_non_speech_remains_strict_and_has_no_person_fields():
    raw = {"labels": [{"q": "Q1", "kind": "thought"}]}
    stripped, frames = compile_turn_frames(raw, task())
    assert not frames and compile_output(stripped, task()).labels[0].speaker_ref is None
    raw["labels"][0]["addressee"] = None
    with pytest.raises(ValidationError):
        compile_turn_frames(raw, task())


@pytest.mark.parametrize("change", ["missing", "duplicate", "extra", "wrong-kind"])
def test_original_coverage_and_unknown_fields_stay_strict(change):
    raw = payload()
    if change == "missing":
        del raw["labels"][0]["addressee"]
    elif change == "duplicate":
        raw["labels"].append(deepcopy(raw["labels"][0]))
    elif change == "extra":
        raw["labels"][0]["reasoning"] = "do not allow extra fields"
    else:
        raw["labels"][0]["kind"] = "made-up"
    with pytest.raises((ValidationError, InvalidModelOutput)):
        compile_turn_frames(raw, task())


class Recording:
    def __init__(self, responses=None):
        self.seen = []
        self.responses = responses or [{**payload(), "_usage": {"total_tokens": 7}}]

    async def generate_labels(self, request):
        self.seen.append(request)
        return deepcopy(self.responses[min(len(self.seen) - 1, len(self.responses) - 1)])


def test_only_system_schema_changes_before_first_call_no_parameter_changes():
    t, backend = task(), Recording()
    request = {"messages": t.messages(), "max_tokens": 500, "max_tokens_override": 500}
    before = deepcopy(request)
    result = asyncio.run(TurnFrameAdapter(backend, t).generate_labels(request))
    assert request == before
    assert backend.seen[0]["messages"][1:] == request["messages"][1:]
    assert backend.seen[0]["max_tokens"] == 500
    assert "addressee_evidence" in backend.seen[0]["messages"][0]["content"]
    assert result["_usage"] == {"total_tokens": 7}
    assert "addressee" not in result["labels"][0]


def test_known_format_failure_keeps_usage_and_diagnostic_with_bounded_retry():
    bad = payload(addressee="C999")
    backend = Recording(
        [{**bad, "_usage": {"total_tokens": 11}}, {**payload(), "_usage": {"total_tokens": 7}}]
    )
    result = asyncio.run(run_trial(TurnFrameAdapter(backend, task()), task(), max_format_retries=1))
    assert result["ok"] and result["known_tokens"] == 18
    assert result["attempts"][0]["usage"]["total_tokens"] == 11
    assert len(backend.seen) == 2
    assert backend.seen[1]["messages"][-1]["content"].startswith(FRAME_RETRY_POLICY)
    assert "provided candidate" in backend.seen[1]["messages"][-1]["content"]


def test_unknown_failure_never_triggers_another_request():
    backend = Recording([{**payload(addressee="C999"), "_usage": {"unknown": True}}])
    result = asyncio.run(run_trial(TurnFrameAdapter(backend, task()), task(), max_format_retries=1))
    assert not result["ok"] and result["reconciliation_required"]
    assert result["unknown_usage_calls"] == 1 and len(backend.seen) == 1


def test_provider_exception_is_not_swallowed_or_retried_inside_adapter():
    class Failing:
        async def generate_labels(self, request):
            raise RuntimeError("outside failure")

    with pytest.raises(RuntimeError, match="outside failure"):
        asyncio.run(
            TurnFrameAdapter(Failing(), task()).generate_labels({"messages": task().messages()})
        )


def test_different_task_context_is_rejected_before_provider_call():
    backend, t = Recording(), task()
    changed = replace(t, candidates=(replace(t.candidates[0], name="沈河"), t.candidates[1]))
    with pytest.raises(ValueError, match="different task"):
        asyncio.run(TurnFrameAdapter(backend, t).generate_labels({"messages": changed.messages()}))
    assert not backend.seen


def test_no_call_recovery_recreates_actual_request_and_preserves_failure_cost(tmp_path):
    t = task()
    source = turn_frame_fingerprint(t.fingerprint())
    journal = CallJournal(
        tmp_path / "frames.trial.sqlite3",
        dependency_fingerprint=source,
        max_calls=2,
        max_tokens=100000,
    )
    backend = Recording(
        [
            {**payload(addressee="C999"), "_usage": {"total_tokens": 11}},
            {**payload(), "_usage": {"total_tokens": 7}},
        ]
    )
    first = asyncio.run(
        run_trial(TurnFrameAdapter(JournaledAdapter(backend, journal), t), t, max_format_retries=1)
    )
    before = journal.stats()

    class NoCall:
        async def generate_labels(self, request):
            raise AssertionError("Do not spend again")

    restored = asyncio.run(
        run_trial(TurnFrameAdapter(JournaledAdapter(NoCall(), journal), t), t, max_format_retries=1)
    )
    assert restored["output"] == first["output"] and restored["known_tokens"] == 18
    assert journal.stats() == before
    assert "addressee_evidence" in backend.seen[0]["messages"][0]["content"]


def test_frame_scope_and_examples_are_generic():
    assert turn_frame_fingerprint("old") != turn_frame_fingerprint("new")
    assert all(n not in TURN_FRAME_POLICY for n in ["浅村", "绫濑", "奈良坂", "义妹生活"])


def test_known_recipient_does_not_fill_an_unknown_speaker():
    stripped, _ = compile_turn_frames(
        payload(character=None, basis="insufficient", evidence=[]), task()
    )
    output = compile_output(stripped, task())
    assert output.labels[0].speaker_ref is None and not output.new_speakers


def test_illegal_original_scene_break_cannot_be_repaired_by_frame_fields():
    raw = {**payload(), "breaks": ["G1"]}
    with pytest.raises(InvalidModelOutput):
        compile_turn_frames(raw, task())


def test_initial_reading_has_no_new_evidence_or_future_identity_lookup():
    t, backend = replace(task(), reading_mode="initial", visible_horizon_cp=13), Recording()
    result = asyncio.run(TurnFrameAdapter(backend, t).generate_labels({"messages": t.messages()}))
    assert backend.seen[0]["messages"][1] == t.messages()[1]
    assert result["labels"][0]["character"] == "C1"


@pytest.mark.parametrize("mutation", ["empty", "multimodal", "second-system", "no-system"])
def test_invalid_message_shape_is_rejected_without_a_provider_call(mutation):
    t, backend = task(), Recording()
    messages = t.messages()
    if mutation == "empty":
        messages = []
    elif mutation == "multimodal":
        messages[1]["content"] = [{"type": "text", "text": "do not use"}]
    elif mutation == "second-system":
        messages.append({"role": "system", "content": "different instructions"})
    else:
        messages[0]["role"] = "user"
    with pytest.raises(ValueError):
        asyncio.run(TurnFrameAdapter(backend, t).generate_labels({"messages": messages}))
    assert not backend.seen
