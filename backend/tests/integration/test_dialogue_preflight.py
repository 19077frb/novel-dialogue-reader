"""Preflight uses original text in isolated databases and never dispatches a provider."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select

from ndr.context.budget import policy_for_version
from ndr.context.service import plan_range
from ndr.domain.enums import JobKind, JobState, ReadingMode
from ndr.storage.models import BookVersion, InferenceRun, Job, JobWindow

TEXT = '第一章\n' + ''.join(f'「{letter * 1000}」\n' for letter in '甲乙丙丁')


@pytest.fixture()
def preflight_book(fake_provider_client):
    client = fake_provider_client
    imported = client.post('/api/books/import', files={
        'file': ('preflight-original.txt', TEXT.encode(), 'text/plain'),
    })
    assert imported.status_code == 202, imported.text
    result = imported.json()['data']
    with client.app.state.session_factory() as session:
        version = session.get(BookVersion, result['book_version_id'])
        plan = plan_range(session, client.app.state.settings, version,
                          reading_mode=ReadingMode.REREAD)
        payload = {'book_id': result['book_id'], 'book_version_id': version.id,
                   'range': {'start_cp': 0, 'end_cp': len(TEXT), 'context_policy': 'context-1'},
                   'reading_mode': 'reread', 'visible_horizon_cp': None,
                   'selected_window_ids': [plan.windows[0].window_id]}
    yield client, payload, plan
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(InferenceRun)) == 0


def occupy(client, payload, *, state=JobState.QUEUED, kind=JobKind.INFERENCE):
    with client.app.state.session_factory() as session:
        job = Job(id=str(uuid4()), book_id=payload['book_id'],
                  book_version_id=payload['book_version_id'], kind=kind, state=state,
                  range_json=json.dumps({**payload['range'],
                                         'reading_mode': payload['reading_mode'],
                                         'visible_horizon_cp': payload['visible_horizon_cp'],
                                         'selected_window_ids': payload['selected_window_ids']}),
                  budget_json='{}')
        session.add(job)
        session.commit()
        return job.id


@pytest.mark.parametrize('state', [JobState.QUEUED, JobState.RUNNING, JobState.PAUSING,
                                   JobState.NEEDS_RECONCILIATION])
@pytest.mark.parametrize('kind', [JobKind.INFERENCE, JobKind.RECHECK, JobKind.RECOMPUTE])
def test_preflight_blocks_all_active_dialogue_targets_without_writes(preflight_book, state, kind):
    client, payload, _plan = preflight_book
    owner = occupy(client, payload, state=state, kind=kind)
    writes = []
    engine = client.app.state.session_factory.kw['bind']

    def track_sql(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE'}:
            writes.append(statement)

    event.listen(engine, 'before_cursor_execute', track_sql)
    try:
        for _ in range(2):
            response = client.post('/api/jobs/preflight', json=payload)
            assert response.status_code == 409, response.text
            assert response.json()['error']['details']['job_id'] == owner
    finally:
        event.remove(engine, 'before_cursor_execute', track_sql)
    assert writes == []
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Job).where(Job.kind != JobKind.IMPORT)) == 1
        assert session.scalar(select(func.count()).select_from(JobWindow)) == 0


def test_preflight_accepts_disjoint_window_with_same_context_and_range(preflight_book):
    client, payload, plan = preflight_book
    occupy(client, payload)
    payload = {**payload, 'selected_window_ids': [plan.windows[1].window_id]}
    response = client.post('/api/jobs/preflight', json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['data'] == {
        'book_id': payload['book_id'], 'book_version_id': payload['book_version_id'],
        'window_count': 1, 'target_count': len(plan.windows[1].target_quote_ids),
    }
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Job).where(Job.kind != JobKind.IMPORT)) == 1


def test_preflight_checks_stable_targets_across_reading_modes_and_policies(preflight_book):
    client, payload, _plan = preflight_book
    owner = occupy(client, payload, state=JobState.NEEDS_RECONCILIATION)
    with client.app.state.session_factory() as session:
        version = session.get(BookVersion, payload['book_version_id'])
        changed = plan_range(session, client.app.state.settings, version,
                             reading_mode=ReadingMode.INITIAL,
                             visible_horizon_cp=len(TEXT),
                             policy=policy_for_version('context-chapter-3'))
    payload = {**payload, 'reading_mode': 'initial', 'visible_horizon_cp': len(TEXT),
               'range': {**payload['range'], 'context_policy': 'context-chapter-3'},
               'selected_window_ids': [changed.windows[0].window_id]}
    response = client.post('/api/jobs/preflight', json=payload)
    assert response.status_code == 409, response.text
    assert response.json()['error']['details']['job_id'] == owner


@pytest.mark.parametrize('state', [JobState.COMPLETED, JobState.FAILED, JobState.PAUSED,
                                   JobState.PARTIAL, JobState.BUDGET_EXHAUSTED])
def test_preflight_terminal_history_does_not_create_replacement_tasks(preflight_book, state):
    client, payload, _plan = preflight_book
    occupy(client, payload, state=state)
    response = client.post('/api/jobs/preflight', json=payload)
    assert response.status_code == 200, response.text
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Job).where(Job.kind != JobKind.IMPORT)) == 1


@pytest.mark.parametrize('change', [
    {'exclude_job_id': 'other-job'},
    {'book_version_id': 'missing-version'},
    {'range': {'start_cp': -1}},
    {'range': {'start_cp': 8, 'end_cp': 2}},
    {'range': {'end_cp': len(TEXT) + 1}},
    {'range': {'start_cp': '0'}},
    {'range': {'chapter_id': 'missing-chapter'}},
    {'range': {'context_policy': 'unknown-policy'}},
    {'selected_window_ids': ['stale-window']},
    {'selected_window_ids': []},
    {'reading_mode': 'unknown-mode'},
])
def test_preflight_rejects_invalid_scope_and_arbitrary_exclusion(preflight_book, change):
    client, payload, _plan = preflight_book
    response = client.post('/api/jobs/preflight', json={**payload, **change})
    assert response.status_code in {400, 404, 422}, response.text
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Job).where(Job.kind != JobKind.IMPORT)) == 0


def test_successful_preflight_never_reserves_or_bypasses_atomic_creation(preflight_book):
    client, payload, _plan = preflight_book
    assert client.post('/api/jobs/preflight', json=payload).status_code == 200
    owner = occupy(client, payload)
    response = client.post('/api/jobs', json={
        **payload, 'idempotency_key': 'new-after-preflight', 'run_now': False,
    })
    assert response.status_code == 409, response.text
    assert response.json()['error']['details']['job_id'] == owner
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Job).where(Job.kind != JobKind.IMPORT)) == 1
