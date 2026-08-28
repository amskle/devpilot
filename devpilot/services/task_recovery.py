from __future__ import annotations

import copy
import json
import uuid

from devpilot.domain.models import ExecutionBudget, RecoveryPoint, TaskStatus, WorkspaceRef
from devpilot.domain.state import GraphState, validate_state
from devpilot.errors import StateConflictError
from devpilot.orchestration.graph import build_graph


class TaskRecoveryCommands:
    """Implement rollback, run restoration, and checkpoint reconciliation."""

    def rollback(
        self,
        task_id: str,
        recovery_point_id: str,
        expected_revision: int,
        *,
        idempotency_key: str | None = None,
    ) -> GraphState:
        if idempotency_key:
            cached = self.control.idempotent_result(
                task_id, "rollback", idempotency_key
            )
            if cached:
                return validate_state(cached)
        state = self.get_state(task_id)
        if state["state_revision"] != expected_revision:
            raise StateConflictError(
                f"expected state_revision {expected_revision}, actual {state['state_revision']}"
            )
        if not state["active_recovery_point_ref"]:
            raise ValueError("task has no active recovery point")
        raw = self.artifacts.read_text(
            task_id,
            state["run_id"],
            {"sha256": state["active_recovery_point_ref"]},
        )
        recovery = RecoveryPoint.from_state_dict(json.loads(raw))
        if recovery.recovery_point_id != recovery_point_id:
            raise ValueError("recovery point does not match active recovery point")
        budget = ExecutionBudget.from_state_dict(state["execution_budget"])
        if budget.rollbacks_used >= budget.max_rollbacks:
            raise ValueError("rollback budget exhausted")
        workspace_ref = WorkspaceRef.from_state_dict(state["workspace_ref"] or {})
        self.workspace_manager.validate_lease(workspace_ref, state["run_id"])
        workspace = self.workspace_manager.rollback(
            workspace_ref, recovery.repository_snapshot_id
        )
        updated = copy.deepcopy(state)
        updated.update(
            {
                "workspace_ref": workspace.to_state_dict(),
                "patch_proposal": None,
                "verification": None,
                "execution_budget": budget.model_copy(
                    update={"rollbacks_used": budget.rollbacks_used + 1}
                ).to_state_dict(),
                "status": TaskStatus.WAITING_HUMAN_INTERVENTION.value,
                "pause_reason": "ROLLBACK_COMPLETED",
            }
        )
        request = self._request_from_state(state)
        graph = build_graph(
            self._runtime(source_repo=None, request=request, state=state),
            self.checkpointer,
        )
        updated = self.control.transition(
            validate_state(updated),
            expected_revision=expected_revision,
            event_type="rollback_completed",
            payload={"recovery_point_id": recovery_point_id},
        )
        graph.update_state(self._config(state["run_id"]), updated)
        self.control.confirm_checkpoint(
            task_id, state["run_id"], updated["state_revision"]
        )
        if idempotency_key:
            self.control.save_idempotent_result(
                task_id, "rollback", idempotency_key, updated
            )
        return updated

    def restore(
        self,
        task_id: str,
        recovery_point_id: str,
        *,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> GraphState:
        if idempotency_key:
            cached = self.control.idempotent_result(
                task_id, "restore", idempotency_key
            )
            if cached:
                return validate_state(cached)
        old = self.get_state(task_id)
        if expected_revision is not None and old["state_revision"] != expected_revision:
            raise StateConflictError(
                f"expected state_revision {expected_revision}, actual {old['state_revision']}"
            )
        if not old["active_recovery_point_ref"]:
            raise ValueError("task has no active recovery point")
        raw = self.artifacts.read_text(
            task_id,
            old["run_id"],
            {"sha256": old["active_recovery_point_ref"]},
        )
        recovery = RecoveryPoint.from_state_dict(json.loads(raw))
        if recovery.recovery_point_id != recovery_point_id:
            raise ValueError("recovery point does not match active recovery point")
        request = self._request_from_state(old)
        workspace_ref = WorkspaceRef.from_state_dict(old["workspace_ref"] or {})
        self.workspace_manager.validate_lease(workspace_ref, old["run_id"])
        workspace = self.workspace_manager.rollback(
            workspace_ref, recovery.repository_snapshot_id
        )
        new_run_id = f"run_{uuid.uuid4().hex[:16]}"
        restored = copy.deepcopy(old)
        restored.update(
            {
                "run_id": new_run_id,
                "parent_run_id": old["run_id"],
                "workspace_ref": workspace.model_copy(
                    update={"lease_owner": new_run_id}
                ).to_state_dict(),
                "status": TaskStatus.RUNNING.value,
                "pause_reason": None,
                "patch_proposal": None,
                "verification": None,
                "diagnosis": None,
                "review": None,
                "latest_failure": None,
                "progress_window": {"entries": [], "no_progress_rounds": 0},
                "pending_approval": None,
            }
        )
        for field, kind in (
            ("context_delta_ref", "task_request"),
            ("baseline_context_ref", "baseline_context"),
            ("active_plan_ref", "plan"),
        ):
            ref = old[field]
            if ref:
                content = self.artifacts.read_bytes(task_id, old["run_id"], ref)
                copied_ref = self.artifacts.put_bytes(
                    task_id, new_run_id, kind, content
                ).to_state_dict()
                if field == "active_plan_ref":
                    copied_ref.update(
                        {
                            key: ref[key]
                            for key in ("plan_id", "version", "content_hash")
                            if key in ref
                        }
                    )
                restored[field] = copied_ref
        pricing_ref = old["execution_budget"].get("pricing_snapshot_ref")
        if pricing_ref and not str(pricing_ref).startswith("art_"):
            pricing_content = self.artifacts.read_bytes(
                task_id, old["run_id"], {"sha256": pricing_ref}
            )
            copied_pricing = self.artifacts.put_bytes(
                task_id, new_run_id, "pricing_snapshot", pricing_content
            )
            restored_budget = copy.deepcopy(restored["execution_budget"])
            restored_budget["pricing_snapshot_ref"] = copied_pricing.sha256
            restored["execution_budget"] = restored_budget
        recovery_content = self.artifacts.read_bytes(
            task_id,
            old["run_id"],
            {"sha256": old["active_recovery_point_ref"]},
        )
        restored["active_recovery_point_ref"] = self.artifacts.put_bytes(
            task_id, new_run_id, "recovery_point", recovery_content
        ).sha256
        restored = self.control.transition(
            validate_state(restored),
            expected_revision=old["state_revision"],
            event_type="run_restored",
            payload={
                "parent_run_id": old["run_id"],
                "recovery_point_id": recovery_point_id,
            },
        )
        graph = build_graph(
            self._runtime(source_repo=None, request=request, state=restored),
            self.checkpointer,
        )
        result = self._invoke(graph, new_run_id, restored)
        if idempotency_key:
            self.control.save_idempotent_result(
                task_id, "restore", idempotency_key, result
            )
        return result

    def reconcile(self, task_id: str) -> bool:
        projection = self.control.get_task(task_id)
        if projection is None:
            raise KeyError(task_id)
        projected = validate_state(projection["state"])
        request = self._request_from_state(projected)
        graph = build_graph(
            self._runtime(source_repo=None, request=request, state=projected),
            self.checkpointer,
        )
        snapshot = graph.get_state(
            self._config(
                projection.get("checkpoint_run_id", projected["run_id"])
            )
        )
        if not snapshot.values:
            raise KeyError(f"checkpoint not found for task: {task_id}")
        return self.control.reconcile(validate_state(dict(snapshot.values)))
