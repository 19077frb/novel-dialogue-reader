"""导出校验。

两层检查，互不替代：

1. **内部检查**（本模块自己实现，永远可用）：zip 结构、`mimetype` 是否为第一项且不压缩、
   是否存在外部引用 / 脚本 / localhost、资源是否闭合、剔除辅助标签后的正文是否与渲染结果一致。
2. **标准检查**：调用 EPUBCheck（`java -jar`，参数数组，不拼接 shell）。
   工具或 Java 缺失时返回 `NOT_RUN` 并说明原因——**绝不假装 PASS**。
"""

from __future__ import annotations

import html as html_module
import posixpath
import re
import shutil
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path

EPUBCHECK_TIMEOUT_SECONDS = 180
EPUBCHECK_VERSION_COMMAND = ("--version",)

_TAG_RE = re.compile(r"<[^>]+>")
_LABEL_RE = re.compile(r'<span class="label"(?:\s[^>]*)?>.*?</span>', re.DOTALL)
_SCRIPT_RE = re.compile(r"<script\b", re.IGNORECASE)
_FETCH_RE = re.compile(r'(?:src|href)\s*=\s*"(?!#)([^"]+)"', re.IGNORECASE)
_URL_CSS_RE = re.compile(r"url\(([^)]+)\)", re.IGNORECASE)
_HOST_RE = re.compile(r"(?:localhost|127\.0\.0\.1)", re.IGNORECASE)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def visible_text(document: str, *, drop_labels: bool = True) -> str:
    """去掉标记（可选去掉编号 span），返回可见文本。"""

    text = _SCRIPT_RE.sub("<script", document)
    if drop_labels:
        text = _LABEL_RE.sub("", text)
    text = _TAG_RE.sub("", text)
    return html_module.unescape(text)


def external_references(document: str) -> list[str]:
    """找出会触发网络/文件系统读取的引用（命名空间 URI 不算）。"""

    found: list[str] = []
    for match in _FETCH_RE.finditer(document):
        target = match.group(1).strip()
        if target.startswith(("http://", "https://", "//", "file:", "ftp:")):
            found.append(target)
    for match in _URL_CSS_RE.finditer(document):
        target = match.group(1).strip().strip("'\"")
        if target.startswith(("http://", "https://", "//", "file:", "ftp:")):
            found.append(target)
    return found


def expected_fragments_from(
    expected_text: str | None = None, expected_fragments: list[str] | None = None
) -> list[str]:
    """要校验的正文片段：按行拆分；标题在导出里可能出现多次（`<title>`/章标题/节点标题）。"""

    if expected_fragments is not None:
        return [item for item in expected_fragments if item and item.strip()]
    if expected_text is None:
        return []
    lines = [line for line in expected_text.splitlines() if line.strip()]
    return lines or [expected_text]


def text_consistency(
    document: str,
    fragments: list[str],
    *,
    document_is_visible_text: bool = False,
) -> tuple[bool, list[str]]:
    """逐段检查正文是否都出现在导出文本里（顺序无关，重复标题不算缺失）。

    EPUB 校验会先逐个 XHTML 文档提取可见文本。此时不能再次按 HTML 解析，
    否则原文中的 ``<书名>`` 会被当作标签，字面量 ``&#9834;`` 也会被二次解码。
    """

    visible = normalize_text(document if document_is_visible_text else visible_text(document))
    missing = [
        fragment
        for fragment in fragments
        if len(normalize_text(fragment)) >= 2 and normalize_text(fragment) not in visible
    ]
    return (not missing), missing[:5]


def check_html(
    document: str,
    *,
    expected_text: str | None = None,
    expected_fragments: list[str] | None = None,
) -> dict:
    references = external_references(document)
    fragments = expected_fragments_from(expected_text, expected_fragments)
    consistent, missing = text_consistency(document, fragments)
    checks = {
        "non_empty": bool(document.strip()),
        "has_charset": "charset" in document.lower(),
        "no_scripts": not _SCRIPT_RE.search(document),
        "no_external_references": not references,
        "no_localhost": not _HOST_RE.search(document),
        "text_consistency": consistent,
    }
    return {
        "format": "html",
        "ok": all(checks.values()),
        "checks": checks,
        "external_references": references[:5],
        "missing_fragments": missing,
        "checked_fragments": len(fragments),
    }


