from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from devpilot.api.core.dependencies import (
    AuthenticatedPrincipal,
    ControlPlaneDependency,
)
from devpilot.api.core.security import SharedStateUnavailableError
from devpilot.api.schemas import ERROR_RESPONSES, EventTicketResponse, ExecutionEvent


router = APIRouter(prefix="/tasks/{task_id}", tags=["events"])


@router.get(
    "/events",
    response_model=list[ExecutionEvent],
    summary="Catch up durable events after a sequence cursor",
    description=(
        "Event Store is authoritative; Redis/WebSocket delivery is only a live copy."
    ),
    responses=ERROR_RESPONSES,
    operation_id="getTaskEvents",
)
async def get_events(
    task_id: str,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
    run_id: Annotated[str, Query(min_length=1)],
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[dict[str, Any]]:
    return await control.events(
        task_id,
        principal,
        run_id,
        after_sequence=after_sequence,
        limit=limit,
    )


@router.post(
    "/event-ticket",
    response_model=EventTicketResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a short-lived WebSocket ticket",
    description=(
        "The opaque ticket is bound to the authenticated subject and one task, "
        "and is single-use."
    ),
    responses=ERROR_RESPONSES,
    operation_id="createTaskEventTicket",
)
async def create_event_ticket(
    task_id: str,
    principal: AuthenticatedPrincipal,
    control: ControlPlaneDependency,
) -> dict[str, str]:
    return await control.issue_ticket(task_id, principal)


@router.websocket("/events", name="task_events_websocket")
async def task_events_websocket(
    websocket: WebSocket,
    task_id: str,
    run_id: str,
    ticket: str,
    control: ControlPlaneDependency,
    after_sequence: int = 0,
) -> None:
    try:
        principal = await control.consume_ticket(ticket, task_id)
        if principal is None:
            await websocket.close(code=4401, reason="invalid or expired event ticket")
            return
        control.authorize(principal, task_id)
    except SharedStateUnavailableError:
        await websocket.close(code=1013, reason="shared security state unavailable")
        return
    except KeyError:
        await websocket.close(code=4404, reason="task not found")
        return
    if after_sequence < 0:
        await websocket.close(code=4400, reason="invalid event cursor")
        return
    await websocket.accept()
    cursor = after_sequence
    try:
        stream_cursor = await control.live_cursor(task_id, run_id)
    except SharedStateUnavailableError:
        await websocket.close(code=1013, reason="live event transport unavailable")
        return
    try:
        while True:
            events = await control.event_batch(
                task_id, run_id, after_sequence=cursor, limit=200
            )
            if not events and stream_cursor is not None:
                stream_cursor, events = await control.live_event_batch(
                    task_id,
                    run_id,
                    after_stream_id=stream_cursor,
                    limit=200,
                    block_milliseconds=500,
                )
            for event in events:
                sequence = int(event["sequence_number"])
                if sequence > cursor:
                    await websocket.send_json(event)
                    cursor = sequence
            try:
                timeout = 0.05 if stream_cursor is not None else 0.5
                incoming = await asyncio.wait_for(websocket.receive(), timeout=timeout)
            except asyncio.TimeoutError:
                continue
            if incoming["type"] == "websocket.disconnect":
                return
            await websocket.close(code=1008, reason="control messages are not accepted")
            return
    except SharedStateUnavailableError:
        await websocket.close(code=1013, reason="live event transport unavailable")
    except WebSocketDisconnect:
        return
