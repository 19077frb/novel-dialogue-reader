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
    # 可控 usage：默认 None（未知，不写 0）；测试可注入 {"input_tokens": ...} 验证结算
    usage: Mapping[str, Any] | None = None
    # 仅测试：'unknown'（默认，全部标为 UNKNOWN）或 'deterministic'（确定性建立新分组）
    labeling_mode: str = "unknown"
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
        if self.labeling_mode == "deterministic":
            return self._deterministic_labels(payload)
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

    def _deterministic_labels(self, payload: Mapping[str, Any] | None) -> dict[str, Any]:
        """离线确定性脚本：第一句建立新分组，其余沿用同一分组（DIRECT 证据 → ACCEPTED）。

        这是**测试/演示专用**的假结果，只在显式开启 FakeProvider（NDR_ALLOW_FAKE_PROVIDER=1）
        且显式设置确定性模式时生效；真实提供方永远不会走到这里。
        """

        targets = [str(item) for item in (payload or {}).get("target_quote_ids", [])]
        labels = [
            {
                "quote_id": quote_id,
                "scene_ref": "scene_current",
                "kind": "speech",
                "assignment": "NEW" if index == 0 else "EXISTING",
                "speaker_ref": "new1",
                "basis": "DIRECT",
                "evidence_refs": [],
            }
            for index, quote_id in enumerate(targets)
        ]
        new_speakers = (
            [
                {
                    "temp_ref": "new1",
                    "scene_ref": "scene_current",
                    "first_quote_id": targets[0],
                    "description": "确定性测试说话人",
                    "evidence_refs": [],
                }
            ]
            if targets
            else []
        )
        return {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            "scene_updates": [],
            "gap_decisions": [],
            "new_speakers": new_speakers,
            "labels": labels,
            "identity_proposals": [],
            "needs_context": [],
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
        """返回脚本里的响应；字符串按模型原始输出原样返回（用于模拟坏 JSON）。"""

        response = self._next(kind="labels", payload=payload)
        if isinstance(response, str):
            return response  # type: ignore[return-value]
        parsed = dict(response)
        parsed.setdefault("_usage", self.normalize_usage(None).as_dict())
        return parsed

    def estimate_tokens(self, text: str) -> TokenEstimate:
        return TokenEstimate(tokens=max(1, len(text) // 2), method="fake", confidence="low")

    def normalize_usage(self, raw: Mapping[str, Any] | None) -> UsageRecord:
        payload = raw or self.usage
        if not payload:
            # 没有真实 usage 时如实标记未知，而不是伪造 0。
            return UsageRecord(unknown=True)
        return UsageRecord(
            input_tokens=payload.get("input_tokens"),
            output_tokens=payload.get("output_tokens"),
            total_tokens=payload.get("total_tokens"),
            unknown=False,
        )
