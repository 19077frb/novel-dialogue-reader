"""T19 升级测试：重跑/升级迁移后，原书籍、人工确认（`user_locked` 标注）与待确认队列都还在。

真实升级路径（新增一个 revision 后执行 `alembic upgrade head`）与这里的「head → head 重跑」走的是同一条
迁移管道；本用例先把数据写进去，再跑迁移，最后逐项核对数据与接口仍可用。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from ndr.config import Settings
from ndr.domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    QuoteKind,
    ReviewQueueStatus,
    ReviewReason,
    ReviewTargetType,
)
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.migrate import run_migrations
from ndr.storage.models import Annotation, AnnotationHistory, Book, Quote, ReviewItem
from ndr.storage.transactions import transaction

TXT = "第一章 雨夜\n「雨停了。」少女合上伞。\n"


def test_rerunning_migrations_keeps_books_annotations_and_queue(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    response = migrated_client.post(
        "/api/books/import",
        files={"file": ("upgrade.txt", TXT.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 202, response.text
    data = response.json()["data"]

    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            quote = (
                session.execute(
                    select(Quote)
                    .where(Quote.book_version_id == data["book_version_id"])
                    .order_by(Quote.start_cp)
                )
                .scalars()
                .first()
            )
            assert quote is not None
            annotation = Annotation(
                quote_id=quote.id,
                kind=QuoteKind.SPEECH,
                status=AnnotationStatus.USER_CONFIRMED,
                source=AnnotationSource.USER,
                user_locked=True,
                evidence_refs_json="[]",
            )
            session.add(annotation)
            session.flush()
            session.add(
                AnnotationHistory(
                    annotation_id=annotation.id,
                    revision=1,
                    snapshot_json="{}",
                    run_id=None,
                    correction_id=None,
                )
            )
            session.add(
                ReviewItem(
                    target_type=ReviewTargetType.QUOTE,
                    quote_id=quote.id,
                    reason=ReviewReason.USER_FLAGGED,
                    queue_status=ReviewQueueStatus.PENDING,
                    candidates_json="[]",
                    annotation_version=1,
                )
            )
            quote_id = quote.id
            annotation_id = annotation.id

        # 升级/重跑迁移
        run_migrations(migrated_settings)

        check_engine = create_db_engine(migrated_settings)
        check_factory = create_session_factory(check_engine)
        try:
            with transaction(check_factory) as session:
                stored = session.get(Annotation, annotation_id)
                assert stored is not None
                assert stored.user_locked is True  # 人工确认不能被迁移抹掉
                assert stored.status is AnnotationStatus.USER_CONFIRMED
                assert session.get(Book, data["book_id"]) is not None

                history = list(
                    session.execute(
                        select(AnnotationHistory).where(
                            AnnotationHistory.annotation_id == annotation_id
                        )
                    ).scalars()
                )
                assert len(history) == 1

                items = list(
                    session.execute(
                        select(ReviewItem).where(ReviewItem.quote_id == quote_id)
                    ).scalars()
                )
                assert len(items) == 1
                assert items[0].queue_status is ReviewQueueStatus.PENDING
        finally:
            check_engine.dispose()

        # 迁移后接口仍然可用
        assert migrated_client.get(f"/api/books/{data['book_id']}").status_code == 200
        quotes = migrated_client.get(
            f"/api/books/{data['book_id']}/quotes?limit=5"
        ).json()["data"]["items"]
        assert quotes and quotes[0]["delimited_text"] == "「雨停了。」"
    finally:
        engine.dispose()
