from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from devpilot.clock import Clock, SystemClock
from devpilot.domain.state import GraphState, validate_state
from devpilot.errors import StateConflictError
from devpilot.events.models import ExecutionEvent, TraceView
from devpilot.events.redaction import sanitize_event_value
from devpilot.services.artifacts import ArtifactStore
from devpilot.services.idempotency_store import IdempotencyStoreMixin
from devpilot.services.outbox_store import OutboxStoreMixin
from devpilot.services.plan_store import PlanStoreMixin
from devpilot.services.replay_store import ReplayEvaluationStoreMixin


def default_data_dir() -> Path:
    configured = os.environ.get("DEVPILOT_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".devpilot").resolve()


class SQLiteControlStore(
    ReplayEvaluationStoreMixin,
    PlanStoreMixin,
    OutboxStoreMixin,
    IdempotencyStoreMixin,
):
    """Event-first control projection.

    A transition appends its audit event and advances the optimistic-lock
    projection before LangGraph writes its checkpoint. `checkpoint_revision`
    is confirmed after invoke returns. Startup reconciliation rewinds an
    unconfirmed projection to the last checkpoint and emits an audit event.
    """

    def __init__(self, path: Path, clock: Clock | None = None):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or SystemClock()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._setup()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _setup(self) -> None:
        self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS task_projection (
              task_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              status TEXT NOT NULL,
              state_revision INTEGER NOT NULL,
              checkpoint_revision INTEGER NOT NULL,
              checkpoint_run_id TEXT NOT NULL,
              state_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS execution_events (
              event_id TEXT PRIMARY KEY,
              schema_version INTEGER NOT NULL,
              task_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              state_revision INTEGER,
              node_name TEXT,
              attempt INTEGER,
              sequence_number INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              correlation_id TEXT,
              causation_id TEXT,
              payload_json TEXT NOT NULL,
              artifact_refs_json TEXT NOT NULL DEFAULT '[]',
              checkpoint_confirmed INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              UNIQUE(task_id, run_id, sequence_number)
            );
            CREATE INDEX IF NOT EXISTS execution_events_task_cursor
              ON execution_events(task_id, run_id, sequence_number);
            CREATE TABLE IF NOT EXISTS event_outbox (
              outbox_id TEXT PRIMARY KEY,
              event_id TEXT NOT NULL UNIQUE,
              task_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              sequence_number INTEGER NOT NULL,
              status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              available_at TEXT NOT NULL,
              claimed_by TEXT,
              claimed_at TEXT,
              published_at TEXT,
              stream_id TEXT,
              last_error TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY(event_id) REFERENCES execution_events(event_id)
            );
            CREATE INDEX IF NOT EXISTS event_outbox_delivery
              ON event_outbox(status, available_at, created_at);
            CREATE TABLE IF NOT EXISTS idempotency_keys (
              task_id TEXT NOT NULL,
              operation TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,
              result_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(task_id, operation, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS idempotency_inputs (
              task_id TEXT NOT NULL,
              operation TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,
              request_hash TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(task_id, operation, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS plan_documents (
              task_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              plan_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              parent_version INTEGER,
              content_hash TEXT NOT NULL,
              document_json TEXT NOT NULL,
              artifact_ref_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(task_id, plan_id, version)
            );
            CREATE TABLE IF NOT EXISTS plan_lifecycles (
              task_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              plan_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              status TEXT NOT NULL,
              activated_at TEXT,
              superseded_at TEXT,
              PRIMARY KEY(task_id, plan_id, version)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_plan_per_task
              ON plan_lifecycles(task_id) WHERE status='ACTIVE';
            CREATE TABLE IF NOT EXISTS replan_requests (
              replan_request_id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              request_json TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              consumed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS change_requests (
              change_request_id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              request_json TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              accepted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS change_requests_task
              ON change_requests(task_id, created_at);
            CREATE TABLE IF NOT EXISTS task_owners (
              task_id TEXT PRIMARY KEY,
              subject TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS replay_records (
              replay_id TEXT PRIMARY KEY,
              task_id TEXT NOT NULL,
              run_id TEXT NOT NULL,
              replay_type TEXT NOT NULL,
              source_digest TEXT NOT NULL,
              result_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS replay_records_task
              ON replay_records(task_id, created_at);
            CREATE TABLE IF NOT EXISTS recovery_forks (
              fork_id TEXT PRIMARY KEY,
              source_task_id TEXT NOT NULL,
              source_run_id TEXT NOT NULL,
              recovery_point_id TEXT NOT NULL,
              target_task_id TEXT NOT NULL UNIQUE,
              target_run_id TEXT NOT NULL,
              result_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evaluation_runs (
              evaluation_id TEXT PRIMARY KEY,
              dataset_name TEXT NOT NULL,
              dataset_version TEXT NOT NULL,
              dataset_digest TEXT NOT NULL,
              model TEXT NOT NULL,
              prompt_version TEXT NOT NULL,
              prompt_digest TEXT NOT NULL,
              report_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS evaluation_runs_dataset
              ON evaluation_runs(dataset_digest, created_at);
            """
        )
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(task_projection)").fetchall()}
        if "checkpoint_run_id" not in columns:
            self._conn.execute("ALTER TABLE task_projection ADD COLUMN checkpoint_run_id TEXT")
            self._conn.execute("UPDATE task_projection SET checkpoint_run_id=run_id WHERE checkpoint_run_id IS NULL")
        event_columns = {
            row[1] for row in self._conn.execute("PRAGMA table_info(execution_events)").fetchall()
        }
        event_migrations = {
            "state_revision": "INTEGER",
            "node_name": "TEXT",
            "attempt": "INTEGER",
            "correlation_id": "TEXT",
            "causation_id": "TEXT",
            "artifact_refs_json": "TEXT NOT NULL DEFAULT '[]'",
        }
        for column, declaration in event_migrations.items():
            if column not in event_columns:
                self._conn.execute(
                    f"ALTER TABLE execution_events ADD COLUMN {column} {declaration}"
                )
        evaluation_columns = {
            row[1]
            for row in self._conn.execute(
                "PRAGMA table_info(evaluation_runs)"
            ).fetchall()
        }
        if "prompt_digest" not in evaluation_columns:
            self._conn.execute(
                """ALTER TABLE evaluation_runs
                   ADD COLUMN prompt_digest TEXT NOT NULL DEFAULT ''"""
            )
        self._conn.commit()

    @contextmanager
    def _immediate_transaction(self):
        """Serialize a read-check-write transaction across processes."""

        with self._lock:
            if self._conn.in_transaction:
                raise RuntimeError("nested SQLite control transaction")
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    @staticmethod
    def _sanitize(value: Any) -> Any:
        return sanitize_event_value(value)

    def create_task(self, state: GraphState) -> None:
        state = validate_state(state)
        now = self.clock.now().isoformat()
        with self._immediate_transaction():
            self._conn.execute(
                """INSERT INTO task_projection
                   (task_id, run_id, status, state_revision, checkpoint_revision, checkpoint_run_id, state_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    state["task_id"], state["run_id"], state["status"], state["state_revision"],
                    state["state_revision"], state["run_id"], json.dumps(state, ensure_ascii=False), now,
                ),
            )
            self._append_event_tx(
                state["task_id"],
                state["run_id"],
                "task_created",
                {"status": state["status"]},
                True,
                state_revision=state["state_revision"],
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM task_projection WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["state"] = json.loads(result.pop("state_json"))
        return result

    def list_tasks(self, *, owner: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if owner is None:
                rows = self._conn.execute(
                    "SELECT p.* FROM task_projection p ORDER BY p.updated_at DESC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT p.* FROM task_projection p
                       JOIN task_owners o ON o.task_id=p.task_id
                       WHERE o.subject=? ORDER BY p.updated_at DESC""",
                    (owner,),
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["state"] = json.loads(item.pop("state_json"))
            result.append(item)
        return result

    def bind_task_owner(self, task_id: str, subject: str) -> None:
        """Bind a task to one API subject without allowing ownership changes."""

        if not subject:
            raise ValueError("subject must not be empty")
        with self._immediate_transaction():
            task = self._conn.execute(
                "SELECT 1 FROM task_projection WHERE task_id=?", (task_id,)
            ).fetchone()
            if task is None:
                raise KeyError(task_id)
            existing = self._conn.execute(
                "SELECT subject FROM task_owners WHERE task_id=?", (task_id,)
            ).fetchone()
            if existing is not None and existing["subject"] != subject:
                raise StateConflictError("task already belongs to another subject")
            self._conn.execute(
                "INSERT OR IGNORE INTO task_owners(task_id, subject, created_at) VALUES (?, ?, ?)",
                (task_id, subject, self.clock.now().isoformat()),
            )

    def task_owner(self, task_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT subject FROM task_owners WHERE task_id=?", (task_id,)
            ).fetchone()
        return str(row["subject"]) if row else None

    def _next_sequence_tx(self, task_id: str, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(sequence_number), 0) + 1 AS value FROM execution_events WHERE task_id=? AND run_id=?",
            (task_id, run_id),
        ).fetchone()
        return int(row["value"])

    @staticmethod
    def _artifact_refs(value: Any) -> list[dict[str, Any] | str]:
        refs: list[dict[str, Any] | str] = []

        def collect(item: Any) -> None:
            if isinstance(item, dict):
                if "artifact_id" in item and "sha256" in item:
                    candidate = {
                        key: item[key]
                        for key in ("artifact_id", "kind", "sha256", "size")
                        if key in item
                    }
                    if candidate not in refs:
                        refs.append(candidate)
                    return
                for nested in item.values():
                    collect(nested)
            elif isinstance(item, list):
                for nested in item:
                    collect(nested)

        collect(value)
        return refs

    def _append_event_tx(
        self,
        task_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        confirmed: bool = False,
        *,
        state_revision: int | None = None,
        node_name: str | None = None,
        attempt: int | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        artifact_refs: list[dict[str, Any] | str] | None = None,
    ) -> int:
        sequence = self._next_sequence_tx(task_id, run_id)
        event_id = str(uuid.uuid4())
        now = self.clock.now().isoformat()
        sanitized_payload = self._sanitize(payload)
        if node_name is None and isinstance(sanitized_payload.get("node"), str):
            node_name = sanitized_payload["node"]
        if attempt is None and isinstance(sanitized_payload.get("attempt"), int):
            attempt = sanitized_payload["attempt"]
        if causation_id is None:
            previous = self._conn.execute(
                """SELECT event_id FROM execution_events
                   WHERE task_id=? AND run_id=? ORDER BY sequence_number DESC LIMIT 1""",
                (task_id, run_id),
            ).fetchone()
            causation_id = previous["event_id"] if previous else None
        sanitized_refs = self._sanitize(
            artifact_refs if artifact_refs is not None else self._artifact_refs(sanitized_payload)
        )
        self._conn.execute(
            """INSERT INTO execution_events
               (event_id, schema_version, task_id, run_id, state_revision, node_name, attempt,
                sequence_number, event_type, correlation_id, causation_id,
                payload_json, artifact_refs_json, checkpoint_confirmed, created_at)
               VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                task_id,
                run_id,
                state_revision,
                node_name,
                attempt,
                sequence,
                event_type,
                correlation_id or run_id,
                causation_id,
                json.dumps(sanitized_payload, ensure_ascii=False),
                json.dumps(sanitized_refs, ensure_ascii=False),
                int(confirmed),
                now,
            ),
        )
        self._conn.execute(
            """INSERT INTO event_outbox
               (outbox_id, event_id, task_id, run_id, sequence_number, status,
                attempts, available_at, claimed_by, claimed_at, published_at,
                stream_id, last_error, created_at)
               VALUES (?, ?, ?, ?, ?, 'PENDING', 0, ?, NULL, NULL, NULL, NULL, NULL, ?)""",
            (str(uuid.uuid4()), event_id, task_id, run_id, sequence, now, now),
        )
        return sequence

    def _update_projection_tx(
        self, state: GraphState, revision: int, updated_at: str
    ) -> None:
        """Update the task projection inside an existing store transaction."""

        self._conn.execute(
            """UPDATE task_projection
               SET run_id=?, status=?, state_revision=?, state_json=?, updated_at=?
               WHERE task_id=?""",
            (
                state["run_id"],
                state["status"],
                revision,
                json.dumps(state, ensure_ascii=False),
                updated_at,
                state["task_id"],
            ),
        )

    def transition(
        self,
        state: GraphState,
        *,
        expected_revision: int,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> GraphState:
        updated = validate_state(state)
        new_revision = expected_revision + 1
        updated["state_revision"] = new_revision
        with self._immediate_transaction():
            row = self._conn.execute(
                "SELECT state_revision FROM task_projection WHERE task_id=?", (updated["task_id"],)
            ).fetchone()
            if row is None or int(row["state_revision"]) != expected_revision:
                actual = None if row is None else int(row["state_revision"])
                raise StateConflictError(f"expected state_revision {expected_revision}, actual {actual}")
            self._append_event_tx(
                updated["task_id"],
                updated["run_id"],
                event_type,
                payload or {},
                state_revision=new_revision,
            )
            self._conn.execute(
                "UPDATE task_projection SET run_id=?, status=?, state_revision=?, state_json=?, updated_at=? WHERE task_id=?",
                (
                    updated["run_id"], updated["status"], new_revision,
                    json.dumps(updated, ensure_ascii=False), self.clock.now().isoformat(), updated["task_id"],
                ),
            )
        return validate_state(updated)

    def confirm_checkpoint(self, task_id: str, run_id: str, revision: int) -> None:
        with self._immediate_transaction():
            cursor = self._conn.execute(
                "UPDATE task_projection SET checkpoint_revision=?, checkpoint_run_id=? WHERE task_id=? AND state_revision=?",
                (revision, run_id, task_id, revision),
            )
            if cursor.rowcount != 1:
                raise StateConflictError(
                    "checkpoint revision does not match the control projection"
                )
            self._conn.execute(
                """UPDATE execution_events SET checkpoint_confirmed=1
                   WHERE task_id=? AND run_id=?
                     AND state_revision IS NOT NULL AND state_revision<=?""",
                (task_id, run_id, revision),
            )

    def reconcile(
        self,
        checkpoint_state: GraphState,
        *,
        defer_if_newer_seconds: float = 0,
    ) -> bool:
        """Repair an event/projection-ahead crash using the durable checkpoint."""
        checkpoint_state = validate_state(checkpoint_state)
        if defer_if_newer_seconds < 0:
            raise ValueError("defer_if_newer_seconds must be non-negative")
        with self._immediate_transaction():
            row = self._conn.execute(
                "SELECT * FROM task_projection WHERE task_id=?",
                (checkpoint_state["task_id"],),
            ).fetchone()
            if row is None:
                now = self.clock.now().isoformat()
                self._conn.execute(
                    """INSERT INTO task_projection
                       (task_id, run_id, status, state_revision,
                        checkpoint_revision, checkpoint_run_id, state_json,
                        updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        checkpoint_state["task_id"],
                        checkpoint_state["run_id"],
                        checkpoint_state["status"],
                        checkpoint_state["state_revision"],
                        checkpoint_state["state_revision"],
                        checkpoint_state["run_id"],
                        json.dumps(checkpoint_state, ensure_ascii=False),
                        now,
                    ),
                )
                self._append_event_tx(
                    checkpoint_state["task_id"],
                    checkpoint_state["run_id"],
                    "task_created",
                    {"status": checkpoint_state["status"]},
                    True,
                    state_revision=checkpoint_state["state_revision"],
                )
                return True
            current = dict(row)
            current["state"] = json.loads(current.pop("state_json"))
            if (
                current["state_revision"] == checkpoint_state["state_revision"]
                and current["checkpoint_revision"]
                == checkpoint_state["state_revision"]
                and current["checkpoint_run_id"] == checkpoint_state["run_id"]
            ):
                return False
            if (
                defer_if_newer_seconds
                and int(current["state_revision"])
                > checkpoint_state["state_revision"]
            ):
                updated_at = datetime.fromisoformat(str(current["updated_at"]))
                age = (self.clock.now() - updated_at).total_seconds()
                if age < defer_if_newer_seconds:
                    raise StateConflictError(
                        "task transition is awaiting checkpoint confirmation"
                    )
            checkpoint_caught_up = (
                current["state_revision"] == checkpoint_state["state_revision"]
                and current["run_id"] == checkpoint_state["run_id"]
            )
            if checkpoint_caught_up:
                self._conn.execute(
                    """UPDATE execution_events SET checkpoint_confirmed=1
                       WHERE task_id=? AND run_id=?
                         AND state_revision IS NOT NULL AND state_revision<=?""",
                    (
                        checkpoint_state["task_id"],
                        checkpoint_state["run_id"],
                        checkpoint_state["state_revision"],
                    ),
                )
            else:
                self._conn.execute(
                    """UPDATE event_outbox
                       SET status='DISCARDED', claimed_by=NULL, claimed_at=NULL,
                           last_error='checkpoint reconciliation invalidated event'
                       WHERE task_id=? AND status IN ('PENDING', 'PROCESSING')
                         AND event_id IN (
                           SELECT event_id FROM execution_events
                           WHERE task_id=? AND checkpoint_confirmed=0
                         )""",
                    (checkpoint_state["task_id"], checkpoint_state["task_id"]),
                )
            self._append_event_tx(
                checkpoint_state["task_id"], checkpoint_state["run_id"], "projection_reconciled",
                {"from_revision": current["state_revision"], "to_revision": checkpoint_state["state_revision"]}, True,
                state_revision=checkpoint_state["state_revision"],
            )
            self._conn.execute(
                "UPDATE task_projection SET run_id=?, status=?, state_revision=?, checkpoint_revision=?, checkpoint_run_id=?, state_json=?, updated_at=? WHERE task_id=?",
                (
                    checkpoint_state["run_id"], checkpoint_state["status"], checkpoint_state["state_revision"],
                    checkpoint_state["state_revision"], checkpoint_state["run_id"], json.dumps(checkpoint_state, ensure_ascii=False),
                    self.clock.now().isoformat(), checkpoint_state["task_id"],
                ),
            )
        return True

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> ExecutionEvent:
        item = dict(row)
        return ExecutionEvent(
            event_id=item["event_id"],
            schema_version=int(item["schema_version"]),
            task_id=item["task_id"],
            run_id=item["run_id"],
            state_revision=item.get("state_revision"),
            node_name=item.get("node_name"),
            attempt=item.get("attempt"),
            event_type=item["event_type"],
            sequence_number=int(item["sequence_number"]),
            correlation_id=item.get("correlation_id"),
            causation_id=item.get("causation_id"),
            payload=json.loads(item["payload_json"]),
            artifact_refs=json.loads(item.get("artifact_refs_json") or "[]"),
            checkpoint_confirmed=bool(item["checkpoint_confirmed"]),
            created_at=item["created_at"],
        )

    def append_event(
        self,
        task_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        state_revision: int | None = None,
        node_name: str | None = None,
        attempt: int | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        artifact_refs: list[dict[str, Any] | str] | None = None,
    ) -> ExecutionEvent:
        """Append a non-state event (for example a message) and enqueue it atomically."""

        with self._immediate_transaction():
            task = self._conn.execute(
                "SELECT run_id FROM task_projection WHERE task_id=?", (task_id,)
            ).fetchone()
            if task is None:
                raise KeyError(task_id)
            if str(task["run_id"]) != run_id:
                raise StateConflictError("task run changed before event append")
            sequence = self._append_event_tx(
                task_id,
                run_id,
                event_type,
                payload or {},
                True,
                state_revision=state_revision,
                node_name=node_name,
                attempt=attempt,
                correlation_id=correlation_id,
                causation_id=causation_id,
                artifact_refs=artifact_refs,
            )
        records = self.event_records(
            task_id, run_id, after_sequence=sequence - 1, limit=1
        )
        if not records:
            raise RuntimeError("persisted event could not be read")
        return records[0]

    def event_records(
        self,
        task_id: str,
        run_id: str | None = None,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> list[ExecutionEvent]:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        clauses = ["task_id=?", "sequence_number>?"]
        parameters: list[Any] = [task_id, after_sequence]
        if run_id is not None:
            clauses.append("run_id=?")
            parameters.append(run_id)
        sql = f"SELECT * FROM execution_events WHERE {' AND '.join(clauses)}"
        sql += " ORDER BY sequence_number" if run_id is not None else " ORDER BY created_at, rowid"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, parameters).fetchall()
        return [self._event_from_row(row) for row in rows]

    def events(
        self,
        task_id: str,
        run_id: str | None = None,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return [
            event.to_state_dict()
            for event in self.event_records(
                task_id,
                run_id,
                after_sequence=after_sequence,
                limit=limit,
            )
        ]

    def event(self, event_id: str) -> ExecutionEvent | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM execution_events WHERE event_id=?", (event_id,)
            ).fetchone()
        return self._event_from_row(row) if row else None

    def trace(self, task_id: str, run_id: str | None = None) -> dict[str, Any]:
        records = self.event_records(task_id, run_id)
        sequences = [event.sequence_number for event in records]
        gaps: list[int] = []
        if run_id is not None and sequences:
            present = set(sequences)
            gaps = [
                sequence
                for sequence in range(sequences[0], sequences[-1] + 1)
                if sequence not in present
            ]
        view = TraceView(
            task_id=task_id,
            run_id=run_id,
            event_count=len(records),
            first_sequence=sequences[0] if sequences and run_id is not None else None,
            last_sequence=sequences[-1] if sequences and run_id is not None else None,
            sequence_gaps=gaps,
            events=records,
        )
        return view.to_state_dict()
