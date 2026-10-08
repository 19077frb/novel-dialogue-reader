from copy import deepcopy
from types import SimpleNamespace

import pytest

from ndr.characters.identity import match_contextual_identity


def records():
    return [
        {"character_id": "named", "name": "林舟", "aliases": ["林舟同学"],
         "identity_records": [{"kind": "name", "value": "林舟"}]},
        {"character_id": "manager", "name": "店长", "description": "林舟打工的书店店长；今天安排排班。"},
    ]


def match(catalog=None, *, context="林舟打工的书店店长；招呼顾客。", proof="店长招呼顾客。",
          role="店长", linked=None):
    facts = [SimpleNamespace(kind="designation", value=role, evidence_spans=[(0, len(proof))]),
             SimpleNamespace(kind="description", value=context, evidence_spans=[(0, len(proof))])]
    return match_contextual_identity(SimpleNamespace(character_id=linked), facts,
                                    records() if catalog is None else catalog,
                                    original=SimpleNamespace(text=proof))


def test_matching_role_and_specific_context_reuses_unique_id_not_new_daily_action():
    assert match() == "manager"
    assert match(context="林舟同学打工的书店店长；换班。") == "manager"


@pytest.mark.parametrize("context", ["书店店长；招呼顾客。", "陆欣打工的书店店长；招呼顾客。",
                                     "林舟打工的餐厅店长；招呼顾客。", "林舟打工的副店长",
                                     "林舟认识的店长"])
def test_same_title_without_matching_concrete_identity_is_not_linked(context):
    assert match(context=context) is None


@pytest.mark.parametrize("proof", ["新店长来到书店。", "这是新的店长。", "店长由另一位接替。",
                                   "新任店长。", "不是原店长。", "书店更换了店长。", "另一名店长。"])
def test_replacement_or_conflicting_evidence_is_not_linked(proof):
    assert match(proof=proof) is None


def test_ambiguous_existing_roles_do_not_pick_first_or_combine_existing_rows():
    catalog = records()
    catalog.append({**catalog[1], "character_id": "another"})
    assert match(catalog) is None
    assert match([]) is None


def test_future_or_unproved_anchor_and_ambiguous_alias_are_not_used():
    catalog = records()
    catalog[0]["identity_records"] = []
    assert match(catalog) is None
    catalog = records()
    catalog.append({"character_id": "other-named", "name": "陆欣", "aliases": ["林舟同学"],
                    "identity_records": [{"kind": "name", "value": "陆欣"}]})
    assert match(catalog, context="林舟同学打工的书店店长") is None


def test_unseen_role_profile_is_not_recovered_from_current_database_metadata():
    catalog = records()
    catalog[1].update(name="", aliases=[], description="", relations=[], identity_records=[])
    assert match(catalog) is None


def test_only_unique_context_matches_and_explicit_model_links_are_not_overridden():
    catalog = deepcopy(records())
    catalog.append({**catalog[1], "character_id": "other", "description": "林舟打工的餐厅店长"})
    assert match(catalog) == "manager"
    assert match(catalog, linked="explicit") is None


def test_validated_relation_alone_can_identify_the_role():
    catalog = records()
    catalog[1]["description"] = ""
    catalog[1]["relations"] = [{"value": "林舟打工的书店店长"}]
    facts = [SimpleNamespace(kind="designation", value="店长", evidence_spans=[(0, 2)]),
             SimpleNamespace(kind="relation", value="林舟打工的书店店长", evidence_spans=[(0, 2)])]
    assert match_contextual_identity(SimpleNamespace(character_id=None), facts, catalog,
                                    original=SimpleNamespace(text="店长")) == "manager"


def test_existing_named_person_can_be_identified_by_their_visible_role():
    catalog = records()
    catalog[1].update(name="刘禾", aliases=["店长"])
    assert match(catalog) == "manager"


@pytest.mark.parametrize("role", ["老师", "女同学", "店员"])
def test_shared_relationship_is_not_sufficient_for_generic_people(role):
    catalog = records()
    catalog[1].update(name=role, description=f"林舟工作的书店{role}")
    assert match(catalog, role=role, context=f"林舟工作的书店{role}") is None
