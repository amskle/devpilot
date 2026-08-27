from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

from devpilot.events.models import ExecutionEvent
from devpilot.events.transport import RedisStreamConsumer


@dataclass(eq=False)
class EventSubscription:
    _hub: "EventSubscriptionHub"
    task_id: str
    run_id: str | None
    queue: asyncio.Queue[ExecutionEvent]
    _closed: bool = False

    async def get(self) -> ExecutionEvent:
        if self._closed:
            raise RuntimeError("subscription is closed")
        return await self.queue.get()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._hub.unsubscribe(self)

    async def __aenter__(self) -> "EventSubscription":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.close()


class EventSubscriptionHub:
    """Framework-neutral WebSocket fan-out foundation.

    Queue overflow drops the live copy only. Clients recover from the durable
    event cursor, so the Event Store remains the source of truth.
    """

    def __init__(self, *, queue_size: int = 256) -> None:
        if queue_size < 1:
            raise ValueError("queue_size must be positive")
        self.queue_size = queue_size
        self._subscriptions: set[EventSubscription] = set()

    def subscribe(self, task_id: str, run_id: str | None = None) -> EventSubscription:
        subscription = EventSubscription(
            _hub=self,
            task_id=task_id,
            run_id=run_id,
            queue=asyncio.Queue(maxsize=self.queue_size),
        )
        self._subscriptions.add(subscription)
        return subscription

    def unsubscribe(self, subscription: EventSubscription) -> None:
        self._subscriptions.discard(subscription)

    async def publish(self, event: ExecutionEvent) -> None:
        for subscription in tuple(self._subscriptions):
            if subscription.task_id != event.task_id:
                continue
            if subscription.run_id is not None and subscription.run_id != event.run_id:
                continue
            if subscription.queue.full():
                subscription.queue.get_nowait()
            subscription.queue.put_nowait(event)


class RedisWebSocketBridge:
    """Moves Redis Stream events into queues consumed by WebSocket handlers."""

    def __init__(
        self,
        consumer: RedisStreamConsumer,
        hub: EventSubscriptionHub,
        *,
        deduplication_window: int = 10_000,
    ) -> None:
        if deduplication_window < 1:
            raise ValueError("deduplication_window must be positive")
        self.consumer = consumer
        self.hub = hub
        self.deduplication_window = deduplication_window
        self._seen_order: deque[str] = deque()
        self._seen_ids: set[str] = set()

    def _remember(self, event_id: str) -> bool:
        if event_id in self._seen_ids:
            return False
        self._seen_ids.add(event_id)
        self._seen_order.append(event_id)
        while len(self._seen_order) > self.deduplication_window:
            self._seen_ids.discard(self._seen_order.popleft())
        return True

    async def poll_once(
        self,
        task_id: str,
        run_id: str,
        *,
        after_stream_id: str = "0-0",
        count: int = 100,
        block_milliseconds: int | None = None,
    ) -> tuple[str, int]:
        cursor, events = await asyncio.to_thread(
            self.consumer.read,
            task_id,
            run_id,
            after_stream_id=after_stream_id,
            count=count,
            block_milliseconds=block_milliseconds,
        )
        forwarded = 0
        for event in events:
            if not self._remember(event.event_id):
                continue
            await self.hub.publish(event)
            forwarded += 1
        return cursor, forwarded
