"""金标准工具：校验、生成模板、离线扫描候选。

用法（项目根目录）：

    # 校验一份金标准（可选带正文做范围/引号检查）
    uv run --project backend python backend/scripts/gold_standard.py validate \
        --gold evaluation/examples/minimal-txt-001/gold.json \
        --text evaluation/examples/minimal-txt-001/text.txt

    # 用扫描器候选生成可填写的标注模板
    uv run --project backend python backend/scripts/gold_standard.py template \
        --text data/run/sample-gb18030.txt --out data/run/gold-template.json

    # 只看候选扫描结果（离线 sanity check，不调用模型）
    uv run --project backend python backend/scripts/gold_standard.py scan --text data/run/a.txt

退出码：0 = 通过，1 = 有错误（error），2 = 用法/文件问题。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ndr.evaluation.gold_standard import (
    build_template,
    check_against_scanner,
    check_references,
    load_gold_standard,
    read_canonical_text,
    summarize,
    validate_schema,
)
from ndr.quotes.scanner import scan_quotes


def _cmd_validate(args: argparse.Namespace) -> int:
    gold = load_gold_standard(args.gold)
    issues = validate_schema(gold, args.schema)

    canonical_text: str | None = None
    if args.text:
        canonical_text, _encoding = read_canonical_text(args.text, encoding=args.encoding)
    issues.extend(check_references(gold, canonical_text=canonical_text))

    summary = summarize(gold)
    if args.text and canonical_text is not None:
        scanner_issues, coverage = check_against_scanner(gold, canonical_text)
        issues.extend(scanner_issues)
        summary["candidate_coverage"] = coverage

    errors = [issue for issue in issues if issue.is_error]
    if args.json:
        print(
            json.dumps(
                {"summary": summary, "issues": [issue.as_dict() for issue in issues]},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            f"金标准：{summary['book_id']} · 场景 {summary['scenes']}"
            f" · 对白 {summary['quotes']} · Gap {summary['gaps']}"
        )
        print(
            f"可判定 {summary['resolvable_quotes']} / 不可判定 {summary['unresolvable_quotes']}"
        )
        if "candidate_coverage" in summary:
            coverage = summary["candidate_coverage"]
            coverage_line = (
                "候选覆盖：精确 {matched} / 被更大候选包含 {inside_larger_candidate}"
                " / 缺失 {missing}（扫描器候选 {scanner_candidates}）"
            )
            print(coverage_line.format(**coverage))
        for issue in issues:
            print(f"[{issue.level}] {issue.code}: {issue.message}")
        print("结论：" + ("通过" if not errors else f"发现 {len(errors)} 个错误"))
    return 1 if errors else 0


def _cmd_template(args: argparse.Namespace) -> int:
    canonical_text, encoding = read_canonical_text(args.text, encoding=args.encoding)
    book_id = args.book_id or Path(args.text).stem
    template = build_template(
        canonical_text,
        book_id=book_id,
        title=args.title or book_id,
        format="EPUB" if str(args.text).lower().endswith(".epub") else "TXT",
        encoding=encoding,
        annotator_id=args.annotator,
    )
    payload = json.dumps(template, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8", newline="\n")
        print(
            f"已写入 {args.out}（候选 {len(template['quotes'])} 条；"
            "需人工填写 resolvable/group_id）"
        )
    else:
        print(payload)
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    canonical_text, encoding = read_canonical_text(args.text, encoding=args.encoding)
    result = scan_quotes(canonical_text, book_version_id="offline-scan")
    if args.json:
        print(
            json.dumps(
                {
                    "encoding": encoding,
                    "length_cp": len(canonical_text),
                    "stats": result.stats,
                    "warnings": [
                        {
                            "code": warning.code,
                            "position_cp": warning.position_cp,
                            "delimiter": warning.delimiter,
                            "detail": warning.detail,
                        }
                        for warning in result.warnings
                    ],
                    "quotes": [
                        {
                            "start_cp": quote.start_cp,
                            "end_cp": quote.end_cp,
                            "depth": quote.nesting_depth,
                            "delimiter": quote.delimiter,
                            "text": canonical_text[quote.start_cp : quote.end_cp],
                        }
                        for quote in result.quotes
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"编码 {encoding} · 长度 {len(canonical_text)} 码点 · 候选 {result.stats['quotes']} 条")
    print(
        "外层候选 {top_level_quotes} · 警告 {warnings} · 放弃开引号 {abandoned_open_quotes}"
        " · 游离闭引号 {stray_closes}".format(**result.stats)
    )
    for quote in result.quotes[: args.head]:
        text = canonical_text[quote.start_cp : quote.end_cp]
        print(f"  [{quote.start_cp},{quote.end_cp}) depth={quote.nesting_depth} {text}")
    if len(result.quotes) > args.head:
        print(f"  …（共 {len(result.quotes)} 条，用 --head 调整显示条数）")
    for warning in result.warnings[:10]:
        print(f"  ! {warning.code}@{warning.position_cp}: {warning.detail}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="金标准工具（校验/模板/离线扫描）")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="校验金标准文件")
    validate.add_argument("--gold", required=True)
    validate.add_argument("--text", default=None, help="配套的 TXT/EPUB 原文（用于范围检查）")
    validate.add_argument("--encoding", default=None)
    validate.add_argument("--schema", default=None)
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=_cmd_validate)

    template = sub.add_parser("template", help="用扫描器候选生成标注模板")
    template.add_argument("--text", required=True)
    template.add_argument("--out", default=None)
    template.add_argument("--book-id", default=None)
    template.add_argument("--title", default=None)
    template.add_argument("--annotator", default="unassigned")
    template.add_argument("--encoding", default=None)
    template.set_defaults(func=_cmd_template)

    scan = sub.add_parser("scan", help="离线扫描候选引语")
    scan.add_argument("--text", required=True)
    scan.add_argument("--encoding", default=None)
    scan.add_argument("--head", type=int, default=10)
    scan.add_argument("--json", action="store_true")
    scan.set_defaults(func=_cmd_scan)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        print(f"文件不存在：{exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"输入无法解析：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
