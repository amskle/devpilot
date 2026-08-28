from __future__ import annotations

from typing import Any

from pydantic import Field

from devpilot.api.schemas.common import ApiModel
from devpilot.domain.models import ExecutionBudget, TaskStatus


class CreateTaskRequest(ApiModel):
    repo: str = Field(
        min_length=1,
        max_length=4096,
        description="Absolute path to an allowed, clean local Git repository",
        examples=[r"C:\work\sample-repository"],
    )
    request: str = Field(
        min_length=1,
        max_length=20_000,
        description="Software engineering task requested from DevPilot",
        examples=["修复失败测试，并保持公开 API 兼容。"],
    )
    revision: str = Field(
        default="HEAD",
        min_length=1,
        max_length=255,
        description="Git revision used to create the isolated worktree",
        examples=["HEAD"],
    )
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Optional per-task model; the selected value is frozen in the pricing snapshot",
        examples=["gpt-5-mini"],
    )


class ModelProfileResponse(ApiModel):
    provider: str
    model: str
    base_url: str | None = None
    context_window: int = 128_000
    max_prompt_tokens: int = 32_000
    max_completion_tokens: int = 4_096


class TaskStateResponse(ApiModel):
    schema_version: int
    state_revision: int = Field(ge=0)
    task_id: str
    run_id: str
    parent_run_id: str | None
    status: TaskStatus
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
    execution_budget: ExecutionBudget
    progress_window: dict[str, Any]
    pending_approval: dict[str, Any] | None
    pending_replan_request: dict[str, Any] | None
    latest_failure: dict[str, Any] | None
    active_recovery_point_ref: str | None
    model_profile: ModelProfileResponse
    request: str
    updated_at: str


class TaskSummaryResponse(ApiModel):
    task_id: str
    run_id: str
    status: TaskStatus
    current_node: str
    state_revision: int = Field(ge=0)
    pause_reason: str | None
    request: str
    model: str
    updated_at: str
    execution_budget: ExecutionBudget
    verification: dict[str, Any] | None


class TaskListResponse(ApiModel):
    items: list[TaskSummaryResponse]
    next_cursor: str | None = Field(
        default=None, description="Opaque cursor for the next page"
    )
