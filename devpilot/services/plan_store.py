from __future__ import annotations

import json
from typing import Any

from devpilot.domain.models import ChangeRequest, PlanDocument, PlanLifecycle, ReplanRequest
from devpilot.domain.state import GraphState, validate_state
from devpilot.errors import StateConflictError


class PlanStoreMixin:
    """Persist immutable plans, replans, and user change requests atomically."""

    def prepare_replan(
        self,
        state: GraphState,
        *,
        expected_revision: int,
        request: ReplanRequest,
        payload: dict[str, Any] | None = None,
    ) -> GraphState:
        updated = validate_state(state)
        if updated["pending_replan_request"] != request.to_state_dict():
            raise ValueError("pending_replan_request does not match persisted request")
        if request.task_id != updated["task_id"] or request.run_id != updated["run_id"]:
            raise ValueError("ReplanRequest does not belong to this task run")
        new_revision = expected_revision + 1
        updated["state_revision"] = new_revision
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT state_revision FROM task_projection WHERE task_id=?",
                (updated["task_id"],),
            ).fetchone()
            if row is None or int(row["state_revision"]) != expected_revision:
                actual = None if row is None else int(row["state_revision"])
                raise StateConflictError(
                    f"expected state_revision {expected_revision}, actual {actual}"
                )
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
            self._update_projection_tx(updated, new_revision, self.clock.now().isoformat())
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
        updated = validate_state(state)
        if updated["pending_replan_request"] != replan_request.to_state_dict():
            raise ValueError("pending_replan_request does not match persisted request")
        if (
            change_request.task_id != updated["task_id"]
            or change_request.run_id != updated["run_id"]
        ):
            raise ValueError("ChangeRequest does not belong to this task run")
        if replan_request.source_change_request_id != change_request.change_request_id:
            raise ValueError("ReplanRequest does not reference the ChangeRequest")
        new_revision = expected_revision + 1
        updated["state_revision"] = new_revision
        accepted_at = self.clock.now().isoformat()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT state_revision FROM task_projection WHERE task_id=?",
                (updated["task_id"],),
            ).fetchone()
            if row is None or int(row["state_revision"]) != expected_revision:
                actual = None if row is None else int(row["state_revision"])
                raise StateConflictError(
                    f"expected state_revision {expected_revision}, actual {actual}"
                )
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
                updated["task_id"],
                updated["run_id"],
                "change_request_accepted",
                event_payload,
                state_revision=new_revision,
            )
            if invalidated_approval_id is not None:
                self._append_event_tx(
                    updated["task_id"],
                    updated["run_id"],
                    "approval_invalidated",
                    {
                        "approval_id": invalidated_approval_id,
                        "change_request_id": change_request.change_request_id,
                    },
                    state_revision=new_revision,
                )
            if invalidated_patch_id is not None:
                self._append_event_tx(
                    updated["task_id"],
                    updated["run_id"],
                    "patch_invalidated",
                    {
                        "patch_id": invalidated_patch_id,
                        "change_request_id": change_request.change_request_id,
                    },
                    state_revision=new_revision,
                )
            self._update_projection_tx(updated, new_revision, accepted_at)
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
                "SELECT state_revision FROM task_projection WHERE task_id=?",
                (updated["task_id"],),
            ).fetchone()
            if row is None or int(row["state_revision"]) != expected_revision:
                actual = None if row is None else int(row["state_revision"])
                raise StateConflictError(
                    f"expected state_revision {expected_revision}, actual {actual}"
                )
            previous = self._conn.execute(
                "SELECT plan_id, version FROM plan_lifecycles "
                "WHERE task_id=? AND status='ACTIVE'",
                (updated["task_id"],),
            ).fetchone()
            if document.version == 1 and previous is not None:
                raise ValueError("initial Plan cannot replace an active Plan")
            if document.version > 1:
                if previous is None:
                    raise ValueError("replan requires an active parent Plan")
                if (
                    previous["plan_id"] != document.plan_id
                    or int(previous["version"]) != document.parent_version
                ):
                    raise ValueError("Plan parent does not match the active version")
                request_row = self._conn.execute(
                    "SELECT status, request_json FROM replan_requests "
                    "WHERE replan_request_id=? AND task_id=?",
                    (replan_request_id, updated["task_id"]),
                ).fetchone()
                if request_row is None or request_row["status"] != "PENDING":
                    raise ValueError("replan request is missing or already consumed")
                persisted_request = ReplanRequest.from_state_dict(
                    json.loads(request_row["request_json"])
                )
                if (
                    persisted_request.run_id != updated["run_id"]
                    or persisted_request.requested_from_plan_id != document.plan_id
                    or persisted_request.requested_from_plan_version
                    != document.parent_version
                ):
                    raise ValueError(
                        "replan request does not target the active parent Plan"
                    )
            elif replan_request_id is not None:
                raise ValueError("initial Plan cannot consume a replan request")
            self._conn.execute(
                """INSERT INTO plan_documents
                   (task_id, run_id, plan_id, version, parent_version, content_hash, document_json, artifact_ref_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    updated["task_id"],
                    updated["run_id"],
                    document.plan_id,
                    document.version,
                    document.parent_version,
                    document.content_hash,
                    json.dumps(document.to_state_dict(), ensure_ascii=False),
                    json.dumps(artifact_ref, ensure_ascii=False),
                    document.created_at,
                ),
            )
            self._conn.execute(
                "UPDATE plan_lifecycles SET status='SUPERSEDED', superseded_at=? "
                "WHERE task_id=? AND status='ACTIVE'",
                (activated_at, updated["task_id"]),
            )
            self._conn.execute(
                """INSERT INTO plan_lifecycles
                   (task_id, run_id, plan_id, version, status, activated_at, superseded_at)
                   VALUES (?, ?, ?, ?, 'ACTIVE', ?, NULL)""",
                (
                    updated["task_id"],
                    updated["run_id"],
                    document.plan_id,
                    document.version,
                    activated_at,
                ),
            )
            if replan_request_id is not None:
                self._conn.execute(
                    "UPDATE replan_requests SET status='CONSUMED', consumed_at=? "
                    "WHERE replan_request_id=?",
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
            self._update_projection_tx(updated, new_revision, activated_at)
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
            "SELECT request_json, status, consumed_at FROM replan_requests "
            "WHERE task_id=? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [
            {
                **json.loads(row["request_json"]),
                "status": row["status"],
                "consumed_at": row["consumed_at"],
            }
            for row in rows
        ]

    def change_requests(self, task_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT request_json, status, accepted_at FROM change_requests "
            "WHERE task_id=? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [
            {
                **json.loads(row["request_json"]),
                "status": row["status"],
                "accepted_at": row["accepted_at"],
            }
            for row in rows
        ]
