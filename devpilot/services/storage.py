from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from devpilot.clock import Clock, SystemClock
from devpilot.domain.models import ArtifactRef, ChangeRequest, PlanDocument, PlanLifecycle, ReplanRequest
from devpilot.domain.state import GraphState, validate_state
from devpilot.errors import StateConflictError
from devpilot.events.models import ExecutionEvent, OutboxEntry, TraceView
from devpilot.events.redaction import sanitize_event_value


def default_data_dir() -> Path:
    configured = os.environ.get("DEVPILOT_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".devpilot").resolve()


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, task_id: str, run_id: str, kind: str, content: bytes) -> ArtifactRef:
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = f"art_{digest[:20]}"
        directory = self.root / "tasks" / task_id / "runs" / run_id / "artifacts"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / digest
        if not target.exists():
            temporary = directory / f".{digest}.{uuid.uuid4().hex}.tmp"
            temporary.write_bytes(content)
            os.replace(temporary, target)
        return ArtifactRef(artifact_id=artifact_id, kind=kind, sha256=digest, size=len(content))

    def put_text(self, task_id: str, run_id: str, kind: str, content: str) -> ArtifactRef:
        return self.put_bytes(task_id, run_id, kind, content.encode("utf-8"))

    def put_json(self, task_id: str, run_id: str, kind: str, value: Any) -> ArtifactRef:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return self.put_text(task_id, run_id, kind, raw)

    def read_bytes(self, task_id: str, run_id: str, ref: dict[str, Any]) -> bytes:
        target = (self.root / "tasks" / task_id / "runs" / run_id / "artifacts" / ref["sha256"]).resolve()
        if self.root not in target.parents:
            raise ValueError("artifact path escaped store root")
        content = target.read_bytes()
        if hashlib.sha256(content).hexdigest() != ref["sha256"]:
            raise ValueError("artifact hash mismatch")
        return content

    def read_text(self, task_id: str, run_id: str, ref: dict[str, Any]) -> str:
        return self.read_bytes(task_id, run_id, ref).decode("utf-8")


