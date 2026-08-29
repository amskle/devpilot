from __future__ import annotations

import sqlite3
from datetime import timedelta

from devpilot.errors import StateConflictError
from devpilot.events.models import ExecutionEvent, OutboxEntry


class OutboxStoreMixin:
    """Lease, publish, and retry transactional outbox records."""

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

    def publish_outbox(
        self, outbox_id: str, *, relay_id: str, stream_id: str
    ) -> None:
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
                raise StateConflictError(
                    "outbox claim is no longer owned by this relay"
                )

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
                raise StateConflictError(
                    "outbox claim is no longer owned by this relay"
                )

    def outbox_entries(self, status: str | None = None) -> list[OutboxEntry]:
        with self._lock:
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
