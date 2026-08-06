from __future__ import annotations

import threading
from typing import Optional

from shared_state.schema import TaskState


class MemoryStore:
    """In-memory shared state store used by the local pipeline."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}
        self._lock = threading.Lock()

    def put(self, task: TaskState) -> None:
        with self._lock:
            self._tasks[task.task_id] = task

    def get(self, task_id: str) -> Optional[TaskState]:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> list[TaskState]:
        with self._lock:
            return list(self._tasks.values())


class RedisStore:
    """Redis-backed store; requires the optional redis dependency."""

    def __init__(self, url: str = "redis://localhost:6379/0") -> None:
        try:
            import redis  # type: ignore
        except ImportError as exc:
            raise RuntimeError("RedisStore requires pip install redis") from exc
        self._client = redis.Redis.from_url(url)

    def put(self, task: TaskState) -> None:
        self._client.set(f"devpilot:task:{task.task_id}", task.to_json())

    def get(self, task_id: str) -> Optional[TaskState]:
        raw = self._client.get(f"devpilot:task:{task_id}")
        return TaskState.from_json(raw.decode("utf-8")) if raw else None
