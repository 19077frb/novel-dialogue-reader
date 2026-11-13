"""Only temporary logs and offline transports, never a user's library."""

import asyncio
import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import httpx
import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError

from ndr import diagnostics
from ndr.llm.adapters.chat_completions import ChatCompletionsAdapter
from ndr.llm.errors import ProviderError, ProviderErrorKind


@contextmanager
def log_directory(path):
    handler = diagnostics.start_diagnostics(path)
    assert handler is not None
    try:
        with diagnostics.diagnostic_scope(path):
            yield handler
    finally:
        diagnostics.stop_diagnostics(handler)


def records(path):
    return [json.loads(line) for line in (path / "tasks.log").read_text(encoding="utf-8").splitlines()]


def test_safe_causal_summary_excludes_provider_text_sql_and_validation_input(tmp_path):
    secret = "private-novel-sk-secret"
    try:
        try:
            raise json.JSONDecodeError("private reason", secret, 3)
        except json.JSONDecodeError as cause:
            raise ProviderError(ProviderErrorKind.INVALID_OUTPUT, secret, details={
                "body": secret, "snippet": secret, "endpoint": secret,
                "reasoning_snippet": secret, "status_code": 502,
                "stage": "http_response_json", "timeout_type": secret,
                "finish_reason": secret, "issues": [{"message": secret}],
            }, receipt={"prompt": secret}) from cause
    except ProviderError as error:
        with log_directory(tmp_path):
            diagnostics.log_failure("provider_call", "job-id", error,
                                    run_id="run-id", unknown_field=secret)
    payload = records(tmp_path)[0]
    assert payload["error"]["chain"][0]["kind"] == "INVALID_OUTPUT"
    assert payload["error"]["chain"][0]["details"] == {
        "status_code": 502, "stage": "http_response_json",
        "timeout_type": "unknown", "finish_reason": "unknown",
    }
    assert payload["error"]["chain"][1]["position"] == 3
    assert secret not in json.dumps(payload)
    assert "unknown_field" not in payload

    original = sqlite3.IntegrityError(secret)
    original.sqlite_errorcode = sqlite3.SQLITE_CONSTRAINT_UNIQUE
    original.sqlite_errorname = secret
    summary = diagnostics.error_summary(IntegrityError("private SQL", {"value": secret}, original))
    assert summary["chain"][1]["sqlite_name"] == "SQLITE_CONSTRAINT_UNIQUE"
    assert secret not in json.dumps(summary)

    class Example(BaseModel):
        value: int

    with pytest.raises(ValidationError) as caught:
        Example(value=secret)
    summary = diagnostics.error_summary(caught.value)
    assert summary["chain"][0]["issue_types"] == ["int_parsing"]
    assert secret not in json.dumps(summary)


def test_chains_frames_and_records_are_bounded(tmp_path):
    def recurse(depth):
        if depth:
            return recurse(depth - 1)
        raise RuntimeError("private exception text")

    with pytest.raises(RuntimeError) as caught:
        recurse(30)
    previous = caught.value
    for _ in range(8):
        wrapper = RuntimeError("private exception text")
        wrapper.__cause__ = previous
        previous = wrapper
    previous.__context__ = previous
    summary = diagnostics.error_summary(previous)
    assert len(summary["chain"]) == 4 and summary["chain_truncated"]
    frames = diagnostics.error_summary(caught.value)["chain"][0]
    assert len(frames["locations"]) == 8 and frames["locations_truncated"]
    with log_directory(tmp_path):
        diagnostics.log_failure("bounded", "job", caught.value, stage="界" * 10000)
        for _ in range(3):
            diagnostics.log_event("safe", job_id="job", stage="x" * 120)
    lines = (tmp_path / "tasks.log").read_bytes().splitlines(keepends=True)
    assert all(len(line) <= diagnostics.MAX_RECORD_BYTES for line in lines)
    assert records(tmp_path)[0]["stage"] == "unknown"


def test_physical_utf8_record_budget_includes_windows_newline(tmp_path, monkeypatch):
    # Exercise the exact boundary with multibyte JSON and a deliberately large future summary.
    monkeypatch.setattr(diagnostics, "error_summary", lambda error: {"chain": [{"type": "界" * 2700}]})
    with log_directory(tmp_path):
        diagnostics.log_failure("bounded", "job", RuntimeError())
    raw = (tmp_path / "tasks.log").read_bytes()
    assert len(raw) <= 8192 and json.loads(raw)["event"] == "task_failure"


