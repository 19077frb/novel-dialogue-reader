"""Shared storage must preserve the complete reader timeline and version ownership."""

import json

from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.orm import Session

from ndr.app import create_app
from ndr.characters.visibility import capture, history, initialize, visible_value
from ndr.ingest.deletion import delete_book
from ndr.ingest.service import import_txt
from ndr.storage.base import utcnow
from ndr.storage.cache import ResultCacheStore
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.history import deduplicate_group_histories, share_history
from ndr.storage.migrate import build_alembic_config, run_migrations
from ndr.storage.models import (
    Annotation,
    BookCharacter,
    BookVersion,
    PresentationHistoryBaseline,
    Quote,
    ResultCache,
    Scene,
    SpeakerGroup,
)
from ndr.storage.retention import apply_retention
from ndr.storage.transactions import transaction


def _groups(session, settings):
    imported = import_txt(session, settings, filename="shared-original.txt",
                          raw="第一章\n\n「早安。」\n「再见。」\n".encode())
    person = BookCharacter(book_version_id=imported.version.id, canonical_name="少女")
    session.add(person)
    session.flush()
    capture(person, 0)
    person.canonical_name = "林舟"
    person.description = "后来揭示的姓名"
    capture(person, 10)
    groups = []
    for number in range(3):
        scene = Scene(book_version_id=imported.version.id, start_cp=number)
        session.add(scene)
        session.flush()
        group = SpeakerGroup(scene_id=scene.id, display_label="A", character_id=person.id,
                             canonical_name="林舟", description=person.description)
        session.add(group)
        session.flush()
        groups.append(group)
    return imported, person, groups


