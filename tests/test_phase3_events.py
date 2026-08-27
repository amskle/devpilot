import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from devpilot.clock import FrozenClock
from devpilot.domain.state import create_initial_state
from devpilot.events import (
    EventSubscriptionHub,
    InMemoryEventTransport,
    OutboxRelay,
    RedisStreamConsumer,
    RedisStreamTransport,
    RedisWebSocketBridge,
)
from devpilot.services.storage import SQLiteControlStore


def _store(tmp_path, *, clock=None):
    store = SQLiteControlStore(tmp_path / "control.sqlite", clock)
    store.create_task(create_initial_state("task", "run"))
    return store


def test_event_envelope_cursor_trace_redaction_and_causation(tmp_path):
    store = _store(tmp_path)
    try:
        event = store.append_event(
            "task",
            "run",
            "agent_summary",
            {
                "node": "diagnosis",
                "attempt": 2,
                "authorization": "Bearer should-never-persist",
                "message": "request used Bearer abcdefghijklmnop",
                "environment": {"SAFE": "no", "TOKEN": "secret"},
                "report_ref": {
                    "artifact_id": "art_123",
                    "kind": "report",
                    "sha256": "abc",
                    "size": 3,
                },
            },
        )

        assert event.sequence_number == 2
        assert event.state_revision is None
        assert event.node_name == "diagnosis"
        assert event.attempt == 2
        assert event.correlation_id == "run"
        assert event.causation_id == store.events("task", "run")[0]["event_id"]
        assert event.payload["authorization"] == "[REDACTED]"
        assert event.payload["environment"] == "[REDACTED]"
        assert event.payload["message"] == "request used Bearer [REDACTED]"
        assert event.artifact_refs[0]["artifact_id"] == "art_123"

        page = store.events("task", "run", after_sequence=1, limit=1)
        assert [item["event_id"] for item in page] == [event.event_id]
        trace = store.trace("task", "run")
        assert trace["event_count"] == 2
        assert trace["first_sequence"] == 1
        assert trace["last_sequence"] == 2
        assert trace["sequence_gaps"] == []
    finally:
        store.close()


