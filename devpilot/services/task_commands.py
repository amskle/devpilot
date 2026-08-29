from __future__ import annotations

import copy
import uuid

from langgraph.types import Command

from devpilot.domain.models import (
    ApprovalRequest,
    ChangeRequest,
    ExecutionBudget,
    TERMINAL_STATUSES,
    TaskStatus,
)
from devpilot.domain.plans import create_replan_request
from devpilot.domain.state import GraphState, validate_state
from devpilot.errors import BudgetExceededError, StateConflictError
from devpilot.orchestration.graph import build_graph


class TaskCommands:
    """Implement user-issued task control and replanning commands."""

    def decide_approval(
        self,
        task_id: str,
        *,
        decision: str,
        approval_id: str,
        patch_hash: str,
        base_revision: str,
        expected_revision: int,
        decided_by: str = "cli",
        idempotency_key: str | None = None,
    ) -> GraphState:
        if decision not in {"APPROVE", "REJECT"}:
            raise ValueError("decision must be APPROVE or REJECT")
        operation = "approve" if decision == "APPROVE" else "reject"
        if idempotency_key:
            cached = self.control.idempotent_result(task_id, operation, idempotency_key)
            if cached:
                return validate_state(cached)
        state = self.get_state(task_id)
        if state["state_revision"] != expected_revision:
            raise StateConflictError(
                f"expected state_revision {expected_revision}, actual {state['state_revision']}"
            )
        if (
            state["status"] != TaskStatus.WAITING_RISK_APPROVAL.value
            or state["pending_approval"] is None
        ):
            raise StateConflictError("task is not waiting for risk approval")
        approval = ApprovalRequest.from_state_dict(state["pending_approval"])
        expected = (approval.approval_id, approval.patch_hash, approval.base_revision)
        if (approval_id, patch_hash, base_revision) != expected:
            raise ValueError("approval target does not match pending patch")
        request = self._request_from_state(state)
        graph = build_graph(
            self._runtime(source_repo=None, request=request, state=state),
            self.checkpointer,
        )
        result = self._invoke(
            graph,
            state["run_id"],
            Command(
                resume={
                    "decision": decision,
                    "approval_id": approval_id,
                    "patch_hash": patch_hash,
                    "base_revision": base_revision,
                    "decided_by": decided_by,
                }
            ),
        )
        if idempotency_key:
            self.control.save_idempotent_result(task_id, operation, idempotency_key, result)
        return result

    def cancel(
        self,
        task_id: str,
        expected_revision: int,
        *,
        idempotency_key: str | None = None,
    ) -> GraphState:
        if idempotency_key:
            cached = self.control.idempotent_result(task_id, "cancel", idempotency_key)
            if cached:
                return validate_state(cached)
        state = self.get_state(task_id)
        if state["state_revision"] != expected_revision:
            raise StateConflictError(
                f"expected state_revision {expected_revision}, actual {state['state_revision']}"
            )
        if state["status"] in TERMINAL_STATUSES:
            if idempotency_key:
                self.control.save_idempotent_result(task_id, "cancel", idempotency_key, state)
            return state
        request = self._request_from_state(state)
        graph = build_graph(
            self._runtime(source_repo=None, request=request, state=state),
            self.checkpointer,
        )
        updated = copy.deepcopy(state)
        proposal = updated.get("patch_proposal")
        if proposal is not None:
            proposal = {**proposal, "status": "INVALIDATED"}
        updated.update(
            {
                "status": TaskStatus.CANCELLED.value,
                "pause_reason": "USER_CANCELLED",
                "pending_approval": None,
                "patch_proposal": proposal,
            }
        )
        updated = self.control.transition(
            validate_state(updated),
            expected_revision=expected_revision,
            event_type="task_cancelled",
            payload={},
        )
        graph.update_state(self._config(state["run_id"]), updated)
        self.control.confirm_checkpoint(task_id, state["run_id"], updated["state_revision"])
        if idempotency_key:
            self.control.save_idempotent_result(task_id, "cancel", idempotency_key, updated)
        return updated

    def replan(
        self,
        task_id: str,
        expected_revision: int,
        *,
        reason: str,
        reason_code: str = "HUMAN_REQUESTED_REPLAN",
        evidence_refs: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> GraphState:
        if idempotency_key:
            cached = self.control.idempotent_result(task_id, "replan", idempotency_key)
            if cached:
                return validate_state(cached)
        state = self.get_state(task_id)
        if state["state_revision"] != expected_revision:
            raise StateConflictError(
                f"expected state_revision {expected_revision}, actual {state['state_revision']}"
            )
        if state["status"] != TaskStatus.WAITING_HUMAN_INTERVENTION.value:
            raise StateConflictError("task is not waiting for human intervention")
        if state["active_plan_ref"] is None:
            raise StateConflictError("task has no active Plan to revise")
        budget = ExecutionBudget.from_state_dict(state["execution_budget"])
        if budget.plan_revisions_used >= budget.max_plan_revisions:
            raise BudgetExceededError("plan revision budget exhausted")
        replan_request = create_replan_request(
            task_id=task_id,
            run_id=state["run_id"],
            active_plan_ref=state["active_plan_ref"],
            reason_code=reason_code,
            summary=reason,
            requested_at=self.clock.now().isoformat(),
            evidence_refs=evidence_refs,
        )
        updated = copy.deepcopy(state)
        proposal = updated.get("patch_proposal")
        if proposal is not None:
            proposal = {**proposal, "status": "INVALIDATED"}
        updated.update(
            {
                "status": TaskStatus.RUNNING.value,
                "pause_reason": None,
                "workspace_ref": self._renew_workspace_lease(state),
                "pending_replan_request": replan_request.to_state_dict(),
                "execution_budget": budget.model_copy(
                    update={"plan_revisions_used": budget.plan_revisions_used + 1}
                ).to_state_dict(),
                "patch_proposal": proposal,
                "diagnosis": None,
                "verification": None,
                "review": None,
                "progress_window": {"entries": [], "no_progress_rounds": 0},
                "current_node": "prepare_replan",
            }
        )
        request = self._request_from_state(state)
        graph = build_graph(
            self._runtime(source_repo=None, request=request, state=state),
            self.checkpointer,
        )
        updated = self.control.prepare_replan(
            validate_state(updated),
            expected_revision=expected_revision,
            request=replan_request,
            payload={"reason_code": reason_code, "source": "human_intervention"},
        )
        graph.update_state(self._config(state["run_id"]), updated, as_node="prepare_replan")
        self.control.confirm_checkpoint(task_id, state["run_id"], updated["state_revision"])
        result = self._invoke(graph, state["run_id"], Command(goto="planning"))
        if idempotency_key:
            self.control.save_idempotent_result(task_id, "replan", idempotency_key, result)
        return result

    def change_request(
        self,
        task_id: str,
        expected_revision: int,
        *,
        content: str,
        requested_by: str,
        confirm_patch_invalidation: bool,
        idempotency_key: str | None = None,
    ) -> GraphState:
        operation = "change-requests"
        if idempotency_key:
            cached = self.control.idempotent_result(task_id, operation, idempotency_key)
            if cached:
                return validate_state(cached)
        state = self.get_state(task_id)
        if state["state_revision"] != expected_revision:
            raise StateConflictError(
                f"expected state_revision {expected_revision}, actual {state['state_revision']}"
            )
        if state["status"] in TERMINAL_STATUSES:
            raise StateConflictError("terminal tasks cannot be changed in place")
        if state["active_plan_ref"] is None:
            raise StateConflictError("task has no active Plan to revise")
        if (
            state["status"] == TaskStatus.WAITING_RISK_APPROVAL.value
            and not confirm_patch_invalidation
        ):
            raise StateConflictError(
                "confirm_patch_invalidation is required while risk approval is pending"
            )
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("ChangeRequest content must not be empty")
        budget = ExecutionBudget.from_state_dict(state["execution_budget"])
        if budget.plan_revisions_used >= budget.max_plan_revisions:
            raise BudgetExceededError("plan revision budget exhausted")
        requested_at = self.clock.now().isoformat()
        change = ChangeRequest(
            change_request_id=f"change_{uuid.uuid4().hex[:16]}",
            task_id=task_id,
            run_id=state["run_id"],
            content=normalized_content,
            requested_by=requested_by,
            requested_at=requested_at,
            expected_state_revision=expected_revision,
            confirm_patch_invalidation=confirm_patch_invalidation,
        )
        replan_request = create_replan_request(
            task_id=task_id,
            run_id=state["run_id"],
            active_plan_ref=state["active_plan_ref"],
            reason_code="USER_CHANGE_REQUEST",
            summary=normalized_content,
            requested_at=requested_at,
            source_change_request_id=change.change_request_id,
        )
        updated = copy.deepcopy(state)
        proposal = updated.get("patch_proposal")
        invalidated_patch_id = None
        if proposal is not None:
            invalidated_patch_id = proposal.get("patch_id")
            proposal = {**proposal, "status": "INVALIDATED"}
        approval = updated.get("pending_approval")
        invalidated_approval_id = approval.get("approval_id") if approval else None
        request_ref = self.artifacts.put_text(
            state["task_id"], state["run_id"], "task_request", normalized_content
        )
        updated.update(
            {
                "status": TaskStatus.RUNNING.value,
                "pause_reason": None,
                "workspace_ref": self._renew_workspace_lease(state),
                "context_delta_ref": request_ref.to_state_dict(),
                "pending_approval": None,
                "pending_replan_request": replan_request.to_state_dict(),
                "execution_budget": budget.model_copy(
                    update={"plan_revisions_used": budget.plan_revisions_used + 1}
                ).to_state_dict(),
                "patch_proposal": proposal,
                "diagnosis": None,
                "verification": None,
                "review": None,
                "progress_window": {"entries": [], "no_progress_rounds": 0},
                "current_node": "prepare_replan",
            }
        )
        graph = build_graph(
            self._runtime(source_repo=None, request=normalized_content, state=state),
            self.checkpointer,
        )
        updated = self.control.prepare_change_request(
            validate_state(updated),
            expected_revision=expected_revision,
            change_request=change,
            replan_request=replan_request,
            invalidated_approval_id=invalidated_approval_id,
            invalidated_patch_id=invalidated_patch_id,
        )
        graph.update_state(self._config(state["run_id"]), updated, as_node="prepare_replan")
        self.control.confirm_checkpoint(task_id, state["run_id"], updated["state_revision"])
        result = self._invoke(graph, state["run_id"], Command(goto="planning"))
        if idempotency_key:
            self.control.save_idempotent_result(task_id, operation, idempotency_key, result)
        return result

    def resume(
        self,
        task_id: str,
        expected_revision: int,
        *,
        idempotency_key: str | None = None,
    ) -> GraphState:
        if idempotency_key:
            cached = self.control.idempotent_result(task_id, "resume", idempotency_key)
            if cached:
                return validate_state(cached)
        state = self.get_state(task_id)
        if state["state_revision"] != expected_revision:
            raise StateConflictError(
                f"expected state_revision {expected_revision}, actual {state['state_revision']}"
            )
        if state["status"] != TaskStatus.WAITING_HUMAN_INTERVENTION.value:
            raise StateConflictError("task is not waiting for human intervention")
        request = self._request_from_state(state)
        graph = build_graph(
            self._runtime(source_repo=None, request=request, state=state),
            self.checkpointer,
        )
        updated = copy.deepcopy(state)
        updated.update(
            {
                "status": TaskStatus.RUNNING.value,
                "pause_reason": None,
                "workspace_ref": self._renew_workspace_lease(state),
            }
        )
        updated = self.control.transition(
            validate_state(updated),
            expected_revision=expected_revision,
            event_type="human_intervention_resumed",
            payload={},
        )
        graph.update_state(self._config(state["run_id"]), updated, as_node="failure_router")
        self.control.confirm_checkpoint(task_id, state["run_id"], updated["state_revision"])
        result = self._invoke(graph, state["run_id"], Command(goto="diagnosis"))
        if idempotency_key:
            self.control.save_idempotent_result(task_id, "resume", idempotency_key, result)
        return result
