import hashlib
import json

import pytest

from ndr.characters.facts import CharacterIdentityFact, IdentityLink, IdentityProfileUpdate
from ndr.domain.enums import CharacterSource
from ndr.exports.identities import freeze_identity
from ndr.storage.models import BookCharacter, BookVersion


def prepared():
    text = "少女说她名叫小舟。另一位同学随后到来。"
    version = BookVersion(
        id="v",
        canonical_length_cp=len(text),
        canonical_sha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    person = BookCharacter(
        id="p",
        book_version_id="v",
        canonical_name="最终姓名",
        aliases_json='["未来别名"]',
        description="未来说明",
        source=CharacterSource.MODEL,
        confirmation_source="model",
        user_confirmed=False,
        name_locked=False,
        first_seen_cp=1,
        preferred_color_index=4,
        identity_facts_json="[]",
        presentation_history_json="[]",
        version=1,
    )
    return person, version


def fact(version, **values):
    return CharacterIdentityFact(
        kind="designation",
        value="少女",
        visible_from_cp=2,
        canonical_sha256=version.canonical_sha256,
        evidence_spans=((0, 2),),
        source="source",
        source_ref="original",
        accepted=True,
        **values,
    )


def update(version, field, value, cp=10, **values):
    return IdentityProfileUpdate(
        field=field,
        value=value,
        visible_from_cp=cp,
        canonical_sha256=version.canonical_sha256,
        source="model",
        source_ref="decision",
        accepted=values.pop("accepted", True),
        **values,
    )


def store(person, records):
    person.identity_facts_json = json.dumps([r.model_dump(mode="json") for r in records])


def test_reread_freezes_metadata_pending_records_and_source_without_human_conversion():
    person, version = prepared()
    store(person, (fact(version), update(version, "aliases", ("候选别名",), accepted=False)))
    before = person.identity_facts_json
    frozen = freeze_identity(person, version, horizon=None)
    assert frozen["name"] == "最终姓名" and frozen["aliases"] == ["未来别名"]
    assert len(frozen["records"]) == 2 and not frozen["records"][1]["accepted"]
    assert frozen["source"] == "MODEL" and not frozen["user_confirmed"]
    frozen["records"][0]["value"] = "修改副本"
    frozen["aliases"].append("修改副本")
    assert person.identity_facts_json == before and person.version == 1
    assert person.aliases_json == '["未来别名"]'


def test_position_safe_omits_future_fields_pending_updates_and_unaccepted_links():
    person, version = prepared()
    link = IdentityLink(
        source_id="old",
        target_id=person.id,
        visible_from_cp=3,
        source="model",
        source_ref="merge",
        accepted=False,
    )
    store(
        person,
        (
            fact(version),
            update(version, "name", "小舟"),
            update(version, "description", "隐含关系", cp=3, identity_links=(link,)),
            update(version, "aliases", ("未经确认",), cp=3, accepted=False),
        ),
    )
    frozen = freeze_identity(person, version, horizon=5)
    assert frozen["name"] == "少女" and frozen["aliases"] == []
    assert frozen["description"] == ""
    raw = json.dumps(frozen, ensure_ascii=False)
    for secret in ("小舟", "最终姓名", "未来", "隐含关系", "未经确认"):
        assert secret not in raw
    assert len(frozen["records"]) == 1


def test_position_safe_manual_clear_does_not_resurrect_history_description():
    person, version = prepared()
    person.presentation_history_json = json.dumps(
        [
            {"cp": 0, "identity": "early", "name": "少女", "description": "旧说明"},
            {"cp": 10, "identity": "later", "name": "小舟", "description": "未来说明"},
        ]
    )
    store(person, (update(version, "description", "", cp=3),))
    frozen = freeze_identity(person, version, horizon=5)
    assert frozen["name"] == "少女" and frozen["description"] == ""
    assert len(frozen["presentation_history"]) == 1


def test_legacy_without_reveal_history_is_not_assumed_visible():
    person, version = prepared()
    assert freeze_identity(person, version, horizon=5) is None
    assert freeze_identity(person, version, horizon=None)["aliases"] == ["未来别名"]


def test_hidden_first_seen_is_not_bundled_with_earlier_visible_record():
    person, version = prepared()
    person.first_seen_cp = 15
    store(person, (fact(version),))
    assert freeze_identity(person, version, horizon=5)["first_seen_cp"] is None


def test_every_merge_link_gates_transferred_records_without_dropping_audit_chain():
    person, version = prepared()
    early = IdentityLink(
        source_id="original",
        target_id="middle",
        visible_from_cp=3,
        source="user",
        source_ref="first-merge",
        accepted=True,
    )
    late = IdentityLink(
        source_id="middle",
        target_id=person.id,
        visible_from_cp=15,
        source="model",
        source_ref="second-merge",
        accepted=True,
    )
    transferred = update(version, "name", "旧身份", cp=2, identity_links=(early, late))
    store(person, (fact(version), transferred))
    initial = freeze_identity(person, version, horizon=5)
    assert initial["name"] == "少女" and len(initial["records"]) == 1
    final = freeze_identity(person, version, horizon=None)
    assert final["records"][1] == transferred.model_dump(mode="json")
    assert len(final["records"][1]["identity_links"]) == 2


@pytest.mark.parametrize("horizon", [-1, True, 999])
def test_invalid_horizon_is_rejected(horizon):
    person, version = prepared()
    with pytest.raises(ValueError):
        freeze_identity(person, version, horizon=horizon)


@pytest.mark.parametrize(
    "history",
    [
        "{}",
        '[{"cp":true}]',
        '[{"cp":999}]',
        '[{"cp":0,"identity":"p","name":"少女","description":"","future":"秘密"}]',
    ],
)
def test_invalid_history_is_not_silently_exported(history):
    person, version = prepared()
    person.presentation_history_json = history
    with pytest.raises(ValueError):
        freeze_identity(person, version, horizon=None)


def test_invalid_fact_and_wrong_original_are_rejected_as_a_whole_block():
    person, version = prepared()
    store(person, (fact(version),))
    person.identity_facts_json = person.identity_facts_json.replace(
        version.canonical_sha256, "0" * 64
    )
    with pytest.raises(ValueError):
        freeze_identity(person, version, horizon=None)
