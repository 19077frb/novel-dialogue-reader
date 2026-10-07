"""Original fixtures for fact boundaries, not identity semantic quality."""

import hashlib
import json

import pytest
from pydantic import ValidationError

from ndr.characters.facts import (
    CharacterIdentityFact,
    IdentityLink,
    OriginalIdentitySnapshot,
    append_identity_facts,
    identity_facts_fingerprint,
    prepare_merged_identity_facts,
    visible_identity_profile,
)
from ndr.storage.models import BookCharacter, BookVersion

TEXT = "路人说：「你好。」\n林舟走来，陆欣称呼他为小舟。\n后来得知林舟是学生会长。\n"
SHA = hashlib.sha256(TEXT.encode()).hexdigest()


def objects():
    version = BookVersion(id="v1", canonical_sha256=SHA, canonical_length_cp=len(TEXT))
    character = BookCharacter(
        id="c1",
        book_version_id="v1",
        canonical_name="未来姓名",
        aliases_json='["未来别名"]',
        description="未来说明",
        identity_facts_json="[]",
        version=1,
        user_confirmed=False,
    )
    return character, version, OriginalIdentitySnapshot("v1", SHA, TEXT)


def fact(kind="name", value="林舟", *, evidence_value=None, **kwargs):
    literal = evidence_value or value
    start = TEXT.index(literal)
    end = start + len(literal)
    return CharacterIdentityFact(
        kind=kind,
        value=value,
        visible_from_cp=end,
        canonical_sha256=SHA,
        evidence_spans=((start, end),),
        source="model",
        source_ref="roster:test:L1",
        accepted=True,
        **kwargs,
    )


def test_legacy_current_metadata_is_not_a_guessed_visible_fact():
    character, version, _ = objects()
    visible = visible_identity_profile(character, version, horizon=len(TEXT))
    assert visible["name"] is None and visible["aliases"] == ()
    assert not visible["description"] and visible["facts"] == ()
    assert character.identity_facts_json == "[]" and character.version == 1


def test_each_name_alias_description_relation_has_its_own_reveal_and_origin():
    character, version, original = objects()
    role = fact("designation", "路人")
    name = fact()
    alias = fact("alias", "小舟")
    relation = fact("relation", "陆欣的同伴", evidence_value="小舟")
    description = fact("description", "学生会长")
    assert append_identity_facts(
        character, version, (role, name, alias, relation, description), original=original
    )
    early = visible_identity_profile(character, version, horizon=role.visible_from_cp)
    assert early["name"] == "路人" and not early["aliases"] and not early["relations"]
    named = visible_identity_profile(character, version, horizon=name.visible_from_cp)
    assert named["name"] == "林舟" and named["aliases"] == ("路人",)
    assert not named["description"] and not named["relations"]
    later = visible_identity_profile(character, version, horizon=alias.visible_from_cp)
    assert later["aliases"] == ("路人", "小舟")
    assert later["relations"] == (relation,)
    assert "陆欣的同伴" not in later["aliases"]
    final = visible_identity_profile(character, version, horizon=len(TEXT))
    assert final["description"] == "学生会长"
    assert all(f.source == "model" for f in final["facts"])
    assert not character.user_confirmed
    assert character.canonical_name == "未来姓名"  # Storage doesn't silently rename anyone.


def test_later_role_does_not_obscure_name_and_description_is_not_blindly_concatenated():
    character, version, original = objects()
    role = fact("designation", "路人").model_copy(update={"visible_from_cp": len(TEXT)})
    old = fact("description", "原先是路人", evidence_value="路人")
    new = fact("description", "学生会长")
    append_identity_facts(character, version, (fact(), role, old, new), original=original)
    profile = visible_identity_profile(character, version, horizon=len(TEXT))
    assert profile["name"] == "林舟" and profile["description"] == "学生会长"


def test_acceptance_is_explicit_and_is_not_human_confirmation():
    character, version, original = objects()
    proposed = fact().model_copy(update={"accepted": False})
    append_identity_facts(character, version, (proposed,), original=original)
    before = identity_facts_fingerprint(character, version)
    assert visible_identity_profile(character, version, horizon=len(TEXT))["name"] is None
    append_identity_facts(character, version, (fact(),), original=original)
    assert identity_facts_fingerprint(character, version) != before
    assert visible_identity_profile(character, version, horizon=len(TEXT))["name"] == "林舟"
    assert not character.user_confirmed


