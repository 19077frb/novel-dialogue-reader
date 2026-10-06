import json
import zlib

import pytest

from ndr.llm.errors import ProviderError, ProviderErrorKind
from ndr.llm.receipt import ProviderResult
from ndr.storage.run_archive import decode_archive, encode_archive


def test_archive_envelope_cannot_be_overridden_by_payload():
    record = decode_archive(encode_archive({"version": "foreign", "complete": False}))
    assert record["version"] == "inference-archive-1"
    assert record["complete"] is True


def test_semantic_validation_failure_keeps_received_provider_evidence():
    from types import SimpleNamespace

    from ndr.storage.run_archive import save_run_archive

    result = ProviderResult({"labels": []}, receipt={"response": {"model": "test"}})
    run = SimpleNamespace()
    save_run_archive(
        run, {"messages": []}, raw=result, error=ValueError("invalid identity"), phase="returned"
    )
    record = decode_archive(run.call_archive)
    assert record["adapter_result"] == {"labels": []}
    assert record["provider_receipt"] == result.receipt


def test_compressed_archive_preserves_original_unicode_and_redacts_structured_secrets():
    original = {
        "request": {
            "messages": [{"content": "林舟说：好。" * 1000}],
            "api_key": "private",
            "headers": {"Authorization": "Bearer private"},
        },
        "response": {"usage": {"total_tokens": 42}, "finish_reason": "stop"},
    }
    blob = encode_archive(original)
    restored = decode_archive(blob)
    assert restored["request"]["messages"] == original["request"]["messages"]
    assert "private" not in json.dumps(restored)
    assert restored["response"] == original["response"]
    assert len(blob) < len(json.dumps(original).encode()) / 10


@pytest.mark.parametrize(
    "value",
    [
        None,
        b"invalid",
        zlib.compress(b"{}"),
        zlib.compress(b"[]"),
        zlib.compress(b'{"version":"bad"}'),
    ],
)
def test_missing_corrupt_or_foreign_archive_is_not_replayed(value):
    with pytest.raises(ValueError):
        decode_archive(value)


def test_size_limit_or_serialization_failure_is_explicit_not_silent_truncation(monkeypatch):
    import ndr.storage.run_archive as module

    monkeypatch.setattr(module, "MAX_ARCHIVE_BYTES", 1000)
    for document in ({"response": "x" * 2000}, {"response": object()}):
        blob = encode_archive(document)
        assert not decode_archive(blob, require_complete=False)["complete"]
        with pytest.raises(ValueError, match="incomplete"):
            decode_archive(blob)
    oversized = zlib.compress(b"x" * 2000)
    with pytest.raises(ValueError, match="oversized"):
        decode_archive(oversized)


def test_private_receipt_never_becomes_model_json_or_public_error_detail():
    receipt = {"response": {"private_body": "original response"}}
    result = ProviderResult({"labels": [], "_usage": {"total_tokens": 1}}, receipt=receipt)
    assert "private_body" not in json.dumps(result)
    assert result.receipt == receipt
    error = ProviderError(ProviderErrorKind.INVALID_OUTPUT, "invalid", receipt=receipt)
    assert "private_body" not in json.dumps(error.as_dict)
