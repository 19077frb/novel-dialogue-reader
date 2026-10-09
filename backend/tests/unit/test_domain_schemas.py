"""领域 schema、错误体与分页契约的单元测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ndr.api.errors import STATUS_BY_CODE
from ndr.api.pagination import decode_cursor, encode_cursor, parse_limit
from ndr.domain.common import CursorPage, DataEnvelope, ErrorBody, ErrorEnvelope, VersionedModel
from ndr.domain.enums import (
    AnnotationStatus,
    Assignment,
    ErrorCode,
    GapDecision,
    JobKind,
    QuoteKind,
    ReadingMode,
    SceneStatus,
    SpeakerBasis,
    VisibilityPolicy,
)
from ndr.domain.jobs import BudgetIn


def test_validation_retry_budget_defaults_and_bounds() -> None:
    assert BudgetIn().max_format_retries == 1
    assert BudgetIn(max_format_retries=0).max_format_retries == 0
    assert BudgetIn(max_format_retries=5).max_format_retries == 5
    for value in (-1, 6, 1.5):
        with pytest.raises(ValidationError):
            BudgetIn(max_format_retries=value)


def test_enum_values_match_contract() -> None:
    assert {member.value for member in SceneStatus} == {"OPEN", "PENDING_BOUNDARY", "CLOSED"}
    assert {member.value for member in GapDecision} == {"CONTINUE", "UPDATE", "BREAK", "UNCERTAIN"}
    assert {member.value for member in QuoteKind} == {
        "speech",
        "thought",
        "quotation",
        "group",
        "other",
        "unknown",
    }
    assert {member.value for member in Assignment} == {"EXISTING", "NEW", "UNKNOWN"}
    assert {member.value for member in AnnotationStatus} == {
        "PROVISIONAL",
        "ACCEPTED",
        "USER_CONFIRMED",
        "UNKNOWN",
    }
    assert {member.value for member in SpeakerBasis} == {
        "DIRECT",
        "COREFERENCE",
        "RESPONSE_LINK",
        "STYLE_ONLY",
        "INSUFFICIENT",
    }
    assert {member.value for member in JobKind} == {
        "IMPORT",
        "INFERENCE",
        "CHARACTER_ROSTER",
        "CHARACTER_MERGE",
        "RECHECK",
        "RECOMPUTE",
        "EXPORT",
    }
    assert {member.value for member in ReadingMode} == {"initial", "reread"}
    assert {member.value for member in VisibilityPolicy} == {"position_safe", "reread"}


def test_error_envelope_serializes_contract_shape() -> None:
    envelope = ErrorEnvelope(
        error=ErrorBody(code=ErrorCode.VERSION_CONFLICT.value, message="版本冲突", details={"a": 1}),
        request_id="req1",
    )
    payload = envelope.model_dump(mode="json")
    assert payload == {
        "error": {"code": "VERSION_CONFLICT", "message": "版本冲突", "details": {"a": 1}},
        "request_id": "req1",
    }


def test_error_codes_have_http_status_mapping() -> None:
    assert STATUS_BY_CODE[ErrorCode.VERSION_CONFLICT] == 409
    assert STATUS_BY_CODE[ErrorCode.IDEMPOTENCY_CONFLICT] == 409
    assert STATUS_BY_CODE[ErrorCode.VALIDATION_ERROR] == 422
    assert STATUS_BY_CODE[ErrorCode.NOT_FOUND] == 404
    assert STATUS_BY_CODE[ErrorCode.PAYLOAD_TOO_LARGE] == 413
    assert STATUS_BY_CODE[ErrorCode.UNSUPPORTED_MEDIA_TYPE] == 415
    assert STATUS_BY_CODE[ErrorCode.RATE_LIMITED] == 429
    assert STATUS_BY_CODE[ErrorCode.PROVIDER_QUOTA_EXHAUSTED] == 402


def test_data_envelope_and_page_shapes() -> None:
    envelope = DataEnvelope[dict](data={"book_id": "b1"}, request_id="req2")
    assert envelope.model_dump(mode="json") == {"data": {"book_id": "b1"}, "request_id": "req2"}

    page = CursorPage[int](items=[1, 2], next_cursor=None)
    assert page.model_dump(mode="json") == {"items": [1, 2], "next_cursor": None}


def test_versioned_model_rejects_non_positive_version() -> None:
    with pytest.raises(ValidationError):
        VersionedModel(id="x", version=0)


def test_models_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ErrorBody(code="X", message="m", unexpected=True)


def test_cursor_roundtrip() -> None:
    cursor = encode_cursor(["2026-09-28T00:00:00+00:00", "q1"])
    assert decode_cursor(cursor) == ["2026-09-28T00:00:00+00:00", "q1"]


def test_invalid_cursor_is_rejected_with_validation_error() -> None:
    from ndr.api.errors import ApiError

    with pytest.raises(ApiError) as excinfo:
        decode_cursor("!!!not-a-cursor!!!")
    assert excinfo.value.code is ErrorCode.VALIDATION_ERROR
    assert excinfo.value.status_code == 422


def test_limit_bounds() -> None:
    from ndr.api.errors import ApiError

    assert parse_limit(None) == 50
    assert parse_limit(10) == 10
    with pytest.raises(ApiError):
        parse_limit(0)
    with pytest.raises(ApiError):
        parse_limit(1000)
