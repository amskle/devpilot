from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from devpilot.api.core.dependencies import (
    AuthenticatedPrincipal,
    ControlPlaneDependency,
    IdempotencyKey,
)
from devpilot.api.schemas import (
    ApprovalDecisionRequest,
    ChangeRequestBody,
    ControlTargetRequest,
    ERROR_RESPONSES,
    RecoveryControlRequest,
    TaskStateResponse,
)


router = APIRouter(prefix="/tasks/{task_id}")


@router.post(
    "/approve",
    response_model=TaskStateResponse,
    tags=["human control"],
    summary="Approve the exact pending Patch",
    description="Binds approval ID, Patch hash, base revision, state revision, and authenticated decider.",
    responses=ERROR_RESPONSES,
    operation_id="approveTaskPatch",
)
async def approve(
    task_id: str,
    body: ApprovalDecisionRequest,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    return await control.decide_approval(
        task_id, body, principal, idempotency_key, "APPROVE"
    )


@router.post(
    "/reject",
    response_model=TaskStateResponse,
    tags=["human control"],
    summary="Reject the exact pending Patch",
    responses=ERROR_RESPONSES,
    operation_id="rejectTaskPatch",
)
async def reject(
    task_id: str,
    body: ApprovalDecisionRequest,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    return await control.decide_approval(
        task_id, body, principal, idempotency_key, "REJECT"
    )


@router.post(
    "/cancel",
    response_model=TaskStateResponse,
    tags=["human control"],
    summary="Cancel a non-terminal task",
    responses=ERROR_RESPONSES,
    operation_id="cancelTask",
)
async def cancel(
    task_id: str,
    body: ControlTargetRequest,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    return await control.cancel(task_id, body, principal, idempotency_key)


@router.post(
    "/rollback",
    response_model=TaskStateResponse,
    tags=["recovery"],
    summary="Apply a compensating rollback",
    description="Restores repository content while preserving the current run and consuming rollback budget.",
    responses=ERROR_RESPONSES,
    operation_id="rollbackTask",
)
async def rollback(
    task_id: str,
    body: RecoveryControlRequest,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    return await control.recover(task_id, body, principal, idempotency_key, "rollback")


@router.post(
    "/restore",
    response_model=TaskStateResponse,
    tags=["recovery"],
    summary="Fork a new run from a recovery point",
    description="Validates the caller's revision, creates a new run_id, and resets the event cursor boundary.",
    responses=ERROR_RESPONSES,
    operation_id="restoreTask",
)
async def restore(
    task_id: str,
    body: RecoveryControlRequest,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    return await control.recover(task_id, body, principal, idempotency_key, "restore")


@router.post(
    "/change-requests",
    response_model=TaskStateResponse,
    tags=["human control"],
    summary="Submit a formal requirement change",
    description=(
        "Creates an immutable ChangeRequest and linked ReplanRequest. If approval is pending, "
        "explicit patch invalidation confirmation is mandatory and both objects are invalidated atomically."
    ),
    responses=ERROR_RESPONSES,
    operation_id="createTaskChangeRequest",
)
async def change_request(
    task_id: str,
    body: ChangeRequestBody,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    return await control.change_request(task_id, body, principal, idempotency_key)