def test_exact_duplicate_is_idempotent_without_revision_or_fingerprint_change():
    character, version, original = objects()
    append_identity_facts(character, version, (fact(), fact()), original=original)
    assert character.version == 2 and len(json.loads(character.identity_facts_json)) == 1
    before = identity_facts_fingerprint(character, version)
    assert not append_identity_facts(character, version, (fact(),), original=original)
    assert character.version == 2 and identity_facts_fingerprint(character, version) == before


@pytest.mark.parametrize(
    "change",
    [
        {"visible_from_cp": True},
        {"visible_from_cp": -1},
        {"accepted": 1},
        {"source_ref": " "},
        {"value": " "},
        {"kind": "unknown"},
        {"evidence_spans": ()},
        {"evidence_spans": ((0, 0),)},
        {"evidence_spans": ((0, True),)},
        {"evidence_spans": ((0, 2), (0, 2))},
        {"visible_from_cp": 0},
        {"canonical_sha256": "wrong"},
    ],
)
def test_malformed_fact_is_rejected(change):
    with pytest.raises(ValidationError):
        CharacterIdentityFact.model_validate({**fact().model_dump(), **change})


@pytest.mark.parametrize(
    "bad",
    [
        {"value": "陆欣"},
        {"canonical_sha256": "a" * 64},
        {"visible_from_cp": len(TEXT) + 1},
        {"evidence_spans": ((9, 10),)},
    ],
)
def test_invalid_second_fact_rejects_entire_block_without_partial_mutation(bad):
    character, version, original = objects()
    changed = fact().model_copy(update=bad)
    with pytest.raises((ValueError, ValidationError)):
        append_identity_facts(character, version, (fact(), changed), original=original)
    assert character.identity_facts_json == "[]" and character.version == 1


@pytest.mark.parametrize("horizon", [None, True, -1, len(TEXT) + 1])
def test_visible_read_requires_explicit_valid_horizon(horizon):
    character, version, _ = objects()
    with pytest.raises(ValueError):
        visible_identity_profile(character, version, horizon=horizon)


def test_wrong_version_missing_original_and_wrong_hash_are_rejected():
    character, version, original = objects()
    with pytest.raises(ValueError, match="hash mismatch"):
        OriginalIdentitySnapshot("v1", "a" * 64, TEXT)
    with pytest.raises(ValueError, match="required"):
        append_identity_facts(character, version, (fact(),))
    other = OriginalIdentitySnapshot("other", SHA, TEXT)
    with pytest.raises(ValueError, match="another book version"):
        append_identity_facts(character, version, (fact(),), original=other)
    character.book_version_id = "other"
    with pytest.raises(ValueError, match="another book version"):
        append_identity_facts(character, version, (fact(),), original=original)


def test_user_assertion_keeps_explicit_reveal_without_fabricating_original_evidence():
    character, version, _ = objects()
    manual = fact().model_copy(
        update={
            "source": "user",
            "source_ref": "manual:test",
            "evidence_spans": (),
            "visible_from_cp": len(TEXT),
        }
    )
    append_identity_facts(character, version, (manual,))
    assert visible_identity_profile(character, version, horizon=len(TEXT) - 1)["name"] is None
    assert visible_identity_profile(character, version, horizon=len(TEXT))["facts"] == (manual,)


def merge_objects():
    source, version, original = objects()
    source.id = "source"
    target = BookCharacter(
        id="target",
        book_version_id=version.id,
        canonical_name="未来姓名",
        identity_facts_json="[]",
        version=1,
    )
    append_identity_facts(source, version, (fact(), fact("alias", "小舟")), original=original)
    link = IdentityLink(
        source_id=source.id,
        target_id=target.id,
        visible_from_cp=len(TEXT) - 1,
        source="user",
        source_ref="merge:test",
        accepted=True,
    )
    return source, target, version, original, link


