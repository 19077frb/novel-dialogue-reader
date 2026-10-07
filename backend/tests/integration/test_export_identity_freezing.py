import json

from sqlalchemy import select

from fixtures.corrections import import_sample, session_scope
from ndr.characters.facts import IdentityProfileUpdate
from ndr.storage.models import BookCharacter, BookVersion, ExportSnapshot, Scene, SpeakerGroup
from ndr.storage.transactions import transaction


def preview(client, book_id, policy="reread"):
    response = client.post(
        f"/api/books/{book_id}/exports/preview",
        json={
            "visibility_policy": policy,
            "style": {"preset": "color_and_label"},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_real_preview_freezes_unassigned_person_and_invalidates_on_alias_only_change(
    migrated_client,
    migrated_settings,
):
    data = import_sample(migrated_client)
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        person = BookCharacter(
            book_version_id=data["book_version_id"],
            canonical_name="小舟",
            aliases_json='["少女"]',
            description="已知说明",
        )
        session.add(person)
        session.flush()
        person_id = person.id
    first = preview(migrated_client, data["book_id"])
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        old = session.get(ExportSnapshot, first["snapshot_id"])
        frozen = old.annotation_projection_json
        payload = json.loads(frozen)
        assert payload["character_snapshot"]["characters"][0]["aliases"] == ["少女"]
        assert not payload["character_snapshot"]["characters"][0]["user_confirmed"]
        person = session.get(BookCharacter, person_id)
        person.aliases_json = '["新别名"]'
        person.version += 1
    second = preview(migrated_client, data["book_id"])
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        old = session.get(ExportSnapshot, first["snapshot_id"])
        fresh = session.get(ExportSnapshot, second["snapshot_id"])
        assert old.annotation_projection_json == frozen
        assert old.snapshot_hash != fresh.snapshot_hash
        assert json.loads(fresh.annotation_projection_json)["character_snapshot"]["characters"][0][
            "aliases"
        ] == ["新别名"]
        assert len(list(session.scalars(select(BookCharacter)))) == 1


def test_real_position_safe_preview_excludes_future_and_isolates_invalid_ledger(
    migrated_client,
    migrated_settings,
):
    data = import_sample(migrated_client)
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        version = session.get(BookVersion, data["book_version_id"])
        update = IdentityProfileUpdate(
            field="name",
            value="后文真名",
            visible_from_cp=10,
            canonical_sha256=version.canonical_sha256,
            source="user",
            source_ref="edit",
            accepted=True,
        )
        session.add(
            BookCharacter(
                book_version_id=version.id,
                canonical_name="后文真名",
                aliases_json='["后文别名"]',
                identity_facts_json=json.dumps([update.model_dump(mode="json")]),
                presentation_history_json=json.dumps(
                    [
                        {"cp": 0, "identity": "early", "name": "少女", "description": ""},
                    ]
                ),
            )
        )
        session.add(
            BookCharacter(
                book_version_id=version.id,
                canonical_name="损坏人物",
                identity_facts_json='[{"kind":"invalid"}]',
            )
        )
    response = preview(migrated_client, data["book_id"], "position_safe")
    assert any("未通过校验" in warning for warning in response["warnings"])
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        payload = json.loads(
            session.get(ExportSnapshot, response["snapshot_id"]).annotation_projection_json
        )
        identities = payload["character_snapshot"]["characters"]
        assert len(identities) == 1 and identities[0]["name"] == "少女"
        assert identities[0]["records"] == [] and identities[0]["aliases"] == []
        assert "后文" not in json.dumps(identities, ensure_ascii=False)
        assert len(list(session.scalars(select(BookCharacter)))) == 2


def test_evidence_only_change_invalidates_preview_without_changing_display_metadata(
    migrated_client,
    migrated_settings,
):
    data = import_sample(migrated_client)
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        person = BookCharacter(book_version_id=data["book_version_id"], canonical_name="小舟")
        session.add(person)
        session.flush()
        person_id = person.id
    before = preview(migrated_client, data["book_id"])
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        version = session.get(BookVersion, data["book_version_id"])
        person = session.get(BookCharacter, person_id)
        pending = IdentityProfileUpdate(
            field="description",
            value="尚待复核的说明",
            visible_from_cp=version.canonical_length_cp,
            canonical_sha256=version.canonical_sha256,
            source="model",
            source_ref="proposal",
            accepted=False,
        )
        person.identity_facts_json = json.dumps([pending.model_dump(mode="json")])
    after = preview(migrated_client, data["book_id"])
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        old = session.get(ExportSnapshot, before["snapshot_id"])
        new = session.get(ExportSnapshot, after["snapshot_id"])
        assert old.snapshot_hash != new.snapshot_hash
        old_person = json.loads(old.annotation_projection_json)["character_snapshot"]["characters"][
            0
        ]
        new_person = json.loads(new.annotation_projection_json)["character_snapshot"]["characters"][
            0
        ]
        assert old_person["name"] == new_person["name"] == "小舟"
        assert old_person["description"] == new_person["description"] == ""
        assert old_person["records"] == [] and len(new_person["records"]) == 1
        assert not new_person["records"][0]["accepted"]


def test_position_safe_group_binding_does_not_disclose_later_merge(
    migrated_client,
    migrated_settings,
):
    data = import_sample(migrated_client)
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        person = BookCharacter(book_version_id=data["book_version_id"], canonical_name="小舟")
        session.add(person)
        session.flush()
        person.presentation_history_json = json.dumps(
            [
                {"cp": 0, "identity": f"character:{person.id}", "name": "小舟", "description": ""},
            ]
        )
        scene = Scene(book_version_id=data["book_version_id"], start_cp=0, end_cp=10)
        session.add(scene)
        session.flush()
        group = SpeakerGroup(
            scene_id=scene.id,
            display_label="少女",
            character_id=person.id,
            canonical_name="小舟",
            presentation_history_json=json.dumps(
                [
                    {"cp": 0, "identity": "unlinked-early", "name": "少女", "description": ""},
                    {
                        "cp": 10,
                        "identity": f"character:{person.id}",
                        "name": "小舟",
                        "description": "",
                    },
                ]
            ),
        )
        session.add(group)
        session.flush()
        group_id, person_id = group.id, person.id
    early = preview(migrated_client, data["book_id"], "position_safe")
    final = preview(migrated_client, data["book_id"])
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        early_payload = json.loads(
            session.get(ExportSnapshot, early["snapshot_id"]).annotation_projection_json
        )
        final_payload = json.loads(
            session.get(ExportSnapshot, final["snapshot_id"]).annotation_projection_json
        )
        assert group_id not in early_payload["speaker_characters"]
        assert early_payload["speaker_identities"][group_id] == "unlinked-early"
        assert final_payload["speaker_characters"][group_id] == f"character:{person_id}"
