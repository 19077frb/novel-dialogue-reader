"""Request identity, not semantic accuracy or provider accounting."""

from copy import deepcopy

import pytest

from ndr.jobs.scheduler import _request_fingerprint


@pytest.mark.parametrize("field", ["messages", "max_tokens_override", "model", "params", "base_url"])
def test_actual_request_or_model_changes_change_the_call_fingerprint(field):
    request = {"messages": [{"role": "user", "content": "少女"}], "max_tokens": 800}
    snapshot = {"model": "model-a", "params": {"max_tokens": 4000}, "base_url": "http://local"}
    changed_request, changed_snapshot = deepcopy(request), deepcopy(snapshot)
    if field == "messages":
        changed_request["messages"][0]["content"] = "少女；请纠正未发送引用"
    elif field == "max_tokens_override":
        changed_request[field] = 8000
    else:
        changed_snapshot[field] = {"max_tokens": 8000} if field == "params" else "different"
    assert _request_fingerprint(request, snapshot) != _request_fingerprint(changed_request, changed_snapshot)


def test_call_fingerprint_excludes_credentials_and_is_order_stable():
    request = {"messages": [], "max_tokens": 800}
    snapshot = {"model": "local", "credential_ref": "private-reference", "api_key": "private-key"}
    assert _request_fingerprint(request, snapshot) == _request_fingerprint(
        {"max_tokens": 800, "messages": []}, {"model": "local"},
    )
