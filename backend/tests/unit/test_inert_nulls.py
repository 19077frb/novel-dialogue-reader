import asyncio
import json
import sqlite3
from copy import deepcopy

import pytest
from pydantic import ValidationError

from ndr.evaluation import inert_nulls
from ndr.evaluation.compact import CompactOutput
from ndr.evaluation.inert_nulls import (
    InertNullAdapter,
    kind_echo_fingerprint,
    normalization_fingerprint,
    normalize_inert_nulls,
    normalize_kind_echoes,
)
from ndr.evaluation.journal import CallJournal, JournaledAdapter


@pytest.mark.parametrize("kind", sorted(inert_nulls.NON_SPEECH))
def test_only_inert_fields_are_removed_and_original_and_unknown_usage_stay(kind):
    original = {
        "labels": [{"q": "Q1", "kind": kind, "character": None, "basis": None, "evidence": None}],
        "_usage": {"unknown": True, "total_tokens": None},
    }
    saved = deepcopy(original)
    result = normalize_inert_nulls(original)
    assert original == saved
    assert result["labels"] == [{"q": "Q1", "kind": kind}]
    assert result["_usage"] == original["_usage"]
    assert CompactOutput.model_validate({"labels": result["labels"]}).labels[0].kind == kind


@pytest.mark.parametrize(
    "contradiction",
    [
        {"character": "C1"},
        {"character": ""},
        {"basis": "direct"},
        {"evidence": []},
        {"evidence": ["G1"]},
        {"speaker_ref": None},
    ],
)
def test_nonempty_or_unknown_fields_remain_strictly_rejected(contradiction):
    original = {"labels": [{"q": "Q1", "kind": "quotation", **contradiction}]}
    result = normalize_inert_nulls(original)
    assert result == original
    with pytest.raises(ValidationError):
        CompactOutput.model_validate(result)


@pytest.mark.parametrize(
    "row",
    [
        None,
        "invalid",
        {"kind": []},
        {"kind": "SPEECH"},
        {"q": "Q1", "kind": "speech", "character": None, "basis": None, "evidence": None},
    ],
)
def test_other_shapes_are_never_silently_repaired(row):
    original = {"labels": [row]}
    assert normalize_inert_nulls(original) == original


def test_non_list_labels_are_unchanged_and_missing_target_is_not_added():
    assert normalize_inert_nulls({"labels": None}) == {"labels": None}
    assert normalize_inert_nulls({}) == {}
    assert normalize_inert_nulls({"labels": [{"kind": "other", "basis": None}]}) == {
        "labels": [{"kind": "other"}]
    }
    with pytest.raises(ValidationError):
        CompactOutput.model_validate({"labels": [{"kind": "other"}]})


def test_policy_and_source_versions_scope_cache_dependency(monkeypatch):
    old = normalization_fingerprint("source")
    assert old != normalization_fingerprint("other")
    monkeypatch.setattr(inert_nulls, "NORMALIZATION_VERSION", "new")
    assert old != normalization_fingerprint("source")


def test_raw_is_journaled_normalized_replay_has_no_new_call_and_request_is_unchanged(tmp_path):
    raw = {
        "labels": [{"q": "Q1", "kind": "other", "character": None}],
        "_usage": {"total_tokens": 17, "unknown": False},
    }
    requests = []

    class Backend:
        async def generate_labels(self, request):
            requests.append(deepcopy(request))
            return raw

    path = tmp_path / "nulls.trial.sqlite3"
    journal = CallJournal(
        path,
        dependency_fingerprint=normalization_fingerprint("task"),
        max_calls=2,
        max_tokens=20000,
    )
    request = {
        "messages": [{"role": "system", "content": "Strict JSON"}],
        "max_tokens_override": 200,
    }
    original = deepcopy(request)
    wrapper = InertNullAdapter(JournaledAdapter(Backend(), journal))
    first = asyncio.run(wrapper.generate_labels(request))
    second = asyncio.run(wrapper.generate_labels(request))
    assert request == original and requests == [original]
    assert first["labels"] == second["labels"] == [{"q": "Q1", "kind": "other"}]
    assert second["_usage"]["journal_replay"]
    assert journal.stats()["calls"] == 1 and journal.stats()["known_tokens"] == 17
    with sqlite3.connect(path) as db:
        saved = json.loads(db.execute("select response from calls").fetchone()[0])
    assert saved == raw and raw["labels"][0]["character"] is None


def test_errors_are_not_swallowed_or_retried():
    calls = []

    class Failing:
        async def generate_labels(self, request):
            calls.append(request)
            raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(InertNullAdapter(Failing()).generate_labels({}))
    assert len(calls) == 1


@pytest.mark.parametrize("kind", sorted(inert_nulls.NON_SPEECH))
def test_kind_echo_is_a_separate_policy_without_changing_type_or_v1(kind):
    raw = {
        "labels": [{"q": "Q1", "kind": kind, "character": None, "basis": kind, "evidence": None}]
    }
    saved = deepcopy(raw)
    assert normalize_inert_nulls(raw)["labels"][0]["basis"] == kind
    result = normalize_kind_echoes(raw)
    assert raw == saved and result == {"labels": [{"q": "Q1", "kind": kind}]}
    CompactOutput.model_validate(result)


@pytest.mark.parametrize(
    "row",
    [
        {"q": "Q1", "kind": "thought", "basis": "quotation"},
        {"q": "Q1", "kind": "thought", "basis": "direct"},
        {"q": "Q1", "kind": "thought", "basis": "thought", "character": "C1"},
        {"q": "Q1", "kind": "thought", "basis": "thought", "evidence": []},
        {"q": "Q1", "kind": "speech", "basis": "speech", "character": None, "evidence": []},
    ],
)
def test_kind_echo_does_not_suppress_contradictions(row):
    result = normalize_kind_echoes({"labels": [row]})
    with pytest.raises(ValidationError):
        CompactOutput.model_validate(result)


def test_kind_echo_fingerprint_is_distinct_and_versioned(monkeypatch):
    old = kind_echo_fingerprint("task")
    assert old != normalization_fingerprint("task")
    monkeypatch.setattr(inert_nulls, "KIND_ECHO_VERSION", "new")
    assert old != kind_echo_fingerprint("task")
