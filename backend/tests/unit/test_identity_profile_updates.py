"""Explicit profile edits are not original evidence, and must survive merging."""

import hashlib
import json

import pytest

from ndr.characters.facts import (
    CharacterIdentityFact,
    IdentityLink,
    IdentityProfileUpdate,
    OriginalIdentitySnapshot,
    append_identity_facts,
    identity_facts_fingerprint,
    prepare_merged_identity_facts,
    prepare_profile_updates,
    read_identity_facts,
    read_identity_records,
    visible_identity_profile,
)
from ndr.storage.models import BookCharacter, BookVersion

TEXT = "林舟走来，陆欣称呼他为小舟。\n后来得知林舟是学生会长。\n这里还有更多正文作为可见位置边界。"
SHA = hashlib.sha256(TEXT.encode()).hexdigest()


def objects():
    version = BookVersion(id="v", canonical_sha256=SHA, canonical_length_cp=len(TEXT))
    person = BookCharacter(
        id="p",
        book_version_id="v",
        version=1,
        canonical_name="不可回填的最终名",
        identity_facts_json="[]",
    )
    original = OriginalIdentitySnapshot("v", SHA, TEXT)
    return person, version, original


def fact(kind, value):
    start = TEXT.index(value)
    return CharacterIdentityFact(
        kind=kind,
        value=value,
        visible_from_cp=start + len(value),
        canonical_sha256=SHA,
        evidence_spans=((start, start + len(value)),),
        source="model",
        source_ref="roster:test",
        accepted=True,
    )


def update(field, value, cp=20, **changes):
    return IdentityProfileUpdate.model_validate(
        {
            "field": field,
            "value": value,
            "visible_from_cp": cp,
            "canonical_sha256": SHA,
            "source": "user",
            "source_ref": "edit:test",
            "accepted": True,
            **changes,
        }
    )


def apply_updates(person, version, *updates):
    person.identity_facts_json = prepare_profile_updates(person, version, updates)


def test_independent_fields_can_clear_values_without_erasing_original_proof():
    person, version, original = objects()
    old = (fact("name", "林舟"), fact("alias", "小舟"), fact("description", "学生会长"))
    append_identity_facts(person, version, old, original=original)
    cp = 30
    before = person.identity_facts_json
    prepared = prepare_profile_updates(
        person,
        version,
        (
            update("name", "小林", cp),
            update("aliases", (), cp),
            update("description", "", cp),
        ),
    )
    assert person.identity_facts_json == before and person.version == 2
    person.identity_facts_json = prepared
    early = visible_identity_profile(person, version, horizon=cp - 1)
    late = visible_identity_profile(person, version, horizon=cp)
    assert early["name"] == "林舟" and "小舟" in early["aliases"]
    assert early["description"] == "学生会长"
    assert late["name"] == "小林" and late["aliases"] == () and late["description"] == ""
    assert read_identity_facts(person, version) == old
    assert all(r.source == "user" for r in late["updates"])


def test_append_keeps_revisions_and_later_fact_can_reveal_new_alias():
    person, version, original = objects()
    append_identity_facts(
        person, version, (fact("name", "林舟"), fact("alias", "小舟")), original=original
    )
    apply_updates(person, version, update("aliases", (), 20))
    before = identity_facts_fingerprint(person, version)
    later = fact("alias", "会长")
    append_identity_facts(person, version, (later,), original=original)
    assert len(read_identity_records(person, version)) == 4
    assert identity_facts_fingerprint(person, version) != before
    profile = visible_identity_profile(person, version, horizon=len(TEXT))
    assert profile["aliases"] == ("会长",)


def test_source_edits_survive_merge_and_target_can_remove_known_merged_aliases():
    source, version, original = objects()
    append_identity_facts(
        source, version, (fact("name", "林舟"), fact("alias", "小舟")), original=original
    )
    apply_updates(source, version, update("name", "小林", 20), update("aliases", (), 20))
    target = BookCharacter(
        id="target", book_version_id=version.id, version=1, identity_facts_json="[]"
    )
    apply_updates(target, version, update("name", "陆欣", 0))
    link = IdentityLink(
        source_id=source.id,
        target_id=target.id,
        visible_from_cp=30,
        source="user",
        source_ref="merge:test",
        accepted=True,
    )
    target.identity_facts_json = prepare_merged_identity_facts(
        source, target, version, link=link, original=original
    )
    early = visible_identity_profile(target, version, horizon=29)
    late = visible_identity_profile(target, version, horizon=30)
    assert early["aliases"] == () and late["aliases"] == ("小林",)
    assert late["name"] == "陆欣" and "小舟" not in late["aliases"]
    apply_updates(target, version, update("aliases", (), 31))
    assert visible_identity_profile(target, version, horizon=31)["aliases"] == ()
    assert visible_identity_profile(target, version, horizon=30)["aliases"] == ("小林",)
    # Original facts and source revisions remain auditable, not deleted.
    assert len(read_identity_facts(target, version)) == 2
    assert len(read_identity_records(target, version)) == 6


