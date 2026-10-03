"""Bounded, no-database paired trials for the experimental compact protocol."""

from __future__ import annotations

import time
from typing import Any

from pydantic import ValidationError

from ..llm.adapter import ProviderAdapter
from ..llm.errors import InvalidModelOutput, ProviderError, ProviderErrorKind
from ..llm.validation import LabelingTargets, parse_and_validate
from .compact import CompactTask, compile_output


async def run_trial(
    adapter: ProviderAdapter,
    task: CompactTask,
    *,
    legacy_messages: list[dict[str, str]] | None = None,
    legacy_targets: LabelingTargets | None = None,
    max_format_retries: int = 1,
    max_tokens: int = 8192,
) -> dict[str, Any]:
    """One window, with explicit bounded retries, including failed-call usage.

    No network access occurs until the caller supplies a real adapter. Timeouts
    and provider errors are not replayed; callers must reconcile unknown usage.
    Persist this result between windows; do not silently restart failed trials.
    """
    if not 0 <= max_format_retries <= 5 or max_tokens <= 0:
        raise ValueError("Invalid trial budget")
    if (legacy_messages is None) != (legacy_targets is None):
        raise ValueError("Legacy messages and targets must be supplied together")
    messages = legacy_messages if legacy_messages is not None else task.messages()
    records = []
    result: dict[str, Any] = {
        "ok": False,
        "fingerprint": task.fingerprint(),
        "protocol": "legacy" if legacy_messages is not None else "compact",
        "attempts": records,
        "output": None,
    }
    start = time.perf_counter()
    for attempt in range(1 + max_format_retries):
        began = time.perf_counter()
        raw = None
        usage = {"unknown": True, "total_tokens": None}
        can_retry = True
        try:
            raw = dict(
                await adapter.generate_labels(
                    {
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "max_tokens_override": max_tokens,
                    }
                )
            )
            usage = raw.pop("_usage", usage)
            if legacy_targets is not None:
                report = parse_and_validate(raw, legacy_targets)
                if not report.ok:
                    raise InvalidModelOutput(";".join(report.error_codes))
                compiled = report.output
            else:
                compiled = compile_output(raw, task)
            record = {"ok": True, "usage": usage, "raw": raw}
            result.update(ok=True, output=compiled.model_dump(mode="json"))
        except (InvalidModelOutput, ValidationError) as exc:
            record = {"ok": False, "usage": usage, "raw": raw, "error": str(exc)}
        except ProviderError as exc:
            can_retry = exc.kind is ProviderErrorKind.INVALID_OUTPUT
            record = {
                "ok": False,
                "usage": exc.details.get("usage", usage),
                "raw": raw,
                "error": exc.kind.value,
                "details": exc.details,
            }
        record["elapsed_seconds"] = time.perf_counter() - began
        records.append(record)
        if result["ok"] or not can_retry:
            break
        if attempt < max_format_retries:
            # Do not silently change output cap, model, thinking mode or context.
            messages = [
                *messages,
                {
                    "role": "user",
                    "content": "输出校验失败："
                    + record["error"][:1200]
                    + "。请依据相同原文重做完整JSON，不能猜身份。",
                },
            ]
    result["wall_seconds"] = time.perf_counter() - start
    result["known_tokens"] = sum(r["usage"].get("total_tokens") or 0 for r in records)
    result["unknown_usage_calls"] = sum(
        r["usage"].get("total_tokens") is None or r["usage"].get("unknown", False) for r in records
    )
    result["first_pass_ok"] = records[0]["ok"]
    return result
