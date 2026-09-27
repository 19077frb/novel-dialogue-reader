"""T05 集成测试：候选引语/Gap/定位接口，以及重扫的幂等与保护。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ndr.config import Settings
from ndr.domain.enums import AnnotationSource, AnnotationStatus, QuoteKind
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import Annotation, Quote, Scene, SpeakerGroup
from ndr.storage.transactions import transaction

SAMPLE = (
    "序章 雨夜\n"
    "\n"
    "「雨停了。」少女合上伞。\n"
    "少年没有回答，只是把外套递了过去。\n"
    "\n"
    "「……谢谢。」她低声说。\n"
    "第一章 转折\n"
    "远处传来钟声。\n"
    "「明天也来这里吧。」少年忽然说。\n"
    "少女说：「他当时说的是『明天见』。」\n"
)

EXPECTED_QUOTES = ["「雨停了。」", "「……谢谢。」", "「明天也来这里吧。」", "「他当时说的是『明天见』。」", "『明天见』"]


def _import(client: TestClient) -> dict:
    response = client.post(
        "/api/books/import",
        files={"file": ("sample.txt", SAMPLE.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def _quotes(client: TestClient, book_id: str, **params) -> list[dict]:
    response = client.get(f"/api/books/{book_id}/quotes", params=params)
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"]


def test_import_produces_candidate_quotes(migrated_client: TestClient) -> None:
    data = _import(migrated_client)
    items = _quotes(migrated_client, data["book_id"])

    assert [item["delimited_text"] for item in items] == EXPECTED_QUOTES
    first = items[0]
    assert first["text"] == "雨停了。"
    assert first["delimiter"] == "corner_bracket"
    assert first["nesting_depth"] == 0
    assert first["parent_quote_id"] is None
    assert first["kind_hint"] is None  # 扫描器不判定对白/心声

    nested = items[-1]
    assert nested["nesting_depth"] == 1
    assert nested["parent_quote_id"] == items[-2]["quote_id"]
    assert nested["kind_hint"] == QuoteKind.QUOTATION.value

    # 扫描不产生任何识别结果
    assert all("speaker" not in item and "assignment" not in item for item in items)


def test_quotes_can_be_filtered_by_chapter(migrated_client: TestClient) -> None:
    data = _import(migrated_client)
    book_id = data["book_id"]
    chapters = migrated_client.get(f"/api/books/{book_id}/chapters").json()["data"]

    first_chapter_quotes = _quotes(migrated_client, book_id, chapter_id=chapters[0]["id"])
    assert [item["text"] for item in first_chapter_quotes] == ["雨停了。", "……谢谢。"]
    second_chapter_quotes = _quotes(migrated_client, book_id, chapter_id=chapters[1]["id"])
    assert [item["text"] for item in second_chapter_quotes] == ["明天也来这里吧。", "他当时说的是『明天见』。", "明天见"]


def test_quote_detail_includes_context_and_gap(migrated_client: TestClient) -> None:
    data = _import(migrated_client)
    items = _quotes(migrated_client, data["book_id"])
    second = items[1]

    detail = migrated_client.get(f"/api/quotes/{second['quote_id']}").json()["data"]
    assert detail["quote"]["quote_id"] == second["quote_id"]
    assert detail["quote"]["text"] == "……谢谢。"
    assert "合上伞" in detail["context_before"]
    assert detail["previous_quote_id"] == items[0]["quote_id"]
    assert detail["next_quote_id"] == items[2]["quote_id"]

    gap = detail["gap_before"]
    assert gap is not None
    assert "少年没有回答" in gap["narration"]
    assert gap["decision"] == "UNCERTAIN"  # 扫描器不判断 Gap 语义


def test_gaps_list_contains_narration(migrated_client: TestClient) -> None:
    data = _import(migrated_client)
    response = migrated_client.get(f"/api/books/{data['book_id']}/gaps")
    assert response.status_code == 200
    gaps = response.json()["data"]["items"]

    assert len(gaps) == 3  # 4 个外层候选之间只隔出 3 个有叙述的间隔
    assert any("少年没有回答" in gap["narration"] for gap in gaps)
    assert all(gap["decision"] == "UNCERTAIN" for gap in gaps)


def test_locate_maps_code_points_to_chapter_and_node(migrated_client: TestClient) -> None:
    data = _import(migrated_client)
    book_id = data["book_id"]
    items = _quotes(migrated_client, book_id)
    quote = items[0]

    located = migrated_client.get(
        f"/api/books/{book_id}/locate",
        params={"start_cp": quote["start_cp"], "end_cp": quote["end_cp"]},
    ).json()["data"]

    assert located["text"] == "「雨停了。」"
    assert located["spans"]
    span = located["spans"][0]
    assert span["node_id"] is not None
    assert span["node_type"] == "paragraph"
    assert span["chapter_ordinal"] == 0
    assert span["synthetic"] is False
    assert "雨停了" in span["text"]

    invalid = migrated_client.get(
        f"/api/books/{book_id}/locate", params={"start_cp": 10, "end_cp": 5}
    )
    assert invalid.status_code == 422


def test_rescan_is_idempotent(migrated_client: TestClient) -> None:
    data = _import(migrated_client)
    book_id = data["book_id"]
    before = _quotes(migrated_client, book_id)

    response = migrated_client.post(f"/api/books/{book_id}/quotes/scan")
    assert response.status_code == 202, response.text
    payload = response.json()["data"]
    assert payload["scanner_version"] == "quote-scan-1"
    assert payload["quote_count"] == len(before)
    assert payload["gap_count"] == 3

    after = _quotes(migrated_client, book_id)
    assert [item["quote_id"] for item in after] == [item["quote_id"] for item in before]

    job = migrated_client.get(f"/api/jobs/{payload['job_id']}").json()["data"]
    assert job["kind"] == "RECOMPUTE"
    assert job["state"] == "COMPLETED"


def test_rescan_is_refused_when_user_labeling_exists(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    data = _import(migrated_client)
    book_id = data["book_id"]
    quote_id = _quotes(migrated_client, book_id)[0]["quote_id"]

    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            session.add(
                Annotation(
                    quote_id=quote_id,
                    kind=QuoteKind.SPEECH,
                    status=AnnotationStatus.USER_CONFIRMED,
                    source=AnnotationSource.USER,
                    user_locked=True,
                )
            )
    finally:
        engine.dispose()

    response = migrated_client.post(f"/api/books/{book_id}/quotes/scan")
    assert response.status_code == 409
    assert response.json()["error"]["details"]["reason"] == "USER_LABELING_PRESENT"
    # 候选没有被覆盖：人工结果仍在，候选数量不变
    assert len(_quotes(migrated_client, book_id)) == len(EXPECTED_QUOTES)


def test_scanning_creates_no_annotations_or_scenes(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    _import(migrated_client)

    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            assert session.execute(select(func.count(Annotation.id))).scalar_one() == 0
            assert session.execute(select(func.count(Scene.id))).scalar_one() == 0
            assert session.execute(select(func.count(SpeakerGroup.id))).scalar_one() == 0
            assert session.execute(select(func.count(Quote.id))).scalar_one() == len(EXPECTED_QUOTES)
    finally:
        engine.dispose()


def test_quotes_of_unknown_book_return_contract_error(migrated_client: TestClient) -> None:
    response = migrated_client.get("/api/books/nope/quotes")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"

    detail = migrated_client.get("/api/quotes/nope")
    assert detail.status_code == 404
