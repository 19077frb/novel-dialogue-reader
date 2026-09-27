"""仅测试/演示用的 FakeProvider（T07）。

- **不发任何网络请求**；只能通过显式开关启用（见 registry 与 NDR_ALLOW_FAKE_PROVIDER）。
- 可以按脚本返回：正常 JSON、坏 JSON、错 ID、上游错误或超时，用于验证调用方的校验与重试策略。
- 真实提供方失败时绝不回退到它（调用方只拿到真实错误）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..adapter import (
    FAKE_PROVIDER_PROTOCOL,
    PROTOCOL_CAPABILITIES,
    AdapterCapabilities,
    ConnectionTestResult,
    TokenEstimate,
    UsageRecord,
)
from ..errors import ProviderError
from ..prompts.connection import build_connection_messages
from ..schemas import OUTPUT_SCHEMA_VERSION

ScriptedResponse = str | Mapping[str, Any] | ProviderError


@dataclass
class FakeProviderAdapter:
    name: str = "fake-provider"
    protocol: str = FAKE_PROVIDER_PROTOCOL
    model: str = "fake-model"
    script: list[ScriptedResponse] = field(default_factory=list)
    capabilities: AdapterCapabilities = field(
        default_factory=lambda: PROTOCOL_CAPABILITIES[FAKE_PROVIDER_PROTOCOL]
    )
    calls: list[dict[str, Any]] = field(default_factory=list)

    def _next(self, *, kind: str, payload: Mapping[str, Any] | None = None) -> Any:
        self.calls.append({"kind": kind, "payload": dict(payload or {})})
        if self.script:
            response = self.script.pop(0)
            if isinstance(response, ProviderError):
                raise response
            return response
        if kind == "connection":
            return json.dumps(
                {
                    "schema_version": OUTPUT_SCHEMA_VERSION,
                    "scene_updates": [],
                    "gap_decisions": [],
                    "new_speakers": [],
                    "labels": [],
                    "identity_proposals": [],
                    "needs_context": [],
                },
                ensure_ascii=False,
            )
        # 标注：把请求里的目标对白都标成 UNKNOWN（不假装知道说话人）
        targets = list((payload or {}).get("target_quote_ids", []))
        return {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            "labels": [
                {
                    "quote_id": quote_id,
                    "scene_ref": "scene_current",
                    "kind": "speech",
                    "assignment": "UNKNOWN",
                    "speaker_ref": None,
                    "basis": "INSUFFICIENT",
                    "evidence_refs": [],
                }
                for quote_id in targets
            ],
        }

    async def test_connection(self) -> ConnectionTestResult:
        payload = {"messages": build_connection_messages()}
        try:
            self._next(kind="connection", payload=payload)
        except ProviderError as exc:
            return ConnectionTestResult(
                ok=False,
                protocol=self.protocol,
                model=self.model,
                detail=f"{exc.code.value}: {exc.message}",
                adapter=self.name,
            )
        return ConnectionTestResult(
            ok=True,
            protocol=self.protocol,
            model=self.model,
            detail="FakeProvider 连接成功（仅测试用，未访问任何网络）。",
            latency_ms=0,
            adapter=self.name,
            usage=self.normalize_usage(None).as_dict(),
        )

    async def generate_labels(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        text = self._next(kind="labels", payload=payload)
        text = self._next(kind="labels", payload=payload)
        parsed = json.loads(text) if isinstance(text, str) else dict(text)
        parsed.setdefault("_usage", self.normalize_usage(None).as_dict())
        return parsed

    def estimate_tokens(self, text: str) -> TokenEstimate:
        return TokenEstimate(tokens=max(1, len(text) // 2), method="fake", confidence="low")

    def normalize_usage(self, raw: Mapping[str, Any] | None) -> UsageRecord:
        # FakeProvider 没有真实 usage：如实标记未知，而不是伪造 0。
        return UsageRecord(unknown=True)
