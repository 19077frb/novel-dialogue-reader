"""模型配置路由（T06）。

- `GET /api/model-profiles`：非敏感配置 + `has_key`，**从不回传密钥**。
- `GET /api/model-profiles/protocols`：协议能力声明（页面据此说明实际能力）。
- `POST /api/model-profiles`：新建配置（可选 api_key 与凭据模式）。
- `PATCH /api/model-profiles/{id}`：字段变更 + keep/replace/remove 密钥三态 + expected_version。
- `DELETE /api/model-profiles/{id}`：删除配置与其凭据引用；被任务引用时 409。

连接测试（`POST /api/model-profiles/test`）属于 T07，本任务不发起任何真实调用。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..domain.common import DataEnvelope
from ..domain.profiles import (
    ModelProfileCreate,
    ModelProfileOut,
    ModelProfilePatch,
    ProtocolCapabilitiesOut,
)
from ..llm.adapter import PROTOCOL_CAPABILITIES
from ..llm.profiles import (
    ProfileView,
    create_profile,
    delete_profile,
    get_profile_or_404,
    list_profiles,
    update_profile,
)
from ..storage.transactions import transaction
from .deps import get_session
from .errors import current_request_id

router = APIRouter(prefix="/model-profiles", tags=["model-profiles"])


def _profile_out(view: ProfileView) -> ModelProfileOut:
    try:
        params = json.loads(view.profile.params_json or "{}")
    except json.JSONDecodeError:
        params = {}
    return ModelProfileOut(
        id=view.profile.id,
        name=view.profile.name,
        protocol=view.profile.protocol,
        base_url=view.profile.base_url,
        model=view.profile.model,
        params=params if isinstance(params, dict) else {},
        credential_mode=view.profile.credential_mode,
        has_key=view.has_key,
        version=view.profile.version,
        created_at=view.profile.created_at,
        updated_at=view.profile.updated_at,
        credential_warning=view.credential_warning,
    )


@router.get(
    "/protocols",
    response_model=DataEnvelope[list[ProtocolCapabilitiesOut]],
    summary="支持的模型协议与能力声明",
)
def list_protocols_route(request: Request) -> DataEnvelope[list[ProtocolCapabilitiesOut]]:
    items = [
        ProtocolCapabilitiesOut(
            protocol=capability.protocol,
            supports_json_schema=capability.supports_json_schema,
            supports_json_object=capability.supports_json_object,
            supports_temperature=capability.supports_temperature,
            supports_max_tokens=capability.supports_max_tokens,
            requires_api_key=capability.requires_api_key,
            notes=capability.notes,
        )
        for capability in PROTOCOL_CAPABILITIES.values()
    ]
    return DataEnvelope(data=items, request_id=current_request_id(request))


@router.get(
    "",
    response_model=DataEnvelope[list[ModelProfileOut]],
    summary="模型配置列表（不含密钥）",
)
def list_profiles_route(
    request: Request,
    session: Session = Depends(get_session),
) -> DataEnvelope[list[ModelProfileOut]]:
    credentials = request.app.state.credentials
    views = list_profiles(session, credentials)
    return DataEnvelope(
        data=[_profile_out(view) for view in views],
        request_id=current_request_id(request),
    )


@router.post(
    "",
    status_code=201,
    response_model=DataEnvelope[ModelProfileOut],
    summary="新建模型配置",
)
def create_profile_route(
    request: Request,
    payload: ModelProfileCreate,
) -> DataEnvelope[ModelProfileOut]:
    factory = request.app.state.session_factory
    credentials = request.app.state.credentials
    with transaction(factory) as session:
        view = create_profile(
            session,
            credentials,
            name=payload.name,
            protocol=payload.protocol,
            base_url=payload.base_url,
            model=payload.model,
            params=payload.params,
            credential_mode=payload.credential_mode,
            api_key=payload.api_key,
        )
        result = _profile_out(view)
    return DataEnvelope(data=result, request_id=current_request_id(request))


@router.patch(
    "/{profile_id}",
    response_model=DataEnvelope[ModelProfileOut],
    summary="更新配置（含密钥 keep/replace/remove）",
)
def update_profile_route(
    request: Request,
    profile_id: str,
    payload: ModelProfilePatch,
) -> DataEnvelope[ModelProfileOut]:
    factory = request.app.state.session_factory
    credentials = request.app.state.credentials
    with transaction(factory) as session:
        profile = get_profile_or_404(session, profile_id)
        view = update_profile(
            session,
            credentials,
            profile,
            name=payload.name,
            protocol=payload.protocol,
            base_url=payload.base_url,
            model=payload.model,
            params=payload.params,
            credential_mode=payload.credential_mode,
            api_key=payload.api_key,
            remove_api_key=payload.remove_api_key,
            expected_version=payload.expected_version,
        )
        result = _profile_out(view)
    return DataEnvelope(data=result, request_id=current_request_id(request))


@router.delete("/{profile_id}", status_code=204, summary="删除配置与凭据引用")
def delete_profile_route(
    request: Request,
    profile_id: str,
) -> Response:
    factory = request.app.state.session_factory
    credentials = request.app.state.credentials
    with transaction(factory) as session:
        profile = get_profile_or_404(session, profile_id)
        delete_profile(session, credentials, profile)
    return Response(status_code=204)