class SQLiteControlStore:
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
        self._conn.close()

    def _setup(self) -> None:
        self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;
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
        self._conn.commit()

    @staticmethod
    def _sanitize(value: Any) -> Any:
        return sanitize_event_value(value)

    def create_task(self, state: GraphState) -> None:
        state = validate_state(state)
        now = self.clock.now().isoformat()
        with self._lock, self._conn:
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
        row = self._conn.execute("SELECT * FROM task_projection WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["state"] = json.loads(result.pop("state_json"))
        return result

    def list_tasks(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM task_projection ORDER BY updated_at DESC").fetchall()
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
        with self._lock, self._conn:
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
        with self._lock, self._conn:
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

    def prepare_replan(
        self,
        state: GraphState,
        *,
        expected_revision: int,
        request: ReplanRequest,
        payload: dict[str, Any] | None = None,
    ) -> GraphState:
        """Persist an immutable ReplanRequest and its state transition atomically."""
        updated = validate_state(state)
        if updated["pending_replan_request"] != request.to_state_dict():
            raise ValueError("pending_replan_request does not match persisted request")
        if request.task_id != updated["task_id"] or request.run_id != updated["run_id"]:
            raise ValueError("ReplanRequest does not belong to this task run")
        new_revision = expected_revision + 1
        updated["state_revision"] = new_revision
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT state_revision FROM task_projection WHERE task_id=?", (updated["task_id"],)
            ).fetchone()
            if row is None or int(row["state_revision"]) != expected_revision:
                actual = None if row is None else int(row["state_revision"])
                raise StateConflictError(f"expected state_revision {expected_revision}, actual {actual}")
            self._conn.execute(
                """INSERT INTO replan_requests
                   (replan_request_id, task_id, run_id, request_json, status, created_at, consumed_at)
                   VALUES (?, ?, ?, ?, 'PENDING', ?, NULL)""",
                (
                    request.replan_request_id,
                    request.task_id,
                    request.run_id,
                    json.dumps(request.to_state_dict(), ensure_ascii=False),
                    request.requested_at,
                ),
            )
            self._append_event_tx(
                updated["task_id"],
                updated["run_id"],
                "replan_prepared",
                {"replan_request_id": request.replan_request_id, **(payload or {})},
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

    def prepare_change_request(
        self,
        state: GraphState,
        *,
        expected_revision: int,
        change_request: ChangeRequest,
        replan_request: ReplanRequest,
        invalidated_approval_id: str | None,
        invalidated_patch_id: str | None,
    ) -> GraphState:
        """Accept a ChangeRequest and prepare its ReplanRequest atomically."""

        updated = validate_state(state)
        if updated["pending_replan_request"] != replan_request.to_state_dict():
            raise ValueError("pending_replan_request does not match persisted request")
        if change_request.task_id != updated["task_id"] or change_request.run_id != updated["run_id"]:
            raise ValueError("ChangeRequest does not belong to this task run")
        if replan_request.source_change_request_id != change_request.change_request_id:
            raise ValueError("ReplanRequest does not reference the ChangeRequest")
        new_revision = expected_revision + 1
        updated["state_revision"] = new_revision
        accepted_at = self.clock.now().isoformat()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT state_revision FROM task_projection WHERE task_id=?", (updated["task_id"],)
            ).fetchone()
            if row is None or int(row["state_revision"]) != expected_revision:
                actual = None if row is None else int(row["state_revision"])
                raise StateConflictError(f"expected state_revision {expected_revision}, actual {actual}")
            self._conn.execute(
                """INSERT INTO change_requests
                   (change_request_id, task_id, run_id, request_json, status, created_at, accepted_at)
                   VALUES (?, ?, ?, ?, 'ACCEPTED', ?, ?)""",
                (
                    change_request.change_request_id,
                    change_request.task_id,
                    change_request.run_id,
                    json.dumps(change_request.to_state_dict(), ensure_ascii=False),
                    change_request.requested_at,
                    accepted_at,
                ),
            )
            self._conn.execute(
                """INSERT INTO replan_requests
                   (replan_request_id, task_id, run_id, request_json, status, created_at, consumed_at)
                   VALUES (?, ?, ?, ?, 'PENDING', ?, NULL)""",
                (
                    replan_request.replan_request_id,
                    replan_request.task_id,
                    replan_request.run_id,
                    json.dumps(replan_request.to_state_dict(), ensure_ascii=False),
                    replan_request.requested_at,
                ),
            )
            event_payload = {
                "change_request_id": change_request.change_request_id,
                "replan_request_id": replan_request.replan_request_id,
                "requested_by": change_request.requested_by,
            }
            self._append_event_tx(
                updated["task_id"], updated["run_id"], "change_request_accepted",
                event_payload, state_revision=new_revision,
            )
            if invalidated_approval_id is not None:
                self._append_event_tx(
                    updated["task_id"], updated["run_id"], "approval_invalidated",
                    {"approval_id": invalidated_approval_id, "change_request_id": change_request.change_request_id},
                    state_revision=new_revision,
                )
            if invalidated_patch_id is not None:
                self._append_event_tx(
                    updated["task_id"], updated["run_id"], "patch_invalidated",
                    {"patch_id": invalidated_patch_id, "change_request_id": change_request.change_request_id},
                    state_revision=new_revision,
                )
            self._conn.execute(
                "UPDATE task_projection SET run_id=?, status=?, state_revision=?, state_json=?, updated_at=? WHERE task_id=?",
                (
                    updated["run_id"], updated["status"], new_revision,
                    json.dumps(updated, ensure_ascii=False), accepted_at, updated["task_id"],
                ),
            )
        return validate_state(updated)

    def activate_plan(
        self,
        state: GraphState,
        *,
        expected_revision: int,
        document: PlanDocument,
        artifact_ref: dict[str, Any],
        replan_request_id: str | None,
        payload: dict[str, Any] | None = None,
    ) -> GraphState:
        """Atomically switch the active Plan version, projection, request, and audit event."""
        updated = validate_state(state)
        active_ref = updated["active_plan_ref"] or {}
        if (
            active_ref.get("plan_id") != document.plan_id
            or active_ref.get("version") != document.version
            or active_ref.get("content_hash") != document.content_hash
            or active_ref.get("sha256") != artifact_ref.get("sha256")
        ):
            raise ValueError("active_plan_ref does not match PlanDocument artifact")
        new_revision = expected_revision + 1
        updated["state_revision"] = new_revision
        activated_at = self.clock.now().isoformat()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT state_revision FROM task_projection WHERE task_id=?", (updated["task_id"],)
            ).fetchone()
            if row is None or int(row["state_revision"]) != expected_revision:
                actual = None if row is None else int(row["state_revision"])
                raise StateConflictError(f"expected state_revision {expected_revision}, actual {actual}")
            previous = self._conn.execute(
                "SELECT plan_id, version FROM plan_lifecycles WHERE task_id=? AND status='ACTIVE'",
                (updated["task_id"],),
            ).fetchone()
            if document.version == 1 and previous is not None:
                raise ValueError("initial Plan cannot replace an active Plan")
            if document.version > 1:
                if previous is None:
                    raise ValueError("replan requires an active parent Plan")
                if previous["plan_id"] != document.plan_id or int(previous["version"]) != document.parent_version:
                    raise ValueError("Plan parent does not match the active version")
                request_row = self._conn.execute(
                    "SELECT status, request_json FROM replan_requests WHERE replan_request_id=? AND task_id=?",
                    (replan_request_id, updated["task_id"]),
                ).fetchone()
                if request_row is None or request_row["status"] != "PENDING":
                    raise ValueError("replan request is missing or already consumed")
                persisted_request = ReplanRequest.from_state_dict(json.loads(request_row["request_json"]))
                if (
                    persisted_request.run_id != updated["run_id"]
                    or persisted_request.requested_from_plan_id != document.plan_id
                    or persisted_request.requested_from_plan_version != document.parent_version
                ):
                    raise ValueError("replan request does not target the active parent Plan")
            elif replan_request_id is not None:
                raise ValueError("initial Plan cannot consume a replan request")
            self._conn.execute(
                """INSERT INTO plan_documents
                   (task_id, run_id, plan_id, version, parent_version, content_hash, document_json, artifact_ref_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    updated["task_id"], updated["run_id"], document.plan_id, document.version,
                    document.parent_version, document.content_hash,
                    json.dumps(document.to_state_dict(), ensure_ascii=False),
                    json.dumps(artifact_ref, ensure_ascii=False), document.created_at,
                ),
            )
            self._conn.execute(
                "UPDATE plan_lifecycles SET status='SUPERSEDED', superseded_at=? WHERE task_id=? AND status='ACTIVE'",
                (activated_at, updated["task_id"]),
            )
            self._conn.execute(
                """INSERT INTO plan_lifecycles
                   (task_id, run_id, plan_id, version, status, activated_at, superseded_at)
                   VALUES (?, ?, ?, ?, 'ACTIVE', ?, NULL)""",
                (updated["task_id"], updated["run_id"], document.plan_id, document.version, activated_at),
            )
            if replan_request_id is not None:
                self._conn.execute(
                    "UPDATE replan_requests SET status='CONSUMED', consumed_at=? WHERE replan_request_id=?",
                    (activated_at, replan_request_id),
                )
            self._append_event_tx(
                updated["task_id"],
                updated["run_id"],
                "plan_activated",
                {
                    "plan_id": document.plan_id,
                    "version": document.version,
                    "parent_version": document.parent_version,
                    "replan_request_id": replan_request_id,
                    **(payload or {}),
                },
                state_revision=new_revision,
                artifact_refs=[artifact_ref],
            )
            self._conn.execute(
                "UPDATE task_projection SET run_id=?, status=?, state_revision=?, state_json=?, updated_at=? WHERE task_id=?",
                (
                    updated["run_id"], updated["status"], new_revision,
                    json.dumps(updated, ensure_ascii=False), activated_at, updated["task_id"],
                ),
            )
        return validate_state(updated)

    def plans(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT d.document_json, d.artifact_ref_json, l.plan_id, l.version,
                      l.status, l.activated_at, l.superseded_at
               FROM plan_documents d
               JOIN plan_lifecycles l
                 ON l.task_id=d.task_id AND l.plan_id=d.plan_id AND l.version=d.version
               WHERE d.task_id=? ORDER BY d.version""",
            (task_id,),
        ).fetchall()
        return [
            {
                "document": json.loads(row["document_json"]),
                "artifact_ref": json.loads(row["artifact_ref_json"]),
                "lifecycle": PlanLifecycle(
                    plan_id=row["plan_id"],
                    version=int(row["version"]),
                    status=row["status"],
                    activated_at=row["activated_at"],
                    superseded_at=row["superseded_at"],
                ).to_state_dict(),
            }
            for row in rows
        ]

    def replan_requests(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT request_json, status, consumed_at FROM replan_requests WHERE task_id=? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [
            {**json.loads(row["request_json"]), "status": row["status"], "consumed_at": row["consumed_at"]}
            for row in rows
        ]

    def change_requests(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT request_json, status, accepted_at FROM change_requests WHERE task_id=? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [
            {**json.loads(row["request_json"]), "status": row["status"], "accepted_at": row["accepted_at"]}
            for row in rows
        ]

    def confirm_checkpoint(self, task_id: str, run_id: str, revision: int) -> None:
        with self._lock, self._conn:
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

    def reconcile(self, checkpoint_state: GraphState) -> bool:
        """Repair an event/projection-ahead crash using the durable checkpoint."""
        checkpoint_state = validate_state(checkpoint_state)
        current = self.get_task(checkpoint_state["task_id"])
        if current is None:
            self.create_task(checkpoint_state)
            return True
        if (
            current["state_revision"] == checkpoint_state["state_revision"]
            and current["checkpoint_revision"] == checkpoint_state["state_revision"]
            and current["checkpoint_run_id"] == checkpoint_state["run_id"]
        ):
            return False
        with self._lock, self._conn:
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

        with self._lock, self._conn:
            task = self._conn.execute(
                "SELECT run_id FROM task_projection WHERE task_id=?", (task_id,)
            ).fetchone()
            if task is None:
                raise KeyError(task_id)
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

    @staticmethod
    def _outbox_from_row(row: sqlite3.Row) -> OutboxEntry:
        return OutboxEntry.from_state_dict(dict(row))

    def claim_outbox(
        self,
        relay_id: str,
        *,
        limit: int = 100,
        lease_seconds: int = 30,
    ) -> list[tuple[OutboxEntry, ExecutionEvent]]:
        if not relay_id:
            raise ValueError("relay_id is required")
        if limit < 1 or lease_seconds < 1:
            raise ValueError("limit and lease_seconds must be positive")
        now_value = self.clock.now()
        now = now_value.isoformat()
        stale_at = (now_value - timedelta(seconds=lease_seconds)).isoformat()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    """SELECT o.* FROM event_outbox o
                       JOIN execution_events e ON e.event_id=o.event_id
                       WHERE e.checkpoint_confirmed=1 AND (
                            (o.status='PENDING' AND o.available_at<=?)
                         OR (o.status='PROCESSING' AND o.claimed_at<=?)
                       )
                         AND NOT EXISTS (
                           SELECT 1 FROM event_outbox earlier
                           WHERE earlier.task_id=o.task_id
                             AND earlier.run_id=o.run_id
                             AND earlier.sequence_number<o.sequence_number
                             AND earlier.status NOT IN ('PUBLISHED', 'DISCARDED')
                         )
                       ORDER BY o.created_at, o.rowid LIMIT ?""",
                    (now, stale_at, limit),
                ).fetchall()
                claimed: list[OutboxEntry] = []
                for row in rows:
                    self._conn.execute(
                        """UPDATE event_outbox
                           SET status='PROCESSING', attempts=attempts+1,
                               claimed_by=?, claimed_at=?
                           WHERE outbox_id=?""",
                        (relay_id, now, row["outbox_id"]),
                    )
                    refreshed = self._conn.execute(
                        "SELECT * FROM event_outbox WHERE outbox_id=?",
                        (row["outbox_id"],),
                    ).fetchone()
                    claimed.append(self._outbox_from_row(refreshed))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        result: list[tuple[OutboxEntry, ExecutionEvent]] = []
        for entry in claimed:
            event = self.event(entry.event_id)
            if event is None:
                raise RuntimeError(f"outbox event is missing: {entry.event_id}")
            result.append((entry, event))
        return result

    def publish_outbox(self, outbox_id: str, *, relay_id: str, stream_id: str) -> None:
        now = self.clock.now().isoformat()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """UPDATE event_outbox
                   SET status='PUBLISHED', published_at=?, stream_id=?,
                       claimed_by=NULL, claimed_at=NULL, last_error=NULL
                   WHERE outbox_id=? AND status='PROCESSING' AND claimed_by=?""",
                (now, stream_id, outbox_id, relay_id),
            )
            if cursor.rowcount != 1:
                raise StateConflictError("outbox claim is no longer owned by this relay")

    def retry_outbox(
        self,
        outbox_id: str,
        *,
        relay_id: str,
        error: str,
        delay_seconds: int,
    ) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be non-negative")
        available_at = (
            self.clock.now() + timedelta(seconds=delay_seconds)
        ).isoformat()
        safe_error = str(self._sanitize(error))[:2_000]
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """UPDATE event_outbox
                   SET status='PENDING', available_at=?, claimed_by=NULL,
                       claimed_at=NULL, last_error=?
                   WHERE outbox_id=? AND status='PROCESSING' AND claimed_by=?""",
                (available_at, safe_error, outbox_id, relay_id),
            )
            if cursor.rowcount != 1:
                raise StateConflictError("outbox claim is no longer owned by this relay")

    def outbox_entries(self, status: str | None = None) -> list[OutboxEntry]:
        if status is None:
            rows = self._conn.execute(
                "SELECT * FROM event_outbox ORDER BY created_at, rowid"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM event_outbox WHERE status=? ORDER BY created_at, rowid",
                (status,),
            ).fetchall()
        return [self._outbox_from_row(row) for row in rows]

    def idempotent_result(self, task_id: str, operation: str, key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT result_json FROM idempotency_keys WHERE task_id=? AND operation=? AND idempotency_key=?",
            (task_id, operation, key),
        ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def bind_idempotency_input(
        self,
        task_id: str,
        operation: str,
        key: str,
        request_hash: str,
    ) -> None:
        """Reject reuse of one idempotency key for a different command payload."""

        with self._lock, self._conn:
            row = self._conn.execute(
                """SELECT request_hash FROM idempotency_inputs
                   WHERE task_id=? AND operation=? AND idempotency_key=?""",
                (task_id, operation, key),
            ).fetchone()
            if row is not None and row["request_hash"] != request_hash:
                raise StateConflictError(
                    "Idempotency-Key was already used with a different request payload"
                )
            self._conn.execute(
                """INSERT OR IGNORE INTO idempotency_inputs
                   (task_id, operation, idempotency_key, request_hash, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (task_id, operation, key, request_hash, self.clock.now().isoformat()),
            )

    def save_idempotent_result(self, task_id: str, operation: str, key: str, result: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO idempotency_keys VALUES (?, ?, ?, ?, ?)",
                (task_id, operation, key, json.dumps(result, ensure_ascii=False), self.clock.now().isoformat()),
            )
