from __future__ import annotations

import copy
from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from devpilot.domain.models import ExecutionBudget, TaskStatus


CURRENT_SCHEMA_VERSION = 1
PROGRESS_WINDOW_LIMIT = 6


class GraphState(TypedDict):
    schema_version: int
    state_revision: int
    task_id: str
    run_id: str
    parent_run_id: str | None
    status: str
    pause_reason: str | None
    current_node: str
    workspace_ref: dict[str, Any] | None
    baseline_context_ref: dict[str, Any] | None
    context_delta_ref: dict[str, Any] | None
    active_plan_ref: dict[str, Any] | None
    diagnosis: dict[str, Any] | None
    patch_proposal: dict[str, Any] | None
    verification: dict[str, Any] | None
    review: dict[str, Any] | None
    execution_budget: dict[str, Any]
    progress_window: dict[str, Any]
    pending_approval: dict[str, Any] | None
    pending_replan_request: dict[str, Any] | None
    latest_failure: dict[str, Any] | None
    active_recovery_point_ref: str | None


class GraphStateSchema(BaseModel):
    """Validation boundary only; LangGraph never stores this model instance."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int
    state_revision: int = Field(ge=0)
    task_id: str
    run_id: str
    parent_run_id: str | None
    status: str
    pause_reason: str | None
    current_node: str
    workspace_ref: dict[str, Any] | None
    baseline_context_ref: dict[str, Any] | None
    context_delta_ref: dict[str, Any] | None
    active_plan_ref: dict[str, Any] | None
    diagnosis: dict[str, Any] | None
    patch_proposal: dict[str, Any] | None
    verification: dict[str, Any] | None
    review: dict[str, Any] | None
    execution_budget: dict[str, Any]
    progress_window: dict[str, Any]
    pending_approval: dict[str, Any] | None
    pending_replan_request: dict[str, Any] | None
    latest_failure: dict[str, Any] | None
    active_recovery_point_ref: str | None

    def to_state_dict(self) -> GraphState:
        return self.model_dump(mode="json")  # type: ignore[return-value]


def create_initial_state(
    task_id: str,
    run_id: str,
    *,
    parent_run_id: str | None = None,
    budget: ExecutionBudget | None = None,
) -> GraphState:
    state: GraphState = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "state_revision": 0,
        "task_id": task_id,
        "run_id": run_id,
        "parent_run_id": parent_run_id,
        "status": TaskStatus.CREATED.value,
        "pause_reason": None,
        "current_node": "workspace_setup",
        "workspace_ref": None,
        "baseline_context_ref": None,
        "context_delta_ref": None,
        "active_plan_ref": None,
        "diagnosis": None,
        "patch_proposal": None,
        "verification": None,
        "review": None,
        "execution_budget": (budget or ExecutionBudget()).to_state_dict(),
        "progress_window": {"entries": [], "no_progress_rounds": 0},
        "pending_approval": None,
        "pending_replan_request": None,
        "latest_failure": None,
        "active_recovery_point_ref": None,
    }
    return validate_state(state)


def validate_state(state: dict[str, Any]) -> GraphState:
    if state.get("schema_version") != CURRENT_SCHEMA_VERSION:
        raise ValueError(f"unsupported GraphState schema_version: {state.get('schema_version')}")
    validated = GraphStateSchema.model_validate(state).to_state_dict()
    entries = validated["progress_window"].get("entries", [])
    if len(entries) > PROGRESS_WINDOW_LIMIT:
        raise ValueError(f"progress window exceeds {PROGRESS_WINDOW_LIMIT}")
    return copy.deepcopy(validated)


def replace_progress_window(state: GraphState, entry: dict[str, Any], made_progress: bool) -> dict[str, Any]:
    old = state["progress_window"]
    entries = [*old.get("entries", []), copy.deepcopy(entry)][-PROGRESS_WINDOW_LIMIT:]
    return {
        "entries": entries,
        "no_progress_rounds": 0 if made_progress else int(old.get("no_progress_rounds", 0)) + 1,
    }


def migrate_state(raw: dict[str, Any]) -> GraphState:
    version = raw.get("schema_version")
    if version == CURRENT_SCHEMA_VERSION:
        return validate_state(raw)
    raise ValueError(f"no safe migration for GraphState schema_version: {version}")
