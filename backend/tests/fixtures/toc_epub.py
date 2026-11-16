"""Original nested publication navigation, including aliases and invalid targets."""

import io
import zipfile

from fixtures.epub_factory import Document, EpubSpec, build_epub


def nested_epub(*, ncx: bool = False) -> bytes:
    spec = EpubSpec(documents=[Document(
        "body", "body.xhtml", '<h1 id="intro">卷首</h1><p>父章正文。</p>'
        '<p id="one">第一节。<span id="two">第二节。</span></p>',
    )], nav=None if ncx else [], ncx=[] if ncx else None)
    if ncx:
        content = '''<ncx><navMap><navPoint><navLabel><text>作品</text></navLabel>
        <navPoint><navLabel><text>第一卷</text></navLabel><content src="body.xhtml#intro"/>
        <navPoint><navLabel><text>第一章</text></navLabel><content src="body.xhtml#one"/>
        <navPoint><navLabel><text>第二节</text></navLabel><content src="body.xhtml#two"/>
        </navPoint></navPoint></navPoint></navPoint></navMap></ncx>'''
        name = "OEBPS/toc.ncx"
    else:
        content = '''<html xmlns:epub="http://www.idpf.org/2007/ops"><body>
        <nav epub:type="toc"><ol><li><span>作品</span><ol>
        <li><a href="body.xhtml#intro">第一卷</a><ol>
        <li><a href="body.xhtml#one">第一章</a><ol>
        <li><a href="body.xhtml#two">第二节</a></li>
        </ol></li></ol></li></ol></li>
        <li><a href="body.xhtml#missing">坏锚点</a></li>
        <li><a href="https://example.invalid/">外链</a></li>
        </ol></nav></body></html>'''
        name = "OEBPS/nav.xhtml"
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(build_epub(spec))) as original, zipfile.ZipFile(output, "w") as dest:
        for item in original.infolist():
            dest.writestr(item, content.encode() if item.filename == name else original.read(item))
    return output.getvalue()
