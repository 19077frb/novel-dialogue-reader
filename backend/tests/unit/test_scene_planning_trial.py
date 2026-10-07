import asyncio
import json

import pytest

from ndr.evaluation.journal import CallJournal, JournaledAdapter
from ndr.evaluation.scene_planner import planner_fingerprint, planning_messages, run_scene_planner
from ndr.llm.errors import ProviderError, ProviderErrorKind

TEXT = "原创场景甲。原创场景乙。"
WINDOWS = {"W1": (0, 6), "W2": (6, 12)}
SCOPE = "mock:model:disabled"


def valid():
    return {
        "scenes": [{"scene": "s1", "windows": ["W1", "W2"], "depends_on": []}],
        "_usage": {"total_tokens": 20},
    }


class Adapter:
    def __init__(self, *responses):
        self.responses, self.requests = iter(responses), []

    async def generate_labels(self, request):
        self.requests.append(request)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def run(adapter, **kwargs):
    return asyncio.run(
        run_scene_planner(adapter, text=TEXT, windows=WINDOWS, model_scope=SCOPE, **kwargs)
    )


def test_complete_continuous_plan_is_valid_without_forcing_independence():
    adapter = Adapter(valid())
    result = run(adapter)
    assert result["ok"] and result["first_pass_ok"]
    assert result["plan"][1]["depends_on"] == ("W1",)
    sent = json.loads(adapter.requests[0]["messages"][1]["content"])
    assert set(sent) == {"text", "windows"} and sent["text"] == TEXT
    assert result["known_tokens"] == 20


def test_bad_plan_retains_cost_and_retries_same_text_and_cap():
    bad = {
        "scenes": [{"scene": "s1", "windows": ["W2"], "depends_on": []}],
        "_usage": {"total_tokens": 12},
    }
    adapter = Adapter(bad, valid())
    result = run(adapter)
    assert result["ok"] and not result["first_pass_ok"] and result["known_tokens"] == 32
    assert [r["max_tokens_override"] for r in adapter.requests] == [4096, 4096]
    assert adapter.requests[0]["messages"][1] == adapter.requests[1]["messages"][1]


def test_unknown_plan_failure_never_retries():
    adapter = Adapter({"scenes": []}, valid())
    result = run(adapter)
    assert not result["ok"] and result["reconciliation_required"]
    assert result["plan"] is None and len(adapter.requests) == 1


@pytest.mark.parametrize(
    "kind",
    [
        ProviderErrorKind.TIMEOUT,
        ProviderErrorKind.RATE_LIMITED,
        ProviderErrorKind.AUTH,
        ProviderErrorKind.UNAVAILABLE,
    ],
)
def test_provider_failure_is_not_a_scene_format_retry(kind):
    adapter = Adapter(ProviderError(kind, "provider failure"), valid())
    assert not run(adapter)["ok"]
    assert len(adapter.requests) == 1


def test_failed_plan_has_no_implicit_serial_fallback():
    adapter = Adapter({"scenes": [], "_usage": {"total_tokens": 10}})
    result = run(adapter, max_format_retries=0)
    assert not result["ok"] and result["plan"] is None and result["payload"] is None


@pytest.mark.parametrize(
    "windows", [{"W1": (True, 6)}, {"W1": (0, 99)}, {"W1": (0, 6), "W2": (5, 12)}, {"": (0, 6)}]
)
def test_invalid_original_bounds_fail_before_adapter_dispatch(windows):
    with pytest.raises(ValueError):
        planning_messages(TEXT, windows)


def test_fingerprint_includes_original_model_and_output_policy():
    fp = planner_fingerprint(TEXT, WINDOWS, model_scope=SCOPE)
    assert fp != planner_fingerprint(TEXT.replace("甲", "丙"), WINDOWS, model_scope=SCOPE)
    assert fp != planner_fingerprint(TEXT, WINDOWS, model_scope="other:model")
    assert fp != planner_fingerprint(TEXT, WINDOWS, model_scope=SCOPE, max_tokens=8192)
    with pytest.raises(ValueError):
        planner_fingerprint(TEXT, WINDOWS, model_scope=SCOPE, max_format_retries=True)


def test_paid_planning_responses_replay_without_new_calls(tmp_path):
    adapter = Adapter(valid())
    fingerprint = planner_fingerprint(TEXT, WINDOWS, model_scope=SCOPE)
    ledger = CallJournal(
        tmp_path / "planner.trial.sqlite3",
        dependency_fingerprint=fingerprint,
        max_calls=2,
        max_tokens=100000,
    )
    first = run(JournaledAdapter(adapter, ledger))
    second = run(JournaledAdapter(adapter, ledger))
    assert first["ok"] and first["plan"] == second["plan"]
    assert ledger.stats()["calls"] == len(adapter.requests) == 1
    assert second["attempts"][0]["usage"]["journal_replay"]
