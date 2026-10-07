import json
from dataclasses import replace

import pytest

from ndr.evaluation.evidence import EvidenceIndex, EvidencePerson, IdentityFact, blocks


def person(identity="p", name="林舟", **kwargs):
    return EvidencePerson(identity, (IdentityFact(name, "name", 0),), **kwargs)


def test_initial_metadata_history_filters_names_aliases_descriptions_and_relations():
    p = EvidencePerson(
        "p",
        (
            IdentityFact("门卫", "name", 0),
            IdentityFact("值班人", "alias", 3),
            IdentityFact("在门口值班", "description", 3),
            IdentityFact("林舟", "name", 15),
            IdentityFact("林老师", "alias", 18),
            IdentityFact("退休教师", "description", 18),
            IdentityFact("周遥的父亲", "relation", 18),
        ),
    )
    index = EvidenceIndex("门卫说：「请进。」\n他的真名是林舟。", (p,))
    task = index.task(((4, 9),), reading_mode="initial", horizon=10)
    payload = json.loads(task.messages()[1]["content"])
    assert task.candidates[0].name == "门卫"
    assert task.candidates[0].aliases == ("值班人",)
    assert task.candidates[0].description == "在门口值班"
    assert "林舟" not in json.dumps(payload, ensure_ascii=False)
    assert all(f["visible_from_cp"] <= 10 for f in task.identity_facts)
    reread = index.task(((4, 9),))
    assert reread.candidates[0].name == "林舟"
    assert "周遥的父亲" not in reread.candidates[0].aliases
    assert any(f["kind"] == "relation" for f in reread.identity_facts)


def test_same_alias_is_retrieval_only_and_does_not_merge_identities():
    index = EvidenceIndex("同学说：「你好。」", (person("a", "同学"), person("b", "同学")))
    task = index.task(((4, 9),))
    assert {c.character_id for c in task.candidates} == {"a", "b"}
    assert task.evidence_hints[0]["mentioned_candidates"] == ["C1", "C2"]


def test_locality_ranking_and_soft_cap_keep_pov_and_explicit_participants():
    text = "林舟在门口。\n周遥说：「你好。」\n"
    index = EvidenceIndex(text, (person("a"), person("b", "周遥"), person("c", "江雨")))
    start = text.index("「")
    task = index.task(((start, start + 5),), margin=1, max_candidates=1)
    assert task.candidates[0].character_id == "b"
    retained = index.task(((start, start + 5),), max_candidates=1, pov_id="a", retain_ids=("c",))
    assert {c.character_id for c in retained.candidates} == {"a", "c"}
    assert next(c.character_id for c in retained.candidates if c.ref == retained.pov_ref) == "a"


def test_original_spans_gaps_and_anchors_are_exact_and_bounded():
    text = (
        "林舟来到学校。\n林舟又停了下来。\n林舟说：「你好。」\n周遥回答：「再见。」\n林舟走了。\n"
    )
    spans = tuple((a, text.index("」", a) + 1) for a in [text.index("「"), text.rindex("「")])
    index = EvidenceIndex(text, (person(), person("b", "周遥")))
    task = index.task(spans, margin=0, anchors_per_person=1)
    for row in task.context:
        assert row["text"] == text[row["start_cp"] : row["end_cp"]]
    assert task.references["Q1"] == f"quote:{spans[0][0]}:{spans[0][1]}"
    assert task.gap_next_quote["G1"] == "Q2"
    assert sum(row["ref"].startswith("E") for row in task.context) <= 2
    no_anchors = index.task(spans, margin=0, anchors_per_person=0)
    assert not any(row["ref"].startswith("E") for row in no_anchors.context)
    assert index.task(spans, margin=0, anchors_per_person=1).fingerprint() == task.fingerprint()


@pytest.mark.parametrize("size", [8, 16, 32])
def test_block_ablations_preserve_every_target_once(size):
    spans = tuple((i * 2, i * 2 + 1) for i in range(65))
    result = blocks(spans, size)
    assert tuple(span for block in result for span in block) == spans
    assert all(0 < len(block) <= size for block in result)


@pytest.mark.parametrize(
    "kwargs",
    [{"horizon": -1}, {"reading_mode": "invalid"}, {"margin": -1}, {"max_candidates": 0}],
)
def test_invalid_retrieval_limits_fail_closed(kwargs):
    with pytest.raises(ValueError):
        EvidenceIndex("「你好。」", (person(),)).task(((0, 5),), **kwargs)


def test_initial_requires_horizon_and_never_sends_target_or_anchor_after_it():
    index = EvidenceIndex("「你好。」\n林舟说。", (person(),))
    with pytest.raises(ValueError, match="explicit horizon"):
        index.task(((0, 5),), reading_mode="initial")
    with pytest.raises(ValueError, match="exceed"):
        index.task(((0, 5),), reading_mode="initial", horizon=4)
    task = index.task(((0, 5),), reading_mode="initial", horizon=5)
    assert all(row["end_cp"] <= 5 for row in task.context)
    with pytest.raises(ValueError, match="Future identity fact"):
        replace(task, identity_facts=({"candidate": "C1", "visible_from_cp": 6},))
    with pytest.raises(ValueError, match="unsent"):
        replace(task, evidence_hints=({"ref": "E99"},))
    with pytest.raises(ValueError, match="Relay"):
        replace(task, relay=({"ref": "E99", "candidate": "C1"},))


@pytest.mark.parametrize(
    "args",
    [("", "name", 0), ("林舟", "name", -1), ("林舟", "name", 0, ((0, 2),))],
)
def test_identity_fact_cannot_leak_before_its_original_evidence(args):
    with pytest.raises(ValueError):
        IdentityFact(*args)


def test_identity_proof_must_be_inside_the_snapshot_and_people_have_distinct_ids():
    p = EvidencePerson("p", (IdentityFact("林舟", "name", 5, ((0, 5),)),))
    with pytest.raises(ValueError, match="outside"):
        EvidenceIndex("短文", (p,))
    with pytest.raises(ValueError, match="Duplicate"):
        EvidenceIndex("原文", (person(), person()))


def test_retrieval_cues_do_not_claim_speaker_or_gold_and_relations_are_not_aliases():
    p = EvidencePerson(
        "p", (IdentityFact("林舟", "name", 0), IdentityFact("周遥的父亲", "relation", 0))
    )
    text = "林舟走进来：「周遥，你好。」"
    task = EvidenceIndex(text, (p,)).task(((text.index("「"), len(text)),))
    hint = task.evidence_hints[0]
    assert hint["entry_exit_possible"]
    assert all("speaker" not in key and "gold" not in key for key in hint)
    assert "周遥的父亲" not in task.candidates[0].aliases
