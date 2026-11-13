"""Durable local export finalization, independent of rendering and provider work."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from ..diagnostics import logger
from ..domain.enums import JobState
from ..jobs.local_finalization import defer_settlement
from ..storage.models import ExportArtifact, ExportSnapshot, Job
from ..storage.paths import resolve_within, to_relative
from ..storage.transactions import finish_local_write, sqlite_lock_error

_PENDING = {JobState.QUEUED.value, JobState.RUNNING.value}


def _remove_receipt(path):
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        logger.warning("export_receipt_cleanup_failed error=%s", type(error).__name__)


def _finish_or_defer(factory, job_id, persist):
    try:
        persist()
    except Exception as error:
        if not sqlite_lock_error(error):
            raise
        defer_settlement(factory, job_id, persist)
        return False
    return True


def receipt_path(settings, artifact_id):
    if Path(artifact_id).name != artifact_id or artifact_id in {".", ".."}:
        raise ValueError("导出产物目录无效")
    return resolve_within(settings, Path("exports") / artifact_id / "settlement.json")


def write_receipt(settings, artifact, snapshot, outcome):
    """Atomically publish the validated result before attempting database settlement."""
    target = receipt_path(settings, artifact.id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".settlement-{uuid4()}.tmp")
    payload = {
        "version": 1, "artifact_id": artifact.id,
        "fingerprint": artifact.fingerprint, "snapshot_id": snapshot.id,
        "snapshot_hash": snapshot.snapshot_hash, "format": artifact.format.value,
        "outcome": outcome,
    }
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        _remove_receipt(temporary)


def _load_outcome(settings, artifact, snapshot, path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = {
            "version": 1, "artifact_id": artifact.id,
            "fingerprint": artifact.fingerprint, "snapshot_id": snapshot.id,
            "snapshot_hash": snapshot.snapshot_hash, "format": artifact.format.value,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise ValueError("导出收尾凭据与当前快照不一致")
        outcome = payload["outcome"]
        if (outcome["state"] not in {"COMPLETED", "FAILED"}
                or not isinstance(outcome["validation"], dict)):
            raise ValueError("导出收尾凭据格式无效")
        filename = outcome.get("filename")
        if outcome["state"] == "COMPLETED" and not filename:
            raise ValueError("导出收尾凭据缺少文件")
        if filename:
            if not isinstance(filename, str) or Path(filename).name != filename:
                raise ValueError("导出收尾凭据的文件名无效")
            exported = resolve_within(settings, Path("exports") / artifact.id / filename)
            data = exported.read_bytes()
            if (len(data) != outcome["byte_size"]
                    or hashlib.sha256(data).hexdigest() != outcome["file_sha256"]):
                raise ValueError("导出文件与收尾凭据不一致")
            outcome["relative_path"] = to_relative(settings, exported)
        else:
            outcome["relative_path"] = None
        if outcome["state"] == "COMPLETED" and not outcome["validation"].get("ok"):
            raise ValueError("导出收尾凭据未通过校验")
        return outcome
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return {
            "state": "FAILED", "relative_path": None, "byte_size": None,
            "file_sha256": None,
            "validation": {"ok": False, "detail": "导出收尾凭据或文件已损坏，请重新生成导出"},
        }


def _persist_outcome(factory, artifact_id, binding, outcome, *, job_id):
    def settle(session):
        current = session.get(ExportArtifact, artifact_id)
        if current is None or current.state not in _PENDING:
            return
        current_snapshot = session.get(ExportSnapshot, current.snapshot_id)
        if current_snapshot is None:
            return
        current_binding = (current.fingerprint, current.snapshot_id, current_snapshot.snapshot_hash)
        result = outcome
        if binding != current_binding:
            result = {
                "state": "FAILED", "relative_path": None, "byte_size": None,
                "file_sha256": None,
                "validation": {"ok": False, "detail": "导出快照已改变，请重新生成导出"},
            }
        current.state = result["state"]
        current.relative_path = result.get("relative_path")
        current.file_sha256 = result.get("file_sha256")
        current.byte_size = result.get("byte_size")
        current.validation_json = json.dumps(result["validation"], ensure_ascii=False)
        job = session.get(Job, current.job_id) if current.job_id else None
        if job is not None:
            job.state = JobState(current.state)
            job.last_error = None if current.state == "COMPLETED" else str(
                result["validation"].get("detail") or "导出未通过校验"
            )
            job.progress_json = json.dumps({"stage": current.state.lower()}, ensure_ascii=False)

    finish_local_write(factory, settle, job_id=job_id)


def fail_receipt_write(factory, artifact, snapshot):
    """A failed filesystem write must also leave an explicit, retryable terminal state."""
    binding = (artifact.fingerprint, artifact.snapshot_id, snapshot.snapshot_hash)
    outcome = {
        "state": "FAILED", "relative_path": None, "byte_size": None, "file_sha256": None,
        "validation": {"ok": False, "detail":
                       "无法保存导出收尾记录，请检查数据目录空间和写入权限后重新生成导出"},
    }

    def persist():
        _persist_outcome(factory, artifact.id, binding, outcome, job_id=artifact.job_id)

    return _finish_or_defer(factory, artifact.job_id, persist)


def recover_export(factory, settings, artifact_id):
    """Settle an existing receipt once. Normal reads never hash files or write rows."""
    path = receipt_path(settings, artifact_id)
    if not path.is_file():
        return False
    with factory() as session:
        artifact = session.get(ExportArtifact, artifact_id)
        if artifact is None:
            return False
        if artifact.state not in _PENDING:
            _remove_receipt(path)
            return False
        snapshot = session.get(ExportSnapshot, artifact.snapshot_id)
        if snapshot is None:
            return False
        binding = (artifact.fingerprint, artifact.snapshot_id, snapshot.snapshot_hash)
        outcome = _load_outcome(settings, artifact, snapshot, path)
        job_id = artifact.job_id

    def persist():
        _persist_outcome(factory, artifact_id, binding, outcome, job_id=job_id)
        _remove_receipt(path)

    return _finish_or_defer(factory, job_id, persist)


def recover_exports(factory, settings):
    """Old workers have exited. Recover receipts and fail interrupted unfinished work."""
    with factory() as session:
        ids = list(session.execute(select(ExportArtifact.id, ExportArtifact.job_id).where(
            ExportArtifact.state.in_(_PENDING),
        )))
    for artifact_id, job_id in ids:
        if receipt_path(settings, artifact_id).is_file():
            recover_export(factory, settings, artifact_id)
            continue

        def fail_interrupted(session, target_id=artifact_id):
            artifact = session.get(ExportArtifact, target_id)
            if artifact is None or artifact.state not in _PENDING:
                return
            artifact.state = "FAILED"
            artifact.validation_json = json.dumps({
                "ok": False, "detail": "进程重启中断了导出，请重新生成导出",
            }, ensure_ascii=False)
            job = session.get(Job, artifact.job_id) if artifact.job_id else None
            if job is not None:
                job.state = JobState.FAILED
                job.last_error = "进程重启中断了导出，请重新生成导出"
                job.progress_json = '{"stage": "failed"}'

        def persist_interrupted(action=fail_interrupted, target_job=job_id):
            finish_local_write(factory, action, job_id=target_job)

        _finish_or_defer(factory, job_id, persist_interrupted)
