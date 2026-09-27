"""模型适配器接口与协议能力（DEVELOPMENT.md 5.4）。

T06 只**定义契约**：接口方法、能力声明与结果数据结构。T07 实现真实
``chat-completions-compatible`` 适配器与仅测试用的 FakeProvider。

设计要点：

- 适配器只负责协议与解析，不负责重试/预算/落库（那是 T10 的任务层）。
- ``capabilities`` 必须如实声明，不假设所有兼容服务都支持 json_schema、temperature 等参数。
- 凭据只通过 :class:`~ndr.llm.credentials.CredentialService` 读取，适配器不接触磁盘。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class AdapterCapabilities:
    protocol: str
    supports_json_schema: bool
    supports_json_object: bool
    supports_temperature: bool
    supports_max_tokens: bool
    requires_api_key: bool
    notes: str = ""


CHAT_COMPLETIONS_COMPATIBLE = AdapterCapabilities(
    protocol="chat-completions-compatible",
    # 兼容服务不保证支持严格 json_schema；T07 会按实际提供方文档核对后再打开。
    supports_json_schema=False,
    supports_json_object=True,
    supports_temperature=True,
    supports_max_tokens=True,
    # 本地服务（如自建网关）可能不需要 Key，因此不强制。
    requires_api_key=False,
    notes="OpenAI 兼容 /chat/completions。Base URL 填 API 根路径，适配器自行追加端点。",
)

PROTOCOL_CAPABILITIES: dict[str, AdapterCapabilities] = {
    CHAT_COMPLETIONS_COMPATIBLE.protocol: CHAT_COMPLETIONS_COMPATIBLE,
}

# 仅测试用：页面必须明确标注，真实提供方失败时不得回退到它。
FAKE_PROVIDER_PROTOCOL = "fake-provider"
PROTOCOL_CAPABILITIES[FAKE_PROVIDER_PROTOCOL] = AdapterCapabilities(
    protocol=FAKE_PROVIDER_PROTOCOL,
    supports_json_schema=True,
    supports_json_object=True,
    supports_temperature=False,
    supports_max_tokens=False,
    requires_api_key=False,
    notes="仅供测试/演示；不会发起任何网络请求，页面必须标注。",
)


@dataclass(frozen=True)
class ConnectionTestResult:
    """连接测试结果：既检查鉴权也检查输出可解析。"""

    ok: bool
    protocol: str
    model: str
    detail: str
    latency_ms: int | None = None
    usage: dict[str, Any] | None = None
    adapter: str = "none"


@dataclass(frozen=True)
class TokenEstimate:
    tokens: int
    method: str
    confidence: str  # high / medium / low


@dataclass(frozen=True)
class UsageRecord:
    """用量口径：未知就保持 None，绝不写成 0。"""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost: str | None = None
    currency: str | None = None
    unknown: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class ProviderAdapter(Protocol):
    """适配器接口（T07 实现；T06 的配置页只依赖能力声明）。"""

    name: str
    protocol: str
    capabilities: AdapterCapabilities

    async def test_connection(self) -> ConnectionTestResult:
        """用微型结构化任务检查鉴权与输出可解析；连接成功不代表小说效果已验证。"""

    async def generate_labels(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """按 §4.4 的输出契约生成标注；实现方必须在返回前做 schema 校验。"""

    def estimate_tokens(self, text: str) -> TokenEstimate:
        """保守估算 token（缺少分词器时标注依据与置信度）。"""

    def normalize_usage(self, raw: Mapping[str, Any] | None) -> UsageRecord:
        """把提供方 usage 归一化；缺失时返回 ``unknown=True`` 而不是 0。"""


def resolve_capabilities(protocol: str) -> AdapterCapabilities:
    """按协议名取能力声明；未知协议返回可解释错误。"""

    try:
        return PROTOCOL_CAPABILITIES[protocol]
    except KeyError as exc:
        raise ValueError(f"未知的模型协议：{protocol}") from exc
