from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from devpilot.domain.models import ExecutionBudget, RecoveryPoint, TaskStatus
from devpilot.events.models import ExecutionEvent, TraceView


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProblemDetails(ApiModel):
    code: str = Field(description="Stable machine-readable error code")
    detail: str = Field(description="Human-readable error description")
    request_id: str | None = Field(default=None, description="Request correlation identifier")


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


class ControlTargetRequest(ApiModel):
    expected_state_revision: int = Field(
        ge=0,
        description="Revision last observed by the caller; stale revisions return HTTP 409",
        examples=[12],
    )


class ApprovalDecisionRequest(ControlTargetRequest):
    approval_id: str = Field(examples=["approval_4f279acbcdab4d6a"])
    patch_hash: str = Field(
        min_length=64, max_length=64, examples=["a" * 64]
    )
    base_revision: str = Field(examples=["58b087af6b48"])


class RecoveryControlRequest(ControlTargetRequest):
    recovery_point_id: str = Field(examples=["recovery_cdfda6e4f34041a8"])


class ChangeRequestBody(ControlTargetRequest):
    content: str = Field(
        min_length=1,
        max_length=20_000,
        description="Replacement or additional requirement that requires a new Plan version",
        examples=["保留旧配置格式，并为迁移路径增加回归测试。"],
    )
    confirm_patch_invalidation: bool = Field(
        description="Must be true when a risk approval is pending"
    )


class MessageCreateRequest(ApiModel):
    content: str = Field(
        min_length=1,
        max_length=20_000,
        description="Conversation text only; it never executes a control operation",
        examples=["这个错误最早出现在哪个验证阶段？"],
    )


class MessageResponse(ApiModel):
    message_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: str


class PlanDocumentResponse(ApiModel):
    model_config = ConfigDict(extra="allow")

    plan_id: str
    version: int = Field(ge=1)
    parent_version: int | None = None
    status: Literal["ACTIVE", "SUPERSEDED"]
    created_at: str
    change_reason: str | None = None
    summary: str
    tasks: list[dict[str, Any]]
    acceptance_criteria: list[str]
    risks: list[str]
    content_hash: str


class DiffResponse(ApiModel):
    patch_id: str | None = None
    text: str
    changed_files: list[str]
    patch_hash: str | None = None


class EventTicketResponse(ApiModel):
    ticket: str = Field(description="Single-use opaque WebSocket ticket")
    expires_at: str


class HealthResponse(ApiModel):
    status: Literal["ok"] = "ok"
    service: Literal["devpilot-api"] = "devpilot-api"


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetails, "description": "Missing or invalid bearer token"},
    403: {"model": ProblemDetails, "description": "Safety policy denied the operation"},
    404: {"model": ProblemDetails, "description": "Task not found or not visible to this subject"},
    409: {"model": ProblemDetails, "description": "State revision or lifecycle conflict"},
    422: {"model": ProblemDetails, "description": "Request or domain validation failed"},
    429: {"model": ProblemDetails, "description": "Rate limit exceeded"},
}


__all__ = [
    "ApprovalDecisionRequest",
    "ChangeRequestBody",
    "ControlTargetRequest",
    "CreateTaskRequest",
    "DiffResponse",
    "ERROR_RESPONSES",
    "EventTicketResponse",
    "ExecutionEvent",
    "HealthResponse",
    "MessageCreateRequest",
    "MessageResponse",
    "PlanDocumentResponse",
    "ProblemDetails",
    "RecoveryControlRequest",
    "RecoveryPoint",
    "TaskListResponse",
    "TaskStateResponse",
    "TraceView",
]
