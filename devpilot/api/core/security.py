from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from fastapi.security import HTTPBearer

from devpilot.api.core.config import Principal


bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="DevPilotBearer",
    description="Bearer token configured through DEVPILOT_API_TOKENS",
)


@dataclass(frozen=True)
class _Ticket:
    task_id: str
    subject: str
    is_admin: bool
    expires_at: datetime


class EventTicketStore:
    def __init__(self, ttl_seconds: int = 30) -> None:
        self.ttl_seconds = ttl_seconds
        self._tickets: dict[str, _Ticket] = {}
        self._lock = threading.Lock()

    def issue(self, task_id: str, principal: Principal) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)
        with self._lock:
            self._purge_locked()
            self._tickets[token] = _Ticket(
                task_id, principal.subject, principal.is_admin, expires_at
            )
        return token, expires_at.isoformat()

    def consume(self, token: str, task_id: str) -> Principal | None:
        with self._lock:
            self._purge_locked()
            ticket = self._tickets.pop(token, None)
        if ticket is None or ticket.task_id != task_id:
            return None
        return Principal(ticket.subject, ticket.is_admin)

    def _purge_locked(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [token for token, ticket in self._tickets.items() if ticket.expires_at <= now]
        for token in expired:
            self._tickets.pop(token, None)


class RateLimiter:
    """Small in-process limit suitable for the single-node Phase 4 API."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, subject: str, bucket: str, *, limit: int, window_seconds: int = 60) -> None:
        now = time.monotonic()
        key = (subject, bucket)
        with self._lock:
            entries = self._entries[key]
            while entries and entries[0] <= now - window_seconds:
                entries.popleft()
            if len(entries) >= limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"rate limit exceeded for {bucket}",
                    headers={"Retry-After": str(window_seconds)},
                )
            entries.append(now)
