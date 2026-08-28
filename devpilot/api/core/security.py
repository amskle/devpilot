from __future__ import annotations

import hashlib
import json
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


class SharedStateUnavailableError(RuntimeError):
    """Raised when distributed security state cannot be reached safely."""


@dataclass(frozen=True)
class _Ticket:
    task_id: str
    subject: str
    is_admin: bool
    expires_at: datetime


class EventTicketStore:
    """Single-process ticket store used only by local development."""

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
        expired = [
            token
            for token, ticket in self._tickets.items()
            if ticket.expires_at <= now
        ]
        for token in expired:
            self._tickets.pop(token, None)


class RateLimiter:
    """In-process limiter for single-worker development only."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(
        self,
        subject: str,
        bucket: str,
        *,
        limit: int,
        window_seconds: int = 60,
    ) -> None:
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


class RedisEventTicketStore:
    """Cross-worker, single-use WebSocket tickets backed by Redis."""

    _CONSUME_SCRIPT = """
local value = redis.call('GET', KEYS[1])
if value then
  redis.call('DEL', KEYS[1])
end
return value
"""

    def __init__(
        self,
        client: object,
        *,
        ttl_seconds: int = 30,
        key_prefix: str = "devpilot",
    ) -> None:
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.key_prefix = key_prefix.rstrip(":")

    def _key(self, token: str) -> str:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"{self.key_prefix}:event-ticket:{digest}"

    def issue(self, task_id: str, principal: Principal) -> tuple[str, str]:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)
        payload = json.dumps(
            {
                "task_id": task_id,
                "subject": principal.subject,
                "is_admin": principal.is_admin,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            for _ in range(3):
                token = secrets.token_urlsafe(32)
                stored = self.client.set(
                    self._key(token), payload, ex=self.ttl_seconds, nx=True
                )
                if stored:
                    return token, expires_at.isoformat()
        except Exception as exc:
            raise SharedStateUnavailableError(
                "Redis event ticket store is unavailable"
            ) from exc
        raise SharedStateUnavailableError("could not allocate a unique event ticket")

    def consume(self, token: str, task_id: str) -> Principal | None:
        try:
            raw = self.client.eval(self._CONSUME_SCRIPT, 1, self._key(token))
        except Exception as exc:
            raise SharedStateUnavailableError(
                "Redis event ticket store is unavailable"
            ) from exc
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            payload = json.loads(str(raw))
        except (TypeError, ValueError) as exc:
            raise SharedStateUnavailableError("invalid event ticket state") from exc
        if payload.get("task_id") != task_id or not payload.get("subject"):
            return None
        return Principal(
            str(payload["subject"]), bool(payload.get("is_admin", False))
        )


class RedisRateLimiter:
    """Atomic fixed-window limiter shared by all API workers."""

    _CHECK_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {count, ttl}
"""

    def __init__(self, client: object, *, key_prefix: str = "devpilot") -> None:
        self.client = client
        self.key_prefix = key_prefix.rstrip(":")

    def _key(self, subject: str, bucket: str) -> str:
        identity = hashlib.sha256(subject.encode("utf-8")).hexdigest()
        safe_bucket = hashlib.sha256(bucket.encode("utf-8")).hexdigest()[:16]
        return f"{self.key_prefix}:rate-limit:{safe_bucket}:{identity}"

    def check(
        self,
        subject: str,
        bucket: str,
        *,
        limit: int,
        window_seconds: int = 60,
    ) -> None:
        try:
            result = self.client.eval(
                self._CHECK_SCRIPT,
                1,
                self._key(subject, bucket),
                window_seconds,
            )
            count, ttl = int(result[0]), max(1, int(result[1]))
        except Exception as exc:
            raise SharedStateUnavailableError(
                "Redis rate limiter is unavailable"
            ) from exc
        if count > limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"rate limit exceeded for {bucket}",
                headers={"Retry-After": str(ttl)},
            )