def test_merge_keeps_original_reveal_and_proof_with_separate_identity_gate():
    source, target, version, original, link = merge_objects()
    before = source.identity_facts_json
    prepared = prepare_merged_identity_facts(source, target, version, link=link, original=original)
    assert source.identity_facts_json == before and target.identity_facts_json == "[]"
    assert source.version == 2 and target.version == 1
    target.identity_facts_json = prepared
    assert (
        visible_identity_profile(target, version, horizon=link.visible_from_cp - 1)["name"] is None
    )
    visible = visible_identity_profile(target, version, horizon=link.visible_from_cp)
    assert visible["name"] == "林舟" and visible["aliases"] == ("小舟",)
    transferred = visible["facts"][0]
    assert transferred.model_dump(exclude={"identity_links"}) == fact().model_dump(
        exclude={"identity_links"}
    )
    assert transferred.identity_links == (link,)
    assert transferred.visible_from_cp < link.visible_from_cp


def test_repeated_merge_cannot_backdate_previous_identity_association():
    source, target, version, original, link = merge_objects()
    target.identity_facts_json = prepare_merged_identity_facts(
        source, target, version, link=link, original=original
    )
    final = BookCharacter(
        id="final", book_version_id=version.id, identity_facts_json="[]", version=1
    )
    earlier_link = IdentityLink(
        source_id=target.id,
        target_id=final.id,
        visible_from_cp=2,
        source="model",
        source_ref="merge:second",
        accepted=True,
    )
    final.identity_facts_json = prepare_merged_identity_facts(
        target, final, version, link=earlier_link, original=original
    )
    assert (
        visible_identity_profile(final, version, horizon=link.visible_from_cp - 1)["name"] is None
    )
    profile = visible_identity_profile(final, version, horizon=len(TEXT))
    assert profile["name"] == "林舟" and profile["facts"][0].identity_links == (link, earlier_link)


def test_transferred_names_and_descriptions_do_not_override_own_visible_facts():
    source, target, version, original, link = merge_objects()
    append_identity_facts(
        target,
        version,
        (fact("name", "陆欣"), fact("description", "陆欣的说明", evidence_value="陆欣")),
        original=original,
    )
    append_identity_facts(source, version, (fact("description", "学生会长"),), original=original)
    target.identity_facts_json = prepare_merged_identity_facts(
        source, target, version, link=link, original=original
    )
    visible = visible_identity_profile(target, version, horizon=len(TEXT))
    assert visible["name"] == "陆欣" and visible["description"] == "陆欣的说明"
    assert visible["aliases"] == ("林舟", "小舟")


def test_unaccepted_link_does_not_promote_accepted_source_facts():
    source, target, version, original, link = merge_objects()
    target.identity_facts_json = prepare_merged_identity_facts(
        source, target, version, link=link.model_copy(update={"accepted": False}), original=original
    )
    assert visible_identity_profile(target, version, horizon=len(TEXT))["facts"] == ()


@pytest.mark.parametrize(
    "change",
    [
        {"source_id": "other"},
        {"target_id": "other"},
        {"accepted": 1},
        {"visible_from_cp": True},
        {"visible_from_cp": len(TEXT) + 1},
        {"source_ref": " "},
    ],
)
def test_invalid_merge_is_atomic(change):
    source, target, version, original, link = merge_objects()
    before = source.identity_facts_json
    with pytest.raises(ValueError):
        prepare_merged_identity_facts(
            source, target, version, link=link.model_copy(update=change), original=original
        )
    assert source.identity_facts_json == before and target.identity_facts_json == "[]"


def test_legacy_empty_merge_does_not_guess_name_or_reveal():
    source, target, version, original, link = merge_objects()
    source.identity_facts_json = "[]"
    assert (
        prepare_merged_identity_facts(source, target, version, link=link, original=original) == "[]"
    )


@pytest.mark.parametrize("mode", ["discontinuous", "cyclic", "wrong_owner", "future"])
def test_corrupt_persisted_identity_chain_is_rejected(mode):
    source, target, version, original, link = merge_objects()
    target.identity_facts_json = prepare_merged_identity_facts(
        source, target, version, link=link, original=original
    )
    payload = json.loads(target.identity_facts_json)
    chain = payload[0]["identity_links"]
    if mode == "wrong_owner":
        chain[0]["target_id"] = "elsewhere"
    elif mode == "future":
        chain[0]["visible_from_cp"] = len(TEXT) + 1
    else:
        chain.append(
            {
                **chain[0],
                "source_id": "disconnected" if mode == "discontinuous" else target.id,
                "target_id": "final" if mode == "discontinuous" else source.id,
            }
        )
    target.identity_facts_json = json.dumps(payload)
    with pytest.raises(ValueError):
        visible_identity_profile(target, version, horizon=len(TEXT))
