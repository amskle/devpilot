from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from devpilot.agents.definitions import AGENT_SPECS, OUTPUT_MODELS
from devpilot.agents.runner import AgentRunner
from devpilot.clock import Clock
from devpilot.domain.models import (
    ApprovalRequest,
    ExecutionBudget,
    FailureRecord,
    ModelProfile,
    PatchProposal,
    RecoveryPoint,
    TaskStatus,
    WorkspaceRef,
)
from devpilot.domain.progress import evaluate_progress_signals
from devpilot.domain.state import GraphState, replace_progress_window, validate_state
from devpilot.services.storage import ArtifactStore, SQLiteControlStore
from devpilot.services.pricing import PricingCatalog
from devpilot.tools.executor import ToolExecutor
from devpilot.workspace import WorkspaceManager


@dataclass
class GraphRuntime:
    source_repo: Path | None
    request: str
    artifacts: ArtifactStore
    control: SQLiteControlStore
    workspace_manager: WorkspaceManager
    tools: ToolExecutor
    agents: AgentRunner
    clock: Clock
    approval_ttl_seconds: int = 86_400
    verification_command: str | None = None
    revision: str = "HEAD"
    model_profile: ModelProfile | None = None
    pricing_catalog: PricingCatalog | None = None


def _merge_transition(
    runtime: GraphRuntime,
    state: GraphState,
    updates: dict[str, Any],
    *,
    node: str,
    event_type: str,
    allowed: set[str],
    payload: dict[str, Any] | None = None,
) -> GraphState:
    unauthorized = set(updates) - allowed
    if unauthorized:
        raise RuntimeError(f"node {node} attempted unauthorized state writes: {sorted(unauthorized)}")
    merged: GraphState = copy.deepcopy(state)
    merged.update(copy.deepcopy(updates))
    merged["current_node"] = node
    merged = validate_state(merged)
    return runtime.control.transition(
        merged,
        expected_revision=state["state_revision"],
        event_type=event_type,
        payload={"node": node, **(payload or {})},
    )


def _read_json(runtime: GraphRuntime, state: GraphState, ref: dict[str, Any] | None) -> Any:
    if ref is None:
        return None
    return json.loads(runtime.artifacts.read_text(state["task_id"], state["run_id"], ref))


def _workspace(state: GraphState) -> WorkspaceRef:
    if state["workspace_ref"] is None:
        raise RuntimeError("workspace is not initialized")
    return WorkspaceRef.from_state_dict(state["workspace_ref"])


def _raise_agent_error(invocation: Any) -> None:
    error = RuntimeError(invocation.result.error)
    error.execution_budget = invocation.execution_budget
    raise error


