"""受控资源读取（T03）。

资源只来自数据库登记项，并只从该书籍版本的源文件（EPUB 包）内读取：
- 条目名必须与登记的相对路径一致，且经过与导入相同的越界校验；
- 不执行、不解析、不下载任何外部内容；
- 脚本类媒体类型不提供下载（避免把可执行内容当资源发给浏览器）。
"""

from __future__ import annotations

import io
import zipfile

from ..api.errors import ApiError
from ..config import Settings
from ..domain.enums import ErrorCode
from ..storage.models import Book, BookVersion, Resource
from ..storage.paths import UnsafePathError, resolve_within
from .epub import BLOCKED_MEDIA_PREFIXES, normalize_entry_name

RESOURCE_MEDIA_TYPE_FALLBACK = "application/octet-stream"


def get_resource_or_404(session, book: Book, resource_id: str) -> tuple[Resource, BookVersion]:  # noqa: ANN001
    """按资源 ID 在当前活动版本内查找登记资源。"""

    if book.active_version_id is None:
        raise ApiError.not_found("书籍还没有可用版本", book_id=book.id)
    resource = (
        session.query(Resource)
        .filter(
            Resource.book_version_id == book.active_version_id,
            Resource.resource_id == resource_id,
        )
        .one_or_none()
    )
    if resource is None:
        raise ApiError.not_found(
            "资源不存在或未登记",
            book_id=book.id,
            resource_id=resource_id,
        )
    version = session.get(BookVersion, book.active_version_id)
    if version is None:
        raise ApiError.not_found("书籍版本不存在", book_id=book.id)
    return resource, version


def read_resource_bytes(
    settings: Settings, book: Book, version: BookVersion, resource: Resource
) -> bytes:
    """从该版本的源文件中读取资源字节；文件缺失时返回可解释错误。"""

    media_type = (resource.media_type or "").lower()
    if media_type.startswith(BLOCKED_MEDIA_PREFIXES):
        raise ApiError(
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            "该资源是可执行脚本类型，不提供下载",
            details={"resource_id": resource.resource_id, "media_type": resource.media_type},
            status_code=415,
        )

    if not version.source_path:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "该版本没有可读取的源文件",
            details={"book_version_id": version.id},
            status_code=409,
        )
    try:
        source_path = resolve_within(settings, version.source_path)
    except UnsafePathError:
        # 源文件路径越界（例如库被手动改过）：拒绝读取，不回显磁盘路径（T18 资源边界）
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "该版本的源文件路径无效",
            details={"book_version_id": version.id},
            status_code=409,
        ) from None
    if not source_path.exists():
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "源文件缺失，请重新导入该书籍",
            details={"book_version_id": version.id},
            status_code=409,
        )

    if source_path.suffix.lower() != ".epub":
        # TXT 版本没有包内资源（后续格式在各自任务中实现）。
        raise ApiError.not_found(
            "该版本没有登记资源",
            book_id=book.id,
            resource_id=resource.resource_id,
        )

    entry_name = normalize_entry_name(resource.relative_path)
    try:
        with zipfile.ZipFile(source_path) as archive:
            if entry_name not in archive.namelist():
                raise ApiError.not_found(
                    "资源在源文件中缺失",
                    resource_id=resource.resource_id,
                    entry=entry_name,
                )
            info = archive.getinfo(entry_name)
            if info.flag_bits & 0x1:
                raise ApiError(
                    ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                    "资源条目被加密，无法读取",
                    details={"resource_id": resource.resource_id},
                    status_code=415,
                )
            with archive.open(info) as handle:
                return handle.read()
    except zipfile.BadZipFile as exc:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "书籍源文件已损坏",
            details={"book_id": book.id, "reason": str(exc)},
            status_code=409,
        ) from exc


def open_zip_from_bytes(payload: bytes) -> zipfile.ZipFile:  # pragma: no cover - 供测试/工具使用
    return zipfile.ZipFile(io.BytesIO(payload))
