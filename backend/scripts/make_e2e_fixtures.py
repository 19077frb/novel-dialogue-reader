"""生成 E2E 用的原创样例文件。

产物写入 ``frontend/e2e/fixtures/``：UTF-8 TXT、GB18030 TXT、含 ruby 与插图的 EPUB。
内容全部是自行编写的最小片段，可重复生成；不含任何真实作品数据。

用法（项目根目录）：
    uv run --project backend python backend/scripts/make_e2e_fixtures.py
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend" / "tests"))  # 复用原创 EPUB 夹具构造器

from fixtures.epub_factory import build_epub, ruby_and_image_spec  # noqa: E402

OUT_DIR = REPO_ROOT / "frontend" / "e2e" / "fixtures"

TXT_SAMPLE = (
    "序章 雨夜\n"
    "\n"
    "「雨停了。」少女合上伞。\n"
    "少年没有回答，只是把外套递了过去。\n"
    "\n"
    "「……谢谢。」她低声说。\n"
    "\n"
    "第一章 转折\n"
    "\n"
    "远处传来钟声，两人都没有再开口。\n"
    "「明天也来这里吧。」少年忽然说。\n"
    "「嗯。」少女点了点头。\n"
    "\n"
    "第二章 名字\n"
    "\n"
    "𠮷野家的猫🐈跳上窗台。\n"
    "「雨停了。」少女又说了一次。\n"
)


def tiny_png() -> bytes:
    """生成一个真实的 1×1 PNG（红点），用于验证图片资源真的能被浏览器加载。"""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00" + b"\xff\x00\x00"  # 无过滤 + 红色像素
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "sample-utf8.txt").write_bytes(TXT_SAMPLE.encode("utf-8"))
    (OUT_DIR / "sample-gb18030.txt").write_bytes(TXT_SAMPLE.encode("gb18030"))
    (OUT_DIR / "original-sample.epub").write_bytes(
        build_epub(ruby_and_image_spec(tiny_png()))
    )
    for path in sorted(OUT_DIR.iterdir()):
        print(f"{path.name}: {path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
