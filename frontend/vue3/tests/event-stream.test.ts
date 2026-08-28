import { describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import { TaskEventStream } from "@/api/events";
import type { ApiClient } from "@/api/client";
import type { ExecutionEvent } from "@/domain/types";
import { event } from "./fixtures";

class FakeSocket {
  onopen: (() => void) | null = null;
  onmessage: ((message: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  close(): void { this.closed = true; }
}

describe("TaskEventStream", () => {
  it("catches up from the durable cursor and deduplicates live copies", async () => {
    const first = event(1);
    const independent = event(2, { state_revision: null, checkpoint_confirmed: false, event_type: "user_message" });
    const received: ExecutionEvent[] = [];
    const socket = new FakeSocket();
    const api = {
      baseUrl: "/api",
      getEvents: vi.fn(async () => [first, independent]),
      createEventTicket: vi.fn(async () => ({ ticket: "short-lived", expires_at: "2099-01-01" })),
    } as unknown as ApiClient;
    const stream = new TaskEventStream({
      api,
      taskId: "task_1",
      runId: "run_1",
      onEvents: (events) => received.push(...events),
      websocketFactory: () => socket as unknown as WebSocket,
    });

    await stream.start();
    socket.onmessage?.(new MessageEvent("message", { data: JSON.stringify(independent) }));
    socket.onmessage?.(new MessageEvent("message", { data: JSON.stringify(event(3)) }));

    expect(received.map((item) => item.event_id)).toEqual(["event_1", "event_2", "event_3"]);
    expect(received[1].state_revision).toBeNull();
    expect(stream.lastSequence).toBe(3);
    expect(api.getEvents).toHaveBeenCalledWith("task_1", "run_1", 0);
    stream.stop();
  });

  it("does not advance past a live sequence gap before durable recovery", async () => {
    const received: ExecutionEvent[] = [];
    const socket = new FakeSocket();
    const api = {
      baseUrl: "/api",
      getEvents: vi.fn(async () => []),
      createEventTicket: vi.fn(async () => ({ ticket: "short-lived", expires_at: "2099-01-01" })),
    } as unknown as ApiClient;
    const stream = new TaskEventStream({
      api,
      taskId: "task_1",
      runId: "run_1",
      onEvents: (events) => received.push(...events),
      websocketFactory: () => socket as unknown as WebSocket,
      reconnectDelay: () => 60_000,
    });

    await stream.start();
    socket.onmessage?.(new MessageEvent("message", { data: JSON.stringify(event(2)) }));

    expect(received).toEqual([]);
    expect(stream.lastSequence).toBe(0);
    expect(socket.closed).toBe(true);
    stream.stop();
  });

  it.each([401, 403, 404])("closes without reconnecting after HTTP %s", async (status) => {
    vi.useFakeTimers();
    const states: string[] = [];
    const api = {
      baseUrl: "/api",
      getEvents: vi.fn(async () => { throw new ApiError("terminal", status); }),
    } as unknown as ApiClient;
    const stream = new TaskEventStream({
      api,
      taskId: "task_1",
      runId: "run_1",
      onEvents: () => undefined,
      onState: (state) => states.push(state),
      reconnectDelay: () => 1,
    });

    await stream.start();
    await vi.runAllTimersAsync();

    expect(api.getEvents).toHaveBeenCalledTimes(1);
    expect(states.at(-1)).toBe("closed");
    stream.stop();
    vi.useRealTimers();
  });
});
