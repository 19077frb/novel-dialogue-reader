"""EPUB 打包。

- `mimetype` 必须是 zip 的第一项且**不压缩**（EPUB 规范要求）。
- 不引用远程资源；图片来自书籍目录里登记过的本地资源。
- 时间戳固定，保证同一输入重复导出的字节一致（配合 fingerprint 做幂等与复用）。
"""

from __future__ import annotations

import base64  # noqa: F401 - 保留下游便于调试内联资源
import io
import zipfile
from collections.abc import Mapping

from ..domain.enums import ContentNodeType
from .html import render_block
from .render import EXPORT_CSS, RenderedBook, escape

_CONTAINER_XML = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "image/webp": "webp",
}

ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def resource_filename(resource_id: str, media_type: str | None) -> str:
    extension = _EXTENSIONS.get(media_type or "", "bin")
    safe = "".join(char for char in resource_id if char.isalnum() or char in "-_") or "resource"
    return f"{safe}.{extension}"


def chapter_xhtml(title: str, body_html: str, *, language: str = "zh") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{language}" lang="{language}">\n'
        "<head>\n"
        '<meta charset="utf-8"/>\n'
        f"<title>{escape(title)}</title>\n"
        '<link rel="stylesheet" type="text/css" href="../style.css"/>\n'
        "</head>\n"
        "<body>\n"
        f'<h2 class="chapter-title">{escape(title)}</h2>\n'
        f"{body_html}\n"
        "</body>\n"
        "</html>\n"
    )


def _write(
    archive: zipfile.ZipFile, name: str, data: bytes | str, *, compress: bool = True
) -> None:
    payload = data.encode("utf-8") if isinstance(data, str) else data
    info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    info.external_attr = 0o644 << 16
    archive.writestr(info, payload)


def build_epub(
    rendered: RenderedBook,
    *,
    images: Mapping[str, bytes],
    identifier: str,
    generated_at: str,
) -> bytes:
    """把渲染结果打包成 EPUB 3（离线可读，不依赖外部资源）。"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        # 规范要求：mimetype 必须第一项且不压缩
        _write(archive, "mimetype", b"application/epub+zip", compress=False)
        _write(archive, "META-INF/container.xml", _CONTAINER_XML)
        _write(archive, "OEBPS/style.css", EXPORT_CSS)

        manifest: list[str] = [
            '<item id="style" href="style.css" media-type="text/css"/>',
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        ]
        spine: list[str] = []
        nav_items: list[str] = []
        used_images: dict[str, str] = {}

        for index, chapter in enumerate(rendered.chapters, start=1):
            body_parts: list[str] = []
            for block in chapter.blocks:
                if block.node_type is ContentNodeType.IMAGE and block.resource_id:
                    filename = resource_filename(block.resource_id, block.media_type)
                    if block.resource_id not in used_images and block.resource_id in images:
                        used_images[block.resource_id] = filename
                        _write(archive, f"OEBPS/images/{filename}", images[block.resource_id])
                    alt = escape(block.alt)
                    body_parts.append(
                        f'<figure><img src="../images/{filename}" alt="{alt}"/>'
                        f"<figcaption>{alt}</figcaption></figure>"
                    )
                    continue
                body_parts.append(render_block(block, images={}))
            href = f"text/chapter-{index:03d}.xhtml"
            _write(
                archive,
                f"OEBPS/{href}",
                chapter_xhtml(chapter.title, "\n".join(body_parts), language=rendered.language),
            )
            manifest.append(
                f'<item id="chapter-{index}" href="{href}" media-type="application/xhtml+xml"/>'
            )
            spine.append(f'<itemref idref="chapter-{index}"/>')
            nav_items.append(f'<li><a href="{href}">{escape(chapter.title)}</a></li>')

        for resource_id, filename in used_images.items():
            media_type = next(
                (
                    block.media_type
                    for chapter in rendered.chapters
                    for block in chapter.blocks
                    if block.resource_id == resource_id
                ),
                "application/octet-stream",
            )
            manifest.append(
                f'<item id="img-{filename}" href="images/{filename}" media-type="{media_type}"/>'
            )

        archive.writestr(
            zipfile.ZipInfo("OEBPS/nav.xhtml", date_time=ZIP_TIMESTAMP),
            (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub='
                f'"http://www.idpf.org/2007/ops" xml:lang="{rendered.language}">\n'
                "<head><title>目录</title></head>\n<body>\n"
                '<nav epub:type="toc" id="toc"><h1>目录</h1><ol>\n'
                + "\n".join(nav_items)
                + "\n</ol></nav>\n</body>\n</html>\n"
            ),
        )
        _write(
            archive,
            "OEBPS/content.opf",
            _content_opf(
                rendered,
                identifier=identifier,
                generated_at=generated_at,
                manifest="\n    ".join(manifest),
                spine="\n    ".join(spine),
            ),
        )
    return buffer.getvalue()

def _content_opf(
    rendered: RenderedBook,
    *,
    identifier: str,
    generated_at: str,
    manifest: str,
    spine: str,
) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="bookid">{escape(identifier)}</dc:identifier>\n'
        f"    <dc:title>{escape(rendered.title)}</dc:title>\n"
        f'    <dc:language>{rendered.language}</dc:language>\n'
        f"    <meta property=\"dcterms:modified\">{generated_at}</meta>\n"
        "  </metadata>\n"
        "  <manifest>\n    "
        + manifest
        + "\n  </manifest>\n  <spine>\n    "
        + spine
        + "\n  </spine>\n</package>\n"
    )
