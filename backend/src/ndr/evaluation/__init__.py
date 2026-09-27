"""评测支持：金标准校验、样例模板与候选覆盖率（T05 起，T16 扩展）。"""

from __future__ import annotations

from .gold_standard import (
    GOLD_SCHEMA_VERSION,
    GoldIssue,
    build_template,
    check_against_scanner,
    check_references,
    load_gold_standard,
    summarize,
    validate_schema,
)

__all__ = [
    "GOLD_SCHEMA_VERSION",
    "GoldIssue",
    "build_template",
    "check_against_scanner",
    "check_references",
    "load_gold_standard",
    "summarize",
    "validate_schema",
]
