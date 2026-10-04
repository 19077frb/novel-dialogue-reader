import asyncio
import json
from copy import deepcopy

import pytest

from ndr.evaluation.cold_roster import (
    COLD_ROSTER_VERSION,
    ColdRosterTask,
    compile_roster,
    run_cold_roster,
)
from ndr.evaluation.evidence import EvidenceIndex
from ndr.evaluation.journal import CallJournal, JournaledAdapter, SnapshotChanged
from ndr.llm.errors import InvalidModelOutput

TEXT = "女旅人进门。\n女旅人说：「我叫云岚。」\n云岚是林舟的姐姐。\n后来沈宁到来。"


def payload():
    return {
        "people": [
            {
                "ref": "R1",
                "facts": [
                    {"kind": "name", "value": "女旅人", "evidence": ["L1"]},
                    {"kind": "name", "value": "云岚", "evidence": ["L2"]},
                    {"kind": "alias", "value": "女旅人", "evidence": ["L1"]},
                    {"kind": "description", "value": "来到屋里的旅人", "evidence": ["L1"]},
                    {"kind": "relation", "value": "林舟的姐姐", "evidence": ["L3"]},
                ],
            }
        ],
        "pov": "R1",
    }


def task():
    return ColdRosterTask(TEXT, len(TEXT))


def test_separate_facts_preserve_original_proofs_reveal_time_and_identity():
    people, pov = compile_roster(payload(), task())
    assert pov == people[0].character_id
    early = people[0].visible_candidate("C1", len("女旅人进门。\n"))
    late = people[0].visible_candidate("C1", len(TEXT))
    assert early.name == "女旅人" and late.name == "云岚"
    assert "云岚" not in early.aliases
    assert {f.kind for f in people[0].facts} == {"name", "alias", "description", "relation"}
    assert all(TEXT[a:b] for f in people[0].facts for a, b in f.evidence_spans)
    assert compile_roster(payload(), task()) == (people, pov)


def test_initial_roster_request_has_empty_directory_and_no_future_source():
    horizon = len("女旅人进门。\n")
    initial = ColdRosterTask(TEXT, horizon)
    user = json.loads(initial.messages()[1]["content"])
    assert user["existing_people"] == []
    assert "云岚" not in json.dumps(user, ensure_ascii=False)
    assert "沈宁" not in json.dumps(user, ensure_ascii=False)
    with pytest.raises(InvalidModelOutput, match="unsent"):
        compile_roster(payload(), initial)
    # Unseen trailing text does not change the visible request fingerprint.
    assert (
        initial.fingerprint()
        == ColdRosterTask(TEXT[:horizon] + "不同的未来", horizon).fingerprint()
    )


def test_original_designation_is_a_name_without_revealing_future_real_name():
    initial = ColdRosterTask(TEXT, len("女旅人进门。\n"))
    people, pov = compile_roster(
        {
            "people": [
                {"ref": "R1", "facts": [{"kind": "name", "value": "女旅人", "evidence": ["L1"]}]}
            ],
            "pov": None,
        },
        initial,
    )
    assert pov is None and people[0].visible_candidate("C1", initial.horizon).name == "女旅人"
    assert "云岚" not in json.dumps(initial.messages(), ensure_ascii=False)


def test_roster_prompt_requires_designations_and_unique_facts_without_relaxing_validation():
    assert COLD_ROSTER_VERSION == "original-fact-roster-2"
    system = task().messages()[0]["content"]
    assert "不能只有alias" in system and "无实名不等于没有人物" in system
    assert "同种kind、同一个value只列一次" in system
    assert "只有原文确实无人时people=[]" in system


def test_changed_roster_version_cannot_reuse_an_old_dependency_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr("ndr.evaluation.cold_roster.COLD_ROSTER_VERSION", "original-fact-roster-1")
    old_fingerprint = task().fingerprint()
    path = tmp_path / "roster.trial.sqlite3"
    CallJournal(path, dependency_fingerprint=old_fingerprint, max_calls=4, max_tokens=100000)
    monkeypatch.setattr("ndr.evaluation.cold_roster.COLD_ROSTER_VERSION", "original-fact-roster-2")
    assert task().fingerprint() != old_fingerprint
    with pytest.raises(SnapshotChanged):
        CallJournal(
            path, dependency_fingerprint=task().fingerprint(), max_calls=4, max_tokens=100000
        )


