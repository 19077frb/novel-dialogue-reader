"""任务累计用量包含每次有效上报，未知用量不冒充零消耗。"""

import json
from types import SimpleNamespace

from ndr.domain.enums import InferenceRunState
from ndr.jobs.service import _spent_tokens


def test_job_usage_includes_success_failure_and_reported_total() -> None:
    rows = [
        SimpleNamespace(
            state=InferenceRunState.SUCCEEDED,
            error_code=None,
            usage_json=json.dumps({"input_tokens": 20, "output_tokens": 3, "total_tokens": 25}),
        ),
        SimpleNamespace(
            state=InferenceRunState.FAILED,
            error_code="INVALID_MODEL_OUTPUT",
            usage_json=json.dumps({"input_tokens": 7, "output_tokens": 2}),
        ),
        SimpleNamespace(
            state=InferenceRunState.SUCCEEDED,
            error_code=None,
            usage_json=json.dumps({"total_tokens": 5}),
        ),
    ]
    assert _spent_tokens(rows) == {
        "input_tokens": 27,
        "output_tokens": 5,
        "total_tokens": 39,
        "unknown_runs": 0,
    }


def test_job_usage_distinguishes_missing_usage_from_zero() -> None:
    rows = [
        SimpleNamespace(state=InferenceRunState.SUCCEEDED, error_code=None, usage_json=value)
        for value in (
            None,
            "{}",
            '{"unknown":true}',
            "invalid-json",
            '{"input_tokens":0,"output_tokens":0,"total_tokens":0}',
        )
    ]
    assert _spent_tokens(rows) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "unknown_runs": 4,
    }
