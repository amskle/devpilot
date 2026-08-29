from __future__ import annotations

import json
from typing import Any

from devpilot.errors import StateConflictError


class IdempotencyStoreMixin:
    """Persist command payload bindings and cached idempotent results."""

    def idempotent_result(
        self, task_id: str, operation: str, key: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT result_json FROM idempotency_keys "
                "WHERE task_id=? AND operation=? AND idempotency_key=?",
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
        with self._immediate_transaction():
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
                (
                    task_id,
                    operation,
                    key,
                    request_hash,
                    self.clock.now().isoformat(),
                ),
            )

    def save_idempotent_result(
        self,
        task_id: str,
        operation: str,
        key: str,
        result: dict[str, Any],
    ) -> None:
        with self._immediate_transaction():
            self._conn.execute(
                "INSERT OR IGNORE INTO idempotency_keys VALUES (?, ?, ?, ?, ?)",
                (
                    task_id,
                    operation,
                    key,
                    json.dumps(result, ensure_ascii=False),
                    self.clock.now().isoformat(),
                ),
            )

    def append_idempotent_event_payload(
        self,
        task_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        operation: str,
        key: str,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Append a non-state event and cache its result in one transaction."""

        with self._immediate_transaction():
            cached = self._conn.execute(
                """SELECT result_json FROM idempotency_keys
                   WHERE task_id=? AND operation=? AND idempotency_key=?""",
                (task_id, operation, key),
            ).fetchone()
            if cached is not None:
                return json.loads(cached["result_json"])
            task = self._conn.execute(
                "SELECT run_id FROM task_projection WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if task is None:
                raise KeyError(task_id)
            if str(task["run_id"]) != run_id:
                raise StateConflictError("task run changed before event append")
            safe_payload = self._sanitize(payload)
            self._append_event_tx(
                task_id,
                run_id,
                event_type,
                safe_payload,
                True,
                correlation_id=correlation_id,
            )
            self._conn.execute(
                """INSERT INTO idempotency_keys
                   (task_id, operation, idempotency_key, result_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    task_id,
                    operation,
                    key,
                    json.dumps(safe_payload, ensure_ascii=False),
                    self.clock.now().isoformat(),
                ),
            )
            return safe_payload
