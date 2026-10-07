"""Verified paragraph-relative positions; never reuse original absolute offsets."""

import hashlib
import re
from bisect import bisect_left
from dataclasses import dataclass


def normalized(text):
    return " ".join(text.split())


def digest(text):
    return hashlib.sha256(normalized(text).encode("utf8")).hexdigest()


def boundaries(text):
    """Map normalized character starts; a reveal inside whitespace rounds forward."""
    starts = []
    words = list(re.finditer(r"\S+", text))
    for i, word in enumerate(words):
        if i:
            starts.append(words[i - 1].end())
        starts.extend(range(word.start(), word.end()))
    return starts, words[-1].end() if words else 0


@dataclass(frozen=True)
class AnchorBlock:
    chapter: int
    ordinal: int
    start: int
    end: int
    text: str

    def descriptor(self):
        return {
            "chapter": self.chapter,
            "block": self.ordinal,
            "digest": digest(self.text),
            "length": len(normalized(self.text)),
        }

    def anchor(self, cp):
        starts, _ = boundaries(self.text)
        return {
            "chapter": self.chapter,
            "block": self.ordinal,
            "offset": bisect_left(starts, cp - self.start),
        }

    def position(self, offset):
        starts, end = boundaries(self.text)
        if type(offset) is not int or not 0 <= offset <= len(starts):
            raise ValueError("Identity text anchor offset is invalid")
        return self.start + (starts[offset] if offset < len(starts) else end)


class ExportAnchors:
    def __init__(self, rendered, text, chapter_bounds):
        self.text = text
        self.chapters = {}
        self.blocks = []
        self.by_chapter = {}
        self.ends = {}
        for index, chapter in enumerate(rendered.chapters):
            bounds = chapter_bounds.get(chapter.chapter_id)
            if bounds is None:
                continue
            self.chapters[index] = bounds
            ordinal = 0
            first_block = len(self.blocks)
            for block in chapter.blocks:
                if block.start_cp is None or block.end_cp is None:
                    continue
                self.blocks.append(
                    AnchorBlock(
                        index,
                        ordinal,
                        block.start_cp,
                        block.end_cp,
                        text[block.start_cp : block.end_cp],
                    )
                )
                ordinal += 1
            self.by_chapter[index] = self.blocks[first_block:]
            self.ends[index] = [b.end for b in self.by_chapter[index]]

    def point(self, cp, *, is_start=False):
        if type(cp) is not int or not 0 <= cp <= len(self.text):
            raise ValueError("Identity original position is invalid")
        if cp == 0:
            return {"origin": "start"}
        chapter = next(
            (
                i
                for i, (a, b) in self.chapters.items()
                if (a <= cp < b if is_start else a < cp <= b)
            ),
            None,
        )
        if chapter is None:
            raise ValueError("Identity position belongs to an omitted chapter")
        blocks = self.by_chapter[chapter]
        if not blocks:
            return {"chapter": chapter, "origin": "end"}
        block = blocks[min(bisect_left(self.ends[chapter], cp), len(blocks) - 1)]
        return block.anchor(cp)

    def evidence(self, a, b):
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(self.text):
            raise ValueError("Identity evidence range is invalid")
        return {
            "start": self.point(a, is_start=True),
            "end": self.point(b),
            "digest": digest(self.text[a:b]),
        }


class ImportAnchors:
    def __init__(self, descriptors, binding, nodes, text):
        if not isinstance(descriptors, list) or len(descriptors) > 100000:
            raise ValueError("Identity text anchors are invalid")
        self.binding = binding
        self.blocks = {}
        actual = {}
        chapter_indices = {chapter.id: index for index, chapter in binding.items()}
        for node in nodes:
            if (
                node.chapter_id not in chapter_indices
                or node.start_cp is None
                or node.end_cp is None
            ):
                continue
            if node.start_cp >= node.end_cp or node.node_type.value in {"image", "separator"}:
                continue
            index = chapter_indices[node.chapter_id]
            ordinal = len(actual.setdefault(index, []))
            actual[index].append(
                AnchorBlock(
                    index, ordinal, node.start_cp, node.end_cp, text[node.start_cp : node.end_cp]
                )
            )
        expected = {}
        for row in descriptors:
            if (
                not isinstance(row, dict)
                or set(row) != {"chapter", "block", "digest", "length"}
                or type(row["chapter"]) is not int
                or row["chapter"] < 0
                or type(row["block"]) is not int
                or row["block"] < 0
                or type(row["length"]) is not int
                or row["length"] < 0
                or not isinstance(row["digest"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", row["digest"])
            ):
                raise ValueError("Identity paragraph descriptor is invalid")
            expected.setdefault(row["chapter"], []).append(row)
        for chapter, rows in expected.items():
            blocks = actual.get(chapter, [])
            if len(rows) == len(blocks) and rows == [b.descriptor() for b in blocks]:
                self.blocks.update({(chapter, b.ordinal): b for b in blocks})
        self.valid_chapters = {
            index for index in binding if not actual.get(index) and not expected.get(index)
        } | {i for i, _ in self.blocks}

    def point(self, point):
        if point == {"origin": "start"}:
            return 0
        if not isinstance(point, dict) or type(point.get("chapter")) is not int:
            raise ValueError("Identity position anchor is invalid")
        chapter = point["chapter"]
        if set(point) == {"chapter", "origin"} and point["origin"] == "end":
            if chapter not in self.valid_chapters:
                raise ValueError("Identity chapter has not been verified")
            return self.binding[chapter].end_cp
        if set(point) != {"chapter", "block", "offset"} or type(point["block"]) is not int:
            raise ValueError("Identity paragraph anchor is invalid")
        block = self.blocks.get((chapter, point["block"]))
        if block is None:
            raise ValueError("Identity original paragraph does not match")
        return block.position(point["offset"])

    def evidence(self, proof, text):
        if not isinstance(proof, dict) or set(proof) != {"start", "end", "digest"}:
            raise ValueError("Identity proof anchor is invalid")
        a, b = self.point(proof["start"]), self.point(proof["end"])
        if a >= b or digest(text[a:b]) != proof["digest"]:
            raise ValueError("Identity evidence text does not match")
        return a, b
