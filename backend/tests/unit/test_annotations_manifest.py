"""单元测试：EPUB 标注清单的构建与解析。"""

from __future__ import annotations

import io
import json
import zipfile

from ndr.domain.enums import ContentNodeType, ExportStylePreset
from ndr.exports.annotations import (
    ANNOTATIONS_ENTRY,
    ANNOTATIONS_MANIFEST_VERSION,
    build_annotations_manifest,
    normalize_for_match,
    parse_annotations_manifest,
)
from ndr.exports.render import RenderedBlock, RenderedBook, RenderedChapter, RenderedRun

CANONICAL = (
    "第一章 雨夜\n"
    "「雨停了。」少女合上伞。\n"
    "「嗯。」她说。\n"
)


def _book() -> RenderedBook:
    return RenderedBook(
        book_id="b1",
        title="测试书",
        language="zh",
        style=ExportStylePreset.COLOR_AND_LABEL,
        chapters=[
            RenderedChapter(
                chapter_id="ch1",
                ordinal=0,
                title="第一章 雨夜",
                blocks=[
                    RenderedBlock(
                        node_type=ContentNodeType.PARAGRAPH,
                        runs=[
                            RenderedRun(
                                text="「雨停了。」",
                                color_class="speaker-0",
                                label="〔S1〕",
                                quote_id="q1",
                            ),
                            RenderedRun(text="少女合上伞。"),
                        ],
                    ),
                    RenderedBlock(
                        node_type=ContentNodeType.PARAGRAPH,
                        runs=[
                            RenderedRun(
                                text="「嗯。」",
                                color_class="speaker-1",
                                label="〔S2〕",
                                quote_id="q2",
                            )
                        ],
                    ),
                ],
            )
        ],
        legend=[],
        warnings=[],
        visible_horizon_cp=None,
    )


def _payload() -> dict:
    return {
        "items": [
            {
                "quote_id": "q1",
                "start_cp": CANONICAL.index("「雨停了。」"),
                "end_cp": CANONICAL.index("「雨停了。」") + len("「雨停了。」"),
                "color_index": 0,
                "label": "S1",
                "speaker_description": "少女",
                "kind": "speech",
                "assignment": "EXISTING",
                "basis": "DIRECT",
                "status": "ACCEPTED",
                "source": "MODEL",
                "stale": False,
                "withheld": False,
            },
            {
                "quote_id": "q2",
                "start_cp": CANONICAL.index("「嗯。」"),
                "end_cp": CANONICAL.index("「嗯。」") + len("「嗯。」"),
                "color_index": 1,
                "label": "S2",
                "speaker_description": "",
                "kind": "speech",
                "assignment": "EXISTING",
                "basis": "DIRECT",
                "status": "ACCEPTED",
                "source": "MODEL",
                "stale": False,
                "withheld": False,
            },
        ]
    }


def test_build_manifest_contains_speakers_and_entries() -> None:
    manifest = build_annotations_manifest(
        projection_payload=_payload(),
        rendered=_book(),
        canonical_text=CANONICAL,
        style=ExportStylePreset.COLOR_AND_LABEL,
    )
    assert manifest is not None
    assert manifest["manifest_version"] == ANNOTATIONS_MANIFEST_VERSION
    assert manifest["chapter_titles"] == ["第一章 雨夜"]
    assert manifest["speakers"] == [
        {"key": "c0", "color_index": 0, "label": "S1", "description": "少女"},
        {"key": "c1", "color_index": 1, "label": "S2", "description": ""},
    ]
    assert manifest["annotations"] == [
        {
            "chapter_index": 0,
            "quote_text": "「雨停了。」",
            "speaker": "c0",
            "kind": "speech",
            "assignment": "EXISTING",
            "basis": "DIRECT",
            "status": "ACCEPTED",
            "source": "MODEL",
            "stale": False,
        },
        {
            "chapter_index": 0,
            "quote_text": "「嗯。」",
            "speaker": "c1",
            "kind": "speech",
            "assignment": "EXISTING",
            "basis": "DIRECT",
            "status": "ACCEPTED",
            "source": "MODEL",
            "stale": False,
        },
    ]


def test_build_manifest_keeps_empty_marker_without_annotations() -> None:
    payload = _payload()
    for item in payload["items"]:
        item["color_index"] = None
        item["label"] = None
    manifest = build_annotations_manifest(
        projection_payload=payload,
        rendered=_book(),
        canonical_text=CANONICAL,
        style=ExportStylePreset.COLOR_AND_LABEL,
    )
    assert manifest["annotations"] == []
    assert manifest["speakers"] == []


def test_normalize_for_match_collapses_whitespace() -> None:
    assert normalize_for_match("「跨块\n 的同一句。」") == "「跨块 的同一句。」"


def _epub_with_manifest(payload: object) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            ANNOTATIONS_ENTRY,
            json.dumps(payload, ensure_ascii=False) if not isinstance(payload, bytes) else payload,
        )
    return buffer.getvalue()


def test_parse_manifest_accepts_valid_payload_and_rejects_others() -> None:
    manifest = build_annotations_manifest(
        projection_payload=_payload(),
        rendered=_book(),
        canonical_text=CANONICAL,
        style=ExportStylePreset.COLOR_AND_LABEL,
    )
    assert manifest is not None
    parsed = parse_annotations_manifest(_epub_with_manifest(manifest))
    assert parsed == manifest

    # 普通 EPUB（无清单）→ None
    assert parse_annotations_manifest(b"not a zip") is None
    assert parse_annotations_manifest(_epub_with_manifest({"foo": 1})) is None

    wrong_version = dict(manifest)
    wrong_version["manifest_version"] = "other"
    assert parse_annotations_manifest(_epub_with_manifest(wrong_version)) is None

    broken = dict(manifest)
    broken["annotations"] = None
    assert parse_annotations_manifest(_epub_with_manifest(broken)) is None