def test_event_and_outbox_roll_back_with_control_transaction(tmp_path):
    store = _store(tmp_path)
    try:
        state = create_initial_state("task", "run")
        changed = dict(state)
        changed["status"] = "RUNNING"
        with store._conn:
            store._conn.execute(
                """CREATE TRIGGER reject_projection BEFORE UPDATE ON task_projection
                   BEGIN SELECT RAISE(ABORT, 'reject projection'); END"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="reject projection"):
            store.transition(changed, expected_revision=0, event_type="should_rollback")

        assert [event["event_type"] for event in store.events("task", "run")] == [
            "task_created"
        ]
        assert len(store.outbox_entries()) == 1
    finally:
        store.close()


def test_outbox_relay_publishes_in_order_and_marks_delivery(tmp_path):
    store = _store(tmp_path)
    try:
        store.append_event("task", "run", "second", {"value": 2})
        transport = InMemoryEventTransport()
        relay = OutboxRelay(store, transport, relay_id="relay-a")

        result = relay.run_once()

        assert result.published == 2
        assert result.failed == 0
        assert [event.event_type for event in transport.events] == ["task_created", "second"]
        assert {entry.status for entry in store.outbox_entries()} == {"PUBLISHED"}
        assert relay.run_once().published == 0
    finally:
        store.close()


def test_relay_waits_for_checkpoint_and_reconcile_discards_stale_delivery(tmp_path):
    store = _store(tmp_path)
    try:
        initial = create_initial_state("task", "run")
        changed = dict(initial)
        changed["status"] = "RUNNING"
        advanced = store.transition(
            changed,
            expected_revision=0,
            event_type="advanced",
        )
        store.append_event("task", "run", "message_after_advanced", {})
        transport = InMemoryEventTransport()
        relay = OutboxRelay(store, transport, relay_id="relay-a")

        assert relay.run_once().published == 1
        assert [event.event_type for event in transport.events] == ["task_created"]

        store.confirm_checkpoint("task", "run", advanced["state_revision"])
        assert relay.run_once().published == 2
        assert [event.event_type for event in transport.events[-2:]] == [
            "advanced",
            "message_after_advanced",
        ]

        later = dict(advanced)
        later["pause_reason"] = "unconfirmed"
        store.transition(later, expected_revision=1, event_type="stale_transition")
        assert store.reconcile(advanced) is True
        statuses = {
            store.event(entry.event_id).event_type: entry.status
            for entry in store.outbox_entries()
        }
        assert statuses["stale_transition"] == "DISCARDED"
        assert relay.run_once().published == 1
        assert transport.events[-1].event_type == "projection_reconciled"
        assert "stale_transition" not in [event.event_type for event in transport.events]
    finally:
        store.close()


def test_reconcile_confirms_event_when_checkpoint_caught_up(tmp_path):
    store = _store(tmp_path)
    try:
        initial = create_initial_state("task", "run")
        changed = dict(initial)
        changed["status"] = "RUNNING"
        advanced = store.transition(
            changed,
            expected_revision=0,
            event_type="checkpoint_written_before_confirmation",
        )

        assert store.reconcile(advanced) is True
        target = next(
            event
            for event in store.events("task", "run")
            if event["event_type"] == "checkpoint_written_before_confirmation"
        )
        assert target["checkpoint_confirmed"] is True
        statuses = {
            store.event(entry.event_id).event_type: entry.status
            for entry in store.outbox_entries()
        }
        assert statuses["checkpoint_written_before_confirmation"] == "PENDING"
    finally:
        store.close()


def test_checkpoint_confirmation_is_bounded_by_state_revision(tmp_path):
    store = _store(tmp_path)
    try:
        initial = create_initial_state("task", "run")
        changed = dict(initial)
        changed["status"] = "RUNNING"
        advanced = store.transition(
            changed,
            expected_revision=0,
            event_type="revision_one",
        )
        with store._lock, store._conn:
            store._append_event_tx(
                "task",
                "run",
                "future_revision",
                {},
                state_revision=2,
            )

        store.confirm_checkpoint("task", "run", advanced["state_revision"])
        records = {
            event.event_type: event for event in store.event_records("task", "run")
        }
        assert records["revision_one"].state_revision == 1
        assert records["revision_one"].checkpoint_confirmed is True
        assert records["future_revision"].state_revision == 2
        assert records["future_revision"].checkpoint_confirmed is False

        transport = InMemoryEventTransport()
        relay = OutboxRelay(store, transport, relay_id="relay-a")
        assert relay.run_once().published == 2
        assert [event.event_type for event in transport.events] == [
            "task_created",
            "revision_one",
        ]
    finally:
        store.close()


def test_outbox_relay_retries_with_backoff_without_losing_event(tmp_path):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    store = _store(tmp_path, clock=clock)

    class FlakyTransport:
        def __init__(self):
            self.fail = True

        def publish(self, event):
            if self.fail:
                raise TimeoutError("Bearer abcdefghijklmnop")
            return "redis-1"

    transport = FlakyTransport()
    relay = OutboxRelay(
        store,
        transport,
        relay_id="relay-a",
        base_retry_seconds=2,
    )
    try:
        assert relay.run_once().failed == 1
        pending = store.outbox_entries()[0]
        assert pending.status == "PENDING"
        assert pending.attempts == 1
        assert pending.last_error == "Bearer [REDACTED]"
        assert relay.run_once().published == 0

        clock.advance(seconds=2)
        transport.fail = False
        assert relay.run_once().published == 1
        delivered = store.outbox_entries()[0]
        assert delivered.status == "PUBLISHED"
        assert delivered.attempts == 2
    finally:
        store.close()


def test_expired_outbox_claim_can_be_recovered_by_another_relay(tmp_path):
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    store = _store(tmp_path, clock=clock)
    try:
        first = store.claim_outbox("relay-a", limit=1, lease_seconds=30)
        assert len(first) == 1
        assert store.claim_outbox("relay-b", limit=1, lease_seconds=30) == []

        clock.advance(seconds=31)
        recovered = store.claim_outbox("relay-b", limit=1, lease_seconds=30)
        assert recovered[0][0].event_id == first[0][0].event_id
        assert recovered[0][0].attempts == 2
        store.publish_outbox(
            recovered[0][0].outbox_id,
            relay_id="relay-b",
            stream_id="redis-1",
        )
        assert store.outbox_entries()[0].status == "PUBLISHED"
    finally:
        store.close()


class _FakeRedis:
    def __init__(self):
        self.streams = {}

    def xadd(self, stream, fields, **options):
        messages = self.streams.setdefault(stream, [])
        message_id = f"{len(messages) + 1}-0"
        messages.append((message_id, fields))
        return message_id.encode()

    def xread(self, streams, **options):
        stream, cursor = next(iter(streams.items()))
        messages = self.streams.get(stream, [])
        after = int(str(cursor).split("-", 1)[0])
        selected = [item for item in messages if int(item[0].split("-", 1)[0]) > after]
        selected = selected[: options.get("count", len(selected))]
        return [(stream.encode(), selected)] if selected else []


def test_redis_stream_transport_consumer_and_websocket_bridge(tmp_path):
    store = _store(tmp_path)
    try:
        event = store.event_records("task", "run")[0]
        redis = _FakeRedis()
        transport = RedisStreamTransport(redis, max_length=100)
        assert transport.publish(event) == "1-0"

        consumer = RedisStreamConsumer(redis)
        cursor, received = consumer.read("task", "run")
        assert cursor == "1-0"
        assert received == [event]
        assert transport.publish(event) == "2-0"

        async def exercise_bridge():
            hub = EventSubscriptionHub(queue_size=2)
            subscription = hub.subscribe("task", "run")
            bridge = RedisWebSocketBridge(consumer, hub)
            next_cursor, count = await bridge.poll_once("task", "run")
            forwarded = await asyncio.wait_for(subscription.get(), timeout=1)
            subscription.close()
            return next_cursor, count, forwarded

        next_cursor, count, forwarded = asyncio.run(exercise_bridge())
        assert next_cursor == "2-0"
        assert count == 1
        assert forwarded.event_id == event.event_id
    finally:
        store.close()


def test_existing_phase1_event_table_is_migrated_in_place(tmp_path):
    path = tmp_path / "control.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE execution_events (
           event_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
           task_id TEXT NOT NULL, run_id TEXT NOT NULL,
           sequence_number INTEGER NOT NULL, event_type TEXT NOT NULL,
           payload_json TEXT NOT NULL,
           checkpoint_confirmed INTEGER NOT NULL DEFAULT 0,
           created_at TEXT NOT NULL,
           UNIQUE(task_id, run_id, sequence_number))"""
    )
    connection.execute(
        "INSERT INTO execution_events VALUES ('old', 1, 'task', 'run', 1, 'old_event', '{}', 1, '2026-01-01T00:00:00+00:00')"
    )
    connection.commit()
    connection.close()

    store = SQLiteControlStore(path)
    try:
        event = store.event("old")
        assert event is not None
        assert event.node_name is None
        assert event.artifact_refs == []
        columns = {
            row[1] for row in store._conn.execute("PRAGMA table_info(execution_events)")
        }
        assert {
            "state_revision",
            "node_name",
            "attempt",
            "correlation_id",
            "causation_id",
            "artifact_refs_json",
        } <= columns
    finally:
        store.close()
