from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from devpilot.api import create_app
from devpilot.api.core.config import ApiSettings, Principal
from devpilot.api.core.security import RedisEventTicketStore, RedisRateLimiter


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, tuple[str, float | None]] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.lock = threading.Lock()
        self.available = True
        self.closed = False

    def _ensure_available(self) -> None:
        if not self.available:
            raise ConnectionError("Redis unavailable")

    def _purge(self, key: str) -> None:
        item = self.values.get(key)
        if item is not None and item[1] is not None and item[1] <= time.monotonic():
            self.values.pop(key, None)

    def ping(self) -> bool:
        self._ensure_available()
        return True

    def close(self) -> None:
        self.closed = True

    def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        self._ensure_available()
        with self.lock:
            self._purge(key)
            if nx and key in self.values:
                return False
            self.values[key] = (value, time.monotonic() + ex)
            return True

    def eval(self, script: str, key_count: int, key: str, *args: object):
        del key_count
        self._ensure_available()
        with self.lock:
            self._purge(key)
            if "INCR" in script:
                window = int(args[0])
                current = int(self.values.get(key, ("0", None))[0]) + 1
                existing_expiry = self.values.get(key, ("", None))[1]
                expires_at = existing_expiry or time.monotonic() + window
                self.values[key] = (str(current), expires_at)
                ttl = max(1, int(expires_at - time.monotonic()))
                return [current, ttl]
            item = self.values.pop(key, None)
            return item[0] if item else None

    def xadd(self, stream: str, fields: dict[str, str], **_: object) -> str:
        self._ensure_available()
        with self.lock:
            messages = self.streams.setdefault(stream, [])
            message_id = f"{len(messages) + 1}-0"
            messages.append((message_id, fields))
            return message_id

    def xrevrange(
        self, stream: str, *, max: str, min: str, count: int
    ) -> list[tuple[str, dict[str, str]]]:
        del max, min
        self._ensure_available()
        return list(reversed(self.streams.get(stream, [])))[:count]

    def xread(self, streams: dict[str, str], **options: object):
        self._ensure_available()
        stream, cursor = next(iter(streams.items()))
        after = int(cursor.split("-", 1)[0])
        selected = [
            item
            for item in self.streams.get(stream, [])
            if int(item[0].split("-", 1)[0]) > after
        ]
        selected = selected[: int(options.get("count", len(selected)))]
        return [(stream, selected)] if selected else []


def test_redis_ticket_and_rate_limit_are_shared_across_workers() -> None:
    redis = FakeRedis()
    tickets_a = RedisEventTicketStore(redis, ttl_seconds=30, key_prefix="test")
    tickets_b = RedisEventTicketStore(redis, ttl_seconds=30, key_prefix="test")
    token, _ = tickets_a.issue("task-1", Principal("alice"))

    assert all(token not in key for key in redis.values)
    assert tickets_b.consume(token, "task-1") == Principal("alice")
    assert tickets_a.consume(token, "task-1") is None

    limiter_a = RedisRateLimiter(redis, key_prefix="test")
    limiter_b = RedisRateLimiter(redis, key_prefix="test")
    limiter_a.check("alice", "control", limit=2)
    limiter_b.check("alice", "control", limit=2)
    with pytest.raises(HTTPException) as exc_info:
        limiter_a.check("alice", "control", limit=2)
    assert exc_info.value.status_code == 429


def test_phase6_settings_require_shared_state_for_multi_worker() -> None:
    with pytest.raises(ValueError, match="multi-worker"):
        ApiSettings(tokens={"token": Principal("alice")}, worker_count=2)


def test_non_development_environment_requires_explicit_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVPILOT_ENV", "production")
    monkeypatch.setenv("DEVPILOT_REDIS_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.delenv("DEVPILOT_API_TOKENS", raising=False)
    with pytest.raises(ValueError, match="DEVPILOT_API_TOKENS"):
        ApiSettings.from_env()

    monkeypatch.setenv(
        "DEVPILOT_API_TOKENS", '{"token":{"subject":"alice","admin":true}}'
    )
    monkeypatch.delenv("DEVPILOT_REDIS_URL", raising=False)
    with pytest.raises(ValueError, match="DEVPILOT_REDIS_URL"):
        ApiSettings.from_env()


def test_readiness_reports_runtime_redis_outage(tmp_path) -> None:
    from devpilot.agents.model_gateway import ScriptedFakeModelGateway
    from devpilot.service import TaskService

    redis = FakeRedis()
    settings = ApiSettings(
        tokens={"token": Principal("alice")},
        redis_url="redis://127.0.0.1:6379/0",
    )
    service = TaskService(
        data_dir=tmp_path / "data", gateway=ScriptedFakeModelGateway({})
    )
    try:
        app = create_app(service=service, settings=settings, redis_client=redis)
        with TestClient(app) as client:
            assert client.get("/api/ready").json() == {
                "status": "ready",
                "service": "devpilot-api",
                "dependencies": {"redis": "ok"},
            }
            redis.available = False
            unavailable = client.get("/api/ready")
            assert unavailable.status_code == 503
            assert unavailable.json()["dependencies"]["redis"] == "unavailable"
            failed_closed = client.post(
                "/api/tasks",
                headers={"Authorization": "Bearer token"},
                json={"repo": str(tmp_path), "request": "must not run"},
            )
            assert failed_closed.status_code == 503
            assert failed_closed.json()["code"] == "SHARED_STATE_UNAVAILABLE"
    finally:
        service.close()


def test_redis_ticket_payload_does_not_expose_raw_token() -> None:
    redis = FakeRedis()
    store = RedisEventTicketStore(redis, key_prefix="test")
    token, _ = store.issue("task-1", Principal("alice", True))
    payload = json.loads(next(iter(redis.values.values()))[0])

    assert all(token not in key for key in redis.values)
    assert payload == {"task_id": "task-1", "subject": "alice", "is_admin": True}
