"""测试夹具：导入样例 → 用**确定性** FakeProvider 生成真实标注。

与投影测试同源：FakeProvider 只在测试/演示中显式启用，且这里的
`labeling_mode="deterministic"` 是测试专用脚本（真实提供方不会走到该分支）。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from ndr.config import Settings
from ndr.domain.enums import JobState
from ndr.jobs.scheduler import run_job
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.storage.engine import create_db_engine, create_session_factory

SAMPLE = (
    "第一章 雨夜\n"
    "「雨停了。」少女合上伞。\n"
    "少年没有回答，只是把外套递了过去。\n"
    "「……谢谢。」她低声说。\n"
    "「不用谢。」\n"
    "远处传来钟声，两人都没有再开口。\n"
    "「明天也来这里吧。」少年忽然说。\n"
    "「嗯。」少女点了点头。\n"
    "第二章 名字\n"
    "「我叫小満。」她抬起头。\n"
    "「我叫阿透。」少年笑了笑。\n"
    "「明天见。」\n"
)

CHAPTER_TWO_CP = SAMPLE.index("第二章 名字")


def import_sample(client: TestClient, sample: str = SAMPLE) -> dict:
    response = client.post(
        "/api/books/import",
        files={"file": ("sample.txt", sample.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def create_fake_profile(
    client: TestClient, *, name: str = "测试提供方", model: str = "fake-model"
) -> str:
    response = client.post(
        "/api/model-profiles",
        json={
            "name": name,
            "protocol": "fake-provider",
            "base_url": "http://127.0.0.1:1",
            "model": model,
            "credential_mode": "none",
        },
    )
    assert response.status_code in {200, 201}, response.text
    return response.json()["data"]["id"]


@contextmanager
def session_scope(settings: Settings) -> Iterator[sessionmaker[Session]]:
    engine = create_db_engine(settings)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def run_deterministic_job(
    settings: Settings,
    client: TestClient,
    *,
    book_id: str,
    profile_id: str,
    key: str,
    start_cp: int = 0,
    end_cp: int | None = None,
) -> str:
    """跑一次确定性任务，产生真实场景/分组/标注（不发任何网络请求）。"""

    range_payload: dict[str, int] = {"start_cp": start_cp}
    if end_cp is not None:
        range_payload["end_cp"] = end_cp
    job = client.post(
        "/api/jobs",
        json={
            "book_id": book_id,
            "profile_id": profile_id,
            "mode": "process",
            "range": range_payload,
            "idempotency_key": key,
            "run_now": False,
        },
    )
    assert job.status_code == 202, job.text
    job_id = job.json()["data"]["id"]
    with session_scope(settings) as factory:
        outcome = run_job(
            factory,
            settings,
            job_id=job_id,
            adapter_factory=lambda *_: FakeProviderAdapter(labeling_mode="deterministic"),
        )
    assert outcome.state is JobState.COMPLETED, outcome.as_dict()
    return job_id


def quote_ids(client: TestClient, book_id: str) -> list[str]:
    payload = client.get(f"/api/books/{book_id}/quotes", params={"limit": 500}).json()["data"]
    return [item["quote_id"] for item in payload["items"]]


def annotations_of(client: TestClient, book_id: str, *, start_cp: int = 0, end_cp: int | None = None):
    params = {"start_cp": start_cp}
    if end_cp is not None:
        params["end_cp"] = end_cp
    response = client.get(f"/api/books/{book_id}/annotations", params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]

def annotation_state(settings: Settings, quote_id: str) -> dict:
    """当前标注的普通 dict（脱离 session 后仍可断言）。"""

    from sqlalchemy import select

    from ndr.storage.models import Annotation
    from ndr.storage.transactions import transaction

    with session_scope(settings) as factory, transaction(factory) as session:  # type: Session
        row = session.execute(
            select(Annotation).where(Annotation.quote_id == quote_id)
        ).scalar_one()
        return {
            "id": row.id,
            "version": row.version,
            "scene_id": row.scene_id,
            "kind": row.kind.value,
            "assignment": row.assignment.value if row.assignment else None,
            "basis": row.basis.value if row.basis else None,
            "speaker_id": row.speaker_id,
            "status": row.status.value,
            "source": row.source.value,
            "stale": row.stale,
            "user_locked": row.user_locked,
        }


def scene_state(settings: Settings, scene_id: str) -> dict:
    from ndr.storage.models import Scene
    from ndr.storage.transactions import transaction

    with session_scope(settings) as factory, transaction(factory) as session:
        row = session.get(Scene, scene_id)
        assert row is not None
        return {
            "id": row.id,
            "status": row.status.value,
            "start_cp": row.start_cp,
            "end_cp": row.end_cp,
            "version": row.version,
        }


def scene_groups(settings: Settings, scene_id: str) -> dict[str, str]:
    """scene 内的 group_id → 展示编号。"""

    from sqlalchemy import select

    from ndr.storage.models import SpeakerGroup
    from ndr.storage.transactions import transaction

    with session_scope(settings) as factory, transaction(factory) as session:
        return {
            row.id: row.display_label
            for row in session.execute(
                select(SpeakerGroup).where(SpeakerGroup.scene_id == scene_id)
            ).scalars()
        }


def count_rows(settings: Settings, model) -> int:  # noqa: ANN001
    from sqlalchemy import select

    from ndr.storage.transactions import transaction

    with session_scope(settings) as factory, transaction(factory) as session:
        return len(list(session.execute(select(model)).scalars()))
