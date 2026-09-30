"""Offline, repeatable project benchmark using an isolated synthetic library.

uv run --project backend python backend/scripts/profile_project.py --label before
uv run --project backend python backend/scripts/profile_project.py --label after
Results and the reusable fixture live in ignored profile_output/, never data/.
"""

from __future__ import annotations

import argparse
import cProfile
import dataclasses
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
import types
from pathlib import Path

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from ndr.characters.directory import directory
from ndr.characters.service import get_roster
from ndr.config import Settings
from ndr.corrections.review import list_review_items, review_counts
from ndr.domain.enums import (
    AnnotationSource,
    AnnotationStatus,
    BookFormat,
    CharacterRosterStatus,
    ExportStylePreset,
    ImportStatus,
    InferenceRunState,
    JobKind,
    JobState,
    QuoteKind,
    ReviewReason,
    ReviewTargetType,
)
from ndr.exports.render import render_book
from ndr.ingest.query import content_nodes, list_books, list_chapters, load_canonical_text
from ndr.ingest.service import import_txt
from ndr.jobs.service import estimate_inference, job_detail, usage_summary
from ndr.scenes.projection import build_projection
from ndr.storage.engine import create_db_engine, create_session_factory
from ndr.storage.migrate import run_migrations
from ndr.storage.models import (
    Annotation,
    Book,
    BookCharacter,
    BookVersion,
    Chapter,
    ChapterCharacterRoster,
    InferenceRun,
    Job,
    JobWindow,
    Quote,
    ReviewItem,
    Scene,
    SpeakerGroup,
)


