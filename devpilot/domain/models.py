from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def to_state_dict(self) -> dict[str, Any]:
        """Return JSON-compatible primitives suitable for LangGraph state."""
        return self.model_dump(mode="json")

    @classmethod
    def from_state_dict(cls, value: dict[str, Any]):
        return cls.model_validate(value)


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_RISK_APPROVAL = "WAITING_RISK_APPROVAL"
    WAITING_HUMAN_INTERVENTION = "WAITING_HUMAN_INTERVENTION"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    COMPLETED_NO_CHANGES = "COMPLETED_NO_CHANGES"
    FAILED = "FAILED"
    POLICY_REJECTED = "POLICY_REJECTED"


TERMINAL_STATUSES = {
    TaskStatus.CANCELLED.value,
    TaskStatus.COMPLETED.value,
    TaskStatus.COMPLETED_NO_CHANGES.value,
    TaskStatus.FAILED.value,
    TaskStatus.POLICY_REJECTED.value,
}


class ArtifactRef(StrictModel):
    artifact_id: str
    kind: str
    sha256: str
    size: int = Field(ge=0)


class WorkspaceRef(StrictModel):
    workspace_id: str
    repository_id: str
    worktree_ref: str
    baseline_revision: str
    current_revision: str
    lease_owner: str
    lease_expires_at: str


class ExecutionBudget(StrictModel):
    max_iterations: int = 3
    max_plan_revisions: int = 2
    max_rollbacks: int = 2
    max_llm_calls: int = 20
    max_tool_calls: int = 40
    max_tool_retries: int = 8
    max_total_tokens: int = 100_000
    max_cost: str | None = None
    cost_currency: str = "USD"
    max_active_seconds: int = Field(default=1800, ge=0)
    iterations_used: int = 0
    plan_revisions_used: int = 0
    rollbacks_used: int = 0
    llm_calls_used: int = 0
    tool_calls_used: int = 0
    tool_retries_used: int = 0
    prompt_tokens_used: int = 0
    completion_tokens_used: int = 0
    cost_used: str = "0.0000"
    pricing_snapshot_ref: str | None = None
    active_seconds_used: int = Field(default=0, ge=0)

    @field_validator("max_cost", "cost_used")
    @classmethod
    def validate_decimal(cls, value: str | None) -> str | None:
        if value is not None and Decimal(value) < 0:
            raise ValueError("cost must be non-negative")
        return value


class ModelProfile(StrictModel):
    provider: Literal["openai-compatible", "fake"]
    model: str
    base_url: str | None = None
    context_window: int = 128_000
    max_prompt_tokens: int = 32_000
    max_completion_tokens: int = 4_096


class AgentSpec(StrictModel):
    agent_id: str
    role: str
    instructions: str
    allowed_tools: tuple[str, ...]
    output_schema: str
    model_profile: str
    max_tool_rounds: int = 4
    timeout_seconds: int = 120


class AgentResult(StrictModel):
    status: Literal["ok", "error"]
    structured_output: dict[str, Any]
    summary: str
    artifact_refs: list[str] = Field(default_factory=list)
    tool_call_refs: list[str] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=dict)
    error: dict[str, Any] | None = None


class PlanDraft(StrictModel):
    summary: str
    tasks: list[dict[str, Any]]
    acceptance_criteria: list[str]
    risks: list[str] = Field(default_factory=list)


class PlanDocument(StrictModel):
    """Immutable, versioned plan content stored as an artifact."""

    plan_id: str
    version: int = Field(ge=1)
    parent_version: int | None = Field(default=None, ge=1)
    created_by: str
    created_at: str
    change_reason: str | None = None
    based_on_diagnosis_revision: int | None = Field(default=None, ge=0)
    repository_snapshot_id: str
    summary: str
    tasks: list[dict[str, Any]]
    acceptance_criteria: list[str]
    risks: list[str] = Field(default_factory=list)
    content_hash: str

    @model_validator(mode="after")
    def validate_version_chain(self):
        if self.version == 1 and self.parent_version is not None:
            raise ValueError("initial Plan must not have a parent version")
        if self.version > 1 and self.parent_version != self.version - 1:
            raise ValueError("Plan parent_version must be the immediately preceding version")
        if len(self.content_hash) != 64 or any(character not in "0123456789abcdef" for character in self.content_hash):
            raise ValueError("Plan content_hash must be a lowercase SHA-256 digest")
        return self


class PlanLifecycle(StrictModel):
    plan_id: str
    version: int = Field(ge=1)
    status: Literal["ACTIVE", "SUPERSEDED"]
    activated_at: str | None = None
    superseded_at: str | None = None


class ReplanRequest(StrictModel):
    """Immutable instruction asking the Planning agent for a new plan version."""

    replan_request_id: str
    task_id: str
    run_id: str
    reason_code: str
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    source_change_request_id: str | None = None
    requested_from_plan_id: str
    requested_from_plan_version: int = Field(ge=1)
    based_on_diagnosis_revision: int | None = Field(default=None, ge=0)
    requested_at: str


class ChangeRequest(StrictModel):
    """Immutable user-authored request to revise an active task plan."""

    change_request_id: str
    task_id: str
    run_id: str
    content: str = Field(min_length=1, max_length=20_000)
    requested_by: str
    requested_at: str
    expected_state_revision: int = Field(ge=0)
    confirm_patch_invalidation: bool = False


class DiagnosisSummary(StrictModel):
    outcome: Literal["NO_ACTION_REQUIRED", "ISSUE_FOUND", "PLAN_INVALID"]
    summary: str
    issues: list[dict[str, Any]] = Field(default_factory=list)


class Replacement(StrictModel):
    old: str
    new: str
    occurrence: int = Field(default=1, ge=1)


class PatchOperation(StrictModel):
    target_file: str
    replacements: list[Replacement]
    issues: list[str] = Field(default_factory=list)


class PatchDraft(StrictModel):
    summary: str
    operations: list[PatchOperation]


class PatchProposal(StrictModel):
    patch_id: str
    patch_ref: dict[str, Any]
    patch_hash: str
    base_revision: str
    changed_files: list[str]
    summary: str
    status: str = "PROPOSED"


class ReviewSummary(StrictModel):
    summary: str
    outcome: str
    lessons: list[str] = Field(default_factory=list)


class ApprovalRequest(StrictModel):
    approval_id: str
    task_id: str
    run_id: str
    patch_ref: dict[str, Any]
    patch_hash: str
    base_revision: str
    risk_report_ref: dict[str, Any]
    requested_at: str
    expires_at: str
    decided_by: str | None = None
    decision: str | None = None


class FailureRecord(StrictModel):
    failure_id: str
    iteration: int
    category: str
    error_code: str
    summary: str
    symptom_fingerprint: str
    change_fingerprint: str | None
    retry_policy: Literal["NEVER", "IMMEDIATE", "BACKOFF"]
    recovery_action: Literal["REGENERATE_PATCH", "REDIAGNOSE", "REPLAN", "HUMAN", "FAIL"]
    agent_actionable: bool
    related_files: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    occurred_at: str


class RecoveryPoint(StrictModel):
    recovery_point_id: str
    checkpoint_id: str
    workspace_id: str
    repository_snapshot_id: str
    plan_id: str
    plan_version: int
    state_revision: int
    created_at: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
