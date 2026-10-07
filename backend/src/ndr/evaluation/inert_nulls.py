"""Opt-in normalisation of semantically empty fields, not semantic repair."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

NORMALIZATION_VERSION = "non-speech-inert-nulls-1"
KIND_ECHO_VERSION = "non-speech-kind-echo-1"
NON_SPEECH = frozenset({"thought", "quotation", "group", "other", "unknown"})
INERT_FIELDS = frozenset({"character", "basis", "evidence"})


def normalization_fingerprint(source_fingerprint: str) -> str:
    return hashlib.sha256(
        json.dumps(
            [NORMALIZATION_VERSION, sorted(NON_SPEECH), sorted(INERT_FIELDS), source_fingerprint]
        ).encode()
    ).hexdigest()


def normalize_inert_nulls(response: dict) -> dict:
    """Preserve kind, references, nonempty contradictions and original response."""
    prepared = deepcopy(response)
    labels = prepared.get("labels")
    if not isinstance(labels, list):
        return prepared
    for row in labels:
        if not isinstance(row, dict):
            continue
        kind = row.get("kind")
        if not isinstance(kind, str) or kind not in NON_SPEECH:
            continue
        for key in INERT_FIELDS:
            if key in row and row[key] is None:
                del row[key]
    return prepared


class InertNullAdapter:
    """Wrap *outside* the journal: retain raw provider response for audit."""

    def __init__(self, adapter):
        self.adapter = adapter

    async def generate_labels(self, request: dict) -> dict:
        return normalize_inert_nulls(await self.adapter.generate_labels(request))


def kind_echo_fingerprint(source_fingerprint: str) -> str:
    return hashlib.sha256(
        json.dumps([KIND_ECHO_VERSION, normalization_fingerprint(source_fingerprint)]).encode()
    ).hexdigest()


def normalize_kind_echoes(response: dict) -> dict:
    """Distinct policy: basis echoing the *same* non-speech kind is redundant."""
    prepared = normalize_inert_nulls(response)
    labels = prepared.get("labels")
    if not isinstance(labels, list):
        return prepared
    for row in labels:
        if not isinstance(row, dict):
            continue
        kind = row.get("kind")
        if isinstance(kind, str) and kind in NON_SPEECH and row.get("basis") == kind:
            del row["basis"]
    return prepared


class RedundantFieldAdapter:
    """Explicit additional policy, never silently changing InertNullAdapter."""

    def __init__(self, adapter):
        self.adapter = adapter

    async def generate_labels(self, request: dict) -> dict:
        return normalize_kind_echoes(await self.adapter.generate_labels(request))
