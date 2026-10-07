import asyncio
import json
from copy import deepcopy
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator

from ndr.evaluation.compact import Candidate, CompactTask
from ndr.evaluation.enumerated_view import (
    ENUMERATED_VIEW_POLICY,
    UNKNOWN_EXAMPLE,
    EnumeratedEvidenceViewAdapter,
    enumerated_view_fingerprint,
)
from ndr.evaluation.evidence_view import NonblankEvidenceViewAdapter, evidence_view_fingerprint
from ndr.evaluation.frame_trial import run_frame_trial
from ndr.evaluation.journal import CallJournal, JournaledAdapter
from ndr.evaluation.turn_frames import FRAME_EXAMPLE, FrameOutput
from ndr.llm.errors import InvalidModelOutput


def task():
    return CompactTask(
        ("Q1", "Q2"),
        {ref: ref.lower() for ref in ("Q1", "Q2", "G1", "G2", "E1")},
        (
            {"ref": "E1", "text": "林舟说道。", "start_cp": 0, "end_cp": 5},
            {"ref": "Q1", "text": "请进。", "start_cp": 5, "end_cp": 8},
            {"ref": "G1", "text": "\n", "start_cp": 8, "end_cp": 9},
            {"ref": "G2", "text": "他们继续交谈。", "start_cp": 9, "end_cp": 16},
            {"ref": "Q2", "text": "谢谢。", "start_cp": 16, "end_cp": 19},
        ),
        (Candidate("C1", "person", "林舟"),),
        {"G1": "Q2", "G2": "Q2"},
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
            for q in task().quote_ids
        ]
    }


def test_prompt_enum_exactly_matches_provided_context_and_is_not_triplicated():
    adapter = EnumeratedEvidenceViewAdapter(None, task())
    refs = [row["ref"] for row in adapter.view["context"] if "ref" in row]
    assert adapter.prompt_schema["$defs"]["OriginalEvidenceReference"]["enum"] == refs
    assert "G1" not in refs and "B1" not in refs and "G2" in refs and "E1" in refs
    for model, field in (
        ("FrameSpeech", "evidence"),
        ("FrameSpeech", "addressee_evidence"),
        ("DiscoveredCharacter", "evidence"),
    ):
        assert adapter.prompt_schema["$defs"][model]["properties"][field]["items"] == {
            "$ref": "#/$defs/OriginalEvidenceReference"
        }
    Draft202012Validator.check_schema(adapter.prompt_schema)
    Draft202012Validator(adapter.prompt_schema).validate(payload())


def test_scene_enum_preserves_blank_and_nonblank_scene_decisions():
    adapter = EnumeratedEvidenceViewAdapter(None, task())
    assert adapter.prompt_schema["properties"]["breaks"]["items"]["enum"] == ["B1", "G2"]
    for boundary in ("B1", "G2"):
        raw = {**payload(), "breaks": [boundary]}
        Draft202012Validator(adapter.prompt_schema).validate(raw)
        assert adapter.compile_payload(raw)[0]["breaks"] == ["G1" if boundary == "B1" else "G2"]


def test_empty_scene_list_has_valid_schema_not_empty_enum():
    adapter = EnumeratedEvidenceViewAdapter(None, replace(task(), gap_next_quote={}))
    Draft202012Validator.check_schema(adapter.prompt_schema)
    validator = Draft202012Validator(adapter.prompt_schema)
    validator.validate(payload())
    assert list(validator.iter_errors({**payload(), "breaks": ["G2"]}))


def test_gap_without_next_target_cannot_appear_in_scene_enum():
    t = CompactTask(
        ("Q1",),
        {"Q1": "q", "G1": "gap"},
        (
            {"ref": "Q1", "text": "嗯。", "start_cp": 0, "end_cp": 2},
            {"ref": "G1", "text": "\n", "start_cp": 2, "end_cp": 3},
        ),
        (),
        {"G1": None},
    )
    adapter = EnumeratedEvidenceViewAdapter(None, t)
    assert "enum" not in adapter.prompt_schema["properties"]["breaks"]["items"]
    assert adapter.view["gap_next_quote"] == {"B1": None}


def test_partial_task_preserves_readonly_quote_evidence_in_enum():
    full = task()
    child = replace(full, quote_ids=("Q2",), gap_next_quote={})
    adapter = EnumeratedEvidenceViewAdapter(None, child)
    assert "Q1" in adapter.prompt_schema["$defs"]["OriginalEvidenceReference"]["enum"]
    assert adapter.view["targets"] == ["Q2"]
    assert adapter.view["context"] == NonblankEvidenceViewAdapter(None, child).view["context"]


