"""迁移、表结构与约束验证。

覆盖：空库迁移、现有库升级、外键错误、版本冲突、字段序列化、迁移状态、
无明文密钥列，以及 UNKNOWN / 用户锁定 / 队列状态互相独立。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from ndr.config import Settings
from ndr.domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    BookFormat,
    DatabaseState,
    ImportStatus,
    JobKind,
    JobPurpose,
    JobState,
    QuoteKind,
    ReviewQueueStatus,
    ReviewReason,
    ReviewTargetType,
    SceneStatus,
)
from ndr.storage.base import Base, utcnow
from ndr.storage.engine import (
    create_db_engine,
    create_session_factory,
    head_revision,
    migration_status,
)
from ndr.storage.migrate import run_migrations
from ndr.storage.models import (
    Annotation,
    Book,
    BookVersion,
    Chapter,
    Job,
    Quote,
    ReviewItem,
    Scene,
    SpeakerGroup,
)
from ndr.storage.transactions import VersionConflict, apply_versioned_update, transaction


def test_empty_database_migrates_to_head(tmp_settings: Settings) -> None:
    run_migrations(tmp_settings)
    engine = create_db_engine(tmp_settings)
    try:
        status = migration_status(engine)
        assert status.state is DatabaseState.READY
        assert status.revision == head_revision()
        assert "review_items" in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_all_foreign_keys_have_leading_indexes(migrated_settings: Settings) -> None:
    """SQLite checks every referencing table for each deleted parent row."""
    engine = create_db_engine(migrated_settings)
    try:
        inspector = inspect(engine)
        for table in inspector.get_table_names():
            indexes = inspector.get_indexes(table) + inspector.get_unique_constraints(table)
            indexes.append({"column_names": inspector.get_pk_constraint(table)["constrained_columns"]})
            for fk in inspector.get_foreign_keys(table):
                columns = fk["constrained_columns"]
                assert any(index["column_names"][:len(columns)] == columns for index in indexes), (
                    table, columns,
                )
    finally:
        engine.dispose()


def test_cli_migration_creates_missing_data_directory(tmp_path, monkeypatch) -> None:
    data_dir = tmp_path / "first-run" / "data"
    monkeypatch.setenv("NDR_DATA_DIR", str(data_dir))

    settings = Settings()
    assert not data_dir.exists()

    command.upgrade(Config(str(settings.alembic_ini_path)), "head")

    assert settings.database_path.is_file()


def test_migrations_are_repeatable(tmp_settings: Settings) -> None:
    run_migrations(tmp_settings)
    run_migrations(tmp_settings)  # 重复运行必须安全
    engine = create_db_engine(tmp_settings)
    try:
        assert migration_status(engine).state is DatabaseState.READY
    finally:
        engine.dispose()


def test_database_without_alembic_version_is_not_initialized(engine: Engine) -> None:
    status = migration_status(engine)
    assert status.state is DatabaseState.NOT_INITIALIZED
    assert status.head_revision == head_revision()


def test_models_and_database_do_not_drift(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    tables = set(inspector.get_table_names())
    for name, table in Base.metadata.tables.items():
        assert name in tables, f"迁移缺少表 {name}"
        db_columns = {column["name"] for column in inspector.get_columns(name)}
        model_columns = {column.name for column in table.columns}
        assert db_columns == model_columns, f"{name} 列与模型不一致"


def test_existing_database_upgrades_without_data_loss(tmp_settings: Settings) -> None:
    run_migrations(tmp_settings, revision="0001")

    engine = create_db_engine(tmp_settings)
    factory = create_session_factory(engine)
    try:
        tables_before = set(inspect(engine).get_table_names())
        assert "review_items" not in tables_before

        with transaction(factory) as session:
            # 注意：这里必须只写 0001 时代存在的列。ORM/Core 的表定义会带上后续迁移新增的列
            # （例如 reading_mode），因此用原生 SQL 明确列出旧列——这正是本用例要覆盖的升级场景。
            now = utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
            book_id = "00000000-0000-4000-8000-000000000001"
            session.execute(
                text(
                    "INSERT INTO books (id, title, format, source_sha256, import_status,"
                    " read_position_cp, created_at, updated_at, version)"
                    " VALUES (:id, :title, :format, :sha, :status, :cp, :created, :updated, :version)"
                ),
                {
                    "id": book_id,
                    "title": "迁移前的书",
                    "format": "TXT",
                    "sha": "a" * 64,
                    "status": "PENDING",
                    "cp": 0,
                    "created": now,
                    "updated": now,
                    "version": 1,
                },
            )

        run_migrations(tmp_settings)

        with transaction(factory) as session:
            stored = session.get(Book, book_id)
            assert stored is not None
            assert stored.title == "迁移前的书"
        assert "review_items" in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_processing_state_is_backfilled_once_when_upgrading_from_0009(
    tmp_settings: Settings,
) -> None:
    run_migrations(tmp_settings, revision="0009")
    engine = create_db_engine(tmp_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            book = Book(
                title="旧任务",
                format=BookFormat.TXT,
                source_sha256="d" * 64,
                import_status=ImportStatus.COMPLETED,
            )
            session.add(book)
            session.flush()
            version = BookVersion(
                book_id=book.id,
                encoding="utf-8",
                parser_version="txt-1",
                normalization_version="canonical-lf-1",
                canonical_sha256="e" * 64,
                canonical_length_cp=10,
            )
            session.add(version)
            session.flush()
            chapter = Chapter(
                book_version_id=version.id,
                ordinal=0,
                title="第一章",
                start_cp=0,
                end_cp=10,
            )
            session.add(chapter)
            session.flush()
            session.add(Job(
                kind=JobKind.INFERENCE,
                purpose=JobPurpose.PROCESS,
                book_id=book.id,
                book_version_id=version.id,
                range_json=f'{{"chapter_id":"{chapter.id}","selected_window_ids":null}}',
                state=JobState.COMPLETED,
                budget_json="{}",
            ))
            chapter_id = chapter.id

        run_migrations(tmp_settings)
        with transaction(factory) as session:
            assert session.get(Chapter, chapter_id).dialogue_processed is True
    finally:
        engine.dispose()


def test_foreign_keys_are_enabled(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_foreign_key_violation_is_rejected(migrated_engine: Engine) -> None:
    factory = create_session_factory(migrated_engine)
    with pytest.raises(IntegrityError), transaction(factory) as session:
        session.add(
            BookVersion(
                book_id="missing-book",
                encoding="utf-8",
                parser_version="p1",
                normalization_version="n1",
                canonical_sha256="b" * 64,
                canonical_length_cp=10,
            )
        )


def test_version_conflict_reports_expected_and_current(migrated_engine: Engine) -> None:
    factory = create_session_factory(migrated_engine)
    with transaction(factory) as session:
        book = Book(title="初版", format=BookFormat.TXT, source_sha256="c" * 64)
        session.add(book)
        session.flush()
        book_id = book.id
        assert apply_versioned_update(
            session, book, expected_version=1, changes={"title": "第二版"}
        ) == 2

    with transaction(factory) as session:
        book = session.get(Book, book_id)
        assert book is not None
        assert book.version == 2
        with pytest.raises(VersionConflict) as excinfo:
            apply_versioned_update(session, book, expected_version=1, changes={"title": "过期写入"})
        assert excinfo.value.details == {
            "entity": "Book",
            "target_id": book_id,
            "expected_version": 1,
            "current_version": 2,
        }


def test_timestamps_roundtrip_as_utc(migrated_engine: Engine) -> None:
    factory = create_session_factory(migrated_engine)
    before = utcnow()
    with transaction(factory) as session:
        book = Book(title="时间", format=BookFormat.EPUB, source_sha256="d" * 64)
        session.add(book)
        session.flush()
        book_id = book.id

    with transaction(factory) as session:
        stored = session.get(Book, book_id)
        assert stored is not None
        created = stored.created_at
        assert created.tzinfo is not None
        assert created.utcoffset() == UTC.utcoffset(None)
        assert before <= created <= datetime.now(tz=UTC)


def test_enum_columns_store_contract_strings(migrated_engine: Engine) -> None:
    factory = create_session_factory(migrated_engine)
    with transaction(factory) as session:
        session.add(Book(title="枚举", format=BookFormat.TXT, source_sha256="e" * 64))

    with migrated_engine.connect() as connection:
        raw_format = connection.execute(text("SELECT format FROM books")).scalar()
        raw_status = connection.execute(text("SELECT import_status FROM books")).scalar()
    assert raw_format == "TXT"
    assert raw_status == ImportStatus.PENDING.value


def test_json_and_version_defaults(migrated_engine: Engine) -> None:
    factory = create_session_factory(migrated_engine)
    with transaction(factory) as session:
        book = Book(title="默认值", format=BookFormat.TXT, source_sha256="f" * 64)
        session.add(book)
        session.flush()
        version = BookVersion(
            book_id=book.id,
            encoding="utf-8",
            parser_version="p1",
            normalization_version="n1",
            canonical_sha256="1" * 64,
            canonical_length_cp=3,
        )
        session.add(version)
        session.flush()
        assert book.version == 1
        assert book.read_position_cp == 0
        assert book.import_status is ImportStatus.PENDING
        assert version.warnings_json == "[]"


def test_model_profiles_has_no_plaintext_secret_column(migrated_engine: Engine) -> None:
    columns = {column["name"].lower() for column in inspect(migrated_engine).get_columns("model_profiles")}
    forbidden = ("key", "secret", "token", "password", "credential_value", "authorization")
    offenders = {name for name in columns if any(word in name for word in forbidden)}
    assert offenders == set(), f"model_profiles 出现疑似明文凭据列：{offenders}"


def _seed_dialogue_rows(session, book_id: str) -> dict[str, str]:
    """建立一条最小的 书籍→版本→章节→引语→场景 链，供约束测试使用。"""

    version = BookVersion(
        book_id=book_id,
        encoding="utf-8",
        parser_version="p1",
        normalization_version="n1",
        canonical_sha256="2" * 64,
        canonical_length_cp=100,
    )
    session.add(version)
    session.flush()
    quote = Quote(
        book_version_id=version.id,
        start_cp=0,
        end_cp=6,
        delimiter="「」",
        scanner_version="s1",
    )
    session.add(quote)
    session.flush()
    scene = Scene(book_version_id=version.id, start_cp=0, status=SceneStatus.OPEN)
    session.add(scene)
    session.flush()
    group = SpeakerGroup(scene_id=scene.id, first_quote_id=quote.id, display_label="S1")
    session.add(group)
    session.flush()
    return {"version_id": version.id, "quote_id": quote.id, "scene_id": scene.id, "group_id": group.id}


def test_quote_positions_are_unique_per_scanner(migrated_engine: Engine) -> None:
    factory = create_session_factory(migrated_engine)
    with transaction(factory) as session:
        book = Book(title="引语唯一", format=BookFormat.TXT, source_sha256="3" * 64)
        session.add(book)
        session.flush()
        ids = _seed_dialogue_rows(session, book.id)

    with pytest.raises(IntegrityError), transaction(factory) as session:
        session.add(
            Quote(
                book_version_id=ids["version_id"],
                start_cp=0,
                end_cp=6,
                delimiter="「」",
                scanner_version="s1",
            )
        )


def test_unknown_lock_stale_and_queue_status_are_independent(migrated_engine: Engine) -> None:
    factory = create_session_factory(migrated_engine)
    with transaction(factory) as session:
        book = Book(title="独立状态", format=BookFormat.TXT, source_sha256="4" * 64)
        session.add(book)
        session.flush()
        ids = _seed_dialogue_rows(session, book.id)
        annotation = Annotation(
            quote_id=ids["quote_id"],
            scene_id=ids["scene_id"],
            kind=QuoteKind.UNKNOWN,
            status=AnnotationStatus.UNKNOWN,
            source=AnnotationSource.USER,
            user_locked=True,
            stale=True,
        )
        session.add(annotation)
        session.flush()
        review_item = ReviewItem(
            target_type=ReviewTargetType.QUOTE,
            quote_id=ids["quote_id"],
            reason=ReviewReason.USER_FLAGGED,
            queue_status=ReviewQueueStatus.DEFERRED,
        )
        session.add(review_item)
        session.flush()
        annotation_id = annotation.id
        review_id = review_item.id

    with transaction(factory) as session:
        annotation = session.get(Annotation, annotation_id)
        review_item = session.get(ReviewItem, review_id)
        assert annotation is not None
        # UNKNOWN、用户锁定、过期三者能同时成立，互不覆盖。
        assert annotation.status is AnnotationStatus.UNKNOWN
        assert annotation.user_locked is True
        assert annotation.stale is True
        assert annotation.speaker_id is None
        # 队列状态只影响处理队列，不改变标注状态。
        assert review_item is not None
        assert review_item.queue_status is ReviewQueueStatus.DEFERRED
        assert annotation.status is AnnotationStatus.UNKNOWN


def test_review_item_requires_exactly_one_target(migrated_engine: Engine) -> None:
    factory = create_session_factory(migrated_engine)
    with transaction(factory) as session:
        book = Book(title="目标唯一", format=BookFormat.TXT, source_sha256="5" * 64)
        session.add(book)
        session.flush()
        ids = _seed_dialogue_rows(session, book.id)

    with pytest.raises(IntegrityError), transaction(factory) as session:
        session.add(
            ReviewItem(
                target_type=ReviewTargetType.QUOTE,
                reason=ReviewReason.LOW_CONFIDENCE,
                queue_status=ReviewQueueStatus.PENDING,
            )
        )

    with pytest.raises(IntegrityError), transaction(factory) as session:
        session.add(
            ReviewItem(
                target_type=ReviewTargetType.QUOTE,
                quote_id=ids["quote_id"],
                gap_id=ids["quote_id"],  # 同时给出两种目标
                reason=ReviewReason.LOW_CONFIDENCE,
                queue_status=ReviewQueueStatus.PENDING,
            )
        )


def test_job_state_values_cover_recovery_cases() -> None:
    assert {state.value for state in JobState} == {
        "QUEUED",
        "RUNNING",
        "PAUSING",
        "PAUSED",
        "PARTIAL",
        "COMPLETED",
        "FAILED",
        "BUDGET_EXHAUSTED",
        "NEEDS_RECONCILIATION",
    }
