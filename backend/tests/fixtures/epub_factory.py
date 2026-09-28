"""原始 EPUB 夹具构造器。

所有内容都是原创最小片段，测试用例按需拼装：spine 顺序与文件名顺序可以不同，
可以加入 ruby、图片、脚本、越界条目与符号链接条目来验证安全边界。
"""

from __future__ import annotations

import io
import mimetypes
import zipfile
from dataclasses import dataclass, field

XHTML_TEMPLATE = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<html xmlns="http://www.w3.org/1999/xhtml" '
    'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh">\n'
    "<head><title>{title}</title></head>\n"
    "<body>\n{body}\n</body>\n</html>\n"
)


@dataclass
class Document:
    doc_id: str
    href: str
    body: str
    title: str = ""


@dataclass
class EpubSpec:
    documents: list[Document] = field(default_factory=list)
    spine: list[str] | None = None
    manifest_order: list[str] | None = None
    resources: dict[str, bytes] = field(default_factory=dict)
    nav: list[tuple[str, str]] | None = None
    ncx: list[tuple[str, str]] | None = None
    extras: dict[str, bytes] = field(default_factory=dict)
    # 外链 manifest 项：用于验证外部引用被拒绝/不下载
    external_items: list[tuple[str, str]] = field(default_factory=list)
    opf_dir: str = "OEBPS"
    title: str = "原创 EPUB 样例"
    language: str = "zh"
    include_container: bool = True
    mimetype_stored: bool = True
    symlink_entries: tuple[str, ...] = ()


def _media_type(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def build_epub(spec: EpubSpec) -> bytes:
    """按 spec 生成 EPUB 字节串。"""

    opf_dir = spec.opf_dir.strip("/")
    prefix = f"{opf_dir}/" if opf_dir else ""
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if spec.mimetype_stored:
            info = zipfile.ZipInfo("mimetype")
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, "application/epub+zip")

        if spec.include_container:
            archive.writestr(
                "META-INF/container.xml",
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<container version="1.0" '
                'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                f'  <rootfiles><rootfile full-path="{prefix}content.opf" '
                'media-type="application/oebps-package+xml"/></rootfiles>\n'
                "</container>\n",
            )

        for document in spec.documents:
            archive.writestr(
                f"{prefix}{document.href}",
                XHTML_TEMPLATE.format(
                    title=document.title or document.doc_id, body=document.body
                ),
            )

        if spec.nav is not None:
            items = "\n".join(
                f'      <li><a href="{href}">{title}</a></li>' for href, title in spec.nav
            )
            archive.writestr(
                f"{prefix}nav.xhtml",
                XHTML_TEMPLATE.format(
                    title="目录",
                    body=(
                        '  <nav epub:type="toc" id="toc">\n'
                        "    <h1>目录</h1>\n"
                        f"    <ol>\n{items}\n    </ol>\n"
                        "  </nav>"
                    ),
                ),
            )

        if spec.ncx is not None:
            points = "\n".join(
                (
                    '    <navPoint id="np{n}" playOrder="{n}">\n'
                    "      <navLabel><text>{title}</text></navLabel>\n"
                    '      <content src="{href}"/>\n'
                    "    </navPoint>"
                ).format(n=index + 1, title=title, href=href)
                for index, (href, title) in enumerate(spec.ncx)
            )
            archive.writestr(
                f"{prefix}toc.ncx",
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
                "  <navMap>\n"
                f"{points}\n"
                "  </navMap>\n"
                "</ncx>\n",
            )

        for href, payload in spec.resources.items():
            archive.writestr(f"{prefix}{href}", payload)

        for path, payload in spec.extras.items():
            archive.writestr(path, payload)

        for path in spec.symlink_entries:
            info = zipfile.ZipInfo(path)
            info.external_attr = 0o120777 << 16
            archive.writestr(info, "../outside")

        manifest_items: list[str] = []
        by_id = {document.doc_id: document for document in spec.documents}
        order = spec.manifest_order or [document.doc_id for document in spec.documents]
        for doc_id in order:
            document = by_id[doc_id]
            manifest_items.append(
                f'    <item id="{doc_id}" href="{document.href}" '
                'media-type="application/xhtml+xml"/>'
            )
        if spec.nav is not None:
            manifest_items.append(
                '    <item id="nav" href="nav.xhtml" '
                'media-type="application/xhtml+xml" properties="nav"/>'
            )
        if spec.ncx is not None:
            manifest_items.append(
                '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
            )
        for index, href in enumerate(spec.resources, start=1):
            manifest_items.append(
                f'    <item id="res{index}" href="{href}" media-type="{_media_type(href)}"/>'
            )
        for index, href in enumerate(spec.extras, start=1):
            manifest_items.append(
                f'    <item id="extra{index}" href="{href}" '
                f'media-type="{_media_type(href)}"/>'
            )
        for item_id, href in spec.external_items:
            manifest_items.append(
                f'    <item id="{item_id}" href="{href}" media-type="application/xhtml+xml"/>'
            )

        spine_order = (
            spec.spine
            if spec.spine is not None
            else [document.doc_id for document in spec.documents]
        )
        spine_items = "\n".join(f'    <itemref idref="{doc_id}"/>' for doc_id in spine_order)
        ncx_attr = ' toc="ncx"' if spec.ncx is not None else ""
        archive.writestr(
            f"{prefix}content.opf",
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="bookid">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            '    <dc:identifier id="bookid">urn:uuid:ndr-original-fixture</dc:identifier>\n'
            f"    <dc:title>{spec.title}</dc:title>\n"
            f"    <dc:language>{spec.language}</dc:language>\n"
            "  </metadata>\n"
            "  <manifest>\n" + "\n".join(manifest_items) + "\n  </manifest>\n"
            f"  <spine{ncx_attr}>\n{spine_items}\n  </spine>\n"
            "</package>\n",
        )

    return buffer.getvalue()


