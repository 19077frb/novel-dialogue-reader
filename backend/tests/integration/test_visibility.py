"""T15 集成测试：证据时点、初读投影与最终定位回归（F04 / F11 / F17）。

覆盖：

- F04：EPUB 的 ruby 注音不进正文、插图成为节点、跨块引语范围正确、脚本/样式不渲染；
- F11：重复出现的同一句对白有不同 ID；emoji/扩展汉字按**码点**计数与切片；
- F17：后文才揭示的身份合并，在初读 horizon 之下不会提前同色（颜色/图例都不泄露）。

门槛：投影查询是**只读**的（不写库、不调用模型），本文件的用例对行数做了断言。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from fixtures.corrections import session_scope
from fixtures.epub_factory import Document, EpubSpec, build_epub, ruby_and_image_spec
from ndr.config import Settings
from ndr.domain.enums import IdentityOperation, JobState
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import (
    Annotation,
    AnnotationHistory,
    IdentityRevision,
    Quote,
    Scene,
    SpeakerGroup,
)
from ndr.storage.transactions import transaction

ASTRAAL_SAMPLE = (
    "第一章 跨块\n"
    "「跨块\n"
    "的同一句。」\n"
    "😀𠮷野家的猫跳上窗台。\n"
    "「嗯。」她说。\n"
    "「嗯。」他又说了一遍。\n"
    "「结束。」\n"
)


def _import_txt(client: TestClient, text: str, name: str = "visibility.txt") -> dict:
    response = client.post(
        "/api/books/import",
        files={"file": (name, text.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def _import_epub(client: TestClient, spec: EpubSpec, name: str = "visibility.epub") -> dict:
    response = client.post(
        "/api/books/import",
        files={"file": (name, build_epub(spec), "application/epub+zip")},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def _nodes(client: TestClient, book_id: str) -> list[dict]:
    response = client.get(f"/api/books/{book_id}/content", params={"limit": 50})
    assert response.status_code == 200, response.text
    return response.json()["data"]["nodes"]


def _quotes(client: TestClient, book_id: str) -> list[dict]:
    response = client.get(f"/api/books/{book_id}/quotes", params={"limit": 50})
    assert response.status_code == 200, response.text
    return response.json()["data"]["items"]


def _locate(client: TestClient, book_id: str, start_cp: int, end_cp: int) -> dict:
    response = client.get(
        f"/api/books/{book_id}/locate", params={"start_cp": start_cp, "end_cp": end_cp}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _rows(settings: Settings) -> dict[str, int]:
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            return {
                "annotations": len(list(session.execute(select(Annotation)).scalars())),
                "history": len(list(session.execute(select(AnnotationHistory)).scalars())),
                "revisions": len(list(session.execute(select(IdentityRevision)).scalars())),
            }
    finally:
        engine.dispose()

def test_f11_repeated_quotes_and_codepoint_mapping(migrated_client: TestClient) -> None:
    """F11：重复对白 ID 不同；emoji/扩展汉字按码点（不是 UTF-16 单元）定位。"""

    data = _import_txt(migrated_client, ASTRAAL_SAMPLE)
    book_id = data["book_id"]
    quotes = _quotes(migrated_client, book_id)
    nodes = _nodes(migrated_client, book_id)

    # 同一句「嗯。」出现两次 → ID 不同（按位置派生），范围各自独立
    repeated = [row for row in quotes if row["text"] == "嗯。"]
    assert len(repeated) == 2
    assert repeated[0]["quote_id"] != repeated[1]["quote_id"]
    assert repeated[0]["start_cp"] != repeated[1]["start_cp"]

    # 每一段引语的范围都能原样定位回来（含引号）
    for quote in quotes:
        located = _locate(migrated_client, book_id, quote["start_cp"], quote["end_cp"])
        assert located["text"] == quote["delimited_text"], quote

    # astral 字符：😀 与 𠮷 各占 1 个码点，但在 UTF-16 里各占 2 个单元
    astral = next(node for node in nodes if "😀" in node["text"])
    assert astral["text"] == "😀𠮷野家的猫跳上窗台。"
    assert astral["end_cp"] - astral["start_cp"] == len(astral["text"]) == 11
    assert len(astral["text"].encode("utf-16-le")) // 2 == 13
    assert data["canonical_length_cp"] == len(ASTRAAL_SAMPLE)

    # 引语紧跟 astral 段之后，起点必须等于该段结束（说明前面按码点计数）
    following = next(row for row in quotes if row["text"] == "嗯。")
    assert following["start_cp"] == astral["end_cp"] + 1  # 加上一个换行

    # 跨行引语：TXT 里同一段落的换行不拆节点，但范围仍要能原样定位回来
    cross = next(row for row in quotes if "跨块" in row["text"])
    located = _locate(migrated_client, book_id, cross["start_cp"], cross["end_cp"])
    assert located["text"] == cross["delimited_text"]


def test_f04_epub_ruby_image_and_cross_node_quote(migrated_client: TestClient) -> None:
    """F04：ruby 注音不进正文、插图登记为节点、跨块引语范围正确。"""

    data = _import_epub(migrated_client, ruby_and_image_spec())
    book_id = data["book_id"]
    nodes = _nodes(migrated_client, book_id)

    # ruby：基底文字留在正文，注音与 rp 括号不重复进入 canonical 文本
    ruby_node = next(node for node in nodes if "漢" in node["text"])
    assert "かん" not in ruby_node["text"]
    assert "(" not in ruby_node["text"]
    located = _locate(migrated_client, book_id, ruby_node["start_cp"], ruby_node["end_cp"])
    assert "かん" not in located["text"]

    # 插图成为 image 节点并登记资源
    image = next(node for node in nodes if node["node_type"] == "image")
    assert image["payload"]["resource_id"].startswith("r")

    # 脚本与样式不渲染：既没有节点文本，也没有可执行内容
    assert all("__evil" not in node["text"] for node in nodes)
    assert all("color: red" not in node["text"] for node in nodes)

    # 跨块引语（真正的两个块）：范围覆盖两个节点，定位回来的文本与候选一致，
    # 并且中间那段是 synthetic 换行（不是原文件里存在的空白）
    cross_data = _import_epub(
        migrated_client,
        EpubSpec(
            title="跨块样例",
            documents=[
                Document(
                    doc_id="ch1",
                    href="text/chapter.xhtml",
                    body=(
                        "<h1>第一章 跨块</h1>\n"
                        "<p>「跨块</p>\n"
                        "<p>的同一句。」</p>\n"
                    ),
                    title="第一章 跨块",
                )
            ],
            nav=[("text/chapter.xhtml", "第一章 跨块")],
        ),
        name="cross-block.epub",
    )
    cross_nodes = _nodes(migrated_client, cross_data["book_id"])
    cross_quote = next(
        row for row in _quotes(migrated_client, cross_data["book_id"]) if "跨块" in row["text"]
    )
    overlapping = [
        node
        for node in cross_nodes
        if cross_quote["start_cp"] < node["end_cp"] and cross_quote["end_cp"] > node["start_cp"]
    ]
    assert len(overlapping) == 2
    located_cross = _locate(
        migrated_client, cross_data["book_id"], cross_quote["start_cp"], cross_quote["end_cp"]
    )
    assert located_cross["text"] == cross_quote["delimited_text"]
    assert any(span["synthetic"] for span in located_cross["spans"])

MERGE_SAMPLE = (
    "第一章 声音\n"
    "「雨停了。」少女说。\n"
    "「……谢谢。」她低声说。\n"
    "少年抬起头，露出了脸。\n"
    "原来一直说话的是同一个人。\n"
)


def test_f17_horizon_does_not_reveal_later_identity_merge(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """F17：文末才揭示的合并，在初读 horizon 之前不能同色，也不能进图例。"""

    from fixtures.corrections import create_fake_profile, run_deterministic_job

    data = _import_txt(fake_provider_client, MERGE_SAMPLE, name="merge.txt")
    book_id = data["book_id"]
    profile_id = create_fake_profile(fake_provider_client, name="T15 身份提供方")
    run_deterministic_job(
        migrated_settings,
        fake_provider_client,
        book_id=book_id,
        profile_id=profile_id,
        key="k-visibility",
    )

    with session_scope(migrated_settings) as factory, transaction(factory) as session:
        quotes = list(
            session.execute(
                select(Quote)
                .where(Quote.book_version_id == data["book_version_id"])
                .order_by(Quote.start_cp)
            ).scalars()
        )
        assert len(quotes) >= 2
        first, second = quotes[0], quotes[1]
        first_annotation = session.execute(
            select(Annotation).where(Annotation.quote_id == first.id)
        ).scalar_one()
        second_annotation = session.execute(
            select(Annotation).where(Annotation.quote_id == second.id)
        ).scalar_one()
        scene = session.get(Scene, first_annotation.scene_id)
        assert scene is not None
        survivor = session.get(SpeakerGroup, first_annotation.speaker_id)
        assert survivor is not None

        # 造出第二个分组（模拟模型先判成两个声音）
        other = SpeakerGroup(
            scene_id=scene.id,
            first_quote_id=second.id,
            display_label="S2",
            evidence_refs_json="[]",
        )
        session.add(other)
        session.flush()
        second_annotation.speaker_id = other.id
        second_annotation.version += 1

        # 模拟「文末才揭示两者是同一个人」：可见时点在全书最后
        second_annotation.speaker_id = survivor.id
        second_annotation.version += 1
        session.add(
            IdentityRevision(
                scene_id=scene.id,
                operation=IdentityOperation.MERGE,
                input_ids_json=json.dumps([other.id, survivor.id]),
                output_ids_json=json.dumps([survivor.id]),
                snapshot_json=json.dumps(
                    {
                        "operation": "MERGE",
                        "revert": {"quotes": {second.id: other.id}, "groups": {}},
                    }
                ),
                evidence_refs_json=json.dumps([second.id]),
                visible_from_cp=len(MERGE_SAMPLE) - 1,
                version=1,
            )
        )
        session.flush()

    rows_before = _rows(migrated_settings)
    early_horizon = MERGE_SAMPLE.index("少年抬起头")

    early = fake_provider_client.get(
        f"/api/books/{book_id}/annotations",
        params={
            "start_cp": 0,
            "end_cp": len(MERGE_SAMPLE),
            "reading_mode": "initial",
            "visible_horizon_cp": early_horizon,
        },
    ).json()["data"]
    labels = {item["quote_id"]: item["label"] for item in early["items"]}
    colors = {item["quote_id"]: item["color_index"] for item in early["items"]}
    assert early["identity_reverts"] == 1
    assert labels[second.id] != labels[first.id]  # 不提前同色
    assert colors[second.id] != colors[first.id]
    assert {row["label"] for row in early["legend"]} == {labels[first.id], labels[second.id]}
    assert all(item["withheld"] is False for item in early["items"])

    # horizon 越过证据 → 合并生效（这是“后文已读到”的状态）
    late = fake_provider_client.get(
        f"/api/books/{book_id}/annotations",
        params={
            "start_cp": 0,
            "end_cp": len(MERGE_SAMPLE),
            "reading_mode": "initial",
            "visible_horizon_cp": len(MERGE_SAMPLE),
        },
    ).json()["data"]
    assert late["identity_reverts"] == 0
    late_labels = {item["quote_id"]: item["label"] for item in late["items"]}
    late_colors = {item["quote_id"]: item["color_index"] for item in late["items"]}
    assert late_labels[second.id] == late_labels[first.id]
    assert late_colors[second.id] == late_colors[first.id]
    assert len(late["legend"]) == 1

    # 重读模式不看 horizon：直接显示最终身份
    reread = fake_provider_client.get(
        f"/api/books/{book_id}/annotations",
        params={
            "start_cp": 0,
            "end_cp": len(MERGE_SAMPLE),
            "reading_mode": "reread",
            "visible_horizon_cp": early_horizon,
        },
    ).json()["data"]
    assert reread["identity_reverts"] == 0
    reread_labels = {item["quote_id"]: item["label"] for item in reread["items"]}
    assert reread_labels[second.id] == reread_labels[first.id]

    # 门槛：投影查询只读（不写库、不重新推理）
    assert _rows(migrated_settings) == rows_before

TWO_CHAPTER_SAMPLE = (
    "第一章 雨夜\n"
    "「雨停了。」少女合上伞。\n"
    "「……谢谢。」她低声说。\n"
    "第二章 名字\n"
    "「我叫小満。」她抬起头。\n"
    "「我叫阿透。」少年笑了笑。\n"
)


def test_f17_engine_merge_visible_only_after_evidence(
    fake_provider_client: TestClient, migrated_settings: Settings
) -> None:
    """F17（端到端离线）：模型先判成两个声音、后用末尾证据合并，初读不提前同色。"""

    from fixtures.corrections import create_fake_profile
    from ndr.llm.adapters.fake import FakeProviderAdapter

    data = _import_txt(fake_provider_client, TWO_CHAPTER_SAMPLE, name="two-chapter.txt")
    book_id = data["book_id"]
    profile_id = create_fake_profile(fake_provider_client, name="T15 合并提供方")
    job = fake_provider_client.post(
        "/api/jobs",
        json={
            "book_id": book_id,
            "profile_id": profile_id,
            "mode": "process",
            "range": {"start_cp": 0, "end_cp": len(TWO_CHAPTER_SAMPLE)},
            "idempotency_key": "k-split-then-merge",
            "run_now": False,
        },
    ).json()["data"]

    from ndr.jobs.scheduler import run_job
    from ndr.storage.engine import create_db_engine, create_session_factory

    engine = create_db_engine(migrated_settings)
    factory = create_session_factory(engine)
    try:
        outcome = run_job(
            factory,
            migrated_settings,
            job_id=job["id"],
            adapter_factory=lambda *_: FakeProviderAdapter(script_mode="split_then_merge"),
        )
    finally:
        engine.dispose()
    assert outcome.state is JobState.COMPLETED, outcome.as_dict()

    # 两个声音都在第一章出现，合并证据在第二章末尾
    first_chapter_quotes = [
        row for row in _quotes(fake_provider_client, book_id) if row["start_cp"] < 30
    ]
    assert len(first_chapter_quotes) == 2

    early = fake_provider_client.get(
        f"/api/books/{book_id}/annotations",
        params={
            "start_cp": 0,
            "end_cp": 30,
            "reading_mode": "initial",
            "visible_horizon_cp": 30,
        },
    ).json()["data"]
    assert early["identity_reverts"] == 1
    labels = [row["label"] for row in early["items"]]
    assert sorted(labels) == ["S1", "S2"]
    assert len({row["color_index"] for row in early["items"]}) == 2
    assert len(early["legend"]) == 2

    reread = fake_provider_client.get(
        f"/api/books/{book_id}/annotations",
        params={"start_cp": 0, "end_cp": 30, "reading_mode": "reread"},
    ).json()["data"]
    assert reread["identity_reverts"] == 0
    assert {row["label"] for row in reread["items"]} == {"S1"}
    assert len(reread["legend"]) == 1
