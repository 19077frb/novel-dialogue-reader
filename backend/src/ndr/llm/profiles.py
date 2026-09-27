"""模型配置服务（T06）。

- 只保存非敏感字段 + ``credential_mode``/``credential_ref``；密钥交给凭据服务。
- Base URL 必须是 API 根路径；把完整业务端点填进来会得到可解释的 422，
  避免“根路径 + 端点”拼出错误地址（DEVELOPMENT.md 5.4）。
- params 里禁止出现 key/token 之类的字段，防止有人把密钥塞进参数表。
- 删除配置时若已被任务引用 → 409。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..api.errors import ApiError
from ..domain.enums import CredentialMode, ErrorCode
from ..storage.models import Job, ModelProfile
from ..storage.transactions import apply_versioned_update
from .adapter import PROTOCOL_CAPABILITIES, resolve_capabilities
from .credentials import CredentialService

SECRET_LIKE_KEYS = {
    "api_key",
    "apikey",
    "key",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "password",
    "secret",
    "client_secret",
}

FULL_ENDPOINT_SUFFIXES = ("/chat/completions", "/completions", "/messages", "/responses")


@dataclass
class ProfileView:
    profile: ModelProfile
    has_key: bool
    credential_warning: str | None = None


def normalize_base_url(raw: str) -> str:
    value = (raw or "").strip().rstrip("/")
    if not value:
        raise ApiError.validation("base_url 不能为空")
    if not value.startswith(("http://", "https://")):
        raise ApiError.validation("base_url 必须以 http:// 或 https:// 开头", base_url=value)
    lowered = value.lower()
    if any(lowered.endswith(suffix) for suffix in FULL_ENDPOINT_SUFFIXES):
        raise ApiError.validation(
            "base_url 应当是 API 根路径，不要包含具体端点；适配器会自行追加相对端点。",
            base_url=value,
            hint="例如填 https://api.example.com/v1，而不是 .../v1/chat/completions",
        )
    return value


def normalize_params(params: dict[str, Any] | None) -> str:
    if not params:
        return "{}"
    offenders = sorted(
        key for key in params if str(key).strip().lower().replace("-", "_") in SECRET_LIKE_KEYS
    )
    if offenders:
        raise ApiError.validation(
            "params 里不能放密钥类字段；请使用 api_key 字段与凭据模式。",
            offending_keys=offenders,
        )
    return json.dumps(params, ensure_ascii=False, sort_keys=True)


def ensure_known_protocol(protocol: str) -> str:
    value = (protocol or "").strip()
    if value not in PROTOCOL_CAPABILITIES:
        raise ApiError.validation(
            "未知的模型协议",
            protocol=value,
            supported=sorted(PROTOCOL_CAPABILITIES),
        )
    resolve_capabilities(value)
    return value


def get_profile_or_404(session: Session, profile_id: str) -> ModelProfile:
    profile = session.get(ModelProfile, profile_id)
    if profile is None:
        raise ApiError.not_found("模型配置不存在", profile_id=profile_id)
    return profile


def _view(
    profile: ModelProfile, credentials: CredentialService, warning: str | None = None
) -> ProfileView:
    ref = profile.credential_ref or CredentialService.reference_for(profile.id)
    return ProfileView(
        profile=profile,
        has_key=credentials.has(mode=profile.credential_mode, ref=ref),
        credential_warning=warning,
    )


def list_profiles(session: Session, credentials: CredentialService) -> list[ProfileView]:
    rows = list(
        session.execute(
            select(ModelProfile).order_by(ModelProfile.created_at, ModelProfile.id)
        ).scalars()
    )
    return [_view(row, credentials) for row in rows]


def create_profile(
    session: Session,
    credentials: CredentialService,
    *,
    name: str,
    protocol: str,
    base_url: str,
    model: str,
    params: dict[str, Any] | None = None,
    credential_mode: CredentialMode = CredentialMode.SESSION,
    api_key: str | None = None,
) -> ProfileView:
    cleaned_name = (name or "").strip()
    if not cleaned_name:
        raise ApiError.validation("name 不能为空")
    cleaned_model = (model or "").strip()
    if not cleaned_model:
        raise ApiError.validation("model 不能为空")

    profile = ModelProfile(
        name=cleaned_name,
        protocol=ensure_known_protocol(protocol),
        base_url=normalize_base_url(base_url),
        model=cleaned_model,
        params_json=normalize_params(params),
        credential_mode=CredentialMode.NONE,
        credential_ref=None,
    )
    session.add(profile)
    try:
        session.flush()
    except IntegrityError as exc:
        raise ApiError(
            ErrorCode.RESOURCE_CONFLICT,
            "同名模型配置已存在",
            details={"name": cleaned_name},
            status_code=409,
        ) from exc

    ref = CredentialService.reference_for(profile.id)
    profile.credential_ref = ref
    mode = credential_mode
    result = credentials.store(mode=mode, ref=ref, secret=api_key) if api_key else None
    if result is not None:
        profile.credential_mode = result.mode
    else:
        # 只声明了模式但没有提供密钥：has_key 会是 false，前端提示需要填 Key。
        profile.credential_mode = mode
    session.flush()
    return _view(profile, credentials, result.warning if result else None)


def update_profile(
    session: Session,
    credentials: CredentialService,
    profile: ModelProfile,
    *,
    name: str | None = None,
    protocol: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    params: dict[str, Any] | None = None,
    credential_mode: CredentialMode | None = None,
    api_key: str | None = None,
    remove_api_key: bool = False,
    expected_version: int | None = None,
) -> ProfileView:
    changes: dict[str, Any] = {}
    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise ApiError.validation("name 不能为空")
        changes["name"] = cleaned
    if protocol is not None:
        changes["protocol"] = ensure_known_protocol(protocol)
    if base_url is not None:
        changes["base_url"] = normalize_base_url(base_url)
    if model is not None:
        cleaned_model = model.strip()
        if not cleaned_model:
            raise ApiError.validation("model 不能为空")
        changes["model"] = cleaned_model
    if params is not None:
        changes["params_json"] = normalize_params(params)

    try:
        apply_versioned_update(
            session, profile, expected_version=expected_version, changes=changes
        )
    except IntegrityError as exc:
        raise ApiError(
            ErrorCode.RESOURCE_CONFLICT,
            "同名模型配置已存在",
            details={"name": changes.get("name")},
            status_code=409,
        ) from exc

    ref = profile.credential_ref or CredentialService.reference_for(profile.id)
    profile.credential_ref = ref
    warning: str | None = None

    if remove_api_key:
        credentials.remove(ref=ref)
        profile.credential_mode = CredentialMode.NONE
        session.flush()
    elif api_key:
        mode = credential_mode or (
            profile.credential_mode
            if profile.credential_mode is not CredentialMode.NONE
            else CredentialMode.SESSION
        )
        result = credentials.store(mode=mode, ref=ref, secret=api_key)
        profile.credential_mode = result.mode
        warning = result.warning
        session.flush()
    elif credential_mode is not None and credential_mode is not profile.credential_mode:
        # 只改模式而不提供密钥：只有从“无密钥”切到某种模式时才有意义
        if profile.credential_mode is not CredentialMode.NONE:
            raise ApiError.validation(
                "更换凭据模式需要同时提供新的 api_key，或使用 remove_api_key 清除。",
                credential_mode=credential_mode.value,
            )
        profile.credential_mode = credential_mode
        session.flush()

    return _view(profile, credentials, warning)


def is_profile_referenced(session: Session, profile_id: str) -> list[str]:
    """返回引用该配置的任务 ID（用于删除前检查）。"""

    rows = session.execute(
        select(Job.id).where(Job.profile_snapshot_json.like(f"%{profile_id}%"))
    ).scalars()
    return [str(job_id) for job_id in rows]


def delete_profile(session: Session, credentials: CredentialService, profile: ModelProfile) -> None:
    referencing = is_profile_referenced(session, profile.id)
    if referencing:
        raise ApiError(
            ErrorCode.RESOURCE_CONFLICT,
            "该配置已被任务引用，不能删除",
            details={"profile_id": profile.id, "job_ids": referencing},
            status_code=409,
        )
    ref = profile.credential_ref or CredentialService.reference_for(profile.id)
    credentials.remove(ref=ref)
    session.delete(profile)
    session.flush()
