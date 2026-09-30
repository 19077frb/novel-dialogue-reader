"""仅测试/演示用的 FakeProvider。

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
from ..errors import ProviderError, ProviderErrorKind
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
    # 仅测试：让第一次标注调用失败，用于验证限流退避/超时未知/缺 Key 的恢复路径
    script_mode: str = ""
    _script_consumed: bool = field(default=False, repr=False)
    capabilities: AdapterCapabilities = field(
        default_factory=lambda: PROTOCOL_CAPABILITIES[FAKE_PROVIDER_PROTOCOL]
    )
    calls: list[dict[str, Any]] = field(default_factory=list)

    def _maybe_scripted_failure(self, kind: str) -> None:
        """仅测试：按脚本让第一次标注调用失败（真实提供方永远不会走到这里）。"""

        if kind != "labels" or not self.script_mode or self._script_consumed:
            return
        failures = {
            "rate_limited_once": ProviderError(
                ProviderErrorKind.RATE_LIMITED, "测试用：提供方限流一次"
            ),
            "unavailable_once": ProviderError(
                ProviderErrorKind.UNAVAILABLE, "测试用：提供方暂时不可用一次"
            ),
            "timeout_once": ProviderError(ProviderErrorKind.TIMEOUT, "测试用：请求超时一次"),
            "auth_failed_once": ProviderError(
                ProviderErrorKind.AUTH, "测试用：鉴权失败一次", retryable=False
            ),
        }
        error = failures.get(self.script_mode)
        if error is None:
            return
        self._script_consumed = True
        raise error

    def _next(self, *, kind: str, payload: Mapping[str, Any] | None = None) -> Any:
        self.calls.append({"kind": kind, "payload": dict(payload or {})})
        self._maybe_scripted_failure(kind)
        if self.script:
            response = self.script.pop(0)
            if isinstance(response, ProviderError):
                raise response
            return response
        if kind == "roster":
            return self._roster_output()
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
        if self.script_mode == "split_then_merge":
            return self._split_then_merge_labels(payload)
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

    def _split_then_merge_labels(self, payload: Mapping[str, Any] | None) -> dict[str, Any]:
        """仅测试：先判成两个声音，再用**窗口末尾的证据**提议合并。

        证据在窗口最后一句上，所以 `visible_from_cp` 落在窗口末端：初读 horizon 在此之前时，
        投影会把合并还原成两个分组；读到证据之后（或重读）才显示为同一个人。
        """

        targets = [str(item) for item in (payload or {}).get("target_quote_ids", [])]
        if len(targets) < 3:
            return self._deterministic_labels(payload)
        labels = []
        for index, quote_id in enumerate(targets):
            if index == 0:
                labels.append(
                    {
                        "quote_id": quote_id,
                        "scene_ref": "scene_current",
                        "kind": "speech",
                        "assignment": "NEW",
                        "speaker_ref": "new1",
                        "basis": "DIRECT",
                        "evidence_refs": [],
                    }
                )
            elif index == 1:
                labels.append(
                    {
                        "quote_id": quote_id,
                        "scene_ref": "scene_current",
                        "kind": "speech",
                        "assignment": "NEW",
                        "speaker_ref": "new2",
                        "basis": "DIRECT",
                        "evidence_refs": [],
                    }
                )
            else:
                labels.append(
                    {
                        "quote_id": quote_id,
                        "scene_ref": "scene_current",
                        "kind": "speech",
                        "assignment": "EXISTING",
                        "speaker_ref": "new1",
                        "basis": "DIRECT",
                        "evidence_refs": [],
                    }
                )
        evidence_quote_id = targets[-1]  # 末尾的一句：可见时点落在窗口末端
        return {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            "scene_updates": [],
            "gap_decisions": [],
            "new_speakers": [
                {
                    "temp_ref": "new1",
                    "scene_ref": "scene_current",
                    "first_quote_id": targets[0],
                    "description": "第一个声音",
                    "name": "第一个声音",
                    "evidence_refs": [],
                },
                {
                    "temp_ref": "new2",
                    "scene_ref": "scene_current",
                    "first_quote_id": targets[1],
                    "description": "第二个声音",
                    "name": "第二个声音",
                    "evidence_refs": [],
                },
            ],
            "labels": labels,
            "identity_proposals": [
                {
                    "operation": "MERGE",
                    "input_refs": ["new1", "new2"],
                    "output_refs": ["new1"],
                    "evidence_refs": [evidence_quote_id],
                }
            ],
            "needs_context": [],
        }

    def _roster_output(self) -> dict[str, Any]:
        """离线人物名单：仅测试/演示使用。

        非确定性模式不编造人物（返回空名单，界面只能手动添加）；
        ``deterministic`` 模式给出固定的两个候选，其中一个标记为本章第一视角候选，
        让 E2E 能离线走通「分析本章人物 → 确认名单与主人公」这一步。
        """

        if self.labeling_mode != "deterministic":
            return {"schema_version": OUTPUT_SCHEMA_VERSION, "characters": []}
        return {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            "characters": [
                {
                    "temp_ref": "c1",
                    "name": "样例说话人甲",
                    "aliases": [],
                    "description": "确定性测试用人物：本章第一视角候选",
                    "evidence_refs": ["L1"],
                    "pov_candidate": True,
                },
                {
                    "temp_ref": "c2",
                    "name": "样例说话人乙",
                    "aliases": [],
                    "description": "确定性测试用人物：与甲交谈的另一人",
                    "evidence_refs": ["L1"],
                    "pov_candidate": False,
                },
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
                # FakeProvider 的确定性模式显式使用另一条目标作为独立证据，
                # 以测试 ACCEPTED/导出链路；单目标窗口没有独立证据，保持暂定。
                "evidence_refs": [targets[(index + 1) % len(targets)]] if len(targets) > 1 else [],
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
                    "name": "确定性测试说话人",
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

        # 人物名单分析与逐句标注走同一个适配器方法，用 task 区分：
        # 失败脚本只作用于标注调用，人物名单不会“吃掉”限流/超时脚本。
        kind = "roster" if str(payload.get("task") or "") == "roster" else "labels"
        response = self._next(kind=kind, payload=payload)
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
