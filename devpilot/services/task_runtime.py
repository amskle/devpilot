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

from devpilot.agents.definitions import AGENT_SPECS
from devpilot.agents.model_gateway import LazyOpenAICompatibleGateway, ModelGateway
from devpilot.agents.runner import AgentRunner
from devpilot.clock import Clock, SystemClock
from devpilot.domain.models import (
    ApprovalRequest,
    ExecutionBudget,
    FailureRecord,
    ModelProfile,
    TaskStatus,
)
from devpilot.domain.state import GraphState, create_initial_state, validate_state
from devpilot.errors import BudgetExceededError, PolicyDeniedError, StateConflictError
from devpilot.orchestration.graph import GraphRuntime, build_graph
from devpilot.services.pricing import PricingCatalog
from devpilot.services.storage import ArtifactStore, SQLiteControlStore, default_data_dir
from devpilot.tools.executor import ToolExecutor, build_default_registry
from devpilot.workspace import WorkspaceManager


class TaskRuntimeCore:
    """Own task runtime dependencies, checkpoints, and graph invocation."""

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
        self._checkpoint_conn = sqlite3.connect(
            self.data_dir / "checkpoints.sqlite", check_same_thread=False
        )
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
            return None, self.model_name
        raw = self.artifacts.read_text(
            state["task_id"], state["run_id"], {"sha256": snapshot_ref}
        )
        catalog, selected_model = PricingCatalog.from_snapshot(json.loads(raw))
        selected_catalog = (
            catalog if state["execution_budget"].get("max_cost") is not None else None
        )
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
        prompt_overrides: dict[str, str] | None = None,
    ) -> GraphRuntime:
        selected_model = model_name or self.model_name
        selected_catalog = pricing_catalog
        if state is not None and selected_catalog is None:
            selected_catalog, selected_model = self._pricing_context(state)
        agent_specs = None
        if prompt_overrides is not None:
            unknown = set(prompt_overrides) - set(AGENT_SPECS)
            if unknown:
                raise ValueError(
                    f"unknown prompt override agents: {sorted(unknown)}"
                )
            if any(not value.strip() for value in prompt_overrides.values()):
                raise ValueError("prompt override instructions must not be empty")
            agent_specs = {
                agent_id: spec.model_copy(
                    update={
                        "instructions": prompt_overrides.get(
                            agent_id, spec.instructions
                        )
                    }
                )
                for agent_id, spec in AGENT_SPECS.items()
            }
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
            agent_specs=agent_specs,
        )

    def _request_from_state(
        self, state: GraphState, *, artifact_run_id: str | None = None
    ) -> str:
        ref = state["context_delta_ref"]
        if ref is None:
            return ""
        return self.artifacts.read_text(
            state["task_id"], artifact_run_id or state["run_id"], ref
        )

    def _invoke(
        self, graph: Any, run_id: str, value: GraphState | Command
    ) -> GraphState:
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
                    previous = graph.get_state(
                        self._config(projection["checkpoint_run_id"])
                    )
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
                iteration=ExecutionBudget.from_state_dict(
                    recovered["execution_budget"]
                ).iterations_used,
                category=category,
                error_code=error_code,
                summary=str(exc)[:1000],
                symptom_fingerprint=hashlib.sha256(
                    f"{error_code}:{type(exc).__name__}".encode()
                ).hexdigest(),
                change_fingerprint=(recovered["patch_proposal"] or {}).get(
                    "patch_hash"
                ),
                retry_policy="NEVER",
                recovery_action=(
                    "HUMAN"
                    if status == TaskStatus.WAITING_HUMAN_INTERVENTION.value
                    else "FAIL"
                ),
                agent_actionable=False,
                related_files=(recovered["patch_proposal"] or {}).get(
                    "changed_files", []
                ),
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
                        ExecutionBudget.from_state_dict(
                            failure_budget
                        ).to_state_dict()
                        if isinstance(failure_budget, dict)
                        else recovered["execution_budget"]
                    ),
                }
            )
            failed = self.control.transition(
                validate_state(failed),
                expected_revision=recovered["state_revision"],
                event_type="node_failed",
                payload={"category": category, "error_code": error_code},
            )
            graph.update_state(config, failed)
            self.control.confirm_checkpoint(
                failed["task_id"], failed["run_id"], failed["state_revision"]
            )
            return failed
        snapshot = graph.get_state(config)
        state = validate_state(dict(snapshot.values))
        self.control.confirm_checkpoint(
            state["task_id"], state["run_id"], state["state_revision"]
        )
        return state

    def create_task(
        self,
        repo: Path,
        request: str,
        *,
        revision: str = "HEAD",
        budget: ExecutionBudget | None = None,
        model: str | None = None,
        parent_run_id: str | None = None,
        prompt_overrides: dict[str, str] | None = None,
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
        state = create_initial_state(
            task_id,
            run_id,
            parent_run_id=parent_run_id,
            budget=selected_budget,
        )
        request_ref = self.artifacts.put_text(task_id, run_id, "task_request", request)
        state["context_delta_ref"] = request_ref.to_state_dict()
        snapshot = catalog.snapshot(
            self.artifacts, task_id, run_id, selected_model=selected_model
        )
        state["execution_budget"]["pricing_snapshot_ref"] = snapshot["sha256"]
        state = validate_state(state)
        self.control.create_task(state)
        runtime = self._runtime(
            source_repo=repo.resolve(),
            request=request,
            revision=revision,
            pricing_catalog=(
                catalog if selected_budget.max_cost is not None else None
            ),
            model_name=selected_model,
            prompt_overrides=prompt_overrides,
        )
        graph = build_graph(runtime, self.checkpointer)
        return self._invoke(graph, run_id, state)

    def _checkpoint_state(
        self, run_id: str, runtime: GraphRuntime
    ) -> tuple[Any, GraphState]:
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
        checkpoint_run_id = projection.get(
            "checkpoint_run_id", projected["run_id"]
        )
        graph, state = self._checkpoint_state(
            checkpoint_run_id,
            self._runtime(source_repo=None, request=request, state=projected),
        )
        if check_expiry:
            state = self._expire_approval_if_needed(graph, state)
        return state

    def _expire_approval_if_needed(
        self, graph: Any, state: GraphState
    ) -> GraphState:
        if (
            state["status"] != TaskStatus.WAITING_RISK_APPROVAL.value
            or state["pending_approval"] is None
        ):
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
        graph.update_state(
            self._config(state["run_id"]), updated, as_node="approval_gate"
        )
        self.control.confirm_checkpoint(
            state["task_id"], state["run_id"], updated["state_revision"]
        )
        return updated
