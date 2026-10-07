"""EPUB 标注清单（annotations.json）。

- 导出时把冻结投影里的**可见标注**写成机器可读清单，一起打进 EPUB；
- 导入本工具生成的 EPUB 时，用清单把标注恢复成数据库数据（场景/说话人分组/标注）；
- 对白标注按「章节 + 对白原文（含出现次序）」重新定位，不复用旧绝对位置；
- 可选人物账本使用核验后的段落锚点恢复证据、资料来源及揭示时点。
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from contextlib import suppress
from typing import Any

from ..domain.enums import ExportStylePreset
from .identity_anchors import ExportAnchors
from .identity_manifest import build_identity_manifest
from .render import RenderedBook, usable_items

ANNOTATIONS_MANIFEST_VERSION = "ndr-annotations-1"
ANNOTATIONS_ENTRY = "OEBPS/annotations.json"

# 与 EPUB 解析侧一致的空白折叠规则：两边正文对空白的处理不同（EPUB 会折叠缩进），
# 匹配前先归一化，避免「内容相同但空白不同」导致恢复失败。
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_match(text: str) -> str:
    """匹配用的对白原文：折叠所有空白，兼容导出/回导两侧的排版差异。"""

    return _WHITESPACE_RE.sub(" ", text).strip()


def build_annotations_manifest(
    *,
    projection_payload: dict[str, Any],
    rendered: RenderedBook,
    canonical_text: str,
    style: ExportStylePreset,
    processed_chapter_indices: list[int] | None = None,
    manual_processing_status: dict[int, bool] | None = None,
) -> dict[str, Any]:
    """从冻结投影 + 渲染结果生成标注清单。

    章节下标与 EPUB 渲染顺序一致（``rendered.chapters`` 的位置），导入侧按
    spine 顺序重建章节，两边可以按位置对齐。即使没有标注也保留空清单，
    以便回导时识别并移除导出器添加的标题和显示标签。
    """

    items = usable_items(projection_payload)
    ledger = build_identity_manifest(projection_payload, rendered, canonical_text)
    ledger_ids = {row["identity"] for row in ledger["characters"]} if ledger else set()
    anchors = ExportAnchors(rendered, canonical_text, projection_payload.get("chapter_bounds", {}))
    speakers: dict[str, dict[str, Any]] = {}
    entries: list[tuple[int, dict[str, Any]]] = []
    seen_quotes: set[str] = set()
    for chapter_index, chapter in enumerate(rendered.chapters):
        for block in chapter.blocks:
            for run in block.runs:
                if not run.quote_id or run.quote_id in seen_quotes:
                    continue
                item = items.get(run.quote_id)
                if item is None:
                    continue
                label = str(item.get("label") or "")
                color_index = item.get("color_index")
                if not label or color_index is None:
                    continue
                seen_quotes.add(run.quote_id)
                history = projection_payload.get("speaker_histories", {}).get(
                    item.get("speaker_group_id"), [],
                )
                character_identity = projection_payload.get("speaker_characters", {}).get(
                    item.get("speaker_group_id"),
                )
                key = (f"g:{item['speaker_group_id']}"
                       if history or character_identity in ledger_ids else f"c{int(color_index)}")
                speakers.setdefault(
                    key,
                    {
                        "key": key,
                        "color_index": int(color_index),
                        "label": label,
                        "description": str(item.get("speaker_description") or ""),
                    },
                )
                if character_identity in ledger_ids:
                    speakers[key]["character_identity"] = character_identity
                if history:
                    bounds = projection_payload.get("chapter_bounds", {})
                    converted = []
                    for record in history:
                        source_id = next((key for key, (start, end) in bounds.items()
                                          if start < record["cp"] <= end), None)
                        index = (-1 if record["cp"] == 0 else next((
                            i for i, section in enumerate(rendered.chapters)
                            if section.chapter_id == source_id
                        ), None))
                        converted.append(
                            {key: value for key, value in record.items() if key != "cp"}
                            | {"after_chapter": index},
                        )
                    speakers[key]["history"] = converted
                    speakers[key]["identity"] = projection_payload.get(
                        "speaker_identities", {},
                    ).get(item.get("speaker_group_id"), history[-1]["identity"])
                    if ledger is not None:
                        # Missing reveals retain the conservative legacy mapping.
                        with suppress(ValueError):
                            speakers[key]["anchored_history"] = [
                                dict(record, cp=anchors.point(record["cp"])) for record in history
                            ]
                start = int(item["start_cp"])
                end = int(item["end_cp"])
                quote_text = (
                    canonical_text[start:end] if 0 <= start < end <= len(canonical_text) else ""
                )
                if not quote_text:
                    continue
                entry = {
                    "chapter_index": chapter_index,
                    "quote_text": normalize_for_match(quote_text),
                    "speaker": key,
                    "kind": str(item.get("kind") or "other"),
                    "assignment": item.get("assignment"),
                    "basis": item.get("basis"),
                    "status": str(item.get("status") or "ACCEPTED"),
                    "source": str(item.get("source") or "MODEL"),
                    "stale": bool(item.get("stale")),
                }
                depth = projection_payload.get("quote_depths", {}).get(run.quote_id, 0)
                if depth:
                    entry["nesting_depth"] = depth
                entries.append((start, entry))
    entries.sort(key=lambda pair: pair[0])
    manifest = {
        "manifest_version": ANNOTATIONS_MANIFEST_VERSION,
        "exporter_style": style.value,
        "book_title": rendered.title,
        "chapter_titles": [chapter.title for chapter in rendered.chapters],
        "processed_chapter_indices": sorted(set(processed_chapter_indices or [])),
        "manual_processing_status": {str(index): status for index, status in
                                     (manual_processing_status or {}).items()},
        "speakers": [speakers[key] for key in sorted(speakers)],
        "annotations": [entry for _, entry in entries],
    }
    if ledger is not None:
        manifest["identity_ledger"] = ledger
    return manifest


def parse_annotations_manifest(raw: bytes) -> dict[str, Any] | None:
    """从 EPUB 字节流里读取标注清单；不是本工具导出或清单损坏时返回 ``None``。"""

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if ANNOTATIONS_ENTRY not in archive.namelist():
                return None
            payload = json.loads(archive.read(ANNOTATIONS_ENTRY).decode("utf-8"))
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, KeyError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("manifest_version") != ANNOTATIONS_MANIFEST_VERSION:
        return None
    if not isinstance(payload.get("annotations"), list):
        return None
    if not isinstance(payload.get("processed_chapter_indices", []), list):
        return None
    return payload
