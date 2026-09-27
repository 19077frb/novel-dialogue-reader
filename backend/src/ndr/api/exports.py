"""导出路由（T15A）：冻结样张、生成、状态与受控下载。

- `POST /api/books/{id}/exports/preview`：冻结快照 + 后端样张（**不调用模型**）。
- `POST /api/books/{id}/exports`：按快照生成 EPUB/HTML（幂等；本地执行，不走网络）。
- `GET /api/exports/{id}`：状态、校验结果与是否可下载。
- `GET /api/exports/{id}/download`：受控下载（正确 MIME 与安全的附件文件名）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..domain.common import DataEnvelope
from ..domain.enums import ErrorCode, ExportFormat, ExportStylePreset
from ..domain.exports import (
    ExportArtifactOut,
    ExportCreateIn,
    ExportPreviewIn,
    ExportPreviewOut,
)
from ..exports.service import (
    create_artifact,
    render_sample,
    run_export,
)
from ..exports.snapshot import freeze_snapshot
from ..ingest.query import active_version, get_book_or_404
from ..storage.models import BookVersion, ExportArtifact, ExportSnapshot
from ..storage.paths import UnsafePathError, resolve_within
from ..storage.transactions import transaction
from .deps import get_session
from .errors import ApiError, current_request_id

book_router = APIRouter(prefix="/books", tags=["exports"])
export_router = APIRouter(prefix="/exports", tags=["exports"])

_MEDIA_TYPES = {
    ExportFormat.EPUB: "application/epub+zip",
    ExportFormat.HTML: "text/html; charset=utf-8",
}


def _artifact_out(artifact: ExportArtifact) -> ExportArtifactOut:
    return ExportArtifactOut(
        id=artifact.id,
        snapshot_id=artifact.snapshot_id,
        format=artifact.format,
        state=artifact.state,
        filename=(
            artifact.relative_path.rsplit("/", 1)[-1] if artifact.relative_path else None
        ),
        relative_path=artifact.relative_path,
        byte_size=artifact.byte_size,
        file_sha256=artifact.file_sha256,
        exporter_version=artifact.exporter_version,
        validation=json.loads(artifact.validation_json or "{}"),
        download_available=bool(
            artifact.relative_path and artifact.state == "COMPLETED"
        ),
        created_at=artifact.created_at.isoformat(),
        updated_at=artifact.updated_at.isoformat(),
    )


def _snapshot_or_404(session: Session, snapshot_id: str) -> ExportSnapshot:
    snapshot = session.get(ExportSnapshot, snapshot_id)
    if snapshot is None:
        raise ApiError.not_found("导出快照不存在", snapshot_id=snapshot_id)
    return snapshot


@book_router.post(
    "/{book_id}/exports/preview",
    response_model=DataEnvelope[ExportPreviewOut],
    summary="冻结导出快照并返回样张（不调用模型）",
)
def export_preview_route(
    request: Request,
    book_id: str,
    payload: ExportPreviewIn,
    session: Session = Depends(get_session),
) -> DataEnvelope[ExportPreviewOut]:
    settings = request.app.state.settings
    book = get_book_or_404(session, book_id)
    version = (
        session.get(BookVersion, payload.book_version_id)
        if payload.book_version_id
        else active_version(session, book)
    )
    if version is None or version.book_id != book.id:
        raise ApiError.validation(
            "书籍版本不存在或不属于该书籍", book_version_id=payload.book_version_id
        )
    try:
        snapshot = freeze_snapshot(
            session,
            book=book,
            version=version,
            chapter_ids=payload.chapter_ids,
            visibility_policy=payload.visibility_policy,
            style=payload.style.preset,
        )
    except ValueError as exc:
        raise ApiError.validation(str(exc)) from exc
    projection = json.loads(snapshot.annotation_projection_json or "{}")
    style = ExportStylePreset(payload.style.preset)
    sample_html, fragments, coverage = render_sample(
        session,
        settings,
        book=book,
        version=version,
        projection_payload=projection,
        style=style,
    )

    data = ExportPreviewOut(
        snapshot_id=snapshot.id,
        book_id=book.id,
        book_version_id=version.id,
        snapshot_hash=snapshot.snapshot_hash,
        source_revision=snapshot.source_revision,
        visibility_policy=payload.visibility_policy,
        selected_chapter_ids=json.loads(snapshot.selected_chapter_ids_json or "[]"),
        counts=projection.get("counts", {}),
        coverage={**coverage, "sample_fragments": len(fragments)},
        warnings=json.loads(snapshot.warnings_json or "[]"),
        sample_html=sample_html,
        sample_fragments=fragments,
        created_at=snapshot.created_at.isoformat(),
    )
    session.commit()
    return DataEnvelope(data=data, request_id=current_request_id(request))


@book_router.post(
    "/{book_id}/exports",
    status_code=201,
    response_model=DataEnvelope[ExportArtifactOut],
    summary="按快照生成 EPUB/HTML（幂等，本地执行）",
)
def create_export_route(
    request: Request,
    book_id: str,
    payload: ExportCreateIn,
) -> DataEnvelope[ExportArtifactOut]:
    settings = request.app.state.settings
    factory = request.app.state.session_factory
    with transaction(factory) as session:
        snapshot = _snapshot_or_404(session, payload.snapshot_id)
        if snapshot.book_id != book_id:
            raise ApiError.validation(
                "快照不属于该书籍", snapshot_id=payload.snapshot_id, book_id=book_id
            )
        artifact, created = create_artifact(
            session,
            snapshot=snapshot,
            fmt=payload.format,
            style=ExportStylePreset(payload.style.preset),
        )
        artifact_id = artifact.id
        reused = not created
    failure: dict | None = None
    if not reused:
        outcome = run_export(factory, settings, artifact_id=artifact_id)
        if outcome.state != "COMPLETED":
            failure = outcome.validation or {}
    with transaction(factory) as session:
        artifact = session.get(ExportArtifact, artifact_id)
        assert artifact is not None
        data = _artifact_out(artifact)
    if failure is not None:
        raise ApiError(
            ErrorCode.INTERNAL_ERROR,
            "导出未通过内部校验",
            details={"artifact_id": artifact_id, "validation": failure},
            status_code=500,
        )
    return DataEnvelope(data=data, request_id=current_request_id(request))


@export_router.get(
    "/{export_id}",
    response_model=DataEnvelope[ExportArtifactOut],
    summary="导出状态、校验结果与下载可用性",
)
def get_export_route(
    request: Request, export_id: str, session: Session = Depends(get_session)
) -> DataEnvelope[ExportArtifactOut]:
    artifact = session.get(ExportArtifact, export_id)
    if artifact is None:
        raise ApiError.not_found("导出产物不存在", export_id=export_id)
    return DataEnvelope(data=_artifact_out(artifact), request_id=current_request_id(request))


@export_router.get("/{export_id}/download", summary="受控下载（从不覆盖原书）")
def download_export_route(
    request: Request, export_id: str, session: Session = Depends(get_session)
) -> FileResponse:
    settings = request.app.state.settings
    artifact = session.get(ExportArtifact, export_id)
    if artifact is None or not artifact.relative_path:
        raise ApiError.not_found("导出文件不存在", export_id=export_id)
    if artifact.state != "COMPLETED":
        raise ApiError(
            ErrorCode.RESOURCE_CONFLICT,
            "导出尚未完成或未通过校验",
            details={"export_id": export_id, "state": artifact.state},
            status_code=409,
        )
    try:
        path = resolve_within(settings, artifact.relative_path)
    except UnsafePathError:
        # 记录被篡改/越界时绝不下发文件，也不把数据目录路径回给调用方（T18 资源边界）
        raise ApiError.not_found("导出文件路径无效", export_id=export_id) from None
    if not path.exists():
        raise ApiError.not_found("导出文件已被移除", export_id=export_id)
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES[artifact.format],
        filename=path.name,
    )
