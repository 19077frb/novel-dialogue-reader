import asyncio
from copy import deepcopy

import pytest

from ndr.evaluation import type_basis_cues
from ndr.evaluation.journal import CallJournal, JournaledAdapter
from ndr.evaluation.type_basis_cues import (
    TYPE_BASIS_POLICY,
    TypeBasisCueAdapter,
    type_basis_fingerprint,
)


class Recording:
    def __init__(self):
        self.requests = []
        self.response = {"labels": [], "_usage": {"total_tokens": None, "unknown": True}}

    async def generate_labels(self, request):
        self.requests.append(request)
        return self.response


def test_general_semantic_policy_keeps_uncertainty_and_has_no_work_specific_examples():
    assert all(
        v in TYPE_BASIS_POLICY
        for v in [
            "自言自语",
            "第一人称",
            "独处",
            "quotation",
            "thought",
            "unknown",
            "coreference",
            "response_link",
            "不能仅改basis",
            "未来信息",
            "非speech",
        ]
    )
    assert all(v not in TYPE_BASIS_POLICY for v in ["浅村", "绫濑", "奈良坂", "丸友和", "义妹生活"])


def test_only_system_changes_and_original_and_response_are_preserved():
    backend = Recording()
    request = {
        "messages": [
            {"role": "system", "content": "Strict JSON"},
            {"role": "user", "content": '{"targets":["Q1"],"pov":"C1"}'},
            {"role": "user", "content": "格式错误，重做完整JSON"},
        ],
        "max_tokens": 8192,
        "max_tokens_override": 8192,
        "thinking": {"type": "disabled"},
    }
    original = deepcopy(request)
    response = asyncio.run(TypeBasisCueAdapter(backend).generate_labels(request))
    assert request == original
    assert response is backend.response
    prepared = backend.requests[0]
    assert prepared["messages"][0]["content"] == "Strict JSON\n\n" + TYPE_BASIS_POLICY
    assert prepared["messages"][1:] == original["messages"][1:]
    assert {k: v for k, v in prepared.items() if k != "messages"} == {
        k: v for k, v in original.items() if k != "messages"
    }


@pytest.mark.parametrize(
    "messages",
    [
        None,
        [],
        [{"role": "user", "content": "text"}],
        [{"role": "system", "content": []}],
        [{"role": "system", "content": "a"}, {"role": "system", "content": "b"}],
        [{"role": "user", "content": "a"}, {"role": "system", "content": "b"}],
        [{"role": "system", "content": "a"}, "invalid"],
    ],
)
def test_invalid_messages_never_reach_provider(messages):
    backend = Recording()
    with pytest.raises(ValueError):
        asyncio.run(TypeBasisCueAdapter(backend).generate_labels({"messages": messages}))
    assert not backend.requests


def test_policy_content_version_and_source_all_scope_fingerprint(monkeypatch):
    baseline = type_basis_fingerprint("task")
    assert baseline == type_basis_fingerprint("task")
    assert baseline != type_basis_fingerprint("other")
    monkeypatch.setattr(type_basis_cues, "TYPE_BASIS_VERSION", "new")
    assert baseline != type_basis_fingerprint("task")
    monkeypatch.setattr(type_basis_cues, "TYPE_BASIS_VERSION", "type-basis-cues-1")
    monkeypatch.setattr(type_basis_cues, "TYPE_BASIS_POLICY", TYPE_BASIS_POLICY + " extra")
    assert baseline != type_basis_fingerprint("task")


def test_provider_error_is_not_retried_or_replaced():
    failure = RuntimeError("unknown provider outcome")

    class Failing:
        async def generate_labels(self, request):
            raise failure

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(
            TypeBasisCueAdapter(Failing()).generate_labels(
                {"messages": [{"role": "system", "content": "strict"}]}
            )
        )
    assert caught.value is failure


def test_modified_request_is_journaled_and_replay_never_calls_provider(tmp_path):
    backend = Recording()
    backend.response = {"labels": [], "_usage": {"total_tokens": 13, "unknown": False}}
    journal = CallJournal(
        tmp_path / "calls.trial.sqlite3",
        dependency_fingerprint=type_basis_fingerprint("source"),
        max_calls=2,
        max_tokens=20000,
    )
    request = {
        "messages": [{"role": "system", "content": "Strict JSON"}],
        "max_tokens": 100,
    }
    wrapper = TypeBasisCueAdapter(JournaledAdapter(backend, journal))
    first = asyncio.run(wrapper.generate_labels(request))
    assert journal.stats()["known_tokens"] == 13
    assert len(backend.requests) == 1
    assert journal.request_key(request) != journal.request_key(backend.requests[0])
    replay = asyncio.run(wrapper.generate_labels(request))
    assert len(backend.requests) == 1 and journal.stats()["calls"] == 1
    assert replay["_usage"]["journal_replay"]
    assert replay["labels"] == first["labels"]
