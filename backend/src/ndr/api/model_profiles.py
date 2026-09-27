"""模型配置路由（T06）。

- `GET /api/model-profiles`：非敏感配置 + `has_key`，**从不回传密钥**。
- `GET /api/model-profiles/protocols`：协议能力声明（页面据此说明实际能力）。
- `POST /api/model-profiles`：新建配置（可选 api_key 与凭据模式）。
- `PATCH /api/model-profiles/{id}`：字段变更 + keep/replace/remove 密钥三态 + expected_version。
- `DELETE /api/model-profiles/{id}`：删除配置与其凭据引用；被任务引用时 409。

连接测试（`POST /api/model-profiles/test`）属于 T07，本任务不发起任何真实调用。
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..domain.common import DataEnvelope
from ..domain.enums import InferenceRunState
from ..domain.profiles import (
    ConnectionTestIn,
    ConnectionTestOut,
    ModelProfileCreate,
    ModelProfileOut,
    ModelProfilePatch,
    ProtocolCapabilitiesOut,
)
from ..llm.adapter import PROTOCOL_CAPABILITIES
from ..llm.adapters import AdapterSpec, build_adapter
from ..llm.errors import ProviderError
from ..llm.profiles import (
    ProfileView,
    create_profile,
    create_profile_draft,
    delete_profile,
    get_profile_or_404,
    list_profiles,
    update_profile,
)
from ..llm.prompts.connection import CONNECTION_PROMPT_VERSION
from ..storage.models import InferenceRun
from ..storage.transactions import transaction
from .deps import get_session
from .errors import ApiError, current_request_id

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

def _fingerprint(payload: dict[str, object]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@router.post(
    "/test",
    response_model=DataEnvelope[ConnectionTestOut],
    summary="连接测试（微型结构化请求；不评估小说效果）",
)
async def test_connection_route(
    request: Request,
    payload: ConnectionTestIn,
) -> DataEnvelope[ConnectionTestOut]:
    """有预算的微型请求：检查鉴权与输出可解析。

    网络调用**不在数据库事务里**：先写入 PREPARED 的推理尝试记录，再调用提供方，
    最后回写结果；usage 缺失时保持 unknown，不写成 0。
    """

    settings = request.app.state.settings
    credentials = request.app.state.credentials
    factory = request.app.state.session_factory

    if not payload.profile_id and payload.draft is None:
        raise ApiError.validation("必须提供 profile_id 或 draft")
    if payload.profile_id and payload.draft is not None:
        raise ApiError.validation("profile_id 与 draft 只能提供一个")

    # 1) 解析配置（短事务）
    temp_ref: str | None = None
    with transaction(factory) as session:
        if payload.profile_id:
            profile = get_profile_or_404(session, payload.profile_id)
            spec = AdapterSpec(
                protocol=profile.protocol,
                base_url=profile.base_url,
                model=profile.model,
                params=json.loads(profile.params_json or "{}"),
                credential_mode=profile.credential_mode,
                credential_ref=profile.credential_ref,
                timeout_seconds=settings.llm_timeout_seconds,
                fake_labeling_mode=settings.fake_provider_labels,
            )
            snapshot = {
                "profile_id": profile.id,
                "name": profile.name,
                "protocol": profile.protocol,
                "base_url": profile.base_url,
                "model": profile.model,
                "credential_mode": profile.credential_mode.value,
                "prompt_version": CONNECTION_PROMPT_VERSION,
                "purpose": "connection-test",
            }
        else:
            draft = payload.draft
            assert draft is not None
            cleaned = create_profile_draft(draft, credentials)
            spec = AdapterSpec(
                protocol=cleaned["protocol"],
                base_url=cleaned["base_url"],
                model=cleaned["model"],
                params=cleaned["params"],
                credential_mode=cleaned["credential_mode"],
                credential_ref=cleaned["credential_ref"],
                timeout_seconds=settings.llm_timeout_seconds,
                fake_labeling_mode=settings.fake_provider_labels,
            )
            temp_ref = cleaned["credential_ref"]
            snapshot = {
                "profile_id": None,
                "name": draft.name,
                "protocol": cleaned["protocol"],
                "base_url": cleaned["base_url"],
                "model": cleaned["model"],
                "credential_mode": cleaned["credential_mode"].value,
                "prompt_version": CONNECTION_PROMPT_VERSION,
                "purpose": "connection-test",
            }

        fingerprint = _fingerprint(
            {
                "protocol": spec.protocol,
                "base_url": spec.base_url,
                "model": spec.model,
                "prompt_version": CONNECTION_PROMPT_VERSION,
            }
        )
        run = InferenceRun(
            job_id=None,
            window_id=None,
            profile_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
            request_fingerprint=fingerprint,
            state=InferenceRunState.PREPARED,
        )
        session.add(run)
        session.flush()
        run_id = run.id

    if spec.protocol == "fake-provider" and not settings.allow_fake_provider:
        raise ApiError.validation(
            "FakeProvider 未启用：请设置 NDR_ALLOW_FAKE_PROVIDER=1（仅用于测试与演示）。",
            protocol=spec.protocol,
        )

    # 2) 网络调用（无数据库事务）
    try:
        adapter = build_adapter(
            spec,
            credentials,
            allow_fake_provider=settings.allow_fake_provider,
        )
        result = await adapter.test_connection()
        error_code = None if result.ok else (result.detail.split(":", 1)[0] or None)
        usage = result.usage
        state = InferenceRunState.SUCCEEDED if result.ok else InferenceRunState.FAILED
    except ProviderError as exc:
        result = None
        error_code = exc.code.value
        usage = None
        state = InferenceRunState.FAILED
        detail = f"{exc.code.value}: {exc.message}"
    finally:
        if temp_ref:
            # 草稿密钥只在本进程内存里存在，用完立即清理两个存储
            credentials.remove(ref=temp_ref)

    # 3) 回写尝试记录（短事务）
    with transaction(factory) as session:
        run = session.get(InferenceRun, run_id)
        assert run is not None
        run.state = state
        run.error_code = error_code
        run.elapsed_ms = result.latency_ms if result is not None else None
        # 未知用量保持 NULL（不写成 0 或 {}）
        run.usage_json = (
            json.dumps(usage, ensure_ascii=False)
            if usage is not None and not usage.get("unknown")
            else None
        )
        if result is not None:
            payload_out = ConnectionTestOut(
                ok=result.ok,
                protocol=result.protocol,
                model=result.model,
                adapter=result.adapter,
                detail=result.detail,
                latency_ms=result.latency_ms,
                usage=usage,
                usage_unknown=bool(usage.get("unknown")) if usage else True,
                error_code=error_code,
                run_id=run_id,
            )
        else:
            payload_out = ConnectionTestOut(
                ok=False,
                protocol=spec.protocol,
                model=spec.model,
                adapter=spec.protocol,
                detail=detail,
                latency_ms=None,
                usage=None,
                usage_unknown=True,
                error_code=error_code,
                run_id=run_id,
            )
    return DataEnvelope(data=payload_out, request_id=current_request_id(request))
