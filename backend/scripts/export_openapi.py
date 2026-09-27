"""导出 OpenAPI 文档到 docs/openapi.json。

用法（从项目根目录）：
    uv run --project backend python backend/scripts/export_openapi.py --output docs/openapi.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ndr.app import create_app


def render_openapi() -> str:
    schema = create_app().openapi()
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 NDR 后端 OpenAPI 文档")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--check",
        action="store_true",
        help="只比较文件是否与当前应用一致，不写文件；不一致时返回非零。",
    )
    args = parser.parse_args()

    content = render_openapi()
    target: Path = args.output

    if args.check:
        if not target.exists():
            print(f"[export_openapi] 缺少 {target}，请重新生成。")
            return 1
        existing = target.read_text(encoding="utf-8")
        if existing != content:
            print(f"[export_openapi] {target} 与当前应用不一致，请重新生成。")
            return 1
        print(f"[export_openapi] {target} 与当前应用一致。")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"[export_openapi] 已写入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