def prepare(settings: Settings, factory) -> dict:  # noqa: ANN001
    marker = settings.data_dir / "profile-fixture.json"
    if marker.exists():
        return json.loads(marker.read_text(encoding="utf-8"))
    text = "\n".join(
        f"第{chapter + 1}章 性能样例\n"
        + "\n".join(
            "他望向书架，慢慢整理手中的书本。\n"
            f"「这是第{index + 1}句对白，我们继续谈论今天的故事。」"
            for index in range(20)
        )
        for chapter in range(100)
    )
    with factory.begin() as session:
        outcome = import_txt(session, settings, filename="profile.txt", raw=text.encode())
        book, version = outcome.book, outcome.version
        chapters = list(
            session.scalars(
                select(Chapter)
                .where(Chapter.book_version_id == version.id)
                .order_by(Chapter.ordinal)
            )
        )
        characters = [
            BookCharacter(
                book_version_id=version.id,
                canonical_name=f"人物{i}",
                description=f"人物{i}的详细说明",
                user_confirmed=True,
            )
            for i in range(60)
        ]
        session.add_all(characters)
        session.flush()
        for chapter in chapters:
            scene = Scene(
                book_version_id=version.id, start_cp=chapter.start_cp, end_cp=chapter.end_cp
            )
            session.add(scene)
            session.flush()
            quotes = list(
                session.scalars(
                    select(Quote).where(Quote.chapter_id == chapter.id).order_by(Quote.start_cp)
                )
            )
            groups = [
                SpeakerGroup(
                    scene_id=scene.id,
                    display_label=f"S{i + 1}",
                    canonical_name=characters[(chapter.ordinal + i) % 60].canonical_name,
                    character_id=characters[(chapter.ordinal + i) % 60].id,
                    description="本场景的人物说明",
                    first_quote_id=quotes[i].id,
                )
                for i in range(3)
            ]
            session.add_all(groups)
            session.flush()
            for index, quote in enumerate(quotes):
                session.add(
                    Annotation(
                        quote_id=quote.id,
                        scene_id=scene.id,
                        speaker_id=groups[index % 3].id,
                        kind=QuoteKind.SPEECH,
                        source=AnnotationSource.MODEL,
                        status=AnnotationStatus.ACCEPTED,
                    )
                )
                if index % 2 == 0:
                    for reason in (ReviewReason.USER_FLAGGED, ReviewReason.STALE_DEPENDENCY):
                        session.add(
                            ReviewItem(
                                quote_id=quote.id,
                                target_type=ReviewTargetType.QUOTE,
                                reason=reason,
                                candidates_json=json.dumps({"note": "线索" * 200}),
                            )
                        )
            session.add(
                ChapterCharacterRoster(
                    chapter_id=chapter.id,
                    book_version_id=version.id,
                    status=CharacterRosterStatus.CONFIRMED,
                    pov_character_id=characters[0].id,
                    confirmed_character_ids_json=json.dumps([row.id for row in characters]),
                    candidates_json="[]",
                )
            )
        jobs = [
            Job(
                book_id=book.id,
                book_version_id=version.id,
                kind=JobKind.INFERENCE,
                state=JobState.COMPLETED,
                profile_snapshot_json=json.dumps({"model": "offline"}),
            )
            for _ in range(200)
        ]
        session.add_all(jobs)
        session.flush()
        for index in range(2000):
            job = jobs[0] if index < 1000 else jobs[1 + index % 199]
            session.add(
                InferenceRun(
                    job_id=job.id,
                    window_id=f"w{index % 40}",
                    state=InferenceRunState.SUCCEEDED,
                    profile_snapshot_json=json.dumps(
                        {"model": "offline", "params": {"padding": "x" * 512}}
                    ),
                    request_fingerprint=f"profile-{index}",
                    usage_json=json.dumps({"input_tokens": 100, "output_tokens": 10})
                    if index % 10
                    else None,
                )
            )
        for index in range(40):
            session.add(
                JobWindow(job_id=jobs[0].id, window_id=f"w{index}", state=JobState.COMPLETED)
            )
        for index in range(49):
            extra = Book(
                title=f"书架样例{index}",
                format=BookFormat.TXT,
                source_sha256=f"{index:064x}",
                import_status=ImportStatus.COMPLETED,
            )
            session.add(extra)
            session.flush()
            extra_version = BookVersion(
                book_id=extra.id,
                encoding="utf-8",
                parser_version="profile",
                normalization_version="profile",
                canonical_sha256=f"{index:064x}",
                canonical_length_cp=0,
            )
            session.add(extra_version)
            session.flush()
            extra.active_version_id = extra_version.id
        manifest = {
            "book_id": book.id,
            "version_id": version.id,
            "chapter_id": chapters[50].id,
            "start_cp": chapters[50].start_cp,
            "end_cp": chapters[50].end_cp,
            "job_id": jobs[0].id,
            "chapters": len(chapters),
            "quotes": 2000,
            "characters": 60,
            "runs": 2000,
            "books": 50,
        }
    marker.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", choices=("before", "after"), required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--verify-reference", help="Compare results with a local Git revision")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2] / "profile_output"
    root.mkdir(exist_ok=True)
    settings = Settings(data_dir=root / "fixture", credential_backend="session")
    run_migrations(settings)
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    ids = prepare(settings, factory)
    metrics = {"queries": 0, "orm_rows": 0}

    def query(*_):
        metrics["queries"] += 1

    def loaded(*_):
        metrics["orm_rows"] += 1

    event.listen(engine, "before_cursor_execute", query)
    event.listen(Session, "loaded_as_persistent", loaded)

    def invoke(name: str):
        with factory() as session:
            if name == "library":
                return list_books(session, limit=50, cursor=None)
            book = session.get(Book, ids["book_id"])
            version = session.get(BookVersion, ids["version_id"])
            if name == "book":
                return book.title
            if name == "catalogue":
                return list_chapters(session, version.id)
            if name == "content":
                return content_nodes(
                    session,
                    settings,
                    book=book,
                    version=version,
                    chapter_id=ids["chapter_id"],
                    start_cp=None,
                    end_cp=None,
                    limit=200,
                    cursor=None,
                )
            if name == "annotations":
                return build_projection(
                    session,
                    book_id=book.id,
                    book_version_id=version.id,
                    start_cp=ids["start_cp"],
                    end_cp=ids["end_cp"],
                )
            if name == "review_counts":
                return review_counts(session, version.id)
            if name == "review_queue":
                return list_review_items(
                    session,
                    book_version_id=version.id,
                    canonical_text=load_canonical_text(settings, version),
                    limit=100,
                )
            if name == "characters":
                return directory(session, version)
            if name == "roster":
                return get_roster(session, version, session.get(Chapter, ids["chapter_id"]))
            if name == "job_poll":
                return job_detail(session, session.get(Job, ids["job_id"]))
            if name == "usage":
                return usage_summary(session, book.id)
            if name == "estimate":
                return estimate_inference(
                    session, settings, version, start_cp=ids["start_cp"], end_cp=ids["end_cp"]
                )
            projection = build_projection(
                session,
                book_id=book.id,
                book_version_id=version.id,
                start_cp=0,
                end_cp=version.canonical_length_cp,
            )
            return render_book(
                session,
                settings,
                book=book,
                version=version,
                projection_payload=projection.model_dump(),
                style=ExportStylePreset.COLOR_AND_LABEL,
            )

    operations = (
        "library",
        "book",
        "catalogue",
        "content",
        "annotations",
        "review_counts",
        "review_queue",
        "characters",
        "roster",
        "job_poll",
        "usage",
        "estimate",
        "export_render",
    )
    if args.verify_reference:

        def normalized(value):
            if dataclasses.is_dataclass(value):
                value = dataclasses.asdict(value)
            if hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            if isinstance(value, dict):
                result = {key: normalized(item) for key, item in value.items()}
                # Annotation SQL order was never contractual; compare by quote ID.
                if "identity_reverts" in result:
                    result["items"].sort(key=lambda item: item["quote_id"])
                return result
            if isinstance(value, (list, tuple)):
                return [normalized(item) for item in value]
            return value

        expected = {name: normalized(invoke(name)) for name in operations}
        bindings = {
            "ndr.ingest.query": ("list_books", "list_chapters", "content_nodes"),
            "ndr.corrections.review": ("review_counts", "list_review_items"),
            "ndr.characters.directory": ("directory",),
            "ndr.characters.service": ("get_roster",),
            "ndr.jobs.service": ("job_detail", "usage_summary", "estimate_inference"),
            "ndr.scenes.projection": ("build_projection",),
            "ndr.exports.render": ("render_book",),
            "ndr.context.window_builder": (),
        }
        reference_planner = None
        for module_name, names in bindings.items():
            path = "backend/src/" + module_name.replace(".", "/") + ".py"
            source = subprocess.check_output(
                ["git", "show", f"{args.verify_reference}:{path}"],
                cwd=root.parent,
            ).decode("utf-8")
            alias = module_name.rsplit(".", 1)[0] + ".profile_reference"
            module = types.ModuleType(alias)
            module.__package__ = module_name.rsplit(".", 1)[0]
            sys.modules[alias] = module
            exec(compile(source, path, "exec"), module.__dict__)
            for name in names:
                globals()[name] = getattr(module, name)
            if module_name == "ndr.context.window_builder":
                reference_planner = module.plan_windows
        from ndr.context import service as context_service

        context_service.plan_windows = reference_planner
        comparison = {}
        for name in operations:
            actual = normalized(invoke(name))
            # Opaque cursors intentionally changed to complete ordering keys.
            if name in {"library", "review_queue"}:
                actual[1] = expected[name][1] = None
            comparison[name] = {
                "equal": actual == expected[name],
                "sha256": hashlib.sha256(
                    json.dumps(
                        expected[name], ensure_ascii=False, default=str, sort_keys=True
                    ).encode()
                ).hexdigest(),
            }
        (root / "reference-validation.json").write_text(
            json.dumps(comparison, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(comparison, indent=2), flush=True)
        assert all(result["equal"] for result in comparison.values()), "Reference mismatch"
        event.remove(Session, "loaded_as_persistent", loaded)
        engine.dispose()
        return
    results = {}
    for name in operations:
        invoke(name)  # Warm caches; timings exclude allocation/call tracing, not light counters.
        timings = []
        for _ in range(args.repeats):
            metrics.update(queries=0, orm_rows=0)
            start = time.perf_counter()
            invoke(name)
            timings.append((time.perf_counter() - start) * 1000)
        counts = dict(metrics)
        tracemalloc.start()
        invoke(name)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results[name] = {
            **counts,
            "median_ms": round(statistics.median(timings), 3),
            "max_ms": round(max(timings), 3),
            "peak_mib": round(peak / 1024**2, 3),
        }
        print(name, json.dumps(results[name]), flush=True)
    profiler = cProfile.Profile()
    profiler.runcall(invoke, "export_render")
    profiler.dump_stats(root / f"{args.label}.prof")
    output = {
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "fixture": ids,
        "repeats": args.repeats,
        "results": results,
    }
    (root / f"{args.label}.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    event.remove(Session, "loaded_as_persistent", loaded)
    engine.dispose()


if __name__ == "__main__":
    main()
