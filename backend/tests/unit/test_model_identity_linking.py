"""Prompt wiring with original inputs, not a model-quality evaluation."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest

from ndr.evaluation.compact import Candidate, CompactTask
from ndr.evaluation.expression_owner import ExplicitOwnerProtocol
from ndr.evaluation.owner_constraints import ConstrainedOwnerProtocol
from ndr.llm.prompts.labeling import IDENTITY_LINKING_POLICY, build_labeling_messages
from ndr.llm.prompts.roster import build_roster_messages
from ndr.llm.prompts.roster_repair import build_roster_repair_messages

CATALOG = [
    {"character_id": "teacher-a", "name": "江老师", "aliases": ["班导师"],
     "description": "林舟所在班级的班导师。"},
    {"character_id": "teacher-b", "name": "宋老师", "aliases": ["班导师"],
     "description": "周遥所在班级的班导师。"},
]
LINES = ["林舟看着讲台。班导师江老师说：", "「别玩过头。」"]


@pytest.mark.parametrize("sourced", [False, True])
def test_roster_and_repair_share_comparison_without_changing_catalog_or_schema(sourced):
    people, lines = deepcopy(CATALOG), list(LINES)
    messages = build_roster_messages(
        chapter_title="林舟的冬日", chapter_lines=lines,
        existing_characters=people, sourced=sourced,
    )
    assert IDENTITY_LINKING_POLICY in messages[0]["content"]
    assert "character_id必须复用existing_characters提供的ID" in messages[0]["content"]
    assert "当前实际发送原文" in messages[0]["content"]
    data = json.loads(messages[1]["content"].split("任务参数（JSON）：\n", 1)[1]
                      .split("\n\n", 1)[0])
    assert data["existing_characters"] == people
    assert people == CATALOG and lines == LINES
    repair = build_roster_repair_messages(messages, {"groups": [], "retained_characters": []})
    assert IDENTITY_LINKING_POLICY in repair[0]["content"]
    assert "不能改写保留人物" in repair[0]["content"]
    assert "retained_characters" in repair[1]["content"]
    assert "existing_characters" in repair[1]["content"]


def task():
    return CompactTask(
        ("Q1",), {"G1": "gap", "Q1": "quote"},
        ({"ref": "G1", "kind": "outer_gap", "text": LINES[0], "start_cp": 0, "end_cp": 30},
         {"ref": "Q1", "kind": "target_quote", "text": LINES[1], "start_cp": 30, "end_cp": 40}),
        tuple(Candidate(f"C{i}", p["character_id"], p["name"], tuple(p["aliases"]),
                        p["description"]) for i, p in enumerate(CATALOG, 1)),
        {"G1": "Q1"}, reading_mode="initial", visible_horizon_cp=40,
    )


@pytest.mark.parametrize("protocol", [None, ExplicitOwnerProtocol, ConstrainedOwnerProtocol])
def test_all_block_owner_protocols_request_reuse_not_new_n(protocol):
    source = task()
    before = deepcopy(source)
    adapter = protocol(source) if protocol else source
    messages = adapter.messages()
    assert IDENTITY_LINKING_POLICY in messages[0]["content"]
    assert "复用其C编号" in messages[0]["content"]
    assert "仅同职务、同姓或相似性格不能证明同一人" in messages[0]["content"]
    assert "身份比较与本句表达归属分别判断" in messages[0]["content"]
    data = json.loads(messages[1]["content"])
    assert [c["id"] for c in data["candidates"]] == ["C1", "C2"]
    assert data["candidates"][0]["aliases"] == data["candidates"][1]["aliases"]
    assert data["candidates"][0]["description"] != data["candidates"][1]["description"]
    assert source == before
    if protocol:
        payload = {"labels": [{"q": "Q1", "kind": "speech", "character": "C1",
                               "basis": "direct", "evidence": ["G1"]}]}
        result = adapter.compile(payload)
        assert result["rows"][0]["character_id"] == "teacher-a"
        assert result["original_payload"]["new_characters"] == []


def test_legacy_scene_new_is_distinct_from_new_book_identity():
    messages = build_labeling_messages(
        context_lines=LINES, target_ids=["q"], book_characters=CATALOG,
    )
    system = messages[0]["content"]
    assert IDENTITY_LINKING_POLICY in system
    assert "跨场景首次出现仍用 NEW" in system
    assert "复用 character_id" in system
    assert "teacher-a" in messages[1]["content"]


def test_prompt_versions_invalidate_task_and_protocol_bindings(monkeypatch):
    import ndr.evaluation.compact as compact
    import ndr.evaluation.expression_owner as explicit
    import ndr.evaluation.owner_constraints as constrained

    source = task()
    current = source.fingerprint()
    monkeypatch.setattr(compact, "PROMPT_VERSION", "compact-prompt-3")
    assert source.fingerprint() != current
    monkeypatch.undo()
    current = ExplicitOwnerProtocol(source).fingerprint()
    monkeypatch.setattr(explicit, "VERSION", "explicit-expression-owner-1")
    assert ExplicitOwnerProtocol(source).fingerprint() != current
    monkeypatch.undo()
    current = ConstrainedOwnerProtocol(source).fingerprint()
    monkeypatch.setattr(constrained, "VERSION", "explicit-owner-null-field-constraints-2")
    assert ConstrainedOwnerProtocol(source).fingerprint() != current


def test_initial_view_still_rejects_unsent_future_identity():
    source = task()
    future = replace(source.candidates[0], visible_from_cp=41)
    with pytest.raises(ValueError, match="Future identity"):
        replace(source, candidates=(future, source.candidates[1]))
