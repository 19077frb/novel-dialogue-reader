"""金标准文件的导入/导出与范围检查（DEVELOPMENT.md 7.3 / T05）。

职责：

- ``load_gold_standard``：读取 JSON。
- ``validate_schema``：用 ``evaluation/schemas/gold-standard.schema.json`` 做结构校验。
- ``check_references``：引用完整性、范围合法性、resolvable 与分组的一致性等**跨字段**检查
  （JSON Schema 表达不了的部分）。
- ``check_against_scanner``：金标准对白是否被候选扫描器找到（覆盖率，T16 会用）。
- ``build_template``：用扫描器输出生成可填写的金标准骨架（人工标注的起点）。

全部检查只读文件，不触碰数据库、不调用模型。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import REPO_ROOT
from ..ingest.epub import parse_epub
from ..ingest.txt import parse_txt
from ..quotes.delimiters import CLOSING_TO_PAIR, OPENING_TO_PAIR
from ..quotes.gaps import build_gaps
from ..quotes.scanner import SCANNER_VERSION, ScanLimits, scan_quotes

GOLD_SCHEMA_VERSION = "1.0"
DEFAULT_SCHEMA_PATH = REPO_ROOT / "evaluation" / "schemas" / "gold-standard.schema.json"

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"


@dataclass(frozen=True)
class GoldIssue:
    level: str
    code: str
    message: str
    target_id: str | None = None

    @property
    def is_error(self) -> bool:
        return self.level == LEVEL_ERROR

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"level": self.level, "code": self.code, "message": self.message}
        if self.target_id:
            payload["target_id"] = self.target_id
        return payload


def load_gold_standard(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("金标准文件必须是 JSON 对象")
    return payload


def validate_schema(
    gold: dict[str, Any], schema_path: str | Path | None = None
) -> list[GoldIssue]:
    """按 JSON Schema 校验结构；缺少 jsonschema 依赖时返回一条明确错误。"""

    try:
        import jsonschema
    except ImportError:  # pragma: no cover - 依赖缺失时的明确提示
        return [
            GoldIssue(
                LEVEL_ERROR,
                "jsonschema_missing",
                "未安装 jsonschema，无法做结构校验（pip/uv 安装 dev 依赖后重试）。",
            )
        ]

    schema = json.loads(Path(schema_path or DEFAULT_SCHEMA_PATH).read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    issues: list[GoldIssue] = []
    for error in sorted(validator.iter_errors(gold), key=lambda item: list(item.path)):
        location = "/".join(str(part) for part in error.path) or "(根)"
        issues.append(
            GoldIssue(
                LEVEL_ERROR,
                "schema_violation",
                f"{location}: {error.message}",
                target_id=str(error.path[-1]) if error.path else None,
            )
        )
    return issues


def check_references(
    gold: dict[str, Any], *, canonical_text: str | None = None
) -> list[GoldIssue]:
    """跨字段一致性检查（Schema 无法表达的部分）。"""

    issues: list[GoldIssue] = []
    book = gold.get("book", {})
    length_cp = book.get("canonical_length_cp")
    scenes = gold.get("scenes", [])
    quotes = gold.get("quotes", [])
    gaps = gold.get("gaps", [])

    scene_ids: set[str] = set()
    for scene in scenes:
        scene_id = str(scene.get("scene_id", ""))
        if scene_id in scene_ids:
            issues.append(
                GoldIssue(LEVEL_ERROR, "duplicate_scene_id", f"场景 {scene_id} 重复", scene_id)
            )
        scene_ids.add(scene_id)
        if length_cp is not None and not (
            0 <= scene.get("start_cp", -1) < scene.get("end_cp", -1) <= length_cp
        ):
            issues.append(
                GoldIssue(
                    LEVEL_ERROR,
                    "scene_range_out_of_bounds",
                    f"场景 {scene_id} 的范围超出正文长度 {length_cp}",
                    scene_id,
                )
            )

    labels_by_scene = {
        str(scene.get("scene_id")): {
            str(participant.get("label")) for participant in scene.get("participants", [])
        }
        for scene in scenes
    }

    quote_ids: set[str] = set()
    quote_ranges: dict[str, tuple[int, int]] = {}
    for quote in quotes:
        quote_id = str(quote.get("quote_id", ""))
        if quote_id in quote_ids:
            issues.append(
                GoldIssue(LEVEL_ERROR, "duplicate_quote_id", f"对白 {quote_id} 重复", quote_id)
            )
        quote_ids.add(quote_id)
        start, end = quote.get("start_cp", -1), quote.get("end_cp", -1)
        if length_cp is not None and not (0 <= start < end <= length_cp):
            issues.append(
                GoldIssue(
                    LEVEL_ERROR,
                    "quote_range_out_of_bounds",
                    f"对白 {quote_id} 的范围 [{start}, {end}) 超出正文长度 {length_cp}",
                    quote_id,
                )
            )
        quote_ranges[quote_id] = (start, end)

        scene_id = str(quote.get("scene_id", ""))
        if scene_id not in scene_ids:
            issues.append(
                GoldIssue(
                    LEVEL_ERROR,
                    "unknown_scene",
                    f"对白 {quote_id} 引用了不存在的场景 {scene_id}",
                    quote_id,
                )
            )
        group_id = quote.get("group_id")
        if quote.get("resolvable") is True and group_id is None:
            issues.append(
                GoldIssue(
                    LEVEL_ERROR,
                    "resolvable_without_group",
                    f"对白 {quote_id} 标记为可判定但没有 group_id",
                    quote_id,
                )
            )
        if quote.get("resolvable") is False and group_id is not None:
            issues.append(
                GoldIssue(
                    LEVEL_ERROR,
                    "unresolvable_with_group",
                    f"对白 {quote_id} 标记为不可判定却给了 group_id {group_id}",
                    quote_id,
                )
            )
        if group_id is not None and str(group_id) not in labels_by_scene.get(scene_id, set()):
            issues.append(
                GoldIssue(
                    LEVEL_ERROR,
                    "group_not_in_scene",
                    f"对白 {quote_id} 的分组 {group_id} 不在场景 {scene_id} 的参与者里",
                    quote_id,
                )
            )
        for fragment in quote.get("fragments", []):
            fragment_start, fragment_end = fragment.get("start_cp", -1), fragment.get("end_cp", -1)
            if not (start <= fragment_start < fragment_end <= end):
                issues.append(
                    GoldIssue(
                        LEVEL_ERROR,
                        "fragment_out_of_quote",
                        f"对白 {quote_id} 的片段 [{fragment_start}, {fragment_end}) 超出对白范围",
                        quote_id,
                    )
                )
        for reference in quote.get("evidence_refs", []):
            ref_start, ref_end = reference.get("start_cp", -1), reference.get("end_cp", -1)
            if length_cp is not None and not (0 <= ref_start < ref_end <= length_cp):
                issues.append(
                    GoldIssue(
                        LEVEL_ERROR,
                        "evidence_range_out_of_bounds",
                        f"对白 {quote_id} 的证据范围 [{ref_start}, {ref_end}) 超出正文长度",
                        quote_id,
                    )
                )
            if reference.get("visible_from_cp", 0) < ref_start:
                issues.append(
                    GoldIssue(
                        LEVEL_WARNING,
                        "visible_before_evidence",
                        f"对白 {quote_id} 的证据可见时点早于证据本身",
                        quote_id,
                    )
                )

    gap_ids: set[str] = set()
    for gap in gaps:
        gap_id = str(gap.get("gap_id", ""))
        if gap_id in gap_ids:
            issues.append(GoldIssue(LEVEL_ERROR, "duplicate_gap_id", f"Gap {gap_id} 重复", gap_id))
        gap_ids.add(gap_id)
        for side in ("left_quote_id", "right_quote_id"):
            value = gap.get(side)
            if value is not None and str(value) not in quote_ids:
                issues.append(
                    GoldIssue(
                        LEVEL_ERROR,
                        "unknown_quote_in_gap",
                        f"Gap {gap_id} 的 {side} 不存在",
                        gap_id,
                    )
                )
        left = gap.get("left_quote_id")
        right = gap.get("right_quote_id")
        if left in quote_ranges and right in quote_ranges:
            expected_start = quote_ranges[str(left)][1]
            expected_end = quote_ranges[str(right)][0]
            for keep in gap.get("must_keep", []):
                keep_start, keep_end = keep.get("start_cp", -1), keep.get("end_cp", -1)
                if not (expected_start <= keep_start < keep_end <= expected_end):
                    issues.append(
                        GoldIssue(
                            LEVEL_WARNING,
                            "must_keep_outside_gap",
                            f"Gap {gap_id} 的 must_keep [{keep_start}, {keep_end})"
                            " 不在该 Gap 范围内",
                            gap_id,
                        )
                    )

    if canonical_text is not None:
        for quote in quotes:
            quote_id = str(quote.get("quote_id", ""))
            start, end = quote.get("start_cp", -1), quote.get("end_cp", -1)
            if not (0 <= start < end <= len(canonical_text)):
                continue
            fragment = canonical_text[start:end]
            if quote.get("kind") not in {"speech", "thought", "quotation"}:
                continue
            if fragment[0] not in OPENING_TO_PAIR or fragment[-1] not in CLOSING_TO_PAIR:
                    issues.append(
                        GoldIssue(
                            LEVEL_WARNING,
                            "quote_not_delimited",
                            f"对白 {quote_id} 的文本没有以引号包裹：{fragment[:12]}…",
                            quote_id,
                        )
                    )
    return issues


def check_against_scanner(
    gold: dict[str, Any],
    canonical_text: str,
    *,
    scanner_version: str = SCANNER_VERSION,
    limits: ScanLimits | None = None,
) -> tuple[list[GoldIssue], dict[str, int]]:
    """金标准对白是否被候选扫描器覆盖（用于评测候选提取的召回）。"""

    result = scan_quotes(
        canonical_text,
        book_version_id=str(gold.get("book", {}).get("book_id", "gold")),
        scanner_version=scanner_version,
        limits=limits,
    )
    candidates = [(quote.start_cp, quote.end_cp) for quote in result.quotes]

    matched = inside = missing = 0
    issues: list[GoldIssue] = []
    for quote in gold.get("quotes", []):
        start, end = quote.get("start_cp", -1), quote.get("end_cp", -1)
        if any(candidate == (start, end) for candidate in candidates):
            matched += 1
        elif any(candidate[0] <= start and end <= candidate[1] for candidate in candidates):
            inside += 1
            issues.append(
                GoldIssue(
                    LEVEL_WARNING,
                    "quote_inside_larger_candidate",
                    f"对白 {quote.get('quote_id')} 落在更大的候选范围内（边界不一致）",
                    str(quote.get("quote_id")),
                )
            )
        else:
            missing += 1
            issues.append(
                GoldIssue(
                    LEVEL_WARNING,
                    "quote_missing_from_candidates",
                    f"对白 {quote.get('quote_id')} 没有被扫描器提取为候选",
                    str(quote.get("quote_id")),
                )
            )
    summary = {
        "gold_quotes": len(gold.get("quotes", [])),
        "matched": matched,
        "inside_larger_candidate": inside,
        "missing": missing,
        "scanner_candidates": len(result.quotes),
    }
    return issues, summary


def summarize(gold: dict[str, Any]) -> dict[str, Any]:
    quotes = gold.get("quotes", [])
    return {
        "schema_version": gold.get("schema_version"),
        "book_id": gold.get("book", {}).get("book_id"),
        "work_id": gold.get("book", {}).get("work_id"),
        "scenes": len(gold.get("scenes", [])),
        "quotes": len(quotes),
        "resolvable_quotes": sum(1 for quote in quotes if quote.get("resolvable") is True),
        "unresolvable_quotes": sum(1 for quote in quotes if quote.get("resolvable") is False),
        "gaps": len(gold.get("gaps", [])),
        "kinds": {
            kind: sum(1 for quote in quotes if quote.get("kind") == kind)
            for kind in sorted({str(quote.get("kind")) for quote in quotes})
        },
    }


def read_canonical_text(path: str | Path, *, encoding: str | None = None) -> tuple[str, str]:
    """读取用于标注的纯文本；返回 ``(canonical_text, encoding)``。"""

    raw = Path(path).read_bytes()
    suffix = Path(path).suffix.lower()
    if suffix == ".epub":
        parsed = parse_epub(raw)
        return parsed.canonical_text, "epub"
    parsed_txt = parse_txt(raw, encoding=encoding)
    return parsed_txt.canonical_text, parsed_txt.encoding or "utf-8"


def build_template(
    canonical_text: str,
    *,
    book_id: str,
    work_id: str | None = None,
    title: str = "待标注样例",
    format: str = "TXT",
    language: str = "zh",
    encoding: str | None = None,
    annotator_id: str = "unassigned",
    annotated_at: str = "1970-01-01T00:00:00+00:00",
    scanner_version: str = SCANNER_VERSION,
) -> dict[str, Any]:
    """用扫描器输出生成可填写的金标准骨架（人工标注的起点，不是金标准本身）。"""

    result = scan_quotes(
        canonical_text, book_version_id=book_id, scanner_version=scanner_version
    )
    gaps = build_gaps(
        canonical_text,
        result.quotes,
        book_version_id=book_id,
        scanner_version=scanner_version,
    )
    canonical_sha256 = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()

    return {
        "schema_version": GOLD_SCHEMA_VERSION,
        "book": {
            "book_id": book_id,
            "work_id": work_id or book_id,
            "title": title,
            "format": format,
            "language": language,
            "canonical_sha256": canonical_sha256,
            "canonical_length_cp": len(canonical_text),
            "parser_version": "template",
            "normalization_version": "template",
            "notes": (
                "由 backend/scripts/gold_standard.py template 生成的骨架："
                f"候选来自扫描器 {scanner_version}（encoding={encoding or 'unknown'}）。"
                "请人工填写 resolvable / group_id / kind / evidence_refs，"
                "未确认的对白必须保持 resolvable=false 且 group_id=null。"
            ),
        },
        "annotator": {
            "annotator_id": annotator_id,
            "annotated_at": annotated_at,
            "guideline_version": "1.0",
        },
        "scenes": [
            {
                "scene_id": "scene_1",
                "start_cp": 0,
                "end_cp": len(canonical_text),
                "participants": [],
                "notes": "默认整篇一个场景；请按金标准场景边界拆分/调整。",
            }
        ],
        "quotes": [
            {
                "quote_id": f"t{index:04d}",
                "scene_id": "scene_1",
                "start_cp": quote.start_cp,
                "end_cp": quote.end_cp,
                "kind": "unknown",
                "utterance_id": None,
                "group_id": None,
                "resolvable": False,
                "evidence_refs": [],
                "notes": f"扫描器候选（delimiter={quote.delimiter}, depth={quote.nesting_depth}）",
            }
            for index, quote in enumerate(result.quotes, start=1)
        ],
        "gaps": [
            {
                "gap_id": f"tg{index:04d}",
                "left_quote_id": f"t{_index_of(result.quotes, gap.left_quote_id):04d}",
                "right_quote_id": f"t{_index_of(result.quotes, gap.right_quote_id):04d}",
                "decision": "UNCERTAIN",
                "must_keep": [{"start_cp": gap.start_cp, "end_cp": gap.end_cp}],
                "notes": "扫描器提出的叙述间隔（中间叙述默认全部保留）。",
            }
            for index, gap in enumerate(gaps, start=1)
        ],
        "notes": "这是标注模板，不是金标准；未经人工确认不能当作评测数据。",
    }


def _index_of(quotes, quote_id: str | None) -> int:  # noqa: ANN001
    for index, quote in enumerate(quotes, start=1):
        if quote.quote_id == quote_id:
            return index
    return 0
