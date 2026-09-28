"""集成测试：单窗口推理引擎。

用 FakeProvider 驱动状态机（不发任何网络请求）：真实模型效果属于评测范畴。
每个用例都用**小的显式窗口**（目标对白必须全部被标注，否则校验会报 missing_targets）。
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select

from ndr.config import Settings
from ndr.context.service import load_window_inputs, plan_range
from ndr.context.window_builder import WindowInputs, plan_windows
from ndr.domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    IdentityOperation,
    QuoteKind,
    ReviewReason,
)
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.llm.validation import RetryPolicy
from ndr.scenes.engine import apply_window
from ndr.scenes.runner import run_window
from ndr.scenes.state import ConfirmedCharacter, SceneState
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.models import (
    Annotation,
    AnnotationHistory,
    BookCharacter,
    BookVersion,
    IdentityRevision,
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
