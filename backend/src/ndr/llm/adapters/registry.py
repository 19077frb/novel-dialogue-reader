"""按配置构建适配器（T07）。

FakeProvider 只有显式启用（``NDR_ALLOW_FAKE_PROVIDER=1``）时才可用；
真实提供方失败时不会回退到它。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from ...domain.enums import CredentialMode
from ..adapter import FAKE_PROVIDER_PROTOCOL, ProviderAdapter
from ..credentials import CredentialService
from ..errors import ProviderError, ProviderErrorKind
from .chat_completions import DEFAULT_TIMEOUT_SECONDS, ChatCompletionsAdapter
from .fake import FakeProviderAdapter


@dataclass(frozen=True)
class AdapterSpec:
    protocol: str
    base_url: str
    model: str
    params: Mapping[str, Any] | None = None
    credential_mode: CredentialMode = CredentialMode.NONE
    credential_ref: str | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    # 仅测试：FakeProvider 的标注脚本模式（'unknown' / 'deterministic'）与失败脚本。
    fake_labeling_mode: str = "unknown"
    fake_script_mode: str = ""


def build_adapter(
    spec: AdapterSpec,
    credentials: CredentialService,
    *,
    allow_fake_provider: bool = False,
    client: httpx.AsyncClient | None = None,
) -> ProviderAdapter:
    """把配置解析成适配器实例；未知协议或未启用的 FakeProvider 会被明确拒绝。"""

    api_key = None
    if spec.credential_ref:
        api_key = credentials.load(mode=spec.credential_mode, ref=spec.credential_ref)

    if spec.protocol == FAKE_PROVIDER_PROTOCOL:
        if not allow_fake_provider:
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                "FakeProvider 只能在明确启用（NDR_ALLOW_FAKE_PROVIDER=1）时使用。",
                details={"protocol": spec.protocol},
                retryable=False,
            )
        # 仅测试：失败脚本可以来自全局配置，也可以来自该配置的生成参数（便于逐个用例切换）。
        script_mode = spec.fake_script_mode or str((spec.params or {}).get("script") or "")
        return FakeProviderAdapter(
            model=spec.model,
            labeling_mode=spec.fake_labeling_mode,
            script_mode=script_mode,
        )

    if spec.protocol == "chat-completions-compatible":
        return ChatCompletionsAdapter(
            base_url=spec.base_url,
            model=spec.model,
            api_key=api_key,
            params=spec.params,
            timeout_seconds=spec.timeout_seconds,
            client=client,
        )

    raise ProviderError(
        ProviderErrorKind.INVALID_OUTPUT,
        f"未知的模型协议：{spec.protocol}",
        details={"protocol": spec.protocol},
        retryable=False,
    )
