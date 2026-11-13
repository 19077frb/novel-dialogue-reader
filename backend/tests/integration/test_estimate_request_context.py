"""Local estimates share production identity messages, using authored isolated data."""

import json
from copy import deepcopy

import pytest
from sqlalchemy import func, select

from ndr.context.budget import estimate_tokens, policy_for_version
from ndr.context.service import plan_range
from ndr.domain.enums import CharacterRosterStatus, JobState, ReadingMode
from ndr.jobs.scheduler import run_job
from ndr.jobs.service import dialogue_strategy_range, estimate_inference
from ndr.llm.adapters.fake import FakeProviderAdapter
from ndr.storage.models import (
    BookCharacter,
    BookVersion,
    Chapter,
    ChapterCharacterRoster,
    InferenceRun,
    Job,
    JobWindow,
)
from ndr.storage.transactions import transaction

TEXT = "第一章\n林舟说：「请进。」\n许晴答：「我带来了地图。」\n第二章\n" + "原创后续旅程。" * 200
NAMES = ("林舟", "许晴", "顾宁", "陆欣", "周远", "叶秋", "沈岚")
FUTURE = "未来揭示的灯塔守护者"


def prepare(client, *, confirmed=True, text=TEXT):
    response = client.post(
        "/api/books/import", files={"file": ("estimate.txt", text.encode(), "text/plain")}
    )
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    factory = client.app.state.session_factory
    with transaction(factory) as session:
        version = session.get(BookVersion, data["book_version_id"])
        chapter = session.scalar(select(Chapter).where(
            Chapter.book_version_id == version.id
        ).order_by(Chapter.start_cp))
        people = []
        for name in NAMES:
            description = f"{name}负责检查古桥，记录沿途天气，带着画有船帆的手册。" * 12
            facts = []
            for kind, value, position in (
                ("name", name, 0), ("alias", name + "队长", 0),
                ("description", description, 0),
                ("relation", f"{name}与许晴共同研究山谷里的古桥", 0),
                ("description", FUTURE + "。" + description, chapter.end_cp + 10),
            ):
                facts.append({
                    "kind": kind, "value": value, "visible_from_cp": position,
                    "canonical_sha256": version.canonical_sha256,
                    "source": "user", "source_ref": "authored-estimate-fixture", "accepted": True,
                })
            person = BookCharacter(
                book_version_id=version.id, canonical_name=name,
                aliases_json=json.dumps([name + "队长"]), description=description,
                source="USER", user_confirmed=True, confirmation_source="user",
                identity_facts_json=json.dumps(facts, ensure_ascii=False), first_seen_cp=0,
            )
            session.add(person)
            people.append(person)
        session.flush()
        if confirmed:
            session.add(ChapterCharacterRoster(
                chapter_id=chapter.id, book_version_id=version.id,
                status=CharacterRosterStatus.CONFIRMED,
                confirmed_character_ids_json=json.dumps([p.id for p in people]),
                pov_character_id=people[0].id,
            ))
        scope = {"chapter_id": chapter.id, "start_cp": chapter.start_cp, "end_cp": chapter.end_cp}
    return data, scope


