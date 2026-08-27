from __future__ import annotations

import json
from typing import Any, Protocol

from devpilot.events.models import ExecutionEvent


class EventTransport(Protocol):
    """At-least-once transport used by the transactional outbox relay."""

    def publish(self, event: ExecutionEvent) -> str:
        """Publish one persisted event and return the transport message id."""


class InMemoryEventTransport:
    """Deterministic transport for local execution and relay tests."""

    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    def publish(self, event: ExecutionEvent) -> str:
        self.events.append(event)
        return f"memory-{len(self.events)}"


class RedisStreamTransport:
    """Redis Streams adapter with an injected redis-py compatible client."""

    def __init__(
        self,
        client: Any,
        *,
        stream_prefix: str = "devpilot:events",
        max_length: int | None = 10_000,
    ) -> None:
        self.client = client
        self.stream_prefix = stream_prefix.rstrip(":")
        self.max_length = max_length

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        stream_prefix: str = "devpilot:events",
        max_length: int | None = 10_000,
    ) -> "RedisStreamTransport":
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - depends on optional deployment package
            raise RuntimeError("Redis transport requires the 'redis' package") from exc
        return cls(
            redis.Redis.from_url(url, decode_responses=True),
            stream_prefix=stream_prefix,
            max_length=max_length,
        )

    def stream_name(self, event: ExecutionEvent) -> str:
        return f"{self.stream_prefix}:{event.task_id}:{event.run_id}"

    def publish(self, event: ExecutionEvent) -> str:
        fields = {
            "event_id": event.event_id,
            "schema_version": str(event.schema_version),
            "task_id": event.task_id,
            "run_id": event.run_id,
            "sequence_number": str(event.sequence_number),
            "event_type": event.event_type,
            "envelope": json.dumps(event.to_state_dict(), ensure_ascii=False, separators=(",", ":")),
        }
        options: dict[str, Any] = {}
        if self.max_length is not None:
            options.update({"maxlen": self.max_length, "approximate": True})
        stream_id = self.client.xadd(self.stream_name(event), fields, **options)
        if isinstance(stream_id, bytes):
            return stream_id.decode("utf-8")
        return str(stream_id)


class RedisStreamConsumer:
    """Cursor reader used by the WebSocket bridge or other live consumers."""

    def __init__(self, client: Any, *, stream_prefix: str = "devpilot:events") -> None:
        self.client = client
        self.stream_prefix = stream_prefix.rstrip(":")

    def stream_name(self, task_id: str, run_id: str) -> str:
        return f"{self.stream_prefix}:{task_id}:{run_id}"

    @staticmethod
    def _text(value: Any) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    def read(
        self,
        task_id: str,
        run_id: str,
        *,
        after_stream_id: str = "0-0",
        count: int = 100,
        block_milliseconds: int | None = None,
    ) -> tuple[str, list[ExecutionEvent]]:
        if count < 1:
            raise ValueError("count must be positive")
        options: dict[str, Any] = {"count": count}
        if block_milliseconds is not None:
            options["block"] = block_milliseconds
        response = self.client.xread(
            {self.stream_name(task_id, run_id): after_stream_id},
            **options,
        )
        cursor = after_stream_id
        events: list[ExecutionEvent] = []
        for _stream, messages in response or []:
            for message_id, fields in messages:
                cursor = self._text(message_id)
                normalized = {self._text(key): value for key, value in fields.items()}
                envelope = normalized.get("envelope")
                if isinstance(envelope, bytes):
                    envelope = envelope.decode("utf-8")
                if envelope is None:
                    continue
                events.append(ExecutionEvent.from_state_dict(json.loads(str(envelope))))
        return cursor, events