def test_old_adapter_schema_example_fingerprint_and_context_remain_unchanged():
    t = task()
    old = NonblankEvidenceViewAdapter(None, t)
    new = EnumeratedEvidenceViewAdapter(None, t)
    assert old.view == new.view
    assert old.task == new.task == t
    assert FRAME_EXAMPLE in old.system and FRAME_EXAMPLE not in new.system
    assert UNKNOWN_EXAMPLE in new.system and ENUMERATED_VIEW_POLICY in new.system
    assert json.dumps(FrameOutput.model_json_schema(), ensure_ascii=False) in old.system
    assert enumerated_view_fingerprint(t.fingerprint()) != evidence_view_fingerprint(
        t.fingerprint()
    )
    assert enumerated_view_fingerprint("one") != enumerated_view_fingerprint("two")


@pytest.mark.parametrize("field", ["speaker", "receiver", "anonymous", "boundary"])
def test_prompt_enum_does_not_replace_or_weaken_local_acceptance(field):
    raw = payload()
    if field == "speaker":
        raw["labels"][0]["evidence"] = ["G1"]
    elif field == "receiver":
        raw["labels"][0].update(addressee="C1", addressee_evidence=["B1"])
    elif field == "anonymous":
        raw["new_characters"] = [
            {"ref": "N1", "name": "门卫", "description": "门口的人", "evidence": ["G1"]}
        ]
    else:
        raw["breaks"] = ["G1"]
    with pytest.raises(InvalidModelOutput):
        EnumeratedEvidenceViewAdapter(None, task()).compile_payload(raw)


class Recording:
    def __init__(self, responses):
        self.responses, self.seen = responses, []

    async def generate_labels(self, request):
        self.seen.append(deepcopy(request))
        return deepcopy(self.responses[len(self.seen) - 1])


def test_actual_request_changes_only_system_not_original_text_or_generation_parameters():
    backend = Recording([{**payload(), "_usage": {"total_tokens": 7}}])
    t = task()
    request = {"messages": t.messages(), "max_tokens": 8192, "max_tokens_override": 8192}
    before = deepcopy(request)
    adapter = EnumeratedEvidenceViewAdapter(backend, t)
    asyncio.run(adapter.generate_labels(request))
    assert request == before
    assert backend.seen[0]["messages"][0]["content"] == adapter.system
    assert json.loads(backend.seen[0]["messages"][1]["content"]) == adapter.view
    assert backend.seen[0]["max_tokens_override"] == 8192


def test_known_repair_keeps_all_usage_and_journal_restore_is_zero_call(tmp_path):
    raw = payload()
    raw["labels"][1]["evidence"] = ["G1"]
    backend = Recording(
        [
            {**raw, "_usage": {"total_tokens": 7}},
            {"labels": [payload()["labels"][1]], "_usage": {"total_tokens": 5}},
        ]
    )
    journal = CallJournal(
        tmp_path / "enum.trial.sqlite3",
        dependency_fingerprint=enumerated_view_fingerprint(task().fingerprint()),
        max_calls=2,
        max_tokens=100000,
    )

    def factory(t):
        return EnumeratedEvidenceViewAdapter(JournaledAdapter(backend, journal), t)

    result = asyncio.run(run_frame_trial(factory, task()))
    stats = journal.stats()
    restored = asyncio.run(run_frame_trial(factory, task()))
    assert result["ok"] and restored["ok"] and result["frame_payload"] == restored["frame_payload"]
    assert result["known_tokens"] == 12 and len(backend.seen) == 2 and stats == journal.stats()


def test_initial_horizon_is_preserved_and_future_text_is_still_rejected():
    t = replace(task(), reading_mode="initial", visible_horizon_cp=19)
    assert EnumeratedEvidenceViewAdapter(None, t).task.visible_horizon_cp == 19
    with pytest.raises(ValueError, match="Future text"):
        replace(t, visible_horizon_cp=18)


def test_legal_reference_does_not_allow_direct_self_evidence():
    raw = payload()
    raw["labels"][0]["evidence"] = ["Q1"]
    with pytest.raises(InvalidModelOutput, match="outside"):
        EnumeratedEvidenceViewAdapter(None, task()).compile_payload(raw)
