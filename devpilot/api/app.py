from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, AsyncIterator

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool

from devpilot.api.schemas import (
    ApprovalDecisionRequest,
    ChangeRequestBody,
    ControlTargetRequest,
    CreateTaskRequest,
    DiffResponse,
    ERROR_RESPONSES,
    EventTicketResponse,
    ExecutionEvent,
    HealthResponse,
    MessageCreateRequest,
    MessageResponse,
    PlanDocumentResponse,
    ProblemDetails,
    RecoveryControlRequest,
    RecoveryPoint,
    TaskListResponse,
    TaskStateResponse,
    TraceView,
)
from devpilot.api.security import (
    ApiSettings,
    EventTicketStore,
    Principal,
    RateLimiter,
    authenticate,
)
from devpilot.domain.models import TaskStatus
from devpilot.errors import BudgetExceededError, PolicyDeniedError, StateConflictError
from devpilot.service import TaskService


IDEMPOTENCY_HEADER = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
        description="Stable key reused only while the outcome of this exact command is unknown",
        examples=["18cdd3fe-e2ad-45b2-96d7-e947e25f65c8"],
    ),
]
AUTHENTICATED_PRINCIPAL = Annotated[Principal, Depends(authenticate)]


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _problem(
    request: Request,
    status_code: int,
    code: str,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ProblemDetails(
            code=code, detail=detail, request_id=_request_id(request)
        ).model_dump(mode="json"),
        headers=headers,
    )


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padding = "=" * (-len(cursor) % 4)
        value = int(base64.urlsafe_b64decode(cursor + padding).decode())
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise ValueError("invalid task cursor") from exc
    if value < 0:
        raise ValueError("invalid task cursor")
    return value


def _authorize_task(service: TaskService, principal: Principal, task_id: str) -> None:
    if service.control.get_task(task_id) is None:
        raise KeyError(task_id)
    owner = service.control.task_owner(task_id)
    if principal.is_admin or owner == principal.subject:
        return
    # Hide resource existence from unauthorized callers.
    raise KeyError(task_id)


def _bind_idempotency(
    service: TaskService,
    task_id: str,
    operation: str,
    key: str,
    payload: dict[str, Any],
) -> None:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    service.control.bind_idempotency_input(
        task_id, operation, key, hashlib.sha256(encoded).hexdigest()
    )


