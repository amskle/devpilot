from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from devpilot.domain.models import StrictModel


EVENT_SCHEMA_VERSION = 1


class ExecutionEvent(StrictModel):
    """Versioned event envelope stored before any real-time delivery."""

    event_id: str
    schema_version: Literal[1] = EVENT_SCHEMA_VERSION
    task_id: str
    run_id: str
    node_name: str | None = None
    attempt: int | None = Field(default=None, ge=0)
    event_type: str
    sequence_number: int = Field(ge=1)
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[dict[str, Any] | str] = Field(default_factory=list)
    checkpoint_confirmed: bool = False
    created_at: str

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        if not value or any(character.isspace() for character in value):
            raise ValueError("event_type must be a non-empty token")
        return value


class OutboxEntry(StrictModel):
    outbox_id: str
    event_id: str
    task_id: str
    run_id: str
    sequence_number: int = Field(ge=1)
    status: Literal["PENDING", "PROCESSING", "PUBLISHED", "DISCARDED"]
    attempts: int = Field(ge=0)
    available_at: str
    claimed_by: str | None = None
    claimed_at: str | None = None
    published_at: str | None = None
    stream_id: str | None = None
    last_error: str | None = None
    created_at: str


class TraceView(StrictModel):
    task_id: str
    run_id: str | None = None
    event_count: int = Field(ge=0)
    first_sequence: int | None = None
    last_sequence: int | None = None
    sequence_gaps: list[int] = Field(default_factory=list)
    events: list[ExecutionEvent] = Field(default_factory=list)
