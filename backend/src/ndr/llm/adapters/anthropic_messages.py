"""Non-streaming Anthropic Messages transport for existing JSON tasks.

Reuse HTTP error/timeout handling and private receipts, not OpenAI payloads.
No tools, implicit retries, model-name heuristics or automatic output increases.
"""

from collections.abc import Mapping
from typing import Any

from ..adapter import ANTHROPIC_MESSAGES, UsageRecord
from ..errors import ProviderError, ProviderErrorKind
from .chat_completions import ChatCompletionsAdapter


class AnthropicMessagesAdapter(ChatCompletionsAdapter):
    name = "anthropic-messages"
    protocol = ANTHROPIC_MESSAGES.protocol
    capabilities = ANTHROPIC_MESSAGES
    connection_test_max_tokens = 4096

    @property
    def endpoint(self) -> str:
        suffix = "/messages" if self.base_url.endswith("/v1") else "/v1/messages"
        return self.base_url + suffix

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "anthropic-version": "2023-06-01"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    def _payload(self, messages, *, max_tokens, json_object, max_tokens_override=None):
        params = dict(self._params)
        configured = params.pop("max_tokens", max_tokens)
        limit = max_tokens_override if max_tokens_override is not None else configured
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, "最大输出 Token 必须为正整数")
        system, turns = [], []
        for message in messages:
            role, content = message.get("role"), message.get("content")
            if not isinstance(content, str) or role not in {"system", "user", "assistant"}:
                raise ProviderError(ProviderErrorKind.INVALID_OUTPUT,
                                    "Anthropic 任务仅支持文本消息")
            if role == "system":
                system.append(content)
            else:
                turns.append({"role": role, "content": content})
        if not turns:
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, "缺少用户或助手消息")

        effort = params.pop("reasoning_effort", None)
        if effort is not None and effort not in ("low", "medium", "high", "xhigh", "max"):
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, "不支持的 Anthropic 思考强度")
        params.pop("response_format", None)  # OpenAI-specific; JSON is requested by the prompt.
        stream = params.pop("stream", False)
        if stream:
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT,
                                "当前 Anthropic 任务不支持流式返回")
        thinking = params.pop("thinking", None)
        output_config = params.pop("output_config", {})
        if not isinstance(output_config, Mapping):
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, "output_config 必须为对象")
        output_config = dict(output_config)
        if thinking is not None:
            if not isinstance(thinking, Mapping):
                raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, "thinking 必须为对象")
            thinking = dict(thinking)
            mode = thinking.get("type")
            if mode not in {"disabled", "enabled", "adaptive"}:
                raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, "不支持的 Anthropic 思考模式")
            if mode == "enabled":
                if limit <= 1024:
                    raise ProviderError(ProviderErrorKind.INVALID_OUTPUT,
                                        "Anthropic 手动思考需要最大输出 Token 大于 1024")
                target = {"low": 1024, "medium": 4096, "high": 8192}.get(effort, 2048)
                budget = thinking.get("budget_tokens", min(target, max(1024, limit // 2)))
                if (not isinstance(budget, int) or isinstance(budget, bool)
                        or budget < 1024 or budget >= limit):
                    raise ProviderError(ProviderErrorKind.INVALID_OUTPUT,
                                        "思考预算必须至少 1024 且小于最大输出 Token")
                thinking["budget_tokens"] = budget
            else:
                thinking.pop("budget_tokens", None)
            if mode == "disabled":
                effort = None
                output_config.pop("effort", None)
            else:
                for key in ("temperature", "top_p", "top_k"):
                    params.pop(key, None)
            if mode != "enabled" and effort:
                output_config["effort"] = effort
        elif effort:
            output_config["effort"] = effort

        allowed = {"temperature", "top_p", "top_k", "stop_sequences", "metadata",
                   "service_tier", "cache_control"}
        unsupported = sorted(set(params) - allowed)
        if unsupported:
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT,
                                "Anthropic 不支持这些生成参数：" + "、".join(unsupported))
        request: dict[str, Any] = {**params, "model": self.model, "messages": turns,
                                   "max_tokens": limit, "stream": False}
        if system:
            request["system"] = "\n\n".join(system)
        if thinking is not None:
            request["thinking"] = thinking
        if output_config:
            request["output_config"] = output_config
        return request

    async def _post(self, payload):
        try:
            data, elapsed = await super()._post(payload)
        except ProviderError as exc:
            exc.details = self._private_receipt({}, exc.details)["response"]
            exc.receipt = self._private_receipt({}, exc.receipt)["response"]
            raise
        # Also redact echoed credentials in connection-test diagnostics.
        return self._private_receipt({}, data)["response"], elapsed

    @staticmethod
    def _finish_reason_of(data):
        reason = data.get("stop_reason")
        return "length" if reason == "max_tokens" else reason if isinstance(reason, str) else None

    def _content_of(self, data):
        reason = self._finish_reason_of(data)
        details = {"stop_reason": data.get("stop_reason"), "finish_reason": reason}
        if reason not in {"end_turn", "stop_sequence"}:
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT,
                                "Anthropic 未完整返回正文，请核对输出上限或服务返回的停止原因",
                                details=details)
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, "Anthropic 响应缺少文本内容块",
                                details=details)
        texts = []
        for block in blocks:
            if not isinstance(block, Mapping):
                raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, "Anthropic 内容块结构异常")
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            elif block.get("type") not in {"thinking", "redacted_thinking"}:
                raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, "Anthropic 返回了非文本结果",
                                    details=details)
        text = "".join(texts)
        if not text.strip():
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT,
                                "Anthropic 返回正文为空，思考内容不作为处理结果", details=details)
        return text

    def normalize_usage(self, raw) -> UsageRecord:
        if not isinstance(raw, Mapping):
            return UsageRecord(unknown=True)
        usage = raw.get("usage") if isinstance(raw.get("usage"), Mapping) else raw

        def count(key):
            value = usage.get(key)
            valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
            return value if valid else None

        inputs = count("input_tokens")
        for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
            cached = count(key)
            if key in usage and cached is None:
                inputs = None
                break
            if inputs is not None:
                inputs += cached or 0
        outputs = count("output_tokens")
        total = inputs + outputs if inputs is not None and outputs is not None else None
        return UsageRecord(input_tokens=inputs, output_tokens=outputs, total_tokens=total,
                           unknown=total is None, raw=dict(usage))