def test_shared_baseline_keeps_local_revisions_and_detached_reads(migrated_settings):
    engine = create_db_engine(migrated_settings)
    try:
        with Session(engine) as session:
            imported, person, groups = _groups(session, migrated_settings)
            original = history(person)
            for group in groups:
                initialize(group, 0, person)
            session.flush()
            assert session.scalar(select(func.count()).select_from(PresentationHistoryBaseline)) == 1
            assert len({group.presentation_baseline_id for group in groups}) == 1
            assert all(group._presentation_history_json == "[]" for group in groups)
            groups[0].canonical_name = "新称呼"
            capture(groups[0], 5, force=True)
            assert len(json.loads(groups[0]._presentation_history_json)) == 1
            assert history(groups[1]) == original
            assert history(groups[0])[:-1] == original
            # Position order and append order are independent, as before sharing.
            fallback = {"private_identity": "group:private"}
            assert visible_value(groups[0].presentation_history_json, 6, fallback=fallback)["name"] == "新称呼"
            assert visible_value(groups[0].presentation_history_json, 10, fallback=fallback)["name"] == "林舟"
            assert visible_value(groups[0].presentation_history_json, None, fallback={"name": "当前"})["name"] == "当前"
            session.delete(person)
            session.flush()
            assert history(groups[1]) == original
            group_ids = [group.id for group in groups]
            version_id = imported.version.id
            session.commit()
        statements = []

        def counted(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", counted)
        with Session(engine) as session:
            groups = list(session.scalars(select(SpeakerGroup).where(SpeakerGroup.id.in_(group_ids))))
            session.expunge_all()
        assert len(statements) == 2
        assert history(groups[1]) == original
        event.remove(engine, "before_cursor_execute", counted)
        with Session(engine) as session:
            group = session.get(SpeakerGroup, group_ids[0])
            replacement = [{"cp": 1, "name": "回导", "identity": "imported", "description": ""}]
            group.presentation_history_json = json.dumps(replacement)
            capture(group, 20)
            assert group.presentation_baseline_id is None
            assert history(group)[0] == replacement[0]
            session.flush()
            session.execute(delete(BookVersion).where(BookVersion.id == version_id))
            session.flush()
            assert session.scalar(select(func.count()).select_from(PresentationHistoryBaseline)) == 0
            assert session.scalar(select(func.count()).select_from(SpeakerGroup)) == 0
    finally:
        engine.dispose()


def test_explicit_history_deduplication_is_semantically_lossless(migrated_settings):
    engine = create_db_engine(migrated_settings)
    try:
        with Session(engine) as session:
            _imported, person, groups = _groups(session, migrated_settings)
            rows = history(person) + [{"cp": 2, "identity": "older", "name": "旧称呼",
                                      "description": "", "extra": {"keep": [1, 2]}}]
            for group in groups:
                group.presentation_history_json = json.dumps(rows)
            before = [history(group) for group in groups]
            assert deduplicate_group_histories(session) == 3
            assert [history(group) for group in groups] == before
            assert session.scalar(select(func.count()).select_from(PresentationHistoryBaseline)) == 1
            assert deduplicate_group_histories(session) == 0
            groups[0]._presentation_history_json = "not-json"
            assert deduplicate_group_histories(session) == 0
    finally:
        engine.dispose()


def test_shared_history_migration_preserves_old_rows_and_proves_cache_owner(tmp_settings):
    run_migrations(tmp_settings, revision="0023")
    engine = create_db_engine(tmp_settings)
    original = [{"cp": 0, "name": "少女", "description": "", "identity": "person"}]
    raw = json.dumps(original)
    try:
        with engine.begin() as connection:
            values = {"now": utcnow(), "raw": raw}
            for statement in (
                "INSERT INTO books (id,title,format,source_sha256,import_status,read_position_cp,reading_mode,created_at,updated_at,version) VALUES ('b','原创','TXT','a','COMPLETED',0,'initial',:now,:now,1)",
                "INSERT INTO book_versions (id,book_id,encoding,parser_version,normalization_version,canonical_sha256,canonical_length_cp,warnings_json,created_at,updated_at) VALUES ('v','b','utf-8','test','test','b',100,'[]',:now,:now)",
                "INSERT INTO scenes (id,book_version_id,start_cp,status,created_at,updated_at,version) VALUES ('s','v',0,'OPEN',:now,:now,1)",
                "INSERT INTO speaker_groups (id,scene_id,display_label,evidence_refs_json,presentation_history_json,created_at,updated_at,version) VALUES ('g','s','A','[]',:raw,:now,:now,1)",
                "INSERT INTO quotes (id,book_version_id,start_cp,end_cp,delimiter,nesting_depth,scanner_version,normalized,created_at,updated_at) VALUES ('q','v',1,5,'「」',0,'test',0,:now,:now)",
                "INSERT INTO annotations (id,quote_id,scene_id,kind,speaker_id,status,source,evidence_refs_json,stale,user_locked,created_at,updated_at,version) VALUES ('a','q','s','speech','g','ACCEPTED','USER','[]',0,1,:now,:now,1)",
                "INSERT INTO jobs (id,kind,book_id,book_version_id,state,range_json,budget_json,created_at,updated_at,version) VALUES ('j','INFERENCE','b','v','COMPLETED','{}','{}',:now,:now,1)",
                "INSERT INTO inference_runs (id,job_id,profile_snapshot_json,request_fingerprint,state,created_at,updated_at) VALUES ('r','j','{}','fp','SUCCEEDED',:now,:now)",
                "INSERT INTO result_cache (id,cache_key,schema_version,result_json,created_run_id,created_at,updated_at) VALUES ('known','known','1','{}','r',:now,:now)",
                "INSERT INTO result_cache (id,cache_key,schema_version,result_json,created_run_id,created_at,updated_at) VALUES ('unknown','unknown','1','{}','missing',:now,:now)",
            ):
                connection.execute(text(statement), values)
        run_migrations(tmp_settings, revision="0024")
        run_migrations(tmp_settings, revision="0024")
        with Session(engine) as session:
            group = session.get(SpeakerGroup, "g")
            assert group.presentation_baseline_id is None
            assert group._presentation_history_json == raw
            assert session.get(ResultCache, "known").book_version_id == "v"
            assert session.get(ResultCache, "unknown").book_version_id is None
            share_history(group, original)
            group.canonical_name = "新称呼"
            capture(group, 20)
            expected = history(group)
            session.commit()
        command.downgrade(build_alembic_config(tmp_settings), "0023")
        with engine.connect() as connection:
            assert json.loads(connection.scalar(text("SELECT presentation_history_json FROM speaker_groups WHERE id='g'"))) == expected
            assert connection.execute(text("SELECT speaker_id, scene_id, user_locked FROM annotations WHERE id='a'")).one() == ("g", "s", 1)
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        run_migrations(tmp_settings, revision="0024")
        with Session(engine) as session:
            assert history(session.get(SpeakerGroup, "g")) == expected
            session.execute(delete(BookVersion).where(BookVersion.id == "v"))
            session.commit()
            assert session.get(ResultCache, "known") is None
            assert session.get(ResultCache, "unknown") is not None
    finally:
        engine.dispose()


def test_normal_book_deletion_cascades_owned_cache_and_shared_histories(migrated_settings):
    engine = create_db_engine(migrated_settings)
    try:
        with Session(engine) as session:
            imported, person, groups = _groups(session, migrated_settings)
            for group in groups:
                initialize(group, 0, person)
            store = ResultCacheStore(session)
            store.put(cache_key="owned-fixture", book_version_id=imported.version.id,
                      result_json='{"labels": []}')
            store.put(cache_key="unknown-fixture", result_json='{"labels": []}')
            assert store.get("owned-fixture").payload() == {"labels": []}
            book_id = imported.book.id
            session.commit()
        delete_book(create_session_factory(engine), migrated_settings, book_id)
        with Session(engine) as session:
            assert ResultCacheStore(session).get("owned-fixture") is None
            assert ResultCacheStore(session).get("unknown-fixture") is not None
            assert session.scalar(select(func.count()).select_from(PresentationHistoryBaseline)) == 0
            assert session.scalar(select(func.count()).select_from(SpeakerGroup)) == 0
    finally:
        engine.dispose()


def test_rebased_history_keeps_initial_colors_through_manual_edit_and_correction_undo(
    migrated_settings,
):
    engine = create_db_engine(migrated_settings)
    try:
        with Session(engine) as session:
            imported = import_txt(session, migrated_settings, filename="undo-original.txt",
                raw="第一章\n\n「早安，今天也来这里吗？」\n「当然，我还带了书。」\n".encode())
            quotes = list(session.scalars(select(Quote).where(
                Quote.book_version_id == imported.version.id,
            ).order_by(Quote.start_cp)))
            first = BookCharacter(book_version_id=imported.version.id,
                                  canonical_name="当前甲", preferred_color_index=3)
            second = BookCharacter(book_version_id=imported.version.id,
                                   canonical_name="当前乙", preferred_color_index=7)
            scene = Scene(book_version_id=imported.version.id, start_cp=0,
                          end_cp=imported.version.canonical_length_cp)
            session.add_all([first, second, scene])
            session.flush()
            capture(first, 0)
            capture(second, 0)
            groups = [SpeakerGroup(scene_id=scene.id, display_label=f"S{index + 1}",
                first_quote_id=quote.id, character_id=person.id,
                canonical_name=person.canonical_name)
                for index, (quote, person) in enumerate(zip(quotes, (first, second), strict=True))]
            session.add_all(groups)
            session.flush()
            share_history(groups[0], [
                {"cp": 0, "identity": f"character:{first.id}", "name": "旧称呼", "description": ""},
                {"cp": 8, "identity": f"character:{second.id}", "name": "后文身份", "description": ""},
            ])
            for cp, name in ((4, "同位置先写"), (4, "同位置后写"), (2, "更早称呼")):
                groups[0].canonical_name = name
                capture(groups[0], cp, force=True)
            initialize(groups[1], 0, second)
            for quote, group in zip(quotes, groups, strict=True):
                session.add(Annotation(quote_id=quote.id, scene_id=scene.id, speaker_id=group.id,
                    kind="speech", assignment="EXISTING", basis="DIRECT", status="ACCEPTED",
                    source="USER", visible_from_cp=0))
            original_history = history(groups[0])
            book_id, character_id = imported.book.id, first.id
            group_id, other_group_id = (group.id for group in groups)
            quote_id = quotes[0].id
            session.commit()
    finally:
        engine.dispose()

    def initial_views(client):
        views = []
        for horizon in (3, 5, 9):
            response = client.get(f"/api/books/{book_id}/annotations", params={
                "reading_mode": "initial", "visible_horizon_cp": horizon,
            })
            assert response.status_code == 200, response.text
            views.append([(item["quote_id"], item["speaker_group_id"], item["label"],
                           item["speaker_description"], item["color_index"])
                          for item in response.json()["data"]["items"]])
        return views

    with TestClient(create_app(migrated_settings)) as client:
        original_views = initial_views(client)
        assert [view[0][2] for view in original_views] == ["更早称呼", "同位置后写", "后文身份"]
    assert apply_retention(migrated_settings)["business_data_unchanged"]
    with TestClient(create_app(migrated_settings)) as client:
        assert initial_views(client) == original_views
        with transaction(client.app.state.session_factory) as session:
            group = session.get(SpeakerGroup, group_id)
            assert group._presentation_history_json == "[]"
            assert history(group) == original_history
        edited = client.put(f"/api/books/{book_id}/character-directory/{character_id}", json={
            "name": "人工称呼", "description": "人工说明", "expected_version": 1,
            "visible_from_cp": 4,
        })
        assert edited.status_code == 200, edited.text
        edited_views = initial_views(client)
        assert [view[0][2] for view in edited_views] == ["更早称呼", "人工称呼", "后文身份"]
        with transaction(client.app.state.session_factory) as session:
            group = session.get(SpeakerGroup, group_id)
            assert history(group)[:-1] == original_history
            assert len(json.loads(group._presentation_history_json)) == 1
        corrected = client.post(f"/api/quotes/{quote_id}/corrections", json={
            "action": "assign_existing", "speaker_ref": other_group_id, "expected_version": 1,
        })
        assert corrected.status_code == 201, corrected.text
        assert initial_views(client) != edited_views
        undone = client.post(f"/api/corrections/{corrected.json()['data']['correction_id']}/undo")
        assert undone.status_code == 201, undone.text
        assert initial_views(client) == edited_views
        with transaction(client.app.state.session_factory) as session:
            assert history(session.get(SpeakerGroup, group_id))[:-1] == original_history
