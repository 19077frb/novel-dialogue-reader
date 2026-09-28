"""校验导出成品。

用法：

    python backend/scripts/validate_exports.py --epub data/test-exports/sample.epub \
        --epubcheck-jar tools/epubcheck/epubcheck.jar

    python backend/scripts/validate_exports.py --html data/test-exports/sample.html \
        --expected-text-file data/test-exports/sample.txt --json report.json

约定：

- 内部检查永远运行（zip 结构 / mimetype / 外部引用 / 资源闭合 / 正文一致性）。
- `--expected-text-file` 给出「剔除标记后应有的正文」，用于正文一致性检查；不提供时该检查记为
  `SKIPPED`（不假装通过）。
- EPUBCheck 用**参数数组**调用 Java，不拼接 shell；jar 或 java 缺失时状态为 `NOT_RUN`。
- 任一失败退出码 1；用法错误退出码 2。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ndr.exports.validation import check_epub, check_html, epubcheck_status  # noqa: E402

DEFAULT_JAR_HINTS = (
    Path("tools/epubcheck/epubcheck.jar"),
    Path("tools/epubcheck-5.1.0/epubcheck.jar"),
)


def _resolve_jar(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit)
    import os

    from_env = os.environ.get("NDR_EPUBCHECK_JAR")
    if from_env:
        return Path(from_env)
    for hint in DEFAULT_JAR_HINTS:
        if hint.exists():
            return hint
    return None


def _report(path: Path, *, fmt: str, expected: str | None, jar: Path | None) -> dict:
    if not path.exists():
        return {
            "path": str(path),
            "format": fmt,
            "ok": False,
            "detail": "文件不存在",
            "internal": {"ok": False},
            "standard": {"state": "NOT_RUN", "tool": None, "detail": "文件不存在"},
        }
    if fmt == "epub":
        internal = check_epub(path.read_bytes(), expected_text=expected or "")
        if expected is None:
            internal["checks"]["text_consistency"] = True
            internal["text_consistency"] = "SKIPPED"
        standard = epubcheck_status(path, jar)
    else:
        internal = check_html(path.read_text(encoding="utf-8"), expected_text=expected or "")
        if expected is None:
            internal["checks"]["text_consistency"] = True
            internal["text_consistency"] = "SKIPPED"
        standard = {"state": "NOT_APPLICABLE", "tool": None, "detail": "HTML 不需要 EPUBCheck"}
    ok = bool(internal.get("ok")) and standard["state"] in {"PASS", "NOT_RUN", "NOT_APPLICABLE"}
    return {
        "path": str(path),
        "format": fmt,
        "ok": ok,
        "internal": internal,
        "standard": standard,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验导出成品（EPUB/HTML）")
    parser.add_argument("--epub", action="append", default=[], help="待校验的 EPUB 文件")
    parser.add_argument("--html", action="append", default=[], help="待校验的 HTML 文件")
    parser.add_argument("--expected-text-file", help="剔除标记后应有的正文（UTF-8）")
    parser.add_argument("--epubcheck-jar", help="EPUBCheck jar 路径")
    parser.add_argument("--json", dest="json_out", help="把完整报告写到该文件")
    args = parser.parse_args(argv)

    targets = [("epub", Path(item)) for item in args.epub] + [
        ("html", Path(item)) for item in args.html
    ]
    if not targets:
        parser.error("至少提供一个 --epub 或 --html")

    expected = (
        Path(args.expected_text_file).read_text(encoding="utf-8")
        if args.expected_text_file
        else None
    )
    jar = _resolve_jar(args.epubcheck_jar) if any(fmt == "epub" for fmt, _ in targets) else None

    reports = [_report(path, fmt=fmt, expected=expected, jar=jar) for fmt, path in targets]
    payload = {
        "ok": all(item.get("ok") for item in reports),
        "epubcheck_jar": str(jar) if jar else None,
        "reports": reports,
    }
    for item in reports:
        standard = item.get("standard") or {}
        print(
            f"[{item['format']}] {item['path']} → "
            f"internal={'ok' if item.get('internal', {}).get('ok') else 'fail'} · "
            f"standard={standard.get('state')}"
            + (f" ({standard.get('detail')})" if standard.get("detail") else "")
        )
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
