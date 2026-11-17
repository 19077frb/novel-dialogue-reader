"""Cover metadata selects registered images without changing document identity."""

from __future__ import annotations

import io
import zipfile
import zlib

import pytest

from fixtures.epub_covers import COVER_IMAGE, build_cover_epub
from ndr.ingest.epub import EPUB_PARSER_VERSION, _Archive, parse_epub


def test_epub3_cover_precedes_epub2_metadata_without_changing_body_or_resources():
    baseline = parse_epub(build_cover_epub(cover_image_id=None))
    parsed = parse_epub(build_cover_epub(legacy_cover_id="res1"))
    assert parsed.cover_resource_id == "r0002"
    assert baseline.cover_resource_id is None
    assert parsed.canonical_sha256 == baseline.canonical_sha256
    assert parsed.canonical_text == baseline.canonical_text
    assert parsed.nodes == baseline.nodes
    assert parsed.chapters == baseline.chapters
    assert parsed.mappings == baseline.mappings
    assert parsed.resources == baseline.resources
    assert parsed.parser_version == baseline.parser_version == EPUB_PARSER_VERSION == "epub-8"


@pytest.mark.parametrize("cover_image_id", [None, "missing", "res3"])
def test_valid_legacy_cover_is_used_when_epub3_candidate_is_unavailable(cover_image_id):
    parsed = parse_epub(build_cover_epub(cover_image_id=cover_image_id, legacy_cover_id="res2"))
    assert parsed.cover_resource_id == "r0002"


@pytest.mark.parametrize("body", [
    '<p><img src="../images/cover.png"/></p>',
    '<svg xmlns="http://www.w3.org/2000/svg"><image href="../images/cover.png"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
    '<image xlink:href="../images/cover.png"/></svg>',
    '<p>&nbsp;<img src="https://example.org/remote.png"/><img src="../images/cover.png"/></p>',
])
def test_guide_cover_page_recognizes_img_and_svg_image(body):
    parsed = parse_epub(build_cover_epub(
        cover_image_id=None, guide_href="text/cover.xhtml", cover_body=body,
    ))
    assert parsed.cover_resource_id == "r0002"
    assert len(parsed.chapters) == 1
    assert len(parsed.resources) == 3


def test_guide_fragment_selects_target_image_and_legacy_precedes_guide():
    kwargs = dict(
        cover_image_id=None, guide_href="text/cover.xhtml#%E5%B0%81%E9%9D%A2",
        cover_body='<img src="../images/illustration.png"/>'
                   '<div id="封面"><img src="../images/cover.png"/></div>',
    )
    assert parse_epub(build_cover_epub(**kwargs)).cover_resource_id == "r0002"
    assert parse_epub(build_cover_epub(**kwargs, legacy_cover_id="res1")).cover_resource_id == "r0001"


def test_guide_can_reference_registered_image_directly():
    parsed = parse_epub(build_cover_epub(cover_image_id=None, guide_href="images/cover.png"))
    assert parsed.cover_resource_id == "r0002"


@pytest.mark.parametrize("guide,body", [
    (None, None),
    ("https://example.org/cover.xhtml", None),
    ("http://[", None),
    ("../../../cover.xhtml", None),
    ("text/missing.xhtml", None),
    ("text/cover.xhtml#missing", '<img src="../images/cover.png"/>'),
    ("text/cover.xhtml", '<p><img src="../images/missing.png"/></p>'),
    ("text/cover.xhtml", '<p><img src="../../../outside.png"/></p>'),
    ("text/cover.xhtml", '<p><img src="https://example.org/cover.png"/></p>'),
    ("text/cover.xhtml", '<p><img src="http://["/></p>'),
    ("text/cover.xhtml", '<p><img src="../styles/book.css"/></p>'),
    ("text/cover.xhtml", '<p><img'),
])
def test_missing_or_invalid_cover_keeps_readable_book_without_guessing_first_image(guide, body):
    parsed = parse_epub(build_cover_epub(
        cover_image_id=None, legacy_cover_id="missing", guide_href=guide, cover_body=body,
    ))
    assert parsed.cover_resource_id is None
    assert "「你好。」" in parsed.canonical_text
    assert len(parsed.chapters) == 1


def test_declared_but_missing_cover_resource_is_ignored():
    original = build_cover_epub()
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(buffer, "w") as target:
        for info in source.infolist():
            if source.read(info) != COVER_IMAGE:
                target.writestr(info, source.read(info))
    parsed = parse_epub(buffer.getvalue())
    assert parsed.cover_resource_id is None
    assert "「你好。」" in parsed.canonical_text


@pytest.mark.parametrize("error", [
    zipfile.BadZipFile("Bad CRC-32 for cover page"),
    NotImplementedError("Unsupported compression method"),
    zlib.error("Invalid compressed cover page"),
])
def test_optional_cover_page_read_failure_keeps_importable_body(monkeypatch, error):
    original = _Archive.read

    def read(archive, name):
        if name == "OEBPS/text/cover.xhtml":
            raise error
        return original(archive, name)

    monkeypatch.setattr(_Archive, "read", read)
    parsed = parse_epub(build_cover_epub(
        cover_image_id=None, guide_href="text/cover.xhtml",
        cover_body='<img src="../images/cover.png"/>',
    ))
    assert parsed.cover_resource_id is None
    assert "「你好。」" in parsed.canonical_text


def test_unknown_cover_page_encoding_keeps_importable_body(monkeypatch):
    original = _Archive.read

    def read(archive, name):
        raw = original(archive, name)
        return raw.replace(b'encoding="utf-8"', b'encoding="unknown-codec"') if (
            name == "OEBPS/text/cover.xhtml"
        ) else raw

    monkeypatch.setattr(_Archive, "read", read)
    parsed = parse_epub(build_cover_epub(
        cover_image_id=None, guide_href="text/cover.xhtml",
        cover_body='<img src="../images/cover.png"/>',
    ))
    assert parsed.cover_resource_id is None
    assert "「你好。」" in parsed.canonical_text
