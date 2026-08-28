from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from devpilot.domain.models import StrictModel, TaskStatus


class ReplayIssue(StrictModel):
    severity: Literal["WARNING", "ERROR"]
    code: str
    detail: str
    sequence_number: int | None = Field(default=None, ge=1)


class EventReplayResult(StrictModel):
    replay_id: str
    replay_type: Literal["EVENT"] = "EVENT"
    task_id: str
    run_id: str
    through_sequence: int
    event_count: int = Field(ge=0)
    first_sequence: int | None = None
    last_sequence: int | None = None
    last_state_revision: int | None = None
    event_type_counts: dict[str, int] = Field(default_factory=dict)
    source_digest: str
    integrity_ok: bool
    issues: list[ReplayIssue] = Field(default_factory=list)
    created_at: str


class StateReplayResult(StrictModel):
    replay_id: str
    replay_type: Literal["STATE"] = "STATE"
    task_id: str
    run_id: str
    checkpoint_id: str
    parent_checkpoint_id: str | None = None
    checkpoint_count: int = Field(ge=1)
    state_revision: int = Field(ge=0)
    state_digest: str
    event_digest: str
    consistent: bool
    issues: list[ReplayIssue] = Field(default_factory=list)
    state: dict[str, Any]
    created_at: str


class RecoveryForkResult(StrictModel):
    fork_id: str
    source_task_id: str
    source_run_id: str
    recovery_point_id: str
    repository_snapshot_id: str
    target_task_id: str
    target_run_id: str
    model: str
    created_at: str


class EvaluationExpectation(StrictModel):
    statuses: list[TaskStatus] = Field(min_length=1)
    changed_files: list[str] | None = None
    verification_passed: bool | None = None
    requires_approval: bool | None = None

    @field_validator("changed_files")
    @classmethod
    def normalize_files(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.replace("\\", "/").strip("/") for item in value]
        if any(not item for item in normalized):
            raise ValueError("expected changed file paths must not be empty")
        if len(set(normalized)) != len(normalized):
            raise ValueError("expected changed file paths must be unique")
        return normalized


class EvaluationCase(StrictModel):
    case_id: str = Field(min_length=1)
    repo: str = Field(min_length=1)
    request: str = Field(min_length=1, max_length=20_000)
    revision: str = "HEAD"
    expectation: EvaluationExpectation


class EvaluationDataset(StrictModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_ids(self) -> "EvaluationDataset":
        identifiers = [case.case_id for case in self.cases]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("evaluation case_id values must be unique")
        return self


class EvaluationCaseResult(StrictModel):
    case_id: str
    task_id: str | None = None
    run_id: str | None = None
    actual_status: str | None = None
    status_match: bool = False
    changed_files_precision: float | None = Field(default=None, ge=0, le=1)
    changed_files_recall: float | None = Field(default=None, ge=0, le=1)
    changed_files_f1: float | None = Field(default=None, ge=0, le=1)
    verification_match: bool | None = None
    approval_match: bool | None = None
    score: float = Field(ge=0, le=1)
    duration_seconds: float = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    cost: str
    error: str | None = None


class EvaluationMetrics(StrictModel):
    case_count: int = Field(ge=1)
    completed_cases: int = Field(ge=0)
    errored_cases: int = Field(ge=0)
    average_score: float = Field(ge=0, le=1)
    status_accuracy: float = Field(ge=0, le=1)
    verification_accuracy: float | None = Field(default=None, ge=0, le=1)
    approval_accuracy: float | None = Field(default=None, ge=0, le=1)
    changed_files_f1: float | None = Field(default=None, ge=0, le=1)
    total_prompt_tokens: int = Field(ge=0)
    total_completion_tokens: int = Field(ge=0)
    total_cost: str


class EvaluationReport(StrictModel):
    evaluation_id: str
    dataset_name: str
    dataset_version: str
    dataset_digest: str
    model: str
    prompt_version: str
    prompt_digest: str
    dataset: EvaluationDataset
    metrics: EvaluationMetrics
    cases: list[EvaluationCaseResult]
    created_at: str


class EvaluationComparison(StrictModel):
    baseline_evaluation_id: str
    candidate_evaluation_id: str
    metric_deltas: dict[str, float]
    winner: Literal["BASELINE", "CANDIDATE", "TIE"]
