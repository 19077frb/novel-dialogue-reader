"""Original fixtures for fact boundaries, not identity semantic quality."""

import hashlib
import json

import pytest
from pydantic import ValidationError

from ndr.characters.facts import (
    CharacterIdentityFact,
    OriginalIdentitySnapshot,
    append_identity_facts,
    identity_facts_fingerprint,
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
