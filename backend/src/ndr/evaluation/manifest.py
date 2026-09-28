"""评测清单：**作品级划分** + 金标准引用校验。

- 一份清单包含若干 `works`，每个作品有唯一 `split`（`dev` / `calibration` / `test`）。
- 同一作品不能出现在两个 split 里（防止调参与测试互相污染）。
- 每个 book 指向正文与金标准文件；校验时会加载并检查金标准结构、引用与作品一致性。

路径解析顺序：先按**清单文件所在目录**解析，再按**仓库根目录**解析（便于从任意 cwd 运行）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .gold_standard import (
    GoldIssue,
    check_references,
    load_gold_standard,
    read_canonical_text,
    validate_schema,
)

MANIFEST_VERSION = "1.0"
KNOWN_SPLITS = ("dev", "calibration", "test")
KNOWN_HARD_CASES = (
    "no_explicit_attribution",
    "scene_boundary",
    "nested_quote",
    "thought",
    "group_voice",
    "long_gap",
    "repeated_quote",
    "astral_text",
)
REPO_ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class ManifestBook:
    book_id: str
    work_id: str
    split: str
    text_path: Path
    gold_path: Path
    book_format: str | None = None
    hard_cases: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""


@dataclass(frozen=True)
class ManifestWork:
    work_id: str
    title: str
    split: str
    books: tuple[ManifestBook, ...] = field(default_factory=tuple)
    license: str = ""
    notes: str = ""


@dataclass(frozen=True)
class Manifest:
    version: str
    path: Path
    works: tuple[ManifestWork, ...] = field(default_factory=tuple)
    description: str = ""
    notes: str = ""

    def books(self, splits: list[str] | None = None) -> list[ManifestBook]:
        selected = set(splits) if splits else None
        return [
            book
            for work in self.works
            for book in work.books
            if selected is None or work.split in selected
        ]

    def work_ids(self, split: str | None = None) -> list[str]:
        return [work.work_id for work in self.works if split is None or work.split == split]


def _resolve(base: Path, value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    for root in (base, REPO_ROOT):
        resolved = (root / candidate).resolve()
        if resolved.exists():
            return resolved
    return (base / candidate).resolve()


def load_manifest(path: str | Path) -> Manifest:
    manifest_path = Path(path).resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("清单必须是 JSON 对象")
    base = manifest_path.parent
    works: list[ManifestWork] = []
    for raw_work in payload.get("works", []):
        split = str(raw_work.get("split", "dev"))
        books = tuple(
            ManifestBook(
                book_id=str(raw_book["book_id"]),
                work_id=str(raw_work["work_id"]),
                split=split,
                text_path=_resolve(base, str(raw_book["text"])),
                gold_path=_resolve(base, str(raw_book["gold"])),
                book_format=raw_book.get("format"),
                hard_cases=tuple(raw_book.get("hard_cases", ()) or ()),
                notes=str(raw_book.get("notes", "")),
            )
            for raw_book in raw_work.get("books", [])
        )
        works.append(
            ManifestWork(
                work_id=str(raw_work["work_id"]),
                title=str(raw_work.get("title", raw_work["work_id"])),
                split=split,
                books=books,
                license=str(raw_work.get("license", "")),
                notes=str(raw_work.get("notes", "")),
            )
        )
    return Manifest(
        version=str(payload.get("manifest_version", MANIFEST_VERSION)),
        path=manifest_path,
        works=tuple(works),
        description=str(payload.get("description", "")),
        notes=str(payload.get("notes", "")),
    )

def validate_manifest(manifest: Manifest, *, check_gold: bool = True) -> list[GoldIssue]:
    """结构与数据检查；返回问题清单（`level=error` 表示不可用于评测）。"""

    issues: list[GoldIssue] = []

    def error(code: str, message: str) -> None:
        issues.append(GoldIssue(level="error", code=code, message=message))

    def warning(code: str, message: str) -> None:
        issues.append(GoldIssue(level="warning", code=code, message=message))

    if manifest.version != MANIFEST_VERSION:
        error("unsupported_manifest_version", f"不支持的 manifest_version：{manifest.version}")
    if not manifest.works:
        error("empty_manifest", "清单里没有任何作品")

    seen_books: set[str] = set()
    work_splits: dict[str, set[str]] = {}
    for work in manifest.works:
        if work.split not in KNOWN_SPLITS:
            error("unknown_split", f"作品 {work.work_id} 的 split 非法：{work.split}")
        work_splits.setdefault(work.work_id, set()).add(work.split)
        if not work.books:
            warning("work_without_books", f"作品 {work.work_id} 没有任何书")
        for book in work.books:
            if book.book_id in seen_books:
                error("duplicate_book_id", f"book_id 重复：{book.book_id}")
            seen_books.add(book.book_id)
            for case in book.hard_cases:
                if case not in KNOWN_HARD_CASES:
                    warning("unknown_hard_case", f"{book.book_id} 的难例类别未知：{case}")
            if not book.text_path.exists():
                error("missing_text", f"缺少正文文件：{book.text_path}")
            if not book.gold_path.exists():
                error("missing_gold", f"缺少金标准文件：{book.gold_path}")
                continue
            if not check_gold:
                continue
            gold = load_gold_standard(book.gold_path)
            issues.extend(
                _prefix_issues(
                    validate_schema(gold, None),
                    book.book_id,
                )
            )
            canonical_text = None
            if book.text_path.exists():
                canonical_text, _encoding = read_canonical_text(book.text_path)
            issues.extend(
                _prefix_issues(
                    check_references(gold, canonical_text=canonical_text), book.book_id
                )
            )
            gold_work = str(gold.get("book", {}).get("work_id", ""))
            if gold_work != work.work_id:
                error(
                    "work_id_mismatch",
                    f"{book.book_id} 的金标准 work_id={gold_work} 与清单 {work.work_id} 不一致",
                )

    for work_id, splits in work_splits.items():
        if len(splits) > 1:
            error(
                "work_in_multiple_splits",
                f"作品 {work_id} 出现在多个 split：{sorted(splits)}（会污染调参/测试划分）",
            )
    return issues


def _prefix_issues(issues: list[GoldIssue], book_id: str) -> list[GoldIssue]:
    return [
        GoldIssue(level=item.level, code=item.code, message=f"[{book_id}] {item.message}")
        for item in issues
    ]
