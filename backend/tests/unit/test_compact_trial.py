import asyncio

import pytest

from ndr.evaluation.compact import Candidate, CompactTask
from ndr.evaluation.compact_trial import run_trial
from ndr.llm.errors import ProviderError, ProviderErrorKind


class Adapter:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.requests = []

    async def generate_labels(self, payload):
        self.requests.append(payload)
        result = next(self.outputs)
        if isinstance(result, Exception):
            raise result
        return result


def task():
    return CompactTask(
        ("Q1",),
        {"Q1": "quote", "E1": "evidence"},
        ({"ref": "Q1", "end_cp": 2, "text": "早"}, {"ref": "E1", "end_cp": 6, "text": "林舟说道"}),
        (Candidate("C1", "person1", "林舟"),),
    )


def valid():
    return {
        "labels": [
            {"q": "Q1", "kind": "speech", "character": "C1", "basis": "direct", "evidence": ["E1"]}
        ],
        "_usage": {"total_tokens": 20},
    }


def test_failed_structural_attempt_retains_usage_then_retries_same_budget():
    adapter = Adapter([{"labels": [], "_usage": {"total_tokens": 12}}, valid()])
    result = asyncio.run(run_trial(adapter, task()))
    assert result["ok"] and not result["first_pass_ok"]
    assert result["known_tokens"] == 32
    assert result["unknown_usage_calls"] == 0
    assert len(adapter.requests) == 2
    assert (
        adapter.requests[0]["max_tokens_override"]
        == adapter.requests[1]["max_tokens_override"]
        == 8192
    )


@pytest.mark.parametrize(
    "kind",
    [
        ProviderErrorKind.TIMEOUT,
        ProviderErrorKind.RATE_LIMITED,
        ProviderErrorKind.AUTH,
        ProviderErrorKind.UNAVAILABLE,
    ],
)
def test_provider_failure_never_silently_replays(kind):
    adapter = Adapter([ProviderError(kind, "failed"), valid()])
    result = asyncio.run(run_trial(adapter, task()))
    assert not result["ok"]
    assert len(adapter.requests) == 1 and result["unknown_usage_calls"] == 1


def test_invalid_response_keeps_provider_usage():
    adapter = Adapter(
        [
            ProviderError(
                ProviderErrorKind.INVALID_OUTPUT, "empty", details={"usage": {"total_tokens": 90}}
            )
        ]
    )
    result = asyncio.run(run_trial(adapter, task(), max_format_retries=0))
    assert result["known_tokens"] == 90 and not result["ok"]


def test_no_model_success_fallback_after_retry_limit():
    adapter = Adapter([{"labels": []}, {"labels": []}, valid()])
    result = asyncio.run(run_trial(adapter, task()))
    assert not result["ok"] and result["output"] is None
    assert result["unknown_usage_calls"] == 2 and len(adapter.requests) == 2


def test_budget_and_legacy_pair_are_explicit():
    with pytest.raises(ValueError):
        asyncio.run(run_trial(Adapter([]), task(), max_format_retries=6))
    with pytest.raises(ValueError):
        asyncio.run(run_trial(Adapter([]), task(), legacy_messages=[]))
