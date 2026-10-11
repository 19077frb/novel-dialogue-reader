"""集成测试：单窗口推理引擎。

用 FakeProvider 驱动状态机（不发任何网络请求）：真实模型效果属于评测范畴。
每个用例都用**小的显式窗口**（目标对白必须全部被标注，否则校验会报 missing_targets）。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ndr.config import Settings
from ndr.context.service import load_window_inputs, plan_range
from ndr.context.window_builder import WindowInputs, plan_windows
from ndr.domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    Assignment,
    IdentityOperation,
    InferenceRunState,
    JobKind,
    JobState,
    QuoteKind,
    ReviewQueueStatus,
    ReviewReason,
    ReviewTargetType,
    SpeakerBasis,
)
from ndr.evaluation.compact import CompactTask
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.llm.errors import InvalidModelOutput, ProviderError, ProviderErrorKind
from ndr.llm.expression_compiler import compile_expression_output
from ndr.llm.validation import RetryPolicy
from ndr.scenes.engine import apply_window
from ndr.scenes.runner import _reference_aliases, run_window
from ndr.scenes.state import ConfirmedCharacter, SceneState
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import (
    Annotation,
    AnnotationHistory,
    BookCharacter,
    BookVersion,
    IdentityRevision,
    InferenceRun,
    Job,
    ResultCache,
    ReviewItem,
    Scene,
    SceneMembership,
    SpeakerGroup,
)
from ndr.storage.transactions import transaction

SAMPLE = (
    "第一章 雨夜\n"
    "「雨停了。」少女合上伞。\n"
    "少年没有回答，只是把外套递了过去。\n"
    "「……谢谢。」她低声说。\n"
    "「不用谢。」\n"
    "远处传来钟声，两人都没有再开口。\n"
    "「明天也来这里吧。」少年忽然说。\n"
    "「嗯。」少女点了点头。\n"
)


def _expression_task(window):
    aliases, references = _reference_aliases(window)
    targets = [f for f in window.fragments if f.fragment_id in window.target_quote_ids]
    targets.sort(key=lambda f: f.start_cp)
    return CompactTask(
        tuple(aliases[q] for q in window.target_quote_ids), references,
        tuple({"ref": aliases[f.fragment_id], "kind": f.kind.value, "text": f.text,
               "start_cp": f.start_cp, "end_cp": f.end_cp} for f in window.fragments),
        (),
        {aliases[f.fragment_id]: next((aliases[q.fragment_id] for q in targets
                                      if q.start_cp >= f.end_cp), None)
         for f in window.fragments if f.kind.value in {"inner_gap", "outer_gap"}},
    )


def _short_output(task, kind="speech", known=True):
    evidence = next(r["ref"] for r in task.context
                    if r["ref"] not in task.quote_ids and r["text"].strip())
    return {"labels": [{"q": q, "kind": kind, "character": "N1" if known else None,
                        "basis": "direct" if known else "insufficient",
                        "evidence": [evidence] if known else []} for q in task.quote_ids],
            "new_characters": [{"ref": "N1", "name": "少女", "description": "原文中的少女",
                                "evidence": [evidence]}] if known else []}


@pytest.mark.parametrize("kind", ["speech", "thought", "quotation"])
def test_short_window_executes_and_preserves_programme_acceptance(
    migrated_client, migrated_settings, kind,
):
    _import(migrated_client)

    def body(session, inputs, version):
        ids = _targets(inputs)[:2]
        window = _window(inputs, ids)
        task = _expression_task(window)
        result = _run(session, script=[_short_output(task, kind)], window=window,
                      state=SceneState(), inputs=inputs, expression_task=task,
                      owner_approvals={ids[0]: False, ids[1]: True})
        assert result.ok, result.application.warnings
        assert result.attempts == 1 and result.compiler_fingerprint
        rows = {r.quote_id: r for r in session.scalars(select(Annotation))}
        assert {r.kind for r in rows.values()} == {QuoteKind(kind)}
        assert rows[ids[0]].status is AnnotationStatus.PROVISIONAL
        assert rows[ids[1]].status is AnnotationStatus.ACCEPTED
        assert rows[ids[0]].basis is SpeakerBasis.DIRECT
        assert rows[ids[0]].speaker_id == rows[ids[1]].speaker_id
        assert session.scalar(select(ReviewItem)).reason is ReviewReason.AMBIGUOUS_SPEAKER
        assert not result.application.scene_state.recent_turns if kind != "speech" else True
        assert '"q": "Q1"' in result.raw_outputs[0]
        compiled = compile_expression_output(_short_output(task, kind), task,
                                             owner_approvals={ids[0]: False, ids[1]: False})
        held = _apply(session, output=compiled.output, window=window,
                      state=result.application.scene_state, inputs=inputs,
                      expected_schema_version="1.1", acceptance_ceilings=compiled.acceptance_ceilings,
                      preserve_existing_candidates=True)
        assert held.validation_ok
        assert rows[ids[1]].status is AnnotationStatus.PROVISIONAL
        original = rows[ids[0]]
        original.user_locked = True
        original.status = AnnotationStatus.USER_CONFIRMED
        original.source = AnnotationSource.USER
        speaker = original.speaker_id
        rerun = _run(session, script=[_short_output(task, kind, known=False)], window=window,
                     state=result.application.scene_state, inputs=inputs, expression_task=task,
                     locked_quote_ids={ids[0]})
        assert rerun.ok and rerun.application.skipped_locked_quote_ids == [ids[0]]
        assert original.status is AnnotationStatus.USER_CONFIRMED
        assert original.speaker_id == speaker and original.kind is QuoteKind(kind)

    _with_session(migrated_settings, body)


@pytest.mark.parametrize("known_usage", [False, True])
def test_short_window_format_retry_requires_known_usage(
    migrated_client, migrated_settings, known_usage,
):
    _import(migrated_client)

    def body(session, inputs, version):
        window = _window(inputs, _targets(inputs)[:2])
        task = _expression_task(window)
        bad = _short_output(task)
        bad["labels"][0]["evidence"] = ["UNSENT"]
        if known_usage:
            bad["_usage"] = {"total_tokens": 30, "input_tokens": 20, "output_tokens": 10}
        good = _short_output(task)
        good["_usage"] = {"total_tokens": 40, "input_tokens": 25, "output_tokens": 15}
        result = _run(session, script=[bad, good], window=window, state=SceneState(),
                      inputs=inputs, expression_task=task)
        assert result.ok is known_usage
        assert result.attempts == (2 if known_usage else 1)
        assert len(result.raw_outputs) == result.attempts
        if known_usage:
            assert sum(u["total_tokens"] for u in result.usage_records) == 70
            assert result.error_code is None
            assert len(list(session.scalars(select(Annotation)))) == 2
        else:
            assert result.error_code == "INVALID_MODEL_OUTPUT"
            assert not list(session.scalars(select(Annotation)))
            assert not list(session.scalars(select(SpeakerGroup)))

    _with_session(migrated_settings, body)


@pytest.mark.parametrize("failure", ["known_format", "unknown_format", "timeout"])
def test_short_window_provider_failure_keeps_usage_and_does_not_resend_unknown(
    migrated_client, migrated_settings, failure,
):
    _import(migrated_client)

    def body(session, inputs, version):
        window = _window(inputs, _targets(inputs)[:1])
        task = _expression_task(window)
        details = {"body": "original invalid response"}
        if failure == "known_format":
            details["usage"] = {"total_tokens": 19}
        error = (ProviderError(ProviderErrorKind.TIMEOUT, "timeout", details=details)
                 if failure == "timeout" else InvalidModelOutput("invalid", details=details))
        result = _run(session, script=[error, _short_output(task, known=False)], window=window,
                      state=SceneState(), inputs=inputs, expression_task=task)
        assert result.ok is (failure == "known_format")
        assert result.attempts == (2 if failure == "known_format" else 1)
        assert result.raw_outputs[0] == details["body"]
        if failure == "known_format":
            assert result.usage_records[0]["total_tokens"] == 19
            row = session.scalar(select(Annotation))
            assert row.status is AnnotationStatus.UNKNOWN and row.speaker_id is None
        else:
            assert not list(session.scalars(select(Annotation)))
        assert not list(session.scalars(select(SpeakerGroup)))

    _with_session(migrated_settings, body)


@pytest.mark.parametrize("change", ["text", "kind", "gap"])
def test_short_window_bad_original_context_is_rejected_before_model_or_write(
    migrated_client, migrated_settings, change,
):
    _import(migrated_client)

    def body(session, inputs, version):
        window = _window(inputs, _targets(inputs)[:1])
        task = _expression_task(window)
        if change == "gap":
            task.gap_next_quote.clear()
        else:
            task.context[0][change] = "not the original text"
        adapter = FakeProviderAdapter(script=[{}])
        positions, gaps, evidence = _positions(inputs, window)
        with pytest.raises(ValueError, match="actual.*window"):
            asyncio.run(run_window(session, adapter=adapter, window=window, state=SceneState(),
                                   book_version_id=inputs.book_version_id, quote_positions=positions,
                                   gap_positions=gaps, evidence_positions=evidence,
                                   expression_task=task))
        assert not adapter.calls
        assert not list(session.scalars(select(Annotation)))
        assert not list(session.scalars(select(Scene)))

    _with_session(migrated_settings, body)


@pytest.mark.parametrize("cap", [AnnotationStatus.ACCEPTED, AnnotationStatus.UNKNOWN, "PROVISIONAL"])
def test_expression_acceptance_ceiling_cannot_upgrade_or_forge_domain_status(
    migrated_client, migrated_settings, cap,
):
    _import(migrated_client)

    def body(session, inputs, version):
        ids = _targets(inputs)[:1]
        window = _window(inputs, ids)
        result = _apply(session, output={"schema_version": "1.1", "labels": [_label(ids[0])]},
                        window=window, state=SceneState(), inputs=inputs,
                        expected_schema_version="1.1", acceptance_ceilings={ids[0]: cap})
        assert not result.validation_ok
        assert "invalid_acceptance_ceiling" in result.validation_codes
        assert not list(session.scalars(select(Annotation)))
        assert not list(session.scalars(select(Scene)))

    _with_session(migrated_settings, body)


@pytest.mark.parametrize("kind", ["thought", "quotation"])
def test_expression_contract_persists_original_kind_and_person(
    migrated_client: TestClient, migrated_settings: Settings, kind: str,
) -> None:
    _import(migrated_client)

    def body(session, inputs, version):
        ids = _targets(inputs)[:2]
        window = _window(inputs, ids)
        evidence = next(f.fragment_id for f in window.fragments
                        if f.fragment_id not in ids and f.text.strip())
        output = {"schema_version": "1.1", "new_speakers": [{
            "temp_ref": "new1", "scene_ref": "scene_current", "first_quote_id": ids[0],
            "name": "少女", "description": "原文中的少女", "evidence_refs": [evidence],
        }], "labels": [
            _label(ids[0], kind=kind, assignment="NEW", speaker_ref="new1",
                   basis="DIRECT", evidence_refs=[evidence]),
            _label(ids[1], kind=kind, assignment="EXISTING", speaker_ref="new1",
                   basis="DIRECT", evidence_refs=[evidence]),
        ]}
        state = SceneState()
        rejected = _apply(session, output=output, window=window, state=state, inputs=inputs)
        assert not rejected.validation_ok
        assert not list(session.scalars(select(Annotation)))
        assert not list(session.scalars(select(SpeakerGroup)))
        result = _apply(session, output=output, window=window, state=state, inputs=inputs,
                        expected_schema_version="1.1")
        assert result.validation_ok, result.validation_codes
        rows = list(session.scalars(select(Annotation)))
        assert len(rows) == 2
        assert {r.kind for r in rows} == {QuoteKind(kind)}
        assert {r.status for r in rows} == {AnnotationStatus.ACCEPTED}
        assert rows[0].speaker_id and rows[0].speaker_id == rows[1].speaker_id
        assert len(list(session.scalars(select(SpeakerGroup)))) == 1
        assert not state.recent_turns  # Thoughts/quotes are not real conversational turns.
        original = rows[0]
        original.user_locked = True
        original.status = AnnotationStatus.USER_CONFIRMED
        original.source = AnnotationSource.USER
        unknown = {"schema_version": "1.1", "labels": [
            _label(q, kind=kind, assignment="UNKNOWN", basis="INSUFFICIENT",
                   evidence_refs=[]) for q in ids]}
        result = _apply(session, output=unknown, window=window, state=state, inputs=inputs,
                        expected_schema_version="1.1", locked_quote_ids={ids[0]})
        assert result.validation_ok
        assert original.status is AnnotationStatus.USER_CONFIRMED and original.speaker_id
        second = session.scalar(select(Annotation).where(Annotation.quote_id == ids[1]))
        assert second.status is AnnotationStatus.UNKNOWN and second.speaker_id is None
        assert len(list(session.scalars(select(SpeakerGroup)))) == 1
        assert result.skipped_locked_quote_ids == [ids[0]]

    _with_session(migrated_settings, body)


@pytest.mark.parametrize("kind", ["thought", "quotation"])
def test_expression_unknown_does_not_create_people(
    migrated_client: TestClient, migrated_settings: Settings, kind: str,
) -> None:
    _import(migrated_client)

    def body(session, inputs, version):
        ids = _targets(inputs)[:1]
        window = _window(inputs, ids)
        bad = {"schema_version": "1.1", "labels": [
            _label(ids[0], kind=kind, assignment="EXISTING", speaker_ref="missing",
                   basis="DIRECT", evidence_refs=[ids[0]])]}
        result = _apply(session, output=bad, window=window, state=SceneState(), inputs=inputs,
                        expected_schema_version="1.1")
        assert not result.validation_ok and "unknown_speaker" in result.validation_codes
        assert not list(session.scalars(select(Annotation)))
        assert not list(session.scalars(select(Scene)))
        output = {"schema_version": "1.1", "labels": [_label(ids[0], kind=kind)]}
        result = _apply(session, output=output, window=window, state=SceneState(), inputs=inputs,
                        expected_schema_version="1.1")
        assert result.validation_ok
        row = session.scalar(select(Annotation))
        assert row.status is AnnotationStatus.UNKNOWN and row.kind is QuoteKind(kind)
        assert row.speaker_id is None
        assert not list(session.scalars(select(SpeakerGroup)))
        assert not list(session.scalars(select(BookCharacter)))

    _with_session(migrated_settings, body)


def _import(client: TestClient) -> dict:
    response = client.post(
        "/api/books/import",
        files={"file": ("sample.txt", SAMPLE.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def _targets(inputs: WindowInputs) -> list[str]:
    return [quote.quote_id for quote in inputs.quotes if quote.nesting_depth == 0]


def _window(inputs: WindowInputs, ids: list[str]):  # noqa: ANN202
    plan = plan_windows(inputs, target_quote_ids=ids)
    assert len(plan.windows) == 1, "测试期望单窗口"
    return plan.windows[0]


def _positions(inputs: WindowInputs, window):  # noqa: ANN001, ANN202
    quote_positions = {quote.quote_id: (quote.start_cp, quote.end_cp) for quote in inputs.quotes}
    gap_positions = {gap.gap_id: gap.start_cp for gap in inputs.gaps}
    evidence_positions = dict(gap_positions)
    evidence_positions.update({quote.quote_id: quote.start_cp for quote in inputs.quotes})
    for fragment in window.fragments:
        evidence_positions.setdefault(fragment.fragment_id, fragment.end_cp)
    return quote_positions, gap_positions, evidence_positions


def _label(quote_id: str, **overrides) -> dict:
    payload = {
        "quote_id": quote_id,
        "scene_ref": "scene_current",
        "kind": "speech",
        "assignment": "UNKNOWN",
        "speaker_ref": None,
        "basis": "INSUFFICIENT",
        "evidence_refs": [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("same_name", ["女同学", "小雨"])
def test_explicit_new_identities_do_not_merge_by_name_and_replay_is_stable(
    migrated_client, migrated_settings, same_name,
):
    _import(migrated_client)

    def body(session, inputs, version):
        old = BookCharacter(book_version_id=version.id, canonical_name=same_name,
                            description="人工资料", source="USER", user_confirmed=True)
        session.add(old)
        session.flush()
        ids = _targets(inputs)[:2]
        window = _window(inputs, ids)
        output = {"schema_version": "1.1", "new_speakers": [
            {"temp_ref": f"n{i}", "scene_ref": "scene_current", "first_quote_id": q,
             "name": same_name, "description": f"不同身份{i}", "evidence_refs": [q]}
            for i, q in enumerate(ids)
        ], "labels": [_label(q, assignment="NEW", speaker_ref=f"n{i}", basis="DIRECT",
                              evidence_refs=[q]) for i, q in enumerate(ids)]}
        state = SceneState(confirmed_characters=[ConfirmedCharacter(old.id, same_name)])
        result = _apply(session, output=output, window=window, state=state, inputs=inputs,
                        expected_schema_version="1.1")
        assert result.validation_ok, result.validation_codes
        people = list(session.scalars(select(BookCharacter)))
        assert len(people) == 3
        new_ids = {p.id for p in people if p.id != old.id}
        assert len(new_ids) == 2 and {s.character_id for s in state.participants} == new_ids
        assert old.description == "人工资料" and old.user_confirmed
        assert all(not p.user_confirmed and p.temp_key for p in people if p.id in new_ids)
        restored = SceneState.from_snapshot(state.snapshot())
        replay = _apply(session, output=output, window=window, state=restored, inputs=inputs,
                        expected_schema_version="1.1")
        assert replay.validation_ok, replay.validation_codes
        assert len(list(session.scalars(select(BookCharacter)))) == 3
        assert {s.character_id for s in restored.participants} == new_ids

    _with_session(migrated_settings, body)


def test_explicit_missing_character_id_is_rejected_without_state_or_database_changes(
    migrated_client, migrated_settings,
):
    _import(migrated_client)

    def body(session, inputs, version):
        ids = _targets(inputs)[:1]
        state = SceneState(confirmed_characters=[ConfirmedCharacter("missing", "小雨")])
        before = state.snapshot()
        output = {"schema_version": "1.1", "new_speakers": [{
            "temp_ref": "n1", "scene_ref": "scene_current", "first_quote_id": ids[0],
            "name": "小雨", "description": "不存在的候选", "character_id": "missing",
            "evidence_refs": ids,
        }], "labels": [_label(ids[0], assignment="NEW", speaker_ref="n1", basis="DIRECT",
                              evidence_refs=ids)]}
        result = _apply(session, output=output, window=_window(inputs, ids), state=state,
                        inputs=inputs, expected_schema_version="1.1")
        assert not result.validation_ok
        assert result.validation_codes == ["invalid_explicit_character_id"]
        assert state.snapshot() == before
        assert not list(session.scalars(select(BookCharacter)))
        assert not list(session.scalars(select(Scene)))

    _with_session(migrated_settings, body)


def test_explicit_existing_id_uses_own_alias_without_name_conflict(
    migrated_client, migrated_settings,
):
    _import(migrated_client)

    def body(session, inputs, version):
        person = BookCharacter(book_version_id=version.id, canonical_name="小雨",
                               aliases_json='["女同学"]', description="人工资料",
                               source="USER", user_confirmed=True)
        other = BookCharacter(book_version_id=version.id, canonical_name="女同学",
                              description="另一个人", source="MODEL", user_confirmed=False)
        session.add_all([person, other])
        session.flush()
        ids = _targets(inputs)[:2]
        state = SceneState(confirmed_characters=[
            ConfirmedCharacter(person.id, "小雨", ("女同学",), "人工资料"),
            ConfirmedCharacter(other.id, "女同学"),
        ])
        output = {"schema_version": "1.1", "new_speakers": [{
            "temp_ref": "c1", "scene_ref": "scene_current", "first_quote_id": ids[0],
            "name": "女同学", "description": "人物声明", "character_id": person.id,
            "evidence_refs": ids[:1],
        }], "labels": [
            _label(ids[0], assignment="NEW", speaker_ref="c1", basis="DIRECT",
                   evidence_refs=ids[:1]),
            _label(ids[1], assignment="EXISTING", speaker_ref="c1", speaker_name="女同学",
                   basis="DIRECT", evidence_refs=ids[:1]),
        ]}
        result = _apply(session, output=output, window=_window(inputs, ids), state=state,
                        inputs=inputs, expected_schema_version="1.1")
        assert result.validation_ok, result.validation_codes
        assert not any(w.startswith("confirmed_identity_conflict") for w in result.warnings)
        assert len(list(session.scalars(select(BookCharacter)))) == 2
        assert {s.character_id for s in state.participants} == {person.id}
        assert person.description == "人工资料" and person.user_confirmed
        refreshed = next(p for p in state.book_characters if p.character_id == person.id)
        assert refreshed.source == "USER" and refreshed.user_confirmed

    _with_session(migrated_settings, body)


def _thought(quote_id: str) -> dict:
    return _label(
        quote_id,
        kind="thought",
        assignment=None,
        speaker_ref=None,
        basis=None,
        evidence_refs=[quote_id],
    )


def _speech(
    quote_id: str,
    *,
    assignment: str,
    speaker_ref: str | None,
    basis: str = "DIRECT",
    evidence_ref: str | None = None,
    speaker_name: str | None = None,
) -> dict:
    return _label(
        quote_id,
        assignment=assignment,
        speaker_ref=speaker_ref,
        basis=basis,
        speaker_name=speaker_name,
        evidence_refs=[evidence_ref or quote_id],
    )


def _with_session(settings: Settings, body):  # noqa: ANN001, ANN202
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    try:
        with transaction(factory) as session:
            version = session.execute(select(BookVersion)).scalars().first()
            assert version is not None
            inputs = load_window_inputs(session, settings, version)
            return body(session, inputs, version)
    finally:
        engine.dispose()


def _run(session, *, script: list, window, state: SceneState, inputs, **kwargs):  # noqa: ANN001, ANN202
    quote_positions, gap_positions, evidence_positions = _positions(inputs, window)
    return asyncio.run(
        run_window(
            session,
            adapter=FakeProviderAdapter(script=script),
            window=window,
            state=state,
            book_version_id=inputs.book_version_id,
            quote_positions=quote_positions,
            gap_positions=gap_positions,
            evidence_positions=evidence_positions,
            retry_policy=RetryPolicy(max_format_retries=1),
            **kwargs,
        )
    )


def _apply(session, *, output, window, state: SceneState, inputs, **kwargs):  # noqa: ANN001, ANN202
    quote_positions, gap_positions, evidence_positions = _positions(inputs, window)
    return apply_window(
        session,
        book_version_id=inputs.book_version_id,
        window=window,
        output=output,
        state=state,
        quote_positions=quote_positions,
        gap_positions=gap_positions,
        evidence_positions=evidence_positions,
        **kwargs,
    )


def test_insufficient_evidence_never_creates_people(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    """证据不足时全部 UNKNOWN，不新建任何人物，并进入待确认队列。"""

    _import(migrated_client)

    def body(session, inputs, version):  # noqa: ANN001, ANN202
        targets = _targets(inputs)[:3]
        window = _window(inputs, targets)
        result = _run(session, script=[], window=window, state=SceneState(), inputs=inputs)
        assert result.ok is True, result.application.validation_codes
        return (
            list(session.execute(select(Annotation)).scalars()),
            list(session.execute(select(SpeakerGroup)).scalars()),
            list(session.execute(select(ReviewItem)).scalars()),
        )

    annotations, groups, reviews = _with_session(migrated_settings, body)

    assert annotations and all(item.status is AnnotationStatus.UNKNOWN for item in annotations)
    assert all(item.speaker_id is None for item in annotations)
    assert groups == []  # UNKNOWN 不制造新人
    assert reviews and all(item.reason is ReviewReason.UNKNOWN_SPEAKER for item in reviews)


def test_three_speakers_and_consecutive_same_speaker(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    """不强制轮流；同一人连续发言属于同一分组。"""

    _import(migrated_client)

    def body(session, inputs, version):  # noqa: ANN001, ANN202
        targets = _targets(inputs)[:3]
        window = _window(inputs, targets)
        output = {
            "schema_version": "1.0",
            "new_speakers": [
                {
                    "temp_ref": "new1",
                    "scene_ref": "scene_current",
                    "first_quote_id": targets[0],
                    "description": "少女",
                    "evidence_refs": [targets[0]],
                },
                {
                    "temp_ref": "new2",
                    "scene_ref": "scene_current",
                    "first_quote_id": targets[2],
                    "description": "少年",
                    "evidence_refs": [targets[2]],
                },
            ],
            "labels": [
                _speech(
                    targets[0],
                    assignment="NEW",
                    speaker_ref="new1",
                    speaker_name="绫濑沙季",
                    evidence_ref=targets[1],
                ),
                _speech(targets[1], assignment="EXISTING", speaker_ref="new1", evidence_ref=targets[0]),
                _speech(targets[2], assignment="NEW", speaker_ref="new2", evidence_ref=targets[1]),
            ],
        }
        result = _apply(session, output=output, window=window, state=SceneState(), inputs=inputs)
        assert result.validation_ok is True, result.validation_codes
        groups = list(
            session.execute(select(SpeakerGroup).order_by(SpeakerGroup.display_label)).scalars()
        )
        annotations = {item.quote_id: item for item in session.execute(select(Annotation)).scalars()}
        return targets, groups, annotations

    targets, groups, annotations = _with_session(migrated_settings, body)

    assert [group.display_label for group in groups] == ["S1", "S2"]
    assert groups[0].canonical_name == "绫濑沙季"
    assert groups[0].description == "少女"
    assert annotations[targets[0]].speaker_id == annotations[targets[1]].speaker_id
    assert annotations[targets[2]].speaker_id != annotations[targets[0]].speaker_id
    assert all(item.status is AnnotationStatus.ACCEPTED for item in annotations.values())


def test_confirmed_identity_wins_when_model_name_conflicts(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    _import(migrated_client)

    def body(session, inputs, version):  # noqa: ANN001, ANN202
        target = _targets(inputs)[0]
        window = _window(inputs, [target])
        session.add_all(
            [
                BookCharacter(
                    id="char-yuta",
                    book_version_id=version.id,
                    canonical_name="浅村悠太",
                    aliases_json="[]",
                    description="用户确认的主人公",
                    source="USER",
                    user_confirmed=True,
                ),
                BookCharacter(
                    id="char-maaya",
                    book_version_id=version.id,
                    canonical_name="奈良坂真绫",
                    aliases_json="[]",
                    description="用户确认的同学",
                    source="USER",
                    user_confirmed=True,
                ),
            ]
        )
        session.flush()
        state = SceneState(
            confirmed_characters=[
                ConfirmedCharacter("char-yuta", "浅村悠太", description="用户确认的主人公"),
                ConfirmedCharacter("char-maaya", "奈良坂真绫", description="用户确认的同学"),
            ],
            pov_character_id="char-yuta",
        )
        state.add_speaker(
            first_quote_id=target,
            canonical_name="浅村悠太",
            description="用户确认的主人公",
        )
        state.sync_confirmed_participants()
        output = {
            "schema_version": "1.0",
            "labels": [
                _speech(
                    target,
                    assignment="EXISTING",
                    speaker_ref="S1",
                    speaker_name="奈良坂真绫",
                )
            ],
        }
        result = _apply(session, output=output, window=window, state=state, inputs=inputs)
        group = session.execute(select(SpeakerGroup)).scalar_one()
        return result, group

    result, group = _with_session(migrated_settings, body)
    assert result.validation_ok is True
    assert group.character_id == "char-yuta"
    assert group.canonical_name == "浅村悠太"
    assert group.description == "用户确认的主人公"
    assert any(item.startswith("confirmed_identity_conflict:S1:奈良坂真绫") for item in result.warnings)


def test_revealed_name_links_group_and_survives_scene_change(migrated_client, migrated_settings):
    _import(migrated_client)

    def body(session, inputs, version):
        person = BookCharacter(
            id="girl", book_version_id=version.id, canonical_name="高个子女生",
            aliases_json="[]", description="已确认的补习班同学", source="USER",
            user_confirmed=True,
        )
        session.add(person)
        session.flush()
        identities = [ConfirmedCharacter("girl", "高个子女生", description=person.description)]
        for index, name in enumerate(["藤波夏帆", "藤波同学"]):
            target = _targets(inputs)[index]
            state = SceneState(book_characters=identities)
            window = _window(inputs, [target])
            output = {
                "new_speakers": [{"temp_ref": "new1", "scene_ref": "scene_current",
                                  "first_quote_id": target, "character_id": "girl",
                                  "name": name, "description": "即前文高个子女生",
                                  "evidence_refs": [target]}],
                "labels": [_speech(target, assignment="NEW", speaker_ref="new1")],
            }
            result = _run(session, script=[output], window=window, state=state, inputs=inputs)
            assert result.application.validation_ok, result.application.validation_codes
            identities = state.book_characters
        target = _targets(inputs)[2]
        result = _run(session, script=[{
            "new_speakers": [{"temp_ref": "name_update", "scene_ref": "scene_current",
                              "first_quote_id": target, "character_id": "girl",
                              "name": "夏帆同学", "description": "同一人在此处被称作夏帆",
                              "evidence_refs": [target]}],
            "labels": [_speech(target, assignment="EXISTING", speaker_ref="S1",
                               speaker_name="夏帆同学")],
        }], window=_window(inputs, [target]), state=state, inputs=inputs)
        assert result.application.validation_ok
        groups = list(session.scalars(select(SpeakerGroup)))
        assert len(groups) == 2
        assert {group.character_id for group in groups} == {"girl"}
        assert {group.canonical_name for group in groups} == {"高个子女生"}
        assert set(json.loads(person.aliases_json)) == {"藤波夏帆", "藤波同学", "夏帆同学"}
        assert person.description == "已确认的补习班同学"

    _with_session(migrated_settings, body)


def test_update_keeps_scene_and_break_opens_new_scene(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    """/：UPDATE 不切场景；BREAK 关闭当前场景并开新场景。"""

    _import(migrated_client)

    def body(session, inputs, version):  # noqa: ANN001, ANN202
        targets = _targets(inputs)
        window = _window(inputs, targets)
        gap_ids = [gap.gap_id for gap in inputs.gaps if gap.gap_id in window.fragment_ids]
        assert len(gap_ids) >= 2
        state = SceneState()

        update_output = {
            "schema_version": "1.0",
            "gap_decisions": [{"gap_id": gap_ids[0], "decision": "UPDATE", "evidence_refs": []}],
            "labels": [_thought(target) for target in targets],
        }
        first = _apply(session, output=update_output, window=window, state=state, inputs=inputs)
        assert first.validation_ok is True, first.validation_codes
        scenes_after_update = list(session.execute(select(Scene)).scalars())
        assert len(scenes_after_update) == 1
        assert state.status.value == "OPEN"

        break_output = {
            "schema_version": "1.0",
            "gap_decisions": [{"gap_id": gap_ids[-1], "decision": "BREAK", "evidence_refs": []}],
            "scene_updates": [
                {
                    "temp_ref": "scene_2",
                    "after_gap_id": gap_ids[-1],
                    "starts_at_quote_id": targets[-1],
                    "evidence_refs": [],
                }
            ],
            "labels": [
                {
                    **_thought(target),
                    "scene_ref": "scene_2" if target == targets[-1] else "scene_current",
                }
                for target in targets
            ],
        }
        second = _apply(session, output=break_output, window=window, state=state, inputs=inputs)
        assert second.validation_ok is True, second.validation_codes
        scenes = list(session.execute(select(Scene).order_by(Scene.start_cp)).scalars())
        memberships = list(session.execute(select(SceneMembership)).scalars())
        return scenes, second, memberships, state

    scenes, second, memberships, state = _with_session(migrated_settings, body)

    assert len(scenes) == 2
    assert scenes[0].status.value == "CLOSED" and scenes[0].end_cp is not None
    assert scenes[1].status.value == "OPEN"
    assert second.closed_scene_ids == [scenes[0].id]
    assert state.scene_ref == "scene_2"
    assert memberships


def test_locked_annotation_is_never_overwritten(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    """用户确认（user_locked）优先，晚到的模型结果不得覆盖。"""

    _import(migrated_client)

    def body(session, inputs, version):  # noqa: ANN001, ANN202
        target = _targets(inputs)[0]
        window = _window(inputs, [target])
        locked = Annotation(
            quote_id=target,
            kind=QuoteKind.SPEECH,
            assignment=None,
            basis=None,
            status=AnnotationStatus.USER_CONFIRMED,
            source=AnnotationSource.USER,
            user_locked=True,
        )
        session.add(locked)
        session.flush()

        result = _apply(
            session,
            output={"schema_version": "1.0", "labels": [_label(target, evidence_refs=[target])]},
            window=window,
            state=SceneState(),
            inputs=inputs,
            locked_quote_ids={target},
        )
        stored = session.execute(
            select(Annotation).where(Annotation.quote_id == target)
        ).scalar_one()
        history = list(
            session.execute(
                select(AnnotationHistory).where(AnnotationHistory.annotation_id == locked.id)
            ).scalars()
        )
        return result, stored, history, target

    result, stored, history, target = _with_session(migrated_settings, body)

    assert result.skipped_locked_quote_ids == [target]
    assert stored.status is AnnotationStatus.USER_CONFIRMED and stored.user_locked is True
    assert history == []


def test_identity_preserved_across_windows(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    """/：跨窗口沿用同一场景与分组（临时引用映射到稳定 ID）。"""

    _import(migrated_client)

    def body(session, inputs, version):  # noqa: ANN001, ANN202
        targets = _targets(inputs)[:3]
        state = SceneState()
        first_window = _window(inputs, [targets[0]])
        first_output = {
            "schema_version": "1.0",
            "new_speakers": [
                {
                    "temp_ref": "new1",
                    "scene_ref": "scene_current",
                    "first_quote_id": targets[0],
                    "description": "少女",
                    "evidence_refs": [targets[0]],
                }
            ],
            "labels": [_speech(targets[0], assignment="NEW", speaker_ref="new1")],
        }
        first = _apply(session, output=first_output, window=first_window, state=state, inputs=inputs)
        assert first.validation_ok is True, first.validation_codes
        group_id = state.participants[0].group_id
        scene_id = state.scene_id

        # 第二个窗口：状态携带过来，用稳定 ID 继续标同一人
        second_window = _window(inputs, [targets[1]])
        second_output = {
            "schema_version": "1.0",
            "labels": [_speech(targets[1], assignment="EXISTING", speaker_ref=group_id)],
        }
        second = _apply(
            session, output=second_output, window=second_window, state=state, inputs=inputs
        )
        assert second.validation_ok is True, second.validation_codes
        annotations = {item.quote_id: item for item in session.execute(select(Annotation)).scalars()}
        groups = list(session.execute(select(SpeakerGroup)).scalars())
        return targets, annotations, groups, group_id, scene_id, state

    targets, annotations, groups, group_id, scene_id, state = _with_session(migrated_settings, body)

    assert len(groups) == 1  # 跨窗口沿用同一分组，没有新建
    assert annotations[targets[0]].speaker_id == annotations[targets[1]].speaker_id == group_id
    assert annotations[targets[1]].scene_id == scene_id == state.scene_id
    assert state.participants[0].display_label == "S1"


def test_late_evidence_merge_records_visible_from(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    """/：后文揭示的身份合并记录可见时点（初读时不提前同色）。"""

    _import(migrated_client)

    def body(session, inputs, version):  # noqa: ANN001, ANN202
        targets = _targets(inputs)[:2]
        state = SceneState()

        first_output = {
            "schema_version": "1.0",
            "new_speakers": [
                {
                    "temp_ref": "new1",
                    "scene_ref": "scene_current",
                    "first_quote_id": targets[0],
                    "description": "门外的声音",
                    "evidence_refs": [targets[0]],
                }
            ],
            "labels": [_speech(targets[0], assignment="NEW", speaker_ref="new1")],
        }
        _apply(
            session,
            output=first_output,
            window=_window(inputs, [targets[0]]),
            state=state,
            inputs=inputs,
        )
        first_group = state.participants[0].group_id

        second_output = {
            "schema_version": "1.0",
            "new_speakers": [
                {
                    "temp_ref": "new2",
                    "scene_ref": "scene_current",
                    "first_quote_id": targets[1],
                    "description": "另一个声音",
                    "evidence_refs": [targets[1]],
                }
            ],
            "labels": [_speech(targets[1], assignment="NEW", speaker_ref="new2")],
        }
        _apply(
            session,
            output=second_output,
            window=_window(inputs, [targets[1]]),
            state=state,
            inputs=inputs,
        )
        second_group = state.participants[1].group_id
        assert first_group and second_group and first_group != second_group

        # 后文揭示两个声音是同一个人（带证据 → 允许自动合并）
        merge_window = _window(inputs, [targets[1]])
        reveal_fragment = next(
            fragment
            for fragment in merge_window.fragments
            if fragment.kind.value == "outer_gap"
        )
        reveal_cp = reveal_fragment.end_cp
        quote_positions, gap_positions, evidence_positions = _positions(inputs, merge_window)
        evidence_positions[reveal_fragment.fragment_id] = reveal_cp

        merge_output = {
            "schema_version": "1.0",
            "identity_proposals": [
                {
                    "operation": "MERGE",
                    "input_refs": [first_group, second_group],
                    "output_refs": [first_group],
                    "evidence_refs": [reveal_fragment.fragment_id],
                }
            ],
            "labels": [
                _label(
                    targets[1],
                    assignment="EXISTING",
                    speaker_ref=first_group,
                    basis="DIRECT",
                    evidence_refs=[reveal_fragment.fragment_id],
                )
            ],
        }
        merged = apply_window(
            session,
            book_version_id=version.id,
            window=merge_window,
            output=merge_output,
            state=state,
            quote_positions=quote_positions,
            gap_positions=gap_positions,
            evidence_positions=evidence_positions,
        )
        assert merged.validation_ok is True, merged.validation_codes
        revisions = list(session.execute(select(IdentityRevision)).scalars())
        annotations = {item.quote_id: item for item in session.execute(select(Annotation)).scalars()}
        return revisions, annotations, first_group, reveal_cp, targets

    revisions, annotations, first_group, reveal_cp, targets = _with_session(
        migrated_settings, body
    )

    assert len(revisions) == 1
    assert revisions[0].operation is IdentityOperation.MERGE
    assert revisions[0].visible_from_cp == reveal_cp  # 后文证据 → 可见时点在揭示处
    assert annotations[targets[1]].speaker_id == first_group  # 合并后指向幸存分组


def test_invalid_output_retries_once_then_rejects_without_writing(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    """坏 JSON 只重试一次；两次都坏 → 拒绝提交，且不留下任何自动结果。"""

    _import(migrated_client)

    def body(session, inputs, version):  # noqa: ANN001, ANN202
        window = _window(inputs, [_targets(inputs)[0]])
        result = _run(
            session,
            script=["这不是 JSON", "这也不是 JSON"],
            window=window,
            state=SceneState(),
            inputs=inputs,
        )
        annotations = list(session.execute(select(Annotation)).scalars())
        return result, annotations

    result, annotations = _with_session(migrated_settings, body)

    assert result.attempts == 2  # 有限重试
    assert result.ok is False
    assert result.error_code == "INVALID_MODEL_OUTPUT"
    assert annotations == []  # 拒绝错误提交，不留半成品


def test_bad_then_good_output_is_accepted(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    _import(migrated_client)

    def body(session, inputs, version):  # noqa: ANN001, ANN202
        target = _targets(inputs)[0]
        window = _window(inputs, [target])
        good = {"schema_version": "1.0", "labels": [_thought(target)]}
        result = _run(
            session,
            script=["{坏 JSON", good],
            window=window,
            state=SceneState(),
            inputs=inputs,
        )
        annotations = list(session.execute(select(Annotation)).scalars())
        return result, annotations

    result, annotations = _with_session(migrated_settings, body)

    assert result.attempts == 2
    assert result.ok is True, result.application.validation_codes
    assert len(annotations) == 1
    # 非 speech 的类型判断可以接受，但不带说话人
    assert annotations[0].kind is QuoteKind.THOUGHT
    assert annotations[0].speaker_id is None
    assert annotations[0].status is AnnotationStatus.ACCEPTED


def test_plan_range_still_covers_whole_book(
    migrated_client: TestClient, migrated_settings: Settings
) -> None:
    """回归：整书窗口规划仍然可用。"""

    _import(migrated_client)

    def body(session, inputs, version):  # noqa: ANN001, ANN202
        plan = plan_range(session, migrated_settings, version)
        covered = [quote_id for window in plan.windows for quote_id in window.target_quote_ids]
        return covered, _targets(inputs)

    covered, targets = _with_session(migrated_settings, body)
    assert covered == targets


def test_model_review_reasons_follow_current_result_and_preserve_manual_flags(
    migrated_client: TestClient, migrated_settings: Settings,
) -> None:
    _import(migrated_client)

    def body(session, inputs, version):
        target = _targets(inputs)[0]
        window = _window(inputs, [target])
        state = SceneState()
        assert _run(session, script=[], window=window, state=state, inputs=inputs).ok
        manual = ReviewItem(target_type=ReviewTargetType.QUOTE, quote_id=target,
            reason=ReviewReason.USER_FLAGGED, candidates_json='{"note":"请再次核对"}',
            queue_status=ReviewQueueStatus.PENDING)
        session.add(manual)
        manual_reason = ReviewItem(target_type=ReviewTargetType.QUOTE, quote_id=target,
            reason=ReviewReason.LOW_CONFIDENCE, candidates_json='{"note":"用户要求保留"}',
            queue_status=ReviewQueueStatus.PENDING)
        session.add(manual_reason)
        payload = {"schema_version": "1.0", "new_speakers": [{"temp_ref": "new1",
            "scene_ref": state.scene_ref, "first_quote_id": target, "name": "少女", "description": "拿伞的少女"}],
            "labels": [_speech(target, assignment="NEW", speaker_ref="new1", basis="STYLE_ONLY")]}
        assert _run(session, script=[payload], window=window, state=state, inputs=inputs).ok
        unknown = session.scalar(select(ReviewItem).where(ReviewItem.reason == ReviewReason.UNKNOWN_SPEAKER))
        assert unknown.queue_status is ReviewQueueStatus.RESOLVED
        low = session.scalar(select(ReviewItem).where(ReviewItem.reason == ReviewReason.LOW_CONFIDENCE))
        assert low.candidates_json == '{"note":"用户要求保留"}'
        payload = {"schema_version": "1.0", "labels": [_speech(target,
            assignment="EXISTING", speaker_ref="S1", evidence_ref=inputs.gaps[0].gap_id)]}
        assert _run(session, script=[payload], window=window, state=state, inputs=inputs).ok
        assert low.queue_status is ReviewQueueStatus.PENDING
        assert manual.queue_status is ReviewQueueStatus.PENDING
    _with_session(migrated_settings, body)


def test_valid_but_weaker_recheck_does_not_erase_previous_candidate(
    migrated_client: TestClient, migrated_settings: Settings,
) -> None:
    _import(migrated_client)

    def body(session, inputs, version):
        target = _targets(inputs)[0]
        window = _window(inputs, [target])
        state = SceneState()
        payload = {"schema_version": "1.0", "new_speakers": [{"temp_ref": "new1",
            "scene_ref": state.scene_ref, "first_quote_id": target, "name": "少女", "description": "拿伞的少女"}],
            "labels": [_speech(target, assignment="NEW", speaker_ref="new1", basis="STYLE_ONLY")]}
        assert _run(session, script=[payload], window=window, state=state, inputs=inputs).ok
        annotation = session.scalar(select(Annotation).where(Annotation.quote_id == target))
        before = (annotation.version, annotation.speaker_id, annotation.visible_from_cp)
        positions, gaps, evidence = _positions(inputs, window)
        result = apply_window(session, book_version_id=version.id, window=window, state=state,
            output={"schema_version": "1.0", "labels": [_label(target)]},
            quote_positions=positions, gap_positions=gaps, evidence_positions=evidence,
            preserve_existing_candidates=True)
        assert result.validation_ok
        assert (annotation.version, annotation.speaker_id, annotation.visible_from_cp) == before
        assert annotation.status is AnnotationStatus.PROVISIONAL
        assert not list(session.scalars(select(AnnotationHistory)))
    _with_session(migrated_settings, body)


def test_offline_cache_repair_is_guarded_non_model_and_idempotent(
    migrated_client: TestClient, migrated_settings: Settings,
) -> None:
    from ndr.scenes.repair_reviews import repair_book_reviews
    _import(migrated_client)

    def body(session, inputs, version):
        target = _targets(inputs)[0]
        window = _window(inputs, [target])
        character = BookCharacter(book_version_id=version.id, canonical_name="绫濑沙季")
        session.add(character)
        session.flush()
        state = SceneState(confirmed_characters=[ConfirmedCharacter(character.id, "绫濑沙季")])
        evidence = next(fragment.fragment_id for fragment in window.fragments
                        if fragment.kind.value in {"inner_gap", "outer_gap"})
        payload = {"schema_version": "1.0", "new_speakers": [{"temp_ref": "new1",
            "scene_ref": state.scene_ref, "first_quote_id": target, "name": "绫濑沙季",
            "description": "拿伞的少女", "character_id": character.id, "evidence_refs": [evidence]}],
            "labels": [_speech(target, assignment="NEW", speaker_ref="new1", evidence_ref=evidence)]}
        first = _run(session, script=[payload], window=window, state=state, inputs=inputs)
        assert first.ok and first.attempts == 1
        annotation = session.scalar(select(Annotation).where(Annotation.quote_id == target))
        annotation.status = AnnotationStatus.UNKNOWN
        annotation.assignment = Assignment.UNKNOWN
        annotation.basis = SpeakerBasis.INSUFFICIENT
        annotation.speaker_id = None
        review = ReviewItem(target_type=ReviewTargetType.QUOTE, quote_id=target,
            reason=ReviewReason.UNKNOWN_SPEAKER, annotation_version=1,
            candidates_json='{"reason":"insufficient_evidence"}', queue_status=ReviewQueueStatus.PENDING)
        session.add(review)
        job = Job(book_id=version.book_id, book_version_id=version.id, kind=JobKind.INFERENCE,
                  state=JobState.COMPLETED)
        session.add(job)
        session.flush()
        run = InferenceRun(job_id=job.id, state=InferenceRunState.SUCCEEDED,
            profile_snapshot_json="{}", request_fingerprint="repair-fixture")
        session.add(run)
        session.flush()
        session.add(ResultCache(cache_key="repair-fixture", schema_version="1.0",
                                result_json=json.dumps(payload), created_run_id=run.id))
        session.flush()
        preview = repair_book_reviews(session, version.book_id)
        assert preview["restored_quotes"] == 1
        assert annotation.status is AnnotationStatus.UNKNOWN and annotation.version == 1
        annotation.user_locked = True
        session.flush()
        assert repair_book_reviews(session, version.book_id)["restored_quotes"] == 0
        annotation.user_locked = False
        original_evidence = annotation.evidence_refs_json
        annotation.evidence_refs_json = "[]"
        session.flush()
        assert repair_book_reviews(session, version.book_id)["restored_quotes"] == 0
        annotation.evidence_refs_json = original_evidence
        job.state = JobState.RUNNING
        session.flush()
        with pytest.raises(ValueError, match="排队或运行任务"):
            repair_book_reviews(session, version.book_id, apply=True)
        job.state = JobState.COMPLETED
        session.flush()
        result = repair_book_reviews(session, version.book_id, apply=True)
        assert result["restored_quotes"] == 1 and result["resolved_reason_records"] == 1
        assert annotation.status is AnnotationStatus.ACCEPTED
        assert annotation.speaker_id and annotation.version == 2
        assert review.queue_status is ReviewQueueStatus.RESOLVED
        assert len(list(session.scalars(select(AnnotationHistory)))) == 1
        assert repair_book_reviews(session, version.book_id, apply=True)["restored_quotes"] == 0
        assert job.state is JobState.COMPLETED
    _with_session(migrated_settings, body)
