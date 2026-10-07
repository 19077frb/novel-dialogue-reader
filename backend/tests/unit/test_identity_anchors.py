from types import SimpleNamespace

import pytest

from ndr.domain.enums import ContentNodeType
from ndr.exports.identity_anchors import AnchorBlock, ExportAnchors, ImportAnchors, digest
from ndr.exports.render import RenderedBlock, RenderedChapter, RenderedRun


def index():
    source = "  ab   cd。\n\n第二段。"
    block = RenderedBlock(
        node_type=ContentNodeType.PARAGRAPH, runs=[RenderedRun(source[:10])], start_cp=0, end_cp=10
    )
    second = RenderedBlock(
        node_type=ContentNodeType.PARAGRAPH,
        runs=[RenderedRun(source[12:])],
        start_cp=12,
        end_cp=len(source),
    )
    rendered = SimpleNamespace(chapters=[RenderedChapter("c", 0, "章", [block, second])])
    export = ExportAnchors(rendered, source, {"c": [0, len(source)]})
    target = "ab cd。\n第二段。"
    chapter = SimpleNamespace(id="new", end_cp=len(target))
    nodes = [
        SimpleNamespace(
            chapter_id="new", start_cp=0, end_cp=6, node_type=ContentNodeType.PARAGRAPH
        ),
        SimpleNamespace(
            chapter_id="new", start_cp=7, end_cp=len(target), node_type=ContentNodeType.PARAGRAPH
        ),
    ]
    restore = ImportAnchors([b.descriptor() for b in export.blocks], {0: chapter}, nodes, target)
    return export, restore, source, target


def test_normalization_preserves_word_boundaries_not_just_nonwhite_characters():
    assert digest("ab cd") != digest("abcd")
    assert digest("  ab\n cd ") == digest("ab cd")


def test_proof_and_reveal_round_forward_with_trimmed_and_collapsed_whitespace():
    export, restore, source, target = index()
    assert restore.evidence(export.evidence(2, 4), target) == (0, 2)
    cp = restore.point(export.point(6))
    assert cp == target.index("c")  # Source reveal lies inside whitespace.
    assert restore.point(export.point(11)) == 7  # Between paragraphs, not previous paragraph.
    assert restore.evidence(export.evidence(source.index("第二"), len(source)), target) == (
        7,
        len(target),
    )


@pytest.mark.parametrize("offset", [-1, True, "2", 999])
def test_invalid_offsets_are_not_coerced(offset):
    with pytest.raises(ValueError):
        AnchorBlock(0, 0, 0, 3, "abc").position(offset)


@pytest.mark.parametrize("change", ["order", "text", "duplicate", "missing"])
def test_entire_chapter_requires_exact_paragraph_count_order_and_digest(change):
    export, _, _, target = index()
    descriptors = [b.descriptor() for b in export.blocks]
    if change == "order":
        descriptors.reverse()
    elif change == "text":
        descriptors[0]["digest"] = "0" * 64
    elif change == "duplicate":
        descriptors.append(dict(descriptors[0]))
    else:
        descriptors.pop()
    chapter = SimpleNamespace(id="new", end_cp=len(target))
    nodes = [
        SimpleNamespace(chapter_id="new", start_cp=a, end_cp=b, node_type=ContentNodeType.PARAGRAPH)
        for a, b in ((0, 6), (7, len(target)))
    ]
    restore = ImportAnchors(descriptors, {0: chapter}, nodes, target)
    with pytest.raises(ValueError):
        restore.point({"chapter": 0, "block": 0, "offset": 0})


def test_absent_chapter_is_not_remapped_to_start_of_selected_book():
    export, _, source, _ = index()
    export.chapters[0] = [12, len(source)]
    with pytest.raises(ValueError):
        export.point(5)


@pytest.mark.parametrize(
    "anchor",
    [
        {"chapter": True, "origin": "end"},
        {"chapter": 0, "block": False, "offset": 0},
        {"origin": "start", "unexpected": 1},
    ],
)
def test_invalid_anchor_shapes_do_not_bypass_validated_block(anchor):
    _, restore, _, _ = index()
    with pytest.raises(ValueError):
        restore.point(anchor)
