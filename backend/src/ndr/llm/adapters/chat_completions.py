"""OpenAI 兼容的 chat-completions 适配器。

- Base URL 是 API 根路径，适配器自行追加 ``/chat/completions``。
- 只用服务端 httpx 客户端；支持本地地址、无 Key 服务与显式超时。
- 上游错误映射为稳定业务错误码，并做**脱敏**（不记录 Authorization、不整段透传响应）。
- 不在这里做重试/预算/落库：那是任务层的职责；本类只负责一次调用。
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

import httpx

from ...context.budget import DEFAULT_ESTIMATOR
from ...context.budget import estimate_tokens as estimate_tokens_with_policy
from ...domain.enums import ErrorCode
from ..adapter import (
    CHAT_COMPLETIONS_COMPATIBLE,
    AdapterCapabilities,
    ConnectionTestResult,
    TokenEstimate,
    UsageRecord,
)
from ..errors import InvalidModelOutput, ProviderError, ProviderErrorKind
from ..prompts.connection import CONNECTION_PROMPT_VERSION, build_connection_messages
from ..validation import load_json_object, parse_output


def _is_cjk_like(char: str) -> bool:
    """CJK 文字、假名、谚文、CJK 标点与全角字符：中文文本里大约 1 字 ≈ 1 token。"""

    code = ord(char)
    return (
        0x3400 <= code <= 0x9FFF
        or 0x3040 <= code <= 0x30FF
        or 0x3000 <= code <= 0x303F
        or 0xAC00 <= code <= 0xD7AF
        or 0xFF00 <= code <= 0xFFEF
        or 0x20000 <= code <= 0x2FA1F
    )


DEFAULT_TIMEOUT_SECONDS = 30.0
# 连接测试要把「原样回显的小 JSON」装下：64 会让部分模型（尤其带缩进或代码块时）被截断成坏 JSON。
CONNECTION_TEST_MAX_TOKENS = 256
ERROR_BODY_SNIPPET_CHARS = 200

_SENSITIVE_MARKERS = ("authorization", "api_key", "apikey", "bearer ", "sk-")


def sanitize(text: str, *, limit: int = ERROR_BODY_SNIPPET_CHARS) -> str:
    """脱敏：截断并去掉可能的密钥痕迹。绝不记录请求头。"""

    cleaned = text.replace("\n", " ").strip()
    lowered = cleaned.lower()
    for marker in _SENSITIVE_MARKERS:
        position = lowered.find(marker)
        if position >= 0:
            cleaned = cleaned[:position] + "[已脱敏]"
            lowered = cleaned.lower()
    return cleaned[:limit]


def _status_to_kind(status_code: int) -> ProviderErrorKind:
    if status_code in (401, 403):
        return ProviderErrorKind.AUTH
    if status_code == 404:
        return ProviderErrorKind.MODEL_NOT_FOUND
    if status_code == 429:
        return ProviderErrorKind.RATE_LIMITED
    if status_code >= 500:
        return ProviderErrorKind.UNAVAILABLE
    return ProviderErrorKind.INVALID_OUTPUT


class ChatCompletionsAdapter:
    """一次调用的适配器实现（无内部重试）。"""

    name = "chat-completions"
    protocol = CHAT_COMPLETIONS_COMPATIBLE.protocol
    capabilities: AdapterCapabilities = CHAT_COMPLETIONS_COMPATIBLE

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        params: Mapping[str, Any] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        params = dict(params or {})
        # 本地参数：per-profile 超时（推理模型/大窗口可能远超默认 30 秒）。不进入请求体。
        local_timeout = params.pop("timeout_seconds", None)
        if (
            isinstance(local_timeout, (int, float))
            and not isinstance(local_timeout, bool)
            and float(local_timeout) > 0
        ):
            timeout_seconds = float(local_timeout)
        self._params = params
        self._timeout = timeout_seconds
        self._client = client
        self.prompt_version = CONNECTION_PROMPT_VERSION

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _payload(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        json_object: bool,
        max_tokens_override: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        params = dict(self._params)
        if self.capabilities.supports_max_tokens:
            if isinstance(max_tokens_override, int) and max_tokens_override > 0:
                # 显式覆盖优先于 profile 的 max_tokens（输出被截断时重试会提高上限）
                payload["max_tokens"] = int(max_tokens_override)
                params.pop("max_tokens", None)
            else:
                payload["max_tokens"] = int(params.pop("max_tokens", max_tokens))
        if self.capabilities.supports_temperature and "temperature" in params:
            payload["temperature"] = params.pop("temperature")
        if self.capabilities.supports_json_object and json_object:
            payload["response_format"] = {"type": "json_object"}
        # 其余参数按提供方原样带上（不假设服务支持哪些）
        payload.update(params)
        return payload

    async def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owns_client = self._client is None
        started = time.perf_counter()
        try:
            response = await client.post(self.endpoint, json=payload, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise ProviderError(
                ProviderErrorKind.TIMEOUT,
                f"请求超时（{self._timeout} 秒）",
                details={"endpoint": self.endpoint},
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderError(
                ProviderErrorKind.UNAVAILABLE,
                f"无法连接提供方：{type(exc).__name__}",
                details={"endpoint": self.endpoint},
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            kind = _status_to_kind(response.status_code)
            snippet = sanitize(response.text)
            raise ProviderError(
                kind,
                f"提供方返回 {response.status_code}",
                details={"status_code": response.status_code, "body": snippet},
            )
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                "提供方响应不是 JSON",
                details={"body": sanitize(response.text)},
            ) from exc
        if not isinstance(data, dict):
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT, "提供方响应顶层不是对象"
            )
        return data, elapsed_ms

    @staticmethod
    def _finish_reason_of(data: Mapping[str, Any]) -> str | None:
        """提供方的结束原因；`length` 表示输出被 max_tokens 截断。"""

        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            reason = choices[0].get("finish_reason")
            return reason if isinstance(reason, str) and reason else None
        return None

    @staticmethod
    def _response_snippet(data: Mapping[str, Any]) -> str:
        """脱敏后的响应片段：真实提供方出错时，这是唯一能定位问题的线索。"""

        try:
            return sanitize(json.dumps(data, ensure_ascii=False))
        except (TypeError, ValueError):
            return ""

    @classmethod
    def _content_of(cls, data: Mapping[str, Any]) -> str:
        snippet = cls._response_snippet(data)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT, "响应缺少 choices", details={"body": snippet}
            )
        first = choices[0]
        if not isinstance(first, Mapping):
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                "choices[0] 结构异常",
                details={"body": snippet},
            )
        message = first.get("message")
        if not isinstance(message, Mapping):
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                "choices[0].message 缺失",
                details={"body": snippet},
            )
        content = message.get("content")
        if isinstance(content, list):
            # 某些兼容服务返回分片数组
            text = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, Mapping)
            )
        else:
            text = content if isinstance(content, str) else ""
        if not text.strip():
            # 空内容的真实原因要写在错误里：finish_reason / reasoning_content 都是常见线索
            finish_reason = first.get("finish_reason")
            reasoning = message.get("reasoning_content")
            details: dict[str, Any] = {"body": snippet}
            hint = "模型返回空内容"
            if isinstance(finish_reason, str) and finish_reason:
                details["finish_reason"] = finish_reason
            if isinstance(reasoning, str) and reasoning.strip():
                details["reasoning_chars"] = len(reasoning)
                details["reasoning_snippet"] = sanitize(reasoning, limit=120)
                hint += (
                    "：正文为空，但 reasoning_content 有内容（推理内容占了输出；"
                    "可换非推理模型，或按网关文档关闭思考/提高输出上限）"
                )
            if finish_reason == "length":
                hint += (
                    "；finish_reason=length 表示输出预算被用完（推理模型常把 token 花在思考上），"
                    "可在「模型配置 → 生成参数」里提高 max_tokens（例如 {\"max_tokens\": 4000}）"
                )
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, hint, details=details)
        return text

    async def test_connection(self) -> ConnectionTestResult:
        """微型结构化任务：检查鉴权 + 输出可解析（不代表小说效果）。"""

        payload = self._payload(
            build_connection_messages(), max_tokens=CONNECTION_TEST_MAX_TOKENS, json_object=True
        )
        try:
            data, elapsed_ms = await self._post(payload)
        except ProviderError as exc:
            return ConnectionTestResult(
                ok=False,
                protocol=self.protocol,
                model=self.model,
                detail=f"{exc.code.value}: {exc.message}",
                adapter=self.name,
                usage=None,
            )

        usage = self.normalize_usage(data.get("usage")).as_dict()
        try:
            text = self._content_of(data)
        except ProviderError as exc:
            detail = f"{exc.code.value}: {exc.message}"
            if exc.kind is ProviderErrorKind.INVALID_OUTPUT:
                # 空 content / 结构异常时把响应片段带出来：真实提供方需要看原始返回才能定位
                snippet = sanitize(json.dumps(data, ensure_ascii=False))
                detail = f"{detail}；响应片段：{snippet or '（空）'}"
            return ConnectionTestResult(
                ok=False,
                protocol=self.protocol,
                model=self.model,
                detail=detail,
                latency_ms=elapsed_ms,
                adapter=self.name,
                usage=usage,
            )

        try:
            # 与标注链路共用同一种解析：整段 JSON 或整段 ```json 代码块都接受
            parse_output(text)
        except InvalidModelOutput as exc:
            issues = (exc.details or {}).get("issues") or []
            summary = "；".join(
                (".".join(str(part) for part in issue.get("loc", ())) or "root")
                + f": {issue.get('msg', '')}"
                for issue in issues[:3]
            )
            finish_reason = self._finish_reason_of(data)
            parts = [f"{ErrorCode.INVALID_MODEL_OUTPUT.value}: {exc.message}"]
            if finish_reason:
                parts.append(f"finish_reason={finish_reason}")
            if summary:
                parts.append(f"问题：{summary}")
            parts.append(f"原始输出片段：{sanitize(text, limit=200) or '（空）'}")
            return ConnectionTestResult(
                ok=False,
                protocol=self.protocol,
                model=self.model,
                detail="；".join(parts),
                latency_ms=elapsed_ms,
                adapter=self.name,
                usage=usage,
            )
        usage = self.normalize_usage(data.get("usage"))
        return ConnectionTestResult(
            ok=True,
            protocol=self.protocol,
            model=self.model,
            detail="连接成功，且返回结构可解析（未评估小说标注效果）。",
            latency_ms=elapsed_ms,
            adapter=self.name,
            usage=usage.as_dict(),
        )

    async def generate_labels(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """按 §4.4 输出契约生成标注；返回**未校验**的原始对象，校验由 validation 负责。"""

        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, "缺少 messages")
        override = payload.get("max_tokens_override")
        request = self._payload(
            [dict(message) for message in messages],
            max_tokens=int(payload.get("max_tokens", CONNECTION_TEST_MAX_TOKENS)),
            json_object=bool(payload.get("json_object", True)),
            max_tokens_override=override if isinstance(override, int) else None,
        )
        data, _elapsed = await self._post(request)
        usage = self.normalize_usage(data.get("usage")).as_dict()
        try:
            text = self._content_of(data)
        except ProviderError as exc:
            # 请求已得到提供方响应时，内容异常也要保留真实 usage，供任务层结算。
            exc.details.setdefault("usage", usage)
            raise
        try:
            # 与连接测试同一套解析：整段 JSON 或整段代码块都接受
            parsed = load_json_object(text)
        except InvalidModelOutput as exc:
            finish_reason = self._finish_reason_of(data)
            message = exc.message
            if finish_reason == "length":
                message += "（提供方因输出上限被截断；重试会自动提高 max_tokens）"
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                message,
                details={
                    "body": sanitize(text),
                    "usage": usage,
                    "finish_reason": finish_reason,
                    **(exc.details or {}),
                },
            ) from exc
        parsed.setdefault("_usage", self.normalize_usage(data.get("usage")).as_dict())
        return parsed

    def estimate_tokens(self, text: str) -> TokenEstimate:
        """保守估算：CJK 约 1 token/字，其它字符约 1 token/4 字符（依据为启发式，置信度低）。"""

        # 与上下文预算共用同一口径（见 context/budget.py），避免两处估算漂移。
        tokens = estimate_tokens_with_policy(text)
        return TokenEstimate(
            tokens=tokens,
            method=DEFAULT_ESTIMATOR.method,
            confidence=DEFAULT_ESTIMATOR.confidence,
        )

    def normalize_usage(self, raw: Mapping[str, Any] | None) -> UsageRecord:
        if not raw:
            # 未知用量：保持 None，绝不写成 0
            return UsageRecord(unknown=True)
        payload = raw.get("usage") if isinstance(raw.get("usage"), Mapping) else raw

        def _pick(*names: str) -> int | None:
            for name in names:
                value = payload.get(name) if isinstance(payload, Mapping) else None
                if isinstance(value, int):
                    return value
            return None

        input_tokens = _pick("prompt_tokens", "input_tokens")
        output_tokens = _pick("completion_tokens", "output_tokens")
        total_tokens = _pick("total_tokens")
        if total_tokens is None and (input_tokens is not None or output_tokens is not None):
            total_tokens = (input_tokens or 0) + (output_tokens or 0)
        unknown = input_tokens is None and output_tokens is None and total_tokens is None
        return UsageRecord(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            unknown=unknown,
            raw=dict(payload) if isinstance(payload, Mapping) else {},
        )
