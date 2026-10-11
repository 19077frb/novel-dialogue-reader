"""Authored identity fixtures; no network or user data mutation."""

import json
from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from ndr.characters.prompt_catalog import (
    IDENTITY_PROMPT_VERSION,
    MAX_RELATIONS,
    RELATION_TOKENS,
    check_version,
    compact_catalog,
)
from ndr.context.budget import estimate_tokens
from ndr.storage.cache import fingerprint
from unit.test_production_expression_task import fixture


def test_duplicate_history_is_bounded_without_mutating_original():
    person = {"character_id": "c", "name": "林舟", "aliases": ["小舟"],
              "description": "书店店员", "name_locked": True,
              "identity_records": [{"kind": "name", "value": "林舟", "source": "model",
                                    "source_ref": str(i), "evidence_spans": [[i, i+1]]}
                                   for i in range(1000)],
              "relations": [{"value": "许晴的朋友", "source_ref": str(i)} for i in range(1000)]}
    saved = deepcopy(person)
    output = compact_catalog([person])[0]
    assert person == saved
    assert output["aliases"] == person["aliases"] and output["name_locked"] is True
    assert output["description"] == person["description"]
    assert len(output["identity_records"]) == 1 and len(output["relations"]) == 1
    assert len(json.dumps(output)) < len(json.dumps(person)) / 100


def test_relations_prefer_context_and_preserve_complete_values_within_budget():
    people = [{"name": "林舟", "relations": [{"value": f"旧友{i}的朋友"} for i in range(20)]
               + [{"value": "许晴的邻居"}, {"value": "很长" * 1000}]},
              {"name": "许晴", "aliases": []}]
    result = compact_catalog(people, context="许晴正在讲话")[0]
    assert result["relations"][0]["value"] == "许晴的邻居"
    assert len(result["relations"]) == MAX_RELATIONS
    assert sum(estimate_tokens(x["value"]) + 8 for x in result["relations"]) <= RELATION_TOKENS
    assert all(x["value"] in {r["value"] for r in people[0]["relations"]} for x in result["relations"])


def test_field_edits_have_sources_not_duplicate_values_or_literal_evidence():
    records = [{"name": "林舟", "description": "店员", "aliases": ["小舟"],
                "identity_records": [
                    {"kind": "name", "value": "林舟", "source": s} for s in ("model", "user")
                ] + [{"kind": "profile_update", "field": "name", "value": "林舟", "source": "user"}]}]
    result = compact_catalog(records)[0]
    assert len(result["identity_records"]) == 2
    assert result["field_sources"] == {"name": "user"}
    assert "evidence_spans" not in json.dumps(result)


def test_new_transport_preserves_local_profiles_and_initial_visibility():
    from ndr.llm.expression_task import build_production_expression_task

    _, _, window, state = fixture()
    old = build_production_expression_task(window, state)
    new = replace(old, identity_prompt_version=IDENTITY_PROMPT_VERSION)
    local = deepcopy(new.effective_profiles)
    data = json.loads(new.messages()[1]["content"])
    assert new.effective_profiles == local == old.effective_profiles
    assert data["candidates"] == json.loads(old.messages()[1]["content"])["candidates"]
    profile = data["effective_identity_profiles"][0]
    assert profile["character_id"] == data["candidates"][0]["id"]
    assert "canonical_name" not in profile and "description" not in profile
    assert profile["field_sources"] == {"name": "user", "aliases": "user", "description": "user"}
    assert "未来姓名" not in json.dumps(data, ensure_ascii=False)
    assert new.fingerprint() != old.fingerprint()
    # Reproduce the historical dataclass fingerprint with no newly introduced field.
    from ndr.evaluation.compact import COMPILER_VERSION, PROMPT_VERSION, PROTOCOL_VERSION

    fields = asdict(old)
    fields.pop("identity_prompt_version")
    fields.pop("auxiliary_protocol")
    fields.pop("nonperson_policy_version")
    assert old.fingerprint() == fingerprint({
        "protocol": PROTOCOL_VERSION, "prompt": PROMPT_VERSION,
        "compiler": COMPILER_VERSION, "task": fields,
    })


def test_unknown_prompt_version_is_rejected():
    with pytest.raises(ValueError):
        check_version("unknown")
