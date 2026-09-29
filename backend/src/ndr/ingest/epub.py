"""EPUB → 统一文档树。

流程：安全的 ZIP 读取 → ``META-INF/container.xml`` → OPF（metadata/manifest/spine）→
按 **spine 顺序**读取正文文档 → 目录（nav.xhtml 或 NCX）与 spine 关联 → XHTML 转受限节点树 →
登记资源 → 提取正文与 source_map。

安全边界：

- 归一化路径不得越出包内；拒绝绝对路径、``..``、反斜杠逃逸、重复条目与符号链接条目。
- 条目数量、单条目解压大小与总解压大小都有上限，超限返回可解释错误而不是吞掉整本书。
- 不执行原书脚本：``script``/``style`` 内容既不进正文也不登记为资源；外链图片只告警、不下载。
- 只读取包内已登记资源，资源端点不接受任意磁盘路径。
"""

from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import posixpath
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from ..domain.enums import ContentNodeType
from .document import (
    ParsedBook,
    ParsedChapter,
    ParsedMapping,
    ParsedNode,
    ParsedResource,
    collapse_whitespace,
)

EPUB_PARSER_VERSION = "epub-1"
EPUB_NORMALIZATION_VERSION = "canonical-epub-blocks-1"

CONTAINER_PATH = "META-INF/container.xml"
BLOCKED_MEDIA_PREFIXES = ("text/javascript", "application/javascript", "application/ecmascript")
DOCUMENT_MEDIA_TYPES = {"application/xhtml+xml", "text/html", "application/x-dtbncx+xml"}
SKIP_TAGS = {
    "script",
    "style",
    "head",
    "title",
    "meta",
    "link",
    "base",
    "object",
    "embed",
    "iframe",
    "audio",
    "video",
    "template",
    "map",
}
BLOCK_TAGS = {
    "p": ContentNodeType.PARAGRAPH,
    "div": ContentNodeType.PARAGRAPH,
    "section": ContentNodeType.PARAGRAPH,
    "article": ContentNodeType.PARAGRAPH,
    "aside": ContentNodeType.PARAGRAPH,
    "blockquote": ContentNodeType.PARAGRAPH,
    "pre": ContentNodeType.PARAGRAPH,
    "figure": ContentNodeType.PARAGRAPH,
    "figcaption": ContentNodeType.PARAGRAPH,
    "li": ContentNodeType.PARAGRAPH,
    "dd": ContentNodeType.PARAGRAPH,
    "dt": ContentNodeType.PARAGRAPH,
    "td": ContentNodeType.PARAGRAPH,
    "th": ContentNodeType.PARAGRAPH,
    "caption": ContentNodeType.PARAGRAPH,
    "hr": ContentNodeType.SEPARATOR,
    "h1": ContentNodeType.HEADING,
    "h2": ContentNodeType.HEADING,
    "h3": ContentNodeType.HEADING,
    "h4": ContentNodeType.HEADING,
    "h5": ContentNodeType.HEADING,
    "h6": ContentNodeType.HEADING,
}
HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


