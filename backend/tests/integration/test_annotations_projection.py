"""集成测试：标注投影（颜色/编号/图例/统计、初读 horizon、只读）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ndr.app import create_app
from ndr.config import Settings
from ndr.domain.enums import AnnotationStatus, JobState
from ndr.jobs.scheduler import run_job
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.migrate import run_migrations
from ndr.storage.models import Annotation, InferenceRun
from ndr.storage.transactions import transaction

SAMPLE = (
    "第一章 雨夜\n"
    "「雨停了。」少女合上伞。\n"
    "少年没有回答，只是把外套递了过去。\n"
    "「……谢谢。」她低声说。\n"
    "「不用谢。」\n"
    "远处传来钟声，两人都没有再开口。\n"
    "「明天也来这里吧。」少年忽然说。\n"
    "「嗯。」少女点了点头。\n"
)


def _import(client: TestClient) -> dict:
    response = client.post(
        "/api/books/import",
        files={"file": ("sample.txt", SAMPLE.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def _fake_profile(client: TestClient) -> str:
    return client.post(
        "/api/model-profiles",
        json={
            "name": "投影用测试提供方",
            "protocol": "fake-provider",
            "base_url": "http://127.0.0.1:1",
            "model": "fake-model",
            "credential_mode": "none",
        },
    ).json()["data"]["id"]


def _create_and_run(client: TestClient, settings: Settings, book_id: str, profile_id: str) -> str:
    job = client.post(
        "/api/jobs",
        json={
            "book_id": book_id,
            "profile_id": profile_id,
            "mode": "process",
            "range": {"start_cp": 0, "end_cp": len(SAMPLE)},
            "budget": {"max_input_tokens": 200_000},
            "idempotency_key": "k-projection",
            "run_now": False,
        },
    ).json()["data"]
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    try:
        outcome = run_job(
            factory, settings, job_id=job["id"], adapter_factory=lambda *_: FakeProviderAdapter()
        )
        assert outcome.state is JobState.COMPLETED
    finally:
        engine.dispose()
    return job["id"]


def test_nested_quotes_are_not_counted_as_unprocessed_targets(
    fake_provider_client: TestClient,
) -> None:
    sample = "第一章\n「她说『明天见』，然后离开了。」\n"
    response = fake_provider_client.post(
        "/api/books/import",
        files={"file": ("nested.txt", sample.encode("utf-8"), "text/plain")},
    )
    data = response.json()["data"]
    payload = fake_provider_client.get(
        f"/api/books/{data['book_id']}/annotations",
        params={"start_cp": 0, "end_cp": len(sample)},
    ).json()["data"]
    assert payload["counts"]["total"] == 0
    assert payload["counts"]["unprocessed_quotes"] == 1


def test_projection_reports_unknown_without_colors(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """FakeProvider 默认输出 UNKNOWN：投影只给统计，不给颜色/编号。"""

    data = _import(fake_provider_client)
    profile_id = _fake_profile(fake_provider_client)
    _create_and_run(fake_provider_client, migrated_settings, data["book_id"], profile_id)

    response = fake_provider_client.get(
        f"/api/books/{data['book_id']}/annotations",
        params={"start_cp": 0, "end_cp": len(SAMPLE)},
    )
    assert response.status_code == 200
    payload = response.json()["data"]

    assert payload["counts"]["total"] >= 4
    assert payload["counts"]["unknown"] >= 4
    assert payload["legend"] == []
    assert all(item["label"] is None and item["color_index"] is None for item in payload["items"])
    assert all(item["status"] == "UNKNOWN" for item in payload["items"])
    assert payload["counts"]["unprocessed_quotes"] >= 0


@pytest.mark.parametrize("first_description,late_description", [
    ("义妹", False), ("绫濑沙季", False), ("绫濑沙季", True),
])
def test_projection_assigns_stable_colors_and_horizon_withholds_late_evidence(
    fake_provider_client: TestClient, migrated_settings: Settings,
    first_description: str, late_description: bool,
) -> None:
    """有明确归属时给颜色/编号；初读 horizon 之下的后文证据不下发颜色。"""

    data = _import(fake_provider_client)
    book_id = data["book_id"]

    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            # 直接写入两条有明确归属的标注，其中第二条的可见时点在文末
            from ndr.storage.models import BookVersion, Quote, Scene, SpeakerGroup

            version = session.get(BookVersion, data["book_version_id"])
            assert version is not None
            quotes = list(
                session.execute(
                    select(Quote).where(Quote.book_version_id == version.id).order_by(Quote.start_cp)
                ).scalars()
            )
            scene = Scene(book_version_id=version.id, start_cp=0, status=__import__(
                "ndr.domain.enums", fromlist=["SceneStatus"]
            ).SceneStatus.OPEN)
            session.add(scene)
            session.flush()
            group = SpeakerGroup(
                scene_id=scene.id,
                first_quote_id=quotes[0].id,
                display_label="S1",
                canonical_name="绫濑沙季",
                description=first_description,
            )
            session.add(group)
            session.flush()
            second_scene = Scene(
                book_version_id=version.id,
                start_cp=quotes[2].start_cp,
                status=__import__("ndr.domain.enums", fromlist=["SceneStatus"]).SceneStatus.OPEN,
            )
            session.add(second_scene)
            session.flush()
            same_person = SpeakerGroup(
                scene_id=second_scene.id,
                first_quote_id=quotes[2].id,
                display_label="S1",
                canonical_name="绫濑沙季",
                description="同一人物在新场景再次出现",
            )
            session.add(same_person)
            session.flush()
            session.add(
                Annotation(
                    quote_id=quotes[0].id,
                    scene_id=scene.id,
                    kind="speech",
                    assignment="EXISTING",
                    basis="DIRECT",
                    speaker_id=group.id,
                    status=AnnotationStatus.ACCEPTED,
                    source="MODEL",
                    visible_from_cp=quotes[0].end_cp,
                )
            )
            session.add(
                Annotation(
                    quote_id=quotes[1].id,
                    scene_id=scene.id,
                    kind="speech",
                    assignment="EXISTING",
                    basis="DIRECT",
                    speaker_id=group.id,
                    status=AnnotationStatus.ACCEPTED,
                    source="MODEL",
                    visible_from_cp=len(SAMPLE),  # 后文才揭示
                )
            )
            session.add(
                Annotation(
                    quote_id=quotes[2].id,
                    scene_id=second_scene.id,
                    kind="speech",
                    assignment="EXISTING",
                    basis="COREFERENCE",
                    speaker_id=same_person.id,
                    status=AnnotationStatus.ACCEPTED,
                    source="MODEL",
                    visible_from_cp=len(SAMPLE) if late_description else quotes[0].end_cp,
                )
            )
    finally:
        engine.dispose()

    full = fake_provider_client.get(
        f"/api/books/{book_id}/annotations",
        params={"start_cp": 0, "end_cp": len(SAMPLE)},
    ).json()["data"]
    assert full["counts"]["accepted"] == 3
    assert [item["color_index"] for item in full["items"]] == [0, 0, 0]
    assert [item["label"] for item in full["items"]] == ["绫濑沙季"] * 3
    expected_description = (
        "义妹" if first_description == "义妹" else "同一人物在新场景再次出现"
    )
    assert {item["speaker_description"] for item in full["items"]} == {expected_description}
    assert len(full["legend"]) == 1
    assert full["legend"][0]["label"] == "绫濑沙季"
    assert full["legend"][0]["description"] == expected_description
    assert full["legend"][0]["quote_count"] == 3

    # 初读：horizon 卡在第一条之后 → 第二条证据尚未出现，不下发颜色/编号
    early = fake_provider_client.get(
        f"/api/books/{book_id}/annotations",
        params={"start_cp": 0, "end_cp": len(SAMPLE), "visible_horizon_cp": SAMPLE.index("少年")},
    ).json()["data"]
    assert early["counts"]["withheld"] == (2 if late_description else 1)
    withheld = [item for item in early["items"] if item["withheld"]]
    assert withheld and withheld[0]["label"] is None and withheld[0]["color_index"] is None
    assert early["legend"][0]["quote_count"] == (1 if late_description else 2)
    if late_description:
        assert early["legend"][0]["description"] == ""
        assert all(item["speaker_description"] == "" for item in early["items"])


def test_projection_is_read_only_and_needs_no_model(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """投影只读：多次请求不会新增标注或推理尝试。"""

    data = _import(fake_provider_client)
    book_id = data["book_id"]

    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            before = (
                len(list(session.execute(select(Annotation)).scalars())),
                len(list(session.execute(select(InferenceRun)).scalars())),
            )
    finally:
        engine.dispose()

    for _ in range(3):
        response = fake_provider_client.get(
            f"/api/books/{book_id}/annotations",
            params={"start_cp": 0, "end_cp": len(SAMPLE)},
        )
        assert response.status_code == 200

    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            after = (
                len(list(session.execute(select(Annotation)).scalars())),
                len(list(session.execute(select(InferenceRun)).scalars())),
            )
    finally:
        engine.dispose()
    assert before == after == (0, 0)


def test_deterministic_fake_provider_produces_colored_projection(tmp_path) -> None:
    """确定性 FakeProvider：建分组后给稳定颜色/编号，供前端离线验证着色链路。

    这是测试专用脚本（NDR_ALLOW_FAKE_PROVIDER=1 + NDR_FAKE_PROVIDER_LABELS=deterministic）；
    真实提供方不会走到这里，界面也不会把假结果当成真实成功。
    """

    settings = Settings(
        data_dir=tmp_path / "data",
        credential_backend="session",
        allow_fake_provider=True,
        fake_provider_labels="deterministic",
    )
    run_migrations(settings)
    app = create_app(settings)
    with TestClient(app) as client:
        data = _import(client)
        profile_id = _fake_profile(client)
        job = client.post(
            "/api/jobs",
            json={
                "book_id": data["book_id"],
                "profile_id": profile_id,
                "mode": "preview",
                "range": {"start_cp": 0, "end_cp": len(SAMPLE)},
                "idempotency_key": "k-deterministic",
                "run_now": False,
            },
        ).json()["data"]

        engine = create_db_engine(settings)
        factory = create_session_factory(engine)
        try:
            outcome = run_job(
                factory, settings, job_id=job["id"], credentials=app.state.credentials
            )
        finally:
            engine.dispose()
        assert outcome.state is JobState.COMPLETED
        assert outcome.calls >= 1

        payload = client.get(
            f"/api/books/{data['book_id']}/annotations",
            params={"start_cp": 0, "end_cp": len(SAMPLE)},
        ).json()["data"]

    colored = [item for item in payload["items"] if item["color_index"] is not None]
    assert colored, payload["counts"]
    assert {item["color_index"] for item in colored} == {0}
    assert {item["label"] for item in colored} == {"确定性测试说话人"}
    assert payload["counts"]["accepted"] >= 1
    assert payload["legend"] and payload["legend"][0]["label"] == "确定性测试说话人"
