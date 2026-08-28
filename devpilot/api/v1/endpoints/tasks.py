from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from devpilot.api.core.dependencies import AuthenticatedPrincipal, ControlPlaneDependency
from devpilot.api.schemas import CreateTaskRequest, ERROR_RESPONSES, TaskListResponse, TaskStateResponse
from devpilot.domain.models import TaskStatus


router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post(
    "",
    response_model=TaskStateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create and start an isolated task",
    description=(
        "Validates the source repository, creates an isolated worktree, freezes the selected "
        "model in a pricing snapshot, and runs until the graph reaches a terminal or human gate."
    ),
    response_description="Current confirmed TaskState",
    responses=ERROR_RESPONSES,
    operation_id="createTask",
)
async def create_task(
    body: CreateTaskRequest,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
) -> dict[str, Any]:
    return await control.create_task(body, principal)


@router.get(
    "",
    response_model=TaskListResponse,
    summary="List visible tasks",
    description="Returns only tasks owned by the authenticated subject unless it is an administrator.",
    responses={401: ERROR_RESPONSES[401], 422: ERROR_RESPONSES[422]},
    operation_id="listTasks",
)
async def list_tasks(
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(description="Cursor returned by the previous page")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    return await control.list_tasks(
        principal, task_status=task_status, cursor=cursor, limit=limit
    )


@router.get(
    "/{task_id}",
    response_model=TaskStateResponse,
    summary="Get current task state",
    description="Returns the confirmed control state plus the task-specific frozen model profile.",
    responses=ERROR_RESPONSES,
    operation_id="getTask",
)
async def get_task(
    task_id: str,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
) -> dict[str, Any]:
    return await control.task_view(task_id, principal)