class EpubError(Exception):
    """EPUB 结构或安全校验失败；由 API 转成 415/422。"""

    def __init__(self, message: str, *, code: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class EpubLimits:
    max_entries: int = 2000
    max_total_uncompressed_bytes: int = 200 * 1024 * 1024
    max_entry_uncompressed_bytes: int = 32 * 1024 * 1024
    max_spine_items: int = 500


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def normalize_entry_name(name: str) -> str:
    """归一化 ZIP 条目路径；越界或非法时抛 :class:`EpubError`。"""

    if name.startswith("/") or "\\" in name:
        raise EpubError(
            "EPUB 内含非法条目路径（绝对路径或反斜杠）",
            code="EPUB_UNSAFE_PATH",
            details={"entry": name},
        )
    normalized = posixpath.normpath(name)
    if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
        raise EpubError(
            "EPUB 条目路径越出包内目录",
            code="EPUB_UNSAFE_PATH",
            details={"entry": name},
        )
    return normalized


def _resolve_href(base_dir: str, href: str) -> tuple[str, str | None]:
    """把 OPF/XHTML 里的相对链接解析成包内路径；返回 ``(path, fragment)``。"""

    parts = urlsplit(href)
    path = unquote(parts.path)
    if parts.scheme or parts.netloc:
        raise EpubError(
            "EPUB 内含外部链接，已拒绝加载",
            code="EPUB_EXTERNAL_REFERENCE",
            details={"href": href},
        )
    joined = (
        posixpath.normpath(posixpath.join(base_dir, path))
        if base_dir
        else posixpath.normpath(path)
    )
    if joined.startswith("../") or joined.startswith("/"):
        raise EpubError(
            "EPUB 链接越出包内目录",
            code="EPUB_UNSAFE_PATH",
            details={"href": href},
        )
    return joined, (parts.fragment or None)


class _Archive:
    """只读、安全校验过的 EPUB ZIP 视图。"""

    def __init__(self, raw: bytes, limits: EpubLimits) -> None:
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise EpubError(
                "文件不是合法的 EPUB/ZIP 容器",
                code="EPUB_NOT_A_ZIP",
                details={"reason": str(exc)},
            ) from exc

        self.limits = limits
        self._entries: dict[str, zipfile.ZipInfo] = {}
        total = 0
        for info in self._zip.infolist():
            if info.is_dir():
                continue
            name = normalize_entry_name(info.filename)
            if info.flag_bits & 0x1:
                raise EpubError(
                    "EPUB 含加密条目，暂不支持",
                    code="EPUB_ENCRYPTED_ENTRY",
                    details={"entry": name},
                )
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise EpubError(
                    "EPUB 含符号链接条目，已拒绝",
                    code="EPUB_SYMLINK_ENTRY",
                    details={"entry": name},
                )
            if name in self._entries:
                raise EpubError(
                    "EPUB 含重复条目路径",
                    code="EPUB_DUPLICATE_ENTRY",
                    details={"entry": name},
                )
            if info.file_size > limits.max_entry_uncompressed_bytes:
                raise EpubError(
                    "EPUB 单条目解压后过大",
                    code="EPUB_ENTRY_TOO_LARGE",
                    details={
                        "entry": name,
                        "size_bytes": info.file_size,
                        "max_entry_bytes": limits.max_entry_uncompressed_bytes,
                    },
                )
            total += info.file_size
            if total > limits.max_total_uncompressed_bytes:
                raise EpubError(
                    "EPUB 解压总量超过上限",
                    code="EPUB_SIZE_LIMIT",
                    details={
                        "total_uncompressed_bytes": total,
                        "max_total_bytes": limits.max_total_uncompressed_bytes,
                    },
                )
            if len(self._entries) >= limits.max_entries:
                raise EpubError(
                    "EPUB 条目数量超过上限",
                    code="EPUB_ENTRY_LIMIT",
                    details={"max_entries": limits.max_entries},
                )
            self._entries[name] = info

    def exists(self, name: str) -> bool:
        return name in self._entries

    def read(self, name: str) -> bytes:
        if name not in self._entries:
            raise EpubError(
                "EPUB 内缺少条目",
                code="EPUB_MISSING_ENTRY",
                details={"entry": name},
            )
        with self._zip.open(name) as handle:
            return handle.read()

    def names(self) -> Iterable[str]:
        return tuple(self._entries)


@dataclass
class _ManifestItem:
    item_id: str
    href: str
    media_type: str
    properties: tuple[str, ...]
    path: str
    # href 指向包外（http/https 等）：目录项允许存在，但正文/资源都不会去下载。
    external: bool = False


@dataclass
class _RawBlock:
    """文档内按顺序排布的块；图片块没有文本。"""

    node_type: ContentNodeType
    text: str = ""
    level: int | None = None
    resource_id: str | None = None
    media_type: str | None = None
    # "ok" / "missing"（包内缺失或未登记）/ "external"（外链，已单独告警）
    image_status: str | None = None
    image_alt: str | None = None
    missing_src: str | None = None
    ruby: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_image(self) -> bool:
        return self.node_type is ContentNodeType.IMAGE


@dataclass
class _Document:
    path: str
    blocks: list[_RawBlock] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)