def estimate(client, data, scope, *, mode="reread", rounds=0):
    response = client.post(f"/api/books/{data['book_id']}/estimates", json={
        "range": scope, "reading_mode": mode, "budget": {"max_recheck_rounds": rounds},
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]


class RecordingAdapter(FakeProviderAdapter):
    async def generate_labels(self, payload):
        if not payload.get("output_protocol"):
            return await super().generate_labels(payload)
        self.calls.append({"payload": deepcopy(payload)})
        sent = json.loads(payload["messages"][1]["content"])
        return {
            "labels": [{"q": q, "kind": "speech", "character": None,
                        "basis": "insufficient", "evidence": []} for q in sent["targets"]],
            "breaks": [], "_usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }


@pytest.mark.parametrize("mode", ["initial", "reread"])
@pytest.mark.parametrize("strategy", [
    "legacy", "complete", "complete-blocks", "complete-blocks-isolated",
])
def test_estimate_contains_exact_first_request_with_full_known_profiles(
    fake_provider_client, mode, strategy,
):
    client = fake_provider_client
    data, scope = prepare(client)
    scope.update(dialogue_strategy_range(strategy))
    result = estimate(client, data, scope, mode=mode)
    assert result["window_count"] == 1
    profile = client.post("/api/model-profiles", json={
        "name": "estimate-local", "protocol": "fake-provider", "model": "fake",
        "base_url": "http://127.0.0.1:1", "credential_mode": "none",
    }).json()["data"]
    response = client.post("/api/jobs", json={
        "book_id": data["book_id"], "profile_id": profile["id"], "range": scope,
        "reading_mode": mode, "budget": {"max_recheck_rounds": 0, "max_format_retries": 0},
        "idempotency_key": f"estimate-{strategy}-{mode}", "run_now": False,
    })
    assert response.status_code == 202, response.text
    adapter = RecordingAdapter(usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
    outcome = run_job(client.app.state.session_factory, client.app.state.settings,
                      job_id=response.json()["data"]["id"], adapter_factory=lambda *_: adapter)
    assert outcome.state is JobState.COMPLETED, outcome.errors
    assert len(adapter.calls) == 1
    request = adapter.calls[0]["payload"]
    messages = request["messages"]
    prompt_tokens = sum(estimate_tokens(message["content"]) for message in messages)
    with client.app.state.session_factory() as session:
        plan = plan_range(session, client.app.state.settings,
                          session.get(BookVersion, data["book_version_id"]),
                          start_cp=scope["start_cp"], end_cp=scope["end_cp"],
                          reading_mode=ReadingMode(mode),
                          policy=policy_for_version(scope.get("context_policy")))
    assert prompt_tokens > plan.windows[0].budget["total_tokens"]
    assert result["input_tokens"] == prompt_tokens
    assert result["total_tokens"] == result["windows"][0]["estimated_tokens"]
    sent = json.dumps(messages, ensure_ascii=False)
    assert all(name in sent for name in NAMES)
    assert (FUTURE in sent) is (mode == "reread")


def test_estimation_is_read_only_and_missing_roster_explains_unknown_growth(fake_provider_client):
    client = fake_provider_client
    data, scope = prepare(client, confirmed=False)
    scope.update(dialogue_strategy_range("complete"))
    factory = client.app.state.session_factory
    with factory() as session:
        counts = [session.scalar(select(func.count()).select_from(model))
                  for model in (Job, JobWindow, InferenceRun)]
        profiles = list(session.execute(select(BookCharacter.id, BookCharacter.identity_facts_json)))
        version = session.get(BookVersion, data["book_version_id"])
        result = estimate_inference(
            session, client.app.state.settings, version, start_cp=scope["start_cp"],
            end_cp=scope["end_cp"], chapter_id=scope["chapter_id"],
            output_protocol=scope["output_protocol"], reading_mode=ReadingMode.REREAD,
            policy=policy_for_version(scope["context_policy"]),
        )
        assert not session.new and not session.dirty and not session.deleted
        assert result.input_tokens > 3000
        assert "新增名册和人物档案" in "".join(result.notes)
    api_result = estimate(client, data, scope)
    assert api_result["input_tokens"] == result.input_tokens
    with factory() as session:
        assert counts == [session.scalar(select(func.count()).select_from(model))
                          for model in (Job, JobWindow, InferenceRun)]
        assert profiles == list(session.execute(select(BookCharacter.id, BookCharacter.identity_facts_json)))


def test_estimate_initial_horizon_excludes_future_profiles_and_reread_includes_them(fake_provider_client):
    client = fake_provider_client
    data, scope = prepare(client)
    scope.update(dialogue_strategy_range("complete"))
    initial = estimate(client, data, scope, mode="initial")
    reread = estimate(client, data, scope, mode="reread")
    assert reread["input_tokens"] > initial["input_tokens"]
    # Legacy range-only callers still receive the complete known book catalog.
    old_scope = {key: value for key, value in scope.items() if key != "chapter_id"}
    compatibility = estimate(client, data, old_scope)
    assert compatibility["input_tokens"] > 3000
    invalid = client.post(f"/api/books/{data['book_id']}/estimates", json={
        "range": {**scope, "chapter_id": "missing-chapter"},
    })
    assert invalid.status_code == 422
    other, other_scope = prepare(client, text=TEXT + "不同的原创结尾。")
    foreign = client.post(f"/api/books/{other['book_id']}/estimates", json={"range": scope})
    assert other_scope["chapter_id"] != scope["chapter_id"]
    assert foreign.status_code == 422


@pytest.mark.parametrize("review,rounds,multiplier", [
    (None, 2, 3), ("expression-evidence-review-1", 2, 7),
])
def test_dynamic_input_uses_review_stage_multiplier(fake_provider_client, review, rounds, multiplier):
    client = fake_provider_client
    data, scope = prepare(client)
    scope.update(dialogue_strategy_range("complete"))
    baseline = estimate(client, data, scope)
    if review:
        scope["review_protocol"] = review
    revised = estimate(client, data, scope, rounds=rounds)
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        assert revised[key] == baseline[key] * multiplier
    assert revised["windows"][0]["estimated_tokens"] == revised["total_tokens"]
    assert "上限" in "".join(revised["notes"])
