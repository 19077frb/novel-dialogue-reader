"""Original EPUB cover declarations with a separate body illustration."""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

from .epub_factory import Document, EpubSpec, build_epub

BODY_IMAGE = b"\x89PNG\r\n\x1a\noriginal-body-illustration"
COVER_IMAGE = b"\x89PNG\r\n\x1a\noriginal-cover-illustration"
OPF_NAMESPACE = "http://www.idpf.org/2007/opf"


def build_cover_epub(
    *,
    cover_image_id: str | None = "res2",
    legacy_cover_id: str | None = None,
    guide_href: str | None = None,
    cover_body: str | None = None,
) -> bytes:
    documents = [Document(
        "ch1", "text/chapter.xhtml",
        '<h1>第一章</h1><p>「你好。」</p><p><img src="../images/illustration.png"/></p>',
    )]
    if cover_body is not None:
        documents.append(Document("cover-page", "text/cover.xhtml", cover_body))
    original = build_epub(EpubSpec(
        documents=documents,
        spine=["ch1"],
        resources={
            "images/illustration.png": BODY_IMAGE,
            "images/cover.png": COVER_IMAGE,
            "styles/book.css": b"p { text-align: left; }",
        },
    ))
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(output, "w") as target:
        for info in source.infolist():
            payload = source.read(info)
            if info.filename == "OEBPS/content.opf":
                root = ET.fromstring(payload)
                if cover_image_id is not None:
                    for item in root.findall(f".//{{{OPF_NAMESPACE}}}item"):
                        if item.get("id") == cover_image_id:
                            item.set("properties", "cover-image")
                if legacy_cover_id is not None:
                    metadata = root.find(f"{{{OPF_NAMESPACE}}}metadata")
                    assert metadata is not None
                    ET.SubElement(metadata, f"{{{OPF_NAMESPACE}}}meta", {
                        "name": "cover", "content": legacy_cover_id,
                    })
                if guide_href is not None:
                    guide = ET.SubElement(root, f"{{{OPF_NAMESPACE}}}guide")
                    ET.SubElement(guide, f"{{{OPF_NAMESPACE}}}reference", {
                        "type": "cover", "href": guide_href,
                    })
                payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(info, payload)
    return output.getvalue()