class _BlockBuilder:
    """把 XHTML 元素流转换成受限块列表。"""

    def __init__(self, resolve_image) -> None:  # noqa: ANN001 - 回调注入以便测试
        self.blocks: list[_RawBlock] = []
        self._current: _RawBlock | None = None
        self._resolve_image = resolve_image

    def _flush(self) -> None:
        if self._current is None:
            return
        block = self._current
        block.text = block.text.rstrip()
        self._current = None
        if block.text or block.is_image:
            self.blocks.append(block)

    def start_block(self, node_type: ContentNodeType, level: int | None = None) -> None:
        self._flush()
        self._current = _RawBlock(node_type=node_type, level=level)

    def add_text(self, text: str) -> tuple[int, int]:
        """追加文本，返回 ``(块内起始偏移, 实际追加长度)``。"""

        if not text:
            return (0, 0)
        if self._current is None:
            self._current = _RawBlock(node_type=ContentNodeType.PARAGRAPH)
        piece = " ".join(text.split()) if text.strip() else ""
        if not piece:
            return (0, 0)
        if not self._current.text:
            piece = piece.lstrip()
        offset = len(self._current.text)
        self._current.text += piece
        return (offset, len(piece))

    def add_image(self, src: str | None, alt: str | None) -> None:
        self._flush()
        block = _RawBlock(node_type=ContentNodeType.IMAGE)
        if not src:
            block.image_status = "missing"
            block.missing_src = alt or ""
        else:
            status, resource_id, media_type = self._resolve_image(src)
            block.image_status = status
            block.resource_id = resource_id
            block.media_type = media_type
            block.image_alt = alt
            if status != "ok":
                block.missing_src = src
        self.blocks.append(block)

    def add_ruby(self, start: int, end: int, base: str, rt: str) -> None:
        if self._current is None:
            self._current = _RawBlock(node_type=ContentNodeType.PARAGRAPH)
        self._current.ruby.append({"start": start, "end": end, "base": base, "rt": rt})

    def finish(self) -> list[_RawBlock]:
        self._flush()
        return self.blocks


