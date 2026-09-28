"""适配器实现与注册。"""

from __future__ import annotations

from .chat_completions import (
    CONNECTION_TEST_MAX_TOKENS,
    DEFAULT_TIMEOUT_SECONDS,
    ChatCompletionsAdapter,
    sanitize,
)
from .fake import FakeProviderAdapter
from .registry import AdapterSpec, build_adapter

__all__ = [
    "CONNECTION_TEST_MAX_TOKENS",
    "DEFAULT_TIMEOUT_SECONDS",
    "AdapterSpec",
    "ChatCompletionsAdapter",
    "FakeProviderAdapter",
    "build_adapter",
    "sanitize",
]