def minimal_spec() -> EpubSpec:
    """最小两章样例：文件名顺序与 spine 顺序故意不同，TOC 与正文顺序分离。"""

    return EpubSpec(
        documents=[
            Document(
                doc_id="ch2",
                href="text/a-second.xhtml",
                body="<h1>第二章 名字</h1>\n<p>「雨停了。」少女又说了一次。</p>",
                title="第二章 名字",
            ),
            Document(
                doc_id="ch1",
                href="text/b-first.xhtml",
                body=(
                    "<h1>第一章 雨夜</h1>\n"
                    "<p>「雨停了。」少女合上伞。</p>\n"
                    "<p>少年没有回答，只是把外套递了过去。</p>"
                ),
                title="第一章 雨夜",
            ),
        ],
        spine=["ch1", "ch2"],
        manifest_order=["ch2", "ch1"],
        nav=[("text/b-first.xhtml", "第一章 雨夜"), ("text/a-second.xhtml", "第二章 名字")],
    )


def ruby_and_image_spec(image_bytes: bytes = b"\x89PNG\r\n\x1a\noriginal") -> EpubSpec:
    """样例：ruby 注音 + 插图 + 跨块对白 + 脚本与样式。"""

    return EpubSpec(
        title="原创 ruby/插图样例",
        documents=[
            Document(
                doc_id="ch1",
                href="text/chapter.xhtml",
                body=(
                    "<h1>第一章 注音</h1>\n"
                    "<p><ruby>漢<rp>(</rp><rt>かん</rt><rp>)</rp></ruby>字与「对白」。</p>\n"
                    '<p><img src="../images/cover.png" alt="插图"/>插图之后的对白。</p>\n'
                    "<p>「跨块的同一句发言，」</p>\n"
                    "<p>「被叙述分开了。」</p>\n"
                    "<script>window.__evil = 1;</script>\n"
                    "<style>p { color: red; }</style>\n"
                    '<p>外链图片：<img src="https://example.com/remote.png"/></p>'
                ),
                title="第一章 注音",
            )
        ],
        resources={"images/cover.png": image_bytes},
        nav=[("text/chapter.xhtml", "第一章 注音")],
    )


def pretty_printed_spec() -> EpubSpec:
    """排版空白样例：源文件里换行缩进很多，正文不应带出多余空白。"""

    return EpubSpec(
        documents=[
            Document(
                doc_id="ch1",
                href="text/pretty.xhtml",
                body="<p>\n  第一段\n  跨行书写。\n</p>\n<p>第二段\t带制表符。</p>",
            )
        ],
    )
