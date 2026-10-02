"""Local restart-only application configuration; never contains model credentials."""

from contextlib import contextmanager

from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..application_settings import (
    ApplicationSettingsOut,
    ApplicationSettingsPatch,
    describe_settings,
    save_settings,
)
from ..domain.enums import ErrorCode
from ..storage.models.jobs import Job
from .errors import ApiError

router = APIRouter(prefix="/settings/application", tags=["settings"])


class ApplicationSettingsEnvelope(BaseModel):
    data: ApplicationSettingsOut


@contextmanager
def _configuration_errors():
    try:
        yield
    except (OSError, ValueError) as exc:
        raise ApiError(ErrorCode.INTERNAL_ERROR, f"应用配置读写失败：{exc}") from exc


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    allowed = {str(request.base_url).rstrip("/"), *request.app.state.settings.cors_origins}
    if origin is not None and origin not in allowed:
        raise ApiError.validation("不允许从其他网站修改应用配置。")


def restart_reason(request: Request) -> str | None:
    if not callable(getattr(request.app.state, "request_restart", None)):
        return "此启动方式不能自动重启，请使用EXE或python -m ndr启动，或保存后手动重启服务。"
    try:
        with request.app.state.session_factory() as session:
            if session.scalar(
                select(Job.id)
                .where(
                    Job.state.in_(["QUEUED", "RUNNING", "PAUSING"]),
                )
                .limit(1)
            ):
                return "仍有排队或运行中的任务，请先停止任务并等待结束，再保存并重启。"
    except SQLAlchemyError:
        return "暂时无法核实任务状态，请保存后手动停止并重启服务。"
    return None


def _envelope(request: Request, data: ApplicationSettingsOut) -> ApplicationSettingsEnvelope:
    data.restart_blocked_reason = restart_reason(request)
    return ApplicationSettingsEnvelope(data=data)


@router.get("", response_model=ApplicationSettingsEnvelope)
def get_application_settings(request: Request) -> ApplicationSettingsEnvelope:
    with _configuration_errors():
        return _envelope(request, describe_settings(request.app.state.settings))


@router.patch("", response_model=ApplicationSettingsEnvelope)
def update_application_settings(
    request: Request,
    payload: ApplicationSettingsPatch,
) -> ApplicationSettingsEnvelope:
    _check_origin(request)
    with _configuration_errors():
        return _envelope(request, save_settings(request.app.state.settings, payload))


@router.post("/save-and-restart", response_model=ApplicationSettingsEnvelope)
def save_and_restart(
    request: Request,
    payload: ApplicationSettingsPatch,
    background: BackgroundTasks,
) -> ApplicationSettingsEnvelope:
    _check_origin(request)
    if getattr(request.app.state, "active_writes", 0) > 1:
        raise ApiError(ErrorCode.RESOURCE_CONFLICT, "还有操作正在保存，请等待完成后再重启。")
    request.app.state.restart_pending = True
    try:
        reason = restart_reason(request)
        if reason:
            raise ApiError(ErrorCode.RESOURCE_CONFLICT, reason)
        with _configuration_errors():
            data = save_settings(request.app.state.settings, payload)
        data.restart_blocked_reason = "正在重启，请等待服务重新启动。"
        background.add_task(request.app.state.request_restart)
        return ApplicationSettingsEnvelope(data=data)
    except Exception:
        request.app.state.restart_pending = False
        raise