def test_bad_scope_resolution_cannot_route_to_another_library(tmp_path, monkeypatch):
    with log_directory(tmp_path):
        def fail(directory):
            raise OSError("private path")

        monkeypatch.setattr(diagnostics, "_directory_key", fail)
        with diagnostics.diagnostic_scope("private path"):
            diagnostics.log_event("must_not_leak", job_id="job")
        assert not records(tmp_path)


def test_cause_suppression_and_cycle_do_not_loop():
    hidden = RuntimeError("private")
    error = ValueError("private")
    error.__context__ = hidden
    error.__suppress_context__ = True
    assert len(diagnostics.error_summary(error)["chain"]) == 1
    error.__cause__ = error
    assert diagnostics.error_summary(error)["chain_truncated"]


def test_explicit_dedup_ignores_attempt_metrics_and_flushes_on_recovery(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(diagnostics, "monotonic", lambda: clock[0])
    with log_directory(tmp_path):
        for attempt in range(5):
            diagnostics.log_failure("settlement_retry", "job", RuntimeError("private"),
                                    deduplicate=True, attempt=attempt, elapsed_ms=attempt * 3)
        diagnostics.log_failure("settlement_retry", "other-job", RuntimeError("private"),
                                deduplicate=True)
        diagnostics.flush_repeats("job")
        diagnostics.log_event("settlement_recovered", job_id="job")
        clock[0] = 61
        diagnostics.log_event("next_event")
        diagnostics.log_failure("terminal", "job", RuntimeError("private"))
        diagnostics.log_failure("terminal", "job", RuntimeError("private"))
    logged = records(tmp_path)
    summary = next(item for item in logged if item["event"] == "diagnostic_repeated")
    assert summary["repeat_count"] == 4 and summary["job_id"] == "job"
    assert len([item for item in logged if item.get("operation") == "terminal"]) == 2
    assert not diagnostics._repeats


def test_continuous_duplicates_only_log_one_full_traceback(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(diagnostics, "monotonic", lambda: clock[0])
    with pytest.raises(RuntimeError) as caught:
        raise RuntimeError("private")
    with log_directory(tmp_path):
        for elapsed in (0, 1, 2, 61, 62, 122):
            clock[0] = elapsed
            diagnostics.log_failure("retry", "job", caught.value, deduplicate=True)
    logged = records(tmp_path)
    assert sum(item["event"] == "task_failure" for item in logged) == 1
    summaries = [item for item in logged if item["event"] == "diagnostic_repeated"]
    assert sum(item["repeat_count"] for item in summaries) == 5
    assert all("locations" not in cause for item in summaries for cause in item["error"]["chain"])
    assert len({item["fingerprint"] for item in summaries}) == 1


def test_dedup_memory_bound_and_stop_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "MAX_REPEAT_KEYS", 3)
    with log_directory(tmp_path):
        for job in range(10):
            diagnostics.log_failure("retry", f"job-{job}", RuntimeError(), deduplicate=True)
            diagnostics.log_failure("retry", f"job-{job}", RuntimeError(), deduplicate=True)
        assert len(diagnostics._repeats) == 3
    logged = records(tmp_path)
    assert len([item for item in logged if item["event"] == "diagnostic_repeated"]) == 10
    assert not diagnostics._repeats


def test_unbound_fault_never_moves_into_a_future_library(tmp_path, monkeypatch, caplog):
    clock = [0.0]
    monkeypatch.setattr(diagnostics, "monotonic", lambda: clock[0])
    assert not diagnostics._handlers and diagnostics._scope.get() is None
    with caplog.at_level(logging.INFO, logger="ndr.diagnostics"):
        diagnostics.log_failure("old_unbound", "unbound-job", RuntimeError(), deduplicate=True)
        diagnostics.log_failure("old_unbound", "unbound-job", RuntimeError(), deduplicate=True)
    assert not diagnostics._repeats
    clock[0] = 61.0
    with log_directory(tmp_path):
        diagnostics.log_event("new_library", job_id="new-job")
        diagnostics.flush_repeats()
    assert [item["event"] for item in records(tmp_path)] == ["new_library"]
    assert "old_unbound" in caplog.text


def test_scoped_concurrent_libraries_and_handler_references(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    with log_directory(first) as handler, log_directory(second):
        same = diagnostics.start_diagnostics(first)
        assert same is handler
        diagnostics.stop_diagnostics(same)

        def write(directory, job):
            with diagnostics.diagnostic_scope(directory):
                diagnostics.log_event("scoped", job_id=job)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda args: write(*args), [(first, "first"), (second, "second")]))
        # Explicitly absent context must never route to a guessed library.
        token = diagnostics._scope.set(None)
        try:
            diagnostics.log_event("unscoped", job_id="private")
        finally:
            diagnostics._scope.reset(token)
    assert [item["job_id"] for item in records(first)] == ["first"]
    assert [item["job_id"] for item in records(second)] == ["second"]


