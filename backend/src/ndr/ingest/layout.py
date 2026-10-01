"""Read-only CSS hints for converting decorative EPUB text into reading paragraphs.

This is not a browser: no CSS URLs, imports, fonts, scripts or generated content are
loaded/executed. Only float and writing-mode hints guide tightly bounded grouping.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from xml.etree import ElementTree as ET

import cssselect2
import tinycss2

_PROPERTIES = {"float", "writing-mode", "-epub-writing-mode", "-webkit-writing-mode"}
_INLINE_TAGS = {"span", "em", "strong", "b", "i", "u", "small", "ruby", "rb",
                "rt", "rp", "br", "a", "sup", "sub", "p"}
_MAX_STYLESHEET_BYTES = 128 * 1024
_MAX_DOCUMENT_CSS_BYTES = 512 * 1024
_MAX_SELECTORS = 2048


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _base_text(element: ET.Element) -> str:
    if _local(element.tag) in {"rt", "rp"}:
        return ""
    return (element.text or "") + "".join(
        _base_text(child) + (child.tail or "") for child in element
    )


def _declarations(content: Any) -> list[tuple[str, str, bool]]:
    return [
        ("float" if item.lower_name == "float" else "writing-mode",
         tinycss2.serialize(item.value).strip().lower(), item.important)
        for item in tinycss2.parse_declaration_list(
            content, skip_comments=True, skip_whitespace=True,
        )
        if item.type == "declaration" and item.lower_name in _PROPERTIES
    ]


class LayoutNormalizer:
    """Cache compiled selectors across a book; never modify source archive bytes."""

    def __init__(self) -> None:
        self._cache: dict[bytes, list[tuple[Any, list[tuple[str, str, bool]]]]] = {}

    def _rules(self, data: bytes) -> list[tuple[Any, list[tuple[str, str, bool]]]]:
        if len(data) > _MAX_STYLESHEET_BYTES:
            return []
        if data in self._cache:
            return self._cache[data]
        rules, _encoding = tinycss2.parse_stylesheet_bytes(
            data, skip_comments=True, skip_whitespace=True,
        )
        result = []
        for rule in rules:
            # In particular, do not follow @import or evaluate conditional media.
            if rule.type != "qualified-rule":
                continue
            declarations = _declarations(rule.content)
            if not declarations:
                continue
            try:
                selectors = cssselect2.compile_selector_list(rule.prelude)
            except (cssselect2.SelectorError, RecursionError):
                continue
            for selector in selectors:
                if selector.pseudo_element is None and not selector.never_matches:
                    result.append((selector, declarations))
                if len(result) >= _MAX_SELECTORS:
                    break
            if len(result) >= _MAX_SELECTORS:
                break
        self._cache[data] = result
        return result

    def normalize(self, root: ET.Element, load_css: Callable[[str], bytes | None]) -> int:
        candidates: dict[ET.Element, list[ET.Element]] = {}
        for element in root.iter():
            if _local(element.tag) not in {"div", "section", "aside", "article"}:
                continue
            children = list(element)
            paragraphs = [child for child in children if _local(child.tag) == "p"]
            if not 3 <= len(paragraphs) <= 160 or (element.text or "").strip():
                continue
            if any(_local(child.tag) not in {"p", "br"} or (child.tail or "").strip()
                   for child in children):
                continue
            if any(_local(node.tag) not in _INLINE_TAGS for p in paragraphs for node in p.iter()):
                continue
            lengths = [len(_base_text(p).strip()) for p in paragraphs]
            if min(lengths) > 0 and max(lengths) <= 12 and sum(lengths) <= 160:
                candidates[element] = paragraphs
        if not candidates:
            return 0

        matcher = cssselect2.Matcher()
        size = 0
        selectors_used = 0
        for node in root.iter():
            tag = _local(node.tag)
            data = None
            if tag == "style":
                data = (node.text or "").encode("utf-8")
            elif tag == "link" and "stylesheet" in (node.get("rel") or "").lower().split():
                data = load_css(node.get("href") or "")
            if not data or size + len(data) > _MAX_DOCUMENT_CSS_BYTES:
                continue
            size += len(data)
            for selector, declarations in self._rules(data):
                if selectors_used >= _MAX_SELECTORS:
                    break
                matcher.add_selector(selector, declarations)
                selectors_used += 1

        styles: dict[ET.Element, dict[str, str]] = {}
        for wrapper in cssselect2.ElementWrapper.from_html_root(root).iter_subtree():
            element = wrapper.etree_element
            values: dict[str, tuple[tuple[Any, ...], str]] = {}
            for specificity, order, _pseudo, declarations in matcher.match(wrapper):
                for index, (name, value, important) in enumerate(declarations):
                    priority = (important, 0, specificity, order, index)
                    if name not in values or priority >= values[name][0]:
                        values[name] = (priority, value)
            inline = element.get("style") or ""
            if inline and len(inline) <= _MAX_STYLESHEET_BYTES:
                for index, (name, value, important) in enumerate(_declarations(inline)):
                    priority = (important, 1, (0, 0, 0), 0, index)
                    if name not in values or priority >= values[name][0]:
                        values[name] = (priority, value)
            resolved = {name: pair[1] for name, pair in values.items()}
            if wrapper.parent is not None:
                inherited = styles[wrapper.parent.etree_element]
                if ("writing-mode" not in resolved or resolved["writing-mode"] == "inherit") and (
                    "writing-mode" in inherited
                ):
                    resolved["writing-mode"] = inherited["writing-mode"]
            styles[element] = resolved

        def floated(element: ET.Element) -> bool:
            return styles[element].get("float") in {"right", "left", "inline-start", "inline-end"}

        # Several one-character floated columns are a strong decorative-page hint.
        decorative = sum(
            floated(element) and all(len(_base_text(p).strip()) == 1 for p in ps)
            for element, ps in candidates.items()
        ) >= 2
        count = 0
        normalized: set[ET.Element] = set()
        for element, paragraphs in candidates.items():
            vertical = styles[element].get("writing-mode") in {
                "vertical-rl", "vertical-lr", "tb-rl", "tb-lr",
            }
            single_letters = all(len(_base_text(p).strip()) == 1 for p in paragraphs)
            if (vertical and single_letters) or (
                floated(element) and (single_letters or decorative)
            ):
                for p in paragraphs:
                    # Keep descendants (including ruby) and text untouched; suppress
                    # only the artificial paragraph boundary in our restricted tree.
                    p.tag = "span"
                normalized.add(element)
                count += 1
        # A subtitle may be several adjacent decorative columns. Unite only a
        # container made entirely of already-normalized columns, not arbitrary divs.
        for element in reversed(list(root.iter())):
            children = list(element)
            if len(children) >= 2 and all(
                child in normalized and child in candidates
                and all(len(_base_text(p).strip()) == 1 for p in candidates[child])
                for child in children
            ) and (
                not (element.text or "").strip()
                and all(not (child.tail or "").strip() for child in children)
                and sum(len(_base_text(child).strip()) for child in children) <= 160
            ):
                for child in children:
                    child.tag = "span"
                normalized.add(element)
        return count