def create_app(
    *,
    service: TaskService | None = None,
    settings: ApiSettings | None = None,
) -> FastAPI:
    api_settings = settings or ApiSettings.from_env()
    task_service = service or TaskService()
    owns_service = service is None
    tickets = EventTicketStore(api_settings.ticket_ttl_seconds)
    limiter = RateLimiter()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_service:
            task_service.close()

    app = FastAPI(
        title="DevPilot Control API",
        summary="Human-in-the-loop control plane for DevPilot tasks",
        description=(
            "Phase 4 REST and WebSocket API. Authenticate in this page with a bearer token, "
            "then create tasks, inspect durable execution evidence, and issue revision-bound "
            "control commands. Chat messages never execute control operations."
        ),
        version="0.4.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        contact={"name": "DevPilot maintainers"},
        license_info={"name": "Repository license"},
    )
    app.state.task_service = task_service
    app.state.api_settings = api_settings
    app.state.event_tickets = tickets
    app.state.rate_limiter = limiter

    if api_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(api_settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {"msg": "invalid request"}
        return _problem(request, 422, "REQUEST_VALIDATION_FAILED", str(first["msg"]))

    @app.exception_handler(HTTPException)
    async def http_handler(request: Request, exc: HTTPException) -> JSONResponse:
        codes = {
            401: "AUTHENTICATION_REQUIRED",
            403: "FORBIDDEN",
            404: "RESOURCE_NOT_FOUND",
            429: "RATE_LIMIT_EXCEEDED",
        }
        return _problem(
            request,
            exc.status_code,
            codes.get(exc.status_code, "HTTP_ERROR"),
            str(exc.detail),
            headers=dict(exc.headers or {}),
        )

    @app.exception_handler(StateConflictError)
    async def state_conflict_handler(request: Request, exc: StateConflictError) -> JSONResponse:
        return _problem(request, 409, "STATE_CONFLICT", str(exc))

    @app.exception_handler(BudgetExceededError)
    async def budget_handler(request: Request, exc: BudgetExceededError) -> JSONResponse:
        return _problem(request, 409, "BUDGET_EXHAUSTED", str(exc))

    @app.exception_handler(PolicyDeniedError)
    async def policy_handler(request: Request, exc: PolicyDeniedError) -> JSONResponse:
        return _problem(request, 403, "POLICY_DENIED", str(exc))

    @app.exception_handler(KeyError)
    async def not_found_handler(request: Request, _: KeyError) -> JSONResponse:
        return _problem(request, 404, "RESOURCE_NOT_FOUND", "resource not found")

    @app.exception_handler(ValueError)
    async def value_handler(request: Request, exc: ValueError) -> JSONResponse:
        return _problem(request, 422, "DOMAIN_VALIDATION_FAILED", str(exc))

    router = APIRouter(prefix="/api")
    @router.get(
        "/health",
        response_model=HealthResponse,
        tags=["system"],
        summary="Check API health",
        operation_id="getApiHealth",
    )
    async def health() -> HealthResponse:
        return HealthResponse()

    @router.post(
        "/tasks",
        response_model=TaskStateResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["tasks"],
        summary="Create and start an isolated task",
        description=(
            "Validates the source repository, creates an isolated worktree, freezes the selected "
            "model in a pricing snapshot, and runs until the graph reaches a terminal or human gate."
        ),
        response_description="Current confirmed TaskState",
        responses=ERROR_RESPONSES,
        operation_id="createTask",
    )
    async def create_task(body: CreateTaskRequest, principal: AUTHENTICATED_PRINCIPAL) -> dict[str, Any]:
        limiter.check(principal.subject, "task-create", limit=10)
        state = await run_in_threadpool(
            task_service.create_task,
            Path(body.repo),
            body.request,
            revision=body.revision,
            model=body.model,
        )
        task_service.control.bind_task_owner(state["task_id"], principal.subject)
        return await run_in_threadpool(task_service.task_view, state["task_id"])

    @router.get(
        "/tasks",
        response_model=TaskListResponse,
        tags=["tasks"],
        summary="List visible tasks",
        description="Returns only tasks owned by the authenticated subject unless it is an administrator.",
        responses={401: ERROR_RESPONSES[401], 422: ERROR_RESPONSES[422]},
        operation_id="listTasks",
    )
    async def list_tasks(
        principal: AUTHENTICATED_PRINCIPAL,
        task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
        cursor: Annotated[str | None, Query(description="Cursor returned by the previous page")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> dict[str, Any]:
        offset = _decode_cursor(cursor)
        items = await run_in_threadpool(
            task_service.list_task_views,
            status=task_status.value if task_status else None,
        )
        visible = [
            item
            for item in items
            if principal.is_admin
            or task_service.control.task_owner(item["task_id"]) == principal.subject
        ]
        page = visible[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "items": page,
            "next_cursor": _encode_cursor(next_offset) if next_offset < len(visible) else None,
        }

    @router.get(
        "/tasks/{task_id}",
        response_model=TaskStateResponse,
        tags=["tasks"],
        summary="Get current task state",
        description="Returns the confirmed control state plus the task-specific frozen model profile.",
        responses=ERROR_RESPONSES,
        operation_id="getTask",
    )
    async def get_task(task_id: str, principal: AUTHENTICATED_PRINCIPAL) -> dict[str, Any]:
        _authorize_task(task_service, principal, task_id)
        return await run_in_threadpool(task_service.task_view, task_id)

    @router.get(
        "/tasks/{task_id}/plan",
        response_model=list[PlanDocumentResponse],
        tags=["evidence"],
        summary="Get immutable Plan history",
        responses=ERROR_RESPONSES,
        operation_id="getTaskPlan",
    )
    async def get_plan(task_id: str, principal: AUTHENTICATED_PRINCIPAL) -> list[dict[str, Any]]:
        _authorize_task(task_service, principal, task_id)
        return await run_in_threadpool(task_service.plan_documents, task_id)

    @router.get(
        "/tasks/{task_id}/diff",
        response_model=DiffResponse,
        tags=["evidence"],
        summary="Get the current Patch diff",
        description="Returns text only; the Patch is never applied by reading this endpoint.",
        responses=ERROR_RESPONSES,
        operation_id="getTaskDiff",
    )
    async def get_diff(task_id: str, principal: AUTHENTICATED_PRINCIPAL) -> dict[str, Any]:
        _authorize_task(task_service, principal, task_id)
        return await run_in_threadpool(task_service.diff_document, task_id)

    @router.get(
        "/tasks/{task_id}/trace",
        response_model=TraceView,
        tags=["evidence"],
        summary="Get the durable execution trace",
        responses=ERROR_RESPONSES,
        operation_id="getTaskTrace",
    )
    async def get_trace(
        task_id: str,
        principal: AUTHENTICATED_PRINCIPAL,
        run_id: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        _authorize_task(task_service, principal, task_id)
        return await run_in_threadpool(task_service.trace, task_id, run_id)

    @router.get(
        "/tasks/{task_id}/messages",
        response_model=list[MessageResponse],
        tags=["conversation"],
        summary="List user messages and auditable Agent summaries",
        responses=ERROR_RESPONSES,
        operation_id="getTaskMessages",
    )
    async def get_messages(task_id: str, principal: AUTHENTICATED_PRINCIPAL) -> list[dict[str, Any]]:
        _authorize_task(task_service, principal, task_id)
        return await run_in_threadpool(task_service.messages, task_id)

    @router.post(
        "/tasks/{task_id}/messages",
        response_model=MessageResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["conversation"],
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
        principal: AUTHENTICATED_PRINCIPAL,
        idempotency_key: IDEMPOTENCY_HEADER,
    ) -> dict[str, Any]:
        _authorize_task(task_service, principal, task_id)
        _bind_idempotency(
            task_service, task_id, "messages", idempotency_key, body.model_dump(mode="json")
        )
        limiter.check(principal.subject, "message-create", limit=60)
        return await run_in_threadpool(
            task_service.add_message,
            task_id,
            body.content,
            idempotency_key=idempotency_key,
        )

    @router.get(
        "/tasks/{task_id}/events",
        response_model=list[ExecutionEvent],
        tags=["events"],
        summary="Catch up durable events after a sequence cursor",
        description="Event Store is authoritative; Redis/WebSocket delivery is only a live copy.",
        responses=ERROR_RESPONSES,
        operation_id="getTaskEvents",
    )
    async def get_events(
        task_id: str,
        principal: AUTHENTICATED_PRINCIPAL,
        run_id: Annotated[str, Query(min_length=1)],
        after_sequence: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> list[dict[str, Any]]:
        _authorize_task(task_service, principal, task_id)
        return await run_in_threadpool(
            task_service.event_history,
            task_id,
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    @router.post(
        "/tasks/{task_id}/event-ticket",
        response_model=EventTicketResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["events"],
        summary="Issue a short-lived WebSocket ticket",
        description="The opaque ticket is bound to the authenticated subject and one task, and is single-use.",
        responses=ERROR_RESPONSES,
        operation_id="createTaskEventTicket",
    )
    async def create_event_ticket(task_id: str, principal: AUTHENTICATED_PRINCIPAL) -> dict[str, str]:
        _authorize_task(task_service, principal, task_id)
        ticket, expires_at = tickets.issue(task_id, principal)
        return {"ticket": ticket, "expires_at": expires_at}

    @router.get(
        "/tasks/{task_id}/recovery-points",
        response_model=list[RecoveryPoint],
        tags=["recovery"],
        summary="List valid recovery points",
        responses=ERROR_RESPONSES,
        operation_id="getTaskRecoveryPoints",
    )
    async def get_recovery_points(task_id: str, principal: AUTHENTICATED_PRINCIPAL) -> list[dict[str, Any]]:
        _authorize_task(task_service, principal, task_id)
        return await run_in_threadpool(task_service.recovery_points, task_id)

    async def _approval_command(
        task_id: str,
        body: ApprovalDecisionRequest,
        principal: Principal,
        idempotency_key: str,
        decision: str,
    ) -> dict[str, Any]:
        _authorize_task(task_service, principal, task_id)
        operation = "approve" if decision == "APPROVE" else "reject"
        _bind_idempotency(
            task_service,
            task_id,
            operation,
            idempotency_key,
            body.model_dump(mode="json"),
        )
        limiter.check(principal.subject, "control", limit=30)
        await run_in_threadpool(
            task_service.decide_approval,
            task_id,
            decision=decision,
            approval_id=body.approval_id,
            patch_hash=body.patch_hash,
            base_revision=body.base_revision,
            expected_revision=body.expected_state_revision,
            decided_by=principal.subject,
            idempotency_key=idempotency_key,
        )
        return await run_in_threadpool(task_service.task_view, task_id)

    @router.post(
        "/tasks/{task_id}/approve",
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
        principal: AUTHENTICATED_PRINCIPAL,
        idempotency_key: IDEMPOTENCY_HEADER,
    ) -> dict[str, Any]:
        return await _approval_command(
            task_id, body, principal, idempotency_key, "APPROVE"
        )

    @router.post(
        "/tasks/{task_id}/reject",
        response_model=TaskStateResponse,
        tags=["human control"],
        summary="Reject the exact pending Patch",
        responses=ERROR_RESPONSES,
        operation_id="rejectTaskPatch",
    )
    async def reject(
        task_id: str,
        body: ApprovalDecisionRequest,
        principal: AUTHENTICATED_PRINCIPAL,
        idempotency_key: IDEMPOTENCY_HEADER,
    ) -> dict[str, Any]:
        return await _approval_command(
            task_id, body, principal, idempotency_key, "REJECT"
        )

    @router.post(
        "/tasks/{task_id}/cancel",
        response_model=TaskStateResponse,
        tags=["human control"],
        summary="Cancel a non-terminal task",
        responses=ERROR_RESPONSES,
        operation_id="cancelTask",
    )
    async def cancel(
        task_id: str,
        body: ControlTargetRequest,
        principal: AUTHENTICATED_PRINCIPAL,
        idempotency_key: IDEMPOTENCY_HEADER,
    ) -> dict[str, Any]:
        _authorize_task(task_service, principal, task_id)
        _bind_idempotency(
            task_service, task_id, "cancel", idempotency_key, body.model_dump(mode="json")
        )
        limiter.check(principal.subject, "control", limit=30)
        await run_in_threadpool(
            task_service.cancel,
            task_id,
            body.expected_state_revision,
            idempotency_key=idempotency_key,
        )
        return await run_in_threadpool(task_service.task_view, task_id)

    async def _recovery_command(
        task_id: str,
        body: RecoveryControlRequest,
        principal: Principal,
        idempotency_key: str,
        operation: str,
    ) -> dict[str, Any]:
        _authorize_task(task_service, principal, task_id)
        _bind_idempotency(
            task_service,
            task_id,
            operation,
            idempotency_key,
            body.model_dump(mode="json"),
        )
        limiter.check(principal.subject, "control", limit=30)
        if operation == "rollback":
            await run_in_threadpool(
                task_service.rollback,
                task_id,
                body.recovery_point_id,
                body.expected_state_revision,
                idempotency_key=idempotency_key,
            )
        else:
            await run_in_threadpool(
                task_service.restore,
                task_id,
                body.recovery_point_id,
                expected_revision=body.expected_state_revision,
                idempotency_key=idempotency_key,
            )
        return await run_in_threadpool(task_service.task_view, task_id)

    @router.post(
        "/tasks/{task_id}/rollback",
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
        principal: AUTHENTICATED_PRINCIPAL,
        idempotency_key: IDEMPOTENCY_HEADER,
    ) -> dict[str, Any]:
        return await _recovery_command(
            task_id, body, principal, idempotency_key, "rollback"
        )

    @router.post(
        "/tasks/{task_id}/restore",
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
        principal: AUTHENTICATED_PRINCIPAL,
        idempotency_key: IDEMPOTENCY_HEADER,
    ) -> dict[str, Any]:
        return await _recovery_command(
            task_id, body, principal, idempotency_key, "restore"
        )

    @router.post(
        "/tasks/{task_id}/change-requests",
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
        principal: AUTHENTICATED_PRINCIPAL,
        idempotency_key: IDEMPOTENCY_HEADER,
    ) -> dict[str, Any]:
        _authorize_task(task_service, principal, task_id)
        _bind_idempotency(
            task_service,
            task_id,
            "change-requests",
            idempotency_key,
            body.model_dump(mode="json"),
        )
        limiter.check(principal.subject, "control", limit=30)
        await run_in_threadpool(
            task_service.change_request,
            task_id,
            body.expected_state_revision,
            content=body.content,
            requested_by=principal.subject,
            confirm_patch_invalidation=body.confirm_patch_invalidation,
            idempotency_key=idempotency_key,
        )
        return await run_in_threadpool(task_service.task_view, task_id)

    @router.websocket("/tasks/{task_id}/events", name="task_events_websocket")
    async def task_events_websocket(
        websocket: WebSocket,
        task_id: str,
        run_id: str,
        ticket: str,
        after_sequence: int = 0,
    ) -> None:
        principal = tickets.consume(ticket, task_id)
        try:
            if principal is None:
                await websocket.close(code=4401, reason="invalid or expired event ticket")
                return
            _authorize_task(task_service, principal, task_id)
        except KeyError:
            await websocket.close(code=4404, reason="task not found")
            return
        if after_sequence < 0:
            await websocket.close(code=4400, reason="invalid event cursor")
            return
        await websocket.accept()
        cursor = after_sequence
        try:
            while True:
                events = await run_in_threadpool(
                    task_service.event_history,
                    task_id,
                    run_id,
                    after_sequence=cursor,
                    limit=200,
                )
                for event in events:
                    await websocket.send_json(event)
                    cursor = max(cursor, int(event["sequence_number"]))
                try:
                    incoming = await asyncio.wait_for(websocket.receive(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if incoming["type"] == "websocket.disconnect":
                    return
                # WebSocket is deliberately receive-only for clients.
                await websocket.close(code=1008, reason="control messages are not accepted")
                return
        except WebSocketDisconnect:
            return

    app.include_router(router)
    return app


__all__ = ["create_app"]