def test_same_position_edit_order_does_not_revive_or_hide_later_merges():
    source, version, original = objects()
    apply_updates(source, version, update("name", "林舟", 0))
    target = BookCharacter(
        id="target", book_version_id=version.id, version=1, identity_facts_json="[]"
    )
    apply_updates(target, version, update("name", "陆欣", 0), update("aliases", (), 30))
    link = IdentityLink(
        source_id=source.id,
        target_id=target.id,
        visible_from_cp=30,
        source="user",
        source_ref="merge:test",
        accepted=True,
    )
    target.identity_facts_json = prepare_merged_identity_facts(
        source, target, version, link=link, original=original
    )
    assert visible_identity_profile(target, version, horizon=30)["aliases"] == ("林舟",)
    apply_updates(target, version, update("aliases", (), 30, source_ref="edit:after-merge"))
    assert visible_identity_profile(target, version, horizon=30)["aliases"] == ()


def test_manual_long_names_and_model_decision_are_not_fabricated_original_facts():
    person, version, _ = objects()
    apply_updates(
        person,
        version,
        update("name", "手工姓名" * 20, 0),
        update("description", "模型整理的说明", 20, source="model", source_ref="merge:job"),
    )
    profile = visible_identity_profile(person, version, horizon=20)
    assert profile["name"] == "手工姓名" * 20 and profile["description"] == "模型整理的说明"
    assert read_identity_facts(person, version) == ()
    assert profile["updates"][-1].source == "model" and not person.user_confirmed


def test_model_update_can_preserve_existing_aliases_beyond_manual_form_limit():
    person, version, _ = objects()
    aliases = tuple(f"原有称呼{i}" for i in range(70))
    apply_updates(
        person, version, update("name", "林舟", 0), update("aliases", aliases, source="model")
    )
    assert visible_identity_profile(person, version, horizon=20)["aliases"] == aliases


def test_backdated_manual_correction_cannot_be_overridden_by_old_future_candidates():
    person, version, original = objects()
    append_identity_facts(
        person,
        version,
        (
            fact("name", "林舟").model_copy(update={"visible_from_cp": 35}),
            fact("alias", "小舟").model_copy(update={"visible_from_cp": 35}),
        ),
        original=original,
    )
    apply_updates(person, version, update("name", "小林", 20), update("aliases", (), 20))
    at_end = visible_identity_profile(person, version, horizon=len(TEXT))
    assert at_end["name"] == "小林" and at_end["aliases"] == ()
    apply_updates(person, version, update("name", "旧修订", 30))
    apply_updates(person, version, update("name", "更正修订", 25, source_ref="later:correction"))
    assert visible_identity_profile(person, version, horizon=24)["name"] == "小林"
    assert visible_identity_profile(person, version, horizon=len(TEXT))["name"] == "更正修订"


def test_unknown_unaccepted_and_invalid_update_blocks_do_not_mutate():
    person, version, _ = objects()
    apply_updates(person, version, update("name", "林舟", 20, accepted=False))
    assert visible_identity_profile(person, version, horizon=20)["name"] is None
    before = person.identity_facts_json
    with pytest.raises(ValueError):
        prepare_profile_updates(
            person, version, (update("aliases", ()), update("name", "陆欣", len(TEXT) + 1))
        )
    assert person.identity_facts_json == before
    person.identity_facts_json = json.dumps([{"kind": "not-a-contract"}])
    with pytest.raises(ValueError):
        read_identity_records(person, version)


@pytest.mark.parametrize(
    "field,value,changes",
    [
        ("aliases", "not a list", {}),
        ("aliases", ("same", "same"), {}),
        ("aliases", (" ",), {}),
        ("aliases", ("x" * 129,), {}),
        ("name", "", {}),
        ("name", "x" * 129, {}),
        ("name", (), {}),
        ("description", "x" * 513, {}),
        ("description", (), {}),
        ("name", "林舟", {"visible_from_cp": True}),
        ("name", "林舟", {"accepted": 1}),
        ("name", "林舟", {"source_ref": " "}),
        ("name", "林舟", {"unexpected": "field"}),
    ],
)
def test_profile_updates_are_strict(field, value, changes):
    with pytest.raises(ValueError):
        update(field, value, **changes)
