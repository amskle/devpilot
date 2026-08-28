from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from devpilot.api.schemas.common import ApiModel


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
