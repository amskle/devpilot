from __future__ import annotations

import json
from typing import Any

from devpilot.errors import StateConflictError


class IdempotencyStoreMixin:
    """Persist command payload bindings and cached idempotent results."""

    def idempotent_result(
        self, task_id: str, operation: str, key: str
    ) -> dict[str, Any] | None:
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
        with self._conn:
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