def test_compiled_roster_integrates_with_existing_initial_evidence_index():
    people, pov = compile_roster(payload(), task())
    horizon = TEXT.index("后来")
    start = TEXT.index("「")
    end = TEXT.index("」") + 1
    attribution = EvidenceIndex(TEXT, people).task(
        ((start, end),), reading_mode="initial", horizon=horizon, pov_id=pov
    )
    assert all(f["visible_from_cp"] <= horizon for f in attribution.identity_facts)
    assert "沈宁" not in json.dumps(attribution.messages(), ensure_ascii=False)


def test_same_name_in_two_roster_refs_is_not_an_automatic_merge():
    data = payload()
    second = deepcopy(data["people"][0])
    second["ref"] = "R2"
    data["people"].append(second)
    people, _ = compile_roster(data, task())
    assert len(people) == 2 and people[0].character_id != people[1].character_id


def test_no_people_is_valid_but_pov_cannot_refer_to_missing_identity():
    assert compile_roster({"people": [], "pov": None}, ColdRosterTask("", 0)) == ((), None)
    with pytest.raises(InvalidModelOutput):
        compile_roster({"people": [], "pov": "R1"}, task())


@pytest.mark.parametrize(
    "change",
    [
        "name",
        "alias",
        "duplicate_ref",
        "duplicate_fact",
        "duplicate_proof",
        "unknown_proof",
        "no_name",
    ],
)
def test_invalid_proposals_are_rejected_without_silent_repair(change):
    data = payload()
    if change in {"name", "alias"}:
        fact = next(f for f in data["people"][0]["facts"] if f["kind"] == change)
        fact["value"] = "江雨"
    elif change == "duplicate_ref":
        data["people"].append(deepcopy(data["people"][0]))
    elif change == "duplicate_fact":
        data["people"][0]["facts"].append(deepcopy(data["people"][0]["facts"][0]))
    elif change == "duplicate_proof":
        data["people"][0]["facts"][0]["evidence"] = ["L1", "L1"]
    elif change == "unknown_proof":
        data["people"][0]["facts"][0]["evidence"] = ["L999"]
    else:
        data["people"][0]["facts"] = [f for f in data["people"][0]["facts"] if f["kind"] != "name"]
    with pytest.raises(InvalidModelOutput):
        compile_roster(data, task())


class Adapter:
    def __init__(self, *, bad=False, unknown=False):
        self.bad, self.unknown, self.requests = bad, unknown, []

    async def generate_labels(self, request):
        self.requests.append(request)
        result = {"people": [], "pov": "unknown"} if self.bad else payload()
        return {
            **result,
            "_usage": {"total_tokens": None if self.unknown else 20, "unknown": self.unknown},
        }


def test_retry_has_fixed_budget_and_counts_all_failure_cost():
    adapter = Adapter(bad=True)
    result = asyncio.run(run_cold_roster(adapter, task(), max_tokens=4096, max_format_retries=1))
    assert not result["ok"] and result["known_tokens"] == 40
    assert result["people"] is None
    assert [r["max_tokens_override"] for r in adapter.requests] == [4096, 4096]


def test_unknown_usage_stops_even_with_structurally_valid_proposal():
    adapter = Adapter(unknown=True)
    result = asyncio.run(run_cold_roster(adapter, task()))
    assert not result["ok"] and result["reconciliation_required"]
    assert result["people"] is None and len(adapter.requests) == 1


def test_cold_roster_success_does_not_need_database_or_preexisting_characters():
    result = asyncio.run(run_cold_roster(Adapter(), task()))
    assert result["ok"] and result["first_pass_ok"]
    assert result["known_tokens"] == 20 and len(result["people"]) == 1


def test_roster_response_recovery_does_not_pay_again(tmp_path):
    ledger = CallJournal(
        tmp_path / "roster.trial.sqlite3",
        dependency_fingerprint=task().fingerprint() + ":mock-disabled:8192",
        max_calls=4,
        max_tokens=100000,
    )
    first = asyncio.run(run_cold_roster(JournaledAdapter(Adapter(), ledger), task()))
    restored_adapter = Adapter(bad=True)
    restored = asyncio.run(run_cold_roster(JournaledAdapter(restored_adapter, ledger), task()))
    assert first["people"] == restored["people"]
    assert not restored_adapter.requests
    assert ledger.stats()["calls"] == 1 and ledger.stats()["known_tokens"] == 20
