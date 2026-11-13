"""Stable dialogue occupancy using only isolated original text and local planning."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from ndr.api.errors import ApiError
from ndr.context.budget import policy_for_version
from ndr.context.service import plan_range
from ndr.domain.enums import ErrorCode, JobKind, JobState, ReadingMode
from ndr.jobs import occupancy
from ndr.storage.models import BookVersion, InferenceRun, Job, JobWindow, Quote

TEXT = ('第一章\n' + ''.join(f'「{letter * 1000}」\n' for letter in '甲乙丙丁戊己')
        + '第二章\n「另一章的原创对白。」\n')


@pytest.fixture()
def scope_db(fake_provider_client):
    client = fake_provider_client
    imported = client.post('/api/books/import', files={
        'file': ('occupancy.txt', TEXT.encode(), 'text/plain'),
    })
    assert imported.status_code == 202, imported.text
    settings = client.app.state.settings
    with client.app.state.session_factory() as session:
        version = session.get(BookVersion, imported.json()['data']['book_version_id'])
        yield session, settings, version
        assert session.scalar(select(func.count()).select_from(InferenceRun)) == 0
        session.rollback()


def scope(version, **extra):
    return {'start_cp': 0, 'end_cp': version.canonical_length_cp,
            'reading_mode': 'initial', **extra}


def windows(session, settings, version, target):
    return plan_range(
        session, settings, version, start_cp=target.get('start_cp', 0),
        end_cp=target.get('end_cp'),
        reading_mode=ReadingMode(target.get('reading_mode', 'initial')),
        visible_horizon_cp=target.get('visible_horizon_cp'),
        policy=policy_for_version(target.get('context_policy')),
    ).windows


def record(session, version, target, *, state=JobState.QUEUED, kind=JobKind.INFERENCE):
    row = Job(id=str(uuid4()), book_id=version.book_id, book_version_id=version.id,
              kind=kind, state=state, range_json=json.dumps(target), budget_json='{}')
    session.add(row)
    session.flush()
    return row


def guard(session, settings, version, target, **kwargs):
    occupancy.guard_active_target(session, version.id, JobKind.INFERENCE, target,
                                  settings=settings, **kwargs)


def assert_conflict(session, settings, version, target, expected_id):
    with pytest.raises(ApiError) as error:
        guard(session, settings, version, target)
    assert error.value.code is ErrorCode.RESOURCE_CONFLICT
    assert error.value.details['job_id'] == expected_id


@pytest.mark.parametrize('change', [
    {'reading_mode': 'reread'},
    {'visible_horizon_cp': len(TEXT)},
    {'context_policy': 'context-chapter-1'},
    {'context_policy': 'context-chapter-3'},
])
def test_queued_window_targets_conflict_across_planning_settings(scope_db, change):
    session, settings, version = scope_db
    old = scope(version)
    first = windows(session, settings, version, old)[0]
    old['selected_window_ids'] = [first.window_id]
    owner = record(session, version, old)
    candidate = scope(version, **change)
    changed = windows(session, settings, version, candidate)[0]
    assert changed.window_id != first.window_id
    assert set(first.target_quote_ids) & set(changed.target_quote_ids)
    candidate['selected_window_ids'] = [changed.window_id]
    assert session.scalar(select(func.count()).select_from(JobWindow)) == 0
    assert_conflict(session, settings, version, candidate, owner.id)


def test_disjoint_selected_windows_share_range_and_context_without_conflict(scope_db):
    session, settings, version = scope_db
    target = scope(version)
    planned = windows(session, settings, version, target)
    assert set(planned[0].target_quote_ids).isdisjoint(planned[1].target_quote_ids)
    record(session, version, {**target, 'selected_window_ids': [planned[0].window_id]})
    guard(session, settings, version, {**target, 'selected_window_ids': [planned[1].window_id]})


@pytest.mark.parametrize('state', [JobState.QUEUED, JobState.RUNNING, JobState.PAUSING,
                                   JobState.NEEDS_RECONCILIATION])
@pytest.mark.parametrize('kind', [JobKind.INFERENCE, JobKind.RECHECK, JobKind.RECOMPUTE])
def test_all_active_dialogue_kinds_hold_targets(scope_db, state, kind):
    session, settings, version = scope_db
    target = scope(version)
    owner = record(session, version, target, state=state, kind=kind)
    assert_conflict(session, settings, version, target, owner.id)


@pytest.mark.parametrize('state', [JobState.PAUSED, JobState.PARTIAL, JobState.COMPLETED,
                                   JobState.FAILED, JobState.BUDGET_EXHAUSTED])
def test_terminal_tasks_do_not_hold_targets(scope_db, state):
    session, settings, version = scope_db
    record(session, version, scope(version), state=state)
    guard(session, settings, version, scope(version))


def test_complete_legacy_windows_resolve_exact_targets_including_completed(scope_db):
    session, settings, version = scope_db
    quotes = list(session.scalars(select(Quote).where(Quote.book_version_id == version.id)
                                  .order_by(Quote.start_cp)))
    old = scope(version, selected_window_ids=['old-policy-window'])
    owner = record(session, version, old, state=JobState.NEEDS_RECONCILIATION)
    session.add(JobWindow(job_id=owner.id, window_id='old-policy-window',
                          target_ids_json=json.dumps([quotes[0].id]), state=JobState.COMPLETED))
    session.flush()
    different = scope(version, start_cp=quotes[1].start_cp, end_cp=quotes[1].end_cp)
    guard(session, settings, version, different)
    same = scope(version, start_cp=quotes[0].start_cp, end_cp=quotes[0].end_cp)
    assert_conflict(session, None, version, same, owner.id)


@pytest.mark.parametrize('use_settings', [True, False])
@pytest.mark.parametrize('selected', [True, False])
def test_partial_stored_windows_never_drop_unbuilt_or_completed_targets(
    scope_db, use_settings, selected,
):
    session, settings, version = scope_db
    target = scope(version)
    planned = windows(session, settings, version, target)
    old = {**target, 'selected_window_ids': [w.window_id for w in planned] if selected else None}
    owner = record(session, version, old)
    session.add(JobWindow(job_id=owner.id, window_id=planned[0].window_id,
                          target_ids_json=json.dumps(list(planned[0].target_quote_ids)),
                          state=JobState.COMPLETED))
    session.flush()
    second = session.get(Quote, planned[1].target_quote_ids[0])
    candidate = scope(version, start_cp=second.start_cp, end_cp=second.end_cp)
    assert_conflict(session, settings if use_settings else None, version, candidate, owner.id)


@pytest.mark.parametrize('bad_range', [
    {'selected_window_ids': ['unresolvable-old-window']},
    {'selected_window_ids': ['unresolvable-old-window'], 'reading_mode': 'old-mode'},
    {'start_cp': 'broken', 'end_cp': 'broken'},
    {},
])
def test_unresolvable_legacy_scope_conservatively_holds_range(scope_db, bad_range):
    session, settings, version = scope_db
    owner = record(session, version, bad_range)
    assert_conflict(session, settings, version, scope(version), owner.id)


def test_bad_legacy_json_conservatively_holds_version(scope_db):
    session, settings, version = scope_db
    owner = record(session, version, {})
    owner.range_json = 'invalid json'
    session.flush()
    assert_conflict(session, settings, version, scope(version), owner.id)


def test_empty_selection_has_no_targets(scope_db):
    session, settings, version = scope_db
    record(session, version, scope(version, selected_window_ids=[]))
    guard(session, settings, version, scope(version))
    record(session, version, scope(version))
    guard(session, settings, version, scope(version, selected_window_ids=[]))


def test_omitted_end_and_target_quote_hint_do_not_shrink_actual_range(scope_db):
    session, settings, version = scope_db
    quotes = list(session.scalars(select(Quote).where(Quote.book_version_id == version.id)
                                  .order_by(Quote.start_cp)))
    owner = record(session, version, {'target_quote_id': quotes[0].id})
    last = quotes[-1]
    assert_conflict(session, settings, version,
                    {'start_cp': last.start_cp, 'end_cp': last.end_cp}, owner.id)


def test_explicit_self_exclusion_still_checks_other_owners(scope_db):
    session, settings, version = scope_db
    target = scope(version)
    own = record(session, version, target)
    guard(session, settings, version, target, exclude_job_id=own.id)
    record(session, version, target)
    with pytest.raises(ApiError):
        guard(session, settings, version, target, exclude_job_id=own.id)


def test_self_exclusion_keeps_own_legacy_target_precision(scope_db):
    session, settings, version = scope_db
    quotes = list(session.scalars(select(Quote).where(Quote.book_version_id == version.id)
                                  .order_by(Quote.start_cp)))
    own_target = scope(version, selected_window_ids=['own-legacy'])
    own = record(session, version, own_target, state=JobState.PAUSED)
    other = record(session, version, scope(version, selected_window_ids=['other-legacy']))
    for job, window_id, quote in [(own, 'own-legacy', quotes[0]),
                                  (other, 'other-legacy', quotes[1])]:
        session.add(JobWindow(job_id=job.id, window_id=window_id,
                              target_ids_json=json.dumps([quote.id]), state=JobState.QUEUED))
    session.flush()
    guard(session, None, version, own_target, exclude_job_id=own.id)


def test_roster_occupancy_remains_separate_and_chapter_based(scope_db):
    session, settings, version = scope_db
    owner = record(session, version, {'chapter_id': 'chapter-one'}, kind=JobKind.CHARACTER_ROSTER)
    guard(session, settings, version, scope(version))
    occupancy.guard_active_target(session, version.id, JobKind.CHARACTER_ROSTER,
                                  {'chapter_id': 'chapter-two'}, settings=settings)
    with pytest.raises(ApiError) as error:
        occupancy.guard_active_target(session, version.id, JobKind.CHARACTER_ROSTER,
                                      {'chapter_id': 'chapter-one'}, settings=settings)
    assert error.value.details['job_id'] == owner.id


def test_disjoint_half_open_ranges_skip_expensive_planning(scope_db, monkeypatch):
    session, settings, version = scope_db
    split = TEXT.index('「乙')
    record(session, version, {'start_cp': 0, 'end_cp': split, 'selected_window_ids': ['old']})
    monkeypatch.setattr(occupancy, 'plan_range', lambda *a, **kw: pytest.fail('planned disjoint range'))
    guard(session, settings, version, scope(version, start_cp=split, selected_window_ids=['new']))


def test_one_request_reuses_same_range_plan(scope_db, monkeypatch):
    session, settings, version = scope_db
    target = scope(version)
    planned = windows(session, settings, version, target)
    record(session, version, {**target, 'selected_window_ids': [planned[0].window_id]})
    calls = []
    original = occupancy.plan_range

    def count_plan(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(occupancy, 'plan_range', count_plan)
    guard(session, settings, version, {**target, 'selected_window_ids': [planned[1].window_id]})
    assert len(calls) == 1

@pytest.mark.parametrize('change', [
    {'reading_mode': 'reread'},
    {'visible_horizon_cp': len(TEXT)},
    {'context_policy': 'context-chapter-1'},
    {'context_policy': 'context-chapter-3'},
])
def test_http_admission_compares_frozen_mode_horizon_and_strategy(fake_provider_client, change):
    client = fake_provider_client
    imported = client.post('/api/books/import', files={
        'file': ('admission.txt', TEXT.encode(), 'text/plain'),
    }).json()['data']
    settings = client.app.state.settings
    with client.app.state.session_factory() as session:
        version = session.get(BookVersion, imported['book_version_id'])
        old_scope = scope(version)
        new_scope = scope(version, **change)
        first_id = windows(session, settings, version, old_scope)[0].window_id
        next_id = windows(session, settings, version, new_scope)[0].window_id
    assert first_id != next_id

    def body(target, selected, key):
        # Context policy requires the production output protocol. All requests
        # remain queued locally and never create a model run.
        target = dict(target)
        mode = target.pop('reading_mode', 'initial')
        horizon = target.pop('visible_horizon_cp', None)
        if target.get('context_policy'):
            target['output_protocol'] = 'expression-production-1'
        return {'book_id': imported['book_id'], 'book_version_id': imported['book_version_id'],
                'range': target, 'reading_mode': mode, 'visible_horizon_cp': horizon,
                'selected_window_ids': [selected], 'idempotency_key': key, 'run_now': False}

    first = client.post('/api/jobs', json=body(old_scope, first_id, 'first'))
    assert first.status_code == 202, first.text
    duplicate = client.post('/api/jobs', json=body(new_scope, next_id, 'changed'))
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()['error']['details']['job_id'] == first.json()['data']['id']
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(InferenceRun)) == 0


def test_service_without_settings_is_conservative_for_unbuilt_selections(scope_db):
    from ndr.domain.enums import JobPurpose
    from ndr.jobs.service import create_inference_job
    from ndr.storage.models import Book

    session, _, version = scope_db
    arguments = {'book': session.get(Book, version.book_id), 'version': version,
                 'profile': None, 'purpose': JobPurpose.PROCESS,
                 'budget': {}, 'reading_mode': ReadingMode.INITIAL,
                 'visible_horizon_cp': None}
    first, _ = create_inference_job(session, **arguments, idempotency_key='old',
                                    range_payload=scope(version, selected_window_ids=['old']))
    with pytest.raises(ApiError) as error:
        create_inference_job(session, **arguments, idempotency_key='new',
                             range_payload=scope(version, selected_window_ids=['new']))
    assert error.value.details['job_id'] == first.id


def test_invalid_stored_targets_and_failed_legacy_plan_do_not_free_occupancy(scope_db):
    session, settings, version = scope_db
    old = scope(version, selected_window_ids=['legacy'], context_policy='context-chapter-1',
                visible_horizon_cp=0)
    owner = record(session, version, old)
    session.add(JobWindow(job_id=owner.id, window_id='legacy', target_ids_json='broken',
                          state=JobState.QUEUED))
    session.flush()
    assert_conflict(session, settings, version, scope(version), owner.id)


def test_unavailable_canonical_material_keeps_legacy_range_occupied(scope_db, monkeypatch):
    session, settings, version = scope_db
    owner = record(session, version, scope(version, selected_window_ids=['legacy']))

    def missing_material(*args, **kwargs):
        raise ApiError.not_found('isolated missing canonical fixture')

    monkeypatch.setattr(occupancy, 'plan_range', missing_material)
    assert_conflict(session, settings, version, scope(version), owner.id)
