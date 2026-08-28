from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProblemDetails(ApiModel):
    code: str = Field(description="Stable machine-readable error code")
    detail: str = Field(description="Human-readable error description")
    request_id: str | None = Field(default=None, description="Request correlation identifier")


class MessageResponse(ApiModel):
    message_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: str


class HealthResponse(ApiModel):
    status: Literal["ok"] = "ok"
    service: Literal["devpilot-api"] = "devpilot-api"


class ReadinessResponse(ApiModel):
    status: Literal["ready", "not_ready"]
    service: Literal["devpilot-api"] = "devpilot-api"
    dependencies: dict[str, Literal["ok", "disabled", "unavailable"]]


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetails, "description": "Missing or invalid bearer token"},
    403: {"model": ProblemDetails, "description": "Safety policy denied the operation"},
    404: {"model": ProblemDetails, "description": "Task not found or not visible to this subject"},
    409: {"model": ProblemDetails, "description": "State revision or lifecycle conflict"},
    422: {"model": ProblemDetails, "description": "Request or domain validation failed"},
    429: {"model": ProblemDetails, "description": "Rate limit exceeded"},
    503: {"model": ProblemDetails, "description": "Shared state dependency unavailable"},
}
