from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from devpilot.clock import Clock, SystemClock
from devpilot.domain.models import ArtifactRef, PlanDocument, PlanLifecycle, ReplanRequest
from devpilot.domain.state import GraphState, validate_state
from devpilot.errors import StateConflictError


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
              sequence_number INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              checkpoint_confirmed INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              UNIQUE(task_id, run_id, sequence_number)
            );
            CREATE TABLE IF NOT EXISTS idempotency_keys (
              task_id TEXT NOT NULL,
              operation TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,
              result_json TEXT NOT NULL,
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
            """
        )
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(task_projection)").fetchall()}
        if "checkpoint_run_id" not in columns:
            self._conn.execute("ALTER TABLE task_projection ADD COLUMN checkpoint_run_id TEXT")
            self._conn.execute("UPDATE task_projection SET checkpoint_run_id=run_id WHERE checkpoint_run_id IS NULL")
        self._conn.commit()

    @staticmethod
    def _sanitize(value: Any) -> Any:
        secret_names = {
            "apikey",
            "authorization",
            "token",
            "accesstoken",
            "refreshtoken",
            "password",
            "secret",
            "clientsecret",
            "env",
        }
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                normalized = "".join(character for character in str(key).lower() if character.isalnum())
                sanitized[str(key)] = (
                    "[REDACTED]"
                    if normalized in secret_names
                    else SQLiteControlStore._sanitize(item)
                )
            return sanitized
        if isinstance(value, list):
            return [SQLiteControlStore._sanitize(item) for item in value]
        if isinstance(value, str) and len(value) > 16_000:
            return value[:16_000] + "...[TRUNCATED]"
        return value

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
            self._append_event_tx(state["task_id"], state["run_id"], "task_created", {"status": state["status"]}, True)

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

    def _next_sequence_tx(self, task_id: str, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(sequence_number), 0) + 1 AS value FROM execution_events WHERE task_id=? AND run_id=?",
            (task_id, run_id),
        ).fetchone()
        return int(row["value"])

    def _append_event_tx(
        self, task_id: str, run_id: str, event_type: str, payload: dict[str, Any], confirmed: bool = False
    ) -> int:
        sequence = self._next_sequence_tx(task_id, run_id)
        self._conn.execute(
            "INSERT INTO execution_events VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), task_id, run_id, sequence, event_type,
                json.dumps(self._sanitize(payload), ensure_ascii=False), int(confirmed), self.clock.now().isoformat(),
            ),
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
            self._append_event_tx(updated["task_id"], updated["run_id"], event_type, payload or {})
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
            )
            self._conn.execute(
                "UPDATE task_projection SET run_id=?, status=?, state_revision=?, state_json=?, updated_at=? WHERE task_id=?",
                (
                    updated["run_id"], updated["status"], new_revision,
                    json.dumps(updated, ensure_ascii=False), self.clock.now().isoformat(), updated["task_id"],
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

    def confirm_checkpoint(self, task_id: str, run_id: str, revision: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE task_projection SET checkpoint_revision=?, checkpoint_run_id=? WHERE task_id=? AND state_revision=?",
                (revision, run_id, task_id, revision),
            )
            self._conn.execute(
                "UPDATE execution_events SET checkpoint_confirmed=1 WHERE task_id=?", (task_id,)
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
            self._append_event_tx(
                checkpoint_state["task_id"], checkpoint_state["run_id"], "projection_reconciled",
                {"from_revision": current["state_revision"], "to_revision": checkpoint_state["state_revision"]}, True,
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

    def events(self, task_id: str, run_id: str | None = None) -> list[dict[str, Any]]:
        if run_id:
            rows = self._conn.execute(
                "SELECT * FROM execution_events WHERE task_id=? AND run_id=? ORDER BY sequence_number",
                (task_id, run_id),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM execution_events WHERE task_id=? ORDER BY created_at, sequence_number", (task_id,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def idempotent_result(self, task_id: str, operation: str, key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT result_json FROM idempotency_keys WHERE task_id=? AND operation=? AND idempotency_key=?",
            (task_id, operation, key),
        ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def save_idempotent_result(self, task_id: str, operation: str, key: str, result: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO idempotency_keys VALUES (?, ?, ?, ?, ?)",
                (task_id, operation, key, json.dumps(result, ensure_ascii=False), self.clock.now().isoformat()),
            )
