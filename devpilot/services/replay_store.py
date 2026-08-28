from __future__ import annotations

import json
from typing import Any


class ReplayEvaluationStoreMixin:
    """Persistence for immutable replay records and evaluation reports."""

    def save_replay_record(
        self,
        replay_id: str,
        task_id: str,
        run_id: str,
        replay_type: str,
        source_digest: str,
        result: dict[str, Any],
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO replay_records
                   (replay_id, task_id, run_id, replay_type, source_digest,
                    result_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    replay_id,
                    task_id,
                    run_id,
                    replay_type,
                    source_digest,
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    self.clock.now().isoformat(),
                ),
            )

    def replay_record(self, replay_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT result_json FROM replay_records WHERE replay_id=?",
            (replay_id,),
        ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def replay_records(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT result_json FROM replay_records
               WHERE task_id=? ORDER BY created_at, replay_id""",
            (task_id,),
        ).fetchall()
        return [json.loads(row["result_json"]) for row in rows]

    def save_recovery_fork(self, result: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO recovery_forks
                   (fork_id, source_task_id, source_run_id, recovery_point_id,
                    target_task_id, target_run_id, result_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    result["fork_id"],
                    result["source_task_id"],
                    result["source_run_id"],
                    result["recovery_point_id"],
                    result["target_task_id"],
                    result["target_run_id"],
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    result["created_at"],
                ),
            )

    def save_evaluation_report(self, report: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO evaluation_runs
                   (evaluation_id, dataset_name, dataset_version,
                    dataset_digest, model, prompt_version, prompt_digest,
                    report_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    report["evaluation_id"],
                    report["dataset_name"],
                    report["dataset_version"],
                    report["dataset_digest"],
                    report["model"],
                    report["prompt_version"],
                    report["prompt_digest"],
                    json.dumps(report, ensure_ascii=False, sort_keys=True),
                    report["created_at"],
                ),
            )

    def evaluation_report(self, evaluation_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT report_json FROM evaluation_runs WHERE evaluation_id=?",
            (evaluation_id,),
        ).fetchone()
        return json.loads(row["report_json"]) if row else None

    def evaluation_reports(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """SELECT report_json FROM evaluation_runs
               ORDER BY created_at DESC, evaluation_id DESC"""
        ).fetchall()
        return [json.loads(row["report_json"]) for row in rows]
