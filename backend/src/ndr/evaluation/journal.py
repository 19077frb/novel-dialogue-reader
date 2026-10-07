"""Private experiment ledger; never the reader database or a second scheduler.

Network work happens outside SQLite transactions. An abandoned request is an
unknown outcome, not permission to resend. Reservations are conservative
estimates, not a guarantee that a provider honours its token cap.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from ..llm.errors import ProviderError


class ReconciliationRequired(RuntimeError):
    pass


class TrialStopped(RuntimeError):
    pass


class TrialBudgetExceeded(RuntimeError):
    pass


class SnapshotChanged(RuntimeError):
    pass


class CallJournal:
    def __init__(
        self, path: Path, *, dependency_fingerprint: str, max_calls: int, max_tokens: int
    ) -> None:
        if not path.name.endswith(".trial.sqlite3") or path.is_symlink():
            raise ValueError("Use a dedicated, non-symlink *.trial.sqlite3 experiment file")
        if not dependency_fingerprint or max_calls < 1 or max_tokens < 1:
            raise ValueError("Explicit dependency fingerprint and positive budgets required")
        self.path, self.fingerprint = path.resolve(), dependency_fingerprint
        self.owner = uuid.uuid4().hex
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and ("meta" not in tables or tables - {"meta", "calls", "checkpoints"}):
                raise ValueError("Refuse a database not owned by this experiment ledger")
            db.execute(
                "CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY CHECK(id=1), "
                "fingerprint TEXT NOT NULL, epoch INTEGER NOT NULL, stopped INTEGER NOT NULL, "
                "max_calls INTEGER NOT NULL, max_tokens INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS calls (key TEXT PRIMARY KEY, state TEXT NOT NULL, "
                "owner TEXT NOT NULL, epoch INTEGER NOT NULL, reserved INTEGER NOT NULL, "
                "tokens INTEGER, response TEXT, error TEXT, audit_note TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS checkpoints "
                "(key TEXT PRIMARY KEY, epoch INTEGER NOT NULL, value TEXT NOT NULL)"
            )
            meta = db.execute("SELECT * FROM meta WHERE id=1").fetchone()
            if meta is None:
                db.execute(
                    "INSERT INTO meta VALUES (1,?,0,0,?,?)",
                    (dependency_fingerprint, max_calls, max_tokens),
                )
            elif meta["fingerprint"] != dependency_fingerprint:
                raise SnapshotChanged("Protocol/model/context dependency fingerprint changed")
            elif (meta["max_calls"], meta["max_tokens"]) != (max_calls, max_tokens):
                raise ValueError("Budget changes require explicit versioned configure()")

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def request_key(self, request: dict) -> str:
        if set(request) - {"messages", "max_tokens", "max_tokens_override"}:
            raise ValueError("Journal only accepts message/output-cap payloads, not credentials")
        return hashlib.sha256(
            json.dumps([self.fingerprint, request], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def begin(self, request: dict, *, reservation: int) -> tuple[str, dict | None, int]:
        if reservation < 1:
            raise ValueError("A positive token reservation is required")
        key = self.request_key(request)
        with self._transaction() as db:
            meta = db.execute("SELECT * FROM meta WHERE id=1").fetchone()
            if meta["stopped"]:
                raise TrialStopped("Experiment is stopped; resume explicitly")
            old = db.execute("SELECT * FROM calls WHERE key=?", (key,)).fetchone()
            if old is not None:
                if old["state"] == "complete":
                    return key, {"response": json.loads(old["response"])}, meta["epoch"]
                if old["state"] == "failed_known":
                    return key, {"provider_error": json.loads(old["error"])}, meta["epoch"]
                raise ReconciliationRequired("Request exists without a verified reusable response")
            rows = db.execute("SELECT state,owner,reserved,tokens FROM calls").fetchall()
            if any(
                r["state"] == "unknown" or (r["state"] == "dispatched" and r["owner"] != self.owner)
                for r in rows
            ):
                raise ReconciliationRequired("Unknown or other-owner calls require reconciliation")
            known = sum(r["tokens"] or 0 for r in rows)
            held = sum(r["reserved"] for r in rows if r["state"] == "dispatched")
            if len(rows) >= meta["max_calls"] or known + held + reservation > meta["max_tokens"]:
                raise TrialBudgetExceeded("Call count or token reservation budget reached")
            db.execute(
                "INSERT INTO calls (key,state,owner,epoch,reserved) VALUES (?, 'dispatched',?,?,?)",
                (key, self.owner, meta["epoch"], reservation),
            )
            return key, None, meta["epoch"]

    def finish(self, key: str, *, response: dict | None = None, error: ProviderError | None = None):
        usage = (
            response.get("_usage", {})
            if response is not None
            else (error.details.get("usage", {}) if error else {})
        )
        if not isinstance(usage, dict):
            usage = {}
        tokens = usage.get("total_tokens")
        known = (
            isinstance(tokens, int)
            and not isinstance(tokens, bool)
            and tokens >= 0
            and not usage.get("unknown", False)
        )
        state = "unknown" if not known else ("complete" if response is not None else "failed_known")
        failure_usage = {
            k: usage[k]
            for k in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "cost",
                "currency",
                "unknown",
            )
            if k in usage
        }
        failure = {"kind": error.kind.value, "usage": failure_usage} if error else None
        with self._transaction() as db:
            row = db.execute("SELECT * FROM calls WHERE key=?", (key,)).fetchone()
            if row is None or row["state"] != "dispatched" or row["owner"] != self.owner:
                raise SnapshotChanged("Call reservation no longer belongs to this dispatcher")
            db.execute(
                "UPDATE calls SET state=?,tokens=?,response=?,error=? WHERE key=?",
                (
                    state,
                    tokens if known else None,
                    json.dumps(response, ensure_ascii=False) if response is not None else None,
                    json.dumps(failure) if failure else None,
                    key,
                ),
            )
        return known

    def reconcile(self, key: str, *, tokens: int, audit_note: str, response: dict | None = None):
        """Explicit verified accounting, not an automatic timeout recovery."""
        if (
            not audit_note.strip()
            or not isinstance(tokens, int)
            or isinstance(tokens, bool)
            or tokens < 0
        ):
            raise ValueError("Verified usage and an audit note are required")
        with self._transaction() as db:
            row = db.execute("SELECT * FROM calls WHERE key=?", (key,)).fetchone()
            if row is None or row["state"] not in {"unknown", "dispatched"}:
                raise ValueError("Only unresolved requests can be reconciled")
            if row["state"] == "dispatched" and row["owner"] == self.owner:
                raise ValueError("Do not reconcile this dispatcher's still-active request")
            if response is not None:
                response = {
                    **response,
                    "_usage": {
                        **response.get("_usage", {}),
                        "total_tokens": tokens,
                        "unknown": False,
                    },
                }
            db.execute(
                "UPDATE calls SET state=?,tokens=?,response=?,audit_note=? WHERE key=?",
                (
                    "complete" if response is not None else "accounted_no_result",
                    tokens,
                    json.dumps(response, ensure_ascii=False) if response is not None else None,
                    audit_note,
                    key,
                ),
            )

    def configure(
        self, *, expected_epoch: int, stopped: bool, max_calls: int, max_tokens: int
    ) -> int:
        if max_calls < 1 or max_tokens < 1:
            raise ValueError("Positive budgets required")
        with self._transaction() as db:
            meta = db.execute("SELECT * FROM meta WHERE id=1").fetchone()
            if meta["epoch"] != expected_epoch:
                raise SnapshotChanged("Experiment version changed")
            epoch = expected_epoch + 1
            db.execute(
                "UPDATE meta SET epoch=?,stopped=?,max_calls=?,max_tokens=? WHERE id=1",
                (epoch, stopped, max_calls, max_tokens),
            )
            return epoch

    def checkpoint(
        self, key: str, value: dict, *, expected_epoch: int, expected_value: dict | None = None
    ) -> None:
        with self._transaction() as db:
            meta = db.execute("SELECT * FROM meta WHERE id=1").fetchone()
            if meta["stopped"] or meta["epoch"] != expected_epoch:
                raise SnapshotChanged("Stopped or stale result cannot advance the checkpoint")
            current = db.execute("SELECT value FROM checkpoints WHERE key=?", (key,)).fetchone()
            prior = json.loads(current["value"]) if current else None
            # Same-value replay is idempotent. Replacing a checkpoint must also
            # compare its previous value, not just the experiment-wide epoch.
            if prior != expected_value and prior != value:
                raise SnapshotChanged("Checkpoint changed since it was read")
            if current is None and expected_value is not None:
                raise SnapshotChanged("Expected checkpoint no longer exists")
            db.execute(
                "INSERT INTO checkpoints VALUES (?,?,?) ON CONFLICT(key) DO UPDATE "
                "SET epoch=excluded.epoch,value=excluded.value",
                (key, expected_epoch, json.dumps(value, ensure_ascii=False)),
            )

    def load_checkpoint(self, key: str) -> dict | None:
        with self._transaction() as db:
            row = db.execute("SELECT value FROM checkpoints WHERE key=?", (key,)).fetchone()
            return json.loads(row["value"]) if row else None

    def stats(self) -> dict:
        with self._transaction() as db:
            meta = dict(db.execute("SELECT * FROM meta WHERE id=1").fetchone())
            rows = db.execute("SELECT state,tokens,reserved FROM calls").fetchall()
            return {
                **meta,
                "calls": len(rows),
                "known_tokens": sum(r["tokens"] or 0 for r in rows),
                "unknown_calls": sum(r["state"] in {"unknown", "dispatched"} for r in rows),
                "unresolved_calls": sum(r["state"] == "unknown" for r in rows),
                "reserved_tokens": sum(r["reserved"] for r in rows if r["state"] == "dispatched"),
            }


class JournaledAdapter:
    """Duck-typed generate_labels wrapper; caller supplies the authorised adapter."""

    def __init__(self, adapter, journal: CallJournal):
        self.adapter, self.journal = adapter, journal

    async def generate_labels(self, request: dict) -> dict:
        cap = request.get("max_tokens_override", request.get("max_tokens", 0))
        if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
            raise ValueError("An explicit positive output cap is required")
        reservation = len(json.dumps(request, ensure_ascii=False).encode()) + cap + 256
        key, cached, epoch = self.journal.begin(request, reservation=reservation)
        if cached is not None:
            if "provider_error" in cached:
                from ..llm.errors import ProviderErrorKind

                failure = cached["provider_error"]
                raise ProviderError(
                    ProviderErrorKind(failure["kind"]),
                    "Recorded provider failure",
                    details={"usage": {**failure["usage"], "journal_replay": True}},
                )
            response = cached["response"]
            return {**response, "_usage": {**response.get("_usage", {}), "journal_replay": True}}
        try:
            response = await self.adapter.generate_labels(request)
        except ProviderError as exc:
            known = self.journal.finish(key, error=exc)
            if not known:
                usage = exc.details.get("usage")
                raise ProviderError(
                    exc.kind,
                    exc.message,
                    details={
                        **exc.details,
                        "usage": {
                            **(usage if isinstance(usage, dict) else {}),
                            "total_tokens": None,
                            "unknown": True,
                        },
                    },
                ) from exc
            raise
        except BaseException:
            self.journal.finish(key)
            raise
        known = self.journal.finish(key, response=response)
        state = self.journal.stats()
        if state["stopped"] or state["epoch"] != epoch:
            raise SnapshotChanged("Response usage recorded; stale result must not be submitted")
        if not known:
            usage = response.get("_usage")
            return {
                **response,
                "_usage": {
                    **(usage if isinstance(usage, dict) else {}),
                    "total_tokens": None,
                    "unknown": True,
                },
            }
        return response