def check_epub(
    data: bytes,
    *,
    expected_text: str | None = None,
    expected_fragments: list[str] | None = None,
) -> dict:
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}
    names: list[str] = []
    resources: set[str] = set()
    referenced: set[str] = set()
    body_text: list[str] = []
    references: list[str] = []
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            checks["non_empty"] = len(names) > 0
            checks["mimetype_first"] = bool(names) and names[0] == "mimetype"
            if names and names[0] == "mimetype":
                checks["mimetype_stored"] = infos[0].compress_type == zipfile.ZIP_STORED
                checks["mimetype_value"] = (
                    archive.read("mimetype") == b"application/epub+zip"
                )
            else:
                checks["mimetype_stored"] = False
                checks["mimetype_value"] = False
            checks["has_container"] = "META-INF/container.xml" in names
            checks["has_opf"] = any(name.endswith(".opf") for name in names)
            checks["has_nav"] = any(name.endswith("nav.xhtml") for name in names)
            for info in infos:
                if info.is_dir():
                    continue
                if info.filename.startswith(("OEBPS/text/", "OEBPS/nav")):
                    document = archive.read(info.filename).decode("utf-8", errors="replace")
                    body_text.append(visible_text(document))
                    references.extend(external_references(document))
                    for match in _FETCH_RE.finditer(document):
                        target = match.group(1).split("#")[0]
                        if target and not target.startswith(("http", "//", "data:", "#")):
                            base = Path(info.filename).parent
                            referenced.add(posixpath.normpath(f"{base.as_posix()}/{target}"))
                elif info.filename.startswith("OEBPS/"):
                    resources.add(info.filename)
                    if info.filename.endswith((".css", ".xhtml", ".opf")):
                        references.extend(
                            external_references(
                                archive.read(info.filename).decode("utf-8", errors="replace")
                            )
                        )
            missing = sorted(ref for ref in referenced if ref not in set(names))
            checks["resource_closure"] = not missing
            details["missing_resources"] = missing[:5]
            checks["no_external_references"] = not references
            checks["no_localhost"] = not _HOST_RE.search("\n".join(body_text))
    except zipfile.BadZipFile:
        checks["readable_zip"] = False
        return {"format": "epub", "ok": False, "checks": checks, "detail": "不是有效的 zip"}
    checks["readable_zip"] = True
    fragments = expected_fragments_from(expected_text, expected_fragments)
    consistent, missing = text_consistency(
        "\n".join(body_text),
        fragments,
        document_is_visible_text=True,
    )
    checks["text_consistency"] = consistent
    return {
        "format": "epub",
        "ok": all(checks.values()),
        "checks": checks,
        "external_references": references[:5],
        "missing_fragments": missing,
        "checked_fragments": len(fragments),
        **details,
    }

def epubcheck_status(path: str | Path, jar_path: str | Path | None) -> dict:
    """运行 EPUBCheck；工具/Java 缺失时返回 `NOT_RUN`（不是 PASS）。"""

    if not jar_path:
        return {"state": "NOT_RUN", "tool": "epubcheck", "detail": "未提供 --epubcheck-jar"}
    jar = Path(jar_path)
    if not jar.exists():
        return {"state": "NOT_RUN", "tool": "epubcheck", "detail": f"找不到 jar：{jar}"}
    java = shutil.which("java")
    if java is None:
        return {
            "state": "NOT_RUN",
            "tool": "epubcheck",
            "detail": "找不到 java（EPUBCheck 需要 JRE）",
            "jar": str(jar),
        }
    try:
        completed = subprocess.run(  # noqa: S603 - 参数数组调用，不拼接 shell
            [java, "-jar", str(jar), str(path)],
            capture_output=True,
            text=True,
            timeout=EPUBCHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"state": "FAIL", "tool": "epubcheck", "detail": "EPUBCheck 超时"}
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode == 0:
        return {
            "state": "PASS",
            "tool": "epubcheck",
            "detail": "EPUBCheck 通过",
            "version": _tool_version(java, jar),
            "exit_code": 0,
        }
    return {
        "state": "FAIL",
        "tool": "epubcheck",
        "detail": f"EPUBCheck 返回 {completed.returncode}",
        "output": output[-2000:],
        "exit_code": completed.returncode,
    }


def _tool_version(java: str, jar: Path) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603 - 参数数组调用
            [java, "-jar", str(jar), *EPUBCHECK_VERSION_COMMAND],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return output.splitlines()[0][:120] if output else None


def validate_export(
    path: str | Path,
    *,
    fmt: str,
    expected_text: str | None = None,
    expected_fragments: list[str] | None = None,
    epubcheck_jar: str | Path | None = None,
) -> dict:
    """内部检查 + 标准检查（标准检查只在 EPUB 且提供了 jar 时运行）。"""

    file_path = Path(path)
    if fmt == "html":
        internal = check_html(
            file_path.read_text(encoding="utf-8"),
            expected_text=expected_text,
            expected_fragments=expected_fragments,
        )
    elif fmt == "epub":
        internal = check_epub(
            file_path.read_bytes(),
            expected_text=expected_text,
            expected_fragments=expected_fragments,
        )
    else:
        return {"ok": False, "detail": f"不支持的格式：{fmt}"}
    result = {
        "internal": internal,
        "standard": {"state": "NOT_APPLICABLE", "tool": None, "detail": "HTML 不需要 EPUBCheck"},
        "tool_versions": {},
    }
    if fmt == "epub":
        result["standard"] = epubcheck_status(file_path, epubcheck_jar)
        if result["standard"].get("version"):
            result["tool_versions"]["epubcheck"] = result["standard"]["version"]
    result["ok"] = bool(internal.get("ok")) and result["standard"]["state"] in {
        "PASS",
        "NOT_RUN",
        "NOT_APPLICABLE",
    }
    return result
