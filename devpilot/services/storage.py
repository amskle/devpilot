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
from devpilot.domain.models import ArtifactRef
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
            """
        )
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(task_projection)").fetchall()}
        if "checkpoint_run_id" not in columns:
            self._conn.execute("ALTER TABLE task_projection ADD COLUMN checkpoint_run_id TEXT")
            self._conn.execute("UPDATE task_projection SET checkpoint_run_id=run_id WHERE checkpoint_run_id IS NULL")
        self._conn.commit()

    @staticmethod
    def _sanitize(value: Any) -> Any:
        secret_names = {"api_key", "authorization", "token", "password", "secret", "env"}
        if isinstance(value, dict):
            return {
                str(k): "[REDACTED]" if str(k).lower() in secret_names else SQLiteControlStore._sanitize(v)
                for k, v in value.items()
            }
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
        return [dict(row) for row in rows]

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
