from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status

from devpilot.api.core.dependencies import (
    AuthenticatedPrincipal,
    ControlPlaneDependency,
    IdempotencyKey,
)
from devpilot.api.schemas import ERROR_RESPONSES, MessageCreateRequest, MessageResponse


router = APIRouter(prefix="/tasks/{task_id}/messages", tags=["conversation"])


@router.get(
    "",
    response_model=list[MessageResponse],
    summary="List user messages and auditable Agent summaries",
    responses=ERROR_RESPONSES,
    operation_id="getTaskMessages",
)
async def get_messages(
    task_id: str,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
) -> list[dict[str, Any]]:
    return await control.messages(task_id, principal)


@router.post(
    "",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Append a non-control conversation message",
    description=(
        "Persists a redacted event without changing GraphState. Text such as ‘approve’ or "
        "‘rollback’ is never interpreted as a command."
    ),
    responses=ERROR_RESPONSES,
    operation_id="createTaskMessage",
)
async def create_message(
    task_id: str,
    body: MessageCreateRequest,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    return await control.add_message(task_id, body, principal, idempotency_key)
