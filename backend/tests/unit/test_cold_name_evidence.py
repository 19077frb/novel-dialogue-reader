import json
from dataclasses import asdict

import pytest

from ndr.evaluation.cold_roster import ColdRosterTask, compile_roster
from ndr.evaluation.compact import compile_output
from ndr.evaluation.evidence import EVIDENCE_VERSION, EvidenceIndex, EvidencePerson, IdentityFact
from ndr.evaluation.journal import CallJournal, SnapshotChanged
from ndr.llm.errors import InvalidModelOutput


def test_truly_unnamed_first_person_can_have_a_supported_designation_and_compile():
    text = "我守在门旁说：「请进。」"
    task = ColdRosterTask(text, len(text))
    people, pov = compile_roster(
        {
            "people": [
                {
                    "ref": "R1",
                    "facts": [{"kind": "designation", "value": "讲述人", "evidence": ["L1"]}],
                }
            ],
            "pov": "R1",
        },
        task,
    )
    person = people[0]
    assert pov == person.character_id and person.visible_candidate("C1", len(text)).name == "讲述人"
    # No literal proper name was invented. The role inference remains model evidence.
    assert person.facts[0].kind == "designation"
    a = text.index("「")
    attribution = EvidenceIndex(text, people).task(((a, len(text)),), pov_id=pov)
    compiled = compile_output(
        {
            "labels": [
                {
                    "q": "Q1",
                    "kind": "speech",
                    "character": "C1",
                    "basis": "direct",
                    "evidence": ["G1"],
                }
            ]
        },
        attribution,
    )
    assert compiled.new_speakers[0].name == "讲述人"


@pytest.mark.parametrize("kind", ["name", "alias", "designation"])
@pytest.mark.parametrize("value", ["我", "她", "自己", " 我 "])
def test_pronoun_facts_are_rejected_not_bound_to_pov_by_program(kind, value):
    text = "我跟她说话，她指着自己。"
    payload = {
        "people": [
            {
                "ref": "R1",
                "facts": [
                    {"kind": "designation", "value": "讲述人", "evidence": ["L1"]},
                    {"kind": kind, "value": value, "evidence": ["L1"]},
                ],
            }
        ],
        "pov": "R1",
    }
    with pytest.raises(InvalidModelOutput, match="not a pronoun"):
        compile_roster(payload, ColdRosterTask(text, len(text)))


def test_real_name_does_not_leak_early_and_later_designation_does_not_hide_it():
    text = "门卫进门。\n门卫说我叫宋梨。\n门卫仍站在那里。\n"
    task = ColdRosterTask(text, len(text))
    people, _ = compile_roster(
        {
            "people": [
                {
                    "ref": "R1",
                    "facts": [
                        {"kind": "designation", "value": "门卫", "evidence": ["L1"]},
                        {"kind": "name", "value": "宋梨", "evidence": ["L2"]},
                        {"kind": "designation", "value": "值班门卫", "evidence": ["L3"]},
                    ],
                }
            ],
            "pov": None,
        },
        task,
    )
    p = people[0]
    early = p.visible_candidate("C1", len("门卫进门。\n"))
    late = p.visible_candidate("C1", len(text))
    assert early.name == "门卫" and not early.aliases
    assert late.name == "宋梨" and set(late.aliases) == {"门卫", "值班门卫"}
    assert p.character_id == people[0].character_id
    assert "宋梨" not in json.dumps(asdict(early), ensure_ascii=False)


def test_name_requires_literal_evidence_even_if_designation_is_supported():
    text = "我守在门旁。"
    payload = {
        "people": [
            {
                "ref": "R1",
                "facts": [
                    {"kind": "designation", "value": "讲述人", "evidence": ["L1"]},
                    {"kind": "name", "value": "沈宁", "evidence": ["L1"]},
                ],
            }
        ],
        "pov": "R1",
    }
    with pytest.raises(InvalidModelOutput, match="literal"):
        compile_roster(payload, ColdRosterTask(text, len(text)))


def test_same_designation_is_not_identity_merge_and_relations_are_not_aliases():
    text = "两个门卫分别值班。"
    payload = {
        "people": [
            {
                "ref": ref,
                "facts": [
                    {"kind": "designation", "value": "门卫", "evidence": ["L1"]},
                    {"kind": "relation", "value": "沈宁的邻居", "evidence": ["L1"]},
                ],
            }
            for ref in ["R1", "R2"]
        ],
        "pov": None,
    }
    people, _ = compile_roster(payload, ColdRosterTask(text, len(text)))
    assert len(people) == 2 and people[0].character_id != people[1].character_id
    assert all(not p.visible_candidate("C1", len(text)).aliases for p in people)


def test_multi_proof_identity_link_is_not_available_before_its_last_necessary_line():
    text = "纸上印着沈宁。\n后来才证实这是门卫的名字。\n"
    people, _ = compile_roster(
        {
            "people": [
                {
                    "ref": "R1",
                    "facts": [
                        {"kind": "designation", "value": "门卫", "evidence": ["L2"]},
                        {"kind": "name", "value": "沈宁", "evidence": ["L1", "L2"]},
                    ],
                }
            ],
            "pov": None,
        },
        ColdRosterTask(text, len(text)),
    )
    assert people[0].visible_candidate("C1", len("纸上印着沈宁。\n")) is None
    assert people[0].visible_candidate("C1", len(text)).name == "沈宁"


def test_initial_request_filters_later_name_and_designation_history():
    text = "门卫说：「请进。」\n他后来介绍自己为沈宁。\n"
    horizon = text.index("\n") + 1
    person = EvidencePerson(
        "p",
        (
            IdentityFact("门卫", "designation", 0),
            IdentityFact("沈宁", "name", len(text)),
            IdentityFact("林舟的父亲", "relation", len(text)),
            IdentityFact("资深教师", "description", len(text)),
        ),
    )
    a, b = text.index("「"), text.index("」") + 1
    task = EvidenceIndex(text, (person,)).task(((a, b),), reading_mode="initial", horizon=horizon)
    payload = json.dumps(task.messages(), ensure_ascii=False)
    assert task.candidates[0].name == "门卫"
    assert all(v not in payload for v in ["沈宁", "林舟的父亲", "资深教师"])


def test_new_evidence_policy_has_a_distinct_cold_roster_dependency(tmp_path, monkeypatch):
    text = "我在门旁。"
    task = ColdRosterTask(text, len(text))
    assert EVIDENCE_VERSION == "evidence-index-2"
    monkeypatch.setattr("ndr.evaluation.cold_roster.EVIDENCE_VERSION", "evidence-index-1")
    old = task.fingerprint()
    path = tmp_path / "old.trial.sqlite3"
    CallJournal(path, dependency_fingerprint=old, max_calls=2, max_tokens=20000)
    monkeypatch.setattr("ndr.evaluation.cold_roster.EVIDENCE_VERSION", "evidence-index-2")
    with pytest.raises(SnapshotChanged):
        CallJournal(path, dependency_fingerprint=task.fingerprint(), max_calls=2, max_tokens=20000)
