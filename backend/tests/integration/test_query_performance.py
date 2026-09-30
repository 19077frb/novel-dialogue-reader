"""Query budgets, migration plans and paging boundaries on isolated databases."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import event, inspect, select, text
from sqlalchemy.orm import Session

from ndr.api.pagination import encode_cursor
from ndr.config import Settings
from ndr.corrections.invalidator import mark_stale
from ndr.corrections.review import list_review_items, review_counts
from ndr.domain.enums import (
    AnnotationSource,
    BookFormat,
    ContentNodeType,
    QuoteKind,
    ReviewQueueStatus,
    ReviewReason,
    ReviewTargetType,
)
from ndr.ingest.query import content_nodes, list_books
from ndr.ingest.service import import_txt
from ndr.storage.base import Base
from ndr.storage.engine import head_revision
from ndr.storage.migrate import run_migrations
from ndr.storage.models import Annotation, Book, Chapter, ContentNode, Quote, ReviewItem


@contextmanager
def counted_queries(engine):
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def test_library_paging_is_chronological_and_constant_query_count(migrated_engine) -> None:
    with Session(migrated_engine) as session:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        # UUIDs are not chronological; same-time rows must use ID as tie breaker.
        for i, row_id in enumerate(["z", "a", "b", "c"]):
            session.add(
                Book(
                    id=row_id,
                    title=row_id,
                    format=BookFormat.TXT,
                    source_sha256="0" * 64,
                    created_at=base + timedelta(seconds=min(i, 2)),
                )
            )
        session.commit()
    with Session(migrated_engine) as session, counted_queries(migrated_engine) as queries:
        items, cursor = list_books(session, limit=2, cursor=None)
        assert [row.id for row in items] == ["z", "a"]
        assert len(queries) == 2
        more, _ = list_books(session, limit=2, cursor=cursor)
        assert [row.id for row in more] == ["b", "c"]
        legacy, _ = list_books(session, limit=3, cursor=encode_cursor(["a"]))
        assert [row.id for row in legacy] == ["b", "c"]
        session.delete(session.get(Book, "a"))
        session.commit()
        assert [row.id for row in list_books(session, limit=2, cursor=cursor)[0]] == ["b", "c"]


def test_review_text_and_counts_without_target_query_amplification(
    migrated_settings: Settings,
    migrated_engine,
) -> None:
    with Session(migrated_engine) as session:
        outcome = import_txt(
            session,
            migrated_settings,
            filename="test.txt",
            raw=("第一章\n「甲。」\n「乙。」\n" * 20).encode(),
        )
        version_id = outcome.version.id
        quotes = list(session.scalars(select(Quote).where(Quote.book_version_id == version_id)))
        for quote in quotes:
            for reason in (ReviewReason.USER_FLAGGED, ReviewReason.STALE_DEPENDENCY):
                session.add(
                    ReviewItem(
                        target_type=ReviewTargetType.QUOTE,
                        quote_id=quote.id,
                        reason=reason,
                        candidates_json='{"large":"' + "x" * 10000 + '"}',
                    )
                )
        session.commit()
    with Session(migrated_engine) as session, counted_queries(migrated_engine) as queries:
        rows, _ = list_review_items(
            session, book_version_id=version_id, canonical_text="第一章\n「甲。」\n「乙。」\n" * 20
        )
        assert len(queries) == 1
        assert all(row.target_text in ("「甲。」", "「乙。」") for row in rows)
        queries.clear()
        counts = review_counts(session, version_id)
        assert counts.total == len(quotes) * 2
        assert counts.targets_total == len(quotes)
        assert len(queries) == 3
        assert not any(isinstance(row, ReviewItem) for row in session.identity_map.values())


def test_content_pages_do_not_repeat_zero_length_nodes_or_other_chapters(
    migrated_settings: Settings,
    migrated_engine,
) -> None:
    with Session(migrated_engine) as session:
        outcome = import_txt(session, migrated_settings, filename="empty.txt", raw=b"body")
        book = outcome.book
        version = outcome.version
        chapter = session.scalar(select(Chapter).where(Chapter.book_version_id == version.id))
        session.query(ContentNode).filter_by(chapter_id=chapter.id).delete()
        chapter.start_cp = chapter.end_cp = 0
        other = Chapter(book_version_id=version.id, ordinal=999, start_cp=0, end_cp=0)
        session.add(other)
        session.flush()
        for owner in (chapter, other):
            for i in range(5):
                session.add(
                    ContentNode(
                        chapter_id=owner.id,
                        node_id=f"n{i}",
                        ordinal=i,
                        start_cp=0,
                        end_cp=0,
                        node_type=ContentNodeType.IMAGE,
                        tree_json="{}",
                    )
                )
        session.flush()
        seen, cursor = [], None
        for _ in range(10):
            result = content_nodes(
                session,
                migrated_settings,
                book=book,
                version=version,
                chapter_id=chapter.id,
                start_cp=None,
                end_cp=None,
                limit=2,
                cursor=cursor,
            )
            assert all(row.chapter_id == chapter.id for row in result.nodes)
            seen.extend(row.node_id for row in result.nodes)
            cursor = result.next_cursor
            if cursor is None:
                break
        assert seen == [f"n{i}" for i in range(5)]


def test_query_indexes_match_models_and_are_used(migrated_engine) -> None:
    inspector = inspect(migrated_engine)
    for table in Base.metadata.sorted_tables:
        actual = {row["name"]: row["column_names"] for row in inspector.get_indexes(table.name)}
        for index in table.indexes:
            assert actual[index.name] == [column.name for column in index.columns]
    with migrated_engine.connect() as connection:
        for sql, expected in [
            ("SELECT id FROM books ORDER BY created_at,id LIMIT 10", "ix_books_created_at_id"),
            (
                "SELECT id FROM quotes WHERE book_version_id='v' AND start_cp>100",
                "ix_quotes_version_start",
            ),
            (
                "SELECT id FROM jobs WHERE book_version_id='v' AND state='QUEUED'",
                "ix_jobs_version_state",
            ),
        ]:
            plan = " ".join(
                str(row) for row in connection.execute(text("EXPLAIN QUERY PLAN " + sql))
            )
            assert expected in plan


def test_index_upgrade_preserves_existing_rows(tmp_settings: Settings) -> None:
    from ndr.storage.engine import create_db_engine

    run_migrations(tmp_settings, revision="0012")
    engine = create_db_engine(tmp_settings)
    try:
        with Session(engine) as session:
            session.add(
                Book(id="preserved", title="keep", format=BookFormat.TXT, source_sha256="a" * 64)
            )
            session.commit()
        run_migrations(tmp_settings)
        with Session(engine) as session:
            assert session.get(Book, "preserved").title == "keep"
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == head_revision()
    finally:
        engine.dispose()


def test_stale_invalidation_batches_reads_and_preserves_locks_and_reopening(
    migrated_settings: Settings, migrated_engine,
) -> None:
    with Session(migrated_engine) as session:
        outcome = import_txt(
            session, migrated_settings, filename="many.txt", raw=("「甲。」\n" * 80).encode(),
        )
        quotes = list(session.scalars(select(Quote).where(Quote.book_version_id == outcome.version.id)))
        annotations = [Annotation(
            quote_id=row.id, kind=QuoteKind.SPEECH, source=AnnotationSource.MODEL,
            user_locked=i == 0,
        ) for i, row in enumerate(quotes)]
        session.add_all(annotations)
        session.flush()
        session.add(ReviewItem(
            quote_id=quotes[1].id, target_type=ReviewTargetType.QUOTE,
            reason=ReviewReason.STALE_DEPENDENCY, queue_status=ReviewQueueStatus.RESOLVED,
        ))
        session.flush()
        with counted_queries(migrated_engine) as queries:
            result = mark_stale(session, annotations + [annotations[1]])
        assert sum(sql.lstrip().upper().startswith("SELECT") for sql in queries) == 1
        assert annotations[0].stale is False
        assert quotes[0].id in result.skipped_locked_quote_ids
        items = list(session.scalars(select(ReviewItem)))
        assert len(items) == 79
        assert all(row.queue_status == ReviewQueueStatus.PENDING for row in items)
        assert len({row.quote_id for row in items}) == 79
