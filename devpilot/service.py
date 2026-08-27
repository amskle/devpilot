from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from devpilot.agents.model_gateway import LazyOpenAICompatibleGateway, ModelGateway
from devpilot.agents.runner import AgentRunner
from devpilot.clock import Clock, SystemClock
from devpilot.domain.models import (
    ApprovalRequest,
    ExecutionBudget,
    FailureRecord,
    ModelProfile,
    RecoveryPoint,
    TaskStatus,
    WorkspaceRef,
)
from devpilot.domain.plans import create_replan_request
from devpilot.domain.state import GraphState, create_initial_state, validate_state
from devpilot.errors import BudgetExceededError, PolicyDeniedError, StateConflictError
from devpilot.orchestration.graph import GraphRuntime, build_graph
from devpilot.services.pricing import PricingCatalog
from devpilot.services.storage import ArtifactStore, SQLiteControlStore, default_data_dir
from devpilot.tools.executor import ToolExecutor, build_default_registry
from devpilot.workspace import WorkspaceManager


class TaskService:
    def __init__(
        self,
        *,
        data_dir: Path | None = None,
        gateway: ModelGateway | None = None,
        gateway_factory: Callable[[str], ModelGateway] | None = None,
        clock: Clock | None = None,
        approval_ttl_seconds: int = 86_400,
        verification_command: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ):
        self.data_dir = (data_dir or default_data_dir()).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock or SystemClock()
        self.artifacts = ArtifactStore(self.data_dir)
        self.control = SQLiteControlStore(self.data_dir / "control.sqlite", self.clock)
        self.workspace_manager = WorkspaceManager(self.data_dir / "workspaces", self.clock)
        self.tools = ToolExecutor(build_default_registry())
        self.model_name = model or "gpt-5-mini"
        if gateway is not None and gateway_factory is not None:
            raise ValueError("gateway and gateway_factory are mutually exclusive")
        self._gateway_factory = gateway_factory
        if gateway is None and self._gateway_factory is None:
            self._gateway_factory = lambda selected_model: LazyOpenAICompatibleGateway(
                model=selected_model,
                base_url=base_url,
            )
        if gateway is not None:
            self.gateway = gateway
        else:
            assert self._gateway_factory is not None
            self.gateway = self._gateway_factory(self.model_name)
        self.agents = AgentRunner(self.gateway, self.tools)
        self.approval_ttl_seconds = approval_ttl_seconds
        self.verification_command = verification_command
        self._checkpoint_conn = sqlite3.connect(self.data_dir / "checkpoints.sqlite", check_same_thread=False)
        self.checkpointer = SqliteSaver(self._checkpoint_conn)

    def close(self) -> None:
        self.control.close()
        self._checkpoint_conn.close()

    @staticmethod
    def _config(run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}}

    def _pricing_context(self, state: GraphState) -> tuple[PricingCatalog | None, str]:
        snapshot_ref = state["execution_budget"].get("pricing_snapshot_ref")
        if snapshot_ref is None:
            return None, self.model_name
        if str(snapshot_ref).startswith("art_"):
            # Compatibility for checkpoints created before the snapshot stored
            # its full content hash. Such tasks did not perform cost settlement.
            return None, self.model_name
        raw = self.artifacts.read_text(
            state["task_id"],
            state["run_id"],
            {"sha256": snapshot_ref},
        )
        catalog, selected_model = PricingCatalog.from_snapshot(json.loads(raw))
        selected_catalog = catalog if state["execution_budget"].get("max_cost") is not None else None
        return selected_catalog, selected_model or self.model_name

    def _agents_for_model(self, model_name: str) -> AgentRunner:
        if model_name == self.model_name:
            return self.agents
        if self._gateway_factory is None:
            raise ValueError(
                "per-task model override requires a gateway_factory when a gateway is injected"
            )
        return AgentRunner(self._gateway_factory(model_name), self.tools)

    def _runtime(
        self,
        *,
        source_repo: Path | None,
        request: str,
        revision: str = "HEAD",
        state: GraphState | None = None,
        pricing_catalog: PricingCatalog | None = None,
        model_name: str | None = None,
    ) -> GraphRuntime:
        selected_model = model_name or self.model_name
        selected_catalog = pricing_catalog
        if state is not None and selected_catalog is None:
            selected_catalog, selected_model = self._pricing_context(state)
        return GraphRuntime(
            source_repo=source_repo,
            request=request,
            artifacts=self.artifacts,
            control=self.control,
            workspace_manager=self.workspace_manager,
            tools=self.tools,
            agents=self._agents_for_model(selected_model),
            clock=self.clock,
            approval_ttl_seconds=self.approval_ttl_seconds,
            verification_command=self.verification_command,
            revision=revision,
            model_profile=ModelProfile(provider="openai-compatible", model=selected_model),
            pricing_catalog=selected_catalog,
        )

    def _request_from_state(self, state: GraphState, *, artifact_run_id: str | None = None) -> str:
        ref = state["context_delta_ref"]
        if ref is None:
            return ""
        return self.artifacts.read_text(state["task_id"], artifact_run_id or state["run_id"], ref)

    def _invoke(self, graph: Any, run_id: str, value: GraphState | Command) -> GraphState:
        config = self._config(run_id)
        try:
            graph.invoke(value, config=config)
        except StateConflictError:
            raise
        except Exception as exc:
            snapshot = graph.get_state(config)
            recovered: GraphState | None = None
            if snapshot.values:
                recovered = validate_state(dict(snapshot.values))
                self.control.reconcile(recovered)
            else:
                task_id = value.get("task_id") if isinstance(value, dict) else None
                projection = self.control.get_task(task_id) if task_id else None
                if projection and projection["checkpoint_run_id"] != run_id:
                    previous = graph.get_state(self._config(projection["checkpoint_run_id"]))
                    if previous.values:
                        recovered = validate_state(dict(previous.values))
                        self.control.reconcile(recovered)
            if recovered is None:
                raise
            if isinstance(exc, PolicyDeniedError):
                status = TaskStatus.POLICY_REJECTED.value
                category = "POLICY"
            elif isinstance(exc, BudgetExceededError):
                status = TaskStatus.WAITING_HUMAN_INTERVENTION.value
                category = "BUDGET"
            else:
                status = TaskStatus.FAILED.value
                category = "NODE"
            error_code = str(getattr(exc, "code", type(exc).__name__))
            failure = FailureRecord(
                failure_id=f"failure_{uuid.uuid4().hex[:16]}",
                iteration=ExecutionBudget.from_state_dict(recovered["execution_budget"]).iterations_used,
                category=category,
                error_code=error_code,
                summary=str(exc)[:1000],
                symptom_fingerprint=hashlib.sha256(f"{error_code}:{type(exc).__name__}".encode()).hexdigest(),
                change_fingerprint=(recovered["patch_proposal"] or {}).get("patch_hash"),
                retry_policy="NEVER",
                recovery_action="HUMAN" if status == TaskStatus.WAITING_HUMAN_INTERVENTION.value else "FAIL",
                agent_actionable=False,
                related_files=(recovered["patch_proposal"] or {}).get("changed_files", []),
                artifact_refs=[],
                occurred_at=self.clock.now().isoformat(),
            )
            failed = copy.deepcopy(recovered)
            failure_budget = getattr(exc, "execution_budget", None)
            failed.update(
                {
                    "status": status,
                    "pause_reason": error_code,
                    "current_node": "node_failure_router",
                    "latest_failure": failure.to_state_dict(),
                    "execution_budget": (
                        ExecutionBudget.from_state_dict(failure_budget).to_state_dict()
                        if isinstance(failure_budget, dict)
                        else recovered["execution_budget"]
                    ),
                }
            )
            failed = self.control.transition(
                validate_state(failed), expected_revision=recovered["state_revision"],
                event_type="node_failed", payload={"category": category, "error_code": error_code},
            )
            graph.update_state(config, failed)
            self.control.confirm_checkpoint(failed["task_id"], failed["run_id"], failed["state_revision"])
            return failed
        snapshot = graph.get_state(config)
        state = validate_state(dict(snapshot.values))
        self.control.confirm_checkpoint(state["task_id"], state["run_id"], state["state_revision"])
        return state

    def create_task(
        self,
        repo: Path,
        request: str,
        *,
        revision: str = "HEAD",
        budget: ExecutionBudget | None = None,
        model: str | None = None,
    ) -> GraphState:
        task_id = f"task_{uuid.uuid4().hex[:16]}"
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        selected_budget = budget or ExecutionBudget()
        selected_model = model or self.model_name
        catalog = PricingCatalog.from_file(self.data_dir / "pricing" / "catalog.json")
        if selected_budget.max_cost is not None and selected_model not in catalog.entries:
            raise ValueError(f"max_cost requires pricing data for model: {selected_model}")
        if selected_model != self.model_name and self._gateway_factory is None:
            raise ValueError(
                "per-task model override requires a gateway_factory when a gateway is injected"
            )
        self.workspace_manager.validate_source(repo.resolve(), revision)
        state = create_initial_state(task_id, run_id, budget=selected_budget)
        request_ref = self.artifacts.put_text(task_id, run_id, "task_request", request)
        state["context_delta_ref"] = request_ref.to_state_dict()
        snapshot = catalog.snapshot(
            self.artifacts,
            task_id,
            run_id,
            selected_model=selected_model,
        )
        state["execution_budget"]["pricing_snapshot_ref"] = snapshot["sha256"]
        state = validate_state(state)
        self.control.create_task(state)
        runtime = self._runtime(
            source_repo=repo.resolve(),
            request=request,
            revision=revision,
            pricing_catalog=catalog if selected_budget.max_cost is not None else None,
            model_name=selected_model,
        )
        graph = build_graph(runtime, self.checkpointer)
        return self._invoke(graph, run_id, state)

    def _checkpoint_state(self, run_id: str, runtime: GraphRuntime) -> tuple[Any, GraphState]:
        graph = build_graph(runtime, self.checkpointer)
        snapshot = graph.get_state(self._config(run_id))
        if not snapshot.values:
            raise KeyError(f"checkpoint not found for run: {run_id}")
        state = validate_state(dict(snapshot.values))
        self.control.reconcile(state)
        return graph, state

    def get_state(self, task_id: str, *, check_expiry: bool = True) -> GraphState:
        projection = self.control.get_task(task_id)
        if projection is None:
            raise KeyError(task_id)
        projected = validate_state(projection["state"])
        request = self._request_from_state(projected)
        checkpoint_run_id = projection.get("checkpoint_run_id", projected["run_id"])
        graph, state = self._checkpoint_state(
            checkpoint_run_id,
            self._runtime(source_repo=None, request=request, state=projected),
        )
        if check_expiry:
            state = self._expire_approval_if_needed(graph, state)
        return state

    def _expire_approval_if_needed(self, graph: Any, state: GraphState) -> GraphState:
        if state["status"] != TaskStatus.WAITING_RISK_APPROVAL.value or state["pending_approval"] is None:
            return state
        approval = ApprovalRequest.from_state_dict(state["pending_approval"])
        if self.clock.now() < datetime.fromisoformat(approval.expires_at):
            return state
        updated = copy.deepcopy(state)
        proposal = updated.get("patch_proposal")
        if proposal is not None:
            proposal = {**proposal, "status": "INVALIDATED"}
        updated.update(
            {
                "status": TaskStatus.CANCELLED.value,
                "pause_reason": "APPROVAL_EXPIRED",
                "pending_approval": None,
                "patch_proposal": proposal,
            }
        )
        updated = self.control.transition(
            validate_state(updated),
            expected_revision=state["state_revision"],
            event_type="approval_expired",
            payload={"approval_id": approval.approval_id},
        )
        graph.update_state(self._config(state["run_id"]), updated, as_node="approval_gate")
        self.control.confirm_checkpoint(state["task_id"], state["run_id"], updated["state_revision"])
        return updated

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
        operation = "approve" if decision == "APPROVE" else "reject"
        if idempotency_key:
            cached = self.control.idempotent_result(task_id, operation, idempotency_key)
            if cached:
                return validate_state(cached)
        state = self.get_state(task_id)
        if state["state_revision"] != expected_revision:
            raise StateConflictError(f"expected state_revision {expected_revision}, actual {state['state_revision']}")
        if state["status"] != TaskStatus.WAITING_RISK_APPROVAL.value or state["pending_approval"] is None:
            raise StateConflictError("task is not waiting for risk approval")
        approval = ApprovalRequest.from_state_dict(state["pending_approval"])
        expected = (approval.approval_id, approval.patch_hash, approval.base_revision)
        received = (approval_id, patch_hash, base_revision)
        if received != expected:
            raise ValueError("approval target does not match pending patch")
        request = self._request_from_state(state)
        graph = build_graph(self._runtime(source_repo=None, request=request, state=state), self.checkpointer)
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
            raise StateConflictError(f"expected state_revision {expected_revision}, actual {state['state_revision']}")
        if state["status"] in {TaskStatus.COMPLETED.value, TaskStatus.COMPLETED_NO_CHANGES.value, TaskStatus.CANCELLED.value}:
            if idempotency_key:
                self.control.save_idempotent_result(task_id, "cancel", idempotency_key, state)
            return state
        request = self._request_from_state(state)
        graph = build_graph(self._runtime(source_repo=None, request=request, state=state), self.checkpointer)
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
            validate_state(updated), expected_revision=expected_revision, event_type="task_cancelled", payload={}
        )
        graph.update_state(self._config(state["run_id"]), updated)
        self.control.confirm_checkpoint(task_id, state["run_id"], updated["state_revision"])
        if idempotency_key:
            self.control.save_idempotent_result(task_id, "cancel", idempotency_key, updated)
        return updated

    def rollback(
        self,
        task_id: str,
        recovery_point_id: str,
        expected_revision: int,
        *,
        idempotency_key: str | None = None,
    ) -> GraphState:
        if idempotency_key:
            cached = self.control.idempotent_result(task_id, "rollback", idempotency_key)
            if cached:
                return validate_state(cached)
        state = self.get_state(task_id)
        if state["state_revision"] != expected_revision:
            raise StateConflictError(f"expected state_revision {expected_revision}, actual {state['state_revision']}")
        if not state["active_recovery_point_ref"]:
            raise ValueError("task has no active recovery point")
        raw = self.artifacts.read_text(task_id, state["run_id"], {"sha256": state["active_recovery_point_ref"]})
        recovery = RecoveryPoint.from_state_dict(json.loads(raw))
        if recovery.recovery_point_id != recovery_point_id:
            raise ValueError("recovery point does not match active recovery point")
        budget = ExecutionBudget.from_state_dict(state["execution_budget"])
        if budget.rollbacks_used >= budget.max_rollbacks:
            raise ValueError("rollback budget exhausted")
        workspace_ref = WorkspaceRef.from_state_dict(state["workspace_ref"] or {})
        self.workspace_manager.validate_lease(workspace_ref, state["run_id"])
        workspace = self.workspace_manager.rollback(workspace_ref, recovery.repository_snapshot_id)
        updated = copy.deepcopy(state)
        updated.update(
            {
                "workspace_ref": workspace.to_state_dict(),
                "patch_proposal": None,
                "verification": None,
                "execution_budget": budget.model_copy(update={"rollbacks_used": budget.rollbacks_used + 1}).to_state_dict(),
                "status": TaskStatus.WAITING_HUMAN_INTERVENTION.value,
                "pause_reason": "ROLLBACK_COMPLETED",
            }
        )
        request = self._request_from_state(state)
        graph = build_graph(self._runtime(source_repo=None, request=request, state=state), self.checkpointer)
        updated = self.control.transition(
            validate_state(updated), expected_revision=expected_revision, event_type="rollback_completed",
            payload={"recovery_point_id": recovery_point_id},
        )
        graph.update_state(self._config(state["run_id"]), updated)
        self.control.confirm_checkpoint(task_id, state["run_id"], updated["state_revision"])
        if idempotency_key:
            self.control.save_idempotent_result(task_id, "rollback", idempotency_key, updated)
        return updated

    def restore(
        self,
        task_id: str,
        recovery_point_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> GraphState:
        if idempotency_key:
            cached = self.control.idempotent_result(task_id, "restore", idempotency_key)
            if cached:
                return validate_state(cached)
        old = self.get_state(task_id)
        if not old["active_recovery_point_ref"]:
            raise ValueError("task has no active recovery point")
        raw = self.artifacts.read_text(task_id, old["run_id"], {"sha256": old["active_recovery_point_ref"]})
        recovery = RecoveryPoint.from_state_dict(json.loads(raw))
        if recovery.recovery_point_id != recovery_point_id:
            raise ValueError("recovery point does not match active recovery point")
        request = self._request_from_state(old)
        workspace_ref = WorkspaceRef.from_state_dict(old["workspace_ref"] or {})
        self.workspace_manager.validate_lease(workspace_ref, old["run_id"])
        workspace = self.workspace_manager.rollback(workspace_ref, recovery.repository_snapshot_id)
        new_run_id = f"run_{uuid.uuid4().hex[:16]}"
        restored = copy.deepcopy(old)
        restored.update(
            {
                "run_id": new_run_id,
                "parent_run_id": old["run_id"],
                "workspace_ref": workspace.model_copy(update={"lease_owner": new_run_id}).to_state_dict(),
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
                copied_ref = self.artifacts.put_bytes(task_id, new_run_id, kind, content).to_state_dict()
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
                task_id,
                old["run_id"],
                {"sha256": pricing_ref},
            )
            copied_pricing = self.artifacts.put_bytes(
                task_id,
                new_run_id,
                "pricing_snapshot",
                pricing_content,
            )
            restored_budget = copy.deepcopy(restored["execution_budget"])
            restored_budget["pricing_snapshot_ref"] = copied_pricing.sha256
            restored["execution_budget"] = restored_budget
        recovery_content = self.artifacts.read_bytes(
            task_id, old["run_id"], {"sha256": old["active_recovery_point_ref"]}
        )
        restored["active_recovery_point_ref"] = self.artifacts.put_bytes(
            task_id, new_run_id, "recovery_point", recovery_content
        ).sha256
        restored = self.control.transition(
            validate_state(restored), expected_revision=old["state_revision"], event_type="run_restored",
            payload={"parent_run_id": old["run_id"], "recovery_point_id": recovery_point_id},
        )
        graph = build_graph(self._runtime(source_repo=None, request=request, state=restored), self.checkpointer)
        result = self._invoke(graph, new_run_id, restored)
        if idempotency_key:
            self.control.save_idempotent_result(task_id, "restore", idempotency_key, result)
        return result

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
        """Resume an intervention by creating a structured ReplanRequest."""
        if idempotency_key:
            cached = self.control.idempotent_result(task_id, "replan", idempotency_key)
            if cached:
                return validate_state(cached)
        state = self.get_state(task_id)
        if state["state_revision"] != expected_revision:
            raise StateConflictError(f"expected state_revision {expected_revision}, actual {state['state_revision']}")
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
                "pending_replan_request": replan_request.to_state_dict(),
                "execution_budget": budget.model_copy(
                    update={"plan_revisions_used": budget.plan_revisions_used + 1}
                ).to_state_dict(),
                "patch_proposal": proposal,
                "diagnosis": None,
                "verification": None,
                "review": None,
                "current_node": "prepare_replan",
            }
        )
        request = self._request_from_state(state)
        graph = build_graph(self._runtime(source_repo=None, request=request, state=state), self.checkpointer)
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

    def plan_history(self, task_id: str) -> list[dict[str, Any]]:
        return self.control.plans(task_id)

    def replan_history(self, task_id: str) -> list[dict[str, Any]]:
        return self.control.replan_requests(task_id)

    def event_history(
        self,
        task_id: str,
        run_id: str | None = None,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read durable, redacted events for cursor catch-up or audit."""

        if self.control.get_task(task_id) is None:
            raise KeyError(task_id)
        return self.control.events(
            task_id,
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    def trace(self, task_id: str, run_id: str | None = None) -> dict[str, Any]:
        """Return the complete persisted event trace for a task or run."""

        if self.control.get_task(task_id) is None:
            raise KeyError(task_id)
        return self.control.trace(task_id, run_id)

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
            raise StateConflictError(f"expected state_revision {expected_revision}, actual {state['state_revision']}")
        if state["status"] != TaskStatus.WAITING_HUMAN_INTERVENTION.value:
            raise StateConflictError("task is not waiting for human intervention")
        request = self._request_from_state(state)
        graph = build_graph(self._runtime(source_repo=None, request=request, state=state), self.checkpointer)
        updated = copy.deepcopy(state)
        updated.update({"status": TaskStatus.RUNNING.value, "pause_reason": None})
        updated = self.control.transition(
            validate_state(updated), expected_revision=expected_revision,
            event_type="human_intervention_resumed", payload={},
        )
        graph.update_state(self._config(state["run_id"]), updated, as_node="failure_router")
        self.control.confirm_checkpoint(task_id, state["run_id"], updated["state_revision"])
        result = self._invoke(graph, state["run_id"], Command(goto="diagnosis"))
        if idempotency_key:
            self.control.save_idempotent_result(task_id, "resume", idempotency_key, result)
        return result

    def reconcile(self, task_id: str) -> bool:
        projection = self.control.get_task(task_id)
        if projection is None:
            raise KeyError(task_id)
        projected = validate_state(projection["state"])
        request = self._request_from_state(projected)
        graph = build_graph(self._runtime(source_repo=None, request=request, state=projected), self.checkpointer)
        snapshot = graph.get_state(self._config(projection.get("checkpoint_run_id", projected["run_id"])))
        if not snapshot.values:
            raise KeyError(f"checkpoint not found for task: {task_id}")
        return self.control.reconcile(validate_state(dict(snapshot.values)))
