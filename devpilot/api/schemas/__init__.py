"""Pydantic request and response contracts grouped by API concern."""

from devpilot.api.schemas.common import (
    ERROR_RESPONSES,
    ApiModel,
    HealthResponse,
    MessageResponse,
    ProblemDetails,
    ReadinessResponse,
)
from devpilot.api.schemas.controls import (
    ApprovalDecisionRequest,
    ChangeRequestBody,
    ControlTargetRequest,
    MessageCreateRequest,
    RecoveryControlRequest,
)
from devpilot.api.schemas.evidence import DiffResponse, EventTicketResponse, PlanDocumentResponse
from devpilot.api.schemas.tasks import (
    CreateTaskRequest,
    ModelProfileResponse,
    TaskListResponse,
    TaskStateResponse,
    TaskSummaryResponse,
)
from devpilot.domain.models import RecoveryPoint
from devpilot.events.models import ExecutionEvent, TraceView

__all__ = [
    "ApiModel",
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
    "ModelProfileResponse",
    "PlanDocumentResponse",
    "ProblemDetails",
    "ReadinessResponse",
    "RecoveryControlRequest",
    "RecoveryPoint",
    "TaskListResponse",
    "TaskStateResponse",
    "TaskSummaryResponse",
    "TraceView",
]
