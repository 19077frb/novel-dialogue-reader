"""Cross-book queue summaries and active target occupancy, without model calls."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(migrated_client: TestClient) -> TestClient:
    return migrated_client


def setup_book(client: TestClient) -> tuple[str, str, str]:
    imported = client.post('/api/books/import', files={
        'file': ('queue.txt', f'第一章\n「早上好。」\n「再见。」\n{uuid4()}'.encode(), 'text/plain')})
    assert imported.status_code == 202, imported.text
    book_id = imported.json()['data']['book_id']
    book = client.get(f'/api/books/{book_id}').json()['data']
    chapter = client.get(f'/api/books/{book_id}/chapters').json()['data'][0]
    return book_id, book['active_version_id'], chapter['id']


def payload(book: tuple[str, str, str], key: str, window: str = 'w1') -> dict:
    return {'book_id': book[0], 'book_version_id': book[1],
            'range': {'chapter_id': book[2], 'start_cp': 0, 'end_cp': 20},
            'selected_window_ids': [window], 'idempotency_key': key,
            'budget': {'max_input_tokens': 1000}, 'run_now': False}


def test_cross_book_queue_pagination_and_no_archives(client: TestClient):
    books = [setup_book(client), setup_book(client)]
    ids = []
    for index, book in enumerate(books):
        response = client.post('/api/jobs', json=payload(book, f'queue-{index}'))
        assert response.status_code == 202, response.text
        ids.append(response.json()['data']['id'])
    page = client.get('/api/jobs/queue?limit=1').json()['data']
    assert page['items'][0]['id'] == ids[1]
    assert page['items'][0]['book_title']
    assert page['items'][0]['chapter_title']
    assert page['items'][0]['selected_window_ids'] == ['w1']
    assert 'range' not in page['items'][0]
    assert 'profile_snapshot' not in page['items'][0]
    next_page = client.get('/api/jobs/queue', params={'limit': 1, 'cursor': page['next_cursor']})
    assert next_page.json()['data']['items'][0]['id'] == ids[0]
    assert next_page.json()['data']['next_cursor'] is None
    client.post(f'/api/jobs/{ids[0]}/pause')
    active = client.get('/api/jobs/queue').json()['data']['items']
    assert [row['id'] for row in active] == [ids[1]]
    history = client.get('/api/jobs/queue?active_only=false').json()['data']['items']
    assert {row['id'] for row in history if row['kind'] == 'INFERENCE'} == set(ids)
    assert client.get('/api/jobs/queue?cursor=invalid').status_code == 422


def test_same_window_different_settings_conflicts_but_other_window_allowed(client: TestClient):
    book = setup_book(client)
    first = client.post('/api/jobs', json=payload(book, 'first'))
    assert first.status_code == 202
    equivalent = client.post('/api/jobs', json=payload(book, 'equivalent'))
    assert equivalent.json()['data']['id'] == first.json()['data']['id']
    other_settings = payload(book, 'different')
    other_settings['budget']['max_input_tokens'] = 2000
    duplicate = client.post('/api/jobs', json=other_settings)
    assert duplicate.status_code == 409, duplicate.text
    assert '已有任务' in duplicate.json()['error']['message']
    assert client.post('/api/jobs', json=payload(book, 'other-window', 'w2')).status_code == 202
    client.post(f"/api/jobs/{first.json()['data']['id']}/pause")
    assert client.post('/api/jobs', json=other_settings).status_code == 202


def test_simultaneous_admission_has_one_owner(client: TestClient):
    book = setup_book(client)
    requests = [payload(book, f'concurrent-{index}') for index in range(2)]
    requests[1]['budget']['max_input_tokens'] = 2000
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda body: client.post('/api/jobs', json=body), requests))
    assert sorted(response.status_code for response in responses) == [202, 409]
    assert len(client.get('/api/jobs/queue').json()['data']['items']) == 1
