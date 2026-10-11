"""Window-local prefetching preserves rows while bounding database round trips."""

from sqlalchemy import event, select
from tests.integration.test_attribution_engine import (
    _apply,
    _import,
    _label,
    _targets,
    _window,
    _with_session,
)

from ndr.scenes.state import SceneState
from ndr.storage.models import Annotation, AnnotationHistory, ReviewItem, SceneMembership


def test_window_reads_and_flushes_are_batched(migrated_client, migrated_settings):
    _import(migrated_client)

    def body(session, inputs, version):
        ids = _targets(inputs)
        window = _window(inputs, ids)
        output = {"schema_version": "1.0", "labels": [_label(q) for q in ids]}
        state = SceneState()
        queries, flushes = [], []

        def query(_, __, statement, *rest):
            if statement.lstrip().upper().startswith("SELECT"):
                queries.append(statement)

        def flushed(*_):
            flushes.append(True)

        connection = session.connection()
        event.listen(connection, "before_cursor_execute", query)
        event.listen(session, "after_flush", flushed)
        try:
            result = _apply(session, output=output, window=window, state=state, inputs=inputs)
            assert result.validation_ok
        finally:
            event.remove(connection, "before_cursor_execute", query)
            event.remove(session, "after_flush", flushed)
        # Three prefetches, not four reads and flushes per dialogue.
        assert len(queries) <= 4, queries
        assert len(flushes) <= 2
        assert len(result.annotation_ids) == len(ids)
        assert len(list(session.scalars(select(Annotation)))) == len(ids)
        assert len(list(session.scalars(select(SceneMembership)))) == len(ids)
        reviews = {r.id for r in session.scalars(select(ReviewItem))}
        assert set(result.review_item_ids) == reviews
        repeated = _apply(session, output=output, window=window, state=state, inputs=inputs)
        assert repeated.annotation_ids == result.annotation_ids
        assert {r.id for r in session.scalars(select(ReviewItem))} == reviews
        assert len(list(session.scalars(select(SceneMembership)))) == len(ids)
        assert len(list(session.scalars(select(AnnotationHistory)))) == len(ids)
        assert {r.version for r in session.scalars(select(Annotation))} == {2}

    _with_session(migrated_settings, body)


def test_production_compiles_before_writer_and_rechecks_binding(fake_provider_client, monkeypatch):
    import json

    from tests.integration.test_expression_pipeline import StageAdapter, prepare

    from ndr.jobs import scheduler
    from ndr.storage.models import Job

    client = fake_provider_client
    create, _ = prepare(client)
    job_id = create("lock-free-compilation")["id"]
    factory = client.app.state.session_factory
    with factory() as session:
        job = session.get(Job, job_id)
        job.budget_json = json.dumps({**json.loads(job.budget_json), "max_recheck_rounds": 0})
        session.commit()
    apply = scheduler._apply_payload
    seen = []

    def checked(*args, **kwargs):
        compiled = kwargs.get("precompiled_expression")
        assert compiled is not None
        assert compiled[0] == kwargs["state"].production_expression_task.fingerprint()
        seen.append(True)
        return apply(*args, **kwargs)

    monkeypatch.setattr(scheduler, "_apply_payload", checked)
    adapter = StageAdapter(["林舟"])
    result = scheduler.run_job(factory, client.app.state.settings,
                               job_id=job_id, adapter_factory=lambda *_: adapter)
    assert result.state.value == "COMPLETED"
    assert seen == [True]