def build_graph(runtime: GraphRuntime, checkpointer: Any):
    builder = StateGraph(GraphState)

    def workspace_setup(state: GraphState) -> GraphState:
        if state["workspace_ref"] is not None:
            return _merge_transition(
                runtime, state, {}, node="workspace_setup", event_type="workspace_reused", allowed=set()
            )
        if runtime.source_repo is None:
            raise RuntimeError("source repository is required for a new task")
        workspace = runtime.workspace_manager.create(
            runtime.source_repo, state["task_id"], state["run_id"], revision=runtime.revision
        )
        return _merge_transition(
            runtime,
            state,
            {"workspace_ref": workspace.to_state_dict(), "status": TaskStatus.RUNNING.value},
            node="workspace_setup",
            event_type="workspace_created",
            allowed={"workspace_ref", "status"},
            payload={"workspace_id": workspace.workspace_id, "baseline_revision": workspace.baseline_revision},
        )

    def baseline_context(state: GraphState) -> GraphState:
        if state["baseline_context_ref"] is not None:
            return _merge_transition(runtime, state, {}, node="baseline_context", event_type="baseline_reused", allowed=set())
        workspace = _workspace(state)
        result = runtime.tools.execute(
            "project-context",
            {"workspace_id": workspace.workspace_id},
            workspace=workspace,
            allowed_tools=("project-context",),
            agent_id=None,
            operation_id=f"baseline:{state['run_id']}",
            execution_budget=state["execution_budget"],
        )
        ref = runtime.artifacts.put_json(state["task_id"], state["run_id"], "baseline_context", result.output)
        return _merge_transition(
            runtime,
            state,
            {"baseline_context_ref": ref.to_state_dict(), "execution_budget": result.execution_budget},
            node="baseline_context",
            event_type="baseline_context_built",
            allowed={"baseline_context_ref", "execution_budget"},
        )

    def planning(state: GraphState) -> GraphState:
        if state["active_plan_ref"] is not None and state["pending_replan_request"] is None:
            return _merge_transition(runtime, state, {}, node="planning", event_type="plan_reused", allowed=set())
        invocation = runtime.agents.invoke(
            AGENT_SPECS["planning"],
            node_context={
                "request": runtime.request,
                "baseline": _read_json(runtime, state, state["baseline_context_ref"]),
                "mode": "replan" if state["pending_replan_request"] else "initial",
            },
            output_model=OUTPUT_MODELS["PlanDraft"],
            workspace=_workspace(state),
            execution_budget=state["execution_budget"],
            model_profile=runtime.model_profile,
            pricing_catalog=runtime.pricing_catalog,
        )
        if invocation.result.status != "ok":
            _raise_agent_error(invocation)
        ref = runtime.artifacts.put_json(state["task_id"], state["run_id"], "plan", invocation.result.structured_output)
        return _merge_transition(
            runtime,
            state,
            {
                "active_plan_ref": ref.to_state_dict(),
                "pending_replan_request": None,
                "execution_budget": invocation.execution_budget,
            },
            node="planning",
            event_type="plan_activated",
            allowed={"active_plan_ref", "pending_replan_request", "execution_budget"},
            payload={"agent_summary": invocation.result.summary},
        )

    def diagnosis(state: GraphState) -> GraphState:
        invocation = runtime.agents.invoke(
            AGENT_SPECS["diagnosis"],
            node_context={"request": runtime.request, "plan": _read_json(runtime, state, state["active_plan_ref"])},
            output_model=OUTPUT_MODELS["DiagnosisSummary"],
            workspace=_workspace(state),
            execution_budget=state["execution_budget"],
            model_profile=runtime.model_profile,
            pricing_catalog=runtime.pricing_catalog,
        )
        if invocation.result.status != "ok":
            _raise_agent_error(invocation)
        updates: dict[str, Any] = {
            "diagnosis": invocation.result.structured_output,
            "execution_budget": invocation.execution_budget,
        }
        if (
            state["latest_failure"] is not None
            and invocation.result.structured_output.get("outcome") == "NO_ACTION_REQUIRED"
        ):
            stalled_window = copy.deepcopy(state["progress_window"])
            stalled_window["no_progress_rounds"] = max(
                2,
                int(stalled_window.get("no_progress_rounds", 0)),
            )
            updates["progress_window"] = stalled_window
        return _merge_transition(
            runtime,
            state,
            updates,
            node="diagnosis",
            event_type="diagnosis_completed",
            allowed={"diagnosis", "execution_budget", "progress_window"},
            payload={"agent_summary": invocation.result.summary},
        )

    def patch_generation(state: GraphState) -> GraphState:
        invocation = runtime.agents.invoke(
            AGENT_SPECS["patch_generation"],
            node_context={
                "request": runtime.request,
                "plan": _read_json(runtime, state, state["active_plan_ref"]),
                "diagnosis": state["diagnosis"],
            },
            output_model=OUTPUT_MODELS["PatchDraft"],
            workspace=_workspace(state),
            execution_budget=state["execution_budget"],
            model_profile=runtime.model_profile,
            pricing_catalog=runtime.pricing_catalog,
        )
        if invocation.result.status != "ok":
            _raise_agent_error(invocation)
        workspace = _workspace(state)
        budget = invocation.execution_budget
        diffs: list[str] = []
        files: list[str] = []
        for index, operation in enumerate(invocation.result.structured_output["operations"]):
            result = runtime.tools.execute(
                "patch-generate",
                {
                    "workspace_id": workspace.workspace_id,
                    "target_file": operation["target_file"],
                    "replacements": operation["replacements"],
                },
                workspace=workspace,
                allowed_tools=("patch-generate",),
                agent_id="patch_generation",
                operation_id=f"patch:{state['run_id']}:{state['state_revision']}:{index}",
                execution_budget=budget,
            )
            budget = result.execution_budget
            if result.output["diff"]:
                diffs.append(result.output["diff"])
                files.append(operation["target_file"])
        patch = "".join(diffs)
        patch_ref = runtime.artifacts.put_text(state["task_id"], state["run_id"], "patch", patch)
        proposal = PatchProposal(
            patch_id=f"patch_{uuid.uuid4().hex[:16]}",
            patch_ref=patch_ref.to_state_dict(),
            patch_hash=hashlib.sha256(patch.encode()).hexdigest(),
            base_revision=workspace.current_revision,
            changed_files=files,
            summary=invocation.result.structured_output["summary"],
        )
        return _merge_transition(
            runtime,
            state,
            {"patch_proposal": proposal.to_state_dict(), "execution_budget": budget},
            node="patch_generation",
            event_type="patch_proposed",
            allowed={"patch_proposal", "execution_budget"},
            payload={"patch_id": proposal.patch_id, "changed_files": files},
        )

    def risk_assessment(state: GraphState) -> GraphState:
        workspace = _workspace(state)
        proposal = PatchProposal.from_state_dict(state["patch_proposal"] or {})
        patch = runtime.artifacts.read_text(state["task_id"], state["run_id"], proposal.patch_ref)
        result = runtime.tools.execute(
            "risk-assessment",
            {"workspace_id": workspace.workspace_id, "diff": patch, "changed_files": proposal.changed_files},
            workspace=workspace,
            allowed_tools=("risk-assessment",),
            agent_id=None,
            operation_id=f"risk:{proposal.patch_id}",
            execution_budget=state["execution_budget"],
        )
        risk_ref = runtime.artifacts.put_json(state["task_id"], state["run_id"], "risk_report", result.output)
        decision = result.output["decision"]
        proposal_status = {
            "DENY": "INVALIDATED",
            "APPROVAL_REQUIRED": "WAITING_RISK_APPROVAL",
            "AUTO_ALLOWED": "APPROVED",
        }[decision]
        updates: dict[str, Any] = {
            "execution_budget": result.execution_budget,
            "patch_proposal": proposal.model_copy(update={"status": proposal_status}).to_state_dict(),
        }
        if decision == "DENY":
            updates.update({"status": TaskStatus.POLICY_REJECTED.value, "pause_reason": "POLICY_DENY"})
        elif decision == "APPROVAL_REQUIRED":
            requested = runtime.clock.now()
            approval = ApprovalRequest(
                approval_id=f"approval_{uuid.uuid4().hex[:16]}",
                task_id=state["task_id"],
                run_id=state["run_id"],
                patch_ref=proposal.patch_ref,
                patch_hash=proposal.patch_hash,
                base_revision=proposal.base_revision,
                risk_report_ref=risk_ref.to_state_dict(),
                requested_at=requested.isoformat(),
                expires_at=(requested + timedelta(seconds=runtime.approval_ttl_seconds)).isoformat(),
            )
            updates.update(
                {
                    "pending_approval": approval.to_state_dict(),
                    "status": TaskStatus.WAITING_RISK_APPROVAL.value,
                    "pause_reason": "RISK_APPROVAL",
                }
            )
        return _merge_transition(
            runtime,
            state,
            updates,
            node="risk_assessment",
            event_type="risk_assessed",
            allowed={"execution_budget", "patch_proposal", "pending_approval", "status", "pause_reason"},
            payload={"decision": decision, "risk_report_ref": risk_ref.to_state_dict()},
        )

    def approval_gate(state: GraphState) -> GraphState:
        approval = ApprovalRequest.from_state_dict(state["pending_approval"] or {})
        proposal = PatchProposal.from_state_dict(state["patch_proposal"] or {})
        decision = interrupt(approval.to_state_dict())
        now = runtime.clock.now()
        if now >= datetime.fromisoformat(approval.expires_at):
            return _merge_transition(
                runtime,
                state,
                {
                    "status": TaskStatus.CANCELLED.value,
                    "pause_reason": "APPROVAL_EXPIRED",
                    "pending_approval": None,
                    "patch_proposal": proposal.model_copy(update={"status": "INVALIDATED"}).to_state_dict(),
                },
                node="approval_gate",
                event_type="approval_expired",
                allowed={"status", "pause_reason", "pending_approval", "patch_proposal"},
            )
        required = {
            "approval_id": approval.approval_id,
            "patch_hash": approval.patch_hash,
            "base_revision": approval.base_revision,
        }
        if any(decision.get(key) != value for key, value in required.items()):
            raise ValueError("approval object does not match pending patch")
        if decision.get("decision") != "APPROVE":
            return _merge_transition(
                runtime,
                state,
                {
                    "status": TaskStatus.CANCELLED.value,
                    "pause_reason": "APPROVAL_REJECTED",
                    "pending_approval": None,
                    "patch_proposal": proposal.model_copy(update={"status": "INVALIDATED"}).to_state_dict(),
                },
                node="approval_gate",
                event_type="approval_rejected",
                allowed={"status", "pause_reason", "pending_approval", "patch_proposal"},
            )
        return _merge_transition(
            runtime,
            state,
            {
                "status": TaskStatus.RUNNING.value,
                "pause_reason": None,
                "pending_approval": None,
                "patch_proposal": proposal.model_copy(update={"status": "APPROVED"}).to_state_dict(),
            },
            node="approval_gate",
            event_type="approval_granted",
            allowed={"status", "pause_reason", "pending_approval", "patch_proposal"},
            payload={"decided_by": decision.get("decided_by", "cli")},
        )

    def apply_patch(state: GraphState) -> GraphState:
        workspace = _workspace(state)
        runtime.workspace_manager.validate_lease(workspace, state["run_id"])
        proposal = PatchProposal.from_state_dict(state["patch_proposal"] or {})
        if workspace.current_revision != proposal.base_revision:
            raise ValueError("patch base revision does not match workspace")
        recovery = RecoveryPoint(
            recovery_point_id=f"recovery_{uuid.uuid4().hex[:16]}",
            checkpoint_id=f"{state['run_id']}:{state['state_revision']}",
            workspace_id=workspace.workspace_id,
            repository_snapshot_id=workspace.current_revision,
            plan_id=(state["active_plan_ref"] or {}).get("artifact_id", "unknown"),
            plan_version=1,
            state_revision=state["state_revision"],
            created_at=runtime.clock.now().isoformat(),
        )
        recovery_ref = runtime.artifacts.put_json(state["task_id"], state["run_id"], "recovery_point", recovery.to_state_dict())
        patch = runtime.artifacts.read_text(state["task_id"], state["run_id"], proposal.patch_ref)
        updated_workspace = runtime.workspace_manager.apply_patch(workspace, patch, proposal.patch_hash)
        return _merge_transition(
            runtime,
            state,
            {
                "workspace_ref": updated_workspace.to_state_dict(),
                "active_recovery_point_ref": recovery_ref.sha256,
                "patch_proposal": proposal.model_copy(update={"status": "APPLIED"}).to_state_dict(),
            },
            node="apply_patch",
            event_type="patch_applied",
            allowed={"workspace_ref", "active_recovery_point_ref", "patch_proposal"},
            payload={"revision": updated_workspace.current_revision},
        )

    def run_verification(state: GraphState) -> GraphState:
        workspace = _workspace(state)
        runtime.workspace_manager.validate_lease(workspace, state["run_id"])
        runtime.workspace_manager.validate_revision(workspace)
        result = runtime.tools.execute(
            "test-execution",
            {"workspace_id": workspace.workspace_id, "command": runtime.verification_command, "timeout": 120},
            workspace=workspace,
            allowed_tools=("test-execution",),
            agent_id=None,
            operation_id=f"verification:{state['run_id']}:{state['state_revision']}",
            execution_budget=state["execution_budget"],
        )
        report_ref = runtime.artifacts.put_json(state["task_id"], state["run_id"], "verification_report", result.output)
        value = {**result.output, "report_ref": report_ref.to_state_dict()}
        return _merge_transition(
            runtime,
            state,
            {"verification": value, "execution_budget": result.execution_budget},
            node="run_verification",
            event_type="verification_executed",
            allowed={"verification", "execution_budget"},
            payload={"exit_code": result.output.get("exit_code")},
        )

    def parse_verification(state: GraphState) -> GraphState:
        verification = state["verification"] or {}
        passed = verification.get("exit_code") == 0 and verification.get("passed") is True
        if passed:
            proposal = PatchProposal.from_state_dict(state["patch_proposal"] or {})
            return _merge_transition(
                runtime,
                state,
                {
                    "latest_failure": None,
                    "patch_proposal": proposal.model_copy(update={"status": "VERIFIED"}).to_state_dict(),
                },
                node="parse_verification",
                event_type="verification_passed",
                allowed={"latest_failure", "patch_proposal"},
            )
        symptom = hashlib.sha256(
            json.dumps(
                {
                    "exit_code": verification.get("exit_code"),
                    "stderr": str(verification.get("stderr", ""))[-500:],
                    "files": (state["patch_proposal"] or {}).get("changed_files", []),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        proposal = state["patch_proposal"] or {}
        failure = FailureRecord(
            failure_id=f"failure_{uuid.uuid4().hex[:16]}",
            iteration=ExecutionBudget.from_state_dict(state["execution_budget"]).iterations_used,
            category="VERIFICATION",
            error_code="TEST_FAILED",
            summary="verification exited unsuccessfully",
            symptom_fingerprint=symptom,
            change_fingerprint=hashlib.sha256(
                f"{proposal.get('patch_hash')}:{proposal.get('base_revision')}".encode()
            ).hexdigest(),
            retry_policy="NEVER",
            recovery_action="REDIAGNOSE",
            agent_actionable=True,
            related_files=proposal.get("changed_files", []),
            artifact_refs=[verification.get("report_ref", {}).get("artifact_id", "")],
            occurred_at=runtime.clock.now().isoformat(),
        )
        return _merge_transition(
            runtime,
            state,
            {"latest_failure": failure.to_state_dict()},
            node="parse_verification",
            event_type="verification_failed",
            allowed={"latest_failure"},
            payload={"failure_id": failure.failure_id},
        )

    def evaluate_progress(state: GraphState) -> GraphState:
        failure = state["latest_failure"] or {}
        entries = state["progress_window"].get("entries", [])
        symptom = failure.get("symptom_fingerprint")
        change = failure.get("change_fingerprint")
        signals = evaluate_progress_signals(
            entries, {"symptom_fingerprint": symptom, "change_fingerprint": change}
        )
        made_progress = signals.made_progress
        window = replace_progress_window(
            state,
            {"symptom_fingerprint": symptom, "change_fingerprint": change},
            made_progress,
        )
        return _merge_transition(
            runtime,
            state,
            {"progress_window": window},
            node="evaluate_progress",
            event_type="progress_evaluated",
            allowed={"progress_window"},
            payload={
                "made_progress": made_progress,
                "same_symptom": signals.same_symptom,
                "repeated_change": signals.repeated_change,
                "symptom_aba": signals.symptom_aba,
                "change_aba": signals.change_aba,
            },
        )

    def failure_router(state: GraphState) -> GraphState:
        budget = ExecutionBudget.from_state_dict(state["execution_budget"])
        entries = state["progress_window"].get("entries", [])
        symptom_aba = len(entries) >= 3 and entries[-1].get("symptom_fingerprint") == entries[-3].get("symptom_fingerprint")
        change_aba = len(entries) >= 3 and entries[-1].get("change_fingerprint") == entries[-3].get("change_fingerprint")
        stopped = (
            state["progress_window"].get("no_progress_rounds", 0) >= 2
            or symptom_aba
            or change_aba
            or budget.iterations_used >= budget.max_iterations
        )
        if stopped:
            workspace = _workspace(state)
            proposal = PatchProposal.from_state_dict(state["patch_proposal"] or {})
            if state["active_recovery_point_ref"] and budget.rollbacks_used < budget.max_rollbacks:
                runtime.workspace_manager.validate_lease(workspace, state["run_id"])
                recovery = json.loads(
                    runtime.artifacts.read_text(
                        state["task_id"], state["run_id"], {"sha256": state["active_recovery_point_ref"]}
                    )
                )
                workspace = runtime.workspace_manager.rollback(workspace, recovery["repository_snapshot_id"])
                budget = budget.model_copy(update={"rollbacks_used": budget.rollbacks_used + 1})
            return _merge_transition(
                runtime,
                state,
                {
                    "workspace_ref": workspace.to_state_dict(),
                    "execution_budget": budget.to_state_dict(),
                    "status": TaskStatus.WAITING_HUMAN_INTERVENTION.value,
                    "pause_reason": "NO_PROGRESS_OR_BUDGET",
                    "patch_proposal": proposal.model_copy(update={"status": "INVALIDATED"}).to_state_dict(),
                },
                node="failure_router",
                event_type="human_intervention_required",
                allowed={"workspace_ref", "execution_budget", "status", "pause_reason", "patch_proposal"},
            )
        budget = budget.model_copy(update={"iterations_used": budget.iterations_used + 1})
        return _merge_transition(
            runtime,
            state,
            {"execution_budget": budget.to_state_dict(), "status": TaskStatus.RUNNING.value},
            node="failure_router",
            event_type="rediagnosis_scheduled",
            allowed={"execution_budget", "status"},
        )

    def review(state: GraphState) -> GraphState:
        outcome = "NO_CHANGES" if (state["diagnosis"] or {}).get("outcome") == "NO_ACTION_REQUIRED" else "VERIFIED"
        invocation = runtime.agents.invoke(
            AGENT_SPECS["review"],
            node_context={"outcome": outcome, "diagnosis": state["diagnosis"], "verification": state["verification"]},
            output_model=OUTPUT_MODELS["ReviewSummary"],
            workspace=_workspace(state),
            execution_budget=state["execution_budget"],
            model_profile=runtime.model_profile,
            pricing_catalog=runtime.pricing_catalog,
        )
        if invocation.result.status != "ok":
            _raise_agent_error(invocation)
        status = TaskStatus.COMPLETED_NO_CHANGES.value if outcome == "NO_CHANGES" else TaskStatus.COMPLETED.value
        return _merge_transition(
            runtime,
            state,
            {
                "review": invocation.result.structured_output,
                "execution_budget": invocation.execution_budget,
                "status": status,
                "pause_reason": None,
            },
            node="review",
            event_type="task_completed",
            allowed={"review", "execution_budget", "status", "pause_reason"},
            payload={"agent_summary": invocation.result.summary, "status": status},
        )

    builder.add_node("workspace_setup", workspace_setup)
    builder.add_node("baseline_context", baseline_context)
    builder.add_node("planning", planning)
    builder.add_node("diagnosis", diagnosis)
    builder.add_node("patch_generation", patch_generation)
    builder.add_node("risk_assessment", risk_assessment)
    builder.add_node("approval_gate", approval_gate)
    builder.add_node("apply_patch", apply_patch)
    builder.add_node("run_verification", run_verification)
    builder.add_node("parse_verification", parse_verification)
    builder.add_node("evaluate_progress", evaluate_progress)
    builder.add_node("failure_router", failure_router)
    builder.add_node("review", review)

    builder.add_edge(START, "workspace_setup")
    builder.add_edge("workspace_setup", "baseline_context")
    builder.add_edge("baseline_context", "planning")
    builder.add_edge("planning", "diagnosis")
    builder.add_conditional_edges(
        "diagnosis",
        lambda s: (
            "failed_no_action"
            if s["latest_failure"] is not None
            and (s["diagnosis"] or {}).get("outcome") == "NO_ACTION_REQUIRED"
            else "review"
            if (s["diagnosis"] or {}).get("outcome") == "NO_ACTION_REQUIRED"
            else "patch_generation"
        ),
        {
            "failed_no_action": "failure_router",
            "review": "review",
            "patch_generation": "patch_generation",
        },
    )
    builder.add_edge("patch_generation", "risk_assessment")
    builder.add_conditional_edges(
        "risk_assessment",
        lambda s: "end" if s["status"] == TaskStatus.POLICY_REJECTED.value else (
            "approval" if s["status"] == TaskStatus.WAITING_RISK_APPROVAL.value else "apply"
        ),
        {"end": END, "approval": "approval_gate", "apply": "apply_patch"},
    )
    builder.add_conditional_edges(
        "approval_gate",
        lambda s: "apply" if s["status"] == TaskStatus.RUNNING.value else "end",
        {"apply": "apply_patch", "end": END},
    )
    builder.add_edge("apply_patch", "run_verification")
    builder.add_edge("run_verification", "parse_verification")
    builder.add_conditional_edges(
        "parse_verification",
        lambda s: "review" if s["latest_failure"] is None else "evaluate",
        {"review": "review", "evaluate": "evaluate_progress"},
    )
    builder.add_edge("evaluate_progress", "failure_router")
    builder.add_conditional_edges(
        "failure_router",
        lambda s: "diagnosis" if s["status"] == TaskStatus.RUNNING.value else "end",
        {"diagnosis": "diagnosis", "end": END},
    )
    builder.add_edge("review", END)
    return builder.compile(checkpointer=checkpointer)
