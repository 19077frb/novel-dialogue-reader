"""Bounded export samples preserve full-range frozen coverage and release writer locks."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import delete, event, select, update

from fixtures.corrections import import_sample
from ndr.api import exports as export_api
from ndr.domain.enums import ContentNodeType, ExportStylePreset
from ndr.exports import render as renderer
from ndr.exports.service import coverage_of, render_sample
from ndr.storage.models import Book, BookVersion, Chapter, ContentNode, ExportSnapshot
from ndr.storage.transactions import transaction


def _book(client):
    return import_sample(client, "".join(
        f"第{number}章 原创样张{number}\n" + "".join(
            f"原创章节{number}段落{index}，风吹过桥面。\n" for index in range(30)
        ) for number in range(1, 4)
    ))


def test_sample_limits_nodes_and_matches_full_selected_range_coverage(migrated_client, monkeypatch):
    client = migrated_client
    data = _book(client)
    factory = client.app.state.session_factory
    with transaction(factory) as session:
        book = session.get(Book, data["book_id"])
        version = session.get(BookVersion, data["book_version_id"])
        chapters = list(session.scalars(select(Chapter).where(
            Chapter.book_version_id == version.id,
        ).order_by(Chapter.ordinal)))
        assert len(chapters) == 3
        nodes = list(session.scalars(select(ContentNode).where(
            ContentNode.chapter_id == chapters[0].id,
            ContentNode.node_type == ContentNodeType.PARAGRAPH,
        ).order_by(ContentNode.start_cp)))
        # Every renderer branch, including empty text and non-text sample blocks.
        for index, (kind, start, end) in enumerate([
            (ContentNodeType.IMAGE, None, None),
            (ContentNodeType.SEPARATOR, None, None),
            (ContentNodeType.PARAGRAPH, nodes[0].start_cp, nodes[0].start_cp),
            (ContentNodeType.HEADING, nodes[0].start_cp, nodes[0].end_cp),
            (ContentNodeType.FOOTNOTE, nodes[0].start_cp, nodes[0].end_cp),
        ]):
            session.add(ContentNode(chapter_id=chapters[0].id, node_id=f"sample-extra-{index}",
                                    node_type=kind, ordinal=1000 + index, tree_json="{}",
                                    start_cp=start, end_cp=end))
        session.flush()
        items = [
            dict(quote_id="outer", start_cp=nodes[0].start_cp, end_cp=nodes[1].end_cp,
                 label="甲", color_index=1),
            dict(quote_id="inner", start_cp=nodes[0].start_cp + 1, end_cp=nodes[0].end_cp - 1,
                 label="乙", color_index=2),
            dict(quote_id="later", start_cp=nodes[20].start_cp, end_cp=nodes[20].end_cp,
                 label="甲", color_index=1),
            dict(quote_id="hidden", start_cp=nodes[21].start_cp, end_cp=nodes[21].end_cp,
                 label="隐藏", color_index=3, withheld=True),
            dict(quote_id="stale", start_cp=nodes[22].start_cp, end_cp=nodes[22].end_cp,
                 label="过期", color_index=4, stale=True),
            dict(quote_id="unknown", start_cp=nodes[23].start_cp, end_cp=nodes[23].end_cp),
        ]
        payload = dict(items=items, legend=[dict(label="甲", color_index=1),
                                           dict(label="乙", color_index=2)])
        selected = [chapters[2].id, chapters[0].id]
        full = renderer.render_book(session, client.app.state.settings, book=book, version=version,
                                    projection_payload=payload,
                                    style=ExportStylePreset.COLOR_AND_LABEL, chapter_ids=selected)
        calls = []
        original = renderer._runs_for_node

        def counted(*args, **kwargs):
            calls.append(kwargs["node_start"])
            return original(*args, **kwargs)

        monkeypatch.setattr(renderer, "_runs_for_node", counted)
        # Evict ORM nodes so load events measure actual materialization, not cache hits.
        session.expunge_all()
        loaded = []

        def on_load(node, context):
            loaded.append(node.id)

        event.listen(ContentNode, "load", on_load)
        try:
            html, fragments, coverage = render_sample(
                session, client.app.state.settings, book=book, version=version,
                projection_payload=payload, style=ExportStylePreset.COLOR_AND_LABEL,
                chapter_ids=selected,
            )
        finally:
            event.remove(ContentNode, "load", on_load)
        assert coverage == coverage_of(full)
        assert coverage["paragraphs_with_annotations"] == 4  # two paragraphs, footnote, later
        assert len(loaded) == 12
        assert len(calls) <= 12
        assert fragments == [block.plain_text for block in full.chapters[0].blocks[:12]
                             if block.plain_text]
        assert "原创章节2段落" not in html
        assert "原创章节3段落" not in html
        assert "原创章节1段落20" not in html


def test_preview_commits_snapshot_before_render_and_uses_selected_chapter(migrated_client, monkeypatch):
    client = migrated_client
    data = _book(client)
    factory = client.app.state.session_factory
    with factory() as session:
        chapters = list(session.scalars(select(Chapter).where(
            Chapter.book_version_id == data["book_version_id"],
        ).order_by(Chapter.ordinal)))
    original = export_api.render_sample
    visited = []

    def concurrent_writer(*args, **kwargs):
        with transaction(factory) as other:
            snapshot = other.scalar(select(ExportSnapshot).where(
                ExportSnapshot.book_id == data["book_id"],
            ))
            assert snapshot is not None
            visited.append(snapshot.id)
            other.execute(update(Book).where(Book.id == data["book_id"]).values(read_position_cp=1))
        assert kwargs["chapter_ids"] == [chapters[1].id]
        return original(*args, **kwargs)

    monkeypatch.setattr(export_api, "render_sample", concurrent_writer)
    response = client.post(f"/api/books/{data['book_id']}/exports/preview", json={
        "visibility_policy": "reread", "chapter_ids": [chapters[1].id],
        "style": {"preset": "color_and_label"},
    })
    assert response.status_code == 200, response.text
    preview = response.json()["data"]
    assert preview["snapshot_id"] == visited[0]
    assert all("原创章节2" in fragment or "原创样张2" in fragment
               for fragment in preview["sample_fragments"])
    assert preview["coverage"]["paragraphs"] > preview["coverage"]["sample_fragments"]


def test_sample_failure_preserves_committed_frozen_snapshot(migrated_client, monkeypatch):
    client = migrated_client
    data = _book(client)

    def fail(*args, **kwargs):
        raise RuntimeError("isolated render failure")

    monkeypatch.setattr(export_api, "render_sample", fail)
    with pytest.raises(RuntimeError, match="isolated render failure"):
        client.post(f"/api/books/{data['book_id']}/exports/preview", json={
            "visibility_policy": "position_safe", "style": {"preset": "color_and_label"},
        })
    with client.app.state.session_factory() as session:
        snapshot = session.scalar(select(ExportSnapshot).where(
            ExportSnapshot.book_id == data["book_id"],
        ))
        assert snapshot is not None
        assert json.loads(snapshot.annotation_projection_json)["visible_horizon_cp"] == 0


def test_concurrent_delete_does_not_return_successful_empty_sample(migrated_client, monkeypatch):
    client = migrated_client
    data = _book(client)
    original = export_api.render_sample

    def remove_book(*args, **kwargs):
        with transaction(client.app.state.session_factory) as other:
            other.execute(delete(Book).where(Book.id == data["book_id"]))
        return original(*args, **kwargs)

    monkeypatch.setattr(export_api, "render_sample", remove_book)
    response = client.post(f"/api/books/{data['book_id']}/exports/preview", json={
        "visibility_policy": "reread", "style": {"preset": "color_and_label"},
    })
    assert response.status_code == 404, response.text