def _iter_text(element: ET.Element) -> str:
    parts = [element.text or ""]
    for child in list(element):
        parts.append(_iter_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _walk(
    element: ET.Element,
    builder: _BlockBuilder,
    *,
    strip_ndr_auxiliary: bool = False,
) -> None:
    tag = _local(element.tag)
    if tag in SKIP_TAGS:
        return
    if strip_ndr_auxiliary and element.get("data-ndr-auxiliary") == "true":
        return

    block_type = BLOCK_TAGS.get(tag)
    if block_type is not None:
        if block_type is ContentNodeType.SEPARATOR:
            # 分隔符是零长度节点，不往正文里插入原文没有的字符。
            builder.start_block(ContentNodeType.SEPARATOR)
            return
        builder.start_block(block_type, HEADING_LEVELS.get(tag))

    if tag == "br":
        builder.add_text(" ")
        return
    if tag in ("img", "image"):
        src = element.get("src") or element.get("{http://www.w3.org/1999/xlink}href")
        builder.add_image(src, element.get("alt"))
        return
    if tag == "ruby":
        _walk_ruby(element, builder)
        return

    if element.text:
        builder.add_text(element.text)
    for child in list(element):
        _walk(child, builder, strip_ndr_auxiliary=strip_ndr_auxiliary)
        if child.tail:
            builder.add_text(child.tail)


def _walk_ruby(element: ET.Element, builder: _BlockBuilder) -> None:
    base_parts: list[str] = [element.text or ""]
    rt_parts: list[str] = []
    for child in list(element):
        tag = _local(child.tag)
        if tag == "rt":
            rt_parts.append(_iter_text(child))
        elif tag == "rp":
            pass  # 括号回退标记不进正文
        else:
            base_parts.append(_iter_text(child))
        if child.tail:
            base_parts.append(child.tail)

    base = "".join(base_parts)
    rt = " ".join(part.strip() for part in rt_parts if part.strip())
    start, length = builder.add_text(base)
    if length and rt:
        base_text = collapse_whitespace(base)
        builder.add_ruby(start, start + length, base_text, rt)


def _parse_xhtml(  # noqa: ANN001
    data: bytes,
    path: str,
    resolve_image,
    *,
    strip_ndr_auxiliary: bool = False,
) -> _Document:
    document = _Document(path=path)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise EpubError(
            "EPUB 正文文档不是合法 XML/XHTML，已拒绝导入（不做脚本或容错执行）",
            code="EPUB_INVALID_XHTML",
            details={"href": path, "reason": str(exc)},
        ) from exc

    builder = _BlockBuilder(resolve_image)
    _walk(root, builder, strip_ndr_auxiliary=strip_ndr_auxiliary)
    document.blocks = builder.finish()
    for block in document.blocks:
        if block.node_type is ContentNodeType.HEADING and block.text:
            document.headings.append(block.text)
    return document


def _parse_container(section: bytes) -> str:
    try:
        root = ET.fromstring(section)
    except ET.ParseError as exc:
        raise EpubError(
            "META-INF/container.xml 解析失败",
            code="EPUB_INVALID_CONTAINER",
            details={"reason": str(exc)},
        ) from exc
    for element in root.iter():
        if _local(element.tag) == "rootfile":
            container_ns = "{urn:oasis:names:tc:opendocument:xmlns:container}"
            full_path = element.get("full-path") or element.get(f"{container_ns}full-path")
            if full_path:
                return normalize_entry_name(full_path)
    raise EpubError(
        "container.xml 未声明 rootfile",
        code="EPUB_INVALID_CONTAINER",
        details={},
    )


def _parse_opf(data: bytes) -> tuple[dict[str, str], list[_ManifestItem], list[dict[str, Any]]]:  # noqa: E501
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise EpubError(
            "OPF 解析失败",
            code="EPUB_INVALID_OPF",
            details={"reason": str(exc)},
        ) from exc

    metadata: dict[str, str] = {}
    manifest: list[_ManifestItem] = []
    spine: list[dict[str, Any]] = []
    # base_dir 在调用方补齐：这里先按包内绝对路径记录 href。
    for element in root.iter():
        tag = _local(element.tag)
        if tag in ("title", "language", "creator", "publisher", "identifier"):
            text = (element.text or "").strip()
            if text and tag not in metadata:
                metadata[tag] = text
        elif tag == "item":
            manifest.append(
                _ManifestItem(
                    item_id=element.get("id", ""),
                    href=element.get("href", ""),
                    media_type=(element.get("media-type") or "").strip().lower(),
                    properties=tuple((element.get("properties") or "").split()),
                    path="",
                )
            )
        elif tag == "itemref":
            spine.append(
                {
                    "idref": element.get("idref", ""),
                    "linear": (element.get("linear") or "yes").lower(),
                    "properties": (element.get("properties") or "").split(),
                }
            )
    return metadata, manifest, spine


def _parse_toc_nav(data: bytes) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return entries
    for nav in root.iter():
        if _local(nav.tag) != "nav":
            continue
        nav_type = nav.get("{http://www.idpf.org/2007/ops}type") or nav.get("type") or ""
        if nav_type and "toc" not in nav_type:
            continue
        for anchor in nav.iter():
            if _local(anchor.tag) != "a":
                continue
            href = anchor.get("href")
            if not href:
                continue
            entries.append({"href": href, "title": collapse_whitespace(_iter_text(anchor))})
    return entries


def _parse_toc_ncx(data: bytes) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return entries
    for point in root.iter():
        if _local(point.tag) != "navPoint":
            continue
        label = ""
        content = ""
        for child in point.iter():
            tag = _local(child.tag)
            if tag == "text" and not label:
                label = collapse_whitespace(child.text or "")
            elif tag == "content" and not content:
                content = child.get("src") or ""
        if content:
            entries.append({"href": content, "title": label})
    return entries


def parse_epub(
    raw: bytes,
    *,
    limits: EpubLimits | None = None,
    title: str | None = None,
) -> ParsedBook:
    """解析 EPUB 字节流；返回与 TXT 相同的统一文档契约。"""

    limits = limits or EpubLimits()
    archive = _Archive(raw, limits)
    warnings: list[str] = []
    strip_ndr_auxiliary = archive.exists("OEBPS/annotations.json")

    if not archive.exists(CONTAINER_PATH):
        raise EpubError(
            "EPUB 缺少 META-INF/container.xml",
            code="EPUB_INVALID_CONTAINER",
            details={},
        )
    opf_path = _parse_container(archive.read(CONTAINER_PATH))
    if not archive.exists(opf_path):
        raise EpubError(
            "container.xml 指向的 OPF 不存在",
            code="EPUB_MISSING_OPF",
            details={"opf": opf_path},
        )

    opf_dir = posixpath.dirname(opf_path)
    metadata, manifest, spine_refs = _parse_opf(archive.read(opf_path))
    by_id = {item.item_id: item for item in manifest}

    def resolve(
        base_dir: str, href: str, *, allow_external: bool = False
    ) -> tuple[str, str | None] | None:
        try:
            return _resolve_href(base_dir, href)
        except EpubError as exc:
            if allow_external and exc.code == "EPUB_EXTERNAL_REFERENCE":
                return None
            raise

    # manifest 的包内路径
    for item in manifest:
        resolved = resolve(opf_dir, item.href, allow_external=True)
        if resolved is None:
            item.external = True
            warnings.append(f"清单项指向包外资源，已忽略：{item.href}")
            item.path = ""
        else:
            item.path = resolved[0]

    resources: dict[str, ParsedResource] = {}

    def register_resource(path: str, media_type: str) -> str | None:
        if not path or not archive.exists(path):
            return None
        media = (media_type or mimetypes.guess_type(path)[0] or "application/octet-stream").lower()
        if media.startswith(BLOCKED_MEDIA_PREFIXES):
            warnings.append(f"已忽略脚本类资源：{path}")
            return None
        if path in resources:
            return resources[path].resource_id
        payload = archive.read(path)
        resource_id = f"r{len(resources) + 1:04d}"
        resources[path] = ParsedResource(
            resource_id=resource_id,
            media_type=media,
            relative_path=path,
            sha256=hashlib.sha256(payload).hexdigest(),
            byte_size=len(payload),
        )
        return resource_id

    # 先登记非正文清单项（图片、CSS、字体等）
    for item in manifest:
        if not item.path:
            continue
        is_document = item.media_type in DOCUMENT_MEDIA_TYPES or item.media_type.startswith(
            "application/x-dtbncx"
        )
        if is_document:
            continue
        if "nav" in item.properties:
            continue
        if item.media_type.startswith(BLOCKED_MEDIA_PREFIXES):
            warnings.append(f"已忽略脚本类清单项：{item.path}")
            continue
        register_resource(item.path, item.media_type)

    # 目录（TOC）：EPUB3 nav 优先，其次 NCX；与 spine 顺序分离
    toc_entries: list[dict[str, str]] = []
    for item in manifest:
        if "nav" in item.properties and item.path:
            nav_dir = posixpath.dirname(item.path)
            for entry in _parse_toc_nav(archive.read(item.path)):
                resolved = resolve(nav_dir, entry["href"], allow_external=True)
                if resolved is None:
                    continue
                toc_entries.append(
                    {"href": resolved[0], "fragment": resolved[1] or "", "title": entry["title"]}
                )
    if not toc_entries:
        for item in manifest:
            if item.media_type == "application/x-dtbncx+xml" and item.path:
                ncx_dir = posixpath.dirname(item.path)
                for entry in _parse_toc_ncx(archive.read(item.path)):
                    resolved = resolve(ncx_dir, entry["href"], allow_external=True)
                    if resolved is None:
                        continue
                    toc_entries.append(
                        {
                            "href": resolved[0],
                            "fragment": resolved[1] or "",
                            "title": entry["title"],
                        }
                    )

    toc_titles: dict[str, str] = {}
    for entry in toc_entries:
        toc_titles.setdefault(entry["href"], entry["title"])

    spine_items: list[_ManifestItem] = []
    seen_spine: set[str] = set()
    for ref in spine_refs:
        item = by_id.get(ref["idref"])
        if item is None:
            warnings.append(f"spine 引用了不存在的清单项：{ref['idref']}")
            continue
        if item.item_id in seen_spine:
            continue
        seen_spine.add(item.item_id)
        if item.external:
            raise EpubError(
                "EPUB spine 引用了包外文档，无法确定阅读顺序",
                code="EPUB_EXTERNAL_REFERENCE",
                details={"itemref": ref["idref"], "href": item.href},
            )
        if ref["linear"] == "no" and "cover" in item.properties:
            continue
        item.properties = tuple(set(item.properties) | set(ref["properties"]))
        spine_items.append(item)

    if not spine_items:
        raise EpubError(
            "EPUB 的 spine 为空，无法确定阅读顺序",
            code="EPUB_EMPTY_SPINE",
            details={},
        )
    if len(spine_items) > limits.max_spine_items:
        raise EpubError(
            "EPUB spine 条目过多",
            code="EPUB_SPINE_LIMIT",
            details={"spine_items": len(spine_items), "max_spine_items": limits.max_spine_items},
        )

    def make_image_resolver(document_path: str):  # noqa: ANN202
        document_dir = posixpath.dirname(document_path)

        def _resolve_image(src: str) -> tuple[str, str | None, str | None]:
            """返回 ``(status, resource_id, media_type)``，status 为 ok/missing/external。"""

            try:
                resolved = _resolve_href(document_dir, src)
            except EpubError as exc:
                if exc.code == "EPUB_EXTERNAL_REFERENCE":
                    warnings.append(f"已忽略外链图片（不下载外部内容）：{src}")
                    return ("external", None, None)
                raise
            path = resolved[0]
            if not archive.exists(path):
                warnings.append(f"图片资源在包内缺失：{path}")
                return ("missing", None, None)
            media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
            resource_id = register_resource(path, media_type)
            if resource_id is None:
                return ("missing", None, None)
            return ("ok", resource_id, resources[path].media_type)

        return _resolve_image

    # 文本装配：块之间插入合成的 "\n"（synthetic=True）
    canonical_parts: list[str] = []
    canonical_cursor = 0
    nodes: list[ParsedNode] = []
    mappings: list[ParsedMapping] = []
    chapters: list[ParsedChapter] = []
    spine_documents: list[tuple[str, _Document]] = []

    for item in spine_items:
        if not item.path or not archive.exists(item.path):
            warnings.append(f"spine 文档缺失：{item.href}")
            continue
        document = _parse_xhtml(
            archive.read(item.path),
            item.path,
            make_image_resolver(item.path),
            strip_ndr_auxiliary=strip_ndr_auxiliary,
        )
        if not document.blocks:
            warnings.append(f"正文文档没有可渲染内容：{item.path}")
            continue
        spine_documents.append((item.path, document))

    # 先收集所有文本块，便于计算段间分隔符覆盖范围
    text_records: list[dict[str, Any]] = []
    for path, document in spine_documents:
        chapter_ordinal = len(chapters)
        chapter_start = canonical_cursor
        chapter_nodes: list[ParsedNode] = []
        counter = 0
        doc_source_cursor = 0
        for block in document.blocks:
            if block.node_type is ContentNodeType.SEPARATOR:
                chapter_nodes.append(
                    ParsedNode(
                        chapter_ordinal=chapter_ordinal,
                        node_id=f"n{counter:05d}",
                        node_type=ContentNodeType.SEPARATOR,
                        ordinal=counter,
                        start_cp=canonical_cursor,
                        end_cp=canonical_cursor,
                        tree_json=json.dumps({"separator": "hr"}, ensure_ascii=False),
                    )
                )
                counter += 1
                continue
            if block.is_image:
                if block.resource_id:
                    payload: dict[str, Any] = {
                        "resource_id": block.resource_id,
                        "media_type": block.media_type,
                        "alt": block.image_alt or "",
                    }
                    chapter_nodes.append(
                        ParsedNode(
                            chapter_ordinal=chapter_ordinal,
                            node_id=f"n{counter:05d}",
                            node_type=ContentNodeType.IMAGE,
                            ordinal=counter,
                            start_cp=canonical_cursor,
                            end_cp=canonical_cursor,
                            tree_json=json.dumps(payload, ensure_ascii=False),
                        )
                    )
                    counter += 1
                elif block.image_status == "missing":
                    warnings.append(
                        f"图片未登记或缺失：{block.missing_src or '(无 src)'}"
                    )
                # external 已在解析时告警，这里不重复。
                continue

            text = block.text
            if not text:
                continue
            if canonical_cursor > 0:
                canonical_parts.append("\n")
                canonical_cursor += 1
                doc_source_cursor += 1
            start_cp = canonical_cursor
            canonical_parts.append(text)
            canonical_cursor += len(text)
            end_cp = canonical_cursor

            payload = {"text": text}
            if block.node_type is ContentNodeType.HEADING:
                payload["level"] = block.level or 1
            if block.ruby:
                payload["ruby"] = [
                    {
                        "start_cp": start_cp + entry["start"],
                        "end_cp": start_cp + entry["end"],
                        "base": entry["base"],
                        "rt": entry["rt"],
                    }
                    for entry in block.ruby
                ]
            node = ParsedNode(
                chapter_ordinal=chapter_ordinal,
                node_id=f"n{counter:05d}",
                node_type=block.node_type,
                ordinal=counter,
                start_cp=start_cp,
                end_cp=end_cp,
                tree_json=json.dumps(payload, ensure_ascii=False),
            )
            counter += 1
            chapter_nodes.append(node)
            text_records.append(
                {
                    "node": node,
                    "chapter_ordinal": chapter_ordinal,
                    "source_href": path,
                    "source_start_cp": doc_source_cursor,
                    "source_end_cp": doc_source_cursor + len(text),
                }
            )
            doc_source_cursor += len(text)

        chapter_end = canonical_cursor
        chapter_title = toc_titles.get(path) or (
            document.headings[0] if document.headings else None
        )
        chapters.append(
            ParsedChapter(
                ordinal=chapter_ordinal,
                title=chapter_title,
                start_cp=chapter_start,
                end_cp=chapter_end,
                source_href=path,
            )
        )
        nodes.extend(chapter_nodes)

    # 章节范围：段的合成换行归属前一个章节
    for index in range(len(chapters) - 1):
        chapters[index] = ParsedChapter(
            ordinal=chapters[index].ordinal,
            title=chapters[index].title,
            start_cp=chapters[index].start_cp,
            end_cp=chapters[index + 1].start_cp,
            source_href=chapters[index].source_href,
        )

    # source_map：文本块一条记录，覆盖到下一块起点（合成换行标记 synthetic）
    chapter_by_ordinal = {chapter.ordinal: chapter for chapter in chapters}
    for index, record in enumerate(text_records):
        node: ParsedNode = record["node"]
        if index + 1 < len(text_records):
            canonical_end = text_records[index + 1]["node"].start_cp
            synthetic = True
        else:
            canonical_end = node.end_cp
            synthetic = False
        mappings.append(
            ParsedMapping(
                chapter_ordinal=record["chapter_ordinal"],
                node_id=node.node_id,
                ordinal=index,
                canonical_start_cp=node.start_cp,
                canonical_end_cp=canonical_end,
                source_text_start_cp=record["source_start_cp"],
                source_text_end_cp=record["source_end_cp"],
                synthetic=synthetic,
                source_href=record["source_href"],
            )
        )
    _ = chapter_by_ordinal

    canonical_text = "".join(canonical_parts)
    if any(mapping.synthetic for mapping in mappings):
        warnings.append("EPUB 块之间的换行是导入时合成的（source_map 标记为 synthetic）。")

    resources_out = tuple(resources.values())
    if resources_out:
        warnings.append(f"已登记 {len(resources_out)} 个资源。")

    return ParsedBook(
        canonical_text=canonical_text,
        canonical_sha256=hashlib.sha256(canonical_text.encode("utf-8")).hexdigest(),
        canonical_length_cp=len(canonical_text),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        format="EPUB",
        parser_version=EPUB_PARSER_VERSION,
        normalization_version=EPUB_NORMALIZATION_VERSION,
        warnings=tuple(warnings),
        chapters=tuple(chapters),
        nodes=tuple(nodes),
        mappings=tuple(mappings),
        resources=resources_out,
        encoding=None,
        encoding_confidence=None,
        toc=tuple(toc_entries),
        metadata={"title": title or metadata.get("title", "")},
    )
