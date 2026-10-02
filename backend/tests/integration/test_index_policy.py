"""Index definitions must earn their storage: real query evidence, plans and no redundancy."""

from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy import UniqueConstraint, inspect, text

from ndr.storage.base import Base

# Pure FK indexes are justified by referential checks/deletion. Every other read
# index requires a real query consumer AND an unforced representative query plan.
# Values: SQL, consumer relative to ndr, query-field evidence in that consumer.
QUERY_USES = {
    "ix_books_created_at_id": ("SELECT id FROM books ORDER BY created_at,id LIMIT 10", "ingest/query.py", "Book.created_at"),
    "ix_books_source_sha256": ("SELECT id FROM books WHERE source_sha256='sha'", "ingest/service.py", "Book.source_sha256"),
    "ix_corrections_target_id": ("DELETE FROM corrections WHERE target_id='q'", "ingest/deletion.py", "Correction.target_id"),
    "ix_jobs_book_created_id": ("SELECT id FROM jobs WHERE book_id='b' ORDER BY created_at,id LIMIT 10", "jobs/service.py", "Job.book_id"),
    "ix_jobs_digest_state_created": ("SELECT id FROM jobs WHERE request_digest='d' AND state='RUNNING' ORDER BY created_at LIMIT 1", "jobs/service.py", "Job.request_digest"),
    "ix_jobs_state": ("SELECT id FROM jobs WHERE state='RUNNING'", "recovery/service.py", "Job.state"),
    "ix_jobs_version_state": ("SELECT id FROM jobs WHERE book_version_id='v' AND state='RUNNING'", "ingest/chapter_repairs.py", "Job.state"),
    "ix_scenes_version_start": ("SELECT id FROM scenes WHERE book_version_id='v' AND start_cp<=100 ORDER BY start_cp DESC LIMIT 1", "corrections/scenes.py", "Scene.start_cp"),
    "ix_bookmarks_book_created_id": ("SELECT id FROM bookmarks WHERE book_id='b' ORDER BY created_at,id LIMIT 10", "api/bookmarks.py", "Bookmark.created_at"),
    "ix_content_nodes_chapter_start_ordinal": ("SELECT id FROM content_nodes WHERE chapter_id='c' AND start_cp>=100 ORDER BY start_cp,ordinal LIMIT 10", "ingest/query.py", "ContentNode.start_cp"),
    "ix_export_artifacts_fingerprint": ("SELECT id FROM export_artifacts WHERE fingerprint='f'", "exports/service.py", "ExportArtifact.fingerprint"),
    "ix_quotes_chapter_start": ("SELECT id FROM quotes WHERE chapter_id='c' AND start_cp>100 ORDER BY start_cp LIMIT 10", "quotes/service.py", "Quote.start_cp"),
    "ix_text_mappings_version_canonical": ("SELECT id FROM text_mappings WHERE book_version_id='v' AND canonical_start_cp<100 AND canonical_end_cp>0", "quotes/service.py", "TextMapping.canonical_start_cp"),
    "ix_text_mappings_version_ordinal": ("SELECT id FROM text_mappings WHERE book_version_id='v' ORDER BY ordinal LIMIT 10", "quotes/service.py", "TextMapping.ordinal"),
    "ix_gaps_version_start": ("SELECT id FROM gaps WHERE book_version_id='v' AND start_cp>100 ORDER BY start_cp LIMIT 10", "quotes/service.py", "Gap.start_cp"),
    "ix_annotations_scene_dependency": ("SELECT id FROM annotations WHERE scene_id='s' AND dependency_hash='d'", "corrections/invalidator.py", "Annotation.dependency_hash"),
    "ix_review_items_created_id": ("SELECT id FROM review_items ORDER BY created_at,id LIMIT 10", "corrections/review.py", "ReviewItem.created_at"),
    "ix_review_items_queue_status": ("SELECT count(*) FROM review_items WHERE queue_status='PENDING'", "corrections/review.py", "ReviewItem.queue_status"),
}


def test_no_redundant_read_indexes(migrated_engine) -> None:
    inspector = inspect(migrated_engine)
    for table in inspector.get_table_names():
        reads = inspector.get_indexes(table)
        unique_keys = inspector.get_unique_constraints(table)
        keys = reads + unique_keys
        keys.append({"name": "primary key", "column_names": inspector.get_pk_constraint(table)["constrained_columns"]})
        for unique in unique_keys + [index for index in reads if index["unique"]]:
            duplicated = [other["name"] for other in keys if other["name"] != unique["name"]
                          and other["column_names"] == unique["column_names"]]
            assert not duplicated, f"{table}.{unique['name']} has duplicate keys: {duplicated}"
        for index in reads:
            if index["unique"]:
                continue
            columns = index["column_names"]
            covered = [other["name"] for other in keys if other["name"] != index["name"]
                       and other["column_names"][:len(columns)] == columns]
            assert not covered, f"{table}.{index['name']} is covered by {covered}; audit index=True and migrate the old index"


def test_every_read_index_has_a_query_or_fk_purpose(migrated_engine) -> None:
    documented = set()
    inspector = inspect(migrated_engine)
    for table in Base.metadata.sorted_tables:
        # Historical migrations used different constraint names in a few tables.
        # Compare semantics/count, not cosmetic names; do not rebuild users' tables.
        actual_unique = sorted(tuple(key["column_names"]) for key in inspector.get_unique_constraints(table.name))
        expected_unique = sorted(tuple(col.name for col in key.columns) for key in table.constraints if isinstance(key, UniqueConstraint))
        assert actual_unique == expected_unique, f"{table.name}: unique constraint definitions drifted from models"
        foreign_keys = {tuple(item.parent.name for item in fk.elements) for fk in table.foreign_key_constraints}
        for index in table.indexes:
            if index.unique or tuple(col.name for col in index.columns) in foreign_keys:
                continue
            documented.add(index.name)
    assert documented == set(QUERY_USES), "Add real query evidence and a query-plan regression when changing an index; remove obsolete registrations"


def test_query_indexes_still_have_real_consumers_and_are_used(migrated_engine) -> None:
    root = Path(__file__).parents[2] / "src" / "ndr"
    with migrated_engine.connect() as connection:
        for index_name, (sql, source, evidence) in QUERY_USES.items():
            tree = ast.parse((root / source).read_text(encoding="utf-8"))
            query_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Compare) or (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"where", "order_by", "join", "group_by"}
            )]
            references = {f"{node.value.id}.{node.attr}" for query in query_nodes for node in ast.walk(query)
                          if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)}
            assert evidence in references, f"{index_name}: query consumer removed; re-audit index purpose"
            assert "INDEXED BY" not in sql.upper(), "Do not force an unused index to pass its plan test"
            plan = " ".join(str(row) for row in connection.execute(text("EXPLAIN QUERY PLAN " + sql)))
            assert index_name in plan, f"{index_name} not used: {plan}"
