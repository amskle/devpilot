from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from devpilot.api.core.dependencies import AuthenticatedPrincipal, ControlPlaneDependency
from devpilot.api.schemas import (
    DiffResponse,
    ERROR_RESPONSES,
    PlanDocumentResponse,
    RecoveryPoint,
    TraceView,
)


router = APIRouter(prefix="/tasks/{task_id}", tags=["evidence"])


@router.get(
    "/plan",
    response_model=list[PlanDocumentResponse],
    summary="Get immutable Plan history",
    responses=ERROR_RESPONSES,
    operation_id="getTaskPlan",
)
async def get_plan(
    task_id: str,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
) -> list[dict[str, Any]]:
    return await control.plans(task_id, principal)


@router.get(
    "/diff",
    response_model=DiffResponse,
    summary="Get the current Patch diff",
    description="Returns text only; the Patch is never applied by reading this endpoint.",
    responses=ERROR_RESPONSES,
    operation_id="getTaskDiff",
)
async def get_diff(
    task_id: str,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
) -> dict[str, Any]:
    return await control.diff(task_id, principal)


@router.get(
    "/trace",
    response_model=TraceView,
    summary="Get the durable execution trace",
    responses=ERROR_RESPONSES,
    operation_id="getTaskTrace",
)
async def get_trace(
    task_id: str,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
    run_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    return await control.trace(task_id, principal, run_id)


@router.get(
    "/recovery-points",
    response_model=list[RecoveryPoint],
    tags=["recovery"],
    summary="List valid recovery points",
    responses=ERROR_RESPONSES,
    operation_id="getTaskRecoveryPoints",
)
async def get_recovery_points(
    task_id: str,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
) -> list[dict[str, Any]]:
    return await control.recovery_points(task_id, principal)
