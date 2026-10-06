import io
import json
import zipfile

import pytest
from sqlalchemy import select

from fixtures.corrections import (
    create_fake_profile,
    import_sample,
    run_deterministic_job,
    session_scope,
)
from ndr.characters.facts import (
    CharacterIdentityFact,
    IdentityLink,
    IdentityProfileUpdate,
    read_identity_records,
    visible_identity_profile,
)
from ndr.domain.enums import CharacterSource, JobState
from ndr.storage.models import (
    Annotation,
    BookCharacter,
    BookVersion,
    ExportArtifact,
    ExportSnapshot,
    Job,
    Quote,
    Scene,
    SpeakerGroup,
)
from ndr.storage.transactions import transaction

TEXT = "第一章 雨夜\n  少女  走进房间。\n第二章 名字\n少女说她叫小舟。\n小舟同学坐下。\n"


def export_book(client, data, **options):
    response = client.post(
        f"/api/books/{data['book_id']}/exports/preview",
        json={
            "visibility_policy": "reread",
            "style": {"preset": "color_and_label"},
            **options,
        },
    )
    assert response.status_code == 200, response.text
    snapshot = response.json()["data"]["snapshot_id"]
    response = client.post(
        f"/api/books/{data['book_id']}/exports",
        json={
            "snapshot_id": snapshot,
            "format": "epub",
            "style": {"preset": "color_and_label"},
            "idempotency_key": snapshot,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["state"] == "COMPLETED", response.text
    artifact = response.json()["data"]
    response = client.get(f"/api/exports/{artifact['id']}/download")
    assert response.status_code == 200
    ledger = manifest(response.content)["identity_ledger"]
    assert artifact["validation"]["identity_ledger"] == {
        "characters": len(ledger["characters"]),
        "omitted": ledger["omitted_characters"],
    }
    return response.content


def reimport(client, raw):
    response = client.post(
        "/api/books/import",
        files={
            "file": ("restored.epub", raw, "application/epub+zip"),
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def manifest(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return json.loads(archive.read("OEBPS/annotations.json"))


def rewrite(raw, mutate):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive, zipfile.ZipFile(output, "w") as target:
        data = manifest(raw)
        mutate(data)
        for info in archive.infolist():
            body = archive.read(info.filename)
            if info.filename == "OEBPS/annotations.json":
                body = json.dumps(data, ensure_ascii=False).encode()
            target.writestr(info, body)
    return output.getvalue()


def prepare(client, settings):
    data = import_sample(client, TEXT)
    with session_scope(settings) as factory, transaction(factory) as session:
        version = session.get(BookVersion, data["book_version_id"])
        person = BookCharacter(
            book_version_id=version.id,
            canonical_name="小舟",
            aliases_json="[]",
            source=CharacterSource.MODEL,
            confirmation_source="automatic",
            user_confirmed=False,
            first_seen_cp=TEXT.index("少女"),
            preferred_color_index=24,
        )
        session.add(person)
        session.flush()
        first = TEXT.index("少女")
        named = TEXT.index("小舟")
        alias = TEXT.index("小舟同学")
        fact = CharacterIdentityFact(
            kind="designation",
            value="少女",
            visible_from_cp=first + 2,
            canonical_sha256=version.canonical_sha256,
            evidence_spans=((first, first + 2),),
            source="source",
            source_ref="original-role",
            accepted=True,
        )
        linked = CharacterIdentityFact(
            kind="name",
            value="小舟",
            visible_from_cp=named + 2,
            canonical_sha256=version.canonical_sha256,
            evidence_spans=((named, named + 2),),
            source="model",
            source_ref="roster",
            accepted=True,
            identity_links=(
                IdentityLink(
                    source_id="deleted-original-person",
                    target_id=person.id,
                    visible_from_cp=len(TEXT),
                    source="user",
                    source_ref="merge",
                    accepted=True,
                ),
            ),
        )
        alias_fact = CharacterIdentityFact(
            kind="alias",
            value="小舟同学",
            visible_from_cp=alias + 4,
            canonical_sha256=version.canonical_sha256,
            evidence_spans=((alias, alias + 4),),
            source="source",
            source_ref="original-alias",
            accepted=True,
        )
        updates = [
            IdentityProfileUpdate(
                field=field,
                value=value,
                visible_from_cp=len(TEXT),
                canonical_sha256=version.canonical_sha256,
                source="user",
                source_ref="edit",
                accepted=True,
            )
            for field, value in (("name", "小舟"), ("aliases", ()), ("description", ""))
        ]
        person.identity_facts_json = json.dumps(
            [r.model_dump(mode="json") for r in (fact, linked, alias_fact, *updates)]
        )
        person.presentation_history_json = json.dumps(
            [
                {
                    "cp": first + 2,
                    "identity": f"character:{person.id}",
                    "name": "少女",
                    "description": "旧说明",
                },
                {
                    "cp": len(TEXT),
                    "identity": f"character:{person.id}",
                    "name": "小舟",
                    "description": "",
                },
            ]
        )
        person_id = person.id
    return data, person_id


def test_full_roundtrip_rebinds_proof_and_preserves_model_origin_merge_and_clears(
    migrated_client,
    migrated_settings,
):
    data, old_id = prepare(migrated_client, migrated_settings)
    raw = export_book(migrated_client, data)
    ledger = manifest(raw)["identity_ledger"]
    assert ledger["omitted_characters"] == 0
    assert manifest(raw)["annotations"] == []  # Unassigned people must also survive.
    restored = reimport(migrated_client, raw)
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        version = session.get(BookVersion, restored["book_version_id"])
        person = session.scalars(
            select(BookCharacter).where(BookCharacter.book_version_id == version.id)
        ).one()
        assert person.id != old_id and person.source == CharacterSource.MODEL
        assert not person.user_confirmed and person.confirmation_source == "automatic"
        assert person.preferred_color_index == 24 and person.aliases_json == "[]"
        records = read_identity_records(person, version)
        assert len(records) == 6 and all(
            r.canonical_sha256 == version.canonical_sha256 for r in records
        )
        content = migrated_client.get(f"/api/books/{restored['book_id']}/content").json()["data"]
        text = "\n".join(node["text"] for node in content["nodes"] if node["text"])
        assert "小舟" in text and version.canonical_sha256 != ledger["original_sha256"]
        named = records[1]
        assert named.identity_links[-1].target_id == person.id
        assert named.identity_links[0].source_id not in {old_id, "deleted-original-person"}
        horizon = records[0].visible_from_cp
        early = visible_identity_profile(person, version, horizon=horizon)
        assert early["name"] == "少女" and "小舟" not in early["aliases"]
        final = visible_identity_profile(person, version, horizon=version.canonical_length_cp)
        assert final["name"] == "小舟" and final["aliases"] == () and final["description"] == ""
        # A second export/import must not fail on remapped identity chains.
    twice = reimport(migrated_client, export_book(migrated_client, restored))
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        version = session.get(BookVersion, twice["book_version_id"])
        person = session.scalars(
            select(BookCharacter).where(BookCharacter.book_version_id == version.id)
        ).one()
        assert len(read_identity_records(person, version)) == 6


@pytest.mark.parametrize("change", ["offset", "proof", "record_hash", "duplicate", "block", "first_seen"])
def test_malformed_identity_block_never_prevents_text_import_or_creates_partial_person(
    migrated_client,
    migrated_settings,
    change,
):
    data, _ = prepare(migrated_client, migrated_settings)
    raw = export_book(migrated_client, data)

    def mutate(payload):
        ledger = payload["identity_ledger"]
        row = ledger["characters"][0]
        if change == "offset":
            row["records"][0]["visible_from_cp"]["offset"] = True
        elif change == "proof":
            row["records"][0]["evidence_spans"][0]["digest"] = "0" * 64
        elif change == "record_hash":
            row["records"][0]["canonical_sha256"] = "0" * 64
        elif change == "duplicate":
            ledger["characters"].append(dict(row))
        elif change == "first_seen":
            row["first_seen_cp"] = {}
        else:
            ledger["blocks"][0]["digest"] = "0" * 64

    restored = reimport(migrated_client, rewrite(raw, mutate))
    assert restored["import_status"] == "COMPLETED"
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        assert not list(
            session.scalars(
                select(BookCharacter).where(
                    BookCharacter.book_version_id == restored["book_version_id"],
                )
            )
        )


def test_partial_export_does_not_drop_resets_and_resurrect_old_aliases(
    migrated_client,
    migrated_settings,
):
    data, _ = prepare(migrated_client, migrated_settings)
    chapters = migrated_client.get(f"/api/books/{data['book_id']}/chapters").json()["data"]
    raw = export_book(migrated_client, data, chapter_ids=[chapters[0]["id"]])
    assert manifest(raw)["identity_ledger"]["omitted_characters"] == 1
    assert manifest(raw)["identity_ledger"]["characters"] == []
    restored = reimport(migrated_client, raw)
    assert restored["import_status"] == "COMPLETED"


def test_position_safe_export_contains_no_future_names_even_inside_ledger(
    migrated_client,
    migrated_settings,
):
    data, _ = prepare(migrated_client, migrated_settings)
    raw = export_book(migrated_client, data, visibility_policy="position_safe")
    ledger = manifest(raw)["identity_ledger"]
    assert ledger["characters"] == []
    assert "小舟" not in json.dumps(ledger, ensure_ascii=False)


def test_colored_span_boundaries_preserve_spaces_and_unverified_import_is_not_human_confirmed(
    fake_provider_client,
    migrated_settings,
):
    client = fake_provider_client
    sample = "第一章 测试\n  前文  「one two」   后文  three four。\n"
    data = import_sample(client, sample)
    profile = create_fake_profile(client)
    run_deterministic_job(
        migrated_settings,
        client,
        book_id=data["book_id"],
        profile_id=profile,
        key="colored-space-boundaries",
    )
    raw = export_book(client, data)
    restored = reimport(client, raw)
    content = client.get(f"/api/books/{restored['book_id']}/content").json()["data"]
    paragraphs = [n["text"] for n in content["nodes"] if n["node_type"] == "paragraph"]
    assert paragraphs == ["前文 「one two」 后文 three four。"]
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        people = list(
            session.scalars(
                select(BookCharacter).where(
                    BookCharacter.book_version_id == restored["book_version_id"],
                )
            )
        )
        assert people and all(not p.user_confirmed for p in people)


def test_manual_confirmation_and_long_name_survive_without_becoming_original_name_fact(
    migrated_client,
    migrated_settings,
):
    data, person_id = prepare(migrated_client, migrated_settings)
    long_name = "人工指定" * 20
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        person = session.get(BookCharacter, person_id)
        version = session.get(BookVersion, data["book_version_id"])
        rows = json.loads(person.identity_facts_json)
        rows.append(
            IdentityProfileUpdate(
                field="name",
                value=long_name,
                visible_from_cp=len(TEXT),
                canonical_sha256=version.canonical_sha256,
                source="user",
                source_ref="manual-edit",
                accepted=True,
            ).model_dump(mode="json")
        )
        person.identity_facts_json = json.dumps(rows)
        person.canonical_name = long_name
        person.source = CharacterSource.USER
        person.user_confirmed = person.name_locked = True
        person.confirmation_source = "manual"
    restored = reimport(migrated_client, export_book(migrated_client, data))
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        version = session.get(BookVersion, restored["book_version_id"])
        person = session.scalars(
            select(BookCharacter).where(BookCharacter.book_version_id == version.id)
        ).one()
        assert person.canonical_name == long_name and person.user_confirmed and person.name_locked
        assert person.confirmation_source == "manual" and person.source == CharacterSource.USER
        record = read_identity_records(person, version)[-1]
        assert record.kind == "profile_update" and record.value == long_name


def test_invalid_block_isolated_from_an_independent_same_named_person(
    migrated_client, migrated_settings
):
    data, _ = prepare(migrated_client, migrated_settings)

    def mutate(payload):
        row = payload["identity_ledger"]["characters"][0]
        payload["identity_ledger"]["characters"].append(
            dict(
                row,
                identity="character:second",
                records=[],
                presentation_history=[],
            )
        )
        row["records"][0]["accepted"] = "true"

    restored = reimport(migrated_client, rewrite(export_book(migrated_client, data), mutate))
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        people = list(
            session.scalars(
                select(BookCharacter).where(
                    BookCharacter.book_version_id == restored["book_version_id"],
                )
            )
        )
        assert len(people) == 1 and people[0].canonical_name == "小舟"
        assert people[0].identity_facts_json == "[]" and not people[0].user_confirmed


def test_same_name_same_color_people_without_group_history_do_not_merge(
    fake_provider_client,
    migrated_settings,
):
    client = fake_provider_client
    data = import_sample(client)
    profile = create_fake_profile(client)
    run_deterministic_job(
        migrated_settings,
        client,
        book_id=data["book_id"],
        profile_id=profile,
        key="same-color-distinct-identities",
    )
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        annotations = list(
            session.scalars(
                select(Annotation)
                .join(Quote)
                .where(
                    Quote.book_version_id == data["book_version_id"],
                )
                .order_by(Quote.start_cp)
            )
        )
        assert len(annotations) >= 2
        for index, annotation in enumerate(annotations[:2]):
            person = BookCharacter(
                book_version_id=data["book_version_id"],
                canonical_name="同学",
                preferred_color_index=0,
                confirmation_source="automatic",
            )
            session.add(person)
            session.flush()
            group = SpeakerGroup(
                scene_id=annotation.scene_id,
                first_quote_id=annotation.quote_id,
                display_label=f"同学{index}",
                canonical_name="同学",
                character_id=person.id,
                presentation_history_json="[]",
            )
            session.add(group)
            session.flush()
            annotation.speaker_id = group.id
    restored = reimport(client, export_book(client, data))
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        ids = set(
            session.scalars(
                select(SpeakerGroup.character_id)
                .join(Scene)
                .where(
                    Scene.book_version_id == restored["book_version_id"],
                    SpeakerGroup.canonical_name == "同学",
                )
            )
        )
        assert len(ids) == 2


def test_image_caption_is_not_added_to_imported_body(migrated_client):
    from fixtures.epub_factory import build_epub, ruby_and_image_spec

    response = migrated_client.post(
        "/api/books/import",
        files={
            "file": ("image.epub", build_epub(ruby_and_image_spec()), "application/epub+zip"),
        },
    )
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    original = migrated_client.get(f"/api/books/{data['book_id']}/content").json()["data"]
    restored = reimport(migrated_client, export_book(migrated_client, data))
    actual = migrated_client.get(f"/api/books/{restored['book_id']}/content").json()["data"]
    assert [n["text"] for n in actual["nodes"]] == [n["text"] for n in original["nodes"]]


def test_bad_frozen_original_hash_finishes_export_as_failed_not_running(
    migrated_client,
    migrated_settings,
):
    data, _ = prepare(migrated_client, migrated_settings)
    response = migrated_client.post(
        f"/api/books/{data['book_id']}/exports/preview",
        json={
            "visibility_policy": "reread",
            "style": {"preset": "color_and_label"},
        },
    )
    assert response.status_code == 200
    snapshot_id = response.json()["data"]["snapshot_id"]
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        snapshot = session.get(ExportSnapshot, snapshot_id)
        payload = json.loads(snapshot.annotation_projection_json)
        payload["character_snapshot"]["canonical_sha256"] = "0" * 64
        snapshot.annotation_projection_json = json.dumps(payload)
    response = migrated_client.post(
        f"/api/books/{data['book_id']}/exports",
        json={
            "snapshot_id": snapshot_id,
            "format": "epub",
            "style": {"preset": "color_and_label"},
            "idempotency_key": snapshot_id,
        },
    )
    assert response.status_code == 500, response.text
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        artifact = session.scalars(
            select(ExportArtifact).where(ExportArtifact.snapshot_id == snapshot_id)
        ).one()
        job = session.get(Job, artifact.job_id)
        assert artifact.state == "FAILED" and job.state == JobState.FAILED
        assert artifact.relative_path is None and "hash" in job.last_error


def test_reader_uses_precise_reveal_and_keeps_group_and_character_histories_independent(
    fake_provider_client,
    migrated_settings,
):
    client = fake_provider_client
    data = import_sample(client)
    profile = create_fake_profile(client)
    run_deterministic_job(
        migrated_settings,
        client,
        book_id=data["book_id"],
        profile_id=profile,
        key="precise-reader-reveal",
    )
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        quote = session.scalars(
            select(Quote)
            .where(
                Quote.book_version_id == data["book_version_id"],
                Quote.nesting_depth == 0,
            )
            .order_by(Quote.start_cp)
        ).first()
        annotation = session.scalar(select(Annotation).where(Annotation.quote_id == quote.id))
        group = session.get(SpeakerGroup, annotation.speaker_id)
        person = BookCharacter(
            book_version_id=data["book_version_id"],
            canonical_name="小舟",
            description="全书说明",
            first_seen_cp=quote.start_cp,
        )
        session.add(person)
        session.flush()
        person.presentation_history_json = json.dumps(
            [
                {
                    "cp": 0,
                    "identity": f"character:{person.id}",
                    "name": "人物代称",
                    "description": "人物旧说明",
                },
                {
                    "cp": quote.end_cp,
                    "identity": f"character:{person.id}",
                    "name": "小舟",
                    "description": "全书说明",
                },
            ]
        )
        group.character_id = person.id
        group.canonical_name = "小舟"
        group.description = "分组说明"
        group.presentation_history_json = json.dumps(
            [
                {
                    "cp": 0,
                    "identity": f"group:{group.id}",
                    "name": "对白代称",
                    "description": "分组旧说明",
                },
                {
                    "cp": quote.end_cp,
                    "identity": f"character:{person.id}",
                    "name": "小舟",
                    "description": "分组说明",
                },
            ]
        )
        for row in session.scalars(select(Annotation).where(Annotation.speaker_id == group.id)):
            row.visible_from_cp = 0
    restored = reimport(client, export_book(client, data))
    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        quote = session.scalars(
            select(Quote)
            .where(
                Quote.book_version_id == restored["book_version_id"],
                Quote.nesting_depth == 0,
            )
            .order_by(Quote.start_cp)
        ).first()
        annotation = session.scalar(select(Annotation).where(Annotation.quote_id == quote.id))
        group = session.get(SpeakerGroup, annotation.speaker_id)
        person = session.get(BookCharacter, group.character_id)
        group_history = json.loads(group.presentation_history_json)
        person_history = json.loads(person.presentation_history_json)
        assert group_history[-1]["cp"] == person_history[-1]["cp"] == quote.end_cp
        assert group_history[-1]["description"] == "分组说明"
        assert person_history[-1]["description"] == "全书说明"
        reveal, quote_id = quote.end_cp, quote.id

    def reader(horizon):
        response = client.get(
            f"/api/books/{restored['book_id']}/annotations",
            params={
                "reading_mode": "initial",
                "visible_horizon_cp": horizon,
            },
        )
        assert response.status_code == 200
        return next(
            item for item in response.json()["data"]["items"] if item["quote_id"] == quote_id
        )

    assert reader(reveal - 1)["label"] == "对白代称"
    assert reader(reveal)["label"] == "小舟"
