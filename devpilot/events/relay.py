from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from devpilot.events.models import ExecutionEvent, OutboxEntry
from devpilot.events.transport import EventTransport


class OutboxStore(Protocol):
    def claim_outbox(
        self,
        relay_id: str,
        *,
        limit: int,
        lease_seconds: int,
    ) -> list[tuple[OutboxEntry, ExecutionEvent]]: ...

    def retry_outbox(
        self,
        outbox_id: str,
        *,
        relay_id: str,
        error: str,
        delay_seconds: int,
    ) -> None: ...

    def publish_outbox(
        self,
        outbox_id: str,
        *,
        relay_id: str,
        stream_id: str,
    ) -> None: ...


@dataclass(frozen=True)
class RelayResult:
    published: int = 0
    failed: int = 0


class OutboxRelay:
    """Claims persisted outbox rows and delivers them in database order."""

    def __init__(
        self,
        store: OutboxStore,
        transport: EventTransport,
        *,
        relay_id: str,
        batch_size: int = 100,
        claim_lease_seconds: int = 30,
        base_retry_seconds: int = 1,
        max_retry_seconds: int = 60,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.store = store
        self.transport = transport
        self.relay_id = relay_id
        self.batch_size = batch_size
        self.claim_lease_seconds = claim_lease_seconds
        self.base_retry_seconds = base_retry_seconds
        self.max_retry_seconds = max_retry_seconds

    def run_once(self) -> RelayResult:
        published = 0
        failed = 0
        for _ in range(self.batch_size):
            claimed = self.store.claim_outbox(
                self.relay_id,
                limit=1,
                lease_seconds=self.claim_lease_seconds,
            )
            if not claimed:
                break
            entry, event = claimed[0]
            try:
                stream_id = self.transport.publish(event)
            except Exception as exc:
                delay = min(
                    self.max_retry_seconds,
                    self.base_retry_seconds
                    * (2 ** min(30, max(0, entry.attempts - 1))),
                )
                self.store.retry_outbox(
                    entry.outbox_id,
                    relay_id=self.relay_id,
                    error=str(exc),
                    delay_seconds=delay,
                )
                failed += 1
                break
            self.store.publish_outbox(
                entry.outbox_id,
                relay_id=self.relay_id,
                stream_id=stream_id,
            )
            published += 1
        return RelayResult(published=published, failed=failed)