def test_failure_isolation_and_safe_propagation(tmp_path, monkeypatch, caplog, capsys):
    monkeypatch.setattr(diagnostics, "_reported_io_failure", False)
    blocker = tmp_path / "file"
    blocker.write_text("existing", encoding="utf-8")
    assert diagnostics.start_diagnostics(blocker / "logs") is None
    diagnostics.stop_diagnostics(None)
    with log_directory(tmp_path / "logs") as handler:
        def fail(*args, **kwargs):
            raise OSError("private secret detail")

        monkeypatch.setattr(handler, "doRollover", fail)
        monkeypatch.setattr(handler, "shouldRollover", lambda record: True)
        diagnostics.log_failure("failed_io", "job", RuntimeError("private exception"))
        with caplog.at_level(logging.WARNING, logger="ndr.diagnostics"):
            try:
                raise RuntimeError("private exception")
            except RuntimeError:
                diagnostics.logger.exception("private log message")
        assert "private" not in caplog.text
        monkeypatch.setattr(handler.stream, "flush", fail)
        handler.flush()
    stderr = capsys.readouterr().err
    assert stderr.count("NDR diagnostics unavailable") == 1
    assert "private" not in stderr


@pytest.mark.parametrize("failure", ["serialization", "summary", "emit", "close"])
def test_diagnostic_infrastructure_failures_never_escape(tmp_path, monkeypatch, failure):
    handler = diagnostics.start_diagnostics(tmp_path)
    assert handler is not None

    def fail(*args, **kwargs):
        raise RuntimeError("private diagnostic failure")

    try:
        with monkeypatch.context() as patch:
            if failure == "serialization":
                patch.setattr(diagnostics, "_encode", fail)
            elif failure == "summary":
                patch.setattr(diagnostics, "_error_item", fail)
            elif failure == "emit":
                patch.setattr(diagnostics.logger, "log", fail)
            else:
                patch.setattr(handler, "close", fail)
            diagnostics.log_failure("failure_test", "job", RuntimeError("private original"))
            diagnostics.stop_diagnostics(handler)
    finally:
        # The close failure was injected above. Release only this test's real stream.
        handler.close()
        diagnostics.stop_diagnostics(handler)


def test_real_rotation_respects_backup_and_record_limits(tmp_path):
    with log_directory(tmp_path) as handler:
        assert handler.maxBytes == 5 * 1024 * 1024 and handler.backupCount == 3
        handler.maxBytes = 500
        for index in range(50):
            diagnostics.log_event("rotation", job_id=f"job-{index}", elapsed_ms=index)
    paths = list(tmp_path.glob("tasks.log*"))
    assert len(paths) == 4
    for path in paths:
        assert path.stat().st_size <= 500
        for line in path.read_text(encoding="utf-8").splitlines():
            assert json.loads(line)["event"] == "rotation"


@pytest.mark.parametrize(("body", "stage", "reason"), [
    ("private invalid HTTP", "http_response_json", "invalid_response_json"),
    ([], "http_response_shape", "invalid_response_object"),
    ({"choices": []}, "model_content", "missing_choices"),
    ({"choices": [{"message": {"content": "private invalid model"}}]},
     "model_json", "invalid_model_json"),
])
def test_offline_provider_failure_stages_are_distinguishable(body, stage, reason):
    def respond(request):
        return httpx.Response(200, text=body) if isinstance(body, str) else httpx.Response(200, json=body)

    async def call():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            adapter = ChatCompletionsAdapter(base_url="https://example.invalid/v1", model="test",
                                             client=client)
            with pytest.raises(ProviderError) as caught:
                await adapter.generate_labels({"messages": []})
            return diagnostics.error_summary(caught.value)

    summary = asyncio.run(call())
    assert summary["chain"][0]["details"]["stage"] == stage
    assert summary["chain"][0]["details"]["reason_code"] == reason
    assert "private" not in json.dumps(summary)
