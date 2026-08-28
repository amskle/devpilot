from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi.concurrency import run_in_threadpool

from devpilot.api.core.config import Principal
from devpilot.api.core.security import (
    EventTicketStore,
    RateLimiter,
    SharedStateUnavailableError,
)
from devpilot.api.schemas import (
    ApprovalDecisionRequest,
    ChangeRequestBody,
    ControlTargetRequest,
    CreateTaskRequest,
    MessageCreateRequest,
    RecoveryControlRequest,
)
from devpilot.domain.models import TaskStatus
from devpilot.events import RedisStreamConsumer
from devpilot.service import TaskService


class ControlPlaneService:
    """Connect HTTP handlers to TaskService without leaking transport concerns."""

    def __init__(
        self,
        tasks: TaskService,
        tickets: EventTicketStore,
        limiter: RateLimiter,
        live_events: RedisStreamConsumer | None = None,
    ) -> None:
        self.tasks = tasks
        self.tickets = tickets
        self.limiter = limiter
        self.live_events = live_events

    def authorize(self, principal: Principal, task_id: str) -> None:
        if self.tasks.control.get_task(task_id) is None:
            raise KeyError(task_id)
        owner = self.tasks.control.task_owner(task_id)
        if principal.is_admin or owner == principal.subject:
            return
        raise KeyError(task_id)

    async def consume_ticket(self, ticket: str, task_id: str) -> Principal | None:
        return await run_in_threadpool(self.tickets.consume, ticket, task_id)

    async def _check_rate_limit(
        self, subject: str, bucket: str, *, limit: int
    ) -> None:
        await run_in_threadpool(
            self.limiter.check,
            subject,
            bucket,
            limit=limit,
        )

    @staticmethod
    def _encode_cursor(offset: int) -> str:
        return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")

    @staticmethod
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

    def _bind_idempotency(
        self,
        task_id: str,
        operation: str,
        key: str,
        payload: dict[str, Any],
    ) -> None:
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        self.tasks.control.bind_idempotency_input(
            task_id, operation, key, hashlib.sha256(encoded).hexdigest()
        )

    async def create_task(
        self, body: CreateTaskRequest, principal: Principal
    ) -> dict[str, Any]:
        await self._check_rate_limit(principal.subject, "task-create", limit=10)
        state = await run_in_threadpool(
            self.tasks.create_task,
            Path(body.repo),
            body.request,
            revision=body.revision,
            model=body.model,
        )
        self.tasks.control.bind_task_owner(state["task_id"], principal.subject)
        return await run_in_threadpool(self.tasks.task_view, state["task_id"])

    async def list_tasks(
        self,
        principal: Principal,
        *,
        task_status: TaskStatus | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        offset = self._decode_cursor(cursor)
        items = await run_in_threadpool(
            self.tasks.list_task_views,
            status=task_status.value if task_status else None,
        )
        visible = [
            item
            for item in items
            if principal.is_admin
            or self.tasks.control.task_owner(item["task_id"]) == principal.subject
        ]
        page = visible[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "items": page,
            "next_cursor": (
                self._encode_cursor(next_offset) if next_offset < len(visible) else None
            ),
        }

    async def task_view(self, task_id: str, principal: Principal) -> dict[str, Any]:
        self.authorize(principal, task_id)
        return await run_in_threadpool(self.tasks.task_view, task_id)

    async def plans(self, task_id: str, principal: Principal) -> list[dict[str, Any]]:
        self.authorize(principal, task_id)
        return await run_in_threadpool(self.tasks.plan_documents, task_id)

    async def diff(self, task_id: str, principal: Principal) -> dict[str, Any]:
        self.authorize(principal, task_id)
        return await run_in_threadpool(self.tasks.diff_document, task_id)

    async def trace(
        self, task_id: str, principal: Principal, run_id: str | None
    ) -> dict[str, Any]:
        self.authorize(principal, task_id)
        return await run_in_threadpool(self.tasks.trace, task_id, run_id)

    async def messages(
        self, task_id: str, principal: Principal
    ) -> list[dict[str, Any]]:
        self.authorize(principal, task_id)
        return await run_in_threadpool(self.tasks.messages, task_id)

    async def add_message(
        self,
        task_id: str,
        body: MessageCreateRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.authorize(principal, task_id)
        self._bind_idempotency(
            task_id, "messages", idempotency_key, body.model_dump(mode="json")
        )
        await self._check_rate_limit(principal.subject, "message-create", limit=60)
        return await run_in_threadpool(
            self.tasks.add_message,
            task_id,
            body.content,
            idempotency_key=idempotency_key,
        )

    async def events(
        self,
        task_id: str,
        principal: Principal,
        run_id: str,
        *,
        after_sequence: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        self.authorize(principal, task_id)
        return await self.event_batch(
            task_id, run_id, after_sequence=after_sequence, limit=limit
        )

    async def event_batch(
        self,
        task_id: str,
        run_id: str,
        *,
        after_sequence: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await run_in_threadpool(
            self.tasks.event_history,
            task_id,
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def issue_ticket(
        self, task_id: str, principal: Principal
    ) -> dict[str, str]:
        self.authorize(principal, task_id)
        ticket, expires_at = await run_in_threadpool(
            self.tickets.issue, task_id, principal
        )
        return {"ticket": ticket, "expires_at": expires_at}

    async def live_cursor(self, task_id: str, run_id: str) -> str | None:
        if self.live_events is None:
            return None
        try:
            return await run_in_threadpool(
                self.live_events.latest_id, task_id, run_id
            )
        except Exception as exc:
            raise SharedStateUnavailableError(
                "Redis live event transport is unavailable"
            ) from exc

    async def live_event_batch(
        self,
        task_id: str,
        run_id: str,
        *,
        after_stream_id: str,
        limit: int,
        block_milliseconds: int,
    ) -> tuple[str, list[dict[str, Any]]]:
        if self.live_events is None:
            return after_stream_id, []
        try:
            cursor, events = await run_in_threadpool(
                self.live_events.read,
                task_id,
                run_id,
                after_stream_id=after_stream_id,
                count=limit,
                block_milliseconds=block_milliseconds,
            )
        except Exception as exc:
            raise SharedStateUnavailableError(
                "Redis live event transport is unavailable"
            ) from exc
        return cursor, [event.to_state_dict() for event in events]

    async def recovery_points(
        self, task_id: str, principal: Principal
    ) -> list[dict[str, Any]]:
        self.authorize(principal, task_id)
        return await run_in_threadpool(self.tasks.recovery_points, task_id)

    async def decide_approval(
        self,
        task_id: str,
        body: ApprovalDecisionRequest,
        principal: Principal,
        idempotency_key: str,
        decision: str,
    ) -> dict[str, Any]:
        self.authorize(principal, task_id)
        operation = "approve" if decision == "APPROVE" else "reject"
        self._bind_idempotency(
            task_id, operation, idempotency_key, body.model_dump(mode="json")
        )
        await self._check_rate_limit(principal.subject, "control", limit=30)
        await run_in_threadpool(
            self.tasks.decide_approval,
            task_id,
            decision=decision,
            approval_id=body.approval_id,
            patch_hash=body.patch_hash,
            base_revision=body.base_revision,
            expected_revision=body.expected_state_revision,
            decided_by=principal.subject,
            idempotency_key=idempotency_key,
        )
        return await run_in_threadpool(self.tasks.task_view, task_id)

    async def cancel(
        self,
        task_id: str,
        body: ControlTargetRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.authorize(principal, task_id)
        self._bind_idempotency(
            task_id, "cancel", idempotency_key, body.model_dump(mode="json")
        )
        await self._check_rate_limit(principal.subject, "control", limit=30)
        await run_in_threadpool(
            self.tasks.cancel,
            task_id,
            body.expected_state_revision,
            idempotency_key=idempotency_key,
        )
        return await run_in_threadpool(self.tasks.task_view, task_id)

    async def recover(
        self,
        task_id: str,
        body: RecoveryControlRequest,
        principal: Principal,
        idempotency_key: str,
        operation: str,
    ) -> dict[str, Any]:
        self.authorize(principal, task_id)
        self._bind_idempotency(
            task_id, operation, idempotency_key, body.model_dump(mode="json")
        )
        await self._check_rate_limit(principal.subject, "control", limit=30)
        if operation == "rollback":
            await run_in_threadpool(
                self.tasks.rollback,
                task_id,
                body.recovery_point_id,
                body.expected_state_revision,
                idempotency_key=idempotency_key,
            )
        else:
            await run_in_threadpool(
                self.tasks.restore,
                task_id,
                body.recovery_point_id,
                expected_revision=body.expected_state_revision,
                idempotency_key=idempotency_key,
            )
        return await run_in_threadpool(self.tasks.task_view, task_id)

    async def change_request(
        self,
        task_id: str,
        body: ChangeRequestBody,
        principal: Principal,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.authorize(principal, task_id)
        self._bind_idempotency(
            task_id, "change-requests", idempotency_key, body.model_dump(mode="json")
        )
        await self._check_rate_limit(principal.subject, "control", limit=30)
        await run_in_threadpool(
            self.tasks.change_request,
            task_id,
            body.expected_state_revision,
            content=body.content,
            requested_by=principal.subject,
            confirm_patch_invalidation=body.confirm_patch_invalidation,
            idempotency_key=idempotency_key,
        )
        return await run_in_threadpool(self.tasks.task_view, task_id)
